from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def resolve_project_path(*, path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return PROJECT_ROOT / candidate


def _load_project_env(*, env_path: Path) -> None:
    if not env_path.is_file():
        return

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


_load_project_env(env_path=PROJECT_ROOT / ".env")


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
        return _env_path(env_var="BLOCK_AUTHORING_PATH", must_exist=None)
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
        readme_step="Step 3 & Step 7 - install Besiege, then point BESIEGE_DATA_PATH at ...\\Besiege\\Besiege_Data",
    ),
    EnvRequirement(
        env_var="COLLIDER_DUMP_PATH",
        kind="file",
        purpose="Collider + geometry dump produced by the BuildArena Block Inspector mod.",
        readme_step="Step 4-6 - install the Inspector mod, run one simulation, then copy its collider dump .toml and point COLLIDER_DUMP_PATH at it",
    ),
    EnvRequirement(
        env_var="SAVED_MACHINE_DIR",
        kind="dir",
        purpose="Folder where generated .bsg machines are written so the game can load them.",
        readme_step="Step 7 - set SAVED_MACHINE_DIR to Besiege's SavedMachines\\BuildArena folder",
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


def check_environment() -> list[EnvCheckResult]:
    """Check every configured path requirement and return per-item results."""
    return [_check_requirement(requirement=requirement) for requirement in ENV_REQUIREMENTS]


def format_environment_report(*, results: list[EnvCheckResult]) -> str:
    lines: list[str] = []
    lines.append("BuildArena setup self-check")
    lines.append("=" * 60)

    env_file = PROJECT_ROOT / ".env"
    if env_file.is_file():
        lines.append(f"[ok]      .env found at {env_file}")
    else:
        example = PROJECT_ROOT / ".env.example"
        lines.append(f"[MISSING] .env not found. Copy {example} -> {env_file} (README Step 2).")
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
        lines.append("All good - you're ready to build! Open block_preview.ipynb (README Step 8).")
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
