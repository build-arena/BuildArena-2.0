"""An explicit save-and-confirm camera editing step, before run preparation."""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
import xml.etree.ElementTree as ET

from controller_sdk.protocol import atomic_write_json
from .paths import saved_machines_dir
from .preflight import PreflightError

CAMERA_BLOCK_ID = "58"  # blocks/block_authoring.toml: Camera Block
# User-selected absolute tolerance for every numeric saved field.
NUMERIC_SAVE_TOLERANCE = 1e-3


def camera_path(value: str | None, *, source: Path, besiege_data: Path) -> Path:
    path = Path(value or (source.stem + "_camera.bsg")).expanduser()
    if path.suffix.lower() != ".bsg":
        path = Path(str(path) + ".bsg")
    # A bare save name is what Besiege's Save As dialog accepts.
    if not path.is_absolute() and path.parent == Path(".") and not any(c in (value or "") for c in ("/", "\\")):
        path = saved_machines_dir(besiege_data) / path
    path = path.resolve()
    if path == source.resolve():
        raise PreflightError("Camera BSG must be a separate Save As file, not the original BSG.")
    return path


def _blocks(data: bytes):
    root = ET.fromstring(data)
    blocks = root.findall("./Blocks/Block")
    if not blocks:
        raise PreflightError("Camera editing requires a BSG with blocks and stable GUIDs.")
    result = {}
    for block in blocks:
        guid = block.get("guid", "").lower()
        if not guid or guid in result:
            raise PreflightError("Missing or duplicate block GUID; save a valid machine before continuing.")
        result[guid] = block
    return result


def _same_value(left: str, right: str) -> bool:
    if left.strip() == right.strip():
        return True
    try:
        a, b = float(left), float(right)
        return math.isfinite(a) and math.isfinite(b) and abs(a - b) < NUMERIC_SAVE_TOLERANCE
    except ValueError:
        return left.strip().lower() == right.strip().lower() if left.strip().lower() in ("true", "false") else False


def _same_node(left, right) -> bool:
    if left is None or right is None:
        return left is right
    if left.tag != right.tag or left.attrib.keys() != right.attrib.keys():
        return False
    if (left.tag == "Vector3" and left.get("key") in ("start-rotation", "end-rotation")
            and left.attrib == right.attrib):
        try:
            # Unity Quaternion.Euler applies Z, X, then Y, including at gimbal lock.
            from scipy.spatial.transform import Rotation
            if all(_same_value(str(x), str(y)) for x, y in zip(_vector(left), _vector(right))):
                return True
            a = Rotation.from_euler("zxy", [_vector(left)[i] for i in (2, 0, 1)], degrees=True)
            b = Rotation.from_euler("zxy", [_vector(right)[i] for i in (2, 0, 1)], degrees=True)
            # Near +/-90 degrees Unity's float32 Euler round trip amplifies
            # small quaternion errors (e.g. 90 -> 89.98022 degrees). Compare
            # normalized quaternion components with the same numeric tolerance
            # as Transform/Rotation, allowing q and -q to represent one pose.
            qa, qb = a.as_quat(), b.as_quat()
            return any(all(_same_value(str(x), str(sign * y)) for x, y in zip(qa, qb))
                       for sign in (1, -1))
        except (ValueError, TypeError, AttributeError):
            return False
    # Unity may serialize q or -q for the same rotation on Save As.
    if left.tag == "Rotation" and set(left.attrib) == {"x", "y", "z", "w"}:
        try:
            if all(_same_value(left.get(k), str(-float(right.get(k)))) for k in left.attrib):
                return True
        except ValueError:
            pass
    if any(not _same_value(value, right.get(key, "")) for key, value in left.attrib.items()):
        return False
    if not _same_value(left.text or "", right.text or ""):
        return False
    key = lambda node: (node.tag, node.get("key", ""))
    a, b = sorted(left, key=key), sorted(right, key=key)
    return len(a) == len(b) and all(_same_node(x, y) for x, y in zip(a, b))


def _vector(node):
    if node.tag != "Vector3" or len(node) != 3 or {n.tag for n in node} != {"X", "Y", "Z"}:
        raise ValueError("Expected a Vector3")
    values = [float(node.find(axis).text) for axis in ("X", "Y", "Z")]
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Nonfinite vector")
    return values


def _same_endpoint_setting(left, right) -> bool:
    if right is None or left.attrib != right.attrib:
        return False
    try:
        return all(_same_value(str(x), str(y)) for x, y in zip(_vector(left), _vector(right)))
    except (ValueError, TypeError, AttributeError):
        return False


def _same_recentered_connector(left, right) -> bool:
    # Native Save As recenters an id 96 connector while offsetting both local
    # endpoints. Compare machine-space endpoints, not the arbitrary local origin.
    if left.get("id") != "96" or right.get("id") != "96":
        return False
    try:
        from scipy.spatial.transform import Rotation
        a, b = left.find("Transform"), right.find("Transform")
        if not all(_same_node(a.find(key), b.find(key)) for key in ("Rotation", "Scale")):
            return False
        def endpoints(block):
            tr = block.find("Transform")
            position = [float(tr.find("Position").get(k)) for k in ("x", "y", "z")]
            scale = [float(tr.find("Scale").get(k)) for k in ("x", "y", "z")]
            rotation = Rotation.from_quat([float(tr.find("Rotation").get(k)) for k in ("x", "y", "z", "w")])
            result = []
            for key in ("start-position", "end-position"):
                local = _vector(block.find(f"Data/Vector3[@key='{key}']"))
                rotated = rotation.apply([v * s for v, s in zip(local, scale)])
                result.extend(p + v for p, v in zip(position, rotated))
            return result
        a, b = endpoints(left), endpoints(right)
        return all(_same_value(str(x), str(y)) for x, y in zip(a, b))
    except (ValueError, TypeError, AttributeError):
        return False


def validate_camera_variant(original: bytes, edited: bytes) -> tuple[list[str], list[str]]:
    baseline, candidate = _blocks(original), _blocks(edited)
    for guid, block in baseline.items():
        other = candidate.get(guid)
        if other is None or other.get("id") != block.get("id"):
            raise PreflightError(f"Original block removed/replaced: {guid}. Only add Camera Blocks.")
        # Existing cameras can be repositioned and reconfigured on subsequent edits.
        if block.get("id") == CAMERA_BLOCK_ID:
            continue
        recentered = _same_recentered_connector(block, other)
        if not _same_node(block.find("Transform"), other.find("Transform")) and not recentered:
            raise PreflightError(f"Original block moved/rotated/scaled: {guid}. Only edit cameras.")
        other_data = other.find("Data")
        settings = {node.get("key"): node for node in other_data} if other_data is not None else {}
        original_data = block.find("Data")
        for node in original_data if original_data is not None else []:
            if recentered and node.tag == "Vector3" and node.get("key") in ("start-position", "end-position"):
                continue
            if (block.get("id") in ("7", "96") and node.tag == "Vector3"
                    and node.get("key") in ("start-position", "end-position")
                    and _same_endpoint_setting(node, settings.get(node.get("key")))):
                continue
            if not _same_node(node, settings.get(node.get("key"))):
                raise PreflightError(f"Original block setting changed: {guid}, {node.get('key')!r}.")
    added = [guid for guid in candidate if guid not in baseline]
    if list(candidate)[:len(baseline)] != list(baseline):
        raise PreflightError("Original block order changed. Append cameras without reordering original blocks.")
    if any(candidate[guid].get("id") != CAMERA_BLOCK_ID for guid in added):
        raise PreflightError("Only additional Camera Blocks (id 58) are supported by --camera-bsg.")
    if not any(block.get("id") == CAMERA_BLOCK_ID for block in candidate.values()):
        raise PreflightError("Saved machine contains no Camera Blocks. Add a camera and Save As first.")
    return list(baseline), added


def _signature(path: Path):
    if not path.is_file():
        return None
    return path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()


def write_controller_machine(prepared: Path, destination: Path) -> Path:
    """Describe control hardware without manual cameras; never load this file."""
    tree = ET.parse(prepared)
    blocks = tree.getroot().find("Blocks")
    for block in list(blocks):
        if block.tag == "Block" and block.get("id") == CAMERA_BLOCK_ID:
            blocks.remove(block)
    tree.write(destination, encoding="utf-8", xml_declaration=True)
    return destination


def select_camera_source(*, source: Path, saved: Path, edit: bool, run_dir: Path,
                         run_id: str, orchestrator, timeout: float, log, status):
    """Caller ensures the sandbox is ready for edit=True. Never starts physics."""
    original = source.read_bytes()
    before = _signature(saved)
    if edit:
        if orchestrator.read_state().get("simulating"):
            raise PreflightError("Stop the current simulation before editing cameras.")
        # For incremental edits, preload the existing camera version if valid.
        preload = source
        if before is not None:
            validate_camera_variant(original, saved.read_bytes())
            preload = saved
        installed = orchestrator.install_machine(source_bsg=preload, name=f"camera_edit_{run_id[:8]}.bsg")
        sequence = orchestrator.send_command("load_machine", path=installed)
        orchestrator.wait_for_command_result(sequence, timeout=timeout)
        orchestrator.wait_for_simulating(False, timeout=timeout)
        status.set_phase("waiting_for_camera_edit", camera_bsg=str(saved))
        log.write(f"Machine loaded for camera editing. Save As: {saved}")
        log.write(f"In the game save dialog use '{saved.stem}' (or copy your saved BSG to the path above).")
        log.write("Keep the game in BUILD mode. Add cameras, save, then type yes here. Type cancel to exit.")
        while True:
            try:
                answer = input("Camera saved? [yes/cancel]: ").strip().lower()
            except (EOFError, KeyboardInterrupt) as exc:
                raise PreflightError("Camera edit cancelled; simulation was not started.") from exc
            if answer in ("cancel", "no", "quit"):
                raise PreflightError("Camera edit cancelled; simulation was not started.")
            if answer != "yes":
                continue
            try:
                if orchestrator.read_state().get("simulating"):
                    raise PreflightError("Return to BUILD mode and save before typing yes.")
                after = _signature(saved)
                if after is None or after == before:
                    raise PreflightError(f"No new save found at {saved}. Save this machine, then type yes again.")
                edited = saved.read_bytes()
                validate_camera_variant(original, edited)
            except (OSError, ET.ParseError, PreflightError) as exc:
                log.write(str(exc))
                continue
            break
    else:
        edited = saved.read_bytes()
    guids, added = validate_camera_variant(original, edited)
    # Run preparation must read this immutable snapshot, not a file the game may resave.
    snapshot = run_dir / "camera_source.bsg"
    snapshot.write_bytes(edited)
    (run_dir / "camera_baseline.bsg").write_bytes(original)
    receipt = {
        "baseline_bsg": str(source.resolve()), "baseline_sha256": hashlib.sha256(original).hexdigest(),
        "saved_camera_bsg": str(saved), "camera_source_bsg": str(snapshot.resolve()),
        "camera_source_sha256": hashlib.sha256(edited).hexdigest(), "edit_before_start": edit,
        "original_guids": guids, "added_camera_guids": added,
        "live_telemetry_default": "original GUIDs only; offline recorder includes full machine",
    }
    atomic_write_json(run_dir / "camera_edit.json", receipt)
    log.write(f"Camera BSG snapshotted: {snapshot}; {len(added)} added camera(s). Preparing fresh bindings.")
    status.set_phase("prepared")
    return snapshot, guids, receipt
