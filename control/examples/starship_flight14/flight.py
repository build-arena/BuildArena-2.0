"""Starship Flight 14 flight software: stack ascent and hot staging, then B21 and S41 fly at once."""
from __future__ import annotations

import math

import numpy as np

from actuators import Command, EngineSet, Flywheels, Thrusters, fire_bolts, pd_torque
from attitude import unit
from booster import INNER5, BoosterReturn
from physics import CENTER, R_SURFACE, gravity, local_frame
from ship import DRAG_MIN, ShipMission
from vehicle import Body

EAST = np.array([1.0, 0.0, 0.0])


class Events:
    def __init__(self):
        self.items = []

    def add(self, t, key, text, real=None, **data):
        rec = dict(t=round(float(t), 2), key=key, text=text, real=real)
        rec.update({k: (float(v) if isinstance(v, (int, float, np.floating)) else v) for k, v in data.items()})
        self.items.append(rec)
        print(f"T+{t:7.2f}s  {text}" + (f"  [real {real}]" if real else ""), flush=True)


# --------------------------------------------------------------------------- stack (booster engines)
class StackAscent:
    def __init__(self, f):
        self.f = f; self.integ = np.zeros(3); self.t_ign = None; self.log = {}

    def step(self, t, dt, st, cmd):
        f, c = self.f, self.f.cfg
        up = st["up"]; g = gravity(st["r"])
        tt = t - self.t_ign
        h0 = st["h"] - f.pad_h
        if tt < 1.0:
            twr = 0.6 + 0.7 * tt
        elif h0 < c["maxq_h"][0]:
            twr = c["twr_liftoff"]
        elif h0 < c["maxq_h"][1]:
            if "maxq" not in self.log:
                self.log["maxq"] = t
                f.ev.add(t, "maxq", "Max Q: throttle bucket", real="T+00:00:58")
            twr = c["twr_maxq"]
        else:
            twr = c["twr_climb"]
        if "engine_out" not in self.log and tt >= c["t_booster_engine_out"]:
            self.log["engine_out"] = t
            f.b_eng.cap[f.b_eng.cells.index((0, 0))] = 2.0 / 3.0
            f.ev.add(t, "booster_engine_out", "B21 center Raptor shuts down on ascent (1 of 33; center block at 2/3)",
                     real="ascent anomaly")
        frac = min(1.0, max(0.0, (h0 - c["pitch_start_h"]) / (c["meco_h"] - c["pitch_start_h"])))
        theta = math.radians(c["pitch_max_deg"]) * frac ** c["pitch_exp"]
        east = unit(EAST - (EAST @ up) * up)
        desired = math.cos(theta) * up + math.sin(theta) * east
        tau, err = pd_torque(st, f.stack, desired, wn=c["stack_wn"], zeta=0.9)
        eb = st["R"].inv().apply(np.cross(st["axis"], desired))
        if tt > 1.5:
            self.integ = np.clip(self.integ + eb * dt * f.stack.inertia * 0.08, -600, 600)
        tau = tau + self.integ
        v_ax = float(st["vel"] @ st["axis"])
        f.b_eng.allocate(f.stack.com_m, twr * f.stack.mass * g, tau[0], tau[2], v_ax)
        return err, h0


# --------------------------------------------------------------------------- flight
class Flight:
    def __init__(self, hw, cfg: dict):
        self.hw, self.cfg = hw, cfg
        self.ev = Events()
        self.stack = Body(hw, list(hw.info), hw.booster_ref, "stack")      # rigid B21 carries the engines
        self.ship = Body(hw, [g for g in hw.ship], hw.root, "ship")
        self.booster = Body(hw, [g for g in hw.booster], hw.booster_ref, "booster")
        self.b_eng = EngineSet(hw, hw.booster_engines, "B21")
        self.s_eng = EngineSet(hw, {**hw.ship_sl, **hw.ship_rvac}, "S41")
        self.s_wheels = Flywheels(hw.wheels)
        self.b_wheels = Flywheels(hw.booster_wheels)
        self.s_rcs = Thrusters(hw, hw.ship_rcs, self.ship, max_fthrust=cfg["rcs_max_fthrust"])
        self.b_rcs = Thrusters(hw, hw.booster_rcs, self.booster, max_fthrust=cfg["rcs_max_fthrust"])
        self.ascent = StackAscent(self)
        self.booster_gnc = BoosterReturn(self)
        self.ship_gnc = ShipMission(self)
        self.phase = "pad"; self.t_phase = 0.0
        self.pad = None; self.last_t = None; self.done = False
        self.log = {}

    def trim_ballast(self, cmd):
        for g, m in self.hw.ballast.items():
            cmd.slider(g, "mass", m)

    def _surface_point(self, ref, direction, dist):
        up, r, _ = local_frame(ref)
        east = unit(direction - (direction @ up) * up)
        ang = dist / R_SURFACE
        return CENTER + (R_SURFACE + (r - R_SURFACE)) * (math.cos(ang) * up + math.sin(ang) * east)

    def step(self, t, d) -> Command:
        dt = 0.04 if self.last_t is None else max(1e-3, min(0.2, t - self.last_t))
        self.last_t = t
        cmd = Command()
        self.trim_ballast(cmd)
        self.s_wheels.idle(cmd); self.b_wheels.idle(cmd)
        self.s_rcs.idle(cmd); self.b_rcs.idle(cmd)
        for name, g in self.hw.drag.items():
            cmd.slider(g, "magnitude", self.ship_gnc.drag_cmd.get(name, DRAG_MIN))
        c = self.cfg
        if self.phase in ("pad", "ascent", "hotstage"):
            self.stack.update_mass(d)
            st = self.stack.state(d, live=True)
            if self.pad is None:
                self.pad = st["pos"].copy(); self.pad_h = st["h"]
                self.b_exit_y = min(self.hw.center(g)[1] for g in self.hw.booster_engines.values()) - 0.55
                self.s_exit_y = min(self.hw.center(g)[1] for g in {**self.hw.ship_sl, **self.hw.ship_rvac}.values()) - 0.55
                self.ground_h = st["h"] - (self.stack.com_m[1] - self.b_exit_y)
                ground = self.pad - (self.stack.com_m[1] - self.b_exit_y) * st["up"]
                self.lz_booster = self._surface_point(ground, EAST, c["booster_lz_downrange"])
                self.ev.add(t, "pad", f"On Pad 2: stack mass {self.stack.mass:.1f}, {len(self.hw.info)} blocks")
            if self.phase == "pad":
                self.b_eng.set_uniform(0.0)
                if t >= c["t_ignition"]:
                    self.phase = "ascent"; self.ascent.t_ign = t
                    self.ev.add(t, "liftoff", "Super Heavy B21 lights all Raptor clusters: liftoff", real="T+00:00:00")
            elif self.phase == "ascent":
                err, h0 = self.ascent.step(t, dt, st, cmd)
                self.log["stack"] = dict(h=round(h0, 1), vr=round(st["vr"], 1), vt=round(st["vt"], 1), err=round(err, 1),
                                         m=round(self.stack.mass, 1))
                if err > 60:
                    self.log.setdefault("bad_since", t)
                    if t - self.log["bad_since"] > 2.0:
                        self.ev.add(t, "abort", f"ABORT: stack attitude error {err:.0f} deg"); self.done = True
                else:
                    self.log.pop("bad_since", None)
                if h0 >= c["meco_h"]:
                    self.phase = "hotstage"; self.t_phase = t
                    self.ev.add(t, "meco", f"MECO at h={st['h']:.0f}, speed {st['speed']:.1f}", real="T+00:02:20",
                                h=st["h"], vr=st["vr"], vt=st["vt"])
            else:
                self._hotstage(t, d, cmd)
        else:
            self.log["booster"] = self.booster_gnc.step(t, dt, d, cmd)
            self.log["ship"] = self.ship_gnc.step(t, dt, d, cmd)
            if "sep_confirmed" not in self.log and self.hw.root in d and self.hw.booster_ref in d:
                gap = float(np.linalg.norm(self.booster.state(d)["pos"] - self.ship.state(d)["pos"]))
                if gap > self.sep_gap0 + c["sep_confirm_gap"]:
                    self.log["sep_confirmed"] = t
                    self.ev.add(t, "sep_confirmed", f"Stage separation confirmed ({gap - self.sep_gap0:.1f} clearance)")
        self.b_eng.emit(cmd); self.s_eng.emit(cmd)
        return cmd

    def _hotstage(self, t, d, cmd):
        c = self.cfg
        tt = t - self.t_phase
        inner = np.array([cc in INNER5 for cc in self.b_eng.cells])
        self.b_eng.set_uniform(0.25 if tt < c["t_hot_release"] + 0.1 else 0.0, active=inner)
        if tt >= c["t_hot_ignite"]:
            if "hot_ignite" not in self.log:
                self.log["hot_ignite"] = t
                self.ev.add(t, "hot_staging", "Hot staging: S41 Raptors light while bolted to the vented ring", real="T+00:02:22")
            self.s_eng.set_uniform(min(1.2, 0.4 + 2.0 * (tt - c["t_hot_ignite"])))
        if tt >= c["t_hot_release"]:
            if "release" not in self.log:
                self.log["release"] = t
                self.ev.add(t, "separation", "Separation bolts fire: S41 leaves B21")
            if tt < c["t_hot_release"] + 0.25:
                fire_bolts(cmd, self.hw.clamps, c["bolt_power"])
            else:
                self.phase = "split"
                self.booster_gnc.set_phase(t, "sep_coast")
                self.ship_gnc.set_phase(t, "ascent")
                self.sep_gap0 = float(np.linalg.norm(self.booster.state(d)["pos"] - self.ship.state(d)["pos"]))
