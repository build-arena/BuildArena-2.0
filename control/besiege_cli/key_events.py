from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


TIME_COLUMNS = (
    "time",
    "t",
    "elapsed",
    "elapsed_seconds",
    "seconds",
    "timestamp",
    "time_seconds",
)
KEY_COLUMNS = ("key", "key_code", "keycode", "input", "button")
EVENT_COLUMNS = ("event", "action", "state", "type")
VALUE_COLUMNS = ("value", "pressed", "is_pressed", "down")


KEY_ALIASES = {
    "up": "UpArrow",
    "uparrow": "UpArrow",
    "down": "DownArrow",
    "downarrow": "DownArrow",
    "left": "LeftArrow",
    "leftarrow": "LeftArrow",
    "right": "RightArrow",
    "rightarrow": "RightArrow",
    "space": "Space",
    "spacebar": "Space",
}


@dataclass(frozen=True)
class KeyEvent:
    time: float
    key: str
    value: float


def normalize_key_name(raw: object) -> str:
    text = str(raw).strip()
    if not text:
        return ""
    compact = text.replace(" ", "").replace("-", "").replace("_", "")
    lowered = compact.lower()
    if lowered in KEY_ALIASES:
        return KEY_ALIASES[lowered]
    if lowered.isdigit() and len(lowered) == 1:
        return f"Alpha{lowered}"
    if lowered.startswith("alpha") and lowered[-1:].isdigit():
        return f"Alpha{lowered[-1]}"
    if lowered.startswith("keypad") and lowered[-1:].isdigit():
        return f"Keypad{lowered[-1]}"
    if len(text) == 1 and text.isalnum():
        return f"Alpha{text.upper()}" if text.isdigit() else text.upper()
    return text


def _first_present(row: dict[str, str], candidates: Iterable[str]) -> str | None:
    lowered = {key.lower(): key for key in row.keys()}
    for candidate in candidates:
        original = lowered.get(candidate.lower())
        if original is not None and row.get(original, "") != "":
            return original
    return None


def _parse_value(row: dict[str, str]) -> float:
    event_col = _first_present(row, EVENT_COLUMNS)
    if event_col is not None:
        event = str(row[event_col]).strip().lower()
        if event in {"down", "press", "pressed", "start", "on", "true", "1"}:
            return 1.0
        if event in {"up", "release", "released", "stop", "off", "false", "0"}:
            return 0.0
        raise ValueError(f"Unsupported key event value: {row[event_col]!r}")

    value_col = _first_present(row, VALUE_COLUMNS)
    if value_col is not None:
        value = str(row[value_col]).strip().lower()
        if value in {"true", "yes", "on", "down", "pressed"}:
            return 1.0
        if value in {"false", "no", "off", "up", "released"}:
            return 0.0
        return 1.0 if float(value) > 0 else 0.0

    raise ValueError(
        "Key event row has neither an event/state column "
        f"({', '.join(EVENT_COLUMNS)}) nor a value column ({', '.join(VALUE_COLUMNS)}); "
        "cannot determine whether the key is pressed or released. Row: " + repr(row)
    )


def read_key_events(path: str | Path) -> list[KeyEvent]:
    csv_path = Path(path)
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"CSV has no header: {csv_path}")
        rows = list(reader)

    if not rows:
        return []

    first_row = rows[0]
    time_col = _first_present(first_row, TIME_COLUMNS)
    key_col = _first_present(first_row, KEY_COLUMNS)
    if time_col is not None and key_col is not None:
        events = [
            KeyEvent(
                time=float(row[time_col]),
                key=normalize_key_name(row[key_col]),
                value=_parse_value(row),
            )
            for row in rows
            if str(row.get(key_col, "")).strip()
        ]
        return sorted(events, key=lambda event: event.time)

    if time_col is None:
        raise ValueError(
            "Key CSV must include a time column, for example 'time' or 'elapsed_seconds'."
        )

    events: list[KeyEvent] = []
    candidate_keys = [
        column
        for column in reader.fieldnames
        if column != time_col and column.lower() not in set(EVENT_COLUMNS + VALUE_COLUMNS)
    ]
    for row in rows:
        t = float(row[time_col])
        for column in candidate_keys:
            raw = str(row.get(column, "")).strip()
            if raw == "":
                continue
            value = raw.lower()
            active = value in {"1", "true", "yes", "down", "pressed", "on"} or (
                value not in {"0", "false", "no", "up", "released", "off"} and float(raw) > 0
            )
            events.append(
                KeyEvent(time=t, key=normalize_key_name(column), value=1.0 if active else 0.0)
            )

    return sorted(events, key=lambda event: event.time)


def key_events_to_intervals(events: Iterable[KeyEvent]) -> list[dict[str, object]]:
    active: dict[str, float] = {}
    intervals: list[dict[str, object]] = []
    for event in sorted(events, key=lambda item: item.time):
        if event.value > 0:
            active.setdefault(event.key, event.time)
        else:
            start = active.pop(event.key, None)
            if start is not None and event.time >= start:
                intervals.append({"key": event.key, "start": start, "end": event.time})
    return intervals
