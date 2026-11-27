"""Super Heavy B21 after hot staging: flip, boostback, coast, landing burn and offshore touchdown."""
from __future__ import annotations

import math

import numpy as np

from actuators import pd_torque
from attitude import slew, unit
from physics import R_SURFACE, engine_force, gravity, propagate, surface_angle

INNER5 = [(0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)]      # blocks standing for the inner 13 Raptors
CENTER3 = [(0, 0), (1, 0), (-1, 0)]                         # final step-down


class BoosterReturn:
    """Phases: sep_coast -> flip -> boostback -> coast -> landing -> safed.

    Engines and the cold-gas thrusters share every torque request: the engines take what their
    differential throttle can give, the thrusters take the rest.
    """

    def __init__(self, f):
        self.f = f; self.phase = "sep_coast"; self.t0 = 0.0; self.log = {}
        self.dir = None

    def set_phase(self, t, ph):
        self.phase = ph; self.t0 = t

    def steer(self, st, desired, wn):
        return pd_torque(st, self.f.booster, desired, wn=wn, zeta=1.0, roll_damp=2.0 * wn)

    def finish_torque(self, cmd, tau):
        """Cold-gas thrusters, then the flywheels, get whatever the engines did not produce."""
        f = self.f
        rem = tau - f.b_eng.tau_got
        rem = rem - f.b_rcs.emit(cmd, rem)
        f.b_wheels.emit(cmd, rem, scale=30.0)

    def landing_speed(self, h_agl):
        """Descent-speed profile: constant net deceleration down to the touchdown gate."""
        c = self.f.cfg
        return math.sqrt(c["touchdown_speed"] ** 2 + 2.0 * c["landing_decel"] * max(h_agl - c["landing_target_h"], 0.0))

    def impact_point(self, st):
        p, _, _ = propagate(st["pos"], st["vel"], 120.0, dt=0.25, drag=True, stop_h=self.f.ground_h + 10.0)
        return p

    def step(self, t, dt, d, cmd):
        f, c, B = self.f, self.f.cfg, self.f.booster
        if self.phase == "safed" or f.hw.booster_ref not in d:
            f.b_eng.set_uniform(0.0)
            f.b_rcs.idle(cmd)
            if self.phase == "safed" and "fts" not in self.log and t - self.t0 > c["fts_delay_s"]:
                self.log["fts"] = t
                f.ev.add(t, "booster_fts", "B21 flight termination system triggered (planned safety demonstration)",
                         real="after T+00:07:01")
            return None
        B.update_mass(d)
        st = B.state(d, live=True)
        up = st["up"]; g = gravity(st["r"])
        tt = t - self.t0
        h_agl = st["h"] - f.ground_h - (B.com_m[1] - f.b_exit_y) * max(0.2, float(st["axis"] @ up))
        v_ax = float(st["vel"] @ st["axis"])
        to_lz = f.lz_booster - st["pos"]
        lz_dist = surface_angle(st["pos"], f.lz_booster) * R_SURFACE
        info = dict(ph=self.phase, h=round(h_agl, 1), vr=round(st["vr"], 1), vt=round(st["vt"], 1),
                    tilt=round(st["tilt"], 1), lz=round(lz_dist, 1), m=round(B.mass, 1))
        if self.dir is None:
            lz_h = unit(to_lz - (to_lz @ up) * up)
            retro_h = -unit(st["vh"]) if st["vt"] > 2 else lz_h
            el = math.radians(c["boostback_elev_deg"])
            self.dir = unit(math.cos(el) * retro_h + math.sin(el) * up)
        act31 = np.ones(9, bool); act31[f.b_eng.cells.index((-1, -1))] = False     # 31 of 33 relight

        if self.phase == "sep_coast":
            f.b_eng.set_uniform(0.0)
            tau = -B.inertia * 2.0 * c["booster_wn"] * st["w_body"]                   # rate damping only
            self.finish_torque(cmd, tau)
            if tt >= c["flip_delay_s"] and "sep_confirmed" in f.log:
                self.set_phase(t, "flip")
                f.ev.add(t, "booster_flip", "B21 flips for boostback", real="T+00:02:25")
            return info

        if self.phase in ("flip", "boostback"):
            desired = self.dir
            if self.phase == "boostback" and "miss" in self.log:
                # steer the predicted impact point onto the zone (both axes), and pitch up as it closes in so
                # the burn ends near the landing attitude
                el = math.radians(c["boostback_elev_deg"])
                dh = unit(self.log["to_zone"])
                target = unit(math.cos(el) * dh + math.sin(el) * up)
                self.dir = slew(self.dir, target, math.radians(20.0) * dt)
                s_ = float(np.clip((c["boostback_blend_miss"] - self.log["miss"]) / c["boostback_blend_miss"], 0.0, 1.0))
                desired = unit((1.0 - c["boostback_blend_max"] * s_) * self.dir + c["boostback_blend_max"] * s_ * up)
            tau, err = self.steer(st, desired, c["booster_wn"])
            if self.phase == "flip":
                force = c["flip_collective"] * engine_force(1.0, v_ax) * act31.sum()
                f.b_eng.allocate(B.com_m, force, tau[0], tau[2], v_ax, active=act31, f_min_frac=0.0,
                                 p_max=c["flip_pmax"], torque_priority=True)
                if err < c["boostback_align_deg"]:
                    self.set_phase(t, "boostback"); tt = 0.0
                    f.ev.add(t, "boostback", "B21 boostback burn: 31 of 33 Raptors relit", real="T+00:02:27")
            else:
                force = c["boostback_collective"] * engine_force(1.0, v_ax) * act31.sum()
                f.b_eng.allocate(B.com_m, force, tau[0], tau[2], v_ax, active=act31, f_min_frac=0.0)
            self.finish_torque(cmd, tau)
            ip = self.impact_point(st)
            gap = f.lz_booster - ip
            gap = gap - (gap @ up) * up
            miss = float(np.linalg.norm(gap))
            self.log["miss"] = miss; self.log["to_zone"] = gap
            best = self.log.get("best_miss", 1e9) if self.phase == "boostback" else 1e9
            self.log["best_miss"] = min(best, miss)
            rising = self.phase == "boostback" and miss > best + 5.0             # closest approach passed
            info.update(err=round(err, 1), miss=round(miss, 1))
            if self.phase == "boostback" and (miss <= c["boostback_lead"] or rising or tt > c["boostback_max_s"]) and tt > 0.3:
                self.set_phase(t, "coast")
                f.b_eng.set_uniform(0.0)
                f.ev.add(t, "boostback_off", f"Boostback shutdown; predicted impact {miss:.0f} from the landing zone",
                         real="T+00:03:07", miss=miss)
            return info

        # ---- coast and landing burn ----
        vz = -st["vr"]
        v_ref = self.landing_speed(h_agl)
        info.update(v_ref=round(v_ref, 1))
        if self.phase == "coast":
            # engines first once falling; until then hold the vertical the boostback ended near
            desired = unit(-st["vel"]) if st["vr"] < -10.0 else up
            tau, err = self.steer(st, desired, c["booster_wn"])
            if err > c["coast_assist_deg"]:
                # too far off for the cold-gas thrusters alone: a differential puff of the inner engines
                f.b_eng.allocate(B.com_m, 0.0, tau[0], tau[2], v_ax, active=np.array([cc in INNER5 for cc in f.b_eng.cells]),
                                 f_min_frac=0.0, p_max=c["coast_assist_pmax"], torque_priority=True)
            else:
                f.b_eng.set_uniform(0.0)
            self.finish_torque(cmd, tau)
            info.update(err=round(err, 1))
            if st["vr"] < 0 and vz >= c["landing_ignite_frac"] * v_ref:
                self.set_phase(t, "landing")
                f.ev.add(t, "landing_burn", "B21 landing burn: 13 inner Raptors commanded, 11 light",
                         real="T+00:06:36", h=h_agl, vz=vz)
            return info

        # landing: track the stopping profile (feed-forward of its deceleration, gravity at this height)
        ff = c["landing_decel"] * (vz / max(v_ref, 0.5)) if h_agl > c["landing_target_h"] else 0.0
        a_v = g + ff + c["booster_vz_gain"] * (vz - v_ref)
        to_lz_h = to_lz - (to_lz @ up) * up
        vh_des = to_lz_h * c["booster_lz_gain"]
        vh_des = vh_des * min(1.0, c["booster_vh_max"] / max(np.linalg.norm(vh_des), 1e-6))
        if h_agl < c["landing_vertical_h"]:
            vh_des = vh_des * 0.0
        a_h = (vh_des - st["vh"]) * c["booster_vh_gain"]
        a_h = a_h * min(1.0, c["booster_ah_max"] / max(np.linalg.norm(a_h), 1e-6))
        acc = max(a_v, 0.0) * up + a_h
        desired = unit(acc) if a_v > 2.0 else up
        f_cmd = B.mass * float(np.linalg.norm(acc)) if a_v > 0 else 0.0
        tau, err = self.steer(st, desired, c["booster_wn"])
        cells = INNER5 if h_agl > c["landing_step1_h"] else CENTER3
        if cells is CENTER3 and "step1" not in self.log:
            self.log["step1"] = t
            f.ev.add(t, "landing_step", "B21 landing burn steps down: 13 -> 5 -> 3 Raptors")
        if "eleven" not in self.log:
            self.log["eleven"] = True
            f.b_eng.cap[f.b_eng.cells.index((0, 1))] = 0.85            # "11 of 13" on the inner ring
        mask = np.array([cc in cells for cc in f.b_eng.cells])
        f.b_eng.allocate(B.com_m, f_cmd, tau[0], tau[2], v_ax, active=mask, f_min_frac=0.02)
        self.finish_torque(cmd, tau)
        info.update(err=round(err, 1))
        # touchdown on unknown terrain: below the gate the descent is slow and steady, so contact shows up as
        # the sink rate collapsing (or the vehicle starting to topple)
        hist = self.log.setdefault("sink", [])
        hist.append((t, vz, st["vt"]))
        while hist and hist[0][0] < t - 1.5:
            hist.pop(0)
        low = h_agl < c["landing_target_h"] + 3.0
        stalled = vz < 0.4 * c["touchdown_speed"] and st["speed"] < 2.0
        if tt > 1.0 and low and (stalled or st["tilt"] > 30.0):
            self.log["contact_n"] = self.log.get("contact_n", 0) + 1
            if self.log["contact_n"] == 1:
                self.log["t_contact"] = t
        else:
            self.log["contact_n"] = 0
        if self.log["contact_n"] >= 4:
            self.set_phase(t, "safed")
            f.b_eng.set_uniform(0.0)
            before = [h_ for h_ in hist if self.log["t_contact"] - 0.3 <= h_[0] < self.log["t_contact"]] or hist
            v_td = max(h_[1] for h_ in before); vh_td = max(h_[2] for h_ in before)       # just before contact
            f.ev.add(t, "booster_splashdown",
                     f"B21 landing burn shutdown at touchdown: {v_td:.1f} u/s vertical, {vh_td:.1f} lateral at "
                     f"contact, {lz_dist:.1f} from the landing zone",
                     real="T+00:07:01", vz=v_td, vh=vh_td, miss=lz_dist, h_agl=h_agl)
        return info
