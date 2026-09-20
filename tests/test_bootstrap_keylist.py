"""KeyList priming must clean up even when a start acknowledgement is lost.

Run with: python -m unittest discover -s tests -p test_bootstrap_keylist.py
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "control"))

from besiege_cli import bootstrap
from besiege_cli.orchestrator import OrchestratorTimeoutError


class PrimeKeylistCleanupTests(unittest.TestCase):
    def setUp(self):
        self.orchestrator = Mock()
        self.orchestrator.send_command.side_effect = lambda command, **kwargs: command
        self.orchestrator.install_machine.return_value = "assembled_validation.bsg"
        for target, kwargs in (
            ("buildarena.validation_machine.inspect_registry", {}),
            (
                "buildarena.validation_machine.build_validation_machine",
                {"return_value": SimpleNamespace(bsg_path=Path("validation.bsg"))},
            ),
            ("besiege_cli.bootstrap.ensure_fresh_sandbox", {}),
            ("besiege_cli.bootstrap.PRIME_SIM_HOLD_SECONDS", {"new": 0.0}),
        ):
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def prime(self):
        return bootstrap._prime_keylist_cache(
            orchestrator=self.orchestrator, saved_dir=Path("unused"), timeout=1.0
        )

    def commands(self):
        return [call.args[0] for call in self.orchestrator.send_command.call_args_list]

    def assert_stopped(self):
        self.assertEqual(self.commands(), ["load_machine", "start_sim", "stop_sim"])
        self.orchestrator.wait_for_command_result.assert_any_call("stop_sim", timeout=1.0)
        self.orchestrator.wait_for_simulating.assert_any_call(False, timeout=1.0)

    def test_start_acknowledgement_timeout_still_stops(self):
        error = OrchestratorTimeoutError("start acknowledgement lost")

        def wait(sequence, **kwargs):
            if sequence == "start_sim":
                raise error

        self.orchestrator.wait_for_command_result.side_effect = wait
        with self.assertRaises(OrchestratorTimeoutError) as caught:
            self.prime()
        self.assertIs(caught.exception, error)
        self.assert_stopped()

    def test_simulating_state_timeout_still_stops(self):
        error = OrchestratorTimeoutError("simulating state not observed")
        self.orchestrator.wait_for_simulating.side_effect = [error, None]
        with self.assertRaises(OrchestratorTimeoutError) as caught:
            self.prime()
        self.assertIs(caught.exception, error)
        self.assert_stopped()

    def test_success_stops_before_returning(self):
        result = self.prime()
        self.assertEqual(result["bsg"], "validation.bsg")
        self.assert_stopped()

    def test_load_timeout_does_not_start_simulation(self):
        self.orchestrator.wait_for_command_result.side_effect = OrchestratorTimeoutError(
            "load acknowledgement lost"
        )
        with self.assertRaises(OrchestratorTimeoutError):
            self.prime()
        self.assertEqual(self.commands(), ["load_machine"])

    def test_stop_failure_is_reported(self):
        error = OrchestratorTimeoutError("stop acknowledgement lost")

        def wait(sequence, **kwargs):
            if sequence == "stop_sim":
                raise error

        self.orchestrator.wait_for_command_result.side_effect = wait
        with self.assertRaises(OrchestratorTimeoutError) as caught:
            self.prime()
        self.assertIs(caught.exception, error)
        self.assertEqual(self.commands(), ["load_machine", "start_sim", "stop_sim"])


if __name__ == "__main__":
    unittest.main()
