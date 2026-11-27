"""Attitude errors and PD torque laws shared by the powered and coast phases."""
from __future__ import annotations

import math

import numpy as np


def unit(v):
    n = np.linalg.norm(v)
    return v / n if n > 1e-9 else v


def slew(current, target, max_step):
    """Rotate unit vector `current` toward `target` by at most max_step radians."""
    ang = math.acos(float(np.clip(current @ target, -1.0, 1.0)))
    if ang <= max_step or ang < 1e-6:
        return target
    ax = np.cross(current, target)
    if np.linalg.norm(ax) < 1e-9:
        return target
    ax = unit(ax)
    return unit(current * math.cos(max_step) + np.cross(ax, current) * math.sin(max_step))


def attitude_error(R, axis_body, desired, roll_body=None, roll_desired=None):
    """Rotation vector (body frame) taking axis_body to desired (and optionally roll_body to roll_desired)."""
    u = R.apply(np.asarray(axis_body, float))
    d = np.asarray(desired, float); d = d / np.linalg.norm(d)
    c = float(np.clip(u @ d, -1.0, 1.0))
    ang = np.arccos(c)
    ax = np.cross(u, d)
    n = np.linalg.norm(ax)
    if n < 1e-6:
        ax = R.apply([1.0, 0.0, 0.0]) if c < 0 else np.zeros(3)
    else:
        ax = ax / n
    err_w = ang * ax
    if roll_body is not None and roll_desired is not None:
        rb = R.apply(np.asarray(roll_body, float))
        rd = np.asarray(roll_desired, float) - (np.asarray(roll_desired, float) @ d) * d
        rb = rb - (rb @ u) * u
        if np.linalg.norm(rd) > 1e-6 and np.linalg.norm(rb) > 1e-6:
            rd /= np.linalg.norm(rd); rb /= np.linalg.norm(rb)
            s = float(np.clip(np.cross(rb, rd) @ u, -1, 1)); cc = float(np.clip(rb @ rd, -1, 1))
            err_w = err_w + np.arctan2(s, cc) * u
    return R.inv().apply(err_w), float(np.degrees(ang))


def pd_attitude(state, body, desired, wn, zeta=1.0, axis_body=(0.0, 1.0, 0.0), roll_body=None, roll_desired=None,
                err_max=0.6, w_ff=None):
    """Inertia-scaled PD torque (body frame) for a full attitude target.

    The error is clipped to err_max, which caps the commanded turn rate at about wn*err_max/(2*zeta)."""
    err_b, ang = attitude_error(state["R"], axis_body, desired, roll_body, roll_desired)
    n = float(np.linalg.norm(err_b))
    if n > err_max:
        err_b = err_b * (err_max / n)
    w_b = state["w_body"] - (state["R"].inv().apply(w_ff) if w_ff is not None else 0.0)
    tau = body.inertia * (wn * wn * err_b - 2.0 * zeta * wn * w_b)
    return tau, ang
