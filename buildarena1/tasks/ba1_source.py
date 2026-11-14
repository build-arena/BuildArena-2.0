"""Read the parts of BuildArena 1.0 that the task files copy.

BuildArena 1.0 (https://github.com/AI4Science-WestlakeU/BuildArena, CC BY-NC 4.0) is
not part of this repository. Point ``BA1_ROOT`` at a git clone of it; every file is read
with ``git show`` at ``BA1_COMMIT``, so the result does not depend on what that clone has
checked out. Files in 1.0 are stored with CRLF line endings; 1.0 itself opened them in
Python text mode, which turns CRLF into LF before YAML parsing, so ``_text()`` does the
same. Everything returned here is what 1.0's own code would have produced at run time.
"""
from __future__ import annotations

import ast
import os
import subprocess
import tempfile
from pathlib import Path

import yaml

BA1_URL = "https://github.com/AI4Science-WestlakeU/BuildArena"
BA1_COMMIT = "c3005b90686ea6dae9a69a293e7f6395eae77c9c"


def ba1_root() -> Path:
    raw = os.environ.get("BA1_ROOT", "").strip()
    if not raw:
        raise RuntimeError(f"Set BA1_ROOT to a git clone of {BA1_URL} (it must contain commit {BA1_COMMIT[:7]}).")
    return Path(raw).expanduser().resolve()


def _git(*args: str) -> bytes:
    return subprocess.run(["git", "-C", str(ba1_root()), *args], check=True, capture_output=True).stdout


def show(path: str) -> str:
    """File text at BA1_COMMIT. Bytes are decoded as UTF-8; line endings are kept as stored."""
    return _git("show", f"{BA1_COMMIT}:{path}").decode("utf-8")


def ls(path: str) -> list[str]:
    return [line for line in _git("ls-tree", "--name-only", f"{BA1_COMMIT}:{path}").decode("utf-8").splitlines() if line]


# 1.0 levels.yaml document order: three transport, three support, three lift.
CELLS = (
    ("transport", "soft"), ("transport", "medium"), ("transport", "hard"),
    ("support", "soft"), ("support", "medium"), ("support", "hard"),
    ("lift", "soft"), ("lift", "medium"), ("lift", "hard"),
)


def cell_name(category: str, level: str) -> str:
    return f"{category}_{level}"


def _text(path: str) -> str:
    raw = show(path)
    return raw.replace("\r\n", "\n").replace("\r", "\n")


def levels() -> list[dict]:
    docs = [d for d in yaml.safe_load_all(_text("levels.yaml")) if d is not None]
    got = [(d["category"], d["level"]) for d in docs]
    if tuple(got) != CELLS:
        raise ValueError(f"levels.yaml order changed: {got}")
    return docs


def level_task(category: str, level: str) -> str:
    for doc in levels():
        if (doc["category"], doc["level"]) == (category, level):
            return doc["task"]
    raise KeyError((category, level))


def prompt_yaml() -> dict:
    return yaml.safe_load(_text("prompt.yaml"))


def planner_message() -> str:
    return prompt_yaml()["agents"]["planner"]["system_message"]


def prompt_yaml_lines() -> list[str]:
    """prompt.yaml as 1-based-indexable lines (index 0 is a dummy), LF endings removed."""
    return [""] + _text("prompt.yaml").split("\n")


def blocks_call_output() -> str:
    """The `{available_blks}` text: 1.0's own `Blocks()()` run on 1.0's block configs.

    The class source is cut out of spatial/build.py with `ast` and executed unchanged, so
    the formatting is 1.0's code, not a re-implementation. `Blocks.__init__` lists
    ./blocks/configs with os.listdir, whose order depends on the file system; the order
    here is sorted file names. Only that one call
    is replaced, inside the namespace the class runs in."""
    src = _text("spatial/build.py")
    tree = ast.parse(src)
    keep = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Blocks":
            keep.append(node)
        elif isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "WaiveableBlocks" for t in node.targets
        ):
            keep.append(node)
    if len(keep) != 2:
        raise ValueError("could not find Blocks and WaiveableBlocks in 1.0 spatial/build.py")
    module = ast.Module(body=keep, type_ignores=[])

    class _SortedOs:
        def __getattr__(self, name):
            return getattr(os, name)

        @staticmethod
        def listdir(path="."):
            return sorted(os.listdir(path))

    names = ls("blocks/configs")
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "blocks" / "configs"
        cfg.mkdir(parents=True)
        for name in names:
            # keep the stored bytes (CRLF); 1.0 opens them in text mode
            (cfg / name).write_bytes(show(f"blocks/configs/{name}").encode("utf-8"))
        ns: dict = {"os": _SortedOs(), "yaml": yaml, "Dict": dict, "__name__": "ba1_blocks"}
        old = os.getcwd()
        os.chdir(tmp)
        try:
            exec(compile(module, "1.0:spatial/build.py", "exec"), ns)
            out = ns["Blocks"]()()
        finally:
            os.chdir(old)
    return out


def block_configs() -> dict[str, dict]:
    out = {}
    for name in ls("blocks/configs"):
        if name.endswith(".yaml"):
            data = yaml.safe_load(_text(f"blocks/configs/{name}"))
            out[data["name"]] = data
    return out
