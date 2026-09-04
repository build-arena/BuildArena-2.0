"""Optional assembly pin against a ToolKit Release manifest.

Steam Workshop can only ship the DLL and ``Mod.xml``. Those installs have no
``compatibility_manifest.json``; the pin is skipped and nothing is printed.
If a Release folder later includes the manifest, the local game assemblies
must match it or setup/run abort.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

COMPATIBILITY_MANIFEST_SCHEMA = "buildarena.compatibility_manifest.v1"
COMPATIBILITY_MANIFEST_FILE = "compatibility_manifest.json"

_TRACKED_ASSEMBLIES = (
    ("Assembly-CSharp.dll", "assembly_csharp_sha256"),
    ("Assembly-CSharp-firstpass.dll", "assembly_csharp_firstpass_sha256"),
)


class CompatibilityError(RuntimeError):
    """The installed ToolKit Release does not match the local game build."""


def installed_toolkit_dir(besiege_data: Path) -> Path:
    """The single installed BuildArenaToolKit mod folder. Zero or multiple
    candidates are both hard errors: there is exactly one supported layout."""
    mods_dir = besiege_data / "Mods"
    candidates = sorted(mods_dir.glob("BuildArenaToolKit*")) if mods_dir.is_dir() else []
    candidates = [path for path in candidates if path.is_dir()]
    if not candidates:
        raise CompatibilityError(
            f"BuildArenaToolKit is not installed under {mods_dir}. Install the Release ZIP's "
            "BuildArenaToolKit folder there."
        )
    if len(candidates) > 1:
        names = ", ".join(path.name for path in candidates)
        raise CompatibilityError(
            f"Multiple installed BuildArenaToolKit folders under {mods_dir}: {names}. "
            "Remove all but one; the loader would otherwise pick one arbitrarily."
        )
    return candidates[0]


def verify_compatibility(besiege_data: Path) -> str:
    """Verify the installed ToolKit against the local game build.

    Returns a one-line human-readable status. Raises CompatibilityError when
    the mod is missing, ambiguous, the manifest is malformed, or the game
    assemblies do not match the Release manifest.
    """
    toolkit_dir = installed_toolkit_dir(besiege_data)
    manifest_path = toolkit_dir / COMPATIBILITY_MANIFEST_FILE
    if not manifest_path.is_file():
        return ""

    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if manifest.get("schema") != COMPATIBILITY_MANIFEST_SCHEMA:
        raise CompatibilityError(
            f"Unsupported compatibility manifest schema {manifest.get('schema')!r} in "
            f"{manifest_path}; this Python checkout expects {COMPATIBILITY_MANIFEST_SCHEMA}. "
            "Update the repository or install a matching Release."
        )

    managed = besiege_data / "Managed"
    mismatches: list[str] = []
    for assembly_name, manifest_key in _TRACKED_ASSEMBLIES:
        assembly_path = managed / assembly_name
        if not assembly_path.is_file():
            mismatches.append(f"{assembly_name}: not found at {assembly_path}")
            continue
        digest = hashlib.sha256(assembly_path.read_bytes()).hexdigest()
        expected = str(manifest.get(manifest_key, ""))
        if digest != expected:
            mismatches.append(
                f"{assembly_name}: local sha256 {digest} != release {expected or '(missing)'}"
            )
    if mismatches:
        raise CompatibilityError(
            "Local game assemblies do not match ToolKit Release "
            f"{manifest.get('toolkit_version')!r} (besiege build "
            f"{manifest.get('besiege_build')!r}): " + "; ".join(mismatches) + ". "
            "Install the Release that matches this game build, or wait for one; "
            "control stays non-ready until then."
        )
    return (
        f"Compatibility verified: ToolKit Release {manifest.get('toolkit_version')} / "
        f"Besiege build {manifest.get('besiege_build')} matches the local game assemblies."
    )
