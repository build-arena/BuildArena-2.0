"""Command line for the stepwise evaluation (buildarena1/README.md section 4).

    python -m buildarena1.evalkit.cli --host HOST.json prepare SAMPLE [--attempt A] [--config CFG.json]
    python -m buildarena1.evalkit.cli --host HOST.json controller-input SAMPLE --attempt A
    python -m buildarena1.evalkit.cli --host HOST.json parse-reply SAMPLE --attempt A --reply-file F [--usage-file U]
    python -m buildarena1.evalkit.cli --host HOST.json run-trial SAMPLE --attempt A
    python -m buildarena1.evalkit.cli --host HOST.json finalize SAMPLE --attempt A [--reason TEXT [--usage-file U]]
    python -m buildarena1.evalkit.cli --host HOST.json summarize ROOT [--batch NAME] [--markdown OUT.md] [--json OUT.json]
    python -m buildarena1.evalkit.cli --host HOST.json identity

Every command prints one JSON object on stdout and nothing else there (the
game and besiege_cli output go to files in the trial directory); the exit code
is 0 unless the command itself failed (a sample that fails the task is not a
command failure). When run-trial cannot get the game (another run holds it past
the host's lock_wait) it exits 1 with ``"game_busy": true`` and leaves the
attempt's state as it was; call the same step again later. ``next`` in the
output is the subcommand to call next (``run-trial``, ``controller-input``,
``parse-reply``, ``finalize``), or ``done``. ``--reply-file -`` reads the reply from stdin, so a driver
on another machine can pipe the control agent's text through ssh.

The control agent's model call is not made here: ``controller-input`` prints
the system prompt and messages, the caller sends them to the model, and
``parse-reply`` takes the reply text back.

HOST.json names the files on this host (keys of ``HostConfig``, README
section 4). CFG.json holds ``EvalConfig`` fields to override; any file missing
``lift_success_rule`` uses ``ba1``. Set it to ``height`` or omit ``--config``
to use the new default. The full config lands in result.json.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from dataclasses import dataclass, fields
from pathlib import Path

from .config import EvalConfig, Paths
from .prompts import ControllerPrompts, controller_system_prompt
from . import stepwise
from .sample import FingerprintMismatch, GameBusy


REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class HostConfig:
    v1_root: str          # git clone of BuildArena 1.0 (controller prompt, support level files)
    collider_dump: str    # this checkout's COLLIDER_DUMP_PATH, written by scripts/setup.py
    besiege_data: str     # this checkout's BESIEGE_DATA_PATH
    v2_root: str = str(REPO_ROOT)
    tasks_dir: str = str(REPO_ROOT / "buildarena1" / "tasks" / "ba1_blocks")
    cargo_plugin: str | None = None    # installed cargo-mass plugin DLL (README section 6)
    launch_timeout: float = 240.0
    command_timeout: float = 120.0
    reload_sandbox: bool = True
    # seconds a run waits for another run on the same game copy (one game, serialised by a lock)
    lock_wait: float = 1800.0

    PATH_KEYS = ("v1_root", "collider_dump", "besiege_data", "v2_root", "tasks_dir", "cargo_plugin")

    @classmethod
    def read(cls, path: Path) -> "HostConfig":
        """Read HOST.json. Relative paths in it are taken from the working directory
        (the repository root in the README) and made absolute here."""
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        known = {f.name for f in fields(cls)}
        unknown = set(raw) - known - {"comment"}
        if unknown:
            raise ValueError(f"Unknown host config keys {sorted(unknown)} in {path}")
        values = {k: v for k, v in raw.items() if k in known}
        for key in cls.PATH_KEYS:
            if values.get(key) is not None:
                values[key] = str(Path(values[key]).expanduser().resolve())
        return cls(**values)

    def paths(self) -> Paths:
        return Paths(Path(self.v1_root), Path(self.v2_root), Path(self.collider_dump), Path(self.besiege_data))

    def runner(self):
        from .runner import LocalRunner
        return LocalRunner(besiege_data=Path(self.besiege_data), repo=Path(self.v2_root),
                           cargo_plugin=Path(self.cargo_plugin) if self.cargo_plugin else None,
                           launch_timeout=self.launch_timeout, command_timeout=self.command_timeout,
                           reload_sandbox=self.reload_sandbox, lock_wait=self.lock_wait)


def controller_prompts(task_file: Path, v1_root: Path) -> ControllerPrompts:
    """Controller system prompt with the same block list and 2.0 name note the
    build session got (the task file's system segments), filled into 1.0's
    controller.system_message."""
    task = json.loads(Path(task_file).read_text(encoding="utf-8"))
    segs = task["system_segments"]
    idx = [i for i, s in enumerate(segs) if any(e[0] == "{available_blks}" for e in s.get("edits") or [])]
    if len(idx) != 1:
        raise ValueError(f"{task_file}: expected one segment that fills {{available_blks}}, found {len(idx)}.")
    text = segs[idx[0]]["text"]
    head, tail = "<available_blocks>\n", "\n</available_blocks>"
    if text.count(head) != 1 or text.count(tail) != 1:
        raise ValueError(f"{task_file}: block list segment is not delimited as 1.0's template is.")
    available = text.split(head, 1)[1].split(tail, 1)[0]
    note_seg = segs[idx[0] + 1] if idx[0] + 1 < len(segs) else None
    if note_seg is None or note_seg.get("kind") != "new":
        raise ValueError(f"{task_file}: no name note after the block list segment.")
    return ControllerPrompts(controller_system_prompt(v1_root, available, note_seg["text"]), v1_root)


def command_name(step: str) -> str:
    """The subcommand that runs a step: state.json spells steps with underscores
    (``run_trial``), the command line with hyphens (``run-trial``); ``finalize``
    and ``done`` are the same in both."""
    return step.replace("_", "-")


def _config(path: str | None) -> EvalConfig:
    if not path:
        return EvalConfig()
    return EvalConfig.from_json(json.loads(Path(path).read_text(encoding="utf-8")), source=path)


def _read_text(path: str) -> str:
    return sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", type=Path, required=True)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare"); p.add_argument("sample"); p.add_argument("--attempt"); p.add_argument("--config")
    for name in ("controller-input", "run-trial"):
        q = sub.add_parser(name); q.add_argument("sample"); q.add_argument("--attempt", required=True)
    q = sub.add_parser("parse-reply"); q.add_argument("sample"); q.add_argument("--attempt", required=True)
    q.add_argument("--reply-file", required=True); q.add_argument("--usage-file")
    q = sub.add_parser("finalize"); q.add_argument("sample"); q.add_argument("--attempt", required=True)
    q.add_argument("--reason"); q.add_argument("--usage-file")
    q = sub.add_parser("summarize"); q.add_argument("root"); q.add_argument("--batch")
    q.add_argument("--markdown"); q.add_argument("--json")
    sub.add_parser("identity")
    args = parser.parse_args(argv)
    host = HostConfig.read(args.host)
    real_stdout = sys.stdout
    sys.stdout = sys.stderr          # 2.0 code prints progress; keep stdout for the JSON answer
    try:
        if args.cmd == "prepare":
            out = stepwise.prepare(Path(args.sample), paths=host.paths(), cfg=_config(args.config),
                                   attempt=args.attempt, runner=host.runner())
        elif args.cmd == "controller-input":
            cell = Path(args.sample).resolve().parent.name
            prompts = controller_prompts(Path(host.tasks_dir) / f"{cell}.json", Path(host.v1_root))
            out = stepwise.controller_input(Path(args.sample), args.attempt, prompts=prompts)
        elif args.cmd == "parse-reply":
            usage = json.loads(_read_text(args.usage_file)) if args.usage_file else {}
            out = stepwise.parse_reply(Path(args.sample), args.attempt, reply=_read_text(args.reply_file), usage=usage)
        elif args.cmd == "run-trial":
            from .rebuild import use_v2
            use_v2(host.paths())
            out = stepwise.run_trial_step(Path(args.sample), args.attempt, runner=host.runner())
        elif args.cmd == "finalize":
            usage = json.loads(_read_text(args.usage_file)) if args.usage_file else None
            res = stepwise.finalize(Path(args.sample), args.attempt, reason=args.reason, usage=usage)
            out = {k: res.get(k) for k in ("status", "success", "metric", "runtime", "num_blocks")}
            out["trials"] = [{k: t.get(k) for k in ("trial", "status", "pass", "metric", "stop_reason", "error")}
                             for t in res.get("trials", [])]
            out["next"] = "done"
        elif args.cmd == "summarize":
            from . import summarize as S
            rows = S.summarize(S.latest_results(Path(args.root), args.batch))
            text = S.markdown(rows)
            if args.markdown:
                Path(args.markdown).write_text(text, encoding="utf-8")
            if args.json:
                Path(args.json).write_text(json.dumps({"note": S.SCOPE_NOTE, "rows": rows}, indent=2), encoding="utf-8")
            out = {"note": S.SCOPE_NOTE, "rows": rows, "markdown": text, "next": "done"}
        elif args.cmd == "identity":
            out = host.runner().identity()
        code = 0
    except GameBusy as exc:   # nothing ran and the state is unchanged: the driver retries the same step
        out = {"error": f"GameBusy: {exc}", "game_busy": True, "retry_step": args.cmd}
        code = 1
    except FingerprintMismatch as exc:   # nothing ran, state unchanged; a person decides (new batch or redeploy)
        out = {"error": f"FingerprintMismatch: {exc}", "fingerprint_mismatch": True}
        code = 1
    except Exception as exc:  # noqa: BLE001 - reported as JSON to the remote driver
        out = {"error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}
        code = 1
    finally:
        sys.stdout = real_stdout
    if isinstance(out.get("next"), str):
        out["next"] = command_name(out["next"])
    sys.stdout.write(json.dumps(out, ensure_ascii=False, default=str) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
