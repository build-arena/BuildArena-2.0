from pathlib import Path
import sys
import os
import unittest
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from buildarena.build import Block
from buildarena.control_descriptor_loader import load_control_semantics
from buildarena.definitions_loader import load_runtime_blocks
from buildarena.mesh_loader import load_block_mesh
from buildarena.validation_machine import inspect_dlc_block_ids, inspect_registry


REGISTRY = ROOT / "blocks" / "block_registry.generated.toml"


class FuelPumpV191Tests(unittest.TestCase):
    def test_registered_as_space_flight_block(self):
        blocks = load_runtime_blocks(registry_path=REGISTRY)
        pump = blocks["Fuel Pump"]

        self.assertEqual(pump["id"], 103)
        self.assertEqual(pump["type"], "basic")
        self.assertEqual(pump["mesh_key"], "FuelPump")
        self.assertFalse(pump["locomotion"])
        self.assertIn(103, inspect_dlc_block_ids(registry_path=REGISTRY)["space-flight"])
        self.assertEqual(
            inspect_registry(registry_path=REGISTRY).category_counts["space-flight"],
            13,
        )

    def test_control_is_addressable_by_toolkit_keylist(self):
        semantics = load_control_semantics(block_id=103)

        self.assertIsNotNone(semantics)
        self.assertEqual(semantics.keylist_indices, {"PumpKey": 0})
        self.assertEqual(semantics.aliases, {"pump": "PumpKey"})
        self.assertIn("flow-speed", semantics.sliders)

    def test_bsg_payload_matches_besiege_191_mapper_keys(self):
        pump = Block.__new__(Block)
        pump.type = "basic"
        pump.spin = None
        pump.shoot = None
        pump.locomotion = False
        pump.data = (
            '<StringArray key="pump"><String>Y</String></StringArray>'
            '<Single key="flow-speed">20</Single>'
            '<Boolean key="toggle-mode">False</Boolean>'
        )

        payload = pump._render_data_payload()
        self.assertIn(
            '<StringArray key="pump"><String>Y</String></StringArray>',
            payload,
        )
        self.assertIn('<Single key="flow-speed">20</Single>', payload)
        self.assertIn('<Boolean key="toggle-mode">False</Boolean>', payload)

    def test_proxy_mesh_loads_without_game_assets(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BESIEGE_DATA_PATH", None)
            mesh = load_block_mesh("FuelPump", registry_path=REGISTRY)

        np.testing.assert_allclose(mesh.extents, [0.91, 1.07, 1.31])


if __name__ == "__main__":
    unittest.main()
