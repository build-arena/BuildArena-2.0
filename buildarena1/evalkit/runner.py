"""The real ``Runner``: one ``python -m besiege_cli run`` from this checkout's
``control/`` directory, on the game that one-command setup configured.

Everything that identifies what ran (game build id and version string,
ToolKit version and DLL hash, Assembly-CSharp hash, 2.0 commit and any local
change outside buildarena1/, the cargo-mass plugin's hash) is written to
``<run name>_meta.json`` next to the run and returned in ``RunOutput.meta``.

Cargo mass. Under ``cargo_mass_mode = "plugin_curve"`` (the default) the
cells with cargo need the cargo-mass plugin described in buildarena1/README.md
section 6. The run writes the plugin's spec next to the trial, passes its path
in ``BH_CARGO_MASS_SPEC`` (with ``BH_CARGO_MASS=1``), quits any running game
and starts the game binary itself, with those variables, before calling
``besiege_cli run``, which then uses that game. besiege_cli would otherwise
ask Steam first (session.ensure_game), and a game Steam starts does not get
the run's environment. The binary is started the way besiege_cli starts it
when Steam is not used: ``BESIEGE_LAUNCH_COMMAND`` if set, else the game
executable. If that start fails, the run raises ``RunError`` and besiege_cli
is not called. Without ``cargo_plugin``
set, or with no file there, such a run raises ``RunError`` before the game
is touched; a run whose plugin log is missing or incomplete raises
``RunError`` after it (``cargo_log_problem``).

Only one run may use a game at a time: the ToolKit's file protocol lives in
one directory per game. On POSIX a lock file named after the game's
Besiege_Data path enforces it, in ``lock_dir`` (default
``$XDG_CACHE_HOME/buildarena1/locks``, else ``~/.cache/buildarena1/locks``),
so runs of one user from different checkouts of this repository that use
the same game wait for each other. Users with different home directories do
not share the lock, and on Windows there is none, so run one evaluation at a
time there. By default the game is quit after every run (``keep_game=False``).

Paths are made absolute when the runner is created, and the subprocesses get
``BESIEGE_DATA_PATH`` set to the absolute Besiege_Data path, because they run
in ``<repo>/control``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .sample import GameBusy, RunOutput, RunPlan
from .version import code_version

try:
    import fcntl
except ImportError:          # Windows
    fcntl = None

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKSHOP_TOOLKIT = Path("workshop/content/346010/3795335349/BuildArenaToolKit/BuildArenaToolKit.dll")
CARGO_SPEC_ENV = "BH_CARGO_MASS_SPEC"
CARGO_SWITCH_ENV = "BH_CARGO_MASS"


class RunError(RuntimeError):
    """besiege_cli did not produce a usable telemetry.csv."""


def _sha256(path: Path | None) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path is not None and path.is_file() else None


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return out.stdout


def _steamapps(game: Path) -> list[Path]:
    """Where a Steam library keeps appmanifest_346010.acf and workshop/ for this game:
    <library>/steamapps/common/Besiege (a Steam install), or <game>/steamapps (a
    steamcmd install into its own directory)."""
    return [game.parent.parent, game / "steamapps"]


def game_identity(besiege_data: Path, repo: Path, cargo_plugin: Path | None) -> dict:
    """What game, mod and 2.0 code a run uses; read from disk, never assumed."""
    data = Path(besiege_data)
    game = data.parent if data.name == "Besiege_Data" else data.parent.parent   # macOS: Besiege.app/Contents
    buildid = None
    for steamapps in _steamapps(game):
        acf = steamapps / "appmanifest_346010.acf"
        if acf.is_file():
            match = re.search(r'"buildid"\s*"(\d+)"', acf.read_text(encoding="utf-8", errors="replace"))
            buildid = match.group(1) if match else None
            break
    mod_xml = data / "Mods" / "BuildArenaToolKit" / "Mod.xml"
    version = None
    if mod_xml.is_file():
        match = re.search(r"<Version>([^<]+)</Version>", mod_xml.read_text(encoding="utf-8", errors="replace"))
        version = match.group(1).strip() if match else None
    workshop = next((s / WORKSHOP_TOOLKIT for s in _steamapps(game) if (s / WORKSHOP_TOOLKIT).is_file()), None)
    outside = ["--", ".", ":(exclude)buildarena1"]
    diff = _git(repo, "diff", "HEAD", *outside)
    local = data / "Mods" / "BuildArenaToolKit" / "BuildArenaToolKit.dll"
    return {
        "game_dir": str(game),
        "game_buildid": buildid,
        "toolkit_version": version,
        "toolkit_dll": str(local),
        "toolkit_dll_sha256": _sha256(local),
        "toolkit_workshop_dll": str(workshop) if workshop else None,
        "toolkit_workshop_dll_sha256": _sha256(workshop),
        "assembly_csharp_sha256": _sha256(data / "Managed" / "Assembly-CSharp.dll"),
        "cargo_plugin_sha256": _sha256(cargo_plugin),
        "v2_repo": str(repo),
        "v2_commit": _git(repo, "rev-parse", "HEAD").strip(),
        "v2_local_diff_files": [l for l in _git(repo, "diff", "--name-only", "HEAD", *outside).splitlines() if l],
        "v2_local_diff_sha256": hashlib.sha256(diff.encode()).hexdigest() if diff else None,
        "python": sys.executable,
        "launch_command": os.environ.get("BESIEGE_LAUNCH_COMMAND") or None,
    }


# Run with ``python -c`` in <repo>/control: start the game binary the way
# besiege_cli does when it does not use Steam, and wait for the ToolKit.
DIRECT_LAUNCH = '''
import sys
from besiege_cli.orchestrator import BesiegeOrchestrator
from besiege_cli.paths import besiege_install_root, mod_data_dir, resolve_besiege_data
from besiege_cli.process import find_besiege_pids, launch_via_exe
data = resolve_besiege_data(sys.argv[1])
if find_besiege_pids():
    sys.exit("A Besiege process is still running; this run needs a game it starts itself.")
orchestrator = BesiegeOrchestrator(mod_data_dir(data))
before = orchestrator.state_mtime()
launch_via_exe(besiege_install_root(data))
orchestrator.wait_for_heartbeat(timeout=float(sys.argv[2]), after_mtime=before)
print("Started the game binary directly; ToolKit heartbeat seen.")
'''


def default_lock_dir() -> Path:
    """$XDG_CACHE_HOME/buildarena1/locks, else ~/.cache/buildarena1/locks. A relative
    XDG_CACHE_HOME is ignored, as the XDG spec says: it would give processes started in
    different directories different locks for the same game."""
    cache = os.environ.get("XDG_CACHE_HOME", "").strip()
    base = Path(cache) if cache and Path(cache).is_absolute() else Path.home() / ".cache"
    return base / "buildarena1" / "locks"


def lock_name(besiege_data: Path) -> str:
    """One lock per game: named after the resolved Besiege_Data path."""
    digest = hashlib.sha256(str(Path(besiege_data).resolve()).encode("utf-8")).hexdigest()[:16]
    return f"game-{digest}.lock"


def _game_version(output_log: Path) -> str | None:
    if not output_log.is_file():
        return None
    with open(output_log, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.startswith("[Besiege] version:"):
                return line.split(":", 1)[1].split(",", 1)[0].strip()
    return None


def loaded_toolkit(output_log: Path) -> str | None:
    """Path of the BuildArenaToolKit.dll the game loaded, from its log (Unity's
    "Non platform assembly: <path> (this message is harmless)" line), or None."""
    if not output_log.is_file():
        return None
    with open(output_log, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            match = re.match(r"Non platform assembly: (.*BuildArenaToolKit\.dll)\b", line.strip())
            if match:
                return match.group(1).strip()
    return None


def toolkit_check(output_log: Path, identity: dict) -> dict:
    """Which ToolKit file the game loaded, from the game's log, against the two hashed before
    the run. ``check`` is ``matched``, ``mismatch``, ``no_game_log`` (the run found no log at
    Besiege_Data/output_log.txt; the game wrote it elsewhere) or ``no_toolkit_line``."""
    if not output_log.is_file():
        return {"check": "no_game_log", "dll": None, "sha256": None}
    loaded = loaded_toolkit(output_log)
    if loaded is None:
        return {"check": "no_toolkit_line", "dll": None, "sha256": None}
    digest = _sha256(Path(loaded))
    known = {identity.get("toolkit_dll_sha256"), identity.get("toolkit_workshop_dll_sha256")} - {None}
    return {"check": "matched" if digest in known else "mismatch", "dll": loaded, "sha256": digest}


def _telemetry_stats(csv_path: Path) -> dict:
    rows, times, ticks = 0, set(), set()
    with open(csv_path, encoding="utf-8") as handle:
        header = handle.readline().rstrip("\n").split(",")
        t_col, s_col = header.index("simulation_time"), header.index("sample_sequence")
        for line in handle:
            parts = line.split(",")
            rows += 1
            times.add(float(parts[t_col]))
            ticks.add(parts[s_col])
    return {"rows": rows, "ticks": len(ticks), "first_time": min(times) if times else None,
            "last_time": max(times) if times else None}


def summarize_cargo_log(path: Path) -> dict:
    """What the cargo-mass plugin did in one run, from its jsonl log."""
    if not path.is_file():
        return {"error": "no log"}
    out: dict = {"frames": 0, "overrides": 0, "post_step_mismatch_events": 0, "frame_gaps": 0}
    first = last = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        event = rec.get("event")
        if event == "start":
            out["start"] = rec
        elif event == "stop":
            out["stop"] = rec
        elif event == "override":
            out["overrides"] += 1
        elif event == "post_step_mismatch":
            out["post_step_mismatch_events"] += 1
        elif event in ("banks_start", "banks_end"):
            out[event] = rec["banks"]
        elif event == "banks":
            out.setdefault("bank_masses_seen", set()).update(b["rb_mass"] for b in rec["banks"])
        elif event in (None, "frame") and "mass" in rec:      # per-frame lines, as cargo_plugin.frames
            out["frames"] += 1
            if last is not None and "frame" in rec and "frame" in last and rec["frame"] != last["frame"] + 1:
                out["frame_gaps"] += 1
            first = first or rec
            last = rec
    out["first"], out["last"] = first, last
    if "bank_masses_seen" in out:
        out["bank_masses_seen"] = sorted(out["bank_masses_seen"], key=lambda v: (v is None, v))
    return out


def cargo_log_problem(summary: dict, telemetry: dict | None) -> str | None:
    """Why a run's cargo-mass log cannot back a score, or None: it needs a normal
    stop record with the post-step check count and no error, no post-step
    mismatch counted either way, per-frame records from frame 1 without gaps,
    and records up to the end of telemetry (within the largest clock offset
    evalkit/cargo_plugin.py allows)."""
    from .cargo_plugin import MAX_OFFSET
    if summary.get("error"):
        return f"CargoMass log: {summary['error']}"
    if not summary.get("frames"):
        return "CargoMass plugin set no mass"
    stop = summary.get("stop")
    if not isinstance(stop, dict):
        return "CargoMass log has no stop record (the run ended before the plugin closed its log)"
    if stop.get("error"):
        return f"CargoMass log ended with an error: {stop['error']}"
    if "post_step_mismatch" not in stop:
        return "CargoMass stop record has no post-step check count"
    mismatches = max(int(stop["post_step_mismatch"] or 0), summary.get("post_step_mismatch_events", 0))
    if mismatches:
        return f"CargoMass: {mismatches} physics steps ran with another mass"
    first, last = summary.get("first") or {}, summary.get("last") or {}
    if first.get("frame") != 1 or summary.get("frame_gaps"):
        return f"CargoMass per-frame records do not run from frame 1 without gaps (first {first.get('frame')}, gaps {summary.get('frame_gaps')})"
    if telemetry and telemetry.get("last_time") is not None and \
            last.get("frame", 0) < round(telemetry["last_time"] * 100) - MAX_OFFSET:
        return f"CargoMass log ends at frame {last.get('frame')}, telemetry at {telemetry['last_time']} s"
    return None


@dataclass
class LocalRunner:
    """``evalkit.sample.Runner`` that runs ``besiege_cli run`` from ``repo/control``."""
    besiege_data: Path
    repo: Path = REPO_ROOT
    # Installed cargo-mass plugin DLL (README section 6); None = not installed.
    cargo_plugin: Path | None = None
    lock_dir: Path | None = None          # default default_lock_dir()
    launch_timeout: float = 240.0
    command_timeout: float = 120.0
    reload_sandbox: bool = True
    keep_game: bool = False
    # Seconds to wait for another run on the same game to finish (runs are
    # serialised by the lock). 0 = fail at once with GameBusy.
    lock_wait: float = 0.0
    extra_args: tuple[str, ...] = ()
    python: str = field(default_factory=lambda: sys.executable)

    def __post_init__(self):
        # absolute now: the subprocesses run in <repo>/control
        self.besiege_data = Path(self.besiege_data).expanduser().resolve()
        self.repo = Path(self.repo).expanduser().resolve()
        if self.cargo_plugin is not None:
            self.cargo_plugin = Path(self.cargo_plugin).expanduser().resolve()

    def env(self) -> dict[str, str]:
        """Environment of the besiege_cli subprocesses: this process's, with the game's data path."""
        return {**os.environ, "BESIEGE_DATA_PATH": str(self.besiege_data)}

    def lock_path(self) -> Path:
        return Path(self.lock_dir or default_lock_dir()) / lock_name(self.besiege_data)

    def identity(self) -> dict:
        return game_identity(Path(self.besiege_data), Path(self.repo), self.cargo_plugin)

    def _cli(self, *args: str, env: dict | None = None, **kwargs) -> subprocess.CompletedProcess:
        return subprocess.run([self.python, "-m", "besiege_cli", *args], cwd=Path(self.repo) / "control",
                              env=env if env is not None else self.env(), **kwargs)

    def start_game_directly(self, env: dict, log: Path) -> None:
        """Start the game binary with ``env`` (not through Steam) and wait for the ToolKit."""
        with open(log, "w", encoding="utf-8") as out:
            proc = subprocess.run([self.python, "-c", DIRECT_LAUNCH, str(self.besiege_data), str(self.launch_timeout)],
                                  cwd=Path(self.repo) / "control", env=env, stdout=out, stderr=subprocess.STDOUT,
                                  timeout=self.launch_timeout + 120)
        if proc.returncode != 0:
            self.stop_game()
            tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-5:]
            raise RunError("Could not start the game binary directly for a run with cargo (a game started through "
                           "Steam would not get the cargo-mass variables); besiege_cli was not called. "
                           f"exit={proc.returncode}; last lines: " + " | ".join(tail))

    def stop_game(self) -> str:
        out = self._cli("quit", "--timeout", str(self.command_timeout), capture_output=True, text=True)
        return (out.stdout + out.stderr).strip()

    def args_for(self, plan: RunPlan) -> list[str]:
        wall = max(600.0, plan.duration * 30.0)
        args = plan.besiege_cli_args() + [
            "--launch-timeout", str(self.launch_timeout), "--timeout", str(self.command_timeout),
            "--controller-timeout", str(wall), "--playback-timeout", str(wall)]
        if self.reload_sandbox:
            args.append("--reload-sandbox")
        return args + list(self.extra_args)

    def run(self, plan: RunPlan) -> RunOutput:
        run_dir = plan.run_dir
        if run_dir.exists():
            raise RunError(f"{run_dir} already exists; every trial gets a fresh run directory.")
        if fcntl is None:
            return self._run_locked(plan, run_dir)
        lock_path = self.lock_path()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "a") as lock:
            deadline = time.monotonic() + self.lock_wait
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise GameBusy(f"Another run held {lock_path} for the whole wait "
                                       f"({self.lock_wait:g} s); nothing was run.") from exc
                    time.sleep(5.0)
            return self._run_locked(plan, run_dir)

    def _run_locked(self, plan: RunPlan, run_dir: Path) -> RunOutput:
        env = self.env()
        identity = self.identity()
        args = self.args_for(plan)
        plan.trial_dir.mkdir(parents=True, exist_ok=True)
        plugin = None
        if plan.cargo_curve is not None:
            dll = Path(self.cargo_plugin) if self.cargo_plugin else None
            if dll is None or not dll.is_file():
                raise RunError("This cell sets the cargo mass with the cargo-mass plugin (cargo_mass_mode "
                               "'plugin_curve'), and no plugin is installed (host config 'cargo_plugin': "
                               f"{self.cargo_plugin!r}). See buildarena1/README.md section 6.")
            spec = {**plan.cargo_curve, "log": str(run_dir / "cargo_mass.jsonl")}
            spec_path = plan.trial_dir / f"cargo_mass_spec_{plan.run_name}.json"
            spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
            env.update({CARGO_SWITCH_ENV: "1", CARGO_SPEC_ENV: str(spec_path)})
            plugin = {"dll": str(dll), "dll_sha256": _sha256(dll), "spec": spec, "spec_path": str(spec_path)}
            self.stop_game()          # the game must be started by this run to see the variables
            self.start_game_directly(env, plan.trial_dir / f"launch_{plan.run_name}.out")
            plugin["started_directly"] = True
        stdout_path = plan.trial_dir / f"besiege_cli_{plan.run_name}.out"
        started = time.time()
        code = None
        try:
            with open(stdout_path, "w", encoding="utf-8") as out:
                proc = self._cli(*args, env=env, stdout=out, stderr=subprocess.STDOUT,
                                 timeout=float(args[args.index("--controller-timeout") + 1]) + 600)
                code = proc.returncode
        finally:
            wall = time.time() - started
            output_log = Path(self.besiege_data) / "output_log.txt"
            if run_dir.is_dir() and output_log.is_file():
                shutil.copyfile(output_log, run_dir / "game_output_log.txt")
            stopped = None if self.keep_game else self.stop_game()
        telemetry = run_dir / "telemetry.csv"
        summary = json.loads((run_dir / "summary.json").read_text()) if (run_dir / "summary.json").is_file() else None
        meta = {
            "schema": "buildarena1.run_meta.v1",
            **identity,
            "evalkit_code": code_version(),
            "game_version": _game_version(run_dir / "game_output_log.txt"),
            "besiege_cli_args": args,
            "besiege_cli_exit": code,
            "besiege_cli_summary_result": summary.get("result") if summary else None,
            "wall_seconds": round(wall, 1),
            "game_stopped_after": stopped,
            "cargo_mass_plugin": plugin,
            "started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        }
        if telemetry.is_file():
            meta["telemetry"] = _telemetry_stats(telemetry)
        loaded = toolkit_check(run_dir / "game_output_log.txt", identity)
        meta["toolkit_loaded_check"] = loaded["check"]
        meta["toolkit_loaded_dll"] = loaded["dll"]
        meta["toolkit_loaded_dll_sha256"] = loaded["sha256"]
        toolkit_problem = None
        if loaded["check"] == "mismatch":
            toolkit_problem = (f"The game loaded the ToolKit from {loaded['dll']}, whose hash is neither of the two "
                               "recorded before the run (local Mods copy, Workshop copy).")
        plugin_problem = None
        if plugin is not None:
            plugin["log_summary"] = summarize_cargo_log(run_dir / "cargo_mass.jsonl")
            plugin_problem = cargo_log_problem(plugin["log_summary"], meta.get("telemetry"))
            if plugin_problem is None:
                meta["cargo_mass_log"] = str(run_dir / "cargo_mass.jsonl")
        (plan.trial_dir / f"{plan.run_name}_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        if toolkit_problem:
            raise RunError(toolkit_problem)
        if plugin_problem and code == 0 and telemetry.is_file():
            raise RunError(plugin_problem)
        if code != 0 or not telemetry.is_file():
            tail = stdout_path.read_text(encoding="utf-8", errors="replace").splitlines()[-15:]
            raise RunError(f"besiege_cli run exit={code}, telemetry.csv {'present' if telemetry.is_file() else 'missing'}; "
                           "last lines: " + " | ".join(tail))
        return RunOutput(telemetry, meta)
