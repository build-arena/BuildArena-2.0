"""Control timeline JSON (buildarena.control_timeline.v2): reading,
validation against a machine's real control channels, and construction from
recorded key events.

A timeline is the offline controller-program format for `besiege_cli run
--controller <timeline>.json`. A hand-authored timeline only needs the four
addressing fields per event::

    {
      "schema": "buildarena.control_timeline.v2",
      "events": [
        {"time": 0.5, "block": 1, "channel": "ThrustKey", "value": 1.0},
        {"time": 3.0, "block": 1, "channel": "ThrustKey", "value": 0.0}
      ]
    }

Before installation the CLI resolves every event against the machine's
channel spec (``besiege_cli.machine.infer_channels``), filling in the
activation address (``keylist_index``, ``semantic_member_kind``) the mod
needs and rejecting unknown or unactivatable channels outright. ``run_id``
is stamped by the CLI at install time; the mod refuses a timeline whose
run_id does not match the active run manifest.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

from controller_sdk.protocol import TIMELINE_SCHEMA

from .key_events import KeyEvent
from .machine import ControlChannel

_KEYLIST_POSITION_NAME = re.compile(r"^keylist_(\d+)$")

_REQUIRED_EVENT_FIELDS = ("time", "channel", "value")


def read_timeline_json(path: str | Path) -> dict[str, object]:
    """Read and structurally validate a timeline JSON file.

    Rejects anything that is not schema ``buildarena.control_timeline.v2``
    (including the retired CSV format and v1 JSON) with an explicit error
    instead of attempting a conversion.
    """
    timeline_path = Path(path)
    try:
        payload = json.loads(timeline_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{timeline_path} is not valid JSON ({exc}). Timeline controllers must be "
            f"{TIMELINE_SCHEMA} JSON files; the legacy control_timeline.csv format is no "
            "longer supported."
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{timeline_path} must contain a JSON object, got {type(payload).__name__}.")
    schema = payload.get("schema")
    if schema != TIMELINE_SCHEMA:
        raise ValueError(
            f"{timeline_path} has schema {schema!r}; expected {TIMELINE_SCHEMA!r}. "
            "Older timeline formats are not converted implicitly."
        )
    events = payload.get("events")
    if not isinstance(events, list) or not events:
        raise ValueError(f"{timeline_path} has no non-empty 'events' array.")
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise ValueError(f"{timeline_path} events[{index}] is not a JSON object.")
        missing = [field for field in _REQUIRED_EVENT_FIELDS if field not in event]
        if missing:
            raise ValueError(
                f"{timeline_path} events[{index}] is missing required field(s) {missing}; "
                f"every event needs {list(_REQUIRED_EVENT_FIELDS)}."
            )
        has_block = "block" in event
        has_guid = "block_guid" in event
        if has_block == has_guid:
            raise ValueError(
                f"{timeline_path} events[{index}] must contain exactly one block address: "
                "integer 'block' (the inspect-machine index) or string 'block_guid'."
            )
        if has_block and (
            isinstance(event["block"], bool) or not isinstance(event["block"], int)
        ):
            raise ValueError(
                f"{timeline_path} events[{index}].block must be an integer block index, "
                f"got {event['block']!r}."
            )
        if has_guid and (
            not isinstance(event["block_guid"], str) or not event["block_guid"]
        ):
            raise ValueError(
                f"{timeline_path} events[{index}].block_guid must be a non-empty string."
            )
        float(event["time"])  # raises on non-numeric
        float(event["value"])
    return payload


def resolve_timeline_events(
    events: Iterable[dict[str, object]],
    channels: Iterable[ControlChannel],
) -> list[dict[str, object]]:
    """Resolve every timeline event against the machine's real control
    channel spec, returning fully-addressed events sorted by time.

    An event addresses its block with exactly one of ``block`` (the integer
    index printed by inspect-machine) or ``block_guid`` (the protocol-level
    identifier). Its ``channel`` may be the catalog semantic name (e.g.
    ``"ThrustKey"``) or the position-neutral ``"keylist_<index>"`` form.
    Unknown addresses and channels raise rather than silently doing nothing.
    """
    channel_list = list(channels)
    by_pair: dict[tuple[str, str], ControlChannel] = {}
    by_local_pair: dict[tuple[int, str], ControlChannel] = {}
    for channel in channel_list:
        for name in (channel.channel, *channel.aliases):
            for table, address in ((by_pair, channel.block_guid), (by_local_pair, channel.local_index)):
                key = (address, name)
                if key in table and table[key] != channel:
                    raise ValueError(f"Ambiguous control channel address {key!r}.")
                table[key] = channel
    by_guid_index: dict[tuple[str, int], ControlChannel] = {
        (channel.block_guid, channel.keylist_index): channel
        for channel in channel_list
        if channel.keylist_index >= 0
    }
    by_local_keylist: dict[tuple[int, int], ControlChannel] = {
        (channel.local_index, channel.keylist_index): channel
        for channel in channel_list
        if channel.keylist_index >= 0
    }

    unresolved: list[str] = []
    unactivatable: list[str] = []
    resolved_events: list[dict[str, object]] = []
    for event in events:
        has_block = "block" in event
        has_guid = "block_guid" in event
        if has_block == has_guid:
            raise ValueError(
                f"Timeline event at time={event.get('time')} must contain exactly one of "
                "'block' or 'block_guid'."
            )
        if has_block and (
            isinstance(event["block"], bool) or not isinstance(event["block"], int)
        ):
            raise ValueError(
                f"Timeline event at time={event.get('time')} has non-integer "
                f"block index {event['block']!r}."
            )
        block_index = int(event["block"]) if has_block else None
        block_guid = str(event["block_guid"]) if has_guid else ""
        channel_name = str(event.get("channel", ""))
        channel = (
            by_local_pair.get((block_index, channel_name))
            if block_index is not None
            else by_pair.get((block_guid, channel_name))
        )
        if channel is None:
            position_match = _KEYLIST_POSITION_NAME.match(channel_name)
            if position_match is not None:
                keylist_index = int(position_match.group(1))
                channel = (
                    by_local_keylist.get((block_index, keylist_index))
                    if block_index is not None
                    else by_guid_index.get((block_guid, keylist_index))
                )
        if channel is None:
            address = (
                f"block={block_index}"
                if block_index is not None
                else f"block_guid={block_guid!r}"
            )
            unresolved.append(
                f"time={event.get('time')} {address} channel={channel_name!r}"
            )
            continue
        if not channel.semantic_member_kind and channel.keylist_index < 0:
            unactivatable.append(
                f"time={event.get('time')} block={channel.local_index} "
                f"channel={channel_name!r}"
            )
            continue
        resolved_events.append(
            {
                "time": round(float(event["time"]), 4),
                "channel_id": channel.channel_id,
                "block_guid": channel.block_guid,
                "block_id": channel.block_id,
                "block_name": channel.block_name,
                "channel": channel.channel,
                "key": channel.keys[0] if channel.keys else "",
                "value": float(event["value"]),
                "local_index": channel.local_index,
                "semantic_member_kind": channel.semantic_member_kind or "",
                "keylist_index": channel.keylist_index,
            }
        )

    if unresolved:
        available = ", ".join(
            f'block {channel.local_index} ("{channel.block_name}") '
            f'channel "{channel.channel}"'
            for channel in channel_list
        )
        raise ValueError(
            "Timeline references control channels that do not exist on this machine's control "
            "channel spec (typo, stale guid, or wrong machine):\n"
            + "\n".join(unresolved)
            + f"\nAvailable channels: {available or '(none)'}."
        )
    if unactivatable:
        raise ValueError(
            "Timeline references control channels with neither a semantic_member_kind nor a "
            "resolved keylist_index; the controller mod has no way to activate them:\n"
            + "\n".join(unactivatable)
        )
    return sorted(resolved_events, key=lambda row: (float(row["time"]), str(row["channel_id"])))


def shift_timeline_events(
    events: list[dict[str, object]],
    offset_seconds: float,
) -> list[dict[str, object]]:
    """Return a copy of ``events`` with every time increased by ``offset_seconds``.

    Used so a timeline's first actuation waits out the same pre-controller
    hold that delays a live Python controller. ``offset_seconds`` must be >= 0.
    """
    if isinstance(offset_seconds, bool) or not isinstance(offset_seconds, (int, float)):
        raise TypeError("timeline hold offset must be numeric.")
    offset = float(offset_seconds)
    if offset < 0.0:
        raise ValueError("timeline hold offset must be >= 0.")
    if offset == 0.0:
        return [dict(event) for event in events]
    shifted: list[dict[str, object]] = []
    for event in events:
        item = dict(event)
        item["time"] = round(float(event["time"]) + offset, 4)
        shifted.append(item)
    return shifted


def installed_timeline_payload(
    resolved_events: list[dict[str, object]],
    *,
    run_id: str,
) -> dict[str, object]:
    """Build the timeline payload installed into the mod data dir, bound to
    one run id."""
    if not run_id:
        raise ValueError("installed_timeline_payload requires a non-empty run_id.")
    duration = max((float(event["time"]) for event in resolved_events), default=0.0)
    return {
        "schema": TIMELINE_SCHEMA,
        "run_id": run_id,
        "duration_seconds": round(duration, 4),
        "events": resolved_events,
    }


def build_timeline_from_key_events(
    key_events: Iterable[KeyEvent],
    channels: Iterable[ControlChannel],
    *,
    start_at_seconds: float | None = None,
) -> dict[str, object]:
    """Convert recorded keyboard transitions into a timeline JSON payload by
    mapping each key to every control channel bound to it.

    ``start_at_seconds`` shifts the whole timeline so its first event starts
    at that time, trimming recorded idle lead-in.
    """
    channel_list = list(channels)
    by_key: dict[str, list[ControlChannel]] = {}
    for channel in channel_list:
        for key in channel.keys:
            by_key.setdefault(str(key), []).append(channel)

    events: list[dict[str, object]] = []
    for event in sorted(key_events, key=lambda item: (item.time, item.key, item.value)):
        value = 1.0 if event.value > 0 else 0.0
        for channel in by_key.get(event.key, []):
            events.append(
                {
                    "time": round(float(event.time), 4),
                    "block_guid": channel.block_guid,
                    "channel": channel.channel,
                    "value": value,
                }
            )
    if not events:
        raise ValueError(
            "No recorded key transition mapped to any control channel; the recording and the "
            "machine's channel spec do not overlap."
        )

    if start_at_seconds is not None:
        first_time = min(float(event["time"]) for event in events)
        shift = first_time - float(start_at_seconds)
        if shift > 0:
            for event in events:
                event["time"] = round(max(0.0, float(event["time"]) - shift), 4)

    resolved = resolve_timeline_events(events, channel_list)
    duration = max(float(event["time"]) for event in resolved)
    return {
        "schema": TIMELINE_SCHEMA,
        "duration_seconds": round(duration, 4),
        "events": resolved,
    }


def write_timeline_json(path: str | Path, timeline: dict[str, object]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(timeline, indent=2, ensure_ascii=False), encoding="utf-8")
