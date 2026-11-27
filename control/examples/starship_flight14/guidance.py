"""Ground track and entry prediction for S41's deorbit, entry and landing.

The belly drag bowls are modelled with the flow speed V into the bowl: vy = V/4 and a force per unit
magnitude slider of rho^2 * vy * (0.6 + min(vy^2 / 2, 100)); during entry a scale factor on this model
is re-estimated from the measured deceleration.
The predictor flies a point mass belly-first (bowls square to the flow, so no lift) through gravity,
the linear air drag every block feels and the bowls, until it reaches the ground.
"""
from __future__ import annotations

import math

import numpy as np

from physics import CENTER, DRAG_COEFF, R_SURFACE, density, gravity, local_frame


def bowl_force(speed_normal: float, rho: float) -> float:
    """Drag Block force per unit magnitude for a flow component `speed_normal` into the bowl."""
    vy = 0.25 * max(speed_normal, 0.0)
    return rho * rho * vy * (0.6 + min(0.5 * vy * vy, 100.0))


def predict_entry(pos, vel, mass, magnitude_sum, k_bowl=1.0, ground_h=0.0, t_max=240.0, stop_fn=None,
                  terminal=None):
    """Belly-first flight to the ground (or until stop_fn(pos, vel) is true).

    terminal = (height, speed, magnitude): below that height or speed the bowls open to `magnitude`
    (the terminal-descent setting). Returns (final position, swept angle in degrees, time)."""
    p = np.array(pos, float); v = np.array(vel, float); t = 0.0; swept = 0.0
    state = {"m": magnitude_sum}

    def acc(pp, vv):
        up, r, h = local_frame(pp)
        a = -gravity(r) * up
        rho = density(h)
        if rho > 0.0:
            sp = float(np.linalg.norm(vv))
            a = a - DRAG_COEFF * rho * vv
            if sp > 1e-6:
                a = a - (k_bowl * state["m"] * bowl_force(sp, rho) / mass) * (vv / sp)
        return a

    while t < t_max:
        _, _, h = local_frame(p)
        if terminal is not None and (h - ground_h < terminal[0] or float(np.linalg.norm(v)) < terminal[1]):
            state["m"] = terminal[2]
        dt = 0.25 if h > 520.0 else 0.05
        a1 = acc(p, v)
        pm = p + 0.5 * dt * v; vm = v + 0.5 * dt * a1
        a2 = acc(pm, vm)
        p_new = p + dt * vm; v = v + dt * a2; t += dt
        r1 = p - CENTER; r2 = p_new - CENTER
        swept += math.degrees(math.atan2(float(np.linalg.norm(np.cross(r1, r2))), float(r1 @ r2)))
        p = p_new
        if local_frame(p)[2] <= ground_h or (stop_fn is not None and stop_fn(p, v)):
            break
    return p, swept, t


class LandingProfile:
    """Descent speed limit v_ref(h) for a landing burn that uses a fixed fraction of the deceleration the
    engines can give at each height (gravity grows toward the surface: 22 below r=620, GM/r^2 above).

    v_ref^2(h) = v_td^2 + 2 * integral_{gate}^{h} margin * (T/m - g) dh; below the gate v_ref = v_td."""

    def __init__(self, ground_h, gate_h, v_td, margin, h_max=1500.0, dh=1.0):
        self.ground_h, self.gate_h, self.v_td, self.margin = ground_h, gate_h, v_td, margin
        self.h = np.arange(0.0, h_max, dh)
        r = R_SURFACE + ground_h + self.h
        self.g = np.minimum(22.0, 8_456_800.0 / (r * r))
        self.v = np.full_like(self.h, v_td); self.a = np.zeros_like(self.h)

    def update(self, accel_max):
        """accel_max: thrust / mass available from the landing engines."""
        a = self.margin * np.maximum(accel_max - self.g, 0.3)
        above = self.h > self.gate_h
        inc = np.where(above, 2.0 * a * (self.h[1] - self.h[0]), 0.0)
        self.v = np.sqrt(self.v_td ** 2 + np.cumsum(inc))
        self.a = np.where(above, a, 0.0)

    def speed(self, h_agl):
        return float(np.interp(h_agl, self.h, self.v))

    def decel(self, h_agl):
        return float(np.interp(h_agl, self.h, self.a))

    def height_for(self, v):
        """Lowest height from which the profile can still stop a descent at speed v."""
        return float(np.interp(v, self.v, self.h))


class Track:
    """Angle travelled along the orbit plane, measured from the launch pad (unwrapped, degrees)."""

    def __init__(self, pad, pos, vel):
        self.p0 = (np.asarray(pad) - CENTER) / np.linalg.norm(np.asarray(pad) - CENTER)
        n = np.cross(np.asarray(pos) - CENTER, np.asarray(vel))
        self.n = n / np.linalg.norm(n)
        self.q0 = np.cross(self.n, self.p0)
        self.last = None; self.turns = 0

    def raw(self, pos):
        r = np.asarray(pos) - CENTER
        return math.degrees(math.atan2(float(r @ self.q0), float(r @ self.p0))) % 360.0

    def angle(self, pos):
        a = self.raw(pos)
        if self.last is not None and a < self.last - 180.0:
            self.turns += 1
        self.last = a
        return a + 360.0 * self.turns

    def point(self, angle_deg, height=0.0):
        a = math.radians(angle_deg)
        return CENTER + (R_SURFACE + height) * (math.cos(a) * self.p0 + math.sin(a) * self.q0)
