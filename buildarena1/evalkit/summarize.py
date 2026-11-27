"""Aggregate sample results: per batch, model and cell, the success count,
metrics, block counts, tokens and requests.

    python -m buildarena1.evalkit.summarize <runs root> [--batch NAME] [--json out.json] [--markdown out.md]

Each sample is read from its latest eval attempt (largest attempt directory
name). If that attempt has no result.json it is reported as incomplete; an
older attempt's result is never used in its place.

Sample outcome (README section 5):
a trial counts when it passed and is not marked hacked; a sample succeeds when
at least one trial counts; a successful sample's metric is the largest among
its counted trials; a failed sample's best metric over its scored trials is
listed apart and kept out of the success mean; samples with a passing but
hacked trial are counted on their own. The outcome is recomputed here from the
per-trial records in result.json ("status", "pass", "hacked", "metric").
The separate 1.0 columns use pass_ba1/hacked_ba1. For lift Lv.2/Lv.3, a missing
rule in an old result means ba1; it is labeled as such and never grouped with
height runs. Samples without a recorded config or any trials have an unknown
rule; they join the cell's only result group when unambiguous.

Samples evaluated despite a flag (a build session that ended without
close_machine_lifespan, which is evaluated on its last saved machine as in 1.0, a final reply without its two sections,
a build check outside the gate that failed, a session that did not end
"completed") are listed per cell from result.json's "flags" and
"build_session_status".

Token counts come from the build session's usage.json plus every control-agent
round (result.json's control_usage, or the trial_*/control_round.json files of
an incomplete attempt). usage.json is expected to carry ``input_tokens``,
``output_tokens`` and ``requests``; a missing field is reported as missing
rather than guessed.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

from . import legacy as L
from .config import validate_lift_success_rule

USAGE_FIELDS = ("input_tokens", "output_tokens", "requests")
LIFT_RULE_CELLS = ("lift_medium", "lift_hard")
SCOPE_NOTE = ("Simplified protocol: one session per sample with BuildArena-2.0 code and a few samples per cell; "
              "not comparable one-to-one with the BuildArena 1.0 multi-agent results (64 samples per cell).")


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _identity(sample: Path) -> dict:
    return {"batch": sample.parents[2].name, "model": sample.parents[1].name, "cell": sample.parent.name,
            "sample": sample.name}


def latest_results(root: Path, batch: str | None = None) -> list[dict]:
    """One record per sample, from its latest attempt directory only."""
    out = []
    for sample in sorted(root.glob("*/*/*/s[0-9][0-9]")):
        if batch is not None and sample.parents[2].name != batch:
            continue
        attempts = sorted(p for p in (sample / "eval").glob("*") if p.is_dir())
        if attempts and (attempts[-1] / "result.json").is_file():
            result = _read(attempts[-1] / "result.json")
            _restore_verdicts(result, attempts[-1])
            out.append(result)
            continue
        rounds = [_read(p) for p in sorted(attempts[-1].glob("trial_*/control_round.json"))] if attempts else []
        state = _read(attempts[-1] / "state.json") if attempts else None
        state_result = (state or {}).get("result") or {}
        out.append({**_identity(sample), "attempt": attempts[-1].name if attempts else None,
                    **({"config": state_result["config"]} if "config" in state_result else {}),
                    "status": "incomplete" if attempts else "not_evaluated",
                    "metric": {"name": None, "value": None}, "trials": [],
                    "build_usage": _read(sample / "usage.json"),
                    "control_usage": [u for r in rounds if isinstance(r, dict) for u in _round_usages(r)]})
    return out


def _round_usages(record: dict) -> list:
    """Every control call of a round: a round re-asked after an unparsable reply
    keeps each call in "attempts" (evalkit.sample.control_round_record)."""
    if isinstance(record.get("attempts"), list):
        return [a.get("usage") for a in record["attempts"] if isinstance(a, dict)]
    return [record.get("usage")]


def _trial_rule(result: dict, trial: dict | None = None) -> str:
    if result.get("config") is None and not result.get("trials") and trial is None:
        return "unknown"
    rule = (result.get("config") or {}).get("lift_success_rule", "ba1")
    validate_lift_success_rule(rule)
    rule = (trial or {}).get("lift_success_rule", rule)
    validate_lift_success_rule(rule)
    return rule


def _rules(result: dict) -> str:
    if result["cell"] not in LIFT_RULE_CELLS:
        return "-"
    return "+".join(sorted({_trial_rule(result, t) for t in result.get("trials") or []}
                           or {_trial_rule(result)}))


def _legacy_rule(result: dict) -> bool:
    if result["cell"] not in LIFT_RULE_CELLS or _rules(result) == "unknown":
        return False
    return "lift_success_rule" not in (result.get("config") or {}) and (
        not result.get("trials") or any("lift_success_rule" not in t for t in result["trials"]))


def _restore_verdicts(result: dict, attempt: Path) -> None:
    """Recover pre-dual-verdict height summaries from their full scores, in memory."""
    if result["cell"] not in LIFT_RULE_CELLS:
        return
    for t in result.get("trials") or []:
        if (t.get("status") == "scored" and _trial_rule(result, t) == "height"
                and not {"pass_ba1", "hacked_ba1"} <= t.keys()):
            score = _read(attempt / f"trial_{t['trial']}" / "score.json")
            if score is not None:
                from .judge import rule_results
                t.update(rule_results(score, "height"))
                t["elevation_change"] = score["elevation_change"]


def _v1_verdict(result: dict, trial: dict) -> tuple[bool, bool]:
    rule = _trial_rule(result, trial)
    if {"pass_ba1", "hacked_ba1"} <= trial.keys():
        return bool(trial["pass_ba1"]), bool(trial["hacked_ba1"])
    if rule == "ba1" or result["cell"] not in LIFT_RULE_CELLS:
        return bool(trial.get("pass")), bool(trial.get("hacked"))
    raise ValueError(f"{result.get('sample', '?')} trial {trial.get('trial', '?')}: height result lacks "
                     "pass_ba1/hacked_ba1; restore its score.json or re-score the saved telemetry.")


def outcome(result: dict) -> dict:
    """The sample rule for one sample, from its trial records."""
    scored = [t for t in result.get("trials") or [] if t.get("status") == "scored"]
    counted = [t for t in scored if t.get("pass") and not t.get("hacked")]
    metrics = lambda ts: [t["metric"] for t in ts if t.get("metric") is not None]
    success = bool(counted)
    v1 = [_v1_verdict(result, t) for t in scored]
    return {"success": success,
            "success_metric": max(metrics(counted), default=None) if success else None,
            "failed_best": None if success else max(metrics(scored), default=None),
            "hacked_pass": any(t.get("pass") and t.get("hacked") for t in scored),
            "configured_pass": any(t.get("pass") for t in scored),
            "v1_hacked_pass": any(passed and hacked for passed, hacked in v1),
            # 1.0's own sample result (sim_support.py:82, sim_common): any passing trial counts,
            # hacked or not; the metric is the trial's raw metric (0 for a trial that did not pass)
            "v1_pass": any(passed for passed, _ in v1),
            "v1_metric": max(metrics(scored), default=0.0)}


def condition(result: dict) -> str:
    """The run fingerprint id(s) the sample's trials ran under (sample.check_fingerprint)."""
    ids = sorted({(t.get("run_fingerprint") or {}).get("id") for t in result.get("trials") or []
                  if t.get("status") in ("scored", "score_failed", "run_failed")} - {None})
    return "+".join(ids) if ids else None


def _usage_total(result: dict) -> dict:
    total, missing = {f: 0 for f in USAGE_FIELDS}, []
    parts = [("build", result.get("build_usage"))] + [("control", u) for u in result.get("control_usage") or []]
    incomplete = []
    for label, usage in parts:
        if not isinstance(usage, dict):
            missing.append(f"{label}: no usage")
            continue
        if usage.get("complete") is False:   # known part only (usage.json without its final event, a failed call)
            incomplete.append(label)
        for f in USAGE_FIELDS:
            if isinstance(usage.get(f), (int, float)):
                total[f] += usage[f]
            else:
                missing.append(f"{label}: {f}")
    if result.get("status") == "incomplete":
        missing.append("attempt incomplete: control usage only from rounds on disk")
    return {**total, "missing": missing, "incomplete": incomplete}


def _cell_order(cell: str) -> tuple[int, int]:
    category, _, level = cell.partition("_")
    return (L.CATEGORIES.index(category) if category in L.CATEGORIES else 9,
            L.LEVELS.index(level) if level in L.LEVELS else 9)


def summarize(results: list[dict]) -> list[dict]:
    # samples of one cell evaluated under different run conditions are not merged
    # For lift Lv.2/Lv.3 a known rule must also match. A missing fingerprint can
    # join a unique condition. Samples without config or trials are kept unknown
    # and join only a unique group for the cell, without inventing a legacy rule.
    recorded: dict[tuple[str, str, str, str], set] = {}
    for r in results:
        if condition(r) is not None:
            recorded.setdefault((r["batch"], r["model"], r["cell"], _rules(r)), set()).add(condition(r))
    groups: dict[tuple[str, str, str, str, str], list[dict]] = {}
    unknown = []
    for r in results:
        key = (r["batch"], r["model"], r["cell"], _rules(r))
        if key[3] == "unknown":
            unknown.append(r)
            continue
        conds = recorded.get(key, set())
        cond = condition(r) or (next(iter(conds)) if len(conds) == 1 else "unrecorded" if conds else "-")
        groups.setdefault((*key, cond), []).append(r)
    split: dict[tuple[str, str, str], set] = {}
    for batch, model, cell, rule, cond in groups:
        split.setdefault((batch, model, cell), set()).add((rule, cond))
    for r in unknown:
        cell_key = (r["batch"], r["model"], r["cell"])
        candidates = [key for key in groups if key[:3] == cell_key and key[3] != "unknown"]
        key = candidates[0] if len(candidates) == 1 else (*cell_key, "unknown", "unrecorded" if candidates else "-")
        groups.setdefault(key, []).append(r)
    rows = []
    for (batch, model, cell, rule, cond), items in sorted(groups.items(),
                                                     key=lambda kv: (kv[0][0], kv[0][1], _cell_order(kv[0][2]), kv[0][3:])):
        items = sorted(items, key=lambda r: r["sample"])
        usages = [_usage_total(r) for r in items]
        outs = [outcome(r) for r in items]
        wins = [o["success_metric"] for o in outs if o["success"]]
        rows.append({
            "batch": batch, "model": model, "cell": cell, "condition": cond, "lift_success_rule": rule,
            "legacy_lift_rule_samples": [r["sample"] for r in items if _legacy_rule(r)],
            "unknown_lift_rule_samples": [r["sample"] for r in items if _rules(r) == "unknown"],
            "cell_split_by_condition": len(split.get((batch, model, cell), ())) > 1, "samples": len(items),
            "v1_passes": sum(1 for o in outs if o["v1_pass"]),
            "configured_passes": sum(1 for o in outs if o["configured_pass"]),
            "successes": sum(1 for o in outs if o["success"]),
            "v1_hacked_pass_samples": sum(1 for o in outs if o["v1_hacked_pass"]),
            "configured_hacked_pass_samples": sum(1 for o in outs if o["hacked_pass"]),
            # Compatibility alias, always the configured rule's count.
            "hacked_pass_samples": sum(1 for o in outs if o["hacked_pass"]),
            "incomplete_samples": sum(1 for r in items if r["status"] in ("incomplete", "not_evaluated")),
            "metric": next((r["metric"]["name"] for r in items if r["metric"]["name"]), None),
            "success_metric_per_sample": [o["success_metric"] for o in outs],
            "success_metric_mean": statistics.fmean(wins) if wins else None,
            # 1.0's support metric is 0 for a failed sample (sim_support.py:82) and v7 Table 2
            # averages it over all samples; given for support next to the success-only mean.
            # It is taken from 1.0's raw per-trial metric, so a passing trial marked hacked counts too.
            "metric_mean_fail_as_0": (statistics.fmean([o["v1_metric"] for r, o in zip(items, outs)
                                                        if r["status"] not in ("incomplete", "not_evaluated")] or [0.0])
                                      if cell.startswith("support_") else None),
            "failed_best_per_sample": [o["failed_best"] for o in outs],
            "status_per_sample": [r["status"] for r in items],
            "result_success_disagrees": [r["sample"] for r, o in zip(items, outs)
                                         if "success" in r and r["status"] not in ("incomplete", "not_evaluated")
                                         and bool(r["success"]) != o["success"]],
            "blocks_per_sample": [r.get("num_blocks") for r in items],
            "support_protocols": sorted({json.dumps(r["support_protocol"], sort_keys=True)
                                         for r in items if isinstance(r.get("support_protocol"), dict)}),
            "flagged_samples": [{"sample": r["sample"], "flags": list(r.get("flags") or []),
                                 "build_session_status": r.get("build_session_status")}
                                for r in items if r.get("flags")],
            **{f: sum(u[f] for u in usages) for f in USAGE_FIELDS},
            "usage_missing": sorted({m for u in usages for m in u["missing"]}),
            # samples whose counted usage is a lower bound: some part was marked complete: false
            "usage_incomplete": [{"sample": r["sample"], "parts": u["incomplete"]}
                                 for r, u in zip(items, usages) if u["incomplete"]],
        })
    return rows


def _fmt(value) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}" if isinstance(value, float) else str(value)


def _tok(row: dict, field: str) -> str:
    """A usage total, marked as a lower bound when some part of it is incomplete."""
    return (">=" if row.get("usage_incomplete") else "") + str(row[field])


def markdown(rows: list[dict]) -> str:
    lines = [SCOPE_NOTE, "",
             "Success: at least one trial passed the configured rule without being marked hacked. Success metric: largest such trial. "
             "Failed best: best scored trial of a failed sample, kept out of the success mean.", "",
             "Configured pass and 1.0 pass: samples with a passing trial under the respective rule, hacked included. "
             "The two hacked pass columns count passing trials marked hacked under each rule, once per sample. "
             "1.0 uses pass_ba1/hacked_ba1 (the recorded verdicts for legacy ba1 results). Condition: the run "
             "fingerprint id (eval_fingerprint.json of the batch). Tokens and requests are those of each sample's "
             "scored attempt (its latest), build session plus control calls.", "",
             "| batch | model | cell | condition | lift rule | success | configured pass | 1.0 pass | configured hacked pass | 1.0 hacked pass | metric | success metric (mean) | "
             "success metric per sample | failed best per sample | status | blocks | in tok (scored attempt) | "
             "out tok (scored attempt) | requests (scored attempt) |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['batch']} | {r['model']} | {r['cell']} | {r.get('condition', '-')} | "
                     f"{r['lift_success_rule']} | {r['successes']}/{r['samples']} | "
                     f"{r['configured_passes']}/{r['samples']} | {r['v1_passes']}/{r['samples']} | "
                     f"{r['configured_hacked_pass_samples']} | {r['v1_hacked_pass_samples']} | "
                     f"{r['metric'] or '-'} | {_fmt(r['success_metric_mean'])} | "
                     f"{', '.join(_fmt(v) for v in r['success_metric_per_sample'])} | "
                     f"{', '.join(_fmt(v) for v in r['failed_best_per_sample'])} | "
                     f"{', '.join(r['status_per_sample'])} | "
                     f"{', '.join(_fmt(v) for v in r['blocks_per_sample'])} | {_tok(r, 'input_tokens')} | "
                     f"{_tok(r, 'output_tokens')} | {_tok(r, 'requests')} |")
    notes = [f"{r['model']}/{r['cell']}: {', '.join(r['usage_missing'])}" for r in rows if r["usage_missing"]]
    if notes:
        lines += ["", "Usage fields missing: " + "; ".join(notes)]
    partial = [f"{r['model']}/{r['cell']}/{u['sample']} ({', '.join(u['parts'])})" for r in rows for u in r.get("usage_incomplete") or []]
    if partial:
        lines += ["", "Usage incomplete (known part counted; the tokens and requests of these cells are lower bounds): "
                  + "; ".join(partial)]
    for r in rows:
        if r["metric_mean_fail_as_0"] is not None:
            lines += ["", f"{r['model']}/{r['cell']}: mean of {r['metric'] or 'metric'} over successful samples only "
                          f"{_fmt(r['success_metric_mean'])} ({r['successes']} successes); over all "
                          f"evaluated samples with 1.0's raw metric, failures as 0 and hacked passes counted "
                          f"(1.0 / paper Table 2) {_fmt(r['metric_mean_fail_as_0'])} ({r['v1_passes']} passes by 1.0's judge)."]
        for text in r["support_protocols"]:
            p = json.loads(text)
            lines += ["", f"{r['model']}/{r['cell']}: support protocol {p['name']}: {p['metric']}; "
                          f"{p['mass_applied']}; {p['hold_rule']}; one run per mass, {p['run_seconds']:g} s each; "
                          f"metric resolution about {round((p['metric_resolution_ratio'] - 1) * 100)} %."]
    splits = sorted({(r["batch"], r["model"], r["cell"]) for r in rows if r.get("cell_split_by_condition")})
    if splits:
        lines += ["", "Cells split because their samples ran under different conditions (game, 2.0 code, plugin, "
                  "evalkit code or judging config; compare the batches' eval_fingerprint.json and each trial's "
                  "run_fingerprint): " + "; ".join(
                      f"{b}/{m}/{c}: " + ", ".join(f"{r['condition']}/{r['lift_success_rule']} ({r['samples']})" for r in rows
                                                    if (r["batch"], r["model"], r["cell"]) == (b, m, c)
                                                    and r["lift_success_rule"] != "unknown")
                      for b, m, c in splits)]
    legacy = [f"{r['model']}/{r['cell']}: {', '.join(r['legacy_lift_rule_samples'])}" for r in rows
              if r["legacy_lift_rule_samples"]]
    if legacy:
        lines += ["", "Legacy results missing lift_success_rule are interpreted as ba1 (BuildArena 1.0): "
                  + "; ".join(legacy)]
    unknown = [f"{r['model']}/{r['cell']}: {', '.join(r['unknown_lift_rule_samples'])}" for r in rows
               if r["unknown_lift_rule_samples"]]
    if unknown:
        lines += ["", "Lift rule unknown (no recorded configuration or trials; included in the cell's only result "
                  "group when unambiguous): " + "; ".join(unknown)]
    flagged = [f"{r['model']}/{r['cell']}/{f['sample']}: {', '.join(f['flags'])}"
               f" (build session {f['build_session_status'] or 'unknown'})" for r in rows for f in r["flagged_samples"]]
    if flagged:
        lines += ["", "Evaluated with flags:"] + [f"- {line}" for line in flagged]
    odd = [f"{r['model']}/{r['cell']}: {', '.join(r['result_success_disagrees'])}" for r in rows
           if r["result_success_disagrees"]]
    if odd:
        lines += ["", "result.json success differs from the rule above for: " + "; ".join(odd)]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", type=Path)
    parser.add_argument("--batch")
    parser.add_argument("--json", type=Path)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args(argv)
    rows = summarize(latest_results(args.root, args.batch))
    text = markdown(rows)
    if args.json:
        args.json.write_text(json.dumps({"note": SCOPE_NOTE, "rows": rows}, indent=2), encoding="utf-8")
    if args.markdown:
        args.markdown.write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
