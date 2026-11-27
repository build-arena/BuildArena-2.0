"""Starship S41 after hot staging: ascent, orbit insertion, Starlink deploy, early deorbit,
belly-first entry, landing flip, landing burn and splashdown."""
from __future__ import annotations

import math

import numpy as np
from scipy.optimize import lsq_linear

from actuators import fire_bolts
from attitude import attitude_error, pd_attitude, slew, unit
from guidance import LandingProfile, Track, bowl_force, predict_entry
from physics import DRAG_COEFF, R_SURFACE, circular_speed, density, engine_force, gravity, local_frame, surface_angle

EAST = np.array([1.0, 0.0, 0.0])
DOOR = (0.0, 0.0, 1.0)         # Pez door side (machine +Z / North at build time)
BELLY = (0.0, 0.0, -1.0)       # heat shield and drag bowls (machine -Z / South)
DRAG_MIN, DRAG_MAX = 0.1, 3.0  # Drag Block magnitude slider range


class ShipMission:
    """Phases: ascent -> coast_apo -> insertion -> deploy_setup -> deploy -> orbit_coast -> deorbit_wait
    -> deorbit -> entry_coast -> entry -> flip -> landing -> splashdown -> done."""

    def __init__(self, f):
        self.f = f; self.phase = "ascent"; self.t0 = 0.0; self.log = {}
        self.dir = None
        self.sat_queue = [g for pair in f.hw.sat_pairs for g in pair]     # outer first, then inner
        self.deployed = 0
        self.drag_cmd = {}
        self.track = None; self.track_now = 0.0; self.target_angle = None; self.target = None
        self.heading = None; self.entry_sign = 1.0
        self.k_bowl = 1.0; self.m_sum = None; self.v_prev = None; self.pitch_i = 0.0
        self.next_predict = 0.0; self.pred = None; self.prof = None

    # ------------------------------------------------------------------ helpers
    def set_phase(self, t, ph):
        self.phase = ph; self.t0 = t

    def sl_mask(self):
        return np.array([cc in self.f.hw.ship_sl for cc in self.f.s_eng.cells])

    def steer(self, st, desired, roll_body=None, roll_desired=None, wn=None, err_max=None, w_ff=None):
        c = self.f.cfg
        return pd_attitude(st, self.f.ship, desired, wn=wn or c["coast_wn"], zeta=1.0, roll_body=roll_body,
                           roll_desired=roll_desired, err_max=err_max or c["coast_err_max"], w_ff=w_ff)

    def rcs(self, cmd, tau, extra=np.zeros(3), quiet_deg=None, ang=None):
        """Thrusters, then the flywheels, take the torque the engines (and drag bowls) did not produce.

        With quiet_deg, the thrusters stay off while the pointing error is inside that dead band (every
        nose-thruster firing also nudges the orbit)."""
        rem = tau - self.f.s_eng.tau_got - extra
        if quiet_deg is not None and ang is not None and ang < quiet_deg:
            self.f.s_rcs.idle(cmd)
            self.f.s_wheels.emit(cmd, rem, scale=30.0)
            return
        rem = rem - self.f.s_rcs.emit(cmd, rem)
        self.f.s_wheels.emit(cmd, rem, scale=30.0)

    def set_drag(self, fwd, aft, mid=None):
        mid = 0.5 * (fwd + aft) if mid is None else mid
        for name in self.f.hw.drag:
            v = fwd if name.startswith("fwd") else aft if name.startswith("aft") else mid
            self.drag_cmd[name] = float(np.clip(v, DRAG_MIN, DRAG_MAX))

    def bowl_arms(self):
        """Bowl names and their positions relative to the live COM (machine frame)."""
        S, hw = self.f.ship, self.f.hw
        names = sorted(hw.drag)
        return names, np.array([hw.center(hw.drag[n]) - S.com_m for n in names])

    def bowl_split(self, T, m_total):
        """Magnitudes per bowl for torque-per-phi T = (pitch, roll), closest total to m_total (torque first)."""
        names, r = self.bowl_arms()
        G = np.vstack([r[:, 1], -r[:, 0], 0.3 * np.ones(len(names))])
        b = np.array([T[0], T[1], 0.3 * m_total])
        m = np.clip(lsq_linear(G, b, bounds=(DRAG_MIN, DRAG_MAX)).x, DRAG_MIN, DRAG_MAX)
        return names, r, m

    def drag_torque(self, st, m_total, tau):
        """Belly bowls: each pushes along body +Z with force m * phi, so its torque is phi * m * (y, -x, 0)
        about the COM. Pitch and roll torque first, total drag (range control) second."""
        rho = density(st["h"])
        phi = self.k_bowl * bowl_force(float(st["vel"] @ st["R"].apply(BELLY)), rho)
        if phi < 0.5:
            m_each = m_total / len(self.f.hw.drag)
            self.set_drag(m_each, m_each, m_each)
            return np.zeros(3), phi
        names, r, m = self.bowl_split((tau[0] / phi, tau[1] / phi), m_total)
        for n, mi in zip(names, m):
            self.drag_cmd[n] = float(mi)
        return phi * np.array([float(m @ r[:, 1]), float(-(m @ r[:, 0])), 0.0]), phi

    def trim_max(self):
        """Largest total bowl magnitude that still trims to zero pitch and roll torque."""
        _, _, m = self.bowl_split((0.0, 0.0), DRAG_MAX * len(self.f.hw.drag))
        return float(m.sum())

    def h_agl(self, st):
        """Height of the engine plane (vertical) or of the belly (horizontal) above the ground datum."""
        S, f = self.f.ship, self.f
        return st["h"] - f.ground_h - max(1.5, (S.com_m[1] - f.s_exit_y) * float(st["axis"] @ st["up"]))

    def belly_first(self, st, aoa_deg, sign=None):
        """Attitude with the belly square to the flow, in the track plane.

        sign +1 puts the nose ahead of the flight path (nose up while the path is shallow), -1 puts it behind
        (nose down-and-aft, the branch closest to the retrograde deorbit attitude). Falling vertically, both
        end belly-down with the nose level."""
        up = st["up"]
        if st["vt"] > 2.0:
            self.heading = unit(st["vh"])
        s_ = self.entry_sign if sign is None else sign
        hg = unit(self.heading - (self.heading @ up) * up)
        gamma = math.atan2(st["vr"], max(float(st["vh"] @ hg), 1e-3))      # flight path angle
        th = gamma + s_ * math.radians(aoa_deg)
        nose = math.cos(th) * hg + math.sin(th) * up
        belly = unit(st["vel"]) if st["speed"] > 1.0 else -up
        belly = unit(belly - (belly @ nose) * nose)
        return nose, belly

    def profile(self):
        """Landing-burn speed profile for the three sea-level engines at the current mass."""
        f, c = self.f, self.f.cfg
        if self.prof is None:
            self.prof = LandingProfile(f.ground_h, c["ship_landing_target_h"], c["ship_touchdown_speed"],
                                       c["ship_landing_margin"])
        self.prof.update(3 * engine_force(2.0, -1.0) / f.ship.mass)
        return self.prof

    def flip_height(self, v):
        """Height the landing flip must start at: the fall during the flip (which adds speed) plus a landing
        burn along the profile from the speed reached at the end of it."""
        c = self.f.cfg
        return self.prof.height_for(v + c["flip_dv"]) + (v + 0.5 * c["flip_dv"]) * c["flip_lead_s"]

    def flip_eta(self, st):
        """Seconds of belly-flop left before the landing flip is due.

        The bowls first bring the ship down to a speed the engines can stop; the flip is due at the last
        moment of the predicted belly-flop (bowls at their trim limit) when there is still height for it."""
        f = self.f
        off = f.ground_h + 1.5

        def room(p, v):
            return local_frame(p)[2] - off - self.flip_height(float(np.linalg.norm(v)))
        state = {"seen": room(st["pos"], st["vel"]) > 0.0}

        def stop(p, v):
            ok = room(p, v) > 0.0
            if ok:
                state["seen"] = True
            return state["seen"] and not ok
        if not state["seen"] and self.log.get("window"):
            return 0.0
        if state["seen"]:
            self.log["window"] = True
        _, _, t_ = predict_entry(st["pos"], st["vel"], f.ship.mass, self.m_sum, k_bowl=self.k_bowl,
                                 ground_h=f.ground_h, t_max=40.0, stop_fn=stop)
        return t_ if state["seen"] else 99.0

    def predicted_angle(self, pos, vel, m_sum, ref_angle):
        """Track angle of the predicted touchdown (current angle plus the angle swept on the way down); the
        bowls switch to the terminal-descent setting where the flight plan does."""
        c = self.f.cfg
        term = (c["terminal_h"], c["terminal_v"], 0.95 * self.trim_max())
        p, swept, _ = predict_entry(pos, vel, self.f.ship.mass, m_sum, k_bowl=self.k_bowl, ground_h=self.f.ground_h,
                                    terminal=term)
        return ref_angle + swept, p

    # ------------------------------------------------------------------ main step
    def step(self, t, dt, d, cmd):
        f, S = self.f, self.f.ship
        if f.hw.root not in d or self.phase == "done":
            f.s_eng.set_uniform(0.0)
            return None
        S.update_mass(d)
        st = S.state(d, live=True)
        up = st["up"]; g = gravity(st["r"])
        tt = t - self.t0
        v_ax = float(st["vel"] @ st["axis"])
        info = dict(ph=self.phase, h=round(st["h"], 1), vr=round(st["vr"], 1), vt=round(st["vt"], 1),
                    apo=round(min(st["apo"], 99999.0), 0), peri=round(st["peri"], 0), tilt=round(st["tilt"], 1),
                    m=round(S.mass, 1))
        prograde_h = unit(st["vh"]) if st["vt"] > 1 else unit(EAST - (EAST @ up) * up)
        if self.track is not None:
            self.track_now = self.track.angle(st["pos"])      # every step, so whole revolutions are counted
        handler = getattr(self, "_" + self.phase)
        handler(t, tt, dt, d, st, cmd, info, up, g, v_ax, prograde_h)
        return info

    # ------------------------------------------------------------------ ascent to orbit
    def _ascent(self, t, tt, dt, d, st, cmd, info, up, g, v_ax, prograde_h):
        """Radial-rate guidance: hold a climb rate that fades toward the target height, rest of the thrust
        builds horizontal speed; SECO once periapsis is high enough (suborbital, like the real SECO)."""
        f, c, S = self.f, self.f.cfg, self.f.ship
        if "rvac_out" not in self.log and tt >= c["t_rvac_out"]:
            self.log["rvac_out"] = t
            f.s_eng.enabled[f.s_eng.cells.index((0, 0))] = False
            f.ev.add(t, "rvac_out", "S41 Raptor Vacuum shuts down early; remaining 5 burn longer", real="ascent anomaly")
        n_on = int(f.s_eng.enabled.sum())
        a_max = 0.8 * n_on * engine_force(2.0, max(v_ax, 0.0)) / S.mass
        vc = circular_speed(st["h"])
        vr_ref = float(np.clip(c["ship_vr_gain"] * (c["target_apo"] - st["h"]), -c["ship_vr_max"], c["ship_vr_max"]))
        a_r = g * (1.0 - min(1.0, st["vt"] / vc) ** 2) + c["ship_vr_kp"] * (vr_ref - st["vr"])
        a_r = float(np.clip(a_r, -0.9 * a_max, 0.9 * a_max))
        a_t = math.sqrt(max(a_max * a_max - a_r * a_r, 0.0))
        raw = unit(a_r * up + a_t * prograde_h)
        prev = self.dir
        self.dir = st["axis"].copy() if prev is None else slew(prev, raw, math.radians(c["ship_slew_deg_s"]) * dt)
        w_ff = np.cross(prev, self.dir) / dt if prev is not None else None
        tau, err = pd_attitude(st, S, self.dir, wn=c["ship_wn"], zeta=0.9, err_max=0.8, w_ff=w_ff)
        off = math.degrees(math.acos(float(np.clip(st["axis"] @ raw, -1.0, 1.0))))
        throttle = float(np.clip((math.cos(math.radians(off)) - 0.5) / 0.4, c["ship_min_throttle"], 1.0))
        throttle *= min(1.0, 0.6 + tt / 1.5)
        f.s_eng.allocate(S.com_m, throttle * n_on * engine_force(2.0, max(v_ax, 0.0)), tau[0], tau[2], v_ax)
        self.rcs(cmd, tau)
        info.update(err=round(err, 1), off=round(off, 1), thr=round(throttle, 2), vr_ref=round(vr_ref, 1))
        if st["peri"] >= c["seco_peri"] or tt > c["ship_ascent_max_s"]:
            self.set_phase(t, "coast_apo")
            f.s_eng.set_uniform(0.0)
            f.ev.add(t, "seco", f"SECO: h={st['h']:.0f}, orbit {st['peri']:.0f} x {st['apo']:.0f} (suborbital)",
                     real="T+00:08:11", apo=st["apo"], peri=st["peri"])

    def _coast_apo(self, t, tt, dt, d, st, cmd, info, up, g, v_ax, prograde_h):
        f, c = self.f, self.f.cfg
        f.s_eng.set_uniform(0.0)
        tau, ang = self.steer(st, prograde_h, DOOR, up)
        self.rcs(cmd, tau, quiet_deg=c["coast_quiet_deg"], ang=ang)
        info.update(err=round(ang, 1))
        if (st["vr"] <= c["insertion_vr"] and ang < 15) or st["vr"] < -6:
            self.set_phase(t, "insertion")
            f.ev.add(t, "insertion", "Orbit insertion burn: one sea-level Raptor", real="T+00:25:17",
                     apo=st["apo"], peri=st["peri"])

    def _insertion(self, t, tt, dt, d, st, cmd, info, up, g, v_ax, prograde_h):
        f, c, S = self.f, self.f.cfg, self.f.ship
        # one sea-level Raptor: the three SL blocks share its thrust (each capped at 1/3) and steer with it
        tau, ang = self.steer(st, prograde_h, DOOR, up, wn=c["ship_wn"])
        f.s_eng.allocate(S.com_m, engine_force(2.0 / 3.0, v_ax) * 3, tau[0], tau[2], v_ax, active=self.sl_mask(),
                         p_max=2.0 / 3.0)
        self.rcs(cmd, tau)
        info.update(err=round(ang, 1))
        if tt > 1.0 and (st["peri"] >= min(c["orbit_min_peri"], st["h"] - 15.0) or tt > c["insertion_max_s"]):
            f.s_eng.set_uniform(0.0)
            self.set_phase(t, "deploy_setup")
            f.ev.add(t, "orbit", f"Starship is orbital: {st['peri']:.0f} x {st['apo']:.0f}", real="T+00:25:36",
                     apo=st["apo"], peri=st["peri"])
            self.track = Track(f.pad, st["pos"], st["vel"])

    # ------------------------------------------------------------------ payload deploy
    def _deploy_setup(self, t, tt, dt, d, st, cmd, info, up, g, v_ax, prograde_h):
        f, c = self.f, self.f.cfg
        f.s_eng.set_uniform(0.0)
        tau, ang = self.steer(st, prograde_h, DOOR, up)            # nose prograde, Pez door away from the planet
        self.rcs(cmd, tau, quiet_deg=c["coast_quiet_deg"], ang=ang)
        info.update(err=round(ang, 1))
        if (tt > c["deploy_settle_s"] and ang < 8) or tt > 25:
            self.set_phase(t, "deploy")
            f.ev.add(t, "deploy_start", "Payload deploy: Pez dispenser starts releasing Starlink V3", real="T+00:34:07")

    def _deploy(self, t, tt, dt, d, st, cmd, info, up, g, v_ax, prograde_h):
        f, c, S = self.f, self.f.cfg, self.f.ship
        f.s_eng.set_uniform(0.0)
        tau, ang = self.steer(st, prograde_h, DOOR, up)
        self.rcs(cmd, tau, quiet_deg=c["coast_quiet_deg"], ang=ang)
        info.update(err=round(ang, 1))
        k = int(tt // c["deploy_interval_s"])
        while self.deployed <= min(k, len(self.sat_queue) - 1):
            g_ = self.sat_queue[self.deployed]
            self.log.setdefault("fire", {})[g_] = t
            self.deployed += 1
            if self.deployed % 5 == 0 or self.deployed == len(self.sat_queue):
                f.ev.add(t, "sat", f"Starlink V3 #{self.deployed} of {len(self.sat_queue)} released")
        for g_, tf in self.log.get("fire", {}).items():
            if t - tf < 0.2:
                fire_bolts(cmd, [g_], c["sat_epower"])
            elif t - tf > 0.5 and g_ in S.guids:
                S.drop([g_])                                        # the satellite has left the ship
        if self.deployed >= len(self.sat_queue) and tt > len(self.sat_queue) * c["deploy_interval_s"] + 1:
            self.set_phase(t, "orbit_coast")
            f.ev.add(t, "deploy_done", f"All {len(self.sat_queue)} Starlink V3 deployed", real="T+01:04:39")

    # ------------------------------------------------------------------ early return
    def _orbit_coast(self, t, tt, dt, d, st, cmd, info, up, g, v_ax, prograde_h):
        f, c, S = self.f, self.f.cfg, self.f.ship
        f.s_eng.set_uniform(0.0)
        S.drop([g_ for g_ in self.sat_queue])
        tau, ang = self.steer(st, prograde_h, DOOR, up)
        self.rcs(cmd, tau, quiet_deg=c["coast_quiet_deg"], ang=ang)
        if tt > c["health_check_s"]:
            self.target_angle = c["landing_zone_deg"]
            self.target = self.track.point(self.target_angle)
            self.set_phase(t, "deorbit_wait")
            f.ev.add(t, "early_return", "Health check after the ascent RVac shutdown: flight control limits time on "
                     f"orbit and targets the northern-Pacific contingency zone ({self.target_angle - 360:.0f} deg into rev 2)",
                     real="out of an abundance of caution")

    def _deorbit_wait(self, t, tt, dt, d, st, cmd, info, up, g, v_ax, prograde_h):
        f, c = self.f, self.f.cfg
        f.s_eng.set_uniform(0.0)
        retro = unit(-st["vel"])
        tau, ang = self.steer(st, retro, DOOR, up)
        self.rcs(cmd, tau, quiet_deg=c["coast_quiet_deg"], ang=ang)
        now = self.track_now
        info.update(err=round(ang, 1), track=round(now, 1))
        if t >= self.next_predict:
            self.m_sum = c["entry_mag_nominal"] * len(f.hw.drag)
            dv = c["deorbit_dv_plan"] * retro
            self.pred, _ = self.predicted_angle(st["pos"], st["vel"] + dv, self.m_sum, now)
            gap = self.target_angle - self.pred
            self.next_predict = t + (0.5 if gap > 8 else 0.0)
        info.update(pred=round(self.pred, 1))
        if self.pred >= self.target_angle and ang < c["deorbit_align_deg"]:
            self.set_phase(t, "deorbit")
            f.ev.add(t, "deorbit_burn", "Deorbit burn: one sea-level Raptor (Starship's first deorbit burn)",
                     real="T+02:12:00")

    def _deorbit(self, t, tt, dt, d, st, cmd, info, up, g, v_ax, prograde_h):
        f, c, S = self.f, self.f.cfg, self.f.ship
        retro = unit(-st["vel"])
        tau, ang = self.steer(st, retro, DOOR, up, wn=c["ship_wn"])
        f.s_eng.allocate(S.com_m, engine_force(c["deorbit_power"], v_ax) * 3, tau[0], tau[2], v_ax,
                         active=self.sl_mask(), p_max=2.0 / 3.0)
        self.rcs(cmd, tau)
        now = self.track_now
        self.pred, _ = self.predicted_angle(st["pos"], st["vel"], self.m_sum, now) if st["peri"] < 480 else (1e9, None)
        info.update(err=round(ang, 1), pred=round(min(self.pred, 1e5), 1))
        if (self.pred <= self.target_angle and tt > 0.3) or tt > c["deorbit_max_s"]:
            f.s_eng.set_uniform(0.0)
            self.set_phase(t, "entry_coast")
            f.ev.add(t, "deorbit_off", f"Deorbit burn complete: periapsis {st['peri']:.0f}, predicted touchdown "
                     f"{(self.pred - self.target_angle) * math.pi / 180 * R_SURFACE:+.0f} along track from the zone",
                     real="T+02:12:11", peri=st["peri"])

    # ------------------------------------------------------------------ entry and descent
    def _entry_coast(self, t, tt, dt, d, st, cmd, info, up, g, v_ax, prograde_h):
        f, c = self.f, self.f.cfg
        f.s_eng.set_uniform(0.0)
        if "sign" not in self.log:
            # take the belly-first branch that needs the smaller turn from the deorbit attitude
            errs = {}
            for sg in (1.0, -1.0):
                n_, b_ = self.belly_first(st, c["entry_aoa_deg"], sign=sg)
                errs[sg] = attitude_error(st["R"], (0, 1, 0), n_, BELLY, b_)[0]
            self.entry_sign = min(errs, key=lambda k: float(np.linalg.norm(errs[k])))
            self.log["sign"] = self.entry_sign
        nose, belly = self.belly_first(st, c["entry_aoa_deg"])
        tau, ang = self.steer(st, nose, BELLY, belly)
        self.rcs(cmd, tau)
        self.set_drag(c["entry_mag_nominal"], c["entry_mag_nominal"])
        info.update(err=round(ang, 1))
        if st["h"] < 500.0:
            self.set_phase(t, "entry")
            f.ev.add(t, "entry", f"Entry interface: belly first at {st['speed']:.0f} u/s", real="T+02:28:52")

    def _entry(self, t, tt, dt, d, st, cmd, info, up, g, v_ax, prograde_h):
        f, c, S = self.f, self.f.cfg, self.f.ship
        f.s_eng.set_uniform(0.0)
        nose, belly = self.belly_first(st, c["entry_aoa_deg"])
        tau, ang = self.steer(st, nose, BELLY, belly, wn=c["entry_wn"], err_max=c["entry_err_max"])
        # integral trim: bowl-model error and the uneven drag of the other blocks leave a steady pitch torque
        err_b, _ = attitude_error(st["R"], (0, 1, 0), nose, BELLY, belly)
        if density(st["h"]) > 0.05:
            self.pitch_i = float(np.clip(self.pitch_i + c["entry_ki"] * err_b[0] * dt, -c["entry_i_max"], c["entry_i_max"]))
        tau = tau + np.array([S.inertia[0] * self.pitch_i, 0.0, 0.0])
        info.update(pi=round(self.pitch_i, 3))
        # learn the bowl coefficient from the measured deceleration
        rho = density(st["h"])
        if self.v_prev is not None and t - self.v_prev[0] >= 0.4:
            a = (st["vel"] - self.v_prev[1]) / (t - self.v_prev[0]) + g * up
            vhat = unit(st["vel"])
            a_bowl = float(-(a @ vhat)) - DRAG_COEFF * rho * st["speed"]
            v_n = float(st["vel"] @ st["R"].apply(BELLY))
            model = sum(self.drag_cmd.values()) * bowl_force(v_n, rho) / S.mass
            if model > 3.0 and ang < 20.0:
                self.k_bowl = float(np.clip(0.8 * self.k_bowl + 0.2 * a_bowl / model, 0.3, 3.0))
            self.v_prev = (t, st["vel"].copy())
            info.update(a_bowl=round(a_bowl, 1), k=round(self.k_bowl, 2))
            decel = float(-(a @ vhat))
            self.log["decel"] = decel
            if decel > self.log.get("peak_decel", 0.0):
                self.log["peak_decel"] = decel
            elif decel < 0.7 * self.log.get("peak_decel", 0.0) and "peak" not in self.log and decel > 3:
                self.log["peak"] = t
                f.ev.add(t, "peak_decel", f"Peak deceleration {self.log['peak_decel']:.0f} u/s^2 at h={st['h']:.0f}")
        elif self.v_prev is None:
            self.v_prev = (t, st["vel"].copy())
        # range control while fast; below that the bowls open to the trim limit for the lowest terminal speed
        now = self.track_now
        h_agl = self.h_agl(st)
        terminal = h_agl < c["terminal_h"] or st["speed"] < c["terminal_v"]
        if terminal and "terminal" not in self.log:
            self.log["terminal"] = t
        if "terminal" in self.log:
            self.m_sum = 0.95 * self.trim_max()
        elif t >= self.next_predict:
            n = len(f.hw.drag)
            lo, hi = DRAG_MIN * n, min(c["entry_mag_max"] * n, 0.85 * self.trim_max())   # keep trim margin
            a_lo, _ = self.predicted_angle(st["pos"], st["vel"], lo, now)
            a_hi, _ = self.predicted_angle(st["pos"], st["vel"], hi, now)
            if a_hi >= self.target_angle:
                self.m_sum = hi
            elif a_lo <= self.target_angle:
                self.m_sum = lo
            else:
                for _ in range(6):
                    mid = 0.5 * (lo + hi)
                    a_mid, _ = self.predicted_angle(st["pos"], st["vel"], mid, now)
                    lo, hi = (mid, hi) if a_mid > self.target_angle else (lo, mid)
                self.m_sum = 0.5 * (lo + hi)
            self.pred, _ = self.predicted_angle(st["pos"], st["vel"], self.m_sum, now)
            self.next_predict = t + 0.5
        tau_drag, phi = self.drag_torque(st, self.m_sum, tau)
        self.rcs(cmd, tau, extra=tau_drag)
        info.update(bowls=[round(self.drag_cmd[n], 2) for n in sorted(self.drag_cmd)])
        miss = (self.pred - self.target_angle) * math.pi / 180.0 * R_SURFACE
        info.update(err=round(ang, 1), M=round(self.m_sum, 2), miss=round(miss, 0), phi=round(phi, 1))
        gamma = math.degrees(math.atan2(st["vr"], max(st["vt"], 1e-3)))
        if gamma < -70 and "bellyflop" not in self.log:
            self.log["bellyflop"] = t
            f.ev.add(t, "bellyflop", f"Belly-flop descent: {st['speed']:.0f} u/s, flight path {gamma:.0f} deg",
                     real="T+02:47:30 transonic")
        eta = 99.0
        if "terminal" in self.log:
            self.profile()
            eta = self.flip_eta(st)
            settled = self.log.get("decel", 99.0) < c["flip_settled_decel"] and h_agl <= self.flip_height(st["speed"])
            if settled:
                eta = 0.0                              # never became feasible and no longer slowing: go now
        info.update(h_agl=round(h_agl, 1), eta=round(eta, 1))
        if st["vr"] < 0 and (eta <= 0.3 or h_agl < c["flip_floor_h"]):
            self.set_phase(t, "flip")
            f.ev.add(t, "landing_flip", "Landing flip: three sea-level Raptors relight and swing S41 tail-down",
                     real="T+02:50:11")

    def _flip(self, t, tt, dt, d, st, cmd, info, up, g, v_ax, prograde_h):
        f, c, S = self.f, self.f.cfg, self.f.ship
        hg = self.entry_sign * unit(self.heading - (self.heading @ up) * up)
        tau, ang = self.steer(st, up, BELLY, hg, wn=c["flip_wn"], err_max=1.0)
        force = c["ship_flip_collective"] * 3 * engine_force(2.0, v_ax)
        f.s_eng.allocate(S.com_m, force, tau[0], tau[2], v_ax, active=self.sl_mask(), f_min_frac=0.05)
        if ang > c["flip_open_loop_deg"] and "flip_pd" not in self.log:
            # the forward bowls go wide and the aft ones close: the airflow swings the tail down
            self.set_drag(DRAG_MAX, DRAG_MIN, DRAG_MIN)
            tau_drag = np.zeros(3)
        else:
            self.log["flip_pd"] = t
            tau_drag, _ = self.drag_torque(st, len(f.hw.drag) * c["entry_mag_nominal"], tau - f.s_eng.tau_got)
        self.rcs(cmd, tau, extra=tau_drag)
        info.update(err=round(ang, 1), h_agl=round(self.h_agl(st), 1))
        if ang < c["flip_align_deg"] or tt > c["flip_max_s"]:
            self.set_phase(t, "landing")
            f.ev.add(t, "landing_burn", "Landing burn: three sea-level Raptors", real="T+02:50:13")

    def _landing(self, t, tt, dt, d, st, cmd, info, up, g, v_ax, prograde_h):
        f, c, S = self.f, self.f.cfg, self.f.ship
        self.set_drag(DRAG_MIN, DRAG_MIN)
        h_agl = self.h_agl(st)
        vz = -st["vr"]
        prof = self.profile()
        v_ref = prof.speed(h_agl)
        ff = prof.decel(h_agl) * (vz / max(v_ref, 0.5))
        a_v = g + ff + c["ship_vz_gain"] * (vz - v_ref)
        to_t = self.target - st["pos"]
        to_t_h = to_t - (to_t @ up) * up
        vh_des = to_t_h * c["ship_lz_gain"]
        vh_des = vh_des * min(1.0, c["ship_vh_max"] / max(np.linalg.norm(vh_des), 1e-6))
        if h_agl < c["ship_vertical_h"]:
            vh_des = vh_des * 0.0
        a_h = (vh_des - st["vh"]) * c["ship_vh_gain"]
        a_h = a_h * min(1.0, c["ship_ah_max"] / max(np.linalg.norm(a_h), 1e-6))
        acc = max(a_v, 0.0) * up + a_h
        desired = unit(acc) if a_v > 2.0 else up
        hg = self.entry_sign * unit(self.heading - (self.heading @ up) * up)
        tau, ang = self.steer(st, desired, BELLY, hg, wn=c["ship_wn"], err_max=0.5)
        if h_agl < c["ship_step2_h"] and "two" not in self.log:
            self.log["two"] = t
            f.s_eng.cap[self.sl_mask()] = 2.0 / 3.0
            f.ev.add(t, "landing_step", "Landing burn steps down: 3 -> 2 Raptors", real="T+03:08:20")
        f.s_eng.allocate(S.com_m, S.mass * float(np.linalg.norm(acc)) if a_v > 0 else 0.0, tau[0], tau[2], v_ax,
                         active=self.sl_mask(), f_min_frac=0.02)
        self.rcs(cmd, tau)
        dist = surface_angle(st["pos"], self.target) * R_SURFACE
        info.update(err=round(ang, 1), h_agl=round(h_agl, 1), v_ref=round(v_ref, 1), zone=round(dist, 1))
        hist = self.log.setdefault("sink", [])
        hist.append((t, vz, st["vt"]))
        while hist and hist[0][0] < t - 1.5:
            hist.pop(0)
        low = h_agl < c["ship_landing_target_h"] + 3.0
        stalled = vz < 0.4 * c["ship_touchdown_speed"] and abs(st["vr"]) < 1.0
        if tt > 0.5 and low and (stalled or st["tilt"] > 30.0):
            self.log["contact_n"] = self.log.get("contact_n", 0) + 1
            if self.log["contact_n"] == 1:
                self.log["t_contact"] = t
        else:
            self.log["contact_n"] = 0
        if self.log["contact_n"] >= 4:
            f.s_eng.set_uniform(0.0)
            self.set_phase(t, "splashdown")
            f.ev.add(t, "landing_step2", "Landing burn: 2 -> 1 Raptor, engine cutoff at touchdown", real="T+03:08:26")
            before = [h_ for h_ in hist if self.log["t_contact"] - 0.3 <= h_[0] < self.log["t_contact"]] or hist
            v_td = max(h_[1] for h_ in before); vh_td = max(h_[2] for h_ in before)       # just before contact
            f.ev.add(t, "splashdown", f"S41 splashdown: {v_td:.1f} u/s vertical, {vh_td:.1f} lateral at contact, "
                     f"{dist:.0f} from the target zone north of 'Hawaii'", real="T+03:08:30",
                     vz=v_td, vh=vh_td, miss=dist)

    def _splashdown(self, t, tt, dt, d, st, cmd, info, up, g, v_ax, prograde_h):
        """LONE ORB has no ocean to knock S41 over, so the tip-over after splashdown is scripted: a short
        kick from two engines on one side levers the ship over the opposite edge of its base."""
        f, c = self.f, self.f.cfg
        if c["tip_kick_start"] <= tt < c["tip_kick_start"] + c["tip_kick_s"] and "tip" not in self.log:
            kick = np.array([cc in c["tip_kick_cells"] for cc in f.s_eng.cells])
            f.s_eng.cap[:] = 1.0; f.s_eng.enabled[kick] = True
            f.s_eng.set_uniform(c["tip_kick_power"], active=kick)
        else:
            f.s_eng.set_uniform(0.0)
        f.s_rcs.idle(cmd)
        if st["tilt"] > 60 and "tip" not in self.log:
            self.log["tip"] = t
            f.ev.add(t, "tip_over", f"S41 tips over after splashdown (tilt {st['tilt']:.0f} deg)",
                     real="after T+03:08:30")
        if tt > c["end_s"]:
            self.set_phase(t, "done")
            f.done = True
