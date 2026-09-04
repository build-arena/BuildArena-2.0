"""ControllerClient for the strict v4 BAA4/BAT4 file protocol."""

from __future__ import annotations

import math
import os
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .action_codec import decode_action, encode_action
from .bulk_codec import (
    BulkBatch,
    BulkCodecError,
    decode_bulk_batch,
    decode_bulk_marker,
)
from .protocol import (
    normalize_telemetry_target_guids,
    ACTION_FILE_PREFIX,
    ACTION_FILE_SUFFIX,
    ARMED_FLAG_FILE,
    BLOCK_TABLE_FILE,
    BLOCK_TABLE_SCHEMA,
    BULK_BUFFER_FILES,
    BULK_MAX_BATCH_SAMPLES,
    BULK_PUBLISH_FILE,
    CONTROL_PROTOCOL_ERROR_FILE,
    ENV_MOD_DATA_DIR,
    ENV_RUN_DIR,
    ENV_RUN_ID,
    SUBSCRIPTION_FILE,
    SUBSCRIPTION_SCHEMA,
    TELEMETRY_BUFFER_FILES,
    TELEMETRY_PUBLISH_FILE,
    action_file_name,
    atomic_write_bytes,
    atomic_write_json,
    read_shared_bytes,
    parse_action_file_name,
    read_json_retry,
)
from .profiles import PROFILE_FULL, TARGET_FIELD_BITS, resolve_telemetry_selection
from .telemetry_codec import (
    FLAG_RECORD_NAMES,
    TelemetryCodecError,
    TelemetryFrame,
    decode_telemetry,
    decode_telemetry_marker,
)

_FIELD_BITS = TARGET_FIELD_BITS
CHANNEL_KIND_KEY = "key"
CHANNEL_KIND_SLIDER = "slider"


class SliderRangeWarning(UserWarning):
    """A slider command was outside the game-published min/max and was clipped."""


@dataclass(frozen=True)
class BlockChannel:
    index: int
    block_guid: str
    name: str
    keys: tuple[str, ...]
    keylist_index: int | None = None
    local_index: int | None = None

    @property
    def channel(self) -> str:
        return self.name

    @property
    def block_guid_lower(self) -> str:
        return self.block_guid.lower()

    @property
    def kind(self) -> str:
        return CHANNEL_KIND_KEY


@dataclass(frozen=True)
class BlockSlider:
    index: int
    block_guid: str
    name: str
    minimum: float
    maximum: float
    default: float
    unclamped: bool = False
    local_index: int | None = None
    initial_value: float | None = None

    @property
    def channel(self) -> str:
        return self.name

    @property
    def block_guid_lower(self) -> str:
        return self.block_guid.lower()

    @property
    def kind(self) -> str:
        return CHANNEL_KIND_SLIDER

    def clip(self, value: float, *, stacklevel: int = 3) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(
                f"slider {self.name!r} on {self.block_guid} value must be numeric."
            )
        requested = float(value)
        if not math.isfinite(requested):
            raise ValueError(
                f"slider {self.name!r} on {self.block_guid} value must be finite."
            )
        if self.minimum <= requested <= self.maximum:
            return requested
        clipped = min(max(requested, self.minimum), self.maximum)
        warnings.warn(
            f"slider {self.name!r} on {self.block_guid} value {requested} "
            f"outside [{self.minimum}, {self.maximum}]; clipped to {clipped}.",
            SliderRangeWarning,
            stacklevel=stacklevel,
        )
        return clipped


def targets_by_guid(
    sample: TelemetryFrame,
    *,
    block_guids: dict[int, str] | None = None,
    expected_guids: tuple[str, ...] | list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Index live BAT4 records by GUID, omitting destroyed targets."""
    if not isinstance(sample, TelemetryFrame):
        raise TypeError("sample must be a decoded BAT4 TelemetryFrame.")
    if block_guids is None:
        raise ValueError("block_guids is required for index-to-GUID resolution.")
    result: dict[str, dict[str, Any]] = {}
    names = set(sample.records.dtype.names or ())
    for row in sample.records:
        target_index = int(row["target_index"])
        guid = block_guids.get(target_index)
        if guid is None:
            raise TelemetryCodecError(
                f"BAT4 record references target index {target_index}, absent from block_table.json."
            )
        if int(row["valid"]) == 0:
            continue
        values: dict[str, Any] = {}
        for name in names:
            if name in ("target_index", "valid"):
                continue
            if name in FLAG_RECORD_NAMES:
                values[name] = bool(row[name])
            elif name == "fire_intensity":
                values[name] = float(row[name])
            else:
                values[name] = tuple(float(value) for value in row[name])
        result[guid.lower()] = values
    if expected_guids is not None:
        expected = {guid.lower() for guid in expected_guids}
        unexpected = set(result) - expected
        if unexpected:
            raise TelemetryCodecError(
                f"Telemetry contains unexpected targets: expected a subset of "
                f"{sorted(expected)}, got {sorted(result)}."
            )
    return result


class ControllerClient:
    """Strict v4 file-protocol client.

    Action writes create immutable ``action_<sequence>.bin`` files.
    Pass ``durable=True`` when crash/power-loss durability is required.
    """

    def __init__(
        self,
        data_dir: Path,
        poll_interval: float = 0.25,
        *,
        run_id: str = "",
        durable: bool = False,
        channel_source_bsg: str | Path | None = None,
        catalog_path: str | Path | None = None,
    ):
        if channel_source_bsg is not None or catalog_path is not None:
            raise ValueError(
                "Protocol v4 does not infer channels from BSG/catalog files; "
                "the mod must publish block_table.json."
            )
        self.data_dir = Path(data_dir)
        self.run_id = run_id
        self.poll_interval = poll_interval
        self.durable = durable
        self.subscription_path = self.data_dir / SUBSCRIPTION_FILE
        self.block_table_path = self.data_dir / BLOCK_TABLE_FILE
        self.protocol_error_path = self.data_dir / CONTROL_PROTOCOL_ERROR_FILE
        self.telemetry_publish_path = self.data_dir / TELEMETRY_PUBLISH_FILE
        self.sample_path = self.telemetry_publish_path
        self.bulk_publish_path = self.data_dir / BULK_PUBLISH_FILE
        self.armed_path = self.data_dir / ARMED_FLAG_FILE
        # A v4 run is a contiguous immutable stream beginning at sequence 1.
        # If this client is reopening an existing run, the scan below resumes
        # after its highest valid action file.
        self.sequence = 0
        self.keepalive = 0
        self._last_sample_sequence: int | None = None
        self._last_bulk_commit: tuple[int, int] | None = None
        self._noted_rigidbody_destroyed = False
        self._channels_by_index: dict[int, BlockChannel] | None = None
        self._channels_by_block_name: dict[tuple[int | str, str], BlockChannel] | None = None
        self._channels_by_key: dict[str, tuple[BlockChannel, ...]] | None = None
        self._sliders_by_index: dict[int, BlockSlider] | None = None
        self._sliders_by_block_name: dict[tuple[int | str, str], BlockSlider] | None = None
        self._last_key_records: list[tuple[int, float]] = []
        self._last_slider_records: list[tuple[int, float]] = []
        self._block_guids: dict[int, str] | None = None
        self._bulk_guids: dict[int, str] | None = None
        self._gc_through_sequence = 0
        self._audit_path = self._resolve_audit_path()
        self._action_stats = {
            "published": 0,
            "empty_releases": 0,
            "gc_deleted": 0,
        }

        latest = self._latest_action_file()
        if latest is not None:
            previous = decode_action(latest.read_bytes())
            self.sequence = max(self.sequence, previous.sequence)
            self.keepalive = previous.keepalive
        if self.block_table_path.exists():
            self.load_block_table()

    def _latest_action_file(self) -> Path | None:
        if not self.data_dir.is_dir():
            return None
        highest = 0
        path: Path | None = None
        for candidate in self.data_dir.glob(f"{ACTION_FILE_PREFIX}*{ACTION_FILE_SUFFIX}"):
            sequence = parse_action_file_name(candidate.name)
            if sequence is not None and sequence > highest:
                highest = sequence
                path = candidate
        return path

    def _resolve_audit_path(self) -> Path | None:
        run_dir = os.environ.get(ENV_RUN_DIR, "")
        if not run_dir:
            return None
        path = Path(run_dir) / "actions.csv"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "sequence,keepalive,record_count,records\n", encoding="utf-8"
            )
        return path

    def _append_action_audit(
        self, sequence: int, keepalive: int, records: list[tuple[int, float]]
    ) -> None:
        if self._audit_path is None:
            return
        encoded = ";".join(f"{index}:{value:.6g}" for index, value in records)
        with self._audit_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(f"{sequence},{keepalive},{len(records)},{encoded}\n")
        stats_path = self._audit_path.with_name("action_stats.json")
        atomic_write_json(stats_path, dict(self._action_stats), durable=False)

    def gc_acked_actions(self, sequence_applied: int) -> int:
        """Delete confirmed action files older than the latest applied snapshot.

        The file for ``sequence_applied`` is kept so the same run can reconnect.
        """
        if not isinstance(sequence_applied, int) or isinstance(sequence_applied, bool):
            raise TypeError("sequence_applied must be an integer.")
        if sequence_applied < 1 or sequence_applied <= self._gc_through_sequence:
            return 0
        if not self.data_dir.is_dir():
            return 0
        deleted = 0
        for candidate in self.data_dir.glob(f"{ACTION_FILE_PREFIX}*{ACTION_FILE_SUFFIX}"):
            sequence = parse_action_file_name(candidate.name)
            if sequence is None or sequence >= sequence_applied:
                continue
            candidate.unlink(missing_ok=True)
            deleted += 1
        self._gc_through_sequence = sequence_applied
        self._action_stats["gc_deleted"] = int(self._action_stats["gc_deleted"]) + deleted
        return deleted

    @classmethod
    def from_environment(
        cls, poll_interval: float = 0.05, *, durable: bool = False
    ) -> "ControllerClient":
        data_dir = os.environ.get(ENV_MOD_DATA_DIR, "")
        run_id = os.environ.get(ENV_RUN_ID, "")
        if not data_dir or not run_id:
            raise RuntimeError(
                f"{ENV_MOD_DATA_DIR} / {ENV_RUN_ID} are not set. Use a managed run "
                "or construct ControllerClient(data_dir=...) directly."
            )
        return cls(
            Path(data_dir), poll_interval=poll_interval, run_id=run_id, durable=durable
        )

    @staticmethod
    def _field_selection(fields: Iterable[str], *, label: str) -> tuple[int, tuple[str, ...]]:
        selected = tuple(dict.fromkeys(fields))
        unknown = [field for field in selected if field not in _FIELD_BITS]
        if unknown:
            raise ValueError(
                f"Unknown {label} fields {unknown}; allowed: {sorted(_FIELD_BITS)}."
            )
        if not selected:
            raise ValueError(f"At least one {label} field must be selected.")
        return sum(_FIELD_BITS[field] for field in selected), selected

    @staticmethod
    def _validated_hz(frequency_hz: float, *, label: str) -> int:
        if isinstance(frequency_hz, bool) or not isinstance(frequency_hz, (int, float)):
            raise TypeError(f"{label} must be numeric.")
        frequency = float(frequency_hz)
        if not math.isfinite(frequency) or frequency not in (10.0, 25.0, 50.0):
            raise ValueError(f"{label} must be one of 10, 25, or 50.")
        return int(frequency)

    def subscribe(
        self,
        *,
        target_guids: Iterable[str] | None = None,
        telemetry_hz: float = 25,
        profile: str | None = None,
        target_fields: Iterable[str] | None = None,
        machine_fields: Iterable[str] | None = None,
        bulk_hz: float | None = None,
        bulk_batch_samples: int | None = None,
    ) -> int:
        selection = resolve_telemetry_selection(
            profile=profile,
            target_fields=target_fields,
            machine_fields=machine_fields,
            default_profile=PROFILE_FULL,
        )
        frequency = self._validated_hz(telemetry_hz, label="telemetry_hz")
        guids = normalize_telemetry_target_guids(target_guids)
        payload: dict[str, Any] = {
            "schema": SUBSCRIPTION_SCHEMA,
            "telemetry_hz": frequency,
            "target_field_mask": selection["target_field_mask"],
            "target_fields": list(selection["target_fields"]),
            "machine_field_mask": selection["machine_field_mask"],
            "machine_fields": list(selection["machine_fields"]),
            "target_guids": list(guids),
        }
        if selection["profile"] is not None:
            payload["profile"] = selection["profile"]
        bulk_options = (bulk_hz, bulk_batch_samples)
        if any(option is not None for option in bulk_options):
            if bulk_hz is None or bulk_batch_samples is None:
                raise ValueError(
                    "bulk_hz and bulk_batch_samples must both be provided when bulk telemetry is enabled."
                )
            if isinstance(bulk_batch_samples, bool) or not isinstance(
                bulk_batch_samples, int
            ):
                raise TypeError("bulk_batch_samples must be an integer.")
            if not 1 <= bulk_batch_samples <= BULK_MAX_BATCH_SAMPLES:
                raise ValueError(
                    f"bulk_batch_samples must be 1..{BULK_MAX_BATCH_SAMPLES}."
                )
            payload["bulk_target_field_mask"] = selection["target_field_mask"]
            payload["bulk_target_fields"] = list(selection["target_fields"])
            payload["bulk_machine_field_mask"] = selection["machine_field_mask"]
            payload["bulk_machine_fields"] = list(selection["machine_fields"])
            if selection["profile"] is not None:
                payload["bulk_profile"] = selection["profile"]
            payload["bulk_hz"] = self._validated_hz(bulk_hz, label="bulk_hz")
            payload["bulk_batch_samples"] = bulk_batch_samples
        if self.run_id:
            payload["run_id"] = self.run_id
        atomic_write_json(self.subscription_path, payload, durable=self.durable)
        return int(selection["target_field_mask"])

    def load_block_table(self) -> list[BlockChannel]:
        payload = read_json_retry(self.block_table_path)
        if payload.get("schema") != BLOCK_TABLE_SCHEMA:
            raise ValueError(
                f"Unsupported block table schema {payload.get('schema')!r}; "
                f"expected {BLOCK_TABLE_SCHEMA!r}."
            )
        table_run_id = payload.get("run_id", "")
        if self.run_id and table_run_id != self.run_id:
            raise ValueError(
                f"block_table.json run_id {table_run_id!r} does not match "
                f"client run_id {self.run_id!r}."
            )
        blocks = payload.get("targets")
        channels = payload.get("channels")
        if not isinstance(blocks, list) or not isinstance(channels, list):
            raise ValueError("block_table.json requires array fields 'targets' and 'channels'.")

        block_guids = self._parse_target_table(blocks, label="targets")

        bulk_rows = payload.get("bulk_targets")
        bulk_guids: dict[int, str] | None = None
        if bulk_rows is not None:
            if not isinstance(bulk_rows, list) or not bulk_rows:
                raise ValueError(
                    "block_table.json bulk_targets must be a non-empty array when present."
                )
            bulk_guids = self._parse_target_table(bulk_rows, label="bulk_targets")

        resolved: list[BlockChannel] = []
        sliders: list[BlockSlider] = []
        used_channel_indices: set[int] = set()
        for position, row in enumerate(channels):
            if not isinstance(row, dict):
                raise ValueError(f"block table channel {position} must be an object.")
            kind = row.get("kind", CHANNEL_KIND_KEY)
            if kind == CHANNEL_KIND_SLIDER:
                sliders.append(self._parse_slider_row(row, position, used_channel_indices))
                continue
            if kind != CHANNEL_KIND_KEY:
                raise ValueError(
                    f"channel {position} kind must be {CHANNEL_KIND_KEY!r} or "
                    f"{CHANNEL_KIND_SLIDER!r}, got {kind!r}."
                )
            resolved.append(self._parse_key_row(row, position, used_channel_indices))

        by_address: dict[tuple[int | str, str], BlockChannel] = {}
        by_key_lists: dict[str, list[BlockChannel]] = {}
        for channel in resolved:
            addresses: list[int | str] = [
                channel.block_guid,
                channel.block_guid.lower(),
                channel.local_index,
            ]
            names = [channel.name]
            if channel.keylist_index is not None:
                names.append(f"keylist_{channel.keylist_index}")
            for address in addresses:
                for name in names:
                    key = (address, name)
                    if key in by_address and by_address[key] != channel:
                        raise ValueError(f"Ambiguous channel address {key!r}.")
                    by_address[key] = channel
            for key_name in channel.keys:
                by_key_lists.setdefault(key_name, []).append(channel)
        slider_addresses: dict[tuple[int | str, str], BlockSlider] = {}
        for slider in sliders:
            addresses = [
                slider.block_guid,
                slider.block_guid.lower(),
                slider.local_index,
            ]
            for address in addresses:
                key = (address, slider.name)
                if key in slider_addresses and slider_addresses[key] != slider:
                    raise ValueError(f"Ambiguous slider address {key!r}.")
                slider_addresses[key] = slider
        self._block_guids = block_guids
        self._bulk_guids = bulk_guids
        self._channels_by_index = {channel.index: channel for channel in resolved}
        self._channels_by_block_name = by_address
        self._channels_by_key = {
            key: tuple(value) for key, value in by_key_lists.items()
        }
        self._sliders_by_index = {slider.index: slider for slider in sliders}
        self._sliders_by_block_name = slider_addresses
        self._last_key_records = []
        self._last_slider_records = []
        return resolved

    def _parse_key_row(
        self, row: dict[str, Any], position: int, used_channel_indices: set[int]
    ) -> BlockChannel:
        index = row.get("index")
        block_guid = row.get("block_guid")
        name = row.get("channel")
        keys = row.get("keys", [])
        keylist_index = row.get("keylist_index")
        local_index = row.get("local_index")
        self._validate_u16(index, f"channel {position} index")
        if index in used_channel_indices:
            raise ValueError(f"Duplicate channel index {index}.")
        if not isinstance(block_guid, str) or not block_guid:
            raise ValueError(f"channel {position} block_guid must be a non-empty string.")
        if not isinstance(name, str) or not name:
            raise ValueError(f"channel {position} name must be a non-empty string.")
        if not isinstance(keys, list) or any(not isinstance(key, str) or not key for key in keys):
            raise ValueError(f"channel {position} keys must be non-empty strings.")
        if keylist_index is not None:
            self._validate_u16(keylist_index, f"channel {position} keylist_index")
        if isinstance(local_index, bool) or not isinstance(local_index, int) or local_index < 0:
            raise ValueError(f"channel {position} local_index must be a non-negative integer.")
        used_channel_indices.add(index)
        return BlockChannel(
            index=index,
            block_guid=block_guid,
            name=name,
            keys=tuple(keys),
            keylist_index=keylist_index,
            local_index=local_index,
        )

    def _parse_slider_row(
        self, row: dict[str, Any], position: int, used_channel_indices: set[int]
    ) -> BlockSlider:
        index = row.get("index")
        block_guid = row.get("block_guid")
        name = row.get("channel")
        local_index = row.get("local_index")
        minimum = row.get("min")
        maximum = row.get("max")
        default = row.get("default")
        unclamped = row.get("unclamped", False)
        initial = row.get("value")
        self._validate_u16(index, f"slider {position} index")
        if index in used_channel_indices:
            raise ValueError(f"Duplicate channel index {index}.")
        if not isinstance(block_guid, str) or not block_guid:
            raise ValueError(f"slider {position} block_guid must be a non-empty string.")
        if not isinstance(name, str) or not name:
            raise ValueError(f"slider {position} name must be a non-empty string.")
        if isinstance(local_index, bool) or not isinstance(local_index, int) or local_index < 0:
            raise ValueError(f"slider {position} local_index must be a non-negative integer.")
        if (
            isinstance(minimum, bool)
            or isinstance(maximum, bool)
            or not isinstance(minimum, (int, float))
            or not isinstance(maximum, (int, float))
            or not math.isfinite(float(minimum))
            or not math.isfinite(float(maximum))
            or float(minimum) > float(maximum)
        ):
            raise ValueError(
                f"slider {position} must publish finite min/max with min <= max."
            )
        if default == "positive_infinity":
            parsed_default = math.inf
        elif default == "negative_infinity":
            parsed_default = -math.inf
        elif (
            isinstance(default, bool)
            or not isinstance(default, (int, float))
            or not math.isfinite(float(default))
        ):
            raise ValueError(
                f"slider {position} default must be a finite number or an explicit "
                "'positive_infinity'/'negative_infinity' token."
            )
        else:
            parsed_default = float(default)
        if not isinstance(unclamped, bool):
            raise ValueError(f"slider {position} unclamped must be a bool.")
        if initial is not None:
            if isinstance(initial, bool) or not isinstance(initial, (int, float)) or not math.isfinite(float(initial)):
                raise ValueError(f"slider {position} value must be a finite number when present.")
            initial = float(initial)
        used_channel_indices.add(index)
        return BlockSlider(
            index=index,
            block_guid=block_guid,
            name=name,
            minimum=float(minimum),
            maximum=float(maximum),
            default=parsed_default,
            unclamped=unclamped,
            local_index=local_index,
            initial_value=initial,
        )

    @staticmethod
    def _validate_u16(value: Any, label: str) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFF:
            raise ValueError(f"{label} must be an unsigned 16-bit integer.")

    @classmethod
    def _parse_target_table(cls, rows: list[Any], *, label: str) -> dict[int, str]:
        guids: dict[int, str] = {}
        seen: set[str] = set()
        for position, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ValueError(f"{label} entry {position} must be an object.")
            index, guid = row.get("index"), row.get("guid")
            cls._validate_u16(index, f"{label} entry {position} index")
            if not isinstance(guid, str) or not guid.strip():
                raise ValueError(f"{label} entry {position} guid must be a non-empty string.")
            if index in guids or guid.lower() in seen:
                raise ValueError(f"Duplicate index or GUID in {label} at entry {position}.")
            guids[index] = guid
            seen.add(guid.lower())
        return guids

    def _require_channels(self) -> None:
        if (
            self._channels_by_index is None
            or self._channels_by_block_name is None
            or self._channels_by_key is None
            or self._sliders_by_index is None
            or self._sliders_by_block_name is None
        ):
            raise RuntimeError(
                "Channel table is not ready; wait for block_table.json and call "
                "load_block_table() before sending actions."
            )

    def _raise_protocol_error(self) -> None:
        if not self.protocol_error_path.exists():
            return
        payload = read_json_retry(self.protocol_error_path)
        code = payload.get("code")
        if code == "rigidbody_destroyed":
            detail = payload.get("detail", "")
            if not self._noted_rigidbody_destroyed:
                self._noted_rigidbody_destroyed = True
                print(
                    f"ToolKit reported rigidbody_destroyed ({detail}); "
                    "continuing because block loss is a lifecycle event, not a protocol failure.",
                    flush=True,
                )
            return
        raise RuntimeError(
            f"ToolKit v4 protocol error {code!r}: {payload.get('detail', '')}"
        )

    @property
    def channels(self) -> list[BlockChannel]:
        self._require_channels()
        assert self._channels_by_index is not None
        return list(self._channels_by_index.values())

    @property
    def sliders(self) -> list[BlockSlider]:
        self._require_channels()
        assert self._sliders_by_index is not None
        return list(self._sliders_by_index.values())

    def guid_of(self, block: int) -> str:
        if self._block_guids is None:
            raise RuntimeError("Block table is not ready; call load_block_table().")
        self._validate_u16(block, "block")
        try:
            return self._block_guids[block]
        except KeyError as exc:
            raise ValueError(f"No block at index {block}.") from exc

    def send_keys(
        self, keys: list[str] | tuple[str, ...], *, keepalive: bool = False
    ) -> int:
        self._require_channels()
        assert self._channels_by_key is not None
        records: list[tuple[int, float]] = []
        used: set[int] = set()
        for key in dict.fromkeys(keys):
            matches = self._channels_by_key.get(str(key))
            if not matches:
                raise ValueError(f"No channel in block_table.json is bound to key {key!r}.")
            for channel in matches:
                if channel.index not in used:
                    records.append((channel.index, 1.0))
                    used.add(channel.index)
        return self._send_merged(keys=records, keepalive=keepalive)

    def send_channels(
        self,
        *,
        channels: list[tuple[int | str, str] | tuple[int | str, str, float]],
        keepalive: bool = False,
    ) -> int:
        self._require_channels()
        assert self._channels_by_block_name is not None
        assert self._sliders_by_block_name is not None
        records: list[tuple[int, float]] = []
        for position, item in enumerate(channels):
            if len(item) == 2:
                block, name = item
                value = 1.0
            elif len(item) == 3:
                block, name, value = item
            else:
                raise ValueError(
                    f"channel request {position} must be (block, name) or (block, name, value)."
                )
            address = block.lower() if isinstance(block, str) else block
            channel = self._channels_by_block_name.get((address, name))
            if channel is None:
                slider = self._sliders_by_block_name.get((address, name))
                if slider is not None:
                    raise ValueError(
                        f"block={block!r}, name={name!r} is a slider; use send_sliders()."
                    )
                raise ValueError(f"No channel for block={block!r}, name={name!r}.")
            records.append((channel.index, value))
        return self._send_merged(keys=records, keepalive=keepalive)

    def send_sliders(
        self,
        sliders: list[tuple[int | str, str, float]],
        *,
        keepalive: bool = False,
    ) -> int:
        """Set mapper sliders. Out-of-range values warn and clip to game min/max.

        Sliders omitted from the published BAA4 snapshot are reset to the
        game default (``ResetValue``), not max and not the last command.
        ``send_sliders`` / ``send_keys`` merge the last key and slider
        snapshots so a follow-up key send does not omit sliders.
        """
        self._require_channels()
        assert self._sliders_by_block_name is not None
        assert self._channels_by_block_name is not None
        records: list[tuple[int, float]] = []
        for position, item in enumerate(sliders):
            if len(item) != 3:
                raise ValueError(
                    f"slider request {position} must be (block, name, value)."
                )
            block, name, value = item
            address = block.lower() if isinstance(block, str) else block
            slider = self._sliders_by_block_name.get((address, name))
            if slider is None:
                channel = self._channels_by_block_name.get((address, name))
                if channel is not None:
                    raise ValueError(
                        f"block={block!r}, name={name!r} is a key channel; use send_channels()."
                    )
                raise ValueError(f"No slider for block={block!r}, name={name!r}.")
            records.append((slider.index, slider.clip(value, stacklevel=3)))
        return self._send_merged(sliders=records, keepalive=keepalive)

    def send_indexes(
        self, records: list[tuple[int, float]], *, keepalive: bool = False
    ) -> int:
        """Send an explicit list of (channel_index, value) records."""
        self._require_channels()
        assert self._channels_by_index is not None
        assert self._sliders_by_index is not None
        keys: list[tuple[int, float]] = []
        sliders: list[tuple[int, float]] = []
        for position, (index, value) in enumerate(records):
            slider = self._sliders_by_index.get(index)
            if slider is not None:
                sliders.append((index, slider.clip(value, stacklevel=3)))
                continue
            if index not in self._channels_by_index:
                raise ValueError(f"Unknown channel_index {index!r} at record {position}.")
            keys.append((index, value))
        self._last_key_records = keys
        self._last_slider_records = sliders
        return self._send_records(keys + sliders, keepalive=keepalive)

    def _send_merged(
        self,
        *,
        keys: list[tuple[int, float]] | None = None,
        sliders: list[tuple[int, float]] | None = None,
        keepalive: bool,
    ) -> int:
        if keys is not None:
            self._last_key_records = list(keys)
        if sliders is not None:
            self._last_slider_records = list(sliders)
        return self._send_records(
            self._last_key_records + self._last_slider_records, keepalive=keepalive
        )

    def _send_records(
        self, records: list[tuple[int, float]], *, keepalive: bool
    ) -> int:
        if not isinstance(keepalive, bool):
            raise TypeError("keepalive must be a bool.")
        if self.sequence >= 0x7FFFFFFF:
            raise OverflowError("BAA4 signed 32-bit action sequence is exhausted.")
        self.sequence += 1
        if keepalive:
            if self.keepalive >= 0x7FFFFFFF:
                raise OverflowError("BAA4 signed 32-bit keepalive token is exhausted.")
            self.keepalive += 1
        payload = encode_action(
            records, sequence=self.sequence, keepalive=self.keepalive
        )
        path = self.data_dir / action_file_name(self.sequence)
        atomic_write_bytes(path, payload, durable=self.durable, exist_ok=False)
        self._action_stats["published"] = int(self._action_stats["published"]) + 1
        if not records:
            self._action_stats["empty_releases"] = (
                int(self._action_stats["empty_releases"]) + 1
            )
        self._append_action_audit(self.sequence, self.keepalive, records)
        return self.sequence

    def _telemetry_path(self) -> tuple[Any, Path]:
        try:
            marker = decode_telemetry_marker(read_shared_bytes(self.telemetry_publish_path))
        except OSError as exc:
            raise RuntimeError(
                f"Could not read telemetry publish pointer {self.telemetry_publish_path}: {exc}"
            ) from exc
        return marker, self.data_dir / TELEMETRY_BUFFER_FILES[marker.slot]

    def read_sample(self) -> TelemetryFrame:
        # A writer may publish the next marker while this process reads the
        # selected slot. Retry the same v4 commit protocol up to ten times;
        # this never switches formats or guesses another slot.
        mismatch = ""
        for _ in range(10):
            try:
                marker, path = self._telemetry_path()
                payload = read_shared_bytes(path)
                marker_after = decode_telemetry_marker(
                    read_shared_bytes(self.telemetry_publish_path)
                )
            except (OSError, RuntimeError) as exc:
                mismatch = f"ModIO commit was temporarily locked: {exc}"
                time.sleep(0.001)
                continue
            frame = decode_telemetry(payload)
            if marker_after != marker:
                mismatch = "BTM4 changed while reading its selected buffer."
                continue
            if (
                frame.sequence == marker.sequence
                and frame.slot == marker.slot
                and frame.simulating == marker.simulating
                and frame.sequence_applied == marker.sequence_applied
            ):
                self.gc_acked_actions(int(frame.sequence_applied))
                return frame
            mismatch = "BAT4 buffer does not match the BTM4 commit marker."
        raise TelemetryCodecError(
            f"Could not obtain one stable BAT4/BTM4 commit after 10 reads: {mismatch}"
        )

    @property
    def bulk_guids(self) -> dict[int, str]:
        if self._bulk_guids is None:
            raise RuntimeError(
                "Bulk target table is not ready; the subscription must enable the "
                "bulk channel and block_table.json must be loaded."
            )
        return dict(self._bulk_guids)

    def bulk_guid_of(self, index: int) -> str:
        if self._bulk_guids is None:
            raise RuntimeError("Bulk target table is not ready; call load_block_table().")
        self._validate_u16(index, "bulk index")
        try:
            return self._bulk_guids[index]
        except KeyError as exc:
            raise ValueError(f"No bulk target at index {index}.") from exc

    def read_bulk_batch(self) -> BulkBatch:
        """Reads one committed BAB4 batch via the bm doorbell.

        Same bounded-retry commit protocol as read_sample: marker, buffer,
        marker again; the three must agree or the read is retried, never
        silently downgraded.
        """
        mismatch = ""
        for _ in range(10):
            try:
                marker = decode_bulk_marker(read_shared_bytes(self.bulk_publish_path))
                payload = read_shared_bytes(self.data_dir / BULK_BUFFER_FILES[marker.slot])
                marker_after = decode_bulk_marker(read_shared_bytes(self.bulk_publish_path))
            except (OSError, RuntimeError) as exc:
                mismatch = f"ModIO commit was temporarily locked: {exc}"
                time.sleep(0.001)
                continue
            batch = decode_bulk_batch(payload)
            if marker_after != marker:
                mismatch = "BBM4 changed while reading its selected buffer."
                continue
            if (
                batch.batch_sequence == marker.batch_sequence
                and batch.slot == marker.slot
                and batch.simulating == marker.simulating
                and len(batch.frames) == marker.frame_count
            ):
                return batch
            mismatch = "BAB4 buffer does not match the BBM4 commit marker."
        raise BulkCodecError(
            f"Could not obtain one stable BAB4/BBM4 commit after 10 reads: {mismatch}"
        )

    def next_bulk_batch(self, timeout: float = 5.0) -> BulkBatch:
        """Waits for a bulk batch newer than the last one this client saw.

        Ordering is (episode, batch_sequence): batch sequences restart at 0
        each episode while episodes only grow within a game session.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._raise_protocol_error()
            if self.bulk_publish_path.exists():
                batch = self.read_bulk_batch()
                commit = (batch.episode, batch.batch_sequence)
                if self._last_bulk_commit is None or commit > self._last_bulk_commit:
                    self._last_bulk_commit = commit
                    return batch
            time.sleep(self.poll_interval)
        raise TimeoutError(
            f"No new BAB4 bulk batch within {timeout}s "
            f"(last commit={self._last_bulk_commit})."
        )

    def observe(self) -> TelemetryFrame:
        sample = self.read_sample()
        if not sample.simulating:
            raise RuntimeError("Besiege simulation is not running.")
        if (
            self._last_sample_sequence is not None
            and sample.sequence < self._last_sample_sequence
        ):
            raise TelemetryCodecError(
                f"BAT4 sample sequence went backwards: "
                f"{self._last_sample_sequence} -> {sample.sequence}."
            )
        self._last_sample_sequence = sample.sequence
        return sample

    def next_sample(self, timeout: float = 2.0) -> TelemetryFrame:
        baseline = self._last_sample_sequence
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._raise_protocol_error()
            sample = self.observe()
            if baseline is None or sample.sequence > baseline:
                return sample
            time.sleep(self.poll_interval)
        raise TimeoutError(
            f"No new BAT4 telemetry sample within {timeout}s "
            f"(last sequence={self._last_sample_sequence})."
        )

    def wait_until_running(
        self,
        timeout: float = 120.0,
        after_episode: int | None = None,
        after_mtime: float | None = None,
    ) -> TelemetryFrame:
        started = time.monotonic()
        deadline = started + timeout
        last_progress = started
        print(
            f"Waiting for live BAT4 telemetry (timeout {timeout:.0f}s)...",
            flush=True,
        )
        while time.monotonic() < deadline:
            self._raise_protocol_error()
            if self.telemetry_publish_path.exists():
                if (
                    after_mtime is not None
                    and self.telemetry_publish_path.stat().st_mtime <= after_mtime
                ):
                    time.sleep(self.poll_interval)
                    continue
                sample = self.read_sample()
                if sample.simulating and (
                    after_episode is None or sample.episode > after_episode
                ):
                    if self._channels_by_index is None and self.block_table_path.exists():
                        self.load_block_table()
                    self._last_sample_sequence = sample.sequence
                    print(
                        f"BAT4 live after {time.monotonic() - started:.0f}s "
                        f"(sequence={sample.sequence}).",
                        flush=True,
                    )
                    return sample
            now = time.monotonic()
            if now - last_progress >= 10.0:
                present = "present" if self.telemetry_publish_path.exists() else "missing"
                print(
                    f"  still waiting for simulation telemetry... "
                    f"{now - started:.0f}s/{timeout:.0f}s BTM4={present}",
                    flush=True,
                )
                last_progress = now
            time.sleep(self.poll_interval)
        raise TimeoutError("Besiege did not enter simulation before the timeout.")

    def read_targets(
        self, *, expected_guids: tuple[str, ...] | list[str] | None = None
    ) -> dict[str, dict[str, Any]]:
        if self._block_guids is None:
            raise RuntimeError("Block table is not ready; call load_block_table().")
        return targets_by_guid(
            self.observe(),
            block_guids=self._block_guids,
            expected_guids=expected_guids,
        )

    def wait_until_applied(
        self,
        sequence: int,
        after_time: float | None = None,
        timeout: float = 2.0,
    ) -> TelemetryFrame:
        deadline = time.monotonic() + timeout
        latest: TelemetryFrame | None = None
        while time.monotonic() < deadline:
            self._raise_protocol_error()
            latest = self.observe()
            if latest.sequence_applied >= sequence and (
                after_time is None or latest.simulation_time > after_time
            ):
                return latest
            time.sleep(self.poll_interval)
        applied = latest.sequence_applied if latest is not None else None
        raise TimeoutError(
            f"Besiege did not acknowledge action sequence {sequence}; latest={applied}."
        )

    def arm(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.armed_path.write_text("armed\n", encoding="ascii")

    def disarm(self) -> None:
        self.armed_path.unlink(missing_ok=True)

    def close(self, *, ack_timeout: float = 2.0) -> None:
        """Send an empty release snapshot, wait for ack, then disarm.

        Ack timeout still force-disarms. The timeout is raised after disarm
        so the runner can mark the release as failed.
        """
        release_error: Exception | None = None
        try:
            if self._channels_by_index is not None:
                sequence = self._send_records([], keepalive=False)
                self.wait_until_applied(sequence, timeout=ack_timeout)
        except Exception as exc:  # noqa: BLE001 - release must always disarm
            release_error = exc
        finally:
            self.disarm()
        if release_error is not None:
            raise RuntimeError(f"Control release failed: {release_error}") from release_error
