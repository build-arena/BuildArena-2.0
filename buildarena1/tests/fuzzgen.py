"""Random scenario generator used in a review of the judge port, kept
unchanged so its seeds reproduce the same machines and trajectories.
Seeds 30, 34, 42, 84 and 106 give lift machines made of the Starting Block
alone, the 15 groups (5 seeds x 3 modes) where the port raised and 1.0 did not.
"""

from __future__ import annotations

import math

from .trajgen import Scenario

NAMES = ["Small Wooden Block", "Powered Wheel", "Water Cannon", "Torch", "Brace", "Winch"]
START_ONLY_LIFT_SEEDS = (30, 34, 42, 84, 106)


def make(rng):
    cat = rng.choice(["transport", "support", "lift"])
    lvl = rng.choice(["soft", "medium", "hard"])
    n = rng.randint(0, 8)
    names = ["Starting Block"] + [rng.choice(NAMES) for _ in range(n)]
    cargo = cat == "support" or (cat == "transport" and lvl != "soft")
    dur = {"transport": 30.0, "support": 17.0, "lift": 10.0 if lvl == "soft" else 30.0}[cat]
    params = {}
    pmiss = rng.choice([0.0, 0.0, 0.05, 0.3])
    thr = {"transport": 10.0, "support": 4.0, "lift": 30.0}[cat]

    def state(lid, t):
        if rng.random() < pmiss:
            return None
        p = params.setdefault(lid, (rng.uniform(-12, 12), rng.uniform(-12, 12), rng.uniform(-1, 1),
                                    rng.uniform(0, thr * 1.3), rng.uniform(0, 30), rng.random() < 0.5,
                                    rng.uniform(0, 10), rng.uniform(-0.3, 0.3)))
        gx, gz, h0, hpeak, tbreak, heated, theat, noise = p
        frac = min(1.0, t / 25.0)
        y = h0 + (hpeak if t < tbreak else hpeak * 0.3) + noise * math.sin(7 * t)
        if cat == "lift":
            y = h0 + hpeak * min(1.0, max(0.0, (t - 2) / 10.0)) + noise
        return (gx * frac + noise, y, gz * frac - noise, heated and t > theat)
    return Scenario(cat, lvl, names, state, cargo=cargo, duration=dur)
