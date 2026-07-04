"""One-command BuildArena setup orchestrator / 一键配置编排器.

This automates every step of the README that a machine *can* do on its own,
and stops with a clear bilingual instruction at the moments that genuinely
need a human (buying the game + DLC, subscribing to the Workshop mods,
toggling them on, running one in-game simulation, and the final visual
check).  It is idempotent: run it again after each human step and it picks
up exactly where it left off.

它会自动完成 README 中机器能独立做的每一步，并在真正需要人介入的时刻
（购买游戏+DLC、订阅创意工坊模组、开启模组、进游戏跑一次模拟、以及最后的
目视检查）停下并给出清晰的双语指引。脚本是幂等的：每完成一个人工步骤后
重跑，它会从上次停下的地方继续。

This is a setup/config script, not part of the runtime package, so it lives
under ``scripts/`` and is run directly:
它是配置脚本，不属于运行时包，所以放在 ``scripts/`` 下并直接运行：

    uv run python scripts/setup.py

Manual fallback: every automated step here mirrors a README step, so if
auto-detection fails you can always fall back to the manual instructions in
README.md / docs/README.zh-CN.md.
手动兜底：这里每个自动步骤都对应 README 的一步，自动探测失败时随时可以回到
README.md / docs/README.zh-CN.md 的手动说明。
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import sys
import tomllib
from pathlib import Path

# Make the ``buildarena`` package importable when this file is run directly as
# a standalone script (``python scripts/setup.py``), where sys.path[0] is the
# scripts/ folder rather than the repository root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from buildarena.paths import (
    PROJECT_ROOT,
    check_environment,
    format_environment_report,
)

# ── Constants ─────────────────────────────────────────────────────────
BESIEGE_APP_ID = "346010"
INSPECTOR_DIR_PREFIX = "BuildArenaBlockInspector_"
COLLIDER_DUMP_RELATIVE = ".local/collider_dump.toml"
SAVED_MACHINE_SUBDIR = ("SavedMachines", "BuildArena")

_DIVIDER = "─" * 64


# ── Bilingual printing ────────────────────────────────────────────────
def _configure_utf8_output() -> None:
    """Force stdout/stderr to UTF-8 so CJK + emoji never crash on Windows
    consoles (whose default GBK/cp936 codec cannot encode them)."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def _say(*, zh: str, en: str) -> None:
    print(zh)
    print(en)


def _section(*, zh: str, en: str) -> None:
    print("")
    print(_DIVIDER)
    print(f"  {zh}")
    print(f"  {en}")
    print(_DIVIDER)


# ── Platform guard ────────────────────────────────────────────────────
def ensure_windows() -> None:
    if platform.system() != "Windows":
        raise RuntimeError(
            "BuildArena setup only supports Windows (Besiege paths assume Windows). "
            f"Detected platform: {platform.system()}. "
            "本配置脚本仅支持 Windows（Besiege 路径按 Windows 设计）。"
        )


# ── .env handling ─────────────────────────────────────────────────────
def ensure_env_file() -> Path:
    env_path = PROJECT_ROOT / ".env"
    if env_path.is_file():
        _say(
            zh=f"[ok] 已存在 .env：{env_path}",
            en=f"[ok] .env already present: {env_path}",
        )
        return env_path

    example_path = PROJECT_ROOT / ".env.example"
    if not example_path.is_file():
        raise FileNotFoundError(
            f".env.example not found at {example_path}; cannot create .env."
        )
    shutil.copyfile(src=example_path, dst=env_path)
    _say(
        zh=f"[创建] 已从 .env.example 复制出 .env：{env_path}",
        en=f"[created] Copied .env from .env.example: {env_path}",
    )
    return env_path


def load_env_into_environ(*, env_path: Path) -> None:
    """Load .env into ``os.environ`` with override so in-process checks see it."""
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
        if key == "":
            raise ValueError(f"Invalid empty env key in {env_path}: {raw_line}")
        os.environ[key] = value.strip().strip('"').strip("'")


def set_env_var(*, env_path: Path, key: str, value: str) -> None:
    """Set ``key=value`` in .env, preserving comments/other lines, and mirror
    the value into ``os.environ`` so the self-check reflects it immediately."""
    lines = env_path.read_text(encoding="utf-8").splitlines()
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    replaced = False
    for index, raw_line in enumerate(lines):
        if pattern.match(raw_line):
            lines[index] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ[key] = value


# ── Steam / Besiege detection ─────────────────────────────────────────
def _steam_roots_from_registry() -> list[Path]:
    import winreg

    roots: list[Path] = []
    probes = (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    )
    for hive, subkey, value_name in probes:
        # A missing key just means "Steam not registered here"; that is an
        # expected probe outcome, not an error we want to hide.
        try:
            handle = winreg.OpenKey(hive, subkey)
        except OSError:
            continue
        with handle:
            value, _kind = winreg.QueryValueEx(handle, value_name)
        candidate = Path(str(value))
        if candidate.is_dir():
            roots.append(candidate)
    return roots


def _library_paths_from_steam_root(*, steam_root: Path) -> list[Path]:
    libraries: list[Path] = [steam_root]
    vdf_path = steam_root / "steamapps" / "libraryfolders.vdf"
    if vdf_path.is_file():
        text = vdf_path.read_text(encoding="utf-8")
        for match in re.finditer(r'"path"\s*"([^"]+)"', text):
            libraries.append(Path(match.group(1).replace("\\\\", "\\")))
    return libraries


def _candidate_steam_roots() -> list[Path]:
    roots: list[Path] = list(_steam_roots_from_registry())
    for default_root in (
        Path(r"C:\Program Files (x86)\Steam"),
        Path(r"C:\Program Files\Steam"),
    ):
        if default_root.is_dir():
            roots.append(default_root)

    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        marker = str(root).lower()
        if marker not in seen:
            seen.add(marker)
            unique.append(root)
    return unique


def _is_besiege_data(*, path: Path) -> bool:
    return path.is_dir() and (path / "Skins").is_dir()


def detect_besiege_data() -> Path | None:
    for steam_root in _candidate_steam_roots():
        for library in _library_paths_from_steam_root(steam_root=steam_root):
            besiege_data = (
                library / "steamapps" / "common" / "Besiege" / "Besiege_Data"
            )
            if _is_besiege_data(path=besiege_data):
                return besiege_data
    return None


def _normalize_besiege_data_input(*, raw: str) -> Path:
    """Accept either the install root or Besiege_Data and resolve to Besiege_Data."""
    candidate = Path(raw.strip().strip('"'))
    if _is_besiege_data(path=candidate):
        return candidate
    nested = candidate / "Besiege_Data"
    if _is_besiege_data(path=nested):
        return nested
    raise FileNotFoundError(
        f"Not a valid Besiege_Data folder (no Skins directory inside): {candidate}"
    )


def prompt_for_besiege_data() -> Path | None:
    """Interactively ask the user to paste the Besiege_Data path.

    Returns the resolved path, or ``None`` if the user indicates the game is
    not installed yet (types ``skip``).
    """
    _say(
        zh=(
            "自动探测没有找到 Besiege 安装目录（可能装在非默认盘符）。\n"
            "请粘贴 Besiege_Data 目录路径（Steam 里右键 Besiege → 管理 → 浏览本地文件，\n"
            "打开的就是安装目录，Besiege_Data 就在里面）。\n"
            "如果还没安装游戏，输入 skip 跳过。"
        ),
        en=(
            "Auto-detection could not find Besiege (it may be on a non-default drive).\n"
            "Paste the Besiege_Data folder path (in Steam: right-click Besiege → Manage →\n"
            "Browse local files; Besiege_Data sits inside that folder).\n"
            "If the game is not installed yet, type skip."
        ),
    )
    while True:
        raw = input("Besiege_Data > ").strip()
        if raw.lower() == "skip" or raw == "":
            return None
        # Re-prompting on a bad path is expected interactive UX, not a hidden
        # failure: surface the reason and let the user correct it.
        try:
            return _normalize_besiege_data_input(raw=raw)
        except FileNotFoundError as error:
            _say(
                zh=f"[无效] {error}\n请再试一次，或输入 skip。",
                en=f"[invalid] {error}\nPlease try again, or type skip.",
            )


# ── Saved machine directory ───────────────────────────────────────────
def ensure_saved_machine_dir(*, besiege_data: Path) -> Path:
    saved_dir = besiege_data.joinpath(*SAVED_MACHINE_SUBDIR)
    saved_dir.mkdir(parents=True, exist_ok=True)
    return saved_dir


# ── Collider dump discovery ───────────────────────────────────────────
def _toml_is_collider_dump(*, toml_path: Path) -> bool:
    with open(toml_path, "rb") as handle:
        data = tomllib.load(handle)
    blocks = data.get("blocks")
    return isinstance(blocks, dict) and len(blocks) > 0


def find_collider_dump(*, besiege_data: Path) -> Path | None:
    """Find the Inspector's collider/geometry dump inside the game's mod data.

    The dump is identified structurally (a top-level ``[blocks]`` table), not
    by filename, so it works regardless of what the mod names its files.
    """
    mods_data = besiege_data / "Mods" / "Data"
    if not mods_data.is_dir():
        return None

    matches: list[Path] = []
    for inspector_dir in sorted(mods_data.iterdir()):
        if not inspector_dir.is_dir():
            continue
        if not inspector_dir.name.startswith(INSPECTOR_DIR_PREFIX):
            continue
        for toml_path in sorted(inspector_dir.glob("*.toml")):
            if _toml_is_collider_dump(toml_path=toml_path):
                matches.append(toml_path)

    if len(matches) == 0:
        return None
    matches.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return matches[0]


def copy_collider_dump(*, source: Path) -> Path:
    dest = PROJECT_ROOT / Path(COLLIDER_DUMP_RELATIVE)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src=source, dst=dest)
    return dest


# ── MCP config generation ─────────────────────────────────────────────
def write_mcp_json() -> Path:
    mcp_path = PROJECT_ROOT / "mcp.json"
    config = {
        "mcpServers": {
            "build-arena": {
                "command": "uv",
                "args": [
                    "run",
                    "--directory",
                    str(PROJECT_ROOT),
                    "python",
                    "-m",
                    "buildarena.mcp_server",
                ],
            }
        }
    }
    mcp_path.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return mcp_path


# ── Human-in-the-loop checkpoints ─────────────────────────────────────
def _print_game_checkpoint() -> None:
    _section(
        zh="需要你来一下：安装游戏 + 两个 DLC（README 第 3 步）",
        en="Your turn: install the game + both DLC (README Step 3)",
    )
    _say(
        zh=(
            "还没探测到 Besiege_Data。请先在 Steam 完成：\n"
            "  1. 安装 Steam 并登录。\n"
            "  2. 购买并安装 Besiege 本体。\n"
            "  3. 购买并安装两个 DLC：The Splintered Sea（水）与 The Broken Beyond（太空）。\n"
            "装好后重跑本脚本即可继续；若装在非默认位置，重跑时按提示粘贴 Besiege_Data 路径。"
        ),
        en=(
            "Besiege_Data was not found yet. In Steam, please:\n"
            "  1. Install Steam and sign in.\n"
            "  2. Buy + install Besiege (base game).\n"
            "  3. Buy + install both DLC: The Splintered Sea (water) and The Broken Beyond (space).\n"
            "Then re-run this script to continue; if it is installed in a custom location,\n"
            "paste the Besiege_Data path when prompted on the next run."
        ),
    )


def _print_mods_checkpoint(*, besiege_data: Path) -> None:
    _section(
        zh="需要你来一下：装模组 + 开模组 + 跑一次模拟（README 第 4-6 步）",
        en="Your turn: mods + toggle on + one simulation (README Steps 4-6)",
    )
    _say(
        zh=(
            f"已找到游戏目录：{besiege_data}\n"
            "但还没找到 Inspector 导出的碰撞数据。请在游戏里完成：\n"
            "  1. 在创意工坊订阅两个模组：BuildArena Block Inspector 与 BuildArena Block Tracker（两个都要）。\n"
            "  2. 启动 Besiege，打开 mod loader，把这两个模组都切到 ON。\n"
            "  3. 进任意关卡/沙盒，随便搭几块，按 ▶ 运行一次模拟（这会触发导出）。\n"
            "然后重跑本脚本，它会自动找到并拷贝碰撞数据。"
        ),
        en=(
            f"Found the game folder: {besiege_data}\n"
            "But the Inspector's collider dump was not found yet. In-game, please:\n"
            "  1. Subscribe to both Workshop mods: BuildArena Block Inspector and BuildArena Block Tracker (both).\n"
            "  2. Launch Besiege, open the mod loader, toggle BOTH mods ON.\n"
            "  3. Enter any level/sandbox, build a couple of blocks, press ▶ to run one simulation (this triggers the dump).\n"
            "Then re-run this script; it will locate and copy the dump automatically."
        ),
    )


def _print_visual_check_note() -> None:
    _section(
        zh="最后一步（人工目检）：预览所有方块（README 第 8 步）",
        en="Final human check: preview every block (README Step 8)",
    )
    _say(
        zh=(
            "配置全部就绪。🎉 现在做一次端到端目检：\n"
            "  uv run jupyter lab block_preview.ipynb\n"
            "从上到下运行，把生成的机器在 Besiege 里加载，确认每个方块都正确显示。\n"
            "只有你（人类）能‘看到’游戏内画面，如有异常请把现象告诉 Agent 一起排查。\n"
            "MCP 已生成 mcp.json，可直接接入你的 Agent（README 第 9 步）。"
        ),
        en=(
            "Everything is configured. 🎉 Now do one end-to-end visual check:\n"
            "  uv run jupyter lab block_preview.ipynb\n"
            "Run it top to bottom, load the generated machine in Besiege, and confirm every block renders.\n"
            "Only you (the human) can 'see' the in-game result; if anything looks off, tell your Agent.\n"
            "MCP config was written to mcp.json for your Agent (README Step 9)."
        ),
    )


# ── Orchestration ─────────────────────────────────────────────────────
def run(*, besiege_data_override: str | None = None, interactive: bool = True) -> int:
    _configure_utf8_output()
    ensure_windows()

    _section(
        zh="BuildArena 一键配置",
        en="BuildArena one-command setup",
    )

    env_path = ensure_env_file()
    load_env_into_environ(env_path=env_path)

    # Step 7a — Besiege_Data
    if besiege_data_override is not None:
        besiege_data = _normalize_besiege_data_input(raw=besiege_data_override)
        _say(
            zh=f"[使用参数] Besiege_Data：{besiege_data}",
            en=f"[from flag] Besiege_Data: {besiege_data}",
        )
    else:
        besiege_data = detect_besiege_data()
        if besiege_data is not None:
            _say(
                zh=f"[探测] 找到 Besiege_Data：{besiege_data}",
                en=f"[detected] Besiege_Data: {besiege_data}",
            )
        elif interactive and sys.stdin.isatty():
            besiege_data = prompt_for_besiege_data()

    if besiege_data is None:
        write_mcp_json()
        _print_game_checkpoint()
        print("")
        print(format_environment_report(results=check_environment()))
        return 1

    set_env_var(env_path=env_path, key="BESIEGE_DATA_PATH", value=str(besiege_data))

    # Step 7b — SavedMachines
    saved_dir = ensure_saved_machine_dir(besiege_data=besiege_data)
    set_env_var(env_path=env_path, key="SAVED_MACHINE_DIR", value=str(saved_dir))
    _say(
        zh=f"[就绪] 机器保存目录：{saved_dir}",
        en=f"[ready] Saved-machine directory: {saved_dir}",
    )

    # Step 6 — collider dump
    dump_source = find_collider_dump(besiege_data=besiege_data)
    if dump_source is None:
        write_mcp_json()
        _print_mods_checkpoint(besiege_data=besiege_data)
        print("")
        print(format_environment_report(results=check_environment()))
        return 1

    dump_dest = copy_collider_dump(source=dump_source)
    set_env_var(
        env_path=env_path, key="COLLIDER_DUMP_PATH", value=COLLIDER_DUMP_RELATIVE
    )
    _say(
        zh=f"[导入] 碰撞数据：{dump_source}\n        → {dump_dest}",
        en=f"[imported] Collider dump: {dump_source}\n           → {dump_dest}",
    )

    # Step 9 — MCP config
    mcp_path = write_mcp_json()
    _say(
        zh=f"[生成] MCP 配置：{mcp_path}",
        en=f"[wrote] MCP config: {mcp_path}",
    )

    # Final self-check (the "compass")
    print("")
    results = check_environment()
    print(format_environment_report(results=results))

    if all(result.ok for result in results):
        _print_visual_check_note()
        return 0
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="BuildArena one-command setup / BuildArena 一键配置",
    )
    parser.add_argument(
        "--besiege-data",
        dest="besiege_data",
        default=None,
        help="Explicit path to Besiege_Data (or the install root) when auto-detection fails.",
    )
    parser.add_argument(
        "--non-interactive",
        dest="non_interactive",
        action="store_true",
        help="Never prompt; print the manual instruction and exit when input is needed.",
    )
    args = parser.parse_args()
    return run(
        besiege_data_override=args.besiege_data,
        interactive=not args.non_interactive,
    )


if __name__ == "__main__":
    raise SystemExit(main())
