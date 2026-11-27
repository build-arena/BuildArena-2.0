"""LocalRunner without a game: a stand-in besiege_cli package in a scratch
checkout records how it is called (arguments, working directory and the
environment variables the runner sets). Covers the host paths, the lock
shared by checkouts that use one game, and how a run with cargo starts the
game. Needs neither a 1.0 clone nor game data."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from . import support
from buildarena1.evalkit import cli
from buildarena1.evalkit.runner import LocalRunner, RunError
from buildarena1.evalkit.sample import GameBusy, RunPlan

try:
    import fcntl
except ImportError:          # Windows
    fcntl = None

FAKE_MAIN = '''
import json, os, sys
with open(os.environ["FAKE_CLI_LOG"], "a") as handle:
    handle.write(json.dumps({"call": sys.argv[1], "argv": sys.argv[1:], "cwd": os.getcwd(),
                             "env": {k: os.environ.get(k) for k in ("BESIEGE_DATA_PATH", "BH_CARGO_MASS",
                                                                    "BH_CARGO_MASS_SPEC")}}) + "\\n")
if sys.argv[1] == "run":
    sys.exit(1)
'''


FAKE_PATHS = """
from pathlib import Path
def resolve_besiege_data(p): return Path(p)
def mod_data_dir(d): return Path(d)
def besiege_install_root(d): return Path(d).parent
"""
FAKE_PROCESS = """
import json, os
def find_besiege_pids(): return []
def launch_via_exe(root):
    with open(os.environ["FAKE_CLI_LOG"], "a") as handle:
        handle.write(json.dumps({"call": "launch_via_exe", "root": str(root),
                                 "env": {k: os.environ.get(k) for k in ("BESIEGE_DATA_PATH", "BH_CARGO_MASS",
                                                                        "BH_CARGO_MASS_SPEC")}}) + "\\n")
    if os.environ.get("FAKE_LAUNCH_FAIL"):
        raise RuntimeError("no game here")
"""
FAKE_ORCHESTRATOR = """
class BesiegeOrchestrator:
    def __init__(self, data_dir, poll_interval=0.25): pass
    def state_mtime(self): return None
    def wait_for_heartbeat(self, timeout, after_mtime=None): return {}
"""


def fake_checkout(root: Path) -> Path:
    """A directory shaped like this repository's checkout with a stand-in besiege_cli."""
    pkg = root / "control" / "besiege_cli"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "__main__.py").write_text(FAKE_MAIN)
    (pkg / "paths.py").write_text(FAKE_PATHS)
    (pkg / "process.py").write_text(FAKE_PROCESS)
    (pkg / "orchestrator.py").write_text(FAKE_ORCHESTRATOR)
    return root


class Runner(LocalRunner):
    def identity(self) -> dict:        # the scratch checkout is not a git repository
        return {}


def plan(tmp: Path, cargo: bool = False) -> RunPlan:
    return RunPlan(tmp / "trial_1", tmp / "m.bsg", tmp / "controller.py", "BARREN EXPANSE", 10.0, 25, 25,
                   run_name="run", cargo_curve={"cargo_guid": "g"} if cargo else None)


def calls(log: Path) -> list[dict]:
    return [json.loads(l) for l in log.read_text().splitlines()] if log.is_file() else []


class RunnerPaths(unittest.TestCase):
    def setUp(self):
        self.tmp = support.scratch("runner_paths_")
        self.old_cwd = os.getcwd()
        self.old_env = dict(os.environ)
        os.environ["FAKE_CLI_LOG"] = str(self.tmp / "cli.jsonl")
        os.environ["XDG_CACHE_HOME"] = str(self.tmp / "cache")

    def tearDown(self):
        os.chdir(self.old_cwd)
        os.environ.clear()
        os.environ.update(self.old_env)

    def test_host_paths_are_made_absolute(self):
        os.chdir(self.tmp)
        (self.tmp / "host.json").write_text(json.dumps({
            "v1_root": "../BuildArena-1.0", "collider_dump": ".local/collider_dump.toml",
            "besiege_data": "game/Besiege_Data", "v2_root": "checkout", "cargo_plugin": None}))
        host = cli.HostConfig.read(self.tmp / "host.json")
        real = self.tmp.resolve()
        self.assertEqual(host.besiege_data, str(real / "game" / "Besiege_Data"))
        self.assertEqual(host.v1_root, str(real.parent / "BuildArena-1.0"))
        self.assertEqual(host.collider_dump, str(real / ".local" / "collider_dump.toml"))
        self.assertIsNone(host.cargo_plugin)
        self.assertTrue(Path(host.runner().besiege_data).is_absolute())

    def test_subprocesses_get_the_absolute_data_path(self):
        repo = fake_checkout(self.tmp / "checkout")
        os.chdir(self.tmp)
        runner = Runner(besiege_data=Path("game/Besiege_Data"), repo=Path("checkout"))
        runner.stop_game()
        quit_call, = calls(self.tmp / "cli.jsonl")
        self.assertEqual(quit_call["call"], "quit")
        self.assertEqual(quit_call["env"]["BESIEGE_DATA_PATH"], str((self.tmp / "game" / "Besiege_Data").resolve()))
        self.assertEqual(Path(quit_call["cwd"]).resolve(), (repo / "control").resolve())


@unittest.skipIf(fcntl is None, "the runner's lock is POSIX only")
class SharedLock(unittest.TestCase):
    def setUp(self):
        self.tmp = support.scratch("runner_lock_")
        self.old_env = dict(os.environ)
        os.environ["FAKE_CLI_LOG"] = str(self.tmp / "cli.jsonl")
        os.environ["XDG_CACHE_HOME"] = str(self.tmp / "cache")

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old_env)

    def test_two_checkouts_of_one_game_share_the_lock(self):
        game = self.tmp / "game" / "Besiege_Data"
        a = Runner(besiege_data=game, repo=fake_checkout(self.tmp / "a"))
        b = Runner(besiege_data=game, repo=fake_checkout(self.tmp / "b"), lock_wait=0)
        other = Runner(besiege_data=self.tmp / "game2" / "Besiege_Data", repo=self.tmp / "b")
        self.assertEqual(a.lock_path(), b.lock_path())
        self.assertNotEqual(a.lock_path(), other.lock_path())
        self.assertTrue(str(a.lock_path()).startswith(str(self.tmp / "cache")))
        a.lock_path().parent.mkdir(parents=True, exist_ok=True)
        with open(a.lock_path(), "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(GameBusy):
                b.run(plan(self.tmp))
        self.assertEqual(calls(self.tmp / "cli.jsonl"), [])      # nothing was started
        self.assertFalse((self.tmp / "trial_1").exists())


@unittest.skipIf(fcntl is None, "the runner's lock is POSIX only")
class CargoRunStartsTheGame(unittest.TestCase):
    """A run with cargo starts the game binary itself, with the plugin's variables,
    before besiege_cli run (which would otherwise ask Steam first)."""

    def setUp(self):
        self.tmp = support.scratch("runner_cargo_")
        self.old_env = dict(os.environ)
        os.environ["FAKE_CLI_LOG"] = str(self.tmp / "cli.jsonl")
        os.environ["XDG_CACHE_HOME"] = str(self.tmp / "cache")
        os.environ.pop("FAKE_LAUNCH_FAIL", None)
        (self.tmp / "plugin.dll").write_bytes(b"plugin")
        self.runner = Runner(besiege_data=self.tmp / "game" / "Besiege_Data", repo=fake_checkout(self.tmp / "repo"),
                             cargo_plugin=self.tmp / "plugin.dll")

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old_env)

    def test_cargo_run_starts_the_game_directly(self):
        with self.assertRaises(RunError):            # the stand-in run exits 1
            self.runner.run(plan(self.tmp, cargo=True))
        log = calls(self.tmp / "cli.jsonl")
        self.assertEqual([c["call"] for c in log], ["quit", "launch_via_exe", "run", "quit"])
        spec = str(self.tmp / "trial_1" / "cargo_mass_spec_run.json")
        for c in log[1:3]:
            self.assertEqual((c["env"]["BH_CARGO_MASS"], c["env"]["BH_CARGO_MASS_SPEC"]), ("1", spec))
        self.assertEqual(log[1]["root"], str((self.tmp / "game").resolve()))

    def test_a_failed_direct_start_stops_before_besiege_cli_run(self):
        os.environ["FAKE_LAUNCH_FAIL"] = "1"
        with self.assertRaisesRegex(RunError, "directly"):
            self.runner.run(plan(self.tmp, cargo=True))
        self.assertEqual([c["call"] for c in calls(self.tmp / "cli.jsonl")], ["quit", "launch_via_exe", "quit"])

    def test_a_run_without_cargo_leaves_the_start_to_besiege_cli(self):
        with self.assertRaises(RunError):
            self.runner.run(plan(self.tmp, cargo=False))
        self.assertEqual([c["call"] for c in calls(self.tmp / "cli.jsonl")], ["run", "quit"])


class Fingerprint(unittest.TestCase):
    """The batch fingerprint follows both ToolKit copies and the evaluation code's contents."""

    def test_workshop_toolkit_is_in_the_fingerprint(self):
        from buildarena1.evalkit.config import EvalConfig
        from buildarena1.evalkit.sample import run_fingerprint

        class Fake:
            def __init__(self, workshop):
                self.workshop = workshop

            def identity(self):
                return {"toolkit_dll_sha256": "local", "toolkit_workshop_dll_sha256": self.workshop}
        self.assertNotEqual(run_fingerprint(Fake("w1"), EvalConfig()), run_fingerprint(Fake("w2"), EvalConfig()))

    def test_loaded_toolkit_is_read_from_the_game_log(self):
        from buildarena1.evalkit.runner import loaded_toolkit
        log = support.scratch("toolkit_log_") / "output_log.txt"
        log.write_text("x\nNon platform assembly: /g/Besiege_Data/Mods/BuildArenaToolKit/BuildArenaToolKit.dll "
                       "(this message is harmless)\n[BuildArenaToolKit] loaded\n")
        self.assertEqual(loaded_toolkit(log), "/g/Besiege_Data/Mods/BuildArenaToolKit/BuildArenaToolKit.dll")

    def test_two_uncommitted_edits_give_two_code_versions(self):
        import subprocess
        from buildarena1.evalkit import version
        root = support.scratch("code_version_")
        code = root / version.CODE_DIR / "a.py"
        code.parent.mkdir(parents=True)
        code.write_text("x = 1\n")
        git = lambda *a: subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True)
        git("init", "-q")
        git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "add", ".")
        git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "c")
        clean = version.code_version(root)
        code.write_text("x = 2\n")
        first = version.code_version(root)
        code.write_text("x = 3\n")
        second = version.code_version(root)
        self.assertEqual((first["commit"], first["dirty_files"]), (second["commit"], second["dirty_files"]))
        self.assertEqual(len({clean["content_sha256"], first["content_sha256"], second["content_sha256"]}), 3)
        from buildarena1.evalkit.sample import FINGERPRINT_CODE_KEYS
        self.assertIn("content_sha256", FINGERPRINT_CODE_KEYS)


class SmallContracts(unittest.TestCase):
    """The plugin-log summary counts the lines the judge counts, the lock directory
    does not depend on the working directory, and the ToolKit check says when it
    could not be made."""

    def test_summary_counts_only_per_frame_lines(self):
        from buildarena1.evalkit.runner import summarize_cargo_log
        log = support.scratch("summary_frames_") / "cargo_mass.jsonl"
        lines = [{"event": "start"}, {"frame": 1, "mass": 5.0, "y": 1.0}, {"event": "frame", "frame": 2, "mass": 5.0},
                 {"event": "observe", "frame": 2, "mass": 9.0, "y": 1.0}, {"event": "stop", "post_step_mismatch": 0}]
        log.write_text("\n".join(json.dumps(l) for l in lines) + "\n")
        self.assertEqual(summarize_cargo_log(log)["frames"], 2)

    def test_a_relative_xdg_cache_home_is_ignored(self):
        from buildarena1.evalkit.runner import default_lock_dir
        old = os.environ.get("XDG_CACHE_HOME")
        os.environ["XDG_CACHE_HOME"] = "relative/cache"
        try:
            self.assertEqual(default_lock_dir(), Path.home() / ".cache" / "buildarena1" / "locks")
        finally:
            if old is None:
                os.environ.pop("XDG_CACHE_HOME")
            else:
                os.environ["XDG_CACHE_HOME"] = old

    def test_toolkit_check_says_when_there_is_no_log(self):
        from buildarena1.evalkit.runner import toolkit_check
        tmp = support.scratch("toolkit_check_")
        dll = tmp / "BuildArenaToolKit.dll"
        dll.write_bytes(b"toolkit")
        import hashlib
        digest = hashlib.sha256(b"toolkit").hexdigest()
        self.assertEqual(toolkit_check(tmp / "missing.txt", {})["check"], "no_game_log")
        log = tmp / "output_log.txt"
        log.write_text("nothing about mods\n")
        self.assertEqual(toolkit_check(log, {})["check"], "no_toolkit_line")
        log.write_text(f"Non platform assembly: {dll} (this message is harmless)\n")
        self.assertEqual(toolkit_check(log, {"toolkit_dll_sha256": digest})["check"], "matched")
        self.assertEqual(toolkit_check(log, {"toolkit_dll_sha256": "other"})["check"], "mismatch")


class NextWithoutGameData(unittest.TestCase):
    """cli.main prints ``next`` as a subcommand name; needs neither 1.0 nor game data."""

    def test_next_is_printed_as_the_subcommand(self):
        import contextlib
        import io
        from buildarena1.evalkit import stepwise
        tmp = support.scratch("cli_next_bare_")
        host = tmp / "host.json"
        host.write_text(json.dumps({"v1_root": "v1", "collider_dump": "c.toml", "besiege_data": "g/Besiege_Data"}))
        real = stepwise.prepare
        try:
            for internal, printed in (("run_trial", "run-trial"), ("controller_input", "controller-input"),
                                      ("parse_reply", "parse-reply"), ("finalize", "finalize"), ("done", "done")):
                stepwise.prepare = lambda *a, _n=internal, **k: {"status": "prepared", "next": _n}
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    code = cli.main(["--host", str(host), "prepare", str(tmp / "b" / "m" / "lift_soft" / "s01")])
                self.assertEqual((code, json.loads(buf.getvalue())["next"]), (0, printed))
        finally:
            stepwise.prepare = real


if __name__ == "__main__":
    unittest.main()
