"""Windowed launch size for the macOS Unity 5.4 player.

Besiege's Mac build does not declare NSHighResolutionCapable, and Unity 5.4
mis-reads a rotated external display. A saved fullscreen size such as
3840x1057 is then stretched across the main display. Launch windowed at the
main display's point size so the framebuffer aspect matches the screen.
"""

from __future__ import annotations

import plistlib
import re
from pathlib import Path

UNITY_PREFS_NAME = "unity.Spiderling Games.Besiege.plist"
# Unity 5.4 player log. Same ~/Library layout as unity_prefs_path.
UNITY_PLAYER_LOG = Path("Library") / "Logs" / "Unity" / "Player.log"
_WIDTH_KEY = "Screenmanager Resolution Width"
_HEIGHT_KEY = "Screenmanager Resolution Height"
_FULLSCREEN_KEY = "Screenmanager Is Fullscreen mode"
# Unity's windowed client area sits under a title bar. On this Mac that bar
# was 28 points (visible height 1023, client height 995).
_TITLE_BAR_POINTS = 28


def fitted_window(
    *,
    frame_width: float,
    frame_height: float,
    visible_width: float,
    visible_height: float,
    title_bar: int = _TITLE_BAR_POINTS,
) -> tuple[int, int]:
    """Largest client size that keeps the display's aspect and fits on screen.

    The menu bar and Dock shrink the visible frame. Filling that frame's width
    while clipping its height changes the aspect and stretches the picture.
    """
    if frame_width < 100 or frame_height < 100:
        raise RuntimeError(
            f"Main display size {frame_width}x{frame_height} is too small to use as the Besiege window."
        )
    if visible_width < 100 or visible_height < 100:
        raise RuntimeError(
            f"Visible display size {visible_width}x{visible_height} is too small to use as the Besiege window."
        )
    aspect = frame_width / frame_height
    max_width = int(visible_width)
    max_height = int(visible_height) - title_bar
    if max_height < 100:
        raise RuntimeError(
            f"Visible height {visible_height} leaves no room for a Besiege window under a {title_bar}pt title bar."
        )
    width = max_width
    height = int(round(width / aspect))
    if height > max_height:
        height = max_height
        width = int(round(height * aspect))
    if width < 100 or height < 100:
        raise RuntimeError(f"Fitted Besiege window {width}x{height} is too small.")
    return width, height


def _screen_rects() -> tuple[tuple[float, float], tuple[float, float]]:
    """Return ``(frame_width, frame_height), (visible_width, visible_height)``."""
    import ctypes
    from ctypes import c_double, c_void_p

    ctypes.CDLL("/System/Library/Frameworks/AppKit.framework/AppKit")

    class NSRect(ctypes.Structure):
        _fields_ = (("x", c_double), ("y", c_double), ("width", c_double), ("height", c_double))

    objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
    objc.objc_getClass.restype = c_void_p
    objc.objc_getClass.argtypes = (c_void_p,)
    objc.sel_registerName.restype = c_void_p
    objc.sel_registerName.argtypes = (c_void_p,)
    message_pointer = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
    message_pointer.objc_msgSend.restype = c_void_p
    message_pointer.objc_msgSend.argtypes = (c_void_p, c_void_p)
    message_rect = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
    message_rect.objc_msgSend.restype = NSRect
    message_rect.objc_msgSend.argtypes = (c_void_p, c_void_p)

    screen_class = objc.objc_getClass(b"NSScreen")
    screen = message_pointer.objc_msgSend(screen_class, objc.sel_registerName(b"mainScreen"))
    if not screen:
        raise RuntimeError("NSScreen.mainScreen returned no display.")
    frame = message_rect.objc_msgSend(screen, objc.sel_registerName(b"frame"))
    visible = message_rect.objc_msgSend(screen, objc.sel_registerName(b"visibleFrame"))
    return (frame.width, frame.height), (visible.width, visible.height)


def launch_window_points() -> tuple[int, int]:
    """Client size for the next Besiege window on the main display."""
    frame, visible = _screen_rects()
    return fitted_window(
        frame_width=frame[0],
        frame_height=frame[1],
        visible_width=visible[0],
        visible_height=visible[1],
    )


def _set_xml_tag(text: str, tag: str, value: str) -> str:
    updated, count = re.subn(
        rf"(<{tag}>)([^<]*)(</{tag}>)",
        rf"\g<1>{value}\g<3>",
        text,
        count=1,
    )
    if count != 1:
        raise RuntimeError(f"Besiege Config.xml has no single <{tag}> element to update.")
    return updated


def write_besiege_window(config_path: Path, *, width: int, height: int) -> None:
    """Set Besiege's own video settings to a window of ``width`` x ``height``."""
    if not config_path.is_file():
        raise FileNotFoundError(f"Besiege Config.xml not found: {config_path}")
    text = config_path.read_text(encoding="utf-8")
    text = _set_xml_tag(text, "WindowedMode", "true")
    text = _set_xml_tag(text, "ScreenWidth", str(width))
    text = _set_xml_tag(text, "ScreenHeight", str(height))
    config_path.write_text(text, encoding="utf-8")


def write_unity_window(prefs_path: Path, *, width: int, height: int) -> None:
    """Set Unity 5.4's Screenmanager prefs to the same window."""
    if prefs_path.is_file():
        with prefs_path.open("rb") as handle:
            payload = plistlib.load(handle)
        if not isinstance(payload, dict):
            raise RuntimeError(f"Unity prefs {prefs_path} are not a dictionary.")
    else:
        payload = {}
        prefs_path.parent.mkdir(parents=True, exist_ok=True)
    payload[_WIDTH_KEY] = int(width)
    payload[_HEIGHT_KEY] = int(height)
    payload[_FULLSCREEN_KEY] = 0
    with prefs_path.open("wb") as handle:
        plistlib.dump(payload, handle, fmt=plistlib.FMT_BINARY)


def unity_prefs_path(*, home: Path | None = None) -> Path:
    root = Path.home() if home is None else home
    return root / "Library" / "Preferences" / UNITY_PREFS_NAME


def unity_player_log_path(*, home: Path | None = None) -> Path:
    """Unity 5.4 standalone player log on macOS.

    Confirmed by the Unity 5.4 log-file table: ``~/Library/Logs/Unity/Player.log``.
    This is the Mac equivalent of Windows/Linux ``Besiege_Data/output_log.txt``.
    """
    root = Path.home() if home is None else home
    return root / UNITY_PLAYER_LOG


def apply_windowed_resolution(
    besiege_data: Path,
    *,
    width: int,
    height: int,
    prefs_path: Path | None = None,
) -> None:
    """Write Besiege Config.xml and the Unity player prefs before launch."""
    write_besiege_window(besiege_data / "Config.xml", width=width, height=height)
    write_unity_window(prefs_path if prefs_path is not None else unity_prefs_path(), width=width, height=height)


def prepare_mac_launch_window(besiege_data: Path) -> tuple[int, int]:
    """Match the next Besiege window to the main display. Returns that size."""
    width, height = launch_window_points()
    apply_windowed_resolution(besiege_data, width=width, height=height)
    return width, height
