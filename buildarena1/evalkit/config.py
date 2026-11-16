"""Switches of the evaluation.

Each field is a choice where 1.0 could not be followed exactly, or where a
check in the game could overturn the default. Except for lift_success_rule,
the defaults are the settings the evaluation ran with; buildarena1/README.md
section 5 lists the ones that differ from 1.0.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Literal
import warnings


LEGACY_LIFT_RULE_NOTE = "lift_success_rule is missing; using 'ba1' (BuildArena 1.0) for this legacy configuration."


def validate_lift_success_rule(value: str) -> None:
    if not isinstance(value, str) or value not in ("height", "ba1"):
        raise ValueError(f"lift_success_rule must be 'height' or 'ba1', got {value!r}")


# 1.0 BSG parameters the 2.0 exporter does not write. Keyed by 2.0 block
# type id; values are (xml tag, key, text) as in 1.0 blocks/configs/
# powered_wheel.yaml:24-35 and water_cannon.yaml:25-28. Key bindings
# (bmt-forward, bmt-backward, bmt-shoot) are left out: 2.0 drives channels.
V1_BLOCK_PARAMS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "2": (
        ("Integer", "bmt-version", "1"),
        ("Single", "bmt-speed", "1"),
        ("Single", "bmt-acceleration", "60"),
        ("Boolean", "bmt-automatic", "False"),
        ("Boolean", "bmt-toggle-mode", "False"),
        ("Boolean", "bmt-auto-brake", "True"),
        ("Boolean", "bmt-opt-collider", "True"),
        ("Single", "bmt-contact", "0.1"),
    ),
    "56": (
        ("Boolean", "bmt-hold-to-fire", "True"),
        ("Single", "bmt-strength", "4"),
    ),
}


@dataclass(frozen=True)
class EvalConfig:
    # --- scoring -----------------------------------------------------------
    # Round time, positions and mass to 0.1 the way the 1.0 Lua print did
    # (spatial/build.py:1306-1316 uses %.1f), then dedup and sort like
    # simulation/operations.py:128-134, 322-378. False scores full precision.
    quantize_like_lua: bool = True
    # Order of a tick's log lines inside one printed time step. "print" is
    # the order the 1.0 Lua printed them; the order 1.0's log window really
    # produced is unknowable without the 1.0 Windows setup.
    log_line_order: Literal["print", "reversed"] = "print"
    # Telemetry field that stands for Lua `burning()` of a Water Cannon.
    heated_field: Literal["fire_burning", "steam"] = "fire_burning"
    # Blocks the lift Lv.2/Lv.3 judge sees. "all" = every non-start block;
    # "random4" = 1.0's random.sample of 4 with a fixed seed.
    lift_tracking: Literal["all", "random4"] = "all"
    lift_random4_seed: int = 0
    # Drop telemetry rows after the 1.0 duration of the cell.
    truncate_at_duration: bool = True
    # Labels of the trajectory text sent back to the control agent. "1.0"
    # reproduces scheduler/runner.py:221-226, where y carries the vertical
    # and z the northward offset; "task" labels them by task coordinates.
    feedback_axis_labels: Literal["1.0", "task"] = "1.0"

    # --- placement ---------------------------------------------------------
    # Gap left under the lowest collider for transport and lift (stands in for
    # 1.0's Set Ground button).
    spawn_clearance: float = 0.1
    # Height of the sandbox floor in game y. 1.0 thresholds (30 for lift,
    # 20 for initial heights) assume 0. Subtracted from telemetry y before
    # judging when the measured floor is not 0.
    ground_y: float = 0.0
    # Write V1_BLOCK_PARAMS into the exported BSG.
    backfill_params: bool = True
    backfill_param_table: dict = field(default_factory=lambda: V1_BLOCK_PARAMS)
    # Largest Ballast mass the run may command; None = 1.0's 50 / 1500.
    ballast_mass_cap: float | None = None
    # A mass the controller sends at simulation time t counts as in effect for
    # telemetry samples later than t + this delay (legacy_log.MassTrace).
    mass_effect_delay: float = 0.0
    # How the cargo mass follows 1.0's curve (5 to 50 over 2 s for transport,
    # 5 to 1500 over 15 s for support, from 0.5 s, floor 0.005).
    #   "plugin_curve" (default): a BepInEx plugin, not part of this
    #     repository (README section 6), sets the cargo's Rigidbody mass every
    #     physics frame; 1.0's BSG start value and placement; the judge reads
    #     the plugin's per-frame log.
    #   "fixed_bsg": transport Lv.2/Lv.3 write
    #     transport_cargo_mass_in_bsg into the BSG, cargo put
    #     cargo_drop_above_top over the machine, nothing sent.
    #   "slider_clipped": the 2.0 control channel's mass slider, which the
    #     game clips to its slider range (0.2 to 3 in build 24131898).
    cargo_mass_mode: Literal["plugin_curve", "fixed_bsg", "slider_clipped"] = "plugin_curve"
    # Send the cargo mass ramp past the slider range the
    # game publishes (1.90: Ballast mass 0.2..3) instead of letting the 2.0
    # client clip it. 1.0's Lua set 50 and 1500 directly.
    ballast_mass_unclipped: bool = False
    # cargo_mass_mode "fixed_bsg": transport Lv.2/Lv.3 write this cargo mass
    # into the BSG and send none (the task text's 50-unit cargo; 1.0's ramp
    # is dropped).
    transport_cargo_mass_in_bsg: float | None = 50.0
    # For cells whose cargo mass is fixed in the BSG
    # (transport Lv.2/Lv.3 and support under "fixed_mass_banks"), the gap
    # between the machine's highest collider point and the cargo's underside.
    # None keeps 1.0's placement: the cargo origin 3 above the highest block
    # centre (simulation_transport.py:81).
    cargo_drop_above_top: float | None = 0.1

    # --- support -----------------------------------------------------------
    # "curve_banks" (default): banks of two heavy scaled Ballast blocks
    # (evalkit/terrain.py), the cargo mass on 1.0's curve (by cargo_mass_mode)
    # and 1.0's support judge. "fixed_mass_banks" (standby, needs no plugin):
    # one fixed cargo mass per run (evalkit/support_fixed.py).
    # "refuse": support is not run.
    support_protocol: Literal["curve_banks", "fixed_mass_banks", "refuse"] = "curve_banks"
    support_bank_mass: float = 100000.0
    support_success_mass: float = 154.5     # 1.0's mass curve at t = 2 s
    support_max_mass: float = 1500.0
    support_bisect_steps: int = 4           # log bisection between the two; about 15 % resolution
    support_run_seconds: float = 10.0

    # --- control -----------------------------------------------------------
    # 1.0 wrote control times with %.1f into the Lua script (build.py:1296-1298).
    quantize_control_times: bool = True
    # How several sequence entries of one key combine. "lua_last_started"
    # is what the 1.0 Lua loop does; "union" ORs the intervals.
    overlap_rule: Literal["lua_last_started", "union"] = "lua_last_started"
    control_tick_s: float = 0.1
    transport_trials: int = 3

    # --- run ---------------------------------------------------------------
    sandbox: str = "BARREN EXPANSE"   # 1.0 only says "the last sandbox on the right"
    telemetry_hz: int = 25
    recorder_hz: int = 25

    # Appended to preserve the positional arguments of the pre-switch config.
    # Lift Lv.2/Lv.3: "height" needs max_height > 30; "ba1" also needs
    # elevation_change > 5. Lv.1 is unchanged. New runs default to height;
    # any JSON configuration without this field is read as ba1 for compatibility.
    lift_success_rule: Literal["height", "ba1"] = "height"

    def __post_init__(self) -> None:
        validate_lift_success_rule(self.lift_success_rule)

    @classmethod
    def from_json(cls, raw: dict, *, source: str) -> "EvalConfig":
        values = dict(raw)
        if "lift_success_rule" not in values:
            warnings.warn(f"{source}: {LEGACY_LIFT_RULE_NOTE}", UserWarning, stacklevel=2)
            values["lift_success_rule"] = "ba1"
        return cls(**values)

    def to_json(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Paths:
    """Where the 1.0 and 2.0 trees and the 2.0 data files live on this host."""
    v1_root: Path
    v2_root: Path
    collider_dump: Path
    besiege_data: Path

    def v2_environment(self) -> dict[str, str]:
        """Process environment 2.0's buildarena package reads (paths.py)."""
        blocks = self.v2_root / "blocks"
        return {
            "BLOCK_REGISTRY_PATH": str(blocks / "block_registry.generated.toml"),
            "BLOCK_ROLES_PATH": str(blocks / "block_roles.toml"),
            "BLOCK_AUTHORING_PATH": str(blocks / "block_authoring.toml"),
            "COLLIDER_DUMP_PATH": str(self.collider_dump),
            "BESIEGE_DATA_PATH": str(self.besiege_data),
        }
