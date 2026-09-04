"""Reusable Heavy Launcher: unattended orbit-and-return mission control.

Public Controller SDK example. Rebuild
``control/examples/Reusable_Heavy_Launcher.json``, then fly it on LONE ORB.

The rebuilt machine gets fresh GUIDs each time, so this controller discovers
the Starting Block, four Boosters, and two Reaction Steering Blocks from the
prepared BSG that ``besiege_cli run`` exports. Engine duty is Bresenham PWM
on ``ThrustKey``. The original orbit machine baked ``bmt-fthrust=2`` into the
BSG while the guidance model stayed at ``thrust_accel=36``; rebuilds omit
that bake, so this controller pins ``fthrust`` to the published maximum and
does not rescale duty. Wheel ``speed`` stays at the game default 0.5.
Do not map duty onto ``fthrust``.
Wheel A/B torque axes are the same machine-frame calibration as the original
orbit experiment:

    wheel A (larger |machine-x| offset) LeftKey ~= -x
    wheel B (larger |machine-z| offset) LeftKey ~= -z

Environment: the LONE ORB space sandbox with a Newtonian point-gravity
planet. Scene GM / center / surface radius stay explicit constants because
gravity fields are not part of the configurable v4 telemetry contract.

Mission sequencer (one closed-loop episode):
    pad -> guided_ascent -> orbit_coast (>= 360 deg swept) ->
    kill_burn -> final_descent -> touchdown

Usage:
    uv run python -m besiege_cli run --bsg <machine.bsg> \\
        --controller control/examples/reusable_heavy_launcher_orbit.py \\
        --sandbox "LONE ORB" --experiment launcher_demo \\
        --telemetry-hz 10 --telemetry-profile full
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path

from controller_sdk import ControllerClient
from controller_sdk.protocol import ENV_CHANNEL_CATALOG, ENV_MACHINE_BSG, ENV_RUN_DIR

ROOT_GUID = ""
ENGINE_GUIDS: tuple[str, ...] = ()
WHEEL_GUIDS: tuple[str, ...] = ()
WHEEL_A_GUID = ""
WHEEL_B_GUID = ""
# Exact MSlider.Key published live by ThrusterBlockBehaviour.
BOOSTER_POWER_SLIDER = "fthrust"
# Live key on Reaction Steering Block (id 101). Authored docs used to say
# rotation-speed; that string is what Grid Fins publish, not the wheels.
WHEEL_SPEED_SLIDER = "speed"
# Machine-frame torque axis of holding LeftKey on each wheel (unit vector);
# RightKey is the exact opposite. Measured in free fall (calibration sessions
# 20260821-110015 on-pad + 20260821-110220 airborne, drift-corrected):
# wheel A LeftKey ~ -x, wheel B LeftKey ~ -z, each alpha ~= 0.1 rad/s^2.
WHEEL_CALIBRATION: dict[str, dict[str, tuple[float, float, float]]] = {}
# Scene constants are explicit inputs, not telemetry fallbacks. Recalibrate
# them before using this controller in another sandbox.
LONE_ORB_CENTER = (0.0, -600.0, 0.0)
LONE_ORB_SURFACE_RADIUS = 600.0
LONE_ORB_GM = 8_456_800.0
LONE_ORB_MAX_GRAVITY = 22.0


# ---------------------------------------------------------------------------
# Small vector helpers (tuples in, tuples out; no numpy in the 10 Hz loop)
# ---------------------------------------------------------------------------

def v_add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def v_sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def v_scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def v_dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def v_cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def v_norm(a):
    return math.sqrt(v_dot(a, a))


def v_unit(a):
    n = v_norm(a)
    if n < 1e-9:
        return (0.0, 0.0, 0.0)
    return v_scale(a, 1.0 / n)


def q_rotate(q, v):
    """Rotate vector v by quaternion q=(x,y,z,w) (Unity component order)."""
    x, y, z, w = q
    ux, uy, uz = (x, y, z)
    s = w
    uv = v_cross((ux, uy, uz), v)
    uuv = v_cross((ux, uy, uz), uv)
    return v_add(v, v_add(v_scale(uv, 2.0 * s), v_scale(uuv, 2.0)))


def q_conj(q):
    return (-q[0], -q[1], -q[2], q[3])


# ---------------------------------------------------------------------------
# Telemetry frame
# ---------------------------------------------------------------------------

@dataclass
class Frame:
    """One decoded BAT3 root-block sample plus derived orbit kinematics."""

    t: float
    pos: tuple
    vel: tuple
    ang_vel: tuple
    rot: tuple
    gravity_center: tuple
    gravity_acc: tuple
    gravity_available: bool
    surface_radius: float
    fuel_fraction: float
    block_count: int
    machine_integrity: float
    # derived
    rel: tuple = (0.0, 0.0, 0.0)
    radius: float = 0.0
    up_r: tuple = (0.0, 1.0, 0.0)
    v_rad: float = 0.0
    v_tan_vec: tuple = (0.0, 0.0, 0.0)
    v_tan: float = 0.0
    speed: float = 0.0
    g_mag: float = 0.0
    up_axis: tuple = (0.0, 1.0, 0.0)  # rocket long axis (world)

    @staticmethod
    def fuel_fraction_from_sample(sample) -> float:
        """Controller-derived remaining/capacity over every fuel-valid block."""
        names = set(sample.records.dtype.names or ())
        if "fuel" not in names or "fuel_valid" not in names:
            raise ValueError("BAT4 sample is missing per-block fuel; use profile full.")
        remaining = 0.0
        capacity = 0.0
        for row in sample.records:
            if int(row["fuel_valid"]) != 1:
                continue
            tank = row["fuel"]
            remaining += float(tank[0])
            capacity += float(tank[1])
        if capacity <= 0.0:
            raise ValueError(
                "BAT4 sample has no fuel-valid blocks; cannot form a fuel fraction."
            )
        return remaining / capacity

    @staticmethod
    def from_telemetry(sample, record) -> "Frame":
        names = set(record.dtype.names or ())
        required = {"position", "rotation", "velocity", "angular_velocity"}
        if not required.issubset(names):
            raise ValueError(
                f"BAT3 root record is missing fields {sorted(required - names)}."
            )
        pos = tuple(float(value) for value in record["position"])
        vel = tuple(float(value) for value in record["velocity"])
        rot = tuple(float(value) for value in record["rotation"])
        ang_vel = tuple(float(value) for value in record["angular_velocity"])
        rel = v_sub(pos, LONE_ORB_CENTER)
        radius = v_norm(rel)
        if radius <= 1e-6:
            raise ValueError("Root block is at the configured gravity center.")
        up_r = v_scale(rel, 1.0 / radius)
        g_mag = min(LONE_ORB_MAX_GRAVITY, LONE_ORB_GM / (radius * radius))
        gravity_acc = v_scale(up_r, -g_mag)
        machine = sample.machine
        if machine.machine_integrity is None or machine.alive_block_count is None:
            raise ValueError(
                "Full BAT4 telemetry did not provide machine integrity fields."
            )
        frame = Frame(
            t=float(sample.simulation_time),
            pos=pos,
            vel=vel,
            ang_vel=ang_vel,
            rot=rot,
            gravity_center=LONE_ORB_CENTER,
            gravity_acc=gravity_acc,
            gravity_available=True,
            surface_radius=LONE_ORB_SURFACE_RADIUS,
            fuel_fraction=Frame.fuel_fraction_from_sample(sample),
            block_count=int(machine.alive_block_count),
            machine_integrity=float(machine.machine_integrity),
        )
        frame.rel = rel
        frame.radius = radius
        frame.up_r = up_r
        frame.v_rad = v_dot(frame.vel, frame.up_r)
        frame.v_tan_vec = v_sub(frame.vel, v_scale(frame.up_r, frame.v_rad))
        frame.v_tan = v_norm(frame.v_tan_vec)
        frame.speed = v_norm(frame.vel)
        frame.g_mag = g_mag
        frame.up_axis = v_unit(q_rotate(frame.rot, (0.0, 1.0, 0.0)))
        return frame


# ---------------------------------------------------------------------------
# Attitude controller: two momentum wheels as bang-bang torque sources
# ---------------------------------------------------------------------------

@dataclass
class AttitudeController:
    """Point the rocket's long axis at a desired world direction using the
    two orthogonal reaction wheels (LeftKey/RightKey = +/- torque about a
    machine-frame axis; auto-brake damps when released).

    Control law: omega_des = kp * (u x d) with lookahead-predicted u; each
    wheel receives the projection of (omega_des - omega) onto its own world
    torque axis and fires bang-bang outside a deadband.
    """

    kp: float
    kd: float
    deadband: float
    lookahead: float

    def commands(self, *, frame: Frame, desired_dir: tuple) -> tuple[list[tuple[str, str]], dict]:
        u = frame.up_axis
        omega = frame.ang_vel
        # first-order latency compensation: u advances along omega x u
        u_pred = v_unit(v_add(u, v_scale(v_cross(omega, u), self.lookahead)))
        err = v_cross(u_pred, desired_dir)
        omega_des = v_scale(err, self.kp)
        torque_req = v_sub(omega_des, v_scale(omega, self.kd))
        controls: list[tuple[str, str]] = []
        debug = {"att_err": v_norm(err), "align": v_dot(u_pred, desired_dir)}
        for guid, cal in WHEEL_CALIBRATION.items():
            axis_world = v_unit(q_rotate(frame.rot, cal["axis"]))
            s = v_dot(torque_req, axis_world)
            if s > self.deadband:
                controls.append((guid, "LeftKey"))
                debug[f"wheel_{guid[:4]}"] = 1.0
            elif s < -self.deadband:
                controls.append((guid, "RightKey"))
                debug[f"wheel_{guid[:4]}"] = -1.0
            else:
                debug[f"wheel_{guid[:4]}"] = 0.0
        return controls, debug


class ThrottlePwm:
    """Bresenham duty-cycle accumulator: fires the engines on a fraction of
    10 Hz ticks so the fixed-thrust boosters approximate a throttle."""

    def __init__(self):
        self.accumulator = 0.0

    def fire(self, *, duty: float) -> bool:
        duty = max(0.0, min(1.0, duty))
        self.accumulator += duty
        if self.accumulator >= 1.0 - 1e-9:
            self.accumulator -= 1.0
            return True
        return False


def discover_booster_power_sliders(*, env: ControllerClient) -> list:
    """Return the live ``fthrust`` slider on every discovered Booster."""
    wanted = {guid.lower() for guid in ENGINE_GUIDS}
    found: list = []
    published: list[str] = []
    for slider in env.sliders:
        if slider.block_guid.lower() not in wanted:
            continue
        published.append(
            f"{slider.block_guid} {slider.name} "
            f"[{slider.minimum},{slider.maximum}] default={slider.default}"
        )
        if slider.name == BOOSTER_POWER_SLIDER:
            found.append(slider)
    if len(found) != len(ENGINE_GUIDS):
        raise RuntimeError(
            f"Each Booster must publish slider {BOOSTER_POWER_SLIDER!r}; "
            f"found {len(found)} for {len(ENGINE_GUIDS)} engines. "
            f"Published booster sliders: {published or '(none)'}."
        )
    return found


def discover_wheel_speed_sliders(*, env: ControllerClient) -> list:
    """Return the live ``speed`` slider on every discovered reaction wheel."""
    wanted = {guid.lower() for guid in WHEEL_GUIDS}
    found: list = []
    published: list[str] = []
    for slider in env.sliders:
        if slider.block_guid.lower() not in wanted:
            continue
        published.append(
            f"{slider.block_guid} {slider.name} "
            f"[{slider.minimum},{slider.maximum}] default={slider.default}"
        )
        if slider.name == WHEEL_SPEED_SLIDER:
            found.append(slider)
    if len(found) != len(WHEEL_GUIDS):
        raise RuntimeError(
            f"Each Reaction Steering Block must publish slider {WHEEL_SPEED_SLIDER!r}; "
            f"found {len(found)} for {len(WHEEL_GUIDS)} wheels. "
            f"Published wheel sliders: {published or '(none)'}."
        )
    return found


def pin_operating_sliders(
    *,
    env: ControllerClient,
    assignments: list[tuple[object, float]],
) -> None:
    """Pin sliders in one BAA4 snapshot. Later key sends keep this snapshot."""
    if not assignments:
        raise RuntimeError("pin_operating_sliders requires at least one slider.")
    records: list[tuple[str, str, float]] = []
    for slider, value in assignments:
        value = float(value)
        if value < slider.minimum or value > slider.maximum:
            raise RuntimeError(
                f"Pin {slider.name}={value} on {slider.block_guid} is outside "
                f"[{slider.minimum},{slider.maximum}]."
            )
        records.append((slider.block_guid, slider.name, value))
        print(
            f"Pinned {slider.name}={value} on {slider.block_guid} "
            f"(range [{slider.minimum},{slider.maximum}] default={slider.default})",
            flush=True,
        )
    env.send_sliders(sliders=records)


# ---------------------------------------------------------------------------
# Mission sequencer
# ---------------------------------------------------------------------------

@dataclass
class MissionConfig:
    # 100 (not 120): a lower orbit has a shorter period, and the powered
    # lap maintenance cost scales with lap time in this draggy atmosphere.
    target_altitude: float = 100.0
    # -- guided ascent (velocity-vector steering; replaces the old
    #    altitude-scheduled pitch program that attempt 20260821-111436
    #    could not fly with alpha~=0.1 rad/s^2 wheels) --
    # vr_target = clamp(gain*(r_t - r), vr_min, vr_max); vr_min is NEGATIVE so
    # the guidance commands descent after overshooting the target radius
    # (attempt 20260821-111923 escaped because the old lower clamp of +5
    # commanded a perpetual climb).
    ascent_vr_gain: float = 0.25
    ascent_vr_min: float = -8.0
    ascent_vr_max: float = 12.0
    # strong vr feedback: the equilibrium climb-rate offset caused by the
    # gravity-compensation term is g/kp (~9 m/s at kp=2.5); at the old 0.8 it
    # was 27 m/s, which blew straight through the target altitude.
    ascent_vr_kp: float = 2.5
    # hard tangential gain: drag at ~30 deg AoA is 5-9 u/s^2 (measured
    # 20260821-112609). A P-controller equilibrates at deficit = drag/kp, so
    # kp must be large enough that the residual v_tan deficit (~1 m/s at
    # kp=4) still puts the periapsis above ground (kp=2 stalled 5 m/s short,
    # attempt 20260821-113453).
    ascent_vt_kp: float = 4.0
    # prograde drag feedforward: cancels the P-controller steady-state error
    # so v_tan can actually reach (slightly exceed) the circular speed and
    # lift the periapsis to the insertion threshold.
    ascent_drag_ff: float = 10.0
    ascent_vr_authority: float = 25.0     # |a_rad| cap of the vr feedback term
    ascent_slew_deg_s: float = 10.0       # max rotation rate of the guidance dir
    # Above this tangential speed the aero weathervane torque overpowers the
    # wheels and the attitude is locked to ~velocity: switch to zero-AoA
    # throttle-only energy management (vector guidance demands there are
    # unexecutable and just buy drag; measured across attempts 121204/121313).
    throttle_mode_vtan: float = 60.0
    throttle_ff_duty: float = 0.30        # ~drag/thrust at ~100 m/s, AoA ~0
    # energy feedback: duty per 1000 u^2/s^2 of orbital-energy deficit
    # (E = v^2/2 - GM/r, target -GM/2r_t). Feeding back v_tan instead lets
    # climb soak the thrust as potential energy and diverge (attempt 121612).
    throttle_ke: float = 0.6
    throttle_kvr: float = 0.03            # duty cut per m/s of climb rate
    # NOTE (measured 20260821-114422): drag is ~C*v^2 with C~=0.0014 even at
    # zero AoA, so ANY ~100 m/s orbit inherently costs ~40% fuel per lap in
    # powered maintenance; do not waste ascent fuel chasing a high periapsis
    # (peri_frac 0.9 cost 24% extra fuel in the asymptotic tail), the lap
    # floor-burn keeps the orbit alive either way.
    orbit_peri_frac: float = 0.55         # periapsis >= surface + frac*target_alt
    orbit_apo_frac: float = 0.75          # and apoapsis >= surface + frac*target_alt
    orbit_sweep_deg: float = 360.0        # required swept angle before deorbit
    align_tol_deg: float = 25.0           # engine-gating attitude alignment
    burn_margin: float = 0.85             # usable fraction of a_max in stop-dist
    kill_burn_slow_speed: float = 12.0    # hand over to final descent below this
    final_touchdown_speed: float = 2.0    # target |v_rad| at ground contact
    final_descent_gain: float = 0.22      # v_des = -max(min_sink, gain*h_agl)
    final_min_sink: float = 1.5
    final_duty_kp: float = 0.10           # duty per (v_des - v_rad) unit
    tilt_gain: float = 0.06               # horizontal-kill tilt per unit v_hor
    tilt_max_deg: float = 10.0
    # Calibrated at game-default fthrust=1.0 (4 engines). Caps a_tan / kill-burn
    # shape and divides duty. Keep this at 36 whenever fthrust stays at default.
    thrust_accel: float = 36.0
    full_thrust_accel: float = 36.0
    # Wheels are weak (alpha ~= 0.1 rad/s^2 per axis): low kp so the demanded
    # rate stays reachable, heavy kd so the bang-bang brake starts early.
    att_kp: float = 0.55
    att_kd: float = 2.2
    att_deadband: float = 0.04
    att_lookahead: float = 0.35
    max_duration: float = 900.0


class MissionController:
    """Stage machine producing (engine duty, desired attitude direction)."""

    def __init__(self, *, config: MissionConfig):
        self.config = config
        self.stage = "pad"
        self.stage_started = 0.0
        self.events: list[dict] = []
        self.gm = 0.0
        self.surface_radius = 0.0
        self.ground_clearance = 0.0
        self.pad_radius = 0.0
        self.east: tuple | None = None     # world tilt direction chosen at pad
        self.plane_normal: tuple | None = None
        self.sweep_angle = 0.0
        self.prev_plane_angle: float | None = None
        self.done = False
        self.abort_reason = ""
        self.touchdown_t: float | None = None
        self.touchdown_speed: float | None = None
        self.settled_t: float | None = None
        self.max_altitude = 0.0
        self.initial_blocks = 0
        self.guidance_dir = (0.0, 1.0, 0.0)
        self.land_duty = 0.5
        self.prev_t: float | None = None
        self.last_dt = 0.1

    # -- orbital elements ---------------------------------------------------

    def update_gm(self, frame: Frame) -> None:
        if frame.gravity_available and frame.g_mag > 1e-6 and frame.radius > 1e-6:
            sample = frame.g_mag * frame.radius * frame.radius
            if self.gm == 0.0:
                self.gm = sample
            elif frame.g_mag < 21.9:
                # |g| clamps at 22.0 near the surface (measured 2026-08-21),
                # so GM = g*r^2 samples are only unbiased in the unclamped
                # region; keep the bootstrap value until we get there.
                self.gm = 0.9 * self.gm + 0.1 * sample

    def elements(self, frame: Frame) -> dict:
        gm = self.gm
        if gm <= 0.0:
            return {"apoapsis": 0.0, "periapsis": 0.0, "ecc": 1.0}
        h_vec = v_cross(frame.rel, frame.vel)
        h = v_norm(h_vec)
        energy = 0.5 * frame.speed * frame.speed - gm / max(frame.radius, 1e-6)
        ecc_sq = 1.0 + 2.0 * energy * h * h / (gm * gm)
        ecc = math.sqrt(max(0.0, ecc_sq))
        semi_latus = h * h / gm
        periapsis = semi_latus / (1.0 + ecc) if (1.0 + ecc) > 1e-9 else 0.0
        apoapsis = semi_latus / (1.0 - ecc) if ecc < 1.0 - 1e-9 else float("inf")
        return {"apoapsis": apoapsis, "periapsis": periapsis, "ecc": ecc, "h": h}

    def v_circular(self, radius: float) -> float:
        return math.sqrt(self.gm / max(radius, 1e-6)) if self.gm > 0.0 else 0.0

    # -- helpers -------------------------------------------------------------

    def transition(self, *, stage: str, frame: Frame, note: str = "") -> None:
        self.events.append(
            {
                "t": frame.t,
                "from": self.stage,
                "to": stage,
                "altitude": frame.radius - self.surface_radius,
                "v_tan": frame.v_tan,
                "v_rad": frame.v_rad,
                "fuel_fraction": frame.fuel_fraction,
                "note": note,
            }
        )
        print(f"[{frame.t:8.2f}s] stage {self.stage} -> {stage} "
              f"(alt={frame.radius - self.surface_radius:.1f}, v_tan={frame.v_tan:.1f}, "
              f"v_rad={frame.v_rad:.1f}, fuel={frame.fuel_fraction:.2f}) {note}",
              flush=True)
        self.stage = stage
        self.stage_started = frame.t

    def prograde_horizontal(self, frame: Frame) -> tuple:
        if frame.v_tan > 0.5:
            return v_unit(frame.v_tan_vec)
        # No tangential velocity yet: use the pad-chosen east direction
        # projected into the local horizontal plane.
        east = self.east if self.east is not None else (1.0, 0.0, 0.0)
        horizontal = v_sub(east, v_scale(frame.up_r, v_dot(east, frame.up_r)))
        return v_unit(horizontal)

    def update_sweep(self, frame: Frame) -> None:
        if self.plane_normal is None:
            return
        e1 = self.sweep_e1
        e2 = self.sweep_e2
        angle = math.atan2(v_dot(frame.rel, e2), v_dot(frame.rel, e1))
        if self.prev_plane_angle is not None:
            delta = angle - self.prev_plane_angle
            while delta > math.pi:
                delta -= 2.0 * math.pi
            while delta < -math.pi:
                delta += 2.0 * math.pi
            self.sweep_angle += delta
        self.prev_plane_angle = angle

    def h_agl(self, frame: Frame) -> float:
        return frame.radius - self.surface_radius - self.ground_clearance

    def dt_est(self, frame: Frame) -> float:
        if self.prev_t is not None and frame.t > self.prev_t:
            self.last_dt = min(0.5, frame.t - self.prev_t)
        return self.last_dt

    @staticmethod
    def slew_toward(*, current: tuple, target: tuple, max_step: float) -> tuple:
        """Rotate unit vector `current` toward `target` by at most max_step rad."""
        cos_angle = max(-1.0, min(1.0, v_dot(current, target)))
        angle = math.acos(cos_angle)
        if angle <= max_step or angle < 1e-6:
            return target
        axis = v_unit(v_cross(current, target))
        if v_norm(axis) < 1e-9:
            return target
        # Rodrigues rotation of `current` about `axis` by max_step
        cos_step = math.cos(max_step)
        sin_step = math.sin(max_step)
        term1 = v_scale(current, cos_step)
        term2 = v_scale(v_cross(axis, current), sin_step)
        term3 = v_scale(axis, v_dot(axis, current) * (1.0 - cos_step))
        return v_unit(v_add(v_add(term1, term2), term3))

    def guidance(self, *, frame: Frame, elements: dict, r_t: float) -> tuple[tuple, float]:
        """Velocity-vector guidance shared by guided_ascent and orbit_coast.

        Demanded accel = gravity compensation (fading with v_tan/v_circ and
        with apoapsis overshoot) + climb-rate tracking (authority-capped) +
        tangential deficit with drag feedforward. Attitude command is the
        demanded accel direction, slew-rate limited; duty is the projection
        of the demand onto the actual thrust axis.
        """
        config = self.config
        if frame.v_tan > config.throttle_mode_vtan:
            # Aero-locked regime: fly at zero AoA (attitude = velocity) and
            # manage energy with the throttle only. -kvr*v_rad: when climbing,
            # cut thrust and let gravity bend the path back; when sinking,
            # add energy.
            desired = v_unit(frame.vel) if frame.speed > 1.0 else frame.up_r
            self.guidance_dir = desired
            energy = 0.5 * frame.speed * frame.speed - self.gm / max(frame.radius, 1e-6)
            energy_target = -self.gm / (2.0 * r_t)
            duty = (config.throttle_ff_duty
                    + config.throttle_ke * (energy_target - energy) / 1000.0
                    - config.throttle_kvr * frame.v_rad)
            duty = max(0.0, min(1.0, duty))
            duty *= max(0.0, v_dot(frame.up_axis, desired))
            return desired, duty
        east = self.prograde_horizontal(frame)
        v_circ_local = self.v_circular(frame.radius)
        v_circ_target = self.v_circular(r_t)
        frac = min(1.0, frame.v_tan / max(v_circ_local, 1e-6))
        vr_target = min(config.ascent_vr_max,
                        max(config.ascent_vr_min, config.ascent_vr_gain * (r_t - frame.radius)))
        apo = elements["apoapsis"]
        if apo == float("inf"):
            gcomp = 0.0
        else:
            gcomp = max(0.0, min(1.0, (1.1 * r_t - apo) / max(0.1 * r_t, 1e-6)))
        # also fade gravity compensation when the climb rate overshoots:
        # with enough vertical momentum the thrust belongs to the tangent
        gcomp *= max(0.0, min(1.0, 1.0 - max(0.0, frame.v_rad - vr_target) / 15.0))
        vr_term = config.ascent_vr_kp * (vr_target - frame.v_rad)
        vr_term = max(-config.ascent_vr_authority, min(config.ascent_vr_authority, vr_term))
        a_rad = frame.g_mag * gcomp * (1.0 - frac * frac) + vr_term
        a_tan = config.ascent_drag_ff + config.ascent_vt_kp * (v_circ_target - frame.v_tan)
        a_rad = max(-15.0, a_rad)
        a_tan = max(0.0, min(config.thrust_accel, a_tan))
        accel_vec = v_add(v_scale(frame.up_r, a_rad), v_scale(east, a_tan))
        a_mag = v_norm(accel_vec)
        raw_dir = v_unit(accel_vec) if a_mag > 1e-6 else frame.up_r
        self.guidance_dir = self.slew_toward(
            current=self.guidance_dir, target=raw_dir,
            max_step=math.radians(config.ascent_slew_deg_s) * self.dt_est(frame))
        a_axis = v_dot(accel_vec, frame.up_axis)
        duty = max(0.0, min(1.0, a_axis / config.full_thrust_accel))
        return self.guidance_dir, duty

    # -- the sequencer -------------------------------------------------------

    def decide(self, *, frame: Frame) -> dict:
        """Returns {"duty": float, "desired_dir": tuple, ...debug fields}."""
        config = self.config
        self.update_gm(frame)
        if self.surface_radius:
            self.max_altitude = max(
                self.max_altitude, frame.radius - self.surface_radius
            )
        elements = self.elements(frame)
        r_t = self.surface_radius + config.target_altitude if self.surface_radius else 0.0
        duty = 0.0
        desired = frame.up_r

        if self.stage == "pad":
            if frame.t >= 0.4 and frame.gravity_available:
                self.surface_radius = frame.surface_radius
                self.pad_radius = frame.radius
                self.ground_clearance = frame.radius - frame.surface_radius
                self.initial_blocks = frame.block_count
                # Tilt in the plane controllable by wheel B (machine +x at
                # pad, projected horizontal); world == machine frame at spawn.
                east_raw = q_rotate(frame.rot, (1.0, 0.0, 0.0))
                east = v_sub(east_raw, v_scale(frame.up_r, v_dot(east_raw, frame.up_r)))
                self.east = v_unit(east)
                self.guidance_dir = frame.up_r
                self.transition(
                    stage="guided_ascent",
                    frame=frame,
                    note=(f"GM={self.gm:.0f} surface_r={self.surface_radius:.1f} "
                          f"clearance={self.ground_clearance:.1f} g={frame.g_mag:.2f}"),
                )
            return {"duty": 0.0, "desired_dir": desired, **elements}

        if self.stage == "guided_ascent":
            desired, duty = self.guidance(frame=frame, elements=elements, r_t=r_t)
            apo = elements["apoapsis"]
            v_circ_local = self.v_circular(frame.radius)
            # Any ellipse whose periapsis clears the ground and whose apoapsis
            # reaches most of the target altitude counts as "in orbit" (the
            # task tolerates imprecise insertion); a full lap works on any
            # such ellipse.
            peri_ok = elements["periapsis"] >= self.surface_radius + config.orbit_peri_frac * config.target_altitude
            apo_ok = (
                apo != float("inf")
                and self.surface_radius + config.orbit_apo_frac * config.target_altitude
                <= apo <= self.surface_radius + 2.5 * config.target_altitude
            )
            circ_ok = frame.v_tan >= v_circ_local and abs(frame.v_rad) < 4.0
            # Powered-orbit criterion: in this atmosphere there is no ballistic
            # orbit (drag ~20 u/s^2 at v_circ), so "in orbit" = the energy
            # controller has settled at the target energy with a flat path.
            # Attempt 20260821-121748 flew 1.7 stable laps that the ballistic
            # peri/ecc criteria refused to count, burning the tank dry.
            energy = 0.5 * frame.speed * frame.speed - self.gm / max(frame.radius, 1e-6)
            energy_target = -self.gm / (2.0 * r_t)
            powered_ok = (
                frame.v_tan > config.throttle_mode_vtan
                and abs(energy - energy_target) < 1200.0
                and abs(frame.v_rad) < 6.0
            )
            if (peri_ok and apo_ok) or circ_ok or powered_ok:
                normal = v_unit(v_cross(frame.rel, frame.vel))
                self.plane_normal = normal
                self.sweep_e1 = v_unit(frame.rel)
                self.sweep_e2 = v_unit(v_cross(normal, self.sweep_e1))
                self.sweep_angle = 0.0
                self.prev_plane_angle = None
                self.transition(
                    stage="orbit_coast", frame=frame,
                    note=(f"ecc={elements['ecc']:.3f} "
                          f"peri={elements['periapsis'] - self.surface_radius:.1f} "
                          f"apo={elements['apoapsis'] - self.surface_radius:.1f}"),
                )

        elif self.stage == "orbit_coast":
            # Same guidance law as the ascent: it holds a stable powered
            # near-circular orbit against the heavy drag (thrusting along
            # raw velocity or coasting both ended in a ground bounce,
            # attempts 20260821-115742 / 20260821-120819).
            self.update_sweep(frame)
            swept = abs(math.degrees(self.sweep_angle))
            desired, duty = self.guidance(frame=frame, elements=elements, r_t=r_t)
            if swept >= config.orbit_sweep_deg:
                self.transition(stage="kill_burn", frame=frame, note=f"swept={swept:.0f}deg")

        elif self.stage == "kill_burn":
            # Hover-brake: hold altitude (gravity + sink damping, vertical)
            # while a tilt of AT MOST ~25 deg plus broadside drag grinds the
            # horizontal speed away. Attitude excursions beyond ~25 deg are
            # unflyable with alpha~0.1 wheels: aggressive retro demands spun
            # the craft and it fell 90 m with duty pinned at 0 (attempt
            # 20260821-122838).
            a_up = frame.g_mag + 2.0 * (0.0 - frame.v_rad)
            a_up = max(8.0, min(config.thrust_accel, a_up))
            a_hor = min(math.tan(math.radians(30.0)) * a_up,
                        math.sqrt(max(0.0, config.thrust_accel ** 2 - a_up ** 2)),
                        1.0 * frame.v_tan)
            retro_h = v_scale(v_unit(frame.v_tan_vec), -1.0) if frame.v_tan > 0.5 else (0.0, 0.0, 0.0)
            accel_vec = v_add(v_scale(frame.up_r, a_up), v_scale(retro_h, a_hor))
            desired = v_unit(accel_vec)
            duty = max(0.0, min(1.0, v_dot(accel_vec, frame.up_axis) / config.full_thrust_accel))
            if frame.v_tan < 8.0 or self.h_agl(frame) < 6.0:
                self.transition(stage="final_descent", frame=frame,
                                note=f"speed={frame.speed:.1f} h_agl={self.h_agl(frame):.1f}")

        elif self.stage == "final_descent":
            h_agl = self.h_agl(frame)
            v_hor_vec = frame.v_tan_vec
            v_hor = v_norm(v_hor_vec)
            # gentle tilt only while high enough; near the ground hold pure
            # vertical (the tilt chase at 12 m flip-flopped faster than the
            # wheels could follow and PUMPED v_hor 9->50, attempt
            # 20260821-122227)
            if h_agl > 8.0 and v_hor > 2.0:
                tilt = min(math.radians(config.tilt_max_deg), config.tilt_gain * v_hor)
                tilt_dir = v_scale(v_unit(v_hor_vec), -1.0)
                raw_desired = v_unit(v_add(v_scale(frame.up_r, math.cos(tilt)), v_scale(tilt_dir, math.sin(tilt))))
            else:
                raw_desired = frame.up_r
            self.guidance_dir = self.slew_toward(
                current=self.guidance_dir, target=raw_desired,
                max_step=math.radians(12.0) * self.dt_est(frame))
            desired = self.guidance_dir
            v_des = -max(config.final_min_sink, min(25.0, config.final_descent_gain * h_agl))
            # Adaptive (integrating) throttle: the craft is much lighter than
            # at launch, so the calibrated hover duty (g/36=0.61) actually
            # CLIMBS -- attempt 20260821-123220 hovered at 20 m until the tank
            # ran dry. The integrator finds the true hover duty by itself.
            err = v_des - frame.v_rad
            self.land_duty += 0.06 * err * self.dt_est(frame)
            self.land_duty = max(0.0, min(1.0, self.land_duty))
            duty = max(0.0, min(1.0, self.land_duty + 0.05 * err))
            if h_agl <= 0.6 and abs(frame.v_rad) <= config.final_touchdown_speed + 1.0:
                self.touchdown_t = frame.t
                self.touchdown_speed = frame.speed
                self.transition(stage="touchdown", frame=frame,
                                note=f"touchdown speed={frame.speed:.2f}")

        elif self.stage == "touchdown":
            duty = 0.0
            desired = frame.up_r
            # Engines are off. Confirm the vehicle remains settled for a
            # short window, then let the one-shot runner own the configured
            # post-completion recording hold.
            if frame.speed > 0.5:
                self.settled_t = None
            elif self.settled_t is None:
                self.settled_t = frame.t
            if self.settled_t is not None and frame.t - self.settled_t >= 0.5:
                self.done = True

        # global aborts
        if frame.t > config.max_duration:
            self.abort_reason = "max_duration"
            self.done = True
        if self.initial_blocks and frame.block_count < self.initial_blocks:
            self.abort_reason = f"crash_blocks_lost_{self.initial_blocks - frame.block_count}"
            self.done = True
        if self.surface_radius and frame.radius > self.surface_radius + 4.0 * config.target_altitude:
            self.abort_reason = "escaped_altitude_bound"
            self.done = True
        if (self.stage in ("orbit_coast", "kill_burn")
                and self.h_agl(frame) < 1.5 and frame.speed < 2.0):
            self.abort_reason = "unexpected_ground_contact"
            self.done = True
        if self.stage not in ("pad", "touchdown") and frame.fuel_fraction <= 0.005:
            self.abort_reason = "fuel_exhausted"
            self.done = True

        self.prev_t = frame.t
        return {
            "duty": duty,
            "desired_dir": desired,
            "apoapsis": elements["apoapsis"],
            "periapsis": elements["periapsis"],
            "ecc": elements["ecc"],
            "sweep_deg": math.degrees(self.sweep_angle),
            "gm": self.gm,
            "h_agl": self.h_agl(frame) if self.surface_radius else 0.0,
        }


# ---------------------------------------------------------------------------
# Calibration policy (open-loop pulses)
# ---------------------------------------------------------------------------

def _list_blocks(blocks) -> str:
    return ", ".join(
        f"{block.local_index}:{block.block_id}:{block.name}" for block in blocks
    )


def discover_hardware() -> None:
    """Bind root / engine / wheel GUIDs using the same names inspect-machine prints."""
    global ROOT_GUID, ENGINE_GUIDS, WHEEL_GUIDS
    bsg_value = os.environ.get(ENV_MACHINE_BSG, "")
    catalog_value = os.environ.get(ENV_CHANNEL_CATALOG, "")
    if bsg_value.strip() == "" or catalog_value.strip() == "":
        raise RuntimeError(
            f"{ENV_MACHINE_BSG} and {ENV_CHANNEL_CATALOG} must be set so this "
            "controller can discover GUIDs on the rebuilt machine."
        )
    from besiege_cli.machine import blocks_named, parse_bsg

    _, blocks, _ = parse_bsg(bsg_value, catalog_path=catalog_value)
    starting = [block for block in blocks_named(blocks, "Starting Block") if block.guid]
    boosters = [block for block in blocks_named(blocks, "Booster") if block.guid]
    wheels = [block for block in blocks_named(blocks, "Reaction Steering Block") if block.guid]
    inventory = _list_blocks(blocks)
    if len(starting) != 1:
        raise RuntimeError(
            f"Expected exactly one Starting Block with a GUID, found {len(starting)}. "
            f"Blocks: {inventory}"
        )
    if len(boosters) < 4:
        raise RuntimeError(
            f"Expected at least 4 Booster blocks, found {len(boosters)}. "
            f"Blocks: {inventory}"
        )
    if len(wheels) != 2:
        raise RuntimeError(
            f"Expected exactly 2 Reaction Steering Blocks, found {len(wheels)}. "
            f"Blocks: {inventory}"
        )
    ROOT_GUID = starting[0].guid
    ENGINE_GUIDS = tuple(block.guid for block in boosters)
    WHEEL_GUIDS = tuple(block.guid for block in wheels)
    print(
        f"Discovered hardware: root={ROOT_GUID} "
        f"engines={len(ENGINE_GUIDS)} wheels={len(WHEEL_GUIDS)}",
        flush=True,
    )


def bind_wheel_axes(*, env: ControllerClient, sample, root_record) -> None:
    """Classify the two wheels as A (+x) and B (+z) from the first telemetry frame."""
    global WHEEL_A_GUID, WHEEL_B_GUID, WHEEL_CALIBRATION
    root_pos = tuple(float(value) for value in root_record["position"])
    root_rot = tuple(float(value) for value in root_record["rotation"])
    wanted = {guid.lower() for guid in WHEEL_GUIDS}
    offsets: list[tuple[str, tuple[float, float, float]]] = []
    for record in sample.records:
        guid = env.guid_of(int(record["target_index"]))
        if guid.lower() not in wanted:
            continue
        pos = tuple(float(value) for value in record["position"])
        rel_machine = q_rotate(q_conj(root_rot), v_sub(pos, root_pos))
        offsets.append((guid, rel_machine))
        print(
            f"  wheel {guid} machine-frame offset="
            f"({rel_machine[0]:+.2f}, {rel_machine[1]:+.2f}, {rel_machine[2]:+.2f})",
            flush=True,
        )
    if len(offsets) != 2:
        raise RuntimeError(
            f"First BAT4 sample is missing Reaction Steering Blocks; found {len(offsets)}."
        )
    first, second = offsets
    if abs(first[1][0]) >= abs(second[1][0]):
        wheel_a, wheel_b = first[0], second[0]
    else:
        wheel_a, wheel_b = second[0], first[0]
    WHEEL_A_GUID = wheel_a
    WHEEL_B_GUID = wheel_b
    WHEEL_CALIBRATION = {
        WHEEL_A_GUID: {"axis": (-1.0, 0.0, 0.0)},
        WHEEL_B_GUID: {"axis": (0.0, 0.0, -1.0)},
    }
    print(f"Wheel A (pitch, -x)={WHEEL_A_GUID}", flush=True)
    print(f"Wheel B (yaw, -z)={WHEEL_B_GUID}", flush=True)


class CalibratePolicy:
    """Scripted pulses: boost airborne first, then pulse each wheel in free
    fall so ground contact does not constrain the measured torque axes
    (session 20260821-110015 showed the on-pad wheel-B pulses were fully
    contaminated by the landing legs). The rocket is sacrificed at impact;
    that is fine for a calibration episode.

    Windows (also used by analyze_calibration):
        1.0-3.5   engines full (specific thrust + fuel burn + g profile)
        4.5-5.5   wheel A LeftKey   (free fall)
        7.0-8.0   wheel B LeftKey   (free fall)
    """

    ENGINE_WINDOW = (1.0, 3.5)
    WHEEL_A_WINDOW = (4.5, 5.5)
    WHEEL_B_WINDOW = (7.0, 8.0)

    def __init__(self):
        self.duration = 9.5

    def decide(self, *, frame: Frame) -> list[tuple[str, str]]:
        schedule = (
            (*self.ENGINE_WINDOW, [(guid, "ThrustKey") for guid in ENGINE_GUIDS]),
            (*self.WHEEL_A_WINDOW, [(WHEEL_A_GUID, "LeftKey")]),
            (*self.WHEEL_B_WINDOW, [(WHEEL_B_GUID, "LeftKey")]),
        )
        controls: list[tuple[str, str]] = []
        for start, end, channels in schedule:
            if start <= frame.t < end:
                controls.extend(channels)
        return controls


# ---------------------------------------------------------------------------
# Episode loop
# ---------------------------------------------------------------------------

def run_episode(
    *,
    env: ControllerClient,
    mission: MissionController | None,
    calibrate: CalibratePolicy | None,
    attitude: AttitudeController,
    duration: float,
    timeout: float,
    steps_path: Path,
) -> list[dict]:
    env.arm()
    sample = env.wait_until_running(timeout=timeout)
    if env._block_guids is None:
        env.load_block_table()
    print(f"sliders={len(env.sliders)}", flush=True)
    for slider in env.sliders:
        print(
            f"  slider {slider.block_guid} {slider.name} "
            f"[{slider.minimum},{slider.maximum}] default={slider.default}",
            flush=True,
        )

    power_sliders = discover_booster_power_sliders(env=env)
    wheel_sliders = discover_wheel_speed_sliders(env=env)
    pin_operating_sliders(
        env=env,
        assignments=[
            *((slider, slider.maximum) for slider in power_sliders),
            *((slider, slider.default) for slider in wheel_sliders),
        ],
    )
    print(
        "Guidance model unchanged: thrust_accel="
        f"{mission.config.thrust_accel if mission is not None else 36.0} "
        f"full_thrust_accel="
        f"{mission.config.full_thrust_accel if mission is not None else 36.0}",
        flush=True,
    )

    throttle = ThrottlePwm()
    steps: list[dict] = []
    last_report = 0.0

    # Stream each step to disk immediately so a crash/interrupt (e.g. the
    # 2026-08-21 mission attempt that was killed mid-flight) never loses data.
    with steps_path.open("w", encoding="utf-8") as steps_handle:
        while True:
            root_record = None
            for record in sample.records:
                target_guid = env.guid_of(int(record["target_index"]))
                if target_guid.lower() == ROOT_GUID.lower():
                    root_record = record
                    break
            if root_record is None:
                raise ValueError(
                    f"BAT4 sample does not include root {ROOT_GUID}."
                )
            if not WHEEL_CALIBRATION:
                bind_wheel_axes(env=env, sample=sample, root_record=root_record)
            frame = Frame.from_telemetry(sample, root_record)

            if calibrate is not None:
                controls = calibrate.decide(frame=frame)
                debug = {}
                decision = {"duty": 0.0}
                fired = any(channel == "ThrustKey" for _guid, channel in controls)
                if frame.t >= calibrate.duration:
                    env.send_channels(channels=[])
                    break
            else:
                assert mission is not None
                decision = mission.decide(frame=frame)
                controls, debug = attitude.commands(
                    frame=frame, desired_dir=decision["desired_dir"]
                )
                fired = throttle.fire(duty=decision["duty"])
                if fired:
                    controls = controls + [
                        (guid, "ThrustKey") for guid in ENGINE_GUIDS
                    ]
                if mission.done:
                    env.send_channels(channels=[])
                    break

            env.send_channels(channels=controls)
            row = {
                "t": frame.t,
                "stage": mission.stage if mission is not None else "calibrate",
                "pos": frame.pos,
                "vel": frame.vel,
                "rot": frame.rot,
                "ang_vel": frame.ang_vel,
                "gravity_acc": frame.gravity_acc,
                "radius": frame.radius,
                "altitude": frame.radius - frame.surface_radius,
                "v_rad": frame.v_rad,
                "v_tan": frame.v_tan,
                "speed": frame.speed,
                "g_mag": frame.g_mag,
                "surface_radius": frame.surface_radius,
                "fuel_fraction": frame.fuel_fraction,
                "block_count": frame.block_count,
                "machine_integrity": frame.machine_integrity,
                "duty": decision.get("duty", 0.0),
                "engines_fired": fired,
                "controls": [f"{guid[:8]}:{channel}" for guid, channel in controls],
                **{key: value for key, value in decision.items()
                   if key in ("apoapsis", "periapsis", "ecc", "sweep_deg", "gm", "h_agl")},
                **debug,
            }
            steps.append(row)
            steps_handle.write(json.dumps(row) + "\n")
            steps_handle.flush()
            if frame.t - last_report >= 5.0:
                last_report = frame.t
                stage = mission.stage if mission is not None else "calibrate"
                print(
                    f"  t={frame.t:7.1f}s stage={stage:13s} "
                    f"alt={frame.radius - frame.surface_radius:8.1f} "
                    f"v_tan={frame.v_tan:6.1f} v_rad={frame.v_rad:6.1f} "
                    f"fuel={frame.fuel_fraction:.2f} "
                    f"att={debug.get('att_err', 0):.2f} "
                    f"align={debug.get('align', 0):.2f} "
                    f"integrity={frame.machine_integrity:.2f}",
                    flush=True,
                )
            if frame.t >= duration:
                print("Duration limit reached; stopping.")
                break
            sample = env.next_sample(timeout=2.0)

    return steps

# ---------------------------------------------------------------------------
# Calibration analysis
# ---------------------------------------------------------------------------

def analyze_calibration(*, steps: list[dict]) -> dict:
    def window(t0: float, t1: float) -> list[dict]:
        return [row for row in steps if t0 <= row["t"] < t1]

    result: dict = {}
    for label, (t0, t1) in (
        ("wheel_a_left", CalibratePolicy.WHEEL_A_WINDOW),
        ("wheel_b_left", CalibratePolicy.WHEEL_B_WINDOW),
    ):
        rows = window(t0 + 0.3, t1)
        if len(rows) < 3:
            result[label] = {"error": "insufficient samples"}
            continue
        first, last = rows[0], rows[-1]
        delta_omega_world = v_sub(last["ang_vel"], first["ang_vel"])
        # express in machine frame using the rotation at the window center
        mid = rows[len(rows) // 2]
        delta_machine = q_rotate(q_conj(mid["rot"]), delta_omega_world)
        dt = last["t"] - first["t"]
        result[label] = {
            "delta_omega_machine": [round(value, 4) for value in delta_machine],
            "alpha_machine": [round(value / dt, 4) for value in delta_machine],
            "axis_unit": [round(value, 3) for value in v_unit(delta_machine)],
            "dt": round(dt, 2),
        }
    engine_t0, engine_t1 = CalibratePolicy.ENGINE_WINDOW
    rows = window(engine_t0 + 0.4, engine_t1 - 0.1)
    if len(rows) >= 3:
        first, last = rows[0], rows[-1]
        dt = last["t"] - first["t"]
        dv = v_sub(last["vel"], first["vel"])
        g_avg = v_scale(v_add(first["gravity_acc"], last["gravity_acc"]), 0.5)
        thrust_acc = v_sub(v_scale(dv, 1.0 / dt), g_avg)
        result["engines"] = {
            "thrust_accel_world": [round(value, 3) for value in thrust_acc],
            "thrust_accel_mag": round(v_norm(thrust_acc), 3),
            "fuel_burn_per_s": round((first["fuel_fraction"] - last["fuel_fraction"]) / dt, 5),
            "dt": round(dt, 2),
        }
    gravity_samples = [
        {"r": round(row["radius"], 2), "g": round(row["g_mag"], 4),
         "gm": round(row["g_mag"] * row["radius"] ** 2, 1)}
        for row in steps[:: max(1, len(steps) // 25)] if row["g_mag"] > 1e-6
    ]
    result["gravity_profile"] = gravity_samples
    return result


# ---------------------------------------------------------------------------
# Managed controller entry point
# ---------------------------------------------------------------------------

def main() -> int:
    run_dir_value = os.environ.get(ENV_RUN_DIR)
    if not run_dir_value:
        raise RuntimeError(
            "This controller must run under `python -m besiege_cli run`; "
            f"{ENV_RUN_DIR} is not set."
        )
    run_dir = Path(run_dir_value)
    policy = os.environ.get("HEAVY_LAUNCHER_POLICY", "mission")
    if policy not in {"mission", "calibrate"}:
        raise ValueError(
            "HEAVY_LAUNCHER_POLICY must be 'mission' or 'calibrate'."
        )

    discover_hardware()
    config = MissionConfig()
    attitude = AttitudeController(
        kp=config.att_kp,
        kd=config.att_kd,
        deadband=config.att_deadband,
        lookahead=config.att_lookahead,
    )
    mission = MissionController(config=config) if policy == "mission" else None
    calibrate = CalibratePolicy() if policy == "calibrate" else None
    duration = config.max_duration if mission is not None else calibrate.duration + 2.0
    client = ControllerClient.from_environment(poll_interval=0.01)

    try:
        steps = run_episode(
            env=client,
            mission=mission,
            calibrate=calibrate,
            attitude=attitude,
            duration=duration,
            timeout=90.0,
            steps_path=run_dir / "steps.jsonl",
        )
    finally:
        client.close()

    summary: dict = {
        "schema": "buildarena.launcher_demo_evidence.v1",
        "policy": policy,
        "config": {key: getattr(config, key) for key in vars(config)},
        "run_id": client.run_id,
        "telemetry": "BuildArena ToolKit v4 BAT4 full profile at 10 Hz",
        "environment_model": {
            "gravity_center": LONE_ORB_CENTER,
            "surface_radius": LONE_ORB_SURFACE_RADIUS,
            "gm": LONE_ORB_GM,
            "max_gravity": LONE_ORB_MAX_GRAVITY,
        },
        "hardware": {
            "root_guid": ROOT_GUID,
            "engine_guids": list(ENGINE_GUIDS),
            "wheel_a_guid": WHEEL_A_GUID,
            "wheel_b_guid": WHEEL_B_GUID,
        },
        "steps": len(steps),
    }
    if mission is not None:
        summary.update(
            {
                "events": mission.events,
                "final_stage": mission.stage,
                "done": mission.done,
                "abort_reason": mission.abort_reason,
                "gm_estimate": mission.gm,
                "surface_radius": mission.surface_radius,
                "max_altitude": mission.max_altitude,
                "sweep_deg": math.degrees(mission.sweep_angle),
                "touchdown_t": mission.touchdown_t,
                "touchdown_speed": mission.touchdown_speed,
                "settled_t": mission.settled_t,
                "final_fuel_fraction": steps[-1]["fuel_fraction"] if steps else None,
                "final_block_count": steps[-1]["block_count"] if steps else None,
                "final_machine_integrity": (
                    steps[-1]["machine_integrity"] if steps else None
                ),
            }
        )
    else:
        assert calibrate is not None
        summary["calibration"] = analyze_calibration(steps=steps)

    (run_dir / "mission_summary.json").write_text(
        json.dumps(summary, indent=1), encoding="utf-8"
    )
    printable = {
        key: value for key, value in summary.items() if key not in ("config", "events")
    }
    print(json.dumps(printable, indent=1, default=str), flush=True)

    require_success = os.environ.get("HEAVY_LAUNCHER_REQUIRE_SUCCESS", "").strip() == "1"
    if mission is not None and mission.stage == "pad":
        raise RuntimeError(
            "Mission never left the pad; ignition / guidance did not start."
        )
    if require_success and mission is not None and (mission.abort_reason or not mission.done):
        raise RuntimeError(
            f"Mission did not complete successfully: stage={mission.stage}, "
            f"abort_reason={mission.abort_reason!r}."
        )
    if mission is not None and (mission.abort_reason or not mission.done):
        print(
            f"Mission ended without a completed landing: stage={mission.stage}, "
            f"abort_reason={mission.abort_reason!r}. "
            "Setup still accepts this as a live closed-loop demo.",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
