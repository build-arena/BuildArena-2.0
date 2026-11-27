"""1.0 control JSON -> 2.0 channel schedule, and the files a run needs.

1.0 applied a control JSON in simulation/simulation_transport.py:39-62: each
control_config entry went through Machine.change_control_key and each
control_sequence entry through add_control_sequence (1.0 spatial/build.py:
1118-1183), which log and skip invalid entries. The Lua script then pressed
and released emulated keys every physics frame (build.py:1295-1298). Here the
same validation is applied, and the Lua key logic is evaluated into
press intervals per 2.0 channel.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field, asdict
from pathlib import Path

from . import legacy as L


class ControlError(ValueError):
    """The control JSON cannot be applied at all (1.0 would raise)."""


@dataclass(frozen=True)
class Binding:
    block_id: str
    action: str
    key: str


@dataclass(frozen=True)
class Press:
    time: float
    key: str
    hold_for: float


@dataclass
class ControlPlan:
    bindings: list[Binding]
    sequence: list[Press]
    dropped: list[dict] = field(default_factory=list)

    def to_json(self) -> dict:
        return {"bindings": [asdict(b) for b in self.bindings],
                "sequence": [asdict(p) for p in self.sequence], "dropped": self.dropped}


def _key_ok(key) -> bool:
    if not isinstance(key, str):
        raise ControlError(f"Control key {key!r} is not a string.")
    return key in L.ARROW_KEYS or bool(re.match(L.DIGIT_KEY_PATTERN, key))


def _binding(entry, actions: dict[str, tuple[str, ...]], block_ids: set[str]) -> tuple[Binding | None, str]:
    """change_control_key's checks, in its order (build.py:1131-1157)."""
    try:
        block_id, action, key = entry["block_id"], entry["action"], entry["key"]
    except (KeyError, TypeError) as exc:
        raise ControlError(f"control_config entry {entry!r} lacks a field: {exc}") from exc
    if not _key_ok(key):
        return None, f"key {key!r} is not in the control key list"
    if str(block_id) not in block_ids:
        return None, f"block {block_id!r} not found"
    if str(block_id) not in actions:
        return None, f"block {block_id!r} has no locomotion actions"
    try:
        known = action in actions[str(block_id)]
    except TypeError as exc:   # 1.0's dict lookup raises on an unhashable action
        raise ControlError(f"Action {action!r} cannot be looked up.") from exc
    if not known:
        return None, f"action {action!r} is not an action of block {block_id!r}"
    return Binding(str(block_id), action, key), ""


def _press(entry, keys: set[str]) -> Press | None:
    """add_control_sequence keeps entries whose key is bound (build.py:1174-1181);
    the Lua export then formats their times as numbers (build.py:1297)."""
    try:
        time, key, hold = entry["time"], entry["key"], entry["hold_for"]
    except (KeyError, TypeError) as exc:
        raise ControlError(f"control_sequence entry {entry!r} lacks a field: {exc}") from exc
    try:
        bound = key in keys
    except TypeError as exc:
        raise ControlError(f"Control key {key!r} cannot be looked up.") from exc
    if not bound:
        return None
    for name, value in (("time", time), ("hold_for", hold)):
        if not isinstance(value, (int, float)):
            raise ControlError(f"control_sequence {name} {value!r} is not a number.")
    return Press(float(time), key, float(hold))


def validate_control(data, actions: dict[str, tuple[str, ...]], block_ids: set[str]) -> ControlPlan:
    """Apply a parsed 1.0 control JSON to a machine the way 1.0 did.

    ``actions`` maps Build ID -> 1.0 actions of powered blocks; ``block_ids``
    holds every Build ID. Invalid entries are dropped with a reason; a JSON
    with no usable key or no sequence raises (simulation_transport.py:58-62).
    """
    if not isinstance(data, dict):
        raise ControlError("Control JSON is not an object.")
    try:
        config, sequence = data["control_config"], data["control_sequence"]
    except KeyError as exc:
        raise ControlError(f"Control JSON lacks {exc}.") from exc
    if not isinstance(config, list) or not isinstance(sequence, list):
        raise ControlError("control_config and control_sequence must be lists.")
    plan = ControlPlan([], [])
    for entry in config:
        binding, reason = _binding(entry, actions, block_ids)
        if binding is None:
            plan.dropped.append({"control_config": entry, "reason": reason})
        else:
            plan.bindings.append(binding)
    keys = {b.key for b in plan.bindings}
    for entry in sequence:
        press = _press(entry, keys)
        if press is None:
            plan.dropped.append({"control_sequence": entry, "reason": "key is not bound"})
        else:
            plan.sequence.append(press)
    if not keys:
        raise ControlError("No control keys assigned to the machine.")
    if not plan.sequence:
        raise ControlError("No control sequence found in the machine.")
    return plan


def _round1(value: float, quantize: bool) -> float:
    """The Lua script holds times as %.1f literals (build.py:1297-1298)."""
    return float("%.1f" % value) if quantize else value


def _merge(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for start, end in sorted(i for i in intervals if i[1] > i[0]):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def key_intervals(presses: list[tuple[float, float]], duration: float, rule: str) -> list[tuple[float, float]]:
    """When one key is held, given its (press, release) entries in list order.

    "lua_last_started": every frame the 1.0 Lua runs, in list order,
    ``if t >= press then start() end; if t >= release then stop() end``; the
    key ends the frame in the state of the last call made. For ordinary
    entries that is the last entry whose press time has passed; an entry with
    a negative hold releases the key from its release time on even though it
    never pressed it.
    "union": the key is held while any entry is.
    """
    if rule == "union":
        return _merge([(max(0.0, s), min(f, duration)) for s, f in presses])
    if rule != "lua_last_started":
        raise ValueError(f"Unknown overlap rule {rule!r}")
    points = sorted({0.0, duration} | {p for s, f in presses for p in (s, f) if 0.0 < p < duration})
    held = []
    for start, end in zip(points, points[1:]):
        state = False
        for s, f in presses:
            if start >= s:
                state = True
            if start >= f:
                state = False
        if state:
            held.append((start, end))
    return _merge(held)


@dataclass(frozen=True)
class ChannelInterval:
    build_id: str
    channel: str
    start: float
    end: float


def channel_schedule(plan: ControlPlan, duration: float, *, quantize: bool, rule: str) -> list[ChannelInterval]:
    """Press intervals per (block, 2.0 channel); a channel is held while any key bound to it is."""
    per_key: dict[str, list[tuple[float, float]]] = {}
    for press in plan.sequence:
        start = _round1(press.time, quantize)
        end = _round1(press.time + press.hold_for, quantize)
        per_key.setdefault(press.key, []).append((start, end))
    held = {key: key_intervals(entries, duration, rule) for key, entries in per_key.items()}
    by_channel: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for binding in plan.bindings:
        target = (binding.block_id, L.ACTION_TO_CHANNEL[binding.action])
        by_channel.setdefault(target, []).extend(held.get(binding.key, []))
    out = []
    for (block_id, channel), intervals in sorted(by_channel.items()):
        out.extend(ChannelInterval(block_id, channel, s, e) for s, e in _merge(intervals))
    return out


RUNTIME_CONTROLLER = Path(__file__).with_name("runtime_controller.py")


def write_python_controller(out_dir: Path, *, duration: float, tick_s: float,
                            intervals: list[ChannelInterval], guid_of: dict[str, str],
                            ballast_guid: str | None, ramp: L.MassRamp | None,
                            mass_unclipped: bool = False, fixed_mass: float | None = None) -> Path:
    """controller.py + schedule.json for ``besiege_cli run --controller``.

    ``mass_unclipped`` sends the cargo mass ramp as is instead of letting the
    2.0 client clip it to the slider range the game publishes.
    ``fixed_mass`` means the cargo mass is written into the BSG: nothing is
    sent, the value is only recorded."""
    out_dir.mkdir(parents=True, exist_ok=True)
    schedule = {
        "schema": "buildarena1.control_schedule.v1", "duration": duration, "tick_s": tick_s,
        "channels": [{"build_id": i.build_id, "guid": guid_of[i.build_id], "channel": i.channel,
                      "start": i.start, "end": i.end} for i in intervals],
        "ballast": (None if ballast_guid is None
                    else {"guid": ballast_guid, "fixed_mass": float(fixed_mass)} if fixed_mass is not None
                    else {"guid": ballast_guid, **asdict(ramp), "unclipped": bool(mass_unclipped)}),
    }
    (out_dir / "schedule.json").write_text(json.dumps(schedule, indent=2), encoding="utf-8")
    controller = out_dir / "controller.py"
    shutil.copyfile(RUNTIME_CONTROLLER, controller)
    return controller


def write_lift_timeline(out_dir: Path, cannon_guids: list[str], duration: float) -> Path:
    """Timeline for lift Lv.2/Lv.3: every cannon fires at 2 s and holds
    (simulation_lift.py:37-44); the release is clipped to the run length."""
    if not cannon_guids:
        raise ValueError("A lift timeline needs at least one Water Cannon.")
    release = min(L.LIFT_FIRE_START + L.LIFT_FIRE_HOLD, duration)
    events = ([{"time": L.LIFT_FIRE_START, "block_guid": g, "channel": "ShootKey", "value": 1.0}
               for g in cannon_guids]
              + [{"time": release, "block_guid": g, "channel": "ShootKey", "value": 0.0} for g in cannon_guids])
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "timeline.json"
    path.write_text(json.dumps({"schema": "buildarena.control_timeline.v2", "events": events}, indent=2),
                    encoding="utf-8")
    return path
