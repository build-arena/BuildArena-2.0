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

USAGE_FIELDS = ("input_tokens", "output_tokens", "requests")
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
            out.append(_read(attempts[-1] / "result.json"))
            continue
        rounds = [_read(p) for p in sorted(attempts[-1].glob("trial_*/control_round.json"))] if attempts else []
        out.append({**_identity(sample), "attempt": attempts[-1].name if attempts else None,
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


def outcome(result: dict) -> dict:
    """The sample rule for one sample, from its trial records."""
    scored = [t for t in result.get("trials") or [] if t.get("status") == "scored"]
    counted = [t for t in scored if t.get("pass") and not t.get("hacked")]
    metrics = lambda ts: [t["metric"] for t in ts if t.get("metric") is not None]
    success = bool(counted)
    return {"success": success,
            "success_metric": max(metrics(counted), default=None) if success else None,
            "failed_best": None if success else max(metrics(scored), default=None),
            "hacked_pass": any(t.get("pass") and t.get("hacked") for t in scored),
            # 1.0's own sample result (sim_support.py:82, sim_common): any passing trial counts,
            # hacked or not; the metric is the trial's raw metric (0 for a trial that did not pass)
            "v1_pass": any(t.get("pass") for t in scored),
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
    # a sample with no recorded condition (not run, or run before fingerprints) joins its
    # cell's only condition; a cell with two or more recorded conditions is split
    recorded: dict[tuple[str, str, str], set] = {}
    for r in results:
        if condition(r) is not None:
            recorded.setdefault((r["batch"], r["model"], r["cell"]), set()).add(condition(r))
    groups: dict[tuple[str, str, str, str], list[dict]] = {}
    for r in results:
        key = (r["batch"], r["model"], r["cell"])
        conds = recorded.get(key, set())
        cond = condition(r) or (next(iter(conds)) if len(conds) == 1 else "unrecorded" if conds else "-")
        groups.setdefault((*key, cond), []).append(r)
    split = {key: conds for key, conds in recorded.items()}
    rows = []
    for (batch, model, cell, cond), items in sorted(groups.items(),
                                                     key=lambda kv: (kv[0][0], kv[0][1], _cell_order(kv[0][2]), kv[0][3])):
        items = sorted(items, key=lambda r: r["sample"])
        usages = [_usage_total(r) for r in items]
        outs = [outcome(r) for r in items]
        wins = [o["success_metric"] for o in outs if o["success"]]
        rows.append({
            "batch": batch, "model": model, "cell": cell, "condition": cond,
            "cell_split_by_condition": len(split.get((batch, model, cell), ())) > 1, "samples": len(items),
            "v1_passes": sum(1 for o in outs if o["v1_pass"]),
            "successes": sum(1 for o in outs if o["success"]),
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
             "Success: at least one trial passed without being marked hacked. Success metric: largest such trial. "
             "Failed best: best scored trial of a failed sample, kept out of the success mean.", "",
             "1.0 pass: samples with a passing trial by 1.0's judge, hacked included. Condition: the run "
             "fingerprint id (eval_fingerprint.json of the batch). Tokens and requests are those of each sample's "
             "scored attempt (its latest), build session plus control calls.", "",
             "| batch | model | cell | condition | success | 1.0 pass | hacked pass | metric | success metric (mean) | "
             "success metric per sample | failed best per sample | status | blocks | in tok (scored attempt) | "
             "out tok (scored attempt) | requests (scored attempt) |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['batch']} | {r['model']} | {r['cell']} | {r.get('condition', '-')} | "
                     f"{r['successes']}/{r['samples']} | {r.get('v1_passes', '-')}/{r['samples']} | "
                     f"{r['hacked_pass_samples']} | {r['metric'] or '-'} | {_fmt(r['success_metric_mean'])} | "
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
                      f"{b}/{m}/{c}: " + ", ".join(f"{r['condition']} ({r['samples']})" for r in rows
                                                    if (r["batch"], r["model"], r["cell"]) == (b, m, c))
                      for b, m, c in splits)]
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
