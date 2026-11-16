"""Offline regression tests for rule provenance, legacy files and summaries.

Prepared files and telemetry are synthetic. The real run-trial, scoring,
fingerprint and finalize paths run without a game, rebuild data or a 1.0 clone.
"""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from unittest.mock import patch

from . import trajgen
from .test_lift_success_rule import EARLY_PEAK, IDENT, NAMES, TIMES
from buildarena1.evalkit import cli, sample, stepwise, summarize, version
from buildarena1.evalkit.config import EvalConfig
from buildarena1.evalkit.judge import score_trial, v1_block_data
from buildarena1.evalkit.legacy_log import rows_to_csv_text
from buildarena1.evalkit.scoring import JUDGES, judge_lift


class RuleRecords(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="lift_records_")
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)

    @staticmethod
    def scenario(level="medium", heated=True):
        heights = dict(zip(TIMES, EARLY_PEAK))
        return trajgen.Scenario("lift", level, NAMES,
                                lambda lid, t: (0.0, heights[t], 0.0, heated and lid in ("2", "3")),
                                times=TIMES)

    def prepared(self, rule="height", *, legacy=False, level="medium", heated=True, batch="b"):
        sc = self.scenario(level, heated)
        sdir = self.root / batch / "model" / f"lift_{level}" / "s01"
        out = sdir / "eval" / "a1"
        prep = out / "prepared"
        prep.mkdir(parents=True)
        (prep / "machine.bsg").write_text("synthetic; never passed to a game")
        manifest = {"blocks": sc.blocks(), "bsg": str(prep / "machine.bsg"), "history": "synthetic.json"}
        (prep / "block_map.json").write_text(json.dumps(manifest))
        (prep / "control_meta.json").write_text(json.dumps({"actions": {}, "block_ids": ["1", "2", "3", "4"]}))
        (prep / "machine_state.txt").write_text("synthetic")
        cfg = EvalConfig(lift_success_rule=rule).to_json()
        if legacy:
            del cfg["lift_success_rule"]
        result = {"batch": batch, "model": "model", "cell": f"lift_{level}", "sample": "s01", "config": cfg,
                  "metric": {"name": "max_height", "value": None}, "trials": [], "control_usage": []}
        state = {"schema": stepwise.STATE_SCHEMA, "result": result, "rounds": [], "next": "run_trial"}
        (out / "state.json").write_text(json.dumps(state))

        class TelemetryRunner:
            calls = 0

            def run(self, plan):
                self.calls += 1
                plan.run_dir.mkdir()
                path = plan.run_dir / "telemetry.csv"
                trajgen.write(sc, path)
                return sample.RunOutput(path)

        return sdir, out, TelemetryRunner()

    def finish(self, sdir, game):
        response = stepwise.run_trial_step(sdir, "a1", runner=game)
        self.assertEqual((response["status"], response["next"]), ("scored", "finalize"))
        return stepwise.finalize(sdir, "a1")

    def test_invalid_rules_rejected_at_all_entry_points(self):
        path = self.root / "cfg.json"
        for value in ("BA1", "ba1 ", "bogus", "", None, True, 1, [], {}):
            with self.subTest(value=value):
                raw = {"lift_success_rule": value}
                path.write_text(json.dumps(raw))
                for construct in (lambda: EvalConfig(**raw), lambda: cli._config(str(path)),
                                  lambda: stepwise._cfg({"result": {"config": raw}}),
                                  lambda: judge_lift("", {}, IDENT, success_rule=value)):
                    with self.assertRaisesRegex(ValueError, "lift_success_rule must be"):
                        construct()

    def test_missing_file_rule_is_legacy_but_new_config_defaults_to_height(self):
        path = self.root / "cfg.json"
        path.write_text("{}")
        with self.assertWarnsRegex(UserWarning, "missing.*'ba1'"):
            cfg = cli._config(str(path))
        self.assertEqual(cfg.lift_success_rule, "ba1")
        self.assertEqual(cli._config(None).lift_success_rule, "height")
        self.assertEqual(EvalConfig().lift_success_rule, "height")

    def test_partial_config_without_rule_uses_ba1(self):
        path = self.root / "partial.json"
        path.write_text(json.dumps({"quantize_like_lua": False}))
        with self.assertWarnsRegex(UserWarning, "missing.*'ba1'"):
            cfg = cli._config(str(path))
        self.assertEqual(cfg.lift_success_rule, "ba1")
        self.assertFalse(cfg.quantize_like_lua)
        self.assertEqual(cfg.lift_tracking, EvalConfig().lift_tracking)

    def test_prepare_cli_reads_legacy_partial_and_explicit_config_files(self):
        host = self.root / "host.json"
        host.write_text(json.dumps({key: str(self.root / key)
                                    for key in ("v1_root", "collider_dump", "besiege_data")}))
        old = EvalConfig(quantize_like_lua=False).to_json()
        del old["lift_success_rule"]
        cases = (("old", old, "ba1"), ("partial", {"quantize_like_lua": False}, "ba1"),
                 ("explicit", {"quantize_like_lua": False, "lift_success_rule": "height"}, "height"),
                 ("default", None, "height"))
        for name, raw, rule in cases:
            with self.subTest(config=name):
                sdir = self.root / name / "model" / "lift_medium" / "s01"
                sdir.mkdir(parents=True)
                argv = [sys.executable, "-m", "buildarena1.evalkit.cli", "--host", str(host),
                        "prepare", str(sdir), "--attempt", "a1"]
                if raw is not None:
                    config_file = self.root / f"{name}.json"
                    config_file.write_text(json.dumps(raw))
                    argv += ["--config", str(config_file)]
                # No machine: real argparse/config/prepare/persistence, no rebuild or game data.
                proc = subprocess.run(argv, cwd=cli.REPO_ROOT, capture_output=True, text=True, timeout=30)
                self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
                answer = json.loads(proc.stdout)
                self.assertEqual((answer["status"], answer["next"]), ("no_machine", "done"))
                if rule == "ba1":
                    self.assertIn("lift_success_rule is missing; using 'ba1'", proc.stderr)
                else:
                    self.assertEqual(proc.stderr, "")
                out = sdir / "eval" / "a1"
                result = json.loads((out / "result.json").read_text())
                state = json.loads((out / "state.json").read_text())
                self.assertEqual(state["result"], result)
                self.assertEqual(result["config"]["lift_success_rule"], rule)
                self.assertEqual(result["config"]["quantize_like_lua"], raw is None)

    def test_old_positional_config_arguments_keep_their_meaning(self):
        names = [f.name for f in fields(EvalConfig)]
        self.assertEqual(names[-1], "lift_success_rule")
        cfg = EvalConfig(True, "print", "fire_burning", "all", 0, False, "task")
        self.assertFalse(cfg.truncate_at_duration)
        self.assertEqual(cfg.feedback_axis_labels, "task")
        self.assertEqual(cfg.lift_success_rule, "height")

    def test_direct_judge_and_registry_default_to_ba1(self):
        sc = self.scenario()
        path = self.root / "telemetry.csv"
        trajgen.write(sc, path)
        score, rows = score_trial(path, sc.blocks(), "lift", "medium", IDENT, EvalConfig(lift_success_rule="ba1"))
        expected = {k: v for k, v in score.items() if k != "v2"}
        for judge in (judge_lift, JUDGES["lift"]):
            actual = judge(rows_to_csv_text(rows), v1_block_data(sc.blocks()),
                           {**IDENT, "category": "lift", "level": "medium"})
            self.assertEqual(actual, expected)
            self.assertFalse(actual["pass"])

    def test_stepwise_records_both_rules_and_metrics(self):
        for level in ("medium", "hard"):
            for rule in ("height", "ba1"):
                with self.subTest(level=level, rule=rule):
                    sdir, out, game = self.prepared(rule, level=level, batch=rule)
                    result = self.finish(sdir, game)
                    self.assertEqual(game.calls, 1)
                    trial = json.loads((out / "trial_1" / "trial.json").read_text())
                    score = json.loads((out / "trial_1" / "score.json").read_text())
                    self.assertEqual(trial["score"], score)
                    summary = result["trials"][0]
                    for record in (score["v2"], summary):
                        self.assertEqual(record["lift_success_rule"], rule)
                        self.assertTrue(record["pass_height"])
                        self.assertFalse(record["pass_ba1"])
                        self.assertFalse(record["hacked_height"])
                        self.assertFalse(record["hacked_ba1"])
                    self.assertEqual(score["elevation_change"], -150.0)
                    self.assertEqual(summary["elevation_change"], -150.0)
                    self.assertEqual(summary["pass"], rule == "height")
                    self.assertEqual(result["success"], rule == "height")
                    row = summarize.summarize([result])[0]
                    self.assertEqual(row["v1_passes"], 0)
                    self.assertEqual(row["configured_passes"], int(rule == "height"))
                    frozen = json.loads((sdir.parents[2] / sample.FINGERPRINT_NAME).read_text())
                    self.assertEqual(frozen["fingerprint"]["config"]["lift_success_rule"], rule)

    def test_hacked_counts_use_the_respective_pass_rule(self):
        sdir, _, game = self.prepared(heated=False)
        result = self.finish(sdir, game)
        t = result["trials"][0]
        self.assertEqual((t["pass_height"], t["hacked_height"], t["pass_ba1"], t["hacked_ba1"]),
                         (True, True, False, False))
        row = summarize.summarize([result])[0]
        self.assertEqual((row["configured_passes"], row["v1_passes"], row["successes"]), (1, 0, 0))
        self.assertEqual((row["configured_hacked_pass_samples"], row["v1_hacked_pass_samples"]), (1, 0))
        text = summarize.markdown([row])
        self.assertIn("| configured pass | 1.0 pass | configured hacked pass | 1.0 hacked pass |", text)

    def test_legacy_state_is_annotated_and_runs_ba1_without_a_fingerprint(self):
        sdir, out, game = self.prepared(legacy=True)
        with self.assertWarnsRegex(UserWarning, "missing.*'ba1'"):
            result = self.finish(sdir, game)
        self.assertFalse(result["success"])
        self.assertEqual(result["config"]["lift_success_rule"], "ba1")
        self.assertIn("legacy configuration", result["config_notes"][0])
        state = json.loads((out / "state.json").read_text())
        self.assertEqual(state["result"], result)
        frozen = json.loads((sdir.parents[2] / sample.FINGERPRINT_NAME).read_text())
        self.assertEqual(frozen["fingerprint"]["config"]["lift_success_rule"], "ba1")

    def test_legacy_state_is_annotated_even_when_only_finalizing(self):
        sdir, out, _ = self.prepared(legacy=True)
        state = json.loads((out / "state.json").read_text())
        state["next"] = "finalize"
        (out / "state.json").write_text(json.dumps(state))
        with self.assertWarnsRegex(UserWarning, "missing.*'ba1'"):
            result = stepwise.finalize(sdir, "a1")
        self.assertEqual(result["config"]["lift_success_rule"], "ba1")
        self.assertTrue(result["config_notes"])

    def test_fingerprint_distinguishes_rules_and_accepts_identical_config(self):
        sdir, out, game = self.prepared()
        cfg = EvalConfig()
        ctx = sample.Ctx(sample.sample_info(sdir), cfg, out, None, game)
        first = sample.check_fingerprint(ctx)
        self.assertEqual(first, sample.check_fingerprint(ctx))
        ba1 = EvalConfig(lift_success_rule="ba1")
        self.assertNotEqual(sample._fingerprint_id(sample.run_fingerprint(game, cfg)),
                            sample._fingerprint_id(sample.run_fingerprint(game, ba1)))
        ctx.cfg = ba1
        with self.assertRaisesRegex(sample.FingerprintMismatch, "config.lift_success_rule"):
            sample.check_fingerprint(ctx)
        self.assertEqual(game.calls, 0)

    def test_legacy_frozen_fingerprint_is_not_rewritten_or_silently_accepted(self):
        sdir, out, game = self.prepared(legacy=True)
        fp = sample.run_fingerprint(game, EvalConfig(lift_success_rule="ba1"))
        del fp["config"]["lift_success_rule"]
        path = sdir.parents[2] / sample.FINGERPRINT_NAME
        before = json.dumps({"id": sample._fingerprint_id(fp), "fingerprint": fp})
        path.write_text(before)
        state_before = (out / "state.json").read_bytes()
        with self.assertWarnsRegex(UserWarning, "missing.*'ba1'"):
            with self.assertRaisesRegex(sample.FingerprintMismatch, "config.lift_success_rule"):
                stepwise.run_trial_step(sdir, "a1", runner=game)
        self.assertEqual(game.calls, 0)
        self.assertEqual(path.read_text(), before)
        self.assertEqual((out / "state.json").read_bytes(), state_before)

    def test_fingerprint_rejects_keys_missing_from_either_side_including_null(self):
        sdir, out, game = self.prepared()
        fp = sample.run_fingerprint(game, EvalConfig())
        path = sdir.parents[2] / sample.FINGERPRINT_NAME
        state = (out / "state.json").read_bytes()
        for part, key, value in [(part, "removed_field_probe", value)
                                 for part in ("game", "evalkit", "config") for value in (None, "probe")] + [
                                     ("config", "lift_success_rule", "height")]:
            for missing_from in ("frozen", "current"):
                with self.subTest(part=part, key=key, value=value, missing_from=missing_from):
                    frozen, current = copy.deepcopy(fp), copy.deepcopy(fp)
                    frozen[part][key] = current[part][key] = value
                    del (frozen if missing_from == "frozen" else current)[part][key]
                    before = json.dumps({"id": sample._fingerprint_id(frozen), "fingerprint": frozen})
                    path.write_text(before)
                    with patch.object(sample, "run_fingerprint", return_value=current):
                        with self.assertRaises(sample.FingerprintMismatch) as caught:
                            stepwise.run_trial_step(sdir, "a1", runner=game)
                    self.assertIn(f"{part}.{key}", str(caught.exception))
                    self.assertIn("<missing>", str(caught.exception))
                    self.assertEqual(path.read_text(), before)
                    self.assertEqual((out / "state.json").read_bytes(), state)
        self.assertEqual(game.calls, 0)

    def test_code_edits_in_plain_deployed_and_git_copies_change_fingerprints(self):
        for source in ("unknown", "deployed", "git"):
            with self.subTest(source=source):
                root = self.root / f"copy_{source}"
                code_dir = root / version.CODE_DIR
                shutil.copytree(Path(sample.__file__).parent, code_dir,
                                ignore=shutil.ignore_patterns("__pycache__"))
                self.assertFalse((root / ".git").exists())
                self.assertFalse((root / version.MANIFEST).exists())
                if source == "deployed":
                    (root / version.MANIFEST).write_text(json.dumps({
                        "commit": "abc", "dirty": False, "deployed_at": "t",
                        "files": {p.relative_to(root).as_posix(): version.sha256_file(p)
                                  for p in code_dir.rglob("*.py")}}))
                elif source == "git":
                    if not shutil.which("git"):
                        self.skipTest("git executable needed for the checkout case")
                    git = ["git", "-C", str(root), "-c", "core.hooksPath=/dev/null",
                           "-c", "commit.gpgsign=false", "-c", "user.name=Evalkit test",
                           "-c", "user.email=evalkit@example.invalid"]
                    for args in (["init", "-q"], ["add", version.CODE_DIR], ["commit", "-qm", "Test fixture"]):
                        subprocess.run(git + args, check=True, capture_output=True, text=True)
                before_code = version.code_version(root)
                self.assertEqual(before_code["source"], source)
                self.assertEqual(before_code["content_sha256"], version.content_sha256(root))
                sdir, out, game = self.prepared(batch=source)
                ctx = sample.Ctx(sample.sample_info(sdir), EvalConfig(), out, None, game)
                path = sdir.parents[2] / sample.FINGERPRINT_NAME
                with patch.object(sample, "code_version", side_effect=lambda: version.code_version(root)):
                    first = sample.check_fingerprint(ctx)
                    self.assertEqual(sample.check_fingerprint(ctx), first)
                    frozen = path.read_bytes()
                    state = (out / "state.json").read_bytes()
                    previous_id, previous_digest = first["id"], before_code["content_sha256"]
                    for edit in (1, 2):
                        with (code_dir / "summarize.py").open("a") as handle:
                            handle.write(f"\n# Content fingerprint regression {edit}\n")
                        now = sample.run_fingerprint(game, ctx.cfg)
                        current_id = sample._fingerprint_id(now)
                        self.assertNotEqual(current_id, previous_id)
                        self.assertNotEqual(now["evalkit"]["content_sha256"], previous_digest)
                        with self.assertRaisesRegex(sample.FingerprintMismatch, "evalkit.content_sha256"):
                            stepwise.run_trial_step(sdir, "a1", runner=game)
                        self.assertEqual(path.read_bytes(), frozen)
                        self.assertEqual((out / "state.json").read_bytes(), state)
                        previous_id, previous_digest = current_id, now["evalkit"]["content_sha256"]
                self.assertEqual(game.calls, 0)

    def test_combine_without_config_reads_the_score_rule(self):
        for rule in ("height", "ba1"):
            with self.subTest(rule=rule):
                sdir, out, game = self.prepared(rule, batch=rule)
                expected = self.finish(sdir, game)
                trial = json.loads((out / "trial_1" / "trial.json").read_text())
                combined = sample.combine(sample.sample_info(sdir), [trial])
                self.assertEqual(combined["trials"], expected["trials"])
                self.assertEqual(combined["trials"][0]["lift_success_rule"], rule)
                self.assertEqual(combined["success"], rule == "height")

    def test_combine_without_config_rejects_a_score_with_no_rule(self):
        sdir, out, game = self.prepared()
        expected = self.finish(sdir, game)
        trial = json.loads((out / "trial_1" / "trial.json").read_text())
        for v2 in ({}, None):
            with self.subTest(v2=v2):
                if v2 is None:
                    trial["score"].pop("v2", None)
                else:
                    trial["score"]["v2"] = v2
                with self.assertRaisesRegex(ValueError, "missing v2.lift_success_rule"):
                    sample.combine(sample.sample_info(sdir), [trial])
                explicit = sample.combine(sample.sample_info(sdir), [trial], cfg=EvalConfig())
                self.assertEqual(explicit["trials"], expected["trials"])

    def test_summary_does_not_merge_unfingerprinted_legacy_with_height(self):
        sdir, _, game = self.prepared()
        new = self.finish(sdir, game)
        old = copy.deepcopy(new)
        old["sample"] = "s02"
        del old["config"]["lift_success_rule"]
        old["success"] = False
        old["trials"] = [{"trial": 1, "status": "scored", "pass": False, "hacked": False, "metric": 400.0}]
        rows = summarize.summarize([old, new])
        self.assertEqual([(r["lift_success_rule"], r["samples"]) for r in rows], [("ba1", 1), ("height", 1)])
        self.assertTrue(all(r["cell_split_by_condition"] for r in rows))
        self.assertEqual(rows[0]["legacy_lift_rule_samples"], ["s02"])
        self.assertIn("missing lift_success_rule are interpreted as ba1", summarize.markdown(rows))
        # Even without any fingerprints, the rule still splits the cell.
        del new["trials"][0]["run_fingerprint"]
        self.assertEqual(len(summarize.summarize([old, new])), 2)

    def test_legacy_ba1_pass_and_hacked_are_still_counted(self):
        r = {"cell": "lift_medium", "trials": [
            {"status": "scored", "pass": True, "hacked": True, "metric": 400.0}]}
        o = summarize.outcome(r)
        self.assertTrue(o["v1_pass"])
        self.assertTrue(o["v1_hacked_pass"])
        self.assertFalse(o["success"])

    def test_pre_dual_height_result_recovers_from_score_file_or_errors(self):
        sdir, out, game = self.prepared(heated=False)
        result = self.finish(sdir, game)
        t = result["trials"][0]
        for key in ("pass_ba1", "hacked_ba1", "pass_height", "hacked_height", "lift_success_rule", "elevation_change"):
            del t[key]
        score_path = out / "trial_1" / "score.json"
        score = json.loads(score_path.read_text())
        score["v2"] = {}
        score_path.write_text(json.dumps(score))
        result_path = out / "result.json"
        result_path.write_text(json.dumps(result))
        before = result_path.read_bytes()
        loaded = summarize.latest_results(self.root)
        row = summarize.summarize(loaded)[0]
        self.assertEqual((row["v1_passes"], row["v1_hacked_pass_samples"], row["configured_passes"]), (0, 0, 1))
        self.assertEqual(loaded[0]["trials"][0]["elevation_change"], -150.0)
        self.assertEqual(result_path.read_bytes(), before)
        score_path.unlink()
        with self.assertRaisesRegex(ValueError, "restore its score.json or re-score"):
            summarize.summarize(summarize.latest_results(self.root))


if __name__ == "__main__":
    unittest.main()
