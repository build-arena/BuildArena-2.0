"""Port of the BuildArena 1.0 judges (1.0 analyze/sim_*.py at commit c3005b9,
https://github.com/AI4Science-WestlakeU/BuildArena, CC BY-NC 4.0).

Input is the cleaned 1.0-style CSV text that ``evalkit.legacy_log`` derives from
a 2.0 telemetry.csv, plus the 1.0 block-id table. The pandas calls mirror the
1.0 ones one for one, because the judges' results depend on pandas' parsing of
mixed-width rows and on its sort order among rows printed at the same 0.1 s.
Output keys are the 1.0 result keys. 1.0 side effects (writing clean CSVs and
result JSONs under datacache/) are left out. Lift Lv.2/Lv.3 default to the
height-only success rule; success_rule="ba1" keeps the original criterion.
"""

from __future__ import annotations

import io
from typing import Any, Literal

import numpy as np
import pandas as pd

from .legacy import V1_MASS


class NoValidRows(ValueError):
    """robust_read_csv's fallback found nothing usable (sim_common.py:173-174)."""


# ---------------------------------------------------------------- sim_common.py
def robust_read(text: str, min_columns: int = 5, validate_numeric_columns: list | None = None,
                skip_to_first_valid: bool = True) -> pd.DataFrame:
    """sim_common.py:96-179 (robust_read_csv), reading from text instead of a path.

    An empty CSV (no tracked block printed anything) makes pd.read_csv raise
    EmptyDataError, which 1.0's robust_read_csv lets through. Every 1.0 caller
    that survives it does so with a blanket ``except Exception`` and its
    defaults (sim_common.py:356-364, 414-417, 474-477, sim_support.py:190-193);
    the callers that do not survive it (sim_transport.py's clean, sim_lift.py's
    cannon read) raise. NoValidRows lets this module do the same selectively.
    """
    try:
        df = pd.read_csv(io.StringIO(text), header=None)
    except pd.errors.EmptyDataError as exc:
        raise NoValidRows("The 1.0-style CSV is empty.") from exc
    except pd.errors.ParserError:
        return _robust_read_fallback(text, min_columns, validate_numeric_columns, skip_to_first_valid)
    if min_columns > 0:
        df = df.dropna(subset=df.columns[:min_columns])
    if validate_numeric_columns:
        for col_idx in validate_numeric_columns:
            if col_idx < len(df.columns):
                df = df[pd.to_numeric(df.iloc[:, col_idx], errors="coerce").notna()]
    return df


def _robust_read_fallback(text: str, min_columns: int, validate_numeric_columns: list | None,
                          skip_to_first_valid: bool) -> pd.DataFrame:
    """sim_common.py:136-179, the line-by-line branch."""
    rows = []
    found_valid_start = False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(",")
        if len(parts) < min_columns:
            continue
        if validate_numeric_columns and not _numeric_ok(parts, validate_numeric_columns):
            continue
        if skip_to_first_valid and not found_valid_start:
            found_valid_start = True
            rows.append(parts)
        elif not skip_to_first_valid or found_valid_start:
            rows.append(parts)
    if not rows:
        raise NoValidRows(f"No valid rows found with min_columns={min_columns}")
    return pd.DataFrame(rows)


def _numeric_ok(parts: list[str], columns: list[int]) -> bool:
    for col_idx in columns:
        if col_idx < len(parts):
            try:
                float(parts[col_idx])
            except (ValueError, IndexError):
                return False
    return True


def clean_positions(text: str) -> pd.DataFrame:
    """sim_common.py:181-217 (clean_simulation_data) without the datacache write."""
    df = robust_read(text)
    if df.shape[1] != 5:
        # 1.0's column assignment raises here; its callers treat that like no rows.
        raise NoValidRows(f"Expected 5 position columns, got {df.shape[1]}")
    df.columns = ["time", "id", "x", "z", "y"]
    for col in ("time", "x", "z", "y"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna()
    return df.sort_values(["time", "id"]).reset_index(drop=True)


def max_speed(position_df: pd.DataFrame, start_time_speed: float) -> float:
    """sim_common.py:239-295 (calculate_max_speed)."""
    if position_df.empty or len(position_df) < 2:
        return 0.0
    filtered = position_df[position_df["time"] > start_time_speed]
    if filtered.empty or len(filtered) < 2:
        return 0.0
    best = 0.0
    for _, block in filtered.groupby("id"):
        if len(block) < 2:
            continue
        block = block.sort_values("time").reset_index(drop=True)
        dt = block["time"].diff().iloc[1:]
        dx, dy, dz = (block[c].diff().iloc[1:] for c in ("x", "y", "z"))
        valid = dt > 0
        if not valid.any():
            continue
        speeds = np.sqrt(dx[valid] ** 2 + dy[valid] ** 2 + dz[valid] ** 2) / dt[valid]
        block_max = speeds.max()
        if not np.isnan(block_max) and block_max > best:
            best = block_max
    return float(best)


def motion_metrics(text: str, start_time_speed: float) -> dict[str, float]:
    """sim_common.py:298-364 (analyze_motion_metrics). 1.0 returns zeros when the
    CSV has no position rows (its blanket except); that is the only failure kept."""
    try:
        df = clean_positions(text)
    except NoValidRows:
        return {"max_height": 0.0, "initial_max_height": 0.0, "max_speed": 0.0, "elevation_change": 0.0}
    max_height = 0.0 if df.empty else float(df["z"].max())
    initial_max_height = float(df["z"][:int(start_time_speed)].max())
    end1 = start_time_speed + 5.0
    slice1 = df[(df["time"] > start_time_speed) & (df["time"] <= end1)]
    slice2 = df[(df["time"] > end1) & (df["time"] <= end1 + 5.0)]
    if len(slice1) > 0 and len(slice2) > 0:
        elevation_change = float(slice2["z"].max() - slice1["z"].max())
    else:
        elevation_change = 0.0
    return {"max_height": max_height, "initial_max_height": initial_max_height,
            "max_speed": max_speed(df, start_time_speed), "elevation_change": elevation_change}


def default_trajectory_stats() -> dict[str, float]:
    """sim_common.py:583-592."""
    return {"max_progress_ratio": 0.0, "direction_consistency": 0.0, "backtrack_penalty": 0.0,
            "lateral_deviation": 0.0, "path_efficiency": 0.0, "final_distance_ratio": 1.0}


def trajectory_metrics(traj: pd.DataFrame, start_pos: np.ndarray, target_pos: np.ndarray) -> dict[str, float]:
    """sim_common.py:492-580 (_calculate_trajectory_metrics)."""
    eps = 1e-8
    ideal = target_pos - start_pos
    dist = float(np.linalg.norm(ideal))
    if dist < 1e-6:
        return default_trajectory_stats()
    unit = ideal / dist
    pos = traj[["x", "y", "z"]].to_numpy(dtype=float)
    times = traj["time"].to_numpy(dtype=float)
    if len(pos) < 2:
        return default_trajectory_stats()
    dt = np.diff(times)
    steps = dt > 0.0
    if not np.any(steps):
        return default_trajectory_stats()
    dt = dt[steps]
    dx = np.diff(pos, axis=0)[steps, :]
    total_t = float(times[-1] - times[0]) if times[-1] > times[0] else float(np.sum(dt))
    if total_t <= 0:
        return default_trajectory_stats()
    vel = dx / dt[:, None]
    speed = np.linalg.norm(vel, axis=1)
    rel = pos - start_pos[None, :]
    along = rel @ unit
    along_speed = vel @ unit
    cos_theta = np.divide(along_speed, np.maximum(speed, eps))
    lateral = np.linalg.norm(rel @ (np.eye(3) - np.outer(unit, unit)).T, axis=1)
    effective = float(max(np.max(along), eps))
    return {
        "max_progress_ratio": float(min(1.0, max(0.0, float(np.max(along)) / (dist + eps)))),
        "direction_consistency": float(np.sum(np.maximum(0.0, cos_theta) * dt) / total_t),
        "backtrack_penalty": float(float(np.sum(np.maximum(0.0, -along_speed) * dt)) / (dist + eps)),
        "lateral_deviation": float(np.sum(lateral[:-1][steps] * dt) / ((dist + eps) * total_t)),
        "path_efficiency": float(max(0.0, float(np.sum(np.linalg.norm(dx, axis=1))) / effective - 1.0)),
        "final_distance_ratio": float(max(0.0, (dist - float(along[-1])) / (dist + eps))),
    }


def target_trajectory_quality(text: str, target_id: str, start_pos, target_pos, start_time: float):
    """sim_common.py:420-477 (analyze_target_trajectory_quality)."""
    try:
        df = clean_positions(text)
    except NoValidRows:
        return default_trajectory_stats()
    df = df[df["time"] > start_time]
    if df.empty or len(df) < 2:
        return default_trajectory_stats()
    target = df[df["id"] == target_id]
    if target.empty or len(target) < 2:
        return default_trajectory_stats()
    return trajectory_metrics(target.sort_values("time").reset_index(drop=True), start_pos, target_pos)


def com_trajectory_quality(text: str, start_pos, target_pos, start_time: float):
    """sim_common.py:367-417 (analyze_trajectory_quality) and 480-489."""
    try:
        df = clean_positions(text)
    except NoValidRows:
        return default_trajectory_stats()
    df = df[df["time"] > start_time]
    if df.empty or len(df) < 2:
        return default_trajectory_stats()
    com = (df.groupby("time")[["x", "y", "z"]].mean().reset_index()
           .sort_values("time").reset_index(drop=True))
    if len(com) < 2:
        return default_trajectory_stats()
    return trajectory_metrics(com, start_pos, target_pos)


def total_mass(block_data: dict[str, str]) -> float:
    """sim_common.py:87-93, summed in block-table order."""
    return sum(V1_MASS[name] for name in block_data.values() if name != "Ballast")


def identity_fields(ident: dict[str, Any], block_data: dict[str, str]) -> dict[str, Any]:
    """Fields every 1.0 result carries before its metrics."""
    return {
        "category": ident["category"], "level": ident["level"], "model_name": ident["model_name"],
        "machine_id": ident["machine_id"], "round_suffix": ident["round_suffix"],
        "machine_bsg_path": ident["machine_bsg_path"], "machine_json_path": ident["machine_json_path"],
        "num_blocks": len(block_data), "total_mass": total_mass(block_data),
    }


# ------------------------------------------------------------- sim_transport.py
T_DISTANCE_THRESHOLD = 10.0                      # sim_transport.py:19
T_START = np.array([0.0, 0.0, 0.0])               # :20
T_TARGET = np.array([10.0, 10.0, 0.0])            # :21
T_START_TIME_SPEED = 2.0                          # :22
T_START_TIME_TRAJECTORY = 2.0                     # :23
T_WHEEL_DISTANCE_THRESHOLD = 1.0                  # :24
T_INITIAL_HEIGHT_THRESHOLD = 20.0                 # :25


def _tracking_target(df: pd.DataFrame) -> tuple[str, dict[str, Any]]:
    """sim_transport.py:157-211 (_determine_tracking_target)."""
    target_id = "ID_Ballast" if "ID_Ballast" in df["id"].unique() else "ID_1"
    target = df[df["id"] == target_id].copy()
    if target.empty:
        return target_id, {"max_distance_from_start": 0.0, "final_position": [0.0, 0.0, 0.0],
                           "initial_max_height": 0.0}
    target = target.sort_values("time").reset_index(drop=True)
    positions = target[["x", "y"]].values
    distances = np.linalg.norm(positions - T_START[:2], axis=1)
    return target_id, {"max_distance_from_start": float(np.max(distances)),
                       "final_position": positions[-1].tolist(),
                       "initial_max_height": float(target["z"][:int(T_START_TIME_SPEED)].max())}


def _wheel_consistency(df: pd.DataFrame, block_data: dict[str, str]) -> dict[str, Any]:
    """sim_transport.py:214-291 (_analyze_wheel_consistency)."""
    wheel_ids = [f"ID_{bid}" for bid, name in block_data.items() if "Wheel" in name]
    if len(wheel_ids) < 2:
        return {"one_piece": True, "wheel_count": len(wheel_ids), "max_distance_variation": 0.0}
    wheels = df[df["id"].isin(wheel_ids)].copy()
    if wheels.empty or len(wheels["id"].unique()) < 2:
        return {"one_piece": True, "wheel_count": len(wheel_ids), "max_distance_variation": 0.0}
    max_variation = 0.0
    for _, group in wheels.groupby("time"):
        if len(group) < 2:
            continue
        positions = {}
        for _, row in group.iterrows():
            positions[row["id"]] = np.array([row["x"], row["y"], row["z"]])
        names = list(positions.keys())
        distances = [np.linalg.norm(positions[names[i]] - positions[names[j]])
                     for i in range(len(names)) for j in range(i + 1, len(names))]
        if distances:
            max_variation = max(max_variation, float(np.std(distances)))
    return {"one_piece": max_variation <= T_WHEEL_DISTANCE_THRESHOLD,
            "wheel_count": len(wheel_ids), "max_distance_variation": max_variation}


def judge_transport(text: str, block_data: dict[str, str], ident: dict[str, Any]) -> dict[str, Any]:
    """sim_transport.py:27-154 (analyze_simulation_transport)."""
    df = clean_positions(text)
    target_id, target = _tracking_target(df)
    wheels = _wheel_consistency(df, block_data)
    motion = motion_metrics(text, T_START_TIME_SPEED)
    traj = target_trajectory_quality(text, target_id, T_START, T_TARGET, T_START_TIME_TRAJECTORY)
    distance = target["max_distance_from_start"]
    passed = distance > T_DISTANCE_THRESHOLD
    if ident["level"] == "soft":
        passed = passed and wheels["wheel_count"] >= 4
    initial = target["initial_max_height"]
    passed = passed and initial < T_INITIAL_HEIGHT_THRESHOLD
    hacked = bool(passed and wheels["wheel_count"] == 0)
    if initial > T_INITIAL_HEIGHT_THRESHOLD:
        hacked = True
    return {
        "pass": passed, "hacked": hacked, **identity_fields(ident, block_data),
        "tracking_target": target_id, "max_transport_distance": distance,
        "final_position": target["final_position"], "initial_max_height": initial,
        "one_piece": wheels["one_piece"], "wheel_count": wheels["wheel_count"],
        "max_wheel_distance_variation": wheels["max_distance_variation"],
        "max_height": motion["max_height"], "max_speed": motion["max_speed"],
        **{k: traj[k] for k in ("max_progress_ratio", "direction_consistency", "backtrack_penalty",
                                "lateral_deviation", "path_efficiency", "final_distance_ratio")},
        "hyperparameters": {
            "DISTANCE_THRESHOLD": T_DISTANCE_THRESHOLD, "START_TIME_SPEED": T_START_TIME_SPEED,
            "START_TIME_TRAJECTORY": T_START_TIME_TRAJECTORY, "START_POSITION": T_START.tolist(),
            "TARGET_POSITION": T_TARGET.tolist(), "WHEEL_DISTANCE_THRESHOLD": T_WHEEL_DISTANCE_THRESHOLD,
            "INITIAL_HEIGHT_THRESHOLD": T_INITIAL_HEIGHT_THRESHOLD},
    }


# --------------------------------------------------------------- sim_support.py
S_HEIGHT_THRESHOLD = 4.0           # sim_support.py:16
S_HOLD_THRESHOLD = 2.0             # :17
S_INITIAL_HEIGHT_THRESHOLD = 20.0  # :18


def default_ballast_stats() -> dict[str, Any]:
    """sim_support.py:265-275."""
    return {"max_weight": 0.0, "max_weight_at_threshold": 0.0, "min_height": 0.0,
            "hold_time_above_threshold": 0.0, "time_below_threshold": 0.0, "hacked": False,
            "initial_height_above_threshold": False}


def _split_ballast_rows(text: str) -> tuple[pd.DataFrame, pd.DataFrame] | None:
    """sim_support.py:117-164: ballast position rows and weight rows."""
    try:
        raw = robust_read(text, min_columns=2, validate_numeric_columns=None, skip_to_first_valid=False)
    except NoValidRows:
        return None
    ballast = raw[raw.iloc[:, 1] == "ID_Ballast"].copy()
    if ballast.empty:
        return None
    positions, weights = [], []
    for _, row in ballast.iterrows():
        if len(row) >= 5 and pd.notna(row.iloc[4]):
            positions.append({"time": float(row.iloc[0]), "id": row.iloc[1], "x": float(row.iloc[2]),
                              "z": float(row.iloc[3]), "y": float(row.iloc[4])})
        elif len(row) >= 3 and pd.notna(row.iloc[2]) and (len(row) < 4 or pd.isna(row.iloc[3])):
            weights.append({"time": float(row.iloc[0]), "id": row.iloc[1], "weight": float(row.iloc[2])})
    return pd.DataFrame(positions), pd.DataFrame(weights)


def ballast_stats(text: str) -> dict[str, Any]:
    """sim_support.py:106-262. 1.0 falls back to the defaults on any exception;
    the two cases that occur (no ballast rows, no weight rows) are handled here."""
    split = _split_ballast_rows(text)
    if split is None:
        return default_ballast_stats()
    pos, weight = split
    if pos.empty:
        return default_ballast_stats()
    if weight.empty:   # 1.0: weight_df.sort_values('time') raises KeyError -> defaults
        return default_ballast_stats()
    pos = pos.sort_values("time").reset_index(drop=True)
    weight = weight.sort_values("time").reset_index(drop=True)
    hacked = False
    initial_above = float(pos["z"].iloc[0]) > S_INITIAL_HEIGHT_THRESHOLD
    min_height = float(pos["z"].min())
    max_weight = float(weight["weight"].max())
    at_threshold = 0.0
    below = pos[pos["z"] < S_HEIGHT_THRESHOLD]["time"].values
    if len(below) > 0:
        before = weight[weight["time"] <= below[0]]
        if not before.empty:
            at_threshold = float(before.iloc[-1]["weight"])
    else:
        at_threshold = max_weight
        if min_height > 5.0:
            hacked = True
    hold, below_time = 0.0, 0.0
    above = pos["z"] >= S_HEIGHT_THRESHOLD
    if len(pos) > 1:
        diffs = pos["time"].diff().iloc[1:]
        hold = float(diffs[above.iloc[1:]].sum())
        below_time = float(diffs[~above.iloc[1:]].sum())
    return {"max_weight": max_weight, "max_weight_at_threshold": at_threshold, "min_height": min_height,
            "hold_time_above_threshold": hold, "time_below_threshold": below_time, "hacked": hacked,
            "initial_height_above_threshold": initial_above}


def judge_support(text: str, block_data: dict[str, str], ident: dict[str, Any]) -> dict[str, Any]:
    """sim_support.py:20-100 (analyze_simulation_support)."""
    stats = ballast_stats(text)
    passed = stats["hold_time_above_threshold"] >= S_HOLD_THRESHOLD and not stats["initial_height_above_threshold"]
    return {
        "pass": passed, **identity_fields(ident, block_data),
        "ballast_max_weight": stats["max_weight"],
        "ballast_max_weight_at_threshold": stats["max_weight_at_threshold"] if passed else 0.0,
        "ballast_min_height": stats["min_height"],
        "hold_time_above_threshold": stats["hold_time_above_threshold"],
        "time_below_threshold": stats["time_below_threshold"], "hacked": stats["hacked"],
        "hyperparameters": {"HEIGHT_THRESHOLD": S_HEIGHT_THRESHOLD, "HOLD_THRESHOLD": S_HOLD_THRESHOLD,
                            "INITIAL_HEIGHT_THRESHOLD": S_INITIAL_HEIGHT_THRESHOLD},
    }


# ------------------------------------------------------------------ sim_lift.py
L_HEIGHT_THRESHOLD = 30.0              # sim_lift.py:16
L_ELEVATION_CHANGE_THRESHOLD = 5.0     # :17
L_START = np.array([0.0, 0.0, 0.0])    # :18
L_TARGET = np.array([0.0, 0.0, 1700])  # :19
L_START_TIME_SPEED = 2.0               # :20
L_START_TIME_TRAJECTORY = 2.0          # :21
L_ACTIVE_THRUST = 13.76                # :198
L_INACTIVE_THRUST = 1.6                # :199


def water_cannon_counts(block_data: dict[str, str], text: str) -> dict[str, int]:
    """sim_lift.py:143-190 (_analyze_water_cannons): a cannon counts as heated
    if any of its rows says `true`."""
    cannons = {f"ID_{bid}": False for bid, name in block_data.items() if name == "Water Cannon"}
    if not cannons:
        return {"active": 0, "inactive": 0, "total": 0}
    df = robust_read(text, min_columns=3, validate_numeric_columns=None, skip_to_first_valid=False)
    rows = df[df.iloc[:, 1].isin(cannons.keys()) & df.iloc[:, 2].isin(["true"])].iloc[:, [1, 2]].drop_duplicates()
    active = len(rows[rows.iloc[:, 1].isin(["true"])])
    return {"active": active, "inactive": len(cannons) - active, "total": len(cannons)}


def judge_lift(text: str, block_data: dict[str, str], ident: dict[str, Any], *,
               success_rule: Literal["height", "ba1"] = "height") -> dict[str, Any]:
    """sim_lift.py:23-137 (analyze_simulation_lift), with a switch for Lv.2/Lv.3 success."""
    mass = total_mass(block_data)
    cannons = water_cannon_counts(block_data, text)
    force = cannons["active"] * L_ACTIVE_THRUST + cannons["inactive"] * L_INACTIVE_THRUST
    effective = force / mass
    motion = motion_metrics(text, L_START_TIME_SPEED)
    traj = com_trajectory_quality(text, L_START, L_TARGET, L_START_TIME_TRAJECTORY)
    if ident["level"] == "soft":
        passed = effective > 1.0
    else:
        passed = motion["max_height"] > L_HEIGHT_THRESHOLD
        if success_rule == "ba1":
            passed = passed and motion["elevation_change"] > L_ELEVATION_CHANGE_THRESHOLD
    return {
        "pass": passed, **identity_fields(ident, block_data),
        "total_propulsion_force": force, "effective_propulsion_force": effective,
        "water_cannon_active": cannons["active"], "water_cannon_inactive": cannons["inactive"],
        "max_height": motion["max_height"], "elevation_change": motion["elevation_change"],
        "initial_max_height": motion["initial_max_height"], "max_speed": motion["max_speed"],
        **{k: traj[k] for k in ("max_progress_ratio", "direction_consistency", "backtrack_penalty",
                                "lateral_deviation", "path_efficiency", "final_distance_ratio")},
        "hacked": bool(passed and effective < 1.0),
        "hyperparameters": {"HEIGHT_THRESHOLD": L_HEIGHT_THRESHOLD, "START_TIME_SPEED": L_START_TIME_SPEED,
                            "START_TIME_TRAJECTORY": L_START_TIME_TRAJECTORY,
                            "START_POSITION": L_START.tolist(), "TARGET_POSITION": L_TARGET.tolist()},
    }


JUDGES = {"transport": judge_transport, "support": judge_support, "lift": judge_lift}

# Metric reported in the paper for each category (1.0 paper, Table 2).
def headline_metric(category: str, level: str) -> str:
    if category == "transport":
        return "max_transport_distance"
    if category == "support":
        return "ballast_max_weight_at_threshold"
    return "effective_propulsion_force" if level == "soft" else "max_height"
