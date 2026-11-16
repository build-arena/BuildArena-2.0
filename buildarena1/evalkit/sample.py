"""Evaluate one sample directory: rebuild, place, run, score.

Sample layout (what a build session leaves; buildarena1/README.md section 3):
    <runs root>/<batch>/<model label>/<cell>/s<NN>/
        machine/<name>.json (valid history) and machine/<name>.bsg (required),
        tool_calls.jsonl (needed when the valid history does not rebuild the saved machine),
        final_reply.txt (transport), meta.json, usage.json, build_check.json (optional)
Outputs go to ``<sample>/eval/<attempt>/``: prepared/ (BSG, block map),
trial_<n>/ (controller files, run output, telemetry.csv, score.json) and
result.json.

Running the game is the ``Runner`` interface; nothing here starts Besiege.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import traceback
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Protocol

from .config import EvalConfig, Paths
from . import legacy as L
from .control import ControlError, channel_schedule, write_lift_timeline, write_python_controller
from .judge import mass_ramp, rule_results, score_trial
from .legacy_log import rows_to_csv_text, trajectory_feedback
from .prompts import ControllerPrompts, controller_turn, parse_controller_reply, plan_sections, task_content
from .rebuild import MachineMismatch, RebuildError, check_final, prepare, use_v2
from .scoring import headline_metric
from .version import code_version


# ------------------------------------------------------------------ interfaces
@dataclass(frozen=True)
class RunPlan:
    """One run of the prepared machine in the game."""
    trial_dir: Path
    bsg: Path
    controller: Path            # .py program or .json timeline
    sandbox: str
    duration: float
    telemetry_hz: int
    recorder_hz: int
    run_name: str = "run"       # run directory under trial_dir; retries get their own
    # Parameters for the cargo-mass plugin (cargo GUID, 1.0's curve, BSG start
    # value, bank GUIDs); None when the plugin is not used.
    cargo_curve: dict | None = None

    @property
    def run_dir(self) -> Path:
        return self.trial_dir / self.run_name

    def besiege_cli_args(self) -> list[str]:
        """Arguments for ``python -m besiege_cli``.

        A Python controller is started before physics (``--controller-prestart``,
        2.0 control/README.md "Initialize a controller before physics"): the
        runner waits for its readiness handshake and only then sends start_sim,
        so the control schedule and the cargo mass ramp are not skipped while
        the subprocess starts. Timelines are replayed by the mod and need no prestart.
        """
        args = ["run", "--bsg", str(self.bsg), "--controller", str(self.controller),
                "--run-dir", str(self.run_dir), "--sandbox", self.sandbox,
                "--telemetry-hz", str(self.telemetry_hz), "--telemetry-profile", "full",
                "--recorder-hz", str(self.recorder_hz), "--pre-controller-hold", "0",
                "--post-completion-hold", "0"]
        if self.controller.suffix == ".py":
            args.append("--controller-prestart")
        return args


@dataclass(frozen=True)
class RunOutput:
    telemetry_csv: Path
    meta: dict = field(default_factory=dict)   # e.g. game build id, 2.0 commit


class Runner(Protocol):
    def run(self, plan: RunPlan) -> RunOutput: ...


class GameBusy(RuntimeError):
    """The Runner could not get the game (another run held it past the wait).
    Nothing was run: ``run_trial`` lets it through instead of counting a failed
    run, and the stepwise ``run-trial`` leaves its state as it was, so the same
    step can be called again later."""


SUPPORT_REFUSED = "support_level_not_loaded"
NO_POWERED = "no_powered_blocks"


def support_fixed(cfg: EvalConfig) -> bool:
    """Support on stand-in banks, one fixed mass per run (the standby protocol)."""
    return cfg.support_protocol == "fixed_mass_banks"


def support_on_banks(cfg: EvalConfig) -> bool:
    """Support on the stand-in banks of evalkit/terrain.py."""
    return cfg.support_protocol in ("curve_banks", "fixed_mass_banks")


def support_allowed(runner, cfg: EvalConfig) -> bool:
    """Support runs on stand-in banks unless the config says "refuse", or on
    1.0's level file through a Runner that loads level files."""
    return support_on_banks(cfg) or runner_loads_support_levels(runner)


def support_refusal() -> str:
    return ("Support levels (1.0 asset/support_*.blv) cannot be loaded by this runner and support_protocol is "
            "'refuse'; not run.")


def runner_loads_support_levels(runner) -> bool:
    """Support cells run on 1.0's custom .blv levels. A Runner that cannot load
    them must not run support at all: a plain sandbox would yield a score for a
    different task. Runners opt in with ``loads_support_levels = True``."""
    return bool(getattr(runner, "loads_support_levels", False))


class GameNotConnected:
    """Runner for hosts without the game: says what would have been run."""

    def run(self, plan: RunPlan) -> RunOutput:
        raise NotImplementedError("Running Besiege is not connected yet; args would be: "
                                  + " ".join(plan.besiege_cli_args()))


@dataclass(frozen=True)
class AgentReply:
    text: str
    usage: dict


# (system prompt, messages) -> reply. Implemented by the build-side adapter.
ControlAgent = Callable[[str, list[dict]], AgentReply]


# --------------------------------------------------------------- sample layout
@dataclass(frozen=True)
class SampleInfo:
    sample_dir: Path
    batch: str
    model: str
    cell: str
    category: str
    level: str
    sample: str


def sample_info(sample_dir: Path) -> SampleInfo:
    sample_dir = Path(sample_dir).resolve()
    if not sample_dir.is_dir():
        # a mistyped path would otherwise become eval/<attempt>/ with status no_machine
        raise ValueError(f"{sample_dir} is not a sample directory (it does not exist).")
    cell = sample_dir.parent.name
    category, _, level = cell.partition("_")
    if category not in L.CATEGORIES or level not in L.LEVELS:
        raise ValueError(f"{sample_dir} is not under a <category>_<level> cell directory.")
    meta_path = sample_dir / "meta.json"
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        for key, value in (("cell", cell), ("category", category), ("level", level)):
            if key in meta and meta[key] != value:
                raise ValueError(f"meta.json {key}={meta[key]!r} disagrees with directory {value!r}.")
    return SampleInfo(sample_dir, sample_dir.parents[2].name, sample_dir.parents[1].name, cell,
                      category, level, sample_dir.name)


def find_history(sample_dir: Path) -> Path | None:
    """The valid-only history machine/<name>.json next to machine/<name>.bsg.

    Only the json named like the .bsg counts: the build stage also writes
    machine/manifest.json and the full history <name>_full.json there. None if
    the build left no .bsg or no history beside it; more than one .bsg raises.
    """
    bsgs = sorted((sample_dir / "machine").glob("*.bsg"))
    if len(bsgs) > 1:
        raise ValueError(f"More than one machine in {sample_dir / 'machine'}: {bsgs}")
    if not bsgs:
        return None
    history = bsgs[0].with_suffix(".json")
    return history if history.is_file() else None


# ------------------------------------------------------------ build-check gate
# A build session that ended without close_machine_lifespan is evaluated on
# the machine it saved last, as 1.0 did (spatial/agent.py:302-306). Only these
# checks of build_check.json keep a sample from evaluation:
GATE_CHECKS = ("machine_files_present", "no_blocked_tool_called", "only_buildarena_tools_called",
               "tools_offered_ok", "not_smoke")
# These are carried into result.json as flags when false; the sample is evaluated.
FLAG_CHECKS = ("close_machine_lifespan_succeeded", "no_shell_messages_to_model", "final_reply_sections_present",
               "no_sample_identity_in_tool_results")
BUILD_INVALID = "build_invalid"
# The machine prepared for evaluation differs from the one the MCP saved at the end
# of the build session (evalkit.rebuild.MachineMismatch); the sample is not run.
MACHINE_MISMATCH = "machine_mismatch"


@dataclass(frozen=True)
class BuildGate:
    failed: list[str]          # gate checks that are false: the sample is not evaluated
    flags: list[str]           # what result.json carries for the summary
    session_status: str | None  # meta.json "status" of the build session


def build_gate(sample_dir: Path, check: Any) -> BuildGate:
    """Read build_check.json (``check``) and meta.json against the gate above.

    A build_check.json without a ``checks`` table (older format) falls back to
    its top-level ``ok``. A gate check missing from the table does not block
    the sample but is flagged.
    """
    meta = _read_json(Path(sample_dir) / "meta.json")
    status = meta.get("status") if isinstance(meta, dict) else None
    flags = [] if status in (None, "completed") else [f"build_session:{status}"]
    if not isinstance(check, dict):
        return BuildGate([], flags + ["build_check_missing"], status)
    checks = check.get("checks")
    if not isinstance(checks, dict):
        failed = ["ok"] if check.get("ok") is False else []
        return BuildGate(failed, flags + ["build_check_without_checks"], status)
    failed = [k for k in GATE_CHECKS if checks.get(k) is False]
    flags += [f"build_check_missing:{k}" for k in GATE_CHECKS if k not in checks]
    flags += [f"build:{k}" for k in FLAG_CHECKS if checks.get(k) is False]
    return BuildGate(failed, flags, status)


def machine_description(sample_dir: Path) -> tuple[str, str, list[str]]:
    """<functionality> and <motion_control> of the build session's last reply for
    the control agent. Without the two sections the whole final_reply.txt stands
    in as the description (functionality = full text, motion = ""); without the
    file the description is empty. Either case is flagged."""
    path = Path(sample_dir) / "final_reply.txt"
    if not path.is_file():
        return "", "", ["final_reply_missing"]
    text = path.read_text(encoding="utf-8")
    try:
        functionality, motion = plan_sections(text)
    except ValueError:
        return text, "", ["final_reply_sections_missing" if text.strip() else "final_reply_empty"]
    return functionality, motion, []


# 1.0 re-ran a failed task up to max_retries=3 times in all (scheduler/task_db.py:115,
# 398-403, 491-500): a control task whose reply did not parse, and a simulation task.
RUN_ATTEMPTS = 3
CONTROL_PARSE_ATTEMPTS = 3


def reply_parse_error(text: str) -> str | None:
    """The two reply errors 1.0 retried: no ```json block, JSON that does not parse
    (1.0 spatial/agent.py:376-384). None when the reply parses; validation of the
    parsed JSON is a separate, final step (evalkit.control.validate_control)."""
    try:
        body = text.split("```json")[1].split("```")[0]
    except IndexError:
        return "Reply has no ```json block."
    try:
        json.loads(body)
    except json.JSONDecodeError as exc:
        return f"Reply JSON does not parse: {exc}"
    return None


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _write_json(path: Path, value: Any) -> None:
    """Write to a temporary file in the same directory, flush it to disk, then
    rename it over ``path``: a reader sees the old file or the new one, never a
    half-written one (state.json, trial.json, result.json)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, indent=2, ensure_ascii=False, default=_json_default)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _json_default(value):
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Not JSON serialisable: {type(value).__name__}")


def cargo_fixed_mass(category: str, level: str, cfg: EvalConfig) -> float | None:
    """Mass written into the BSG for this cell, or None when the controller ramps it.
    For support under the fixed-mass protocol this is the first run's mass;
    every run writes its own (evalkit/support_fixed.py)."""
    if category == "transport" and level != "soft" and cfg.cargo_mass_mode == "fixed_bsg":
        return cfg.transport_cargo_mass_in_bsg
    if category == "support" and support_fixed(cfg):
        return cfg.support_success_mass
    return None


def plugin_curve(ctx: "Ctx") -> dict | None:
    """Cargo-mass plugin parameters for this cell, or None."""
    cat, level = ctx.info.category, ctx.info.level
    ramp = mass_ramp(cat, level, ctx.cfg)
    if ctx.cfg.cargo_mass_mode != "plugin_curve" or ramp is None or cargo_fixed_mass(cat, level, ctx.cfg) is not None:
        return None
    banks = [b["guid"] for b in (ctx.prep.manifest.get("terrain") or {}).get("banks", [])]
    return {"cargo_guid": ctx.ballast_guid(), "mass_min": ramp.mass_min, "mass_max": ramp.mass_max,
            "ramp_duration": ramp.ramp_duration, "ramp_start": ramp.ramp_start, "clamp_low": ramp.clamp_low,
            "initial_slider": float(L.BALLAST_DATA_MASS), "bank_guids": banks}


def controller_files(tdir: Path, ctx: "Ctx", intervals: list, *, fixed_mass: float | None = None) -> Path:
    """write_python_controller with this cell's cargo handling. With the
    CargoMass plugin the controller sends no mass at all."""
    cat, level = ctx.info.category, ctx.info.level
    fixed = fixed_mass if fixed_mass is not None else cargo_fixed_mass(cat, level, ctx.cfg)
    plugin = fixed is None and plugin_curve(ctx) is not None
    return write_python_controller(tdir, duration=ctx.duration, tick_s=ctx.cfg.control_tick_s, intervals=intervals,
                                   guid_of=ctx.guid_of(), ballast_guid=None if plugin else ctx.ballast_guid(),
                                   ramp=mass_ramp(cat, level, ctx.cfg), mass_unclipped=ctx.cfg.ballast_mass_unclipped,
                                   fixed_mass=fixed)


def add_cargo_mass(prep, info: SampleInfo, cfg: EvalConfig, result: dict) -> None:
    if mass_ramp(info.category, info.level, cfg) is not None and cfg.cargo_mass_mode != "plugin_curve":
        # a standby cargo-mass method is on: say so where the summary lists flagged samples
        result["flags"] = list(result.get("flags") or []) + [f"cargo_mass:{cfg.cargo_mass_mode}"]
    mass = cargo_fixed_mass(info.category, info.level, cfg)
    if mass is None:
        return
    from .cargo import machine_top_y, write_cargo_mass
    drop = cfg.cargo_drop_above_top
    top = machine_top_y(prep.rebuilt.machine) if drop is not None else None
    manifest = write_cargo_mass(Path(prep.manifest["bsg"]).parent, mass, top_y=top, drop=drop)
    keys = ("cargo_mass_in_bsg", "bsg_sha256", "cargo_drop_above_top", "machine_top_collider_y",
            "cargo_placement_1_0_y")
    prep.manifest.update({k: manifest[k] for k in keys if k in manifest})
    result["cargo_mass_in_bsg"] = mass
    result["cargo_drop_above_top"] = drop   # None: 1.0's placement, 3 above the highest block centre


def add_terrain(prep, info: SampleInfo, cfg: EvalConfig, paths: Paths, result: dict, runner) -> None:
    """Support cells on stand-in banks: write them into the prepared BSG and say so in result.json."""
    if info.category != "support" or not support_on_banks(cfg) or runner_loads_support_levels(runner):
        return
    from .terrain import apply_to_prepared
    manifest = apply_to_prepared(Path(prep.manifest["bsg"]).parent, paths.v1_root, info.level,
                                 mass=cfg.support_bank_mass)
    prep.manifest.update({k: manifest[k] for k in ("terrain", "bsg_sha256")})
    result["support_terrain"] = {k: v for k, v in manifest["terrain"].items() if k != "banks"}
    flags = ["support_terrain:ballast_banks"]
    if support_fixed(cfg):
        from .support_fixed import describe
        result["support_protocol"] = describe(cfg)
        flags.append("support_protocol:fixed_mass")
    result["flags"] = list(result.get("flags") or []) + flags


# ------------------------------------------------------------------- one trial
@dataclass
class Ctx:
    info: SampleInfo
    cfg: EvalConfig
    out: Path
    prep: Any            # evalkit.rebuild.Prepared
    runner: Runner

    @property
    def duration(self) -> float:
        if self.info.category == "support" and support_fixed(self.cfg):
            return self.cfg.support_run_seconds
        return L.DURATION[(self.info.category, self.info.level)]

    def guid_of(self) -> dict[str, str]:
        return {b["build_id"]: b["guid"] for b in self.prep.blocks if b["build_id"] is not None}

    def ballast_guid(self) -> str | None:
        return next((b["guid"] for b in self.prep.blocks if b["legacy_id"] == L.BALLAST_ID), None)


class FingerprintMismatch(RuntimeError):
    """This trial would run under other conditions than the batch's first evaluation
    (game, 2.0 code, plugin, evalkit code or judging config). Nothing was run."""


FINGERPRINT_NAME = "eval_fingerprint.json"
# both ToolKit copies: which one the game loads depends on how it was started (a
# game started without Steam loads the local Mods copy, with Steam the Workshop one)
FINGERPRINT_GAME_KEYS = ("game_buildid", "toolkit_dll_sha256", "toolkit_workshop_dll_sha256",
                         "assembly_csharp_sha256", "v2_commit",
                         "v2_local_diff_sha256", "cargo_plugin_sha256")
FINGERPRINT_CODE_KEYS = ("commit", "dirty_at_deploy", "files_match", "changed_files", "unlisted_files", "dirty_files",
                         "content_sha256")


def run_fingerprint(runner, cfg: EvalConfig) -> dict:
    """The conditions a trial runs under; the evalkit code is read now, at the trial."""
    ident = runner.identity() if callable(getattr(runner, "identity", None)) else {}
    code = code_version()
    return {"game": {k: ident.get(k) for k in FINGERPRINT_GAME_KEYS},
            "evalkit": {k: code.get(k) for k in FINGERPRINT_CODE_KEYS if k in code},
            "config": json.loads(json.dumps(cfg.to_json(), default=str))}


def _fingerprint_id(fp: dict) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(fp, sort_keys=True).encode()).hexdigest()[:12]


def check_fingerprint(ctx: "Ctx") -> dict:
    """Freeze the batch's conditions at its first trial (``<batch>/eval_fingerprint.json``,
    written once, exclusively); every later trial must match them or nothing runs
    so a batch is never scored under mixed conditions."""
    fp = run_fingerprint(ctx.runner, ctx.cfg)
    path = ctx.info.sample_dir.parents[2] / FINGERPRINT_NAME
    if not path.is_file():
        record = {"schema": "buildarena1.run_fingerprint.v1", "id": _fingerprint_id(fp),
                  "frozen_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "frozen_by": str(ctx.out), "fingerprint": fp}
        try:
            with open(path, "x", encoding="utf-8") as handle:
                handle.write(json.dumps(record, indent=2, sort_keys=True))
        except FileExistsError:
            pass                    # another trial froze it first; compare with that
    frozen = json.loads(path.read_text(encoding="utf-8"))
    diff = [f"{part}.{key}: frozen {frozen['fingerprint'].get(part, {}).get(key)!r}, now {value!r}"
            for part in ("game", "evalkit", "config") for key, value in fp[part].items()
            if frozen["fingerprint"].get(part, {}).get(key) != value]
    if diff:
        raise FingerprintMismatch(f"{path} differs from this trial: " + "; ".join(diff))
    return {"id": frozen["id"]}


def run_trial(ctx: Ctx, n: int, controller: Path, *, bsg: Path | None = None, judge=None) -> dict:
    """Run the game once through the Runner and score the telemetry.

    A run that raises is retried with the same controller in a fresh run
    directory, up to RUN_ATTEMPTS runs in all. A scoring error marks this trial
    ``score_failed`` instead of losing the whole sample (1.0 caught it and kept
    the earlier trials, analyze/sim_common.py).
    """
    tdir = ctx.out / f"trial_{n}"
    fingerprint = check_fingerprint(ctx)    # raises FingerprintMismatch before anything runs
    evalkit_code = code_version()
    # a run that came back GameBusy earlier left run errors (if any) but no run of its own
    errors, output = (_read_json(tdir / "run_errors.json") or []), None
    curve = None if judge is not None else plugin_curve(ctx)
    for k in range(len(errors) + 1, RUN_ATTEMPTS + 1):
        suffix = "" if k == 1 else f"_retry{k}"
        plan = RunPlan(tdir, bsg or ctx.prep.bsg, controller, ctx.cfg.sandbox, ctx.duration,
                       ctx.cfg.telemetry_hz, ctx.cfg.recorder_hz, run_name=f"run{suffix}", cargo_curve=curve)
        if plan.run_dir.exists():   # left by a call that ended before recording this trial
            errors.append({"attempt": k, "run_dir": str(plan.run_dir), "traceback": "",
                           "error": "run directory left by an earlier call that did not record its trial"})
            continue
        _write_json(tdir / f"run_plan{suffix}.json", {**asdict(plan), "besiege_cli_args": plan.besiege_cli_args()})
        try:
            output = ctx.runner.run(plan)
            break
        except (NotImplementedError, GameBusy):
            if errors:
                _write_json(tdir / "run_errors.json", errors)
            raise
        except Exception as exc:   # the game run is the external step that can fail
            errors.append({"attempt": k, "run_dir": str(plan.run_dir), "error": f"{type(exc).__name__}: {exc}",
                           "traceback": traceback.format_exc()})
    if errors:
        _write_json(tdir / "run_errors.json", errors)
    if output is None:
        failure = {"trial": n, "status": "run_failed", "error": errors[-1]["error"], "run_attempts": len(errors),
                   "traceback": errors[-1]["traceback"], "run_fingerprint": fingerprint, "evalkit_code": evalkit_code}
        _write_json(tdir / "run_error.json", failure)
        return failure
    telemetry = tdir / "telemetry.csv"
    if Path(output.telemetry_csv).resolve() != telemetry.resolve():
        shutil.copyfile(output.telemetry_csv, telemetry)
    ident = {"model_name": ctx.info.model, "machine_id": Path(ctx.prep.manifest["history"]).stem,
             "round_suffix": f"_{n}", "machine_bsg_path": str(ctx.prep.bsg),
             "machine_json_path": ctx.prep.manifest["history"]}
    # the mass record of this very run (a retry runs in its own directory): the
    # CargoMass plugin's per-frame log, put on the telemetry clock, when it set
    # the mass (evalkit/cargo_plugin.py), else the controller trace
    clock = None
    if output.meta.get("cargo_mass_log"):
        from .cargo_plugin import align
        try:
            clock = align(Path(output.meta["cargo_mass_log"]), telemetry, ctx.ballast_guid(),
                          tdir / f"cargo_mass_trace_{Path(output.telemetry_csv).parent.name}.jsonl")
        except (ValueError, OSError, KeyError) as exc:
            failure = {"trial": n, "status": "score_failed", "error": f"CargoMass log not aligned: {exc}",
                       "traceback": traceback.format_exc(), "run_meta": output.meta, "run_attempts": len(errors) + 1,
                       "run_fingerprint": fingerprint, "evalkit_code": evalkit_code}
            _write_json(tdir / "score_error.json", failure)
            return failure
        trace = Path(clock["aligned_trace"])
    else:
        trace = Path(output.telemetry_csv).parent / "controller_trace.jsonl"
    try:
        if judge is not None:   # a protocol's own judge: score dict, no 1.0-style rows
            score, rows = judge(telemetry), []
            score.update({k: ident[k] for k in ("model_name", "machine_id", "round_suffix")})
        else:
            score, rows = score_trial(telemetry, ctx.prep.blocks, ctx.info.category, ctx.info.level, ident, ctx.cfg,
                                      controller_trace=trace if trace.is_file() else None, cargo_clock=clock)
    except Exception as exc:  # noqa: BLE001 - one unscorable trial (scoring.NoValidRows included) must not drop the sample
        failure = {"trial": n, "status": "score_failed", "error": f"{type(exc).__name__}: {exc}",
                   "traceback": traceback.format_exc(), "run_meta": output.meta, "run_attempts": len(errors) + 1,
                   "run_fingerprint": fingerprint, "evalkit_code": evalkit_code}
        _write_json(tdir / "score_error.json", failure)
        return failure
    if rows:
        (tdir / "legacy_rows.csv").write_text(rows_to_csv_text(rows), encoding="utf-8")
    _write_json(tdir / "score.json", score)
    trial = {"trial": n, "status": "scored", "score": score, "rows": rows, "run_meta": output.meta,
             "run_attempts": len(errors) + 1, "run_fingerprint": fingerprint, "evalkit_code": evalkit_code}
    if clock is not None:
        trial["cargo_mass_clock"] = clock
    return trial


def support_fixed_run(ctx: Ctx, n: int, mass: float) -> dict:
    """One run of the fixed-mass support protocol: its own BSG with ``mass``, no sends."""
    from .support_fixed import bsg_with_mass, judge_run
    tdir = ctx.out / f"trial_{n}"
    cargo = ctx.ballast_guid()
    bsg = bsg_with_mass(ctx.prep.bsg, cargo, mass, tdir / f"{ctx.prep.bsg.stem}_m{mass:g}.bsg")
    controller = controller_files(tdir, ctx, [], fixed_mass=mass)
    trial = run_trial(ctx, n, controller, bsg=bsg,
                      judge=lambda telemetry: judge_run(telemetry, cargo, mass, ctx.info.level, ctx.cfg))
    trial["mass"] = mass
    return trial


def support_fixed_trials(ctx: Ctx) -> list[dict]:
    """The run sequence of the fixed-mass support protocol (evalkit/support_fixed.next_mass)."""
    from .support_fixed import next_mass
    trials, runs = [], []
    while (mass := next_mass(runs, ctx.cfg)) is not None:
        trial = support_fixed_run(ctx, len(trials) + 1, mass)
        trials.append(trial)
        if trial["status"] != "scored":
            break
        runs.append((mass, bool(trial["score"]["held"])))
    return trials


def fixed_controller(ctx: Ctx) -> Path:
    """Controller for support and lift: 1.0 used no control agent there."""
    tdir = ctx.out / "trial_1"
    cat, level = ctx.info.category, ctx.info.level
    if cat == "lift" and level != "soft":
        cannons = [b["guid"] for b in ctx.prep.blocks if b["name_v2"] == "Water Cannon"]
        if cannons:
            return write_lift_timeline(tdir, cannons, ctx.duration)
    return controller_files(tdir, ctx, [])


def transport_trials(ctx: Ctx, agent: ControlAgent, prompts: ControllerPrompts) -> tuple[list[dict], list[dict]]:
    """Up to three control rounds, each followed by a run (1.0 scheduler/runner.py:180-251)."""
    functionality, motion, _ = machine_description(ctx.info.sample_dir)
    task = task_content(prompts.task(ctx.info.category, ctx.info.level), functionality, motion)
    trials, usage, previous, feedback = [], [], None, None
    for n in range(1, ctx.cfg.transport_trials + 1):
        tdir = ctx.out / f"trial_{n}"
        messages = controller_turn(task=task, state=ctx.prep.machine_state, previous=previous, feedback=feedback)
        attempts = []
        for _ in range(CONTROL_PARSE_ATTEMPTS):   # same messages again on a parse error only
            reply = agent(prompts.system, messages)
            usage.append(reply.usage)
            error = reply_parse_error(reply.text)
            attempts.append({"reply": reply.text, "usage": reply.usage, "parse_error": error})
            if error is None:
                break
        _write_json(tdir / "control_round.json", control_round_record(prompts.system, messages, attempts))
        if error is not None:
            trials.append({"trial": n, "status": "control_parse_failed", "error": error,
                           "control_attempts": len(attempts)})
            break
        try:
            raw, plan = parse_controller_reply(reply.text, ctx.prep.actions, ctx.prep.block_ids)
        except ControlError as exc:
            trials.append({"trial": n, "status": "control_invalid", "error": str(exc)})
            break
        intervals = channel_schedule(plan, ctx.duration, quantize=ctx.cfg.quantize_control_times,
                                     rule=ctx.cfg.overlap_rule)
        _write_json(tdir / "control.json", {"raw": raw, "plan": plan.to_json(), "intervals": [asdict(i) for i in intervals]})
        controller = controller_files(tdir, ctx, intervals)
        trial = run_trial(ctx, n, controller)
        trials.append(trial)
        if trial["status"] != "scored" or n == ctx.cfg.transport_trials:
            break
        try:
            feedback, not_moving = trajectory_feedback(trial["rows"], axis_labels=ctx.cfg.feedback_axis_labels)
        except ValueError as exc:   # 1.0 runner.py:217 indexes csv_data[0] and fails the same way
            trial["stop_reason"] = str(exc)
            break
        if not_moving and n >= 2:   # runner.py:212-214, checked from the second run on
            trial["stop_reason"] = "The machine is not moving at all"
            break
        previous = messages + [{"role": "assistant", "content": reply.text}]
    return trials, usage


def control_round_record(system: str, messages: list[dict], attempts: list[dict]) -> dict:
    """control_round.json: the round's input, every call made with it, and the
    last reply (the one used when it parsed)."""
    return {"system": system, "messages": messages, "reply": attempts[-1]["reply"],
            "usage": attempts[-1]["usage"], "attempts": attempts}


# ---------------------------------------------------------------- whole sample
def _summary_of(trial: dict, metric: str, lift_success_rule: str = "ba1") -> dict:
    out = {k: v for k, v in trial.items() if k not in ("score", "rows", "traceback")}
    if trial["status"] == "scored":
        score = trial["score"]
        rule = score.get("v2", {}).get("lift_success_rule", lift_success_rule)
        out.update({"pass": bool(score["pass"]), "metric": score[metric],
                    "hacked": bool(score.get("hacked", False)), **rule_results(score, rule)})
        if "elevation_change" in score:
            out["elevation_change"] = score["elevation_change"]
    return out


def _counts(trial: dict) -> bool:
    return bool(trial["score"]["pass"]) and not bool(trial["score"].get("hacked", False))


def combine(info: SampleInfo, trials: list[dict], *, cfg: EvalConfig | None = None) -> dict:
    """How trials make a sample (1.0 has no code for it; README section 5).

    A trial counts when it passes and is not flagged hacked. The sample succeeds
    when at least one trial counts; its metric is the largest among counting
    trials. A failed sample has metric None, and its best scored value is kept
    apart in ``metric_unsuccessful_best``. A sample with a passing but hacked
    trial is flagged ``hacked_pass_any``.
    """
    metric = headline_metric(info.category, info.level)
    scored = [t for t in trials if t["status"] == "scored"]
    counted = [t for t in scored if _counts(t)]
    values = lambda ts: [t["score"][metric] for t in ts if t["score"].get(metric) is not None]
    success = bool(counted)
    return {"success": success,
            "metric": {"name": metric, "value": max(values(counted), default=None)},
            "metric_unsuccessful_best": {"name": metric,
                                         "value": None if success else max(values(scored), default=None)},
            "trials": [_summary_of(t, metric, cfg.lift_success_rule if cfg else "ba1") for t in trials],
            "hacked_any": any(bool(t["score"].get("hacked", False)) for t in scored),
            "hacked_pass_any": any(bool(t["score"]["pass"]) and bool(t["score"].get("hacked", False))
                                   for t in scored)}


def evaluate_sample(sample_dir: Path, *, runner: Runner, paths: Paths, cfg: EvalConfig = EvalConfig(),
                    control_agent: ControlAgent | None = None, prompts: ControllerPrompts | None = None,
                    attempt: str | None = None) -> dict:
    """Evaluate one sample; writes eval/<attempt>/result.json and returns it."""
    info = sample_info(Path(sample_dir))
    attempt = attempt or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = info.sample_dir / "eval" / attempt
    out.mkdir(parents=True, exist_ok=False)
    result = {"schema": "buildarena1.sample_result.v1", **{k: str(v) for k, v in asdict(info).items()},
              "attempt": attempt, "config": cfg.to_json(), "success": False,
              "metric": {"name": headline_metric(info.category, info.level), "value": None},
              "build_check": _read_json(info.sample_dir / "build_check.json"),
              "build_usage": _read_json(info.sample_dir / "usage.json"), "trials": [], "control_usage": [],
              "evalkit_code": code_version()}
    status = _evaluate(info, out, result, runner, paths, cfg, control_agent, prompts)
    result["status"] = status
    _write_json(out / "result.json", result)
    return result


def _evaluate(info, out, result, runner, paths, cfg, agent, prompts) -> str:
    try:
        history = find_history(info.sample_dir)
    except ValueError as exc:
        result["error"] = str(exc)
        return "machine_ambiguous"
    if history is None:
        return "no_machine"
    gate = build_gate(info.sample_dir, result["build_check"])
    result["build_session_status"] = gate.session_status
    result["flags"] = list(gate.flags)
    if gate.failed:
        result["build_gate_failed"] = gate.failed
        return BUILD_INVALID
    if info.category == "support" and not support_allowed(runner, cfg):
        result["error"] = support_refusal()
        return SUPPORT_REFUSED
    use_v2(paths)
    try:
        prep = prepare(history, info.category, info.level, cfg, out / "prepared", machine_name=history.stem,
                       mcp_bsg=history.with_suffix(".bsg"), tool_calls=info.sample_dir / "tool_calls.jsonl")
        add_terrain(prep, info, cfg, paths, result, runner)
        add_cargo_mass(prep, info, cfg, result)
        check_final(prep, cfg)
    except MachineMismatch as exc:
        result["error"] = str(exc)
        result["machine_check"] = {"status": MACHINE_MISMATCH, **exc.details}
        return MACHINE_MISMATCH
    except RebuildError as exc:
        result["error"] = str(exc)
        return "rebuild_failed"
    result["machine_check"] = prep.machine_check
    result["num_blocks"] = sum(1 for b in prep.blocks if b["legacy_id"] != L.BALLAST_ID)
    result["prepared"] = {k: prep.manifest[k] for k in ("bsg", "bsg_sha256", "history_sha256", "operations")}
    ctx = Ctx(info, cfg, out, prep, runner)
    if info.category == "transport":
        if agent is None or prompts is None:
            raise ValueError("Transport samples need a control agent and controller prompts.")
        result["flags"] += machine_description(info.sample_dir)[2]
        if not prep.actions:   # 1.0 spatial/agent.py:340-351 errors before calling the model
            result["error"] = "The machine has no powered blocks; the control agent was not called."
            return NO_POWERED
        trials, usage = transport_trials(ctx, agent, prompts)
        result["control_usage"] = usage
    elif info.category == "support" and support_fixed(cfg):
        trials = support_fixed_trials(ctx)
    else:
        trials = [run_trial(ctx, 1, fixed_controller(ctx))]
    result.update(combine(info, trials, cfg=cfg))
    if any(t["status"] == "scored" for t in trials):
        return "scored"
    return trials[0]["status"] if trials else "no_trial"
