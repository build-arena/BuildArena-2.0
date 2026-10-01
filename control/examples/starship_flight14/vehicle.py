"""Hardware binding and rigid-body state estimation for the Starship stack and its stages."""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation as Rot

from parts import BLOCKS, PARTS
from physics import FUEL_MASS, elements, local_frame

BALLAST_MASS = 0.2          # default Ballast trim (slider minimum); load-path blocks are heavier


class Hardware:
    """Named parts (parts.py, by build ID) rebound to this run's GUIDs."""

    def __init__(self, rebuild_manifest: Path, bsg: Path):
        guid_of = {k: v.lower() for k, v in json.loads(Path(rebuild_manifest).read_text())["build_id_to_guid"].items()}
        self.guid_of = guid_of
        self.P = PARTS
        self.info = {guid_of[bid]: {"name": name, "center": list(center), "mass": mass}
                     for bid, (name, center, mass) in BLOCKS.items()}
        # saved (spawn) orientation of every block, from the prepared BSG
        root = ET.parse(bsg).getroot()
        self.saved = {}
        self.origin = {}
        for b in root.findall("Blocks/Block"):
            q = b.find("Transform/Rotation"); p = b.find("Transform/Position")
            g = b.get("guid").lower()
            self.saved[g] = Rot.from_quat([float(q.get(k)) for k in "xyzw"])
            self.origin[g] = np.array([float(p.get(k)) for k in "xyz"])
        g = self.g
        # ---- groups ----
        self.booster = set(g(x) for x in self._flat(["ring", "booster_tanks", "booster_plate", "booster_engines", "grid_fins"]))
        self.booster |= {g(self.P["booster_wheel_x"]), g(self.P["booster_wheel_z"])}
        self.booster |= set(g(x) for x in self.P["booster_rcs"].values())
        self.booster |= set(g(x) for x in self._flat(["clamp", "ring_braces", "interstage_braces"]))   # bolts stay with B21
        self.sat_pairs = [(g(s["outer"]), g(s["inner"])) for s in self.P["sats"]]
        self.sats = set(x for pair in self.sat_pairs for x in pair)
        self.ship = set(self.info) - self.booster
        # ---- named hardware ----
        cells = lambda d: {tuple(int(v) for v in k.split(",")): g(b) for k, b in d.items()}
        self.ship_sl = cells(self.P["ship_sl"]); self.ship_rvac = cells(self.P["ship_rvac"])
        self.clamps = [g(b) for b in self.P["clamp"].values()]
        self.booster_engines = cells(self.P["booster_engines"])
        self.grid_fins = {k: g(v) for k, v in self.P["grid_fins"].items()}
        self.flaps = {k: {kk: g(vv) for kk, vv in v.items()} for k, v in self.P["flaps"].items()}
        self.ship_rcs = {k: g(v) for k, v in self.P["ship_rcs"].items()}
        self.booster_rcs = {k: g(v) for k, v in self.P["booster_rcs"].items()}
        self.drag = {k: g(v) for k, v in self.P["drag"].items()}             # belly drag bowls (aft*, fwd*)
        self.wheels = {"x": g(self.P["wheel_x"]), "z": g(self.P["wheel_y"])}   # machine axes x (East) and z (North)
        self.booster_wheels = {"x": g(self.P["booster_wheel_x"]), "z": g(self.P["booster_wheel_z"])}
        self.ballast = {gg: BALLAST_MASS for gg, v in self.info.items() if v["name"] == "Ballast"}
        for x in self._flat(["clamp_strut", "ring"]):
            self.ballast[g(x)] = 1.0          # stiffer joints: avoid 1:300 mass ratios in the interstage load path
        self.root = g(self.P["root"])
        self.booster_ref = g(self.P["booster_tanks"][2])

    def g(self, bid) -> str:
        return self.guid_of[str(bid)]

    def _flat(self, keys):
        out = []
        for k in keys:
            v = self.P[k]
            out += list(v.values()) if isinstance(v, dict) else list(v)
        return out

    def center(self, guid) -> np.ndarray:
        return np.asarray(self.info[guid]["center"], float)

    def mass(self, guid, d=None) -> float:
        info = self.info[guid]
        m = self.ballast[guid] if info["name"] == "Ballast" else float(info["mass"])
        bid = {"Fuel Barrel Big": 99, "Fuel Barrel": 92}.get(info["name"])
        if bid is not None:
            cap = 100.0 if bid == 99 else 10.0
            amount = cap
            if d is not None and guid in d and d[guid].get("fuel_valid"):
                amount = float(d[guid]["fuel"][0])
            m += amount * FUEL_MASS[bid]
        return m


class Body:
    """A rigid set of blocks; pose from one live reference block, mass/COM from live fuel."""

    def __init__(self, hw: Hardware, guids, ref: str, name: str):
        self.hw, self.name = hw, name
        self.guids = [x for x in guids]
        self.ref = ref
        self.com_m = None; self.mass = None; self.inertia = None
        self.update_mass(None)

    def drop(self, guids):
        """Remove blocks that left this body (released payload)."""
        gone = set(guids)
        self.guids = [x for x in self.guids if x not in gone]

    def update_mass(self, d):
        ms = []; cs = []
        for gg in self.guids:
            if d is not None and gg not in d:
                continue                       # destroyed or released
            ms.append(self.hw.mass(gg, d)); cs.append(self.hw.center(gg))
        ms = np.asarray(ms); cs = np.asarray(cs)
        self.mass = float(ms.sum())
        self.com_m = (ms[:, None] * cs).sum(0) / self.mass
        r = cs - self.com_m
        self.inertia = np.array([(ms * (r[:, 1] ** 2 + r[:, 2] ** 2)).sum(),
                                 (ms * (r[:, 0] ** 2 + r[:, 2] ** 2)).sum(),
                                 (ms * (r[:, 0] ** 2 + r[:, 1] ** 2)).sum()])

    def live_kinematics(self, d):
        """Mass-weighted COM position, COM velocity and mean angular velocity from live block states.

        Averaging over every block removes the local vibration a single reference block carries."""
        gs = tuple(gg for gg in self.guids if gg in d)
        if not gs:
            return None
        if getattr(self, "_cache_key", None) != gs:
            self._cache_key = gs
            self._off = np.array([self.hw.center(gg) - self.hw.origin[gg] for gg in gs])
            self._saved_inv = Rot.concatenate([self.hw.saved[gg] for gg in gs]).inv()
        q = np.array([d[gg]["rotation"] for gg in gs])
        p = np.array([d[gg]["position"] for gg in gs])
        v = np.array([d[gg]["velocity"] for gg in gs])
        w = np.array([d[gg]["angular_velocity"] for gg in gs])
        m = np.array([self.hw.mass(gg, d) for gg in gs])
        rel = (Rot.from_quat(q) * self._saved_inv).apply(self._off)
        world_c = p + rel
        v_c = v + np.cross(w, rel)
        M = m.sum()
        return (m[:, None] * world_c).sum(0) / M, (m[:, None] * v_c).sum(0) / M, (m[:, None] * w).sum(0) / M

    def fuel(self, d):
        tot = cap = 0.0
        for gg in self.guids:
            s = d.get(gg)
            if s and s.get("fuel_valid") and self.hw.info[gg]["name"] in ("Fuel Barrel Big", "Fuel Barrel"):
                tot += float(s["fuel"][0]); cap += float(s["fuel"][1])
        return tot, cap

    def state(self, d, live=False) -> dict:
        s = d[self.ref]
        R = Rot.from_quat(s["rotation"]) * self.hw.saved[self.ref].inv()      # machine -> world
        origin = np.asarray(s["position"]) - R.apply(self.hw.origin[self.ref])
        kin = self.live_kinematics(d) if live else None
        if kin is not None:
            pos, vel, w = kin
            self.com_m = R.inv().apply(pos - origin)
        else:
            pos = origin + R.apply(self.com_m)
            w = np.asarray(s["angular_velocity"])
            vel = np.asarray(s["velocity"]) + np.cross(w, pos - np.asarray(s["position"]))
        up, r, h = local_frame(pos)
        vr = float(vel @ up)
        vh = vel - vr * up
        axis = R.apply([0.0, 1.0, 0.0])
        el = elements(pos, vel)
        return dict(R=R, pos=pos, vel=vel, w=w, w_body=R.inv().apply(w), up=up, r=r, h=h, vr=vr, vh=vh,
                    vt=float(np.linalg.norm(vh)), speed=float(np.linalg.norm(vel)), axis=axis,
                    tilt=float(np.degrees(np.arccos(np.clip(axis @ up, -1, 1)))), apo=el["apo"], peri=el["peri"],
                    orbit=el)
