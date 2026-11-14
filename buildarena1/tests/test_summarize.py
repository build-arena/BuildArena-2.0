"""Summary over a runs root: latest attempt only, and the sample outcome rule
(README section 5)."""

from __future__ import annotations

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
        self.assertIn("| b | m | transport_soft | - | 1/5 | ", text)          # no recorded run condition here
        self.assertIn("| 1 | max_transport_distance | 11.00 |", text)
        self.assertIn("incomplete, scored, scored, scored, not_evaluated", text)
        self.assertIn("result.json success differs from the rule above for: m/transport_soft: s03", text)


if __name__ == "__main__":
    unittest.main()
