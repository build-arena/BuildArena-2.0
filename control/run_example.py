"""Rebuild a final example from MCP history, then run its automatic controller."""
from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stdout
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import runpy
import sys
import uuid
import xml.etree.ElementTree as ET
from tqdm import tqdm

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "control")]
sys.dont_write_bytecode = True

EXAMPLES = {
    "rocket_orbit_return": ("LONE ORB", 10, 0.0, 1800),
    "transforming_car_aerobatics": ("BARREN EXPANSE", 25, 3.0, 600),
    "shuttle_booster_recovery": ("LONE ORB", 25, 0.0, 1700),
}


@dataclass
class PreparedExample:
    name: str
    bsg: Path
    controller: Path
    run_dir: Path
    environment: dict[str, str]
    clear_prefix: str


def _shuttle_settings(tree, guids, run_dir, folder):
    """Apply final native settings, without changing geometry, and rebind calibration."""
    import copy
    constants = runpy.run_path(str(folder / "runtime_config.py"))
    hardware = copy.deepcopy(constants["HARDWARE"])
    old_guids = hardware["guids"]
    if set(guids) != set(old_guids):
        raise ValueError("Rebuilt shuttle Build IDs differ from final hardware roles.")
    rebase = {old: guids[build_id] for build_id, old in old_guids.items()}
    hardware["guids"] = guids
    blocks = {b.get("guid"): b for b in tree.findall("Blocks/Block")}
    for name in ("foldR", "foldL", "foldFrontR", "foldFrontL"):
        block = blocks[guids[hardware["parts"][name]]]
        if block.get("id") != "28":
            raise ValueError(f"{name} is not a steering hinge")
        data = block.find("Data")
        for node in list(data):
            if node.get("key") in ("bmt-uselimits", "bmt-limits"):
                data.remove(node)
        ET.SubElement(data, "Boolean", key="bmt-uselimits").text = "True"
        limits = ET.SubElement(data, "SingleArray", key="bmt-limits")
        for _ in range(2):
            ET.SubElement(limits, "Single").text = "90"
    for name in ("bladeL0", "bladeL2", "bladeL3"):
        data = blocks[guids[hardware["parts"][name]]].find("Data")
        for node in list(data):
            if node.get("key") == "flipped":
                data.remove(node)
        ET.SubElement(data, "Boolean", key="flipped").text = "True"
    calibration = copy.deepcopy(constants["SERVO_CALIBRATION"])
    for surface in calibration["surfaces"].values():
        for key in ("servo_guid", "blade_guid", "base_guid"):
            surface[key] = rebase[surface[key]]
    calibration["binding_rebase"] = "Rebound by MCP Build ID after single-history replay."
    for name, value in (("dry_hardware.json", hardware), ("servo_calibration.json", calibration)):
        (run_dir / name).write_text(json.dumps(value, indent=2), encoding="utf-8")


def prepare_example(name: str, run_dir: Path) -> PreparedExample:
    from buildarena.build import Machine
    from buildarena.history_json import prepare_history_json
    from buildarena.paths import load_project_env
    load_project_env()
    if name not in EXAMPLES:
        raise ValueError(f"Unknown example: {name}")
    folder = REPO / "control" / "examples" / name
    history = folder / "machine.json"
    run_dir = Path(run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    source_hash = hashlib.sha256(history.read_bytes()).hexdigest()
    print(f"Rebuilding {name} from its MCP history; log: {run_dir / 'rebuild.log'}", flush=True)
    with (run_dir / "rebuild.log").open("w", encoding="utf-8") as log, redirect_stdout(log):
        machine = Machine(name=name, save_dir=str(run_dir), write_full_history=False)
        path = prepare_history_json(history_json=history, machine=machine)
        operations = json.loads(path.read_text(encoding="utf-8"))
        unknown = {op["op"] for op in operations} - machine.operations.keys()
        if unknown:
            raise ValueError(f"Unsupported history operations: {sorted(unknown)}")
        machine.output_sequence_path = path
        with tqdm(operations, desc=f"Rebuild {name}", unit="op", file=sys.stderr,
                  dynamic_ncols=True, ascii=True) as progress:
            machine.rebuild_from_history(progress)
        if len(machine.operation_history) != len(operations):
            raise ValueError("History replay rejected operations; inspect rebuild.log.")
        tree = ET.fromstring(machine.to_xml())
    # Stable per-history GUIDs allow a saved camera variant to survive a fresh
    # rebuild. Build IDs, not block ordering or old machine GUIDs, are the key.
    guids = {str(i): str(uuid.uuid5(uuid.NAMESPACE_URL, f"buildarena:{source_hash}:{i}"))
             for i in machine.blocks}
    rebase = {block.guid: guids[str(i)] for i, block in machine.blocks.items()}
    for node in tree.iter():
        for key, value in list(node.attrib.items()):
            if value in rebase:
                node.set(key, rebase[value])
        if node.text and node.text.strip() in rebase:
            node.text = rebase[node.text.strip()]
    if name == "shuttle_booster_recovery":
        _shuttle_settings(tree, guids, run_dir, folder)
    bsg = run_dir / f"{name}.bsg"
    ET.indent(tree, space="    ")
    ET.ElementTree(tree).write(bsg, encoding="utf-8", xml_declaration=True)
    settings = runpy.run_path(str(folder / "mission.py"))
    (run_dir / "rebuild_manifest.json").write_text(json.dumps({
        "history": str(history), "history_sha256": source_hash,
        "operations": len(operations), "blocks": len(guids), "build_id_to_guid": guids,
        "bsg": str(bsg), "bsg_sha256": hashlib.sha256(bsg.read_bytes()).hexdigest(),
        "native_settings_applied": name == "shuttle_booster_recovery",
    }, indent=2), encoding="utf-8")
    print(f"Rebuilt {len(guids)} blocks; source directory contains no generated outputs.", flush=True)
    return PreparedExample(name, bsg, folder / "controller.py", run_dir,
                           settings["ENVIRONMENT"], settings["CLEAR_PREFIX"])


@contextmanager
def _controller_environment(example):
    before = dict(os.environ)
    try:
        for key in list(os.environ):
            if key.startswith(example.clear_prefix):
                del os.environ[key]
        os.environ.update(example.environment)
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
        os.environ["PYTHONUTF8"] = "1"
        yield
    finally:
        os.environ.clear()
        os.environ.update(before)


def execute_prepared(example: PreparedExample, *, besiege_data=None, catalog=None,
                     launch_timeout=120, timeout=60, camera_bsg=None,
                     edit_before_start=False, tail_seconds=20,
                     native_view=True) -> int:
    from besiege_cli.cli import build_parser
    from besiege_cli.run import cmd_run
    scene, hz, pre_hold, controller_timeout = EXAMPLES[example.name]
    args = ["run", "--bsg", str(example.bsg), "--controller", str(example.controller),
            "--run-dir", str(example.run_dir), "--sandbox", scene,
            "--telemetry-hz", str(hz), "--telemetry-profile", "full", "--recorder-hz", "25",
            "--pre-controller-hold", str(pre_hold), "--post-completion-hold", str(tail_seconds),
            "--controller-timeout", str(controller_timeout), "--launch-timeout", str(launch_timeout),
            "--timeout", str(timeout)]
    if besiege_data:
        args += ["--besiege-data", str(besiege_data)]
    if catalog:
        args += ["--catalog", str(catalog)]
    if example.name == "transforming_car_aerobatics":
        args += ["--reload-sandbox"]
    if edit_before_start:
        args += ["--edit-before-start"]
        camera_bsg = camera_bsg or example.name + "_camera"
    if camera_bsg:
        args += ["--camera-bsg", str(camera_bsg)]
    if native_view:
        from buildarena.paths import get_besiege_data_path
        data = Path(besiege_data) if besiege_data else get_besiege_data_path()
        settings = data / "Mods" / "BuildArenaMultiView" / "views.json"
        if settings.exists():
            previous = settings.read_bytes()
            config = json.loads(previous.decode("utf-8-sig"))
            config.update(enabled=False, time_scale=1, views=[])
            (example.run_dir / "previous_multiview_settings.json").write_bytes(previous)
            from controller_sdk.protocol import atomic_write_json
            atomic_write_json(settings, config)
    with _controller_environment(example):
        return cmd_run(build_parser().parse_args(args))


def main(default_example=None):
    parser = argparse.ArgumentParser(description=__doc__)
    if default_example is None:
        parser.add_argument("example", choices=EXAMPLES)
    parser.add_argument("--edit-before-start", action="store_true")
    parser.add_argument("--camera-bsg")
    parser.add_argument("--prepare-only", action="store_true", help="Rebuild and validate history without touching the game.")
    parser.add_argument("--run-dir", type=Path, help="New/empty output directory within datacache.")
    parser.add_argument("--besiege-data", type=Path)
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--tail-seconds", type=int, default=20)
    args = parser.parse_args()
    if not 0 <= args.tail_seconds <= 3600:
        parser.error("tail-seconds must be 0..3600")
    name = default_example or args.example
    # Uses the existing D-backed recording output directory on this workstation,
    # and a normal repository datacache directory on a fresh checkout.
    output = args.run_dir or REPO / "datacache" / "manual_cases" / (datetime.now().strftime("%Y%m%d_%H%M%S_%f") + "_" + name)
    # Include existing D-drive junction targets without allowing outputs in
    # tracked example/source directories via --run-dir.
    roots = [REPO / "datacache", REPO / "datacache/manual_cases",
             REPO / "datacache/control_experiments"]
    if not any(output.resolve().is_relative_to(root.resolve()) for root in roots):
        parser.error("run-dir must be inside repository datacache (including its D-drive junctions).")
    if output.exists() and any(output.iterdir()):
        parser.error("Use a new/empty run directory; existing recordings are never overwritten.")
    example = prepare_example(name, output)
    if args.prepare_only:
        print(f"Prepared only: {example.bsg}")
        return 0
    return execute_prepared(example, besiege_data=args.besiege_data, catalog=args.catalog,
                            edit_before_start=args.edit_before_start, camera_bsg=args.camera_bsg,
                            tail_seconds=args.tail_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
