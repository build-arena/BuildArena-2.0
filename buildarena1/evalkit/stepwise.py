"""``evaluate_sample`` cut into steps that can run in separate processes.

The control agent's model call is made by the caller, outside this package,
possibly on another machine than the game, so a transport sample is evaluated
as a sequence of separate calls:

    prepare -> (controller_input -> parse_reply -> run_trial) x up to 3 -> finalize

Support and lift skip the control rounds: prepare -> run_trial -> finalize.
Each step reads and writes ``<sample>/eval/<attempt>/``; ``state.json`` there
records what has been done and which step comes next. The decisions (stop on an
invalid control JSON, on a failed run, on missing trajectory rows, on a machine
that does not move from the second trial on) are the ones
``evalkit.sample.transport_trials`` makes, and the final ``result.json`` has the
same fields as ``evaluate_sample``'s; ``buildarena1/tests/test_stepwise.py``
compares the two on the same fake game.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import legacy as L
from .config import EvalConfig, Paths, LEGACY_LIFT_RULE_NOTE
from .control import ControlError, channel_schedule, write_python_controller
from .judge import mass_ramp
from .legacy_log import trajectory_feedback
from .prompts import ControllerPrompts, controller_turn, parse_controller_reply, task_content
from .rebuild import MachineMismatch, RebuildError, check_final, prepare as rebuild_prepare, use_v2
from .sample import (BUILD_INVALID, CONTROL_PARSE_ATTEMPTS, MACHINE_MISMATCH, NO_POWERED, build_gate, machine_description, SUPPORT_REFUSED, Ctx, Runner, _read_json, _write_json,
                     combine, control_round_record, find_history, fixed_controller, reply_parse_error, run_trial,
                     runner_loads_support_levels, sample_info, add_terrain, support_allowed, support_refusal,
                     add_cargo_mass, controller_files, support_fixed, support_fixed_run)
from .support_fixed import next_mass
from .scoring import headline_metric
from .version import code_version

STATE_SCHEMA = "buildarena1.stepwise_state.v1"


class StepError(RuntimeError):
    """A step was called out of order or on a finished attempt."""


@dataclass
class LoadedPrep:
    """What later steps need from ``rebuild.prepare``, read back from prepared/."""
    manifest: dict
    machine_state: str
    actions: dict[str, tuple[str, ...]]
    block_ids: set[str]

    @property
    def blocks(self) -> list[dict]:
        return self.manifest["blocks"]

    @property
    def bsg(self) -> Path:
        return Path(self.manifest["bsg"])

    @classmethod
    def read(cls, prepared: Path) -> "LoadedPrep":
        meta = json.loads((prepared / "control_meta.json").read_text(encoding="utf-8"))
        return cls(json.loads((prepared / "block_map.json").read_text(encoding="utf-8")),
                   (prepared / "machine_state.txt").read_text(encoding="utf-8"),
                   {k: tuple(v) for k, v in meta["actions"].items()}, set(meta["block_ids"]))


def attempt_dir(sample_dir: Path, attempt: str) -> Path:
    return sample_info(Path(sample_dir)).sample_dir / "eval" / attempt


def _load_state(out: Path) -> dict:
    path = out / "state.json"
    if not path.is_file():
        raise StepError(f"{out} has no state.json; run prepare first.")
    state = json.loads(path.read_text(encoding="utf-8"))
    _cfg(state)  # validate and annotate legacy config even when only finalizing
    return state


def _save_state(out: Path, state: dict) -> None:
    _write_json(out / "state.json", state)


def _cfg(state: dict) -> EvalConfig:
    raw = dict(state["result"]["config"])
    cfg = EvalConfig.from_json(raw, source="state.json config")
    if "lift_success_rule" not in raw:
        state["result"].setdefault("config_notes", []).append(LEGACY_LIFT_RULE_NOTE)
    state["result"]["config"] = cfg.to_json()
    return cfg


def _finish_early(out: Path, state: dict, status: str) -> dict:
    state["result"]["status"] = status
    state["next"] = "done"
    _write_json(out / "result.json", state["result"])
    _save_state(out, state)
    return {"status": status, "next": "done", "result": str(out / "result.json")}


# ---------------------------------------------------------------------- steps
def prepare(sample_dir: Path, *, paths: Paths, cfg: EvalConfig = EvalConfig(), attempt: str | None = None,
            runner: Runner | None = None) -> dict:
    """Rebuild, place and export. Returns the machine summary and the next step."""
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
    state = {"schema": STATE_SCHEMA, "result": result, "rounds": [], "next": None}
    try:
        history = find_history(info.sample_dir)
    except ValueError as exc:
        result["error"] = str(exc)
        return _finish_early(out, state, "machine_ambiguous")
    if history is None:
        return _finish_early(out, state, "no_machine")
    gate = build_gate(info.sample_dir, result["build_check"])
    result["build_session_status"] = gate.session_status
    result["flags"] = list(gate.flags)
    if gate.failed:
        result["build_gate_failed"] = gate.failed
        return _finish_early(out, state, BUILD_INVALID)
    if info.category == "support" and runner is not None and not support_allowed(runner, cfg):
        result["error"] = support_refusal()
        return _finish_early(out, state, SUPPORT_REFUSED)
    use_v2(paths)
    try:
        prep = rebuild_prepare(history, info.category, info.level, cfg, out / "prepared", machine_name=history.stem,
                               mcp_bsg=history.with_suffix(".bsg"), tool_calls=info.sample_dir / "tool_calls.jsonl")
        add_terrain(prep, info, cfg, paths, result, runner)
        add_cargo_mass(prep, info, cfg, result)
        check_final(prep, cfg)
    except MachineMismatch as exc:
        result["error"] = str(exc)
        result["machine_check"] = {"status": MACHINE_MISMATCH, **exc.details}
        return _finish_early(out, state, MACHINE_MISMATCH)
    except RebuildError as exc:
        result["error"] = str(exc)
        return _finish_early(out, state, "rebuild_failed")
    result["machine_check"] = prep.machine_check
    _write_json(out / "prepared" / "control_meta.json",
                {"actions": {k: list(v) for k, v in prep.actions.items()}, "block_ids": sorted(prep.block_ids)})
    result["num_blocks"] = sum(1 for b in prep.blocks if b["legacy_id"] != L.BALLAST_ID)
    result["prepared"] = {k: prep.manifest[k] for k in ("bsg", "bsg_sha256", "history_sha256", "operations")}
    if info.category == "transport":
        result["flags"] += machine_description(info.sample_dir)[2]
        if not prep.actions:
            result["error"] = "The machine has no powered blocks; the control agent was not called."
            return _finish_early(out, state, NO_POWERED)
        state["next"] = "controller_input"
    else:
        state["next"] = "run_trial"
    _save_state(out, state)
    return {"status": "prepared", "attempt": attempt, "category": info.category, "level": info.level,
            "duration": Ctx(info, cfg, out, prep, None).duration, "num_blocks": result["num_blocks"],
            "machine_state": prep.machine_state, "actions": {k: list(v) for k, v in prep.actions.items()},
            "flags": result["flags"], "build_session_status": result["build_session_status"],
            "next": state["next"], "round": 1}


def controller_input(sample_dir: Path, attempt: str, *, prompts: ControllerPrompts) -> dict:
    """System prompt and messages of the next control call (1.0 agent.py:357-367).

    After a reply that did not parse, the same round is asked again with the
    same messages (``call`` counts the calls of the round)."""
    info = sample_info(Path(sample_dir))
    out = info.sample_dir / "eval" / attempt
    state = _load_state(out)
    if state["next"] != "controller_input":
        raise StepError(f"Next step is {state['next']!r}, not 'controller_input'.")
    if state["rounds"] and state["rounds"][-1].get("retry"):
        rnd = state["rounds"][-1]
        rnd["retry"] = False
        state["next"] = "parse_reply"
        _save_state(out, state)
        return {"round": rnd["round"], "call": len(rnd["attempts"]) + 1, "system": rnd["system"],
                "messages": rnd["messages"], "next": "parse_reply"}
    n = len(state["rounds"]) + 1
    prep = LoadedPrep.read(out / "prepared")
    if n == 1:
        functionality, motion, _ = machine_description(info.sample_dir)
        task = task_content(prompts.task(info.category, info.level), functionality, motion)
        messages = controller_turn(task=task, state=prep.machine_state)
    else:
        last = state["rounds"][-1]
        previous = last["messages"] + [{"role": "assistant", "content": last["reply"]}]
        messages = controller_turn(task="", state=prep.machine_state, previous=previous,
                                   feedback=last["feedback"])
    state["rounds"].append({"round": n, "system": prompts.system, "messages": messages, "attempts": []})
    state["next"] = "parse_reply"
    _write_json(out / f"trial_{n}" / "control_input.json", {"system": prompts.system, "messages": messages})
    _save_state(out, state)
    return {"round": n, "call": 1, "system": prompts.system, "messages": messages, "next": "parse_reply"}


def parse_reply(sample_dir: Path, attempt: str, *, reply: str, usage: dict) -> dict:
    """Record the control agent's reply; write the trial's controller if it parses."""
    info = sample_info(Path(sample_dir))
    out = info.sample_dir / "eval" / attempt
    state = _load_state(out)
    if state["next"] != "parse_reply":
        raise StepError(f"Next step is {state['next']!r}, not 'parse_reply'.")
    rnd = state["rounds"][-1]
    n = rnd["round"]
    tdir = out / f"trial_{n}"
    cfg = _cfg(state)
    prep = LoadedPrep.read(out / "prepared")
    error = reply_parse_error(reply)
    rnd["attempts"].append({"reply": reply, "usage": usage, "parse_error": error})
    rnd.update(reply=reply, usage=usage)
    state["result"]["control_usage"].append(usage)
    _write_json(tdir / "control_round.json", control_round_record(rnd["system"], rnd["messages"], rnd["attempts"]))
    if error is not None:
        calls = len(rnd["attempts"])
        if calls < CONTROL_PARSE_ATTEMPTS:
            rnd["retry"] = True
            state["next"] = "controller_input"
            _save_state(out, state)
            return {"round": n, "call": calls, "status": "control_parse_failed", "error": error,
                    "next": "controller_input"}
        _write_json(tdir / "trial.json", {"trial": n, "status": "control_parse_failed", "error": error,
                                          "control_attempts": calls})
        state["next"] = "finalize"
        _save_state(out, state)
        return {"round": n, "call": calls, "status": "control_parse_failed", "error": error, "next": "finalize"}
    try:
        raw, plan = parse_controller_reply(reply, prep.actions, prep.block_ids)
    except ControlError as exc:
        trial = {"trial": n, "status": "control_invalid", "error": str(exc)}
        _write_json(tdir / "trial.json", trial)
        state["next"] = "finalize"
        _save_state(out, state)
        return {"round": n, "status": "control_invalid", "error": str(exc), "next": "finalize"}
    duration = L.DURATION[(info.category, info.level)]
    intervals = channel_schedule(plan, duration, quantize=cfg.quantize_control_times, rule=cfg.overlap_rule)
    _write_json(tdir / "control.json", {"raw": raw, "plan": plan.to_json(), "intervals": [asdict(i) for i in intervals]})
    controller_files(tdir, Ctx(info, cfg, out, prep, None), intervals)
    state["next"] = "run_trial"
    _save_state(out, state)
    return {"round": n, "status": "control_ok", "dropped": plan.dropped,
            "intervals": [asdict(i) for i in intervals], "next": "run_trial"}


def _finished_trial(tdir: Path, n: int) -> dict | None:
    """The trial record a run-trial call left in ``tdir`` before it could update
    state.json (the process ended in between), or None if that run is not complete."""
    try:
        trial = json.loads((tdir / "trial.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(trial, dict) or trial.get("trial") != n or "status" not in trial:
        return None
    if trial["status"] == "scored" and not ((tdir / "score.json").is_file() and (tdir / "telemetry.csv").is_file()):
        return None
    return trial


def run_trial_step(sample_dir: Path, attempt: str, *, runner: Runner) -> dict:
    """Run the game once for the current round and score it.

    Support under the fixed-mass protocol runs several times, one mass each
    (evalkit/support_fixed.py); while another mass is due, ``next`` stays
    ``run_trial``, so a driver that follows ``next`` needs no support logic.

    Re-entry: the trial record is written before state.json. If a call ended
    between the two, the next call finds this round's complete trial.json,
    brings state.json up to date from it and returns the same answer without
    running the game again (``"resumed": true``). An incomplete record is run
    again. When the game is busy (``GameBusy``) nothing changes."""
    info = sample_info(Path(sample_dir))
    out = info.sample_dir / "eval" / attempt
    state = _load_state(out)
    if state["next"] != "run_trial":
        raise StepError(f"Next step is {state['next']!r}, not 'run_trial'.")
    cfg = _cfg(state)
    prep = LoadedPrep.read(out / "prepared")
    ctx = Ctx(info, cfg, out, prep, runner)
    support_runs = None
    if info.category == "support" and support_fixed(cfg):
        support_runs = state.setdefault("support_runs", [])
        mass = next_mass([tuple(r) for r in support_runs], cfg)
        if mass is None:
            raise StepError("No support run is due; finalize.")
        n = len(support_runs) + 1
    elif info.category == "transport":
        n = state["rounds"][-1]["round"]
    else:
        n = 1
    tdir = out / f"trial_{n}"
    trial = _finished_trial(tdir, n)
    resumed = trial is not None
    if resumed:
        rows = read_rows(tdir / "legacy_rows.csv") if (tdir / "legacy_rows.csv").is_file() else None
        trial.pop("stop_reason", None)      # decided again below, the same way
    elif support_runs is not None:
        trial = support_fixed_run(ctx, n, mass)
        rows = trial.pop("rows", None)
    else:
        controller = tdir / "controller.py" if info.category == "transport" else fixed_controller(ctx)
        trial = run_trial(ctx, n, controller)
        rows = trial.pop("rows", None)
    nxt = "finalize"
    if support_runs is not None and trial["status"] == "scored":
        support_runs.append([trial["mass"], bool(trial["score"]["held"])])
        if next_mass([tuple(r) for r in support_runs], cfg) is not None:
            nxt = "run_trial"
    if info.category == "transport" and trial["status"] == "scored" and n < cfg.transport_trials:
        try:
            feedback, not_moving = trajectory_feedback(rows or [], axis_labels=cfg.feedback_axis_labels)
        except ValueError as exc:
            trial["stop_reason"] = str(exc)
        else:
            if not_moving and n >= 2:
                trial["stop_reason"] = "The machine is not moving at all"
            else:
                state["rounds"][-1]["feedback"] = feedback
                nxt = "controller_input"
    if not resumed:
        _write_json(tdir / "trial.json", trial)
    state["next"] = nxt
    _save_state(out, state)
    summary = {"round": n, "status": trial["status"], "next": nxt}
    if resumed:
        summary["resumed"] = True
    if support_runs is not None:
        summary["mass"] = trial["mass"]
        if trial["status"] == "scored":
            summary["held"] = bool(trial["score"]["held"])
    if trial["status"] == "scored":
        metric = headline_metric(info.category, info.level)
        summary.update({"pass": bool(trial["score"]["pass"]), "metric": {metric: trial["score"][metric]}})
    else:
        summary["error"] = trial.get("error")
    if "stop_reason" in trial:
        summary["stop_reason"] = trial["stop_reason"]
    return summary


def _trials(out: Path) -> list[dict]:
    found = sorted(out.glob("trial_*/trial.json"), key=lambda p: int(p.parent.name.split("_")[1]))
    return [json.loads(p.read_text(encoding="utf-8")) for p in found]


def _runtime(trials: list[dict]) -> dict | None:
    keys = ("game_buildid", "game_version", "toolkit_version", "toolkit_dll_sha256", "v2_commit",
            "v2_local_diff_files", "v2_local_diff_sha256", "game_dir")
    for t in trials:
        meta = t.get("run_meta") or {}
        if meta:
            return {k: meta.get(k) for k in keys}
    return None


def finalize(sample_dir: Path, attempt: str, *, reason: str | None = None, usage: dict | None = None) -> dict:
    """Combine the trials into result.json (same fields as evaluate_sample).

    Normally called when the next step is ``finalize``. The driver may also stop
    early: before a control round (the rounds run so far count) or after a
    control call that returned no reply, which ``reason`` describes and which
    is recorded as a ``control_call_failed`` trial. ``usage`` is what that failed
    call (with its retries) was billed; it goes into result.json's
    control_usage so the summary counts it (it needs ``reason``).
    """
    info = sample_info(Path(sample_dir))
    out = info.sample_dir / "eval" / attempt
    state = _load_state(out)
    if state["next"] == "done":
        return json.loads((out / "result.json").read_text(encoding="utf-8"))
    if usage is not None and not reason:
        raise StepError("A usage record goes with a failed control call; pass its reason too.")
    if state["next"] not in ("finalize", "controller_input", "parse_reply"):
        raise StepError(f"Cannot finalize while the next step is {state['next']!r}.")
    if state["next"] == "parse_reply":
        if not reason:
            raise StepError("The current control round has no reply; pass the reason to finalize anyway.")
        n = state["rounds"][-1]["round"]
        trial = {"trial": n, "status": "control_call_failed", "error": reason}
        if usage is not None:
            trial["usage"] = usage
        _write_json(out / f"trial_{n}" / "trial.json", trial)
        state["next"] = "finalize"
    if usage is not None:
        state["result"]["control_usage"].append({**usage, "control_call_failed": reason})
    result = state["result"]
    trials = _trials(out)
    result["trials"] = []
    result.update(combine(info, trials, cfg=_cfg(state)))
    result["runtime"] = _runtime(trials)
    if any(t["status"] == "scored" for t in trials):
        result["status"] = "scored"
    else:
        result["status"] = trials[0]["status"] if trials else "no_trial"
    if state["next"] == "controller_input":
        result["stopped_early"] = "finalized before the remaining control rounds"
    _write_json(out / "result.json", result)
    state["next"] = "done"
    _save_state(out, state)
    return result


def read_rows(path: Path) -> list[list[str]]:
    with open(path, newline="", encoding="utf-8") as handle:
        return [row for row in csv.reader(handle)]
