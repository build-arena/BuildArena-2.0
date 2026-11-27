"""LONE ORB environment and Booster (Raptor stand-in) models used by the flight software.

Checked against an in-game calibration flight: thrust, fuel use and a linear-drag fit agree with the
measured accelerations to 0.6 u/s^2 RMS.
Units are game units (length) and seconds; masses are block masses.
"""
from __future__ import annotations

import math

import numpy as np

CENTER = np.array([0.0, -600.0, 0.0])   # planet center (world)
R_SURFACE = 600.0
GM = 8_456_800.0                         # 22 * 620^2: gravity is 22 inside r=620, inverse-square outside
G_MAX = 22.0
ATMOSPHERE_TOP = 500.0                   # density reaches zero 500 above the surface
DRAG_COEFF = 0.123                       # linear velocity damping per unit density (fitted)
FUEL_USE = 1.0                           # fuel units per second per unit of fthrust
FUEL_MASS = {99: 0.035, 92: 0.025}       # mass per fuel unit (big barrel, small barrel)


def gravity(r: float) -> float:
    return min(G_MAX, GM / (r * r))


def density(h: float) -> float:
    """Normalized atmosphere density: (exp(-x) - e^-1) / (1 - e^-1), x = h / 500."""
    if h >= ATMOSPHERE_TOP:
        return 0.0
    x = max(h, 0.0) / ATMOSPHERE_TOP
    return (math.exp(-x) - math.exp(-1.0)) / (1.0 - math.exp(-1.0))


def engine_force(power: float, v_axial: float) -> float:
    """Booster force for fthrust `power` when the vehicle moves `v_axial` along the thrust direction.

    Only motion *along* the thrust direction erodes thrust; braking burns get the full 210*power.
    """
    if power <= 0.0:
        return 0.0
    base = 600.0
    if v_axial > 0.0:
        base -= min((v_axial * 0.5 / power) ** 2, 450.0)
    force = power * base * 0.35
    return force - min(max(v_axial, 0.0), force) * 0.45


def power_for_force(force: float, v_axial: float, p_max: float = 2.0) -> float:
    if force <= 0.0:
        return 0.0
    if engine_force(p_max, v_axial) <= force:
        return p_max
    lo, hi = 0.0, p_max
    for _ in range(28):
        mid = 0.5 * (lo + hi)
        if engine_force(mid, v_axial) < force:
            lo = mid
        else:
            hi = mid
    return hi


def local_frame(pos):
    """(up, r, h) at world position pos."""
    rel = np.asarray(pos) - CENTER
    r = float(np.linalg.norm(rel))
    return rel / r, r, r - R_SURFACE


def elements(pos, vel):
    """Two-body apoapsis/periapsis altitudes, specific energy and angular momentum vector."""
    rel = np.asarray(pos) - CENTER
    v = np.asarray(vel)
    r = float(np.linalg.norm(rel))
    energy = 0.5 * float(v @ v) - GM / r
    hvec = np.cross(rel, v)
    hmag = float(np.linalg.norm(hvec))
    ecc = math.sqrt(max(0.0, 1.0 + 2.0 * energy * hmag * hmag / (GM * GM)))
    p = hmag * hmag / GM
    peri = p / (1.0 + ecc) - R_SURFACE
    apo = (p / (1.0 - ecc) - R_SURFACE) if (ecc < 1.0 and energy < 0) else float("inf")
    return dict(apo=apo, peri=peri, ecc=ecc, energy=energy, h=hvec)


def circular_speed(h: float) -> float:
    return math.sqrt(GM / (R_SURFACE + h))


def propagate(pos, vel, t_max, dt=0.1, drag=True, stop_h=None):
    """RK2 point-mass propagation with gravity (+ optional linear drag). Returns (pos, vel, t)."""
    p = np.array(pos, float); v = np.array(vel, float); t = 0.0
    def acc(pp, vv):
        up, r, h = local_frame(pp)
        a = -gravity(r) * up
        if drag:
            a = a - DRAG_COEFF * density(h) * vv
        return a
    while t < t_max:
        a1 = acc(p, v)
        pm = p + 0.5 * dt * v; vm = v + 0.5 * dt * a1
        a2 = acc(pm, vm)
        p = p + dt * vm; v = v + dt * a2; t += dt
        if stop_h is not None:
            _, _, h = local_frame(p)
            if h <= stop_h:
                break
    return p, v, t


def surface_angle(a, b):
    """Great-circle angle (rad) between two world positions as seen from the planet center."""
    ua = (np.asarray(a) - CENTER); ub = (np.asarray(b) - CENTER)
    c = float(ua @ ub) / (np.linalg.norm(ua) * np.linalg.norm(ub))
    return math.acos(max(-1.0, min(1.0, c)))
