"""Drive the ToolKit Inspector file protocol and collect its artifacts."""

from __future__ import annotations

import json
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from buildarena.progress import TimeBudgetBar
from controller_sdk.protocol import (
    CHANNEL_CATALOG_FILE,
    INSPECTOR_REPORT_FILE,
    INSPECTOR_REPORT_SCHEMA,
    INSPECTOR_REQUEST_FILE,
    INSPECTOR_REQUEST_SCHEMA,
    atomic_write_json,
    read_json_retry,
)

COLLIDER_DUMP_NAME = "collider_dump.toml"
BEHAVIOUR_TYPES_NAME = "block_behaviour_types.json"
CHANNEL_CATALOG_NAME = CHANNEL_CATALOG_FILE
KEYLIST_CACHE_NAME = "block_live_keylist_cache.tsv"


class InspectorError(RuntimeError):
    """The Inspector request failed, timed out, or produced incomplete artifacts."""


@dataclass(frozen=True)
class InspectorReport:
    request_id: str
    payload: dict[str, Any]
    collider_dump: Path
    behaviour_types: Path | None
    channel_catalog: Path | None
    dumped_block_ids: tuple[int, ...]


def _read_toml_block_ids(dump_path: Path) -> tuple[int, ...]:
    import tomllib

    with dump_path.open("rb") as handle:
        data = tomllib.load(handle)
    blocks = data.get("blocks")
    if not isinstance(blocks, dict) or not blocks:
        raise InspectorError(
            f"{dump_path} has no top-level [blocks] table; Inspector dump is incomplete."
        )
    ids: list[int] = []
    for raw_key in blocks:
        if not str(raw_key).isdigit():
            raise InspectorError(
                f"{dump_path} has a non-numeric block key {raw_key!r}; refusing to guess."
            )
        ids.append(int(raw_key))
    return tuple(sorted(ids))


def write_inspector_request(*, data_dir: Path, request_id: str | None = None) -> str:
    resolved = request_id if request_id is not None else str(uuid.uuid4())
    if resolved.strip() == "":
        raise InspectorError("inspector request_id must be a non-empty string.")
    data_dir.mkdir(parents=True, exist_ok=True)
    report_path = data_dir / INSPECTOR_REPORT_FILE
    report_path.unlink(missing_ok=True)
    atomic_write_json(
        data_dir / INSPECTOR_REQUEST_FILE,
        {
            "schema": INSPECTOR_REQUEST_SCHEMA,
            "request_id": resolved,
        },
    )
    return resolved


def read_inspector_report(*, data_dir: Path) -> dict[str, Any] | None:
    path = data_dir / INSPECTOR_REPORT_FILE
    if not path.is_file():
        return None
    payload = read_json_retry(path)
    if not isinstance(payload, dict):
        raise InspectorError(f"{path} is not a JSON object.")
    return payload


def wait_for_inspector_report(
    *,
    data_dir: Path,
    request_id: str,
    timeout: float,
    poll_interval: float = 0.25,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_payload: dict[str, Any] | None = None
    bar = TimeBudgetBar(
        timeout=timeout,
        desc=f"Waiting for Inspector report request_id={request_id}",
    )
    finished = False
    try:
        while time.monotonic() < deadline:
            payload = read_inspector_report(data_dir=data_dir)
            if payload is not None:
                last_payload = payload
                schema = payload.get("schema")
                if schema != INSPECTOR_REPORT_SCHEMA:
                    raise InspectorError(
                        f"{INSPECTOR_REPORT_FILE} schema is {schema!r}; expected {INSPECTOR_REPORT_SCHEMA}."
                    )
                reported_id = str(payload.get("request_id") or "")
                if reported_id == request_id:
                    error = str(payload.get("error") or "")
                    if error:
                        raise InspectorError(
                            f"Inspector reported error={error!r} for request_id={request_id}."
                        )
                    finished = True
                    return payload
            bar.poke(f"last_request_id={(last_payload or {}).get('request_id')!r}")
            time.sleep(poll_interval)
    finally:
        bar.close(ok=finished)
    stale = ""
    if last_payload is not None:
        stale = (
            f" Last report had request_id={last_payload.get('request_id')!r} "
            f"schema={last_payload.get('schema')!r}."
        )
    request_path = data_dir / INSPECTOR_REQUEST_FILE
    pending = ""
    if request_path.is_file():
        pending = (
            f" {INSPECTOR_REQUEST_FILE} is still present, so ToolKit did not consume "
            "the request. Setup writes it before enter_sandbox because the mod reads "
            "it on scene load."
        )
    raise InspectorError(
        f"{INSPECTOR_REPORT_FILE} matching request_id={request_id!r} did not appear within "
        f"{timeout}s at {data_dir}.{stale}{pending} The installed BuildArenaToolKit.dll must "
        f"implement {INSPECTOR_REQUEST_SCHEMA} -> {INSPECTOR_REPORT_SCHEMA}."
    )


def collect_inspector_artifacts(*, data_dir: Path, request_id: str, payload: dict[str, Any]) -> InspectorReport:
    dump_path = data_dir / COLLIDER_DUMP_NAME
    if not dump_path.is_file():
        raise InspectorError(
            f"Inspector report request_id={request_id} arrived but {COLLIDER_DUMP_NAME} is missing at {dump_path}."
        )
    dumped_ids = _read_toml_block_ids(dump_path)
    if int(payload.get("prefabs_dumped") or 0) <= 0:
        raise InspectorError(
            f"Inspector report request_id={request_id} has prefabs_dumped={payload.get('prefabs_dumped')!r}; "
            "the dump is not a successful bulk prefab scan."
        )
    behaviour = data_dir / BEHAVIOUR_TYPES_NAME
    catalog = data_dir / CHANNEL_CATALOG_NAME
    return InspectorReport(
        request_id=request_id,
        payload=payload,
        collider_dump=dump_path,
        behaviour_types=behaviour if behaviour.is_file() else None,
        channel_catalog=catalog if catalog.is_file() else None,
        dumped_block_ids=dumped_ids,
    )


def _copy_if_different(*, source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and source.resolve() == dest.resolve():
        return
    shutil.copy2(source, dest)


def copy_inspector_artifacts(
    *,
    report: InspectorReport,
    collider_dest: Path,
    behaviour_dest: Path | None = None,
    catalog_dest: Path | None = None,
) -> dict[str, str]:
    copied: dict[str, str] = {}
    _copy_if_different(source=report.collider_dump, dest=collider_dest)
    copied["collider_dump"] = str(collider_dest)
    if behaviour_dest is not None and report.behaviour_types is not None:
        _copy_if_different(source=report.behaviour_types, dest=behaviour_dest)
        copied["block_behaviour_types"] = str(behaviour_dest)
    if catalog_dest is not None and report.channel_catalog is not None:
        _copy_if_different(source=report.channel_catalog, dest=catalog_dest)
        copied["block_channel_catalog"] = str(catalog_dest)
    return copied


def inspector_dump_reusable(*, report: InspectorReport | None) -> bool:
    """True when the game-side dump still has collider and behaviour files.

    The channel catalog is assembled by setup into the ToolKit data dir;
    ToolKit itself does not emit it, so a missing catalog does not force
    another Inspector pass.
    """
    if report is None:
        return False
    if not report.collider_dump.is_file():
        return False
    if report.behaviour_types is None or not report.behaviour_types.is_file():
        return False
    return len(report.dumped_block_ids) > 0


def existing_collider_dump(
    *,
    data_dir: Path,
) -> InspectorReport | None:
    dump_path = data_dir / COLLIDER_DUMP_NAME
    if not dump_path.is_file():
        return None
    dumped_ids = _read_toml_block_ids(dump_path)
    behaviour = data_dir / BEHAVIOUR_TYPES_NAME
    catalog = data_dir / CHANNEL_CATALOG_NAME
    return InspectorReport(
        request_id="",
        payload={
            "schema": INSPECTOR_REPORT_SCHEMA,
            "request_id": "",
            "reused_existing_artifacts": True,
        },
        collider_dump=dump_path,
        behaviour_types=behaviour if behaviour.is_file() else None,
        channel_catalog=catalog if catalog.is_file() else None,
        dumped_block_ids=dumped_ids,
    )


def load_catalog_block_ids(*, catalog_path: Path) -> tuple[int, ...]:
    if not catalog_path.is_file():
        raise InspectorError(f"Block channel catalog not found: {catalog_path}")
    payload = json.loads(catalog_path.read_text(encoding="utf-8-sig"))
    blocks = payload.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        raise InspectorError(f"{catalog_path} has no blocks array.")
    ids: list[int] = []
    for entry in blocks:
        if not isinstance(entry, dict) or "block_id" not in entry:
            raise InspectorError(f"{catalog_path} contains a block entry without block_id.")
        ids.append(int(entry["block_id"]))
    return tuple(sorted(ids))
