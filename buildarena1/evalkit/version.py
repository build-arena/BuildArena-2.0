"""Which commit of this repository the running evaluation code is.

``code_version()`` reports the checkout's HEAD and the files under
buildarena1/evalkit/ that differ from it. A copy installed somewhere else may carry a
``DEPLOYED.json`` at the repository root (keys: ``commit``, ``dirty``,
``deployed_at`` and ``files``, a map from a path under the root to its
sha256); then the copied files are re-hashed, so a copy edited after it was
made shows ``files_match: false`` with the files named. In all cases, including
a plain copy without Git metadata or a deployment manifest,
``content_sha256`` is a digest of the current contents of every .py file
under buildarena1/evalkit/, so two different uncommitted edits give two
different values. run_meta.json, result.json and the batch fingerprint
record this.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CODE_DIR = "buildarena1/evalkit"
MANIFEST = "DEPLOYED.json"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def content_sha256(root: Path = ROOT) -> str:
    """sha256 over (path, sha256 of contents) of the .py files under CODE_DIR, as they are now."""
    files = sorted(p for p in (root / CODE_DIR).rglob("*.py") if "__pycache__" not in p.parts)
    listing = "".join(f"{p.relative_to(root).as_posix()}\0{sha256_file(p)}\n" for p in files)
    return hashlib.sha256(listing.encode("utf-8")).hexdigest()


def code_version(root: Path = ROOT) -> dict:
    manifest = root / MANIFEST
    if manifest.is_file():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        files = data.get("files") or {}
        changed = sorted(p for p, h in files.items()
                         if not (root / p).is_file() or sha256_file(root / p) != h)
        listed = set(files)
        extra = sorted(str(p.relative_to(root)) for p in (root / CODE_DIR).glob("*.py")
                       if str(p.relative_to(root)) not in listed)
        return {"source": "deployed", "commit": data.get("commit"), "dirty_at_deploy": data.get("dirty"),
                "deployed_at": data.get("deployed_at"), "files_match": not changed and not extra,
                "changed_files": changed, "unlisted_files": extra, "content_sha256": content_sha256(root)}
    try:
        commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True,
                                check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--", CODE_DIR],
                               capture_output=True, text=True, check=True).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError):
        return {"source": "unknown", "commit": None, "files_match": None,
                "content_sha256": content_sha256(root)}
    return {"source": "git", "commit": commit, "dirty_files": [l[3:] for l in dirty], "files_match": not dirty,
            "content_sha256": content_sha256(root)}
