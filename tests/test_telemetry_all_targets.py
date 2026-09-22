from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
CONTROL_ROOT = ROOT / "control"
for path in (ROOT, CONTROL_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from besiege_cli.machine import (  # noqa: E402
    configure_telemetry_bsg,
    prepare_machine_bsg,
)


SOURCE_XML = """<?xml version="1.0" encoding="utf-8"?>
<Machine>
  <Data />
  <Blocks>
    <Block id="0" guid="11111111-1111-1111-1111-111111111111"><Data /></Block>
    <Block id="1" guid="22222222-2222-2222-2222-222222222222"><Data /></Block>
  </Blocks>
</Machine>
"""


def telemetry_targets(path: Path) -> str:
    root = ET.parse(path).getroot()
    node = root.find("./Data/String[@key='telemetry.target_guids']")
    if node is None:
        raise AssertionError("prepared BSG has no telemetry.target_guids")
    return node.text or ""


class TelemetryAllTargetTests(unittest.TestCase):
    def test_prepare_writes_explicit_guids_instead_of_wildcard(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.bsg"
            output = Path(folder) / "prepared.bsg"
            source.write_text(SOURCE_XML, encoding="utf-8")

            prepare_machine_bsg(source, output, run_id="test-run")

            self.assertEqual(
                telemetry_targets(output),
                "11111111-1111-1111-1111-111111111111;"
                "22222222-2222-2222-2222-222222222222",
            )

    def test_configure_all_targets_writes_explicit_guids(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.bsg"
            output = Path(folder) / "configured.bsg"
            source.write_text(SOURCE_XML, encoding="utf-8")

            report = configure_telemetry_bsg(
                source,
                target_guids=(),
                output_basename="test",
                output_bsg=output,
            )

            self.assertEqual(report["targets"], "all")
            self.assertNotIn("*", telemetry_targets(output))
            self.assertEqual(len(telemetry_targets(output).split(";")), 2)


if __name__ == "__main__":
    unittest.main()
