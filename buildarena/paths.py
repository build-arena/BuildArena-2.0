from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def resolve_project_path(*, path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return PROJECT_ROOT / candidate


def parse_env_file(*, env_path: Path) -> list[tuple[str, str]]:
    """Return ``(key, value)`` pairs from a dotenv file, in file order."""
    assignments: list[tuple[str, str]] = []
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line == "" or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Invalid env line in {env_path}: {raw_line}")

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key == "":
            raise ValueError(f"Invalid empty env key in {env_path}: {raw_line}")
        assignments.append((key, value))
    return assignments


def load_project_env(*, env_path: Path | None = None, overwrite: bool = False) -> None:
    """Load ``.env`` into ``os.environ``.

    ``overwrite=False`` keeps values already present in the process (normal
    import). Setup writes a new ``.env`` with detected paths, then calls this
    with ``overwrite=True`` so the current process sees those values.
    """
    path = PROJECT_ROOT / ".env" if env_path is None else env_path
    if not path.is_file():
        return
    for key, value in parse_env_file(env_path=path):
        if overwrite:
            os.environ[key] = value
        else:
            os.environ.setdefault(key, value)


def _env_path(
    *,
    env_var: str,
    must_exist: str | None,
) -> Path:
    raw_path = os.environ.get(env_var)
    if raw_path is None or raw_path.strip() == "":
        raise RuntimeError(f"{env_var} is not set. Add it to .env or the process environment.")

    resolved_path = resolve_project_path(path=raw_path.strip())
    if must_exist == "file" and not resolved_path.is_file():
        raise FileNotFoundError(f"{env_var} file not found: {resolved_path}")
    if must_exist == "dir" and not resolved_path.is_dir():
        raise FileNotFoundError(f"{env_var} directory not found: {resolved_path}")
    if must_exist not in {"file", "dir", None}:
        raise ValueError(f"Invalid must_exist value: {must_exist}")
    return resolved_path


load_project_env(env_path=PROJECT_ROOT / ".env", overwrite=False)


def get_block_registry_path(*, registry_path: str | Path | None = None) -> Path:
    if registry_path is None:
        return _env_path(env_var="BLOCK_REGISTRY_PATH", must_exist="file")
    resolved_path = resolve_project_path(path=registry_path)
    if not resolved_path.is_file():
        raise FileNotFoundError(f"Block registry file not found: {resolved_path}")
    return resolved_path


def get_block_roles_path(*, roles_path: str | Path | None = None) -> Path:
    if roles_path is None:
        return _env_path(env_var="BLOCK_ROLES_PATH", must_exist="file")
    resolved_path = resolve_project_path(path=roles_path)
    if not resolved_path.is_file():
        raise FileNotFoundError(f"Block roles file not found: {resolved_path}")
    return resolved_path


def get_block_authoring_path(*, authoring_path: str | Path | None = None) -> Path:
    if authoring_path is None:
        return _env_path(env_var="BLOCK_AUTHORING_PATH", must_exist="file")
    return resolve_project_path(path=authoring_path)


def get_besiege_data_path(*, data_path: str | Path | None = None) -> Path:
    if data_path is None:
        resolved_path = _env_path(env_var="BESIEGE_DATA_PATH", must_exist="dir")
    else:
        resolved_path = resolve_project_path(path=data_path)

    if not resolved_path.is_dir():
        raise FileNotFoundError(f"BESIEGE_DATA_PATH directory not found: {resolved_path}")

    skins_path = resolved_path / "Skins"
    if not skins_path.is_dir():
        install_root_hint = resolved_path / "Besiege_Data"
        hint = ""
        if install_root_hint.is_dir():
            hint = f" It looks like an install root; use BESIEGE_DATA_PATH={install_root_hint}."
        raise FileNotFoundError(
            f"BESIEGE_DATA_PATH must point to Besiege_Data containing a Skins directory: {resolved_path}.{hint}"
        )

    return resolved_path


def get_skin_dir(
    *,
    mesh_key: str,
    skin_set: str,
    data_path: str | Path | None = None,
) -> Path:
    besiege_data_path = get_besiege_data_path(data_path=data_path)
    return besiege_data_path / "Skins" / skin_set / mesh_key


def get_saved_machine_dir(*, saved_machine_dir: str | Path | None = None) -> Path:
    if saved_machine_dir is None:
        return _env_path(env_var="SAVED_MACHINE_DIR", must_exist=None)
    return resolve_project_path(path=saved_machine_dir)


def get_block_channel_catalog_path(*, catalog_path: str | Path | None = None) -> Path:
    """Resolve the Inspector channel catalog.

    Preference order:
    1. explicit ``catalog_path``
    2. ``BLOCK_CHANNEL_CATALOG`` when set
    3. ToolKit data dir under ``BESIEGE_DATA_PATH``
    """
    if catalog_path is not None:
        resolved_path = resolve_project_path(path=catalog_path)
        if not resolved_path.is_file():
            raise FileNotFoundError(f"Block channel catalog not found: {resolved_path}")
        return resolved_path

    raw_override = os.environ.get("BLOCK_CHANNEL_CATALOG")
    if raw_override is not None and raw_override.strip() != "":
        return _env_path(env_var="BLOCK_CHANNEL_CATALOG", must_exist="file")

    control_root = PROJECT_ROOT / "control"
    if str(control_root) not in sys.path:
        sys.path.insert(0, str(control_root))
    from controller_sdk.protocol import CHANNEL_CATALOG_FILE, TOOLKIT_DATA_DIR_NAME

    resolved_path = get_besiege_data_path() / "Mods" / "Data" / TOOLKIT_DATA_DIR_NAME / CHANNEL_CATALOG_FILE
    if not resolved_path.is_file():
        raise FileNotFoundError(
            f"Block channel catalog not found: {resolved_path}. Run scripts/setup.py."
        )
    return resolved_path


def get_collider_dump_path(*, dump_path: str | Path | None = None) -> Path:
    if dump_path is None:
        return _env_path(env_var="COLLIDER_DUMP_PATH", must_exist="file")
    resolved_path = resolve_project_path(path=dump_path)
    if not resolved_path.is_file():
        raise FileNotFoundError(f"Collider dump file not found: {resolved_path}")
    return resolved_path


# ── Setup self-check guard ────────────────────────────────────────────
# One friendly command that answers "what am I still missing, and which
# README step fixes it?".  This is a diagnostic: it deliberately collects
# and reports every problem at once instead of raising on the first one,
# so a new operator can see the whole checklist in a single run.


@dataclass(frozen=True)
class EnvRequirement:
    env_var: str
    kind: str  # "file" | "dir" | "besiege_data"
    purpose: str
    readme_step: str


@dataclass(frozen=True)
class EnvCheckResult:
    requirement: EnvRequirement
    ok: bool
    detail: str
    resolved_path: Path | None


ENV_REQUIREMENTS: tuple[EnvRequirement, ...] = (
    EnvRequirement(
        env_var="BESIEGE_DATA_PATH",
        kind="besiege_data",
        purpose="Besiege game data folder (holds the block Skins / .obj meshes we load).",
        readme_step="Install Besiege, then re-run scripts/setup.ps1 or set BESIEGE_DATA_PATH to ...\\Besiege\\Besiege_Data",
    ),
    EnvRequirement(
        env_var="COLLIDER_DUMP_PATH",
        kind="file",
        purpose="Collider + geometry dump produced by the BuildArena ToolKit Inspector.",
        readme_step="Run scripts/setup.ps1 so Inspector artifacts are copied to .local/collider_dump.toml",
    ),
    EnvRequirement(
        env_var="SAVED_MACHINE_DIR",
        kind="dir",
        purpose="Folder where generated .bsg machines are written so the game can load them.",
        readme_step="Run scripts/setup.ps1; it creates SavedMachines\\BuildArena and writes SAVED_MACHINE_DIR",
    ),
    EnvRequirement(
        env_var="BLOCK_REGISTRY_PATH",
        kind="file",
        purpose="Block runtime registry (ships with the repo under blocks/).",
        readme_step="Step 2 - run `uv sync` and keep the repo's blocks/ folder intact",
    ),
    EnvRequirement(
        env_var="BLOCK_ROLES_PATH",
        kind="file",
        purpose="Block role table (ships with the repo under blocks/).",
        readme_step="Step 2 - keep the repo's blocks/ folder intact",
    ),
    EnvRequirement(
        env_var="BLOCK_AUTHORING_PATH",
        kind="file",
        purpose="Unique public block names and authored descriptions (ships with the repo under blocks/).",
        readme_step="Step 2 - keep the repo's blocks/ folder intact",
    ),
)


def _check_requirement(*, requirement: EnvRequirement) -> EnvCheckResult:
    raw_value = os.environ.get(requirement.env_var)
    if raw_value is None or raw_value.strip() == "":
        return EnvCheckResult(
            requirement=requirement,
            ok=False,
            detail=f"{requirement.env_var} is not set (add it to .env).",
            resolved_path=None,
        )

    resolved_path = resolve_project_path(path=raw_value.strip())

    if requirement.kind == "file":
        ok = resolved_path.is_file()
        detail = "found" if ok else f"file not found: {resolved_path}"
        return EnvCheckResult(requirement=requirement, ok=ok, detail=detail, resolved_path=resolved_path)

    if requirement.kind == "dir":
        ok = resolved_path.is_dir()
        detail = "found" if ok else f"directory not found: {resolved_path}"
        return EnvCheckResult(requirement=requirement, ok=ok, detail=detail, resolved_path=resolved_path)

    if requirement.kind == "besiege_data":
        if not resolved_path.is_dir():
            return EnvCheckResult(
                requirement=requirement,
                ok=False,
                detail=f"directory not found: {resolved_path}",
                resolved_path=resolved_path,
            )
        if not (resolved_path / "Skins").is_dir():
            install_root_hint = resolved_path / "Besiege_Data"
            hint = ""
            if install_root_hint.is_dir():
                hint = f" It looks like an install root; use BESIEGE_DATA_PATH={install_root_hint}."
            return EnvCheckResult(
                requirement=requirement,
                ok=False,
                detail=f"must be Besiege_Data containing a Skins directory: {resolved_path}.{hint}",
                resolved_path=resolved_path,
            )
        return EnvCheckResult(
            requirement=requirement,
            ok=True,
            detail="found",
            resolved_path=resolved_path,
        )

    raise ValueError(f"Invalid requirement kind: {requirement.kind}")


def _extra_ready_checks() -> list[EnvCheckResult]:
    extras: list[EnvCheckResult] = []
    catalog_requirement = EnvRequirement(
        env_var="CHANNEL_CATALOG",
        kind="file",
        purpose="Block channel catalog written to the ToolKit data directory by setup.",
        readme_step="Run scripts/setup.py; it writes block_channel_catalog.json next to Inspector dumps",
    )
    try:
        catalog = get_block_channel_catalog_path()
        extras.append(
            EnvCheckResult(
                requirement=catalog_requirement,
                ok=True,
                detail="found",
                resolved_path=catalog,
            )
        )
    except (RuntimeError, FileNotFoundError) as exc:
        extras.append(
            EnvCheckResult(
                requirement=catalog_requirement,
                ok=False,
                detail=str(exc),
                resolved_path=None,
            )
        )
    report_path = PROJECT_ROOT / ".local" / "setup-report.json"
    report_ok = False
    report_detail = f"file not found: {report_path}"
    if report_path.is_file():
        import json

        payload = json.loads(report_path.read_text(encoding="utf-8"))
        report_ok = payload.get("status") == "passed"
        report_detail = f"status={payload.get('status')!r}"
    extras.append(
        EnvCheckResult(
            requirement=EnvRequirement(
                env_var="SETUP_REPORT",
                kind="file",
                purpose="Latest one-command setup report, including the telemetry smoke.",
                readme_step="Run scripts/setup.ps1 until .local/setup-report.json status is passed",
            ),
            ok=report_ok,
            detail=report_detail,
            resolved_path=report_path if report_path.is_file() else None,
        )
    )
    raw_besiege = os.environ.get("BESIEGE_DATA_PATH", "").strip()
    toolkit_ok = False
    toolkit_detail = "BESIEGE_DATA_PATH is not set"
    toolkit_path = None
    if raw_besiege:
        control_root = PROJECT_ROOT / "control"
        if str(control_root) not in sys.path:
            sys.path.insert(0, str(control_root))
        from besiege_cli.compat import CompatibilityError, installed_toolkit_dir

        besiege = resolve_project_path(path=raw_besiege)
        try:
            toolkit_path = installed_toolkit_dir(besiege)
            toolkit_ok = True
            toolkit_detail = f"workshop item at {toolkit_path}"
        except CompatibilityError as exc:
            toolkit_detail = str(exc)
    extras.append(
        EnvCheckResult(
            requirement=EnvRequirement(
                env_var="TOOLKIT_INSTALL",
                kind="dir",
                purpose="Steam Workshop subscription at workshop/content/346010/3795335349.",
                readme_step="Subscribe to https://steamcommunity.com/sharedfiles/filedetails/?id=3795335349 then re-run setup",
            ),
            ok=toolkit_ok,
            detail=toolkit_detail,
            resolved_path=toolkit_path,
        )
    )
    return extras


def check_environment() -> list[EnvCheckResult]:
    """Check every configured path requirement and return per-item results."""
    return [_check_requirement(requirement=requirement) for requirement in ENV_REQUIREMENTS] + _extra_ready_checks()


def format_environment_report(*, results: list[EnvCheckResult]) -> str:
    lines: list[str] = []
    lines.append("BuildArena setup self-check")
    lines.append("=" * 60)

    env_file = PROJECT_ROOT / ".env"
    if env_file.is_file():
        lines.append(f"[ok]      .env found at {env_file}")
    else:
        lines.append("[MISSING] .env not found. Run scripts/setup.py to write it from detected paths.")
    lines.append("")

    missing: list[EnvCheckResult] = []
    for result in results:
        marker = "[ok]     " if result.ok else "[MISSING]"
        lines.append(f"{marker} {result.requirement.env_var}: {result.detail}")
        if not result.ok:
            lines.append(f"          purpose: {result.requirement.purpose}")
            lines.append(f"          fix:     {result.requirement.readme_step}")
            missing.append(result)

    lines.append("")
    lines.append("-" * 60)
    if len(missing) == 0:
        lines.append("All good - one-command setup passed and control is ready.")
    else:
        lines.append(
            f"{len(missing)} item(s) still need attention. "
            "Open README.md and follow the referenced step(s) above."
        )
    return "\n".join(lines)


def require_environment() -> None:
    """Raise a single aggregated error when any path requirement is unmet.

    Callers that want a hard guard before doing real work can call this;
    the individual getters still raise on their own when used directly.
    """
    results = check_environment()
    missing = [result for result in results if not result.ok]
    if len(missing) == 0:
        return
    report = format_environment_report(results=results)
    raise RuntimeError(
        "BuildArena environment is not fully configured.\n" + report
    )


def main() -> int:
    results = check_environment()
    print(format_environment_report(results=results))
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
