"""BuildArena 1.0 constants the evaluation stage reproduces.

Every value here is read off BuildArena 1.0 at commit c3005b9
(https://github.com/AI4Science-WestlakeU/BuildArena, CC BY-NC 4.0); the comment
next to each one names the file and lines. ``buildarena1/tests`` imports the
1.0 originals (when ``BA1_ROOT`` points at a 1.0 clone) and checks these values
against them, so a drift shows up as a failing test rather than a silent change
of judge.
"""

from __future__ import annotations

from dataclasses import dataclass

# 2.0 public block name -> 1.0 block name. 1.0 names: blocks/configs/*.yaml `name`.
# 2.0 names: blocks/block_authoring.toml.
V2_TO_V1_NAME: dict[str, str] = {
    "Starting Block": "Starting Block",
    "Single Wooden Block": "Small Wooden Block",
    "Wheel": "Powered Wheel",
    "Water Cannon": "Water Cannon",
    "Torch": "Torch",
    "Brace": "Brace",
    "Rope Winch": "Winch",
}

# 1.0 block mass table, blocks/configs/*.yaml `weight`. analyze/sim_common.py:87-93
# sums these by name; Ballast is excluded there (sim_common.py:80, 91).
V1_MASS: dict[str, float] = {
    "Starting Block": 0.25,
    "Small Wooden Block": 0.3,
    "Powered Wheel": 1.0,
    "Water Cannon": 1.5,
    "Torch": 1,
    "Brace": 0.5,
    "Winch": 0.4,
}

# 1.0 locomotion actions per block (blocks/configs/powered_wheel.yaml:39-41,
# water_cannon.yaml:29-30; every other config has `locomotion: false`).
V1_ACTIONS: dict[str, tuple[str, ...]] = {
    "Powered Wheel": ("spinning_forward", "spinning_backward"),
    "Water Cannon": ("hold_to_fire",),
}

# 1.0 action -> 2.0 control channel (2.0 blocks/control_descriptors/mechanical.py:10-19,
# weapons.py:16-18).
ACTION_TO_CHANNEL: dict[str, str] = {
    "spinning_forward": "ForwardKey",
    "spinning_backward": "BackwardKey",
    "hold_to_fire": "ShootKey",
}

# Keys the 1.0 control tools accept (spatial/build.py:34-39, 1131-1132).
ARROW_KEYS: tuple[str, ...] = ("UpArrow", "DownArrow", "LeftArrow", "RightArrow")
DIGIT_KEY_PATTERN = r"^(Alpha|Keypad)[0-9]$"

BALLAST_ID = "Ballast"           # local_id of the cargo, simulation_transport.py:77
BALLAST_BLOCK_TYPE_ID = "35"     # blocks/configs/ballast_.yaml:2
BALLAST_DATA_MASS = "5.0"        # ballast_.yaml:40-41, written into the BSG
# Ballast rotation after Block.rotate(yaw=0, pitch=270, roll=0) on the default
# orientation (simulation_transport.py:78, simulation_support.py:78), as x, y, z, w.
BALLAST_ROTATION_XYZW: tuple[float, float, float, float] = (
    -0.7071067811865476, -0.0, -0.0, 0.7071067811865475)
BALLAST_SCALE_DEFAULT: tuple[float, float, float] = (2.5, 2.5, 1.5)   # ballast_.yaml:5
BALLAST_SCALE_TRANSPORT_HARD: tuple[float, float, float] = (4.0, 8.0, 1.5)  # simulation_transport.py:79-80
BALLAST_DROP_ABOVE_TOP = 3.0      # simulation_transport.py:81, simulation_support.py:77

# Support placement: Orientation(rot=[90, 0, 0]) (simulation_support.py:71), as x, y, z, w.
SUPPORT_ROTATED_XYZW: tuple[float, float, float, float] = (
    0.0, 0.7071067811865475, 0.0, 0.7071067811865475)
SUPPORT_GAP_LENGTH = {"soft": 5.0, "medium": 10.0, "hard": 20.0}  # simulation_support.py:59-64
# 1.0 Machine.to_xml writes Global y = 5 - shift_virtual[1] (spatial/build.py:1231-1235).
V1_GLOBAL_Y = 5.0

# Lift Lv.2/Lv.3 control: every Water Cannon fires from 2 s for 30 s
# (simulation_lift.py:37-44).
LIFT_FIRE_START = 2.0
LIFT_FIRE_HOLD = 30.0


@dataclass(frozen=True)
class MassRamp:
    """Lua ballast mass schedule, spatial/build.py:1252-1261, 1289-1292."""
    mass_min: float
    mass_max: float
    ramp_duration: float
    ramp_start: float = 0.5
    clamp_low: float = 0.005
    cap: float | None = None   # largest mass the run may command; not in 1.0

    def mass_at(self, t: float) -> float:
        frac = max(min((t - self.ramp_start) / self.ramp_duration, 1.0), self.clamp_low)
        mass = self.mass_min + (self.mass_max - self.mass_min) * frac
        return mass if self.cap is None else min(mass, self.cap)


TRANSPORT_RAMP = MassRamp(mass_min=5.0, mass_max=50.0, ramp_duration=2.0)   # change_mass False
SUPPORT_RAMP = MassRamp(mass_min=5.0, mass_max=1500.0, ramp_duration=15.0)  # change_mass True

# Simulation length in seconds, simulation_*.py run_simulation_sequence(duration=...).
DURATION = {
    ("transport", "soft"): 30.0, ("transport", "medium"): 30.0, ("transport", "hard"): 30.0,
    ("support", "soft"): 17.0, ("support", "medium"): 17.0, ("support", "hard"): 17.0,
    ("lift", "soft"): 10.0, ("lift", "medium"): 30.0, ("lift", "hard"): 30.0,
}

CATEGORIES = ("transport", "support", "lift")
LEVELS = ("soft", "medium", "hard")


def v1_name(v2_name: str) -> str:
    """1.0 name of a 2.0 block; raises for blocks outside the 1.0 set."""
    if v2_name not in V2_TO_V1_NAME:
        raise KeyError(f"Block {v2_name!r} has no BuildArena 1.0 counterpart.")
    return V2_TO_V1_NAME[v2_name]
