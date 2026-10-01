"""Actuator models and command assembly (Booster engines, flywheels, RCS, clamps, dispensers)."""
from __future__ import annotations

import numpy as np
from scipy.optimize import lsq_linear

from physics import engine_force, power_for_force


class Command:
    """One control frame: key channels and slider values (sliders must be re-sent every frame)."""

    def __init__(self):
        self.keys: list[tuple[str, str]] = []
        self.sliders: dict[tuple[str, str], float] = {}

    def key(self, guid, name):
        self.keys.append((guid, name))

    def slider(self, guid, name, value):
        self.sliders[(guid, name)] = float(value)

    def slider_list(self):
        return [(g, n, v) for (g, n), v in self.sliders.items()]


class EngineSet:
    """Parallel engines thrusting along machine +Y; differential throttle provides pitch/yaw torque.

    Forces are allocated with a bounded least-squares fit to [total force, torque_x, torque_z] about the
    body COM, then converted to fthrust with the velocity-dependent engine model.
    """

    def __init__(self, hw, engines: dict, name: str):
        self.hw, self.name = hw, name
        self.cells = list(engines.keys())
        self.guids = [engines[c] for c in self.cells]
        self.pos = np.array([hw.center(g) for g in self.guids])
        self.cap = np.ones(len(self.guids))      # per-engine power fraction (engine-out modelling)
        self.enabled = np.ones(len(self.guids), bool)
        self.power = np.zeros(len(self.guids))
        self.tau_got = np.zeros(3)             # body torque of the last allocation (engine-model estimate)

    def allocate(self, com_m, force, tau_x, tau_z, v_axial, active=None, f_min_frac=0.05, p_max=2.0,
                 torque_priority=False, torque_weight=4.0):
        """Bounded least-squares fit of engine forces to [total force, torque_x, torque_z] about the COM.

        Torque rows are weighted (torque_weight per unit lever) so attitude wins when the engines
        saturate; the torque of an asymmetric engine set (engine out) is compensated automatically.
        A small pull toward an even split picks one solution among equivalent ones. Without
        torque_priority a zero force request switches the engines off.
        """
        act = self.enabled.copy() if active is None else (np.asarray(active, bool) & self.enabled)
        n = len(self.guids)
        idx = np.where(act)[0]
        if (force <= 0 and not torque_priority) or len(idx) == 0:
            self.power = np.zeros(n)
            return self.power
        fmax = np.array([engine_force(p_max * self.cap[i], v_axial) for i in range(n)])
        r = self.pos[idx] - com_m
        k = len(idx)
        A = np.vstack([-r[:, 2], r[:, 0]])                      # torque per unit force (machine x, z)
        fm = fmax[idx]
        lo = np.minimum(f_min_frac * fm, fm - 1e-6)
        even = np.clip(max(force, 0.0) / k * np.minimum(1.0, fm / max(fm.mean(), 1e-9)), lo, fm)
        eps = 0.05
        G = np.vstack([np.ones(k), torque_weight * A, eps * np.eye(k)])
        b = np.concatenate([[max(force, 0.0)], torque_weight * np.array([tau_x, tau_z]), eps * even])
        f = np.clip(lsq_linear(G, b, bounds=(lo, fm), method="bvls").x, lo, fm)
        out = np.zeros(n); out[idx] = f
        want = np.array([tau_x, tau_z])
        got = A @ f
        self.torque_scale = float(got @ want / max(want @ want, 1e-9)) if want @ want > 1e-9 else 1.0
        self.tau_got = np.array([got[0], 0.0, got[1]])
        self.power = np.array([power_for_force(out[i], v_axial, p_max * self.cap[i]) if act[i] else 0.0 for i in range(n)])
        return self.power

    def set_uniform(self, p, active=None):
        act = self.enabled.copy() if active is None else (np.asarray(active, bool) & self.enabled)
        self.power = np.where(act, p * self.cap, 0.0)
        self.tau_got = np.zeros(3)
        return self.power

    def emit(self, cmd: Command):
        for g, p in zip(self.guids, self.power):
            cmd.slider(g, "fthrust", max(0.0, min(2.0, float(p))))
            if p > 0.01:
                cmd.key(g, "ThrustKey")

    def torque_capacity(self, com_m, v_axial, p_mean):
        """Rough max differential torque about machine x/z with engines around p_mean."""
        r = self.pos[self.enabled] - com_m
        span = min(p_mean, 2.0 - p_mean)
        df = engine_force(max(span, 1e-3), v_axial) * 0.8
        return df * float(np.sum(np.abs(r[:, 0]))), df * float(np.sum(np.abs(r[:, 2])))


def pd_torque(state, body, desired_axis, wn, zeta, axis_body=(0.0, 1.0, 0.0), roll_damp=0.0, w_ff=None):
    """Body-frame torque pointing a body axis at desired_axis (world); inertia-scaled PD.

    w_ff (world angular velocity) is the reference's own turn rate, removing the ramp-tracking lag."""
    R = state["R"]
    u = R.apply(np.asarray(axis_body, float))
    d = np.asarray(desired_axis, float); d = d / np.linalg.norm(d)
    c = float(np.clip(u @ d, -1.0, 1.0))
    ang = np.arccos(c)
    ax = np.cross(u, d)
    nrm = np.linalg.norm(ax)
    if nrm < 1e-6:
        ax = R.apply([1.0, 0.0, 0.0]) if c < 0 else np.zeros(3)
    else:
        ax = ax / nrm
    err_world = ang * ax
    err_b = R.inv().apply(err_world)
    w_b = state["w_body"] - (R.inv().apply(w_ff) if w_ff is not None else 0.0)
    I = body.inertia
    tau = np.array([I[0] * (wn * wn * err_b[0] - 2 * zeta * wn * w_b[0]),
                    -roll_damp * I[1] * w_b[1],
                    I[2] * (wn * wn * err_b[2] - 2 * zeta * wn * w_b[2])])
    return tau, float(np.degrees(ang))


K_RCS = 3.5          # Steering Thruster force per unit fthrust, measured from B21's roll response in flight


class Thrusters:
    """Steering Thrusters (fuel-free RCS) used as proportional torque sources.

    LeftKey pushes along the block's local +Y; forces are a bounded least-squares fit of the
    requested body torque over the thrusters' torque axes (from their spawn pose and the COM).
    """

    def __init__(self, hw, guids: dict, body, max_fthrust=1.0):
        self.hw, self.guids, self.body = hw, guids, body
        self.max_fthrust = max_fthrust
        self.last = np.zeros(len(guids))        # last commanded force per thruster (signed, LeftKey +)

    def axes(self):
        com = self.body.com_m
        return np.array([np.cross(self.hw.center(g) - com, self.hw.saved[g].apply([0.0, 1.0, 0.0]))
                         for g in self.guids.values()])

    def emit(self, cmd: Command, tau_body, deadband=3.0, max_fthrust=None):
        fmax = K_RCS * (self.max_fthrust if max_fthrust is None else max_fthrust)
        A = self.axes().T
        f = lsq_linear(A, np.asarray(tau_body, float), bounds=(-fmax, fmax)).x
        self.last = f
        for g, fi in zip(self.guids.values(), f):
            if abs(fi) < deadband:
                cmd.slider(g, "fthrust", 0.0)
                continue
            cmd.slider(g, "fthrust", abs(fi) / K_RCS)
            cmd.key(g, "LeftKey" if fi > 0 else "RightKey")
        return A @ f

    def idle(self, cmd: Command):
        self.last = np.zeros(len(self.guids))
        for g in self.guids.values():
            cmd.slider(g, "fthrust", 0.0)


class Flywheels:
    """Reaction Steering Blocks used as bang-bang torque sources with automatic rate damping."""

    def __init__(self, guids: dict):
        self.guids = guids              # axis name -> guid ('x' or 'z' machine axis)

    def emit(self, cmd: Command, tau_body, scale, deadband=0.15, brake=0.6):
        for axis, g in self.guids.items():
            i = 0 if axis == "x" else 2
            demand = tau_body[i] / max(scale, 1e-6)
            if abs(demand) < deadband:
                cmd.slider(g, "speed", 0.5); cmd.slider(g, "damper", brake)
                continue
            s = float(np.clip(0.22 + 0.2 * abs(demand), 0.22, 0.42))
            cmd.slider(g, "speed", s); cmd.slider(g, "damper", 0.0)
            # LeftKey torque sign on the body was measured negative about the flywheel axis
            cmd.key(g, "RightKey" if demand > 0 else "LeftKey")

    def idle(self, cmd: Command, damper=0.6):
        for g in self.guids.values():
            cmd.slider(g, "speed", 0.5); cmd.slider(g, "damper", damper)


def fire_bolts(cmd: Command, bolts, power=1.0):
    """Explosive bolts (Decoupler): break their own mount joint and push the halves apart."""
    for g in bolts:
        cmd.slider(g, "epower", power)
        cmd.key(g, "ExplodeKey")
