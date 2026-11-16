"""Summary over a runs root: latest attempt only, and the sample outcome rule
(README section 5)."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from buildarena1.evalkit import summarize

USAGE = {"input_tokens": 100, "output_tokens": 50, "requests": 3}
ROUND = {"input_tokens": 10, "output_tokens": 5, "requests": 1}


def trial(n, passed, hacked, metric):
    return {"trial": n, "status": "scored", "pass": passed, "hacked": hacked, "metric": metric}


def result(name, trials, success):
    return {"batch": "b", "model": "m", "cell": "transport_soft", "sample": name, "status": "scored",
            "success": success, "metric": {"name": "max_transport_distance", "value": None},
            "trials": trials, "build_usage": USAGE, "control_usage": [ROUND] * len(trials), "num_blocks": 7}


class Summary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(tempfile.mkdtemp(prefix="summary_"))
        cell = root / "b" / "m" / "transport_soft"

        def sample(name, attempts):
            d = cell / name
            d.mkdir(parents=True)
            (d / "usage.json").write_text(json.dumps(USAGE))
            for attempt, value in attempts:
                a = d / "eval" / attempt
                a.mkdir(parents=True)
                if isinstance(value, dict):
                    (a / "result.json").write_text(json.dumps(value))
                elif value == "rounds":
                    (a / "trial_1").mkdir()
                    (a / "trial_1" / "control_round.json").write_text(json.dumps({"usage": ROUND}))
        old_win = result("s01", [trial(1, True, False, 12.0)], True)
        sample("s01", [("20260101T000000Z", old_win), ("20260101T010000Z", "rounds")])
        sample("s02", [("a1", result("s02", [trial(1, False, True, 250.0), trial(2, True, False, 11.0)], True))])
        sample("s03", [("a1", result("s03", [trial(1, True, True, 40.0)], True))])
        sample("s04", [("a1", result("s04", [trial(1, False, False, 9.0), trial(2, False, False, 7.0),
                                               {"trial": 3, "status": "run_failed"}], False))])
        sample("s05", [])
        cls.results = summarize.latest_results(root)
        cls.row = summarize.summarize(cls.results)[0]

    def test_latest_attempt_without_result_is_incomplete(self):
        first = self.results[0]
        self.assertEqual((first["sample"], first["status"], first["attempt"]), ("s01", "incomplete", "20260101T010000Z"))
        self.assertEqual(self.row["status_per_sample"], ["incomplete", "scored", "scored", "scored", "not_evaluated"])
        self.assertIsNone(self.row["success_metric_per_sample"][0])
        self.assertIn("attempt incomplete: control usage only from rounds on disk", self.row["usage_missing"])

    def test_outcome_rule(self):
        r = self.row
        self.assertEqual((r["samples"], r["successes"], r["hacked_pass_samples"], r["incomplete_samples"]), (5, 1, 1, 2))
        self.assertEqual(r["success_metric_per_sample"], [None, 11.0, None, None, None])
        self.assertEqual(r["success_metric_mean"], 11.0)
        self.assertEqual(r["failed_best_per_sample"], [None, None, 40.0, 9.0, None])
        self.assertEqual(r["result_success_disagrees"], ["s03"])

    def test_tokens_include_incomplete_rounds(self):
        # build usage x4 (s05 has usage.json too), rounds: s01 1 on disk, s02 2, s03 1, s04 3
        r = self.row
        self.assertEqual((r["input_tokens"], r["output_tokens"], r["requests"]),
                         (5 * 100 + 7 * 10, 5 * 50 + 7 * 5, 5 * 3 + 7 * 1))

    def test_markdown(self):
        text = summarize.markdown([self.row])
        self.assertTrue(text.startswith(summarize.SCOPE_NOTE))
        self.assertIn("| b | m | transport_soft | - | - | 1/5 | 2/5 | 2/5 | ", text)
        self.assertIn("| 1 | 1 | max_transport_distance | 11.00 |", text)
        self.assertIn("incomplete, scored, scored, scored, not_evaluated", text)
        self.assertIn("result.json success differs from the rule above for: m/transport_soft: s03", text)


class LiftRuleGrouping(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="summary_rules_")
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)

    @staticmethod
    def record(name, cell, rule, fingerprint=None):
        r = result(name, [trial(1, True, False, 40.0)], True)
        r["cell"] = cell
        if rule is not None:
            r["config"] = {"lift_success_rule": rule}
            r["trials"][0].update({"lift_success_rule": rule, "pass_ba1": True, "hacked_ba1": False})
        if fingerprint is not None:
            r["trials"][0]["run_fingerprint"] = {"id": fingerprint}
        return r

    def test_unevaluated_lift_sample_is_unknown_and_joins_the_only_group(self):
        for cell in ("lift_medium", "lift_hard"):
            for fingerprint in (None, "fp"):
                with self.subTest(cell=cell, fingerprint=fingerprint):
                    root = self.root / f"{cell}_{fingerprint}"
                    pending = root / "b" / "m" / cell / "s01"
                    pending.mkdir(parents=True)
                    out = pending.parent / "s02" / "eval" / "a1"
                    out.mkdir(parents=True)
                    (out / "result.json").write_text(json.dumps(self.record("s02", cell, "height", fingerprint)))
                    loaded = summarize.latest_results(root)
                    self.assertEqual(loaded[0]["status"], "not_evaluated")
                    self.assertNotIn("config", loaded[0])
                    alone = summarize.summarize(loaded[:1])[0]
                    self.assertEqual(alone["lift_success_rule"], "unknown")
                    self.assertEqual(alone["legacy_lift_rule_samples"], [])
                    self.assertFalse(alone["cell_split_by_condition"])
                    rows = summarize.summarize(loaded)
                    self.assertEqual(rows, summarize.summarize(list(reversed(loaded))))
                    self.assertEqual(len(rows), 1)
                    row = rows[0]
                    self.assertEqual((row["lift_success_rule"], row["samples"], row["successes"]), ("height", 2, 1))
                    self.assertEqual(row["incomplete_samples"], 1)
                    self.assertEqual(row["unknown_lift_rule_samples"], ["s01"])
                    self.assertEqual(row["legacy_lift_rule_samples"], [])
                    self.assertFalse(row["cell_split_by_condition"])
                    text = summarize.markdown(rows)
                    self.assertNotIn("Legacy results", text)
                    self.assertNotIn("Cells split", text)
                    self.assertIn("Lift rule unknown", text)

    def test_incomplete_attempt_uses_only_its_own_recorded_config(self):
        sdir = self.root / "b" / "m" / "lift_medium" / "s01"
        older, latest = (sdir / "eval" / name for name in ("a1", "a2"))
        older.mkdir(parents=True)
        latest.mkdir()
        (older / "result.json").write_text(json.dumps(self.record("s01", "lift_medium", "ba1")))
        loaded = summarize.latest_results(self.root)
        self.assertEqual(loaded[0]["status"], "incomplete")
        self.assertNotIn("config", loaded[0])
        self.assertEqual(summarize.summarize(loaded)[0]["lift_success_rule"], "unknown")
        for config, rule, legacy in (({}, "ba1", ["s01"]), ({"lift_success_rule": "height"}, "height", [])):
            with self.subTest(config=config):
                (latest / "state.json").write_text(json.dumps({"result": {"config": config}}))
                row = summarize.summarize(summarize.latest_results(self.root))[0]
                self.assertEqual(row["lift_success_rule"], rule)
                self.assertEqual(row["legacy_lift_rule_samples"], legacy)
                self.assertEqual(row["unknown_lift_rule_samples"], [])

    def test_both_lift_cells_split_known_rules_and_keep_ambiguous_samples_unknown(self):
        for cell in ("lift_medium", "lift_hard"):
            with self.subTest(cell=cell):
                old = self.record("s01", cell, None)
                new = self.record("s02", cell, "height")
                pending = self.record("s03", cell, None)
                pending.update({"status": "not_evaluated", "trials": []})
                rows = summarize.summarize([old, new, pending])
                self.assertEqual([(r["lift_success_rule"], r["samples"]) for r in rows],
                                 [("ba1", 1), ("height", 1), ("unknown", 1)])
                self.assertEqual([r["legacy_lift_rule_samples"] for r in rows], [["s01"], [], []])
                self.assertEqual(rows[2]["unknown_lift_rule_samples"], ["s03"])
                self.assertTrue(all(r["cell_split_by_condition"] for r in rows))
                split_note = next(line for line in summarize.markdown(rows).splitlines() if line.startswith("Cells split"))
                self.assertNotIn("unknown", split_note)

    def test_unaffected_cells_merge_rules_and_legacy_results_by_condition(self):
        cells = ("lift_soft", "transport_soft", "transport_medium", "transport_hard",
                 "support_soft", "support_medium", "support_hard")
        for cell in cells:
            for fingerprints in ((None, None), ("fp", "fp"), (None, "fp")):
                with self.subTest(cell=cell, fingerprints=fingerprints):
                    old = self.record("s01", cell, None, fingerprints[0])
                    new = self.record("s02", cell, "height", fingerprints[1])
                    ba1 = self.record("s03", cell, "ba1", fingerprints[1])
                    rows = summarize.summarize([old, new, ba1])
                    self.assertEqual(len(rows), 1)
                    row = rows[0]
                    self.assertEqual((row["lift_success_rule"], row["samples"], row["successes"]), ("-", 3, 3))
                    self.assertEqual(row["legacy_lift_rule_samples"], [])
                    self.assertFalse(row["cell_split_by_condition"])
                    self.assertNotIn("Legacy results", summarize.markdown(rows))
                    # Distinct conditions still split these cells, independent of the lift rule.
                    different = copy.deepcopy(new)
                    different["trials"][0]["run_fingerprint"] = {"id": "different"}
                    same = copy.deepcopy(new)
                    same["trials"][0]["run_fingerprint"] = {"id": "same"}
                    split = summarize.summarize([same, different])
                    self.assertEqual(len(split), 2)
                    self.assertTrue(all(r["cell_split_by_condition"] for r in split))


if __name__ == "__main__":
    unittest.main()
