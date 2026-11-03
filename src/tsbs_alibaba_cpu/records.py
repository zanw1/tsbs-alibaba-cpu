"""Source, mapping and wire-format contracts shared by dataset operations."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path

import numpy as np

DAY_SECONDS = 86400
INTERVAL_SECONDS = 10
SLOTS = DAY_SECONDS // INTERVAL_SECONDS
BASE_NS = 1451606400000000000
SECOND_NS = 1000000000
ABSENT = 255
MISSING_CPU = 254
BUFFER_BYTES = 1024 * 1024
DATASET_FORMAT_VERSION = 4
VIEWS = ("compatible", "matched_tsbs")
TAGS = ("hostname", "region", "datacenter", "rack", "os", "arch", "team", "service", "service_version", "service_environment")
CPU_FIELDS = ("usage_user", "usage_system", "usage_idle", "usage_nice", "usage_iowait", "usage_irq", "usage_softirq", "usage_steal", "usage_guest", "usage_guest_nice")


@dataclass(frozen=True)
class RawRecord:
    """The Alibaba identity, time and CPU value used by the paired experiment."""

    machine: str
    second: int
    cpu: int | None


def parse_second(value: str | bytes) -> int:
    """Accept integral decimal seconds while rejecting any fractional timestamp."""
    try:
        return int(value)
    except ValueError:
        text = value.decode("ascii") if isinstance(value, bytes) else value
        try:
            number = Decimal(text)
            if not number.is_finite() or number != number.to_integral_value():
                raise ValueError("fractional timestamp cannot be matched to the TSBS grid")
            return int(number)
        except (InvalidOperation, ValueError) as error:
            raise RuntimeError(f"Invalid source timestamp: {text}") from error


def parse_raw(line: bytes, row_number: int = 0) -> RawRecord:
    """Read the source CPU contract; other raw metrics do not enter this dataset."""
    parts = line.decode("ascii").rstrip("\r\n").split(",")
    if len(parts) != 9:
        raise RuntimeError(f"Source row {row_number}: expected nine columns")
    try:
        second = parse_second(parts[1])
        cpu = int(parts[2]) if parts[2] else None
        if cpu is not None and not 0 <= cpu <= 100:
            raise ValueError("CPU outside [0, 100]")
    except (InvalidOperation, ValueError) as error:
        raise RuntimeError(f"Source row {row_number}: {error}") from error
    if not parts[0].startswith("m_") or not parts[0][2:].isdigit() or second < 0:
        raise RuntimeError(f"Source row {row_number}: invalid machine or time")
    return RawRecord(parts[0], second, cpu)


def read_mapping(path: Path) -> list[str]:
    """Read an explicit ordered machine population without depending on a v1 dataset."""
    with path.open(encoding="utf-8", newline="") as handle:
        records = list(csv.DictReader(handle))
    if not records:
        raise ValueError("Machine mapping is empty")
    machines = [row["machine_id"] for row in records]
    if [row["hostname"] for row in records] != [f"host_{index}" for index in range(len(records))] or len(set(machines)) != len(machines):
        raise ValueError("Mapping requires unique machines and consecutive host_i names")
    return machines


def write_mapping(path: Path, machines: list[str]) -> None:
    """Keep the selected mapping inside the output bundle for relocation."""
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["hostname", "machine_id"])
        writer.writerows((f"host_{index}", machine) for index, machine in enumerate(machines))
    return None


def save_json(path: Path, payload: object) -> None:
    """Write complete JSON metadata; reject unsupported values instead of coercing them."""
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
    return None


def parse_cpu(line: bytes) -> tuple[dict[str, str], dict[str, int], int]:
    """Decode the TSBS-compatible schema for database readback."""
    try:
        tags, fields, timestamp = line.decode("ascii").rstrip("\n").split(" ")
        measurement, *tag_tokens = tags.split(",")
        tag_pairs = [item.split("=", 1) for item in tag_tokens]
        field_pairs = [item.split("=", 1) for item in fields.split(",")]
        if measurement != "cpu" or tuple(key for key, _ in tag_pairs) != TAGS:
            raise ValueError("unexpected measurement/tag schema")
        if tuple(key for key, _ in field_pairs) != CPU_FIELDS:
            raise ValueError("unexpected metric schema")
        values = {}
        for name, token in field_pairs:
            if not token.endswith("i"):
                raise ValueError("integer field requires i suffix")
            values[name] = int(token[:-1])
            if not 0 <= values[name] <= 100:
                raise ValueError("CPU metric outside [0, 100]")
        return dict(tag_pairs), values, int(timestamp)
    except ValueError as error:
        raise RuntimeError(f"Invalid CPU ILP: {error}") from error


@dataclass
class SourceIndex:
    """Bounded source CPU grid; absent timestamps remain distinct from missing CPU."""

    cpu: np.ndarray
    day: int
    summary: dict[str, object]


@contextmanager
def index_source(source: Path, machines: list[str], day: int) -> Iterator[SourceIndex]:
    """Stream the raw trace into a bounded CPU grid for the selected window.

    Args:
        source: Raw Alibaba CSV, independent of this package's location.
        machines: Ordered mapping for the largest requested scale.
        day: Zero-based one-day source window.

    Yields:
        In-memory index whose source file is closed before it is consumed.
    """
    if day < 0:
        raise ValueError("day must be non-negative")
    lookup = {machine.encode(): host for host, machine in enumerate(machines)}
    cpu = np.full((len(machines), SLOTS), ABSENT, dtype=np.uint8)
    daily_rows: Counter[int] = Counter()
    selected = 0
    source_start = day * DAY_SECONDS
    with source.open("rb", buffering=BUFFER_BYTES) as handle:
        for row_number, line in enumerate(handle, 1):
            prefix = line.split(b",", 3)
            if len(prefix) != 4:
                raise RuntimeError(f"Source row {row_number}: malformed CSV")
            second = parse_second(prefix[1])
            daily_rows[second // DAY_SECONDS] += 1
            if row_number % 25000000 == 0:
                print(f"Source: {row_number:,} rows", flush=True)
            host = lookup.get(prefix[0])
            if host is None or not source_start <= second < source_start + DAY_SECONDS:
                continue
            record = parse_raw(line, row_number)
            if second % INTERVAL_SECONDS:
                raise RuntimeError(f"Source row {row_number}: timestamp is off the 10-second grid")
            slot = (second - source_start) // INTERVAL_SECONDS
            if cpu[host, slot] != ABSENT:
                raise RuntimeError(f"Duplicate source key: {record.machine}, {record.second}")
            cpu[host, slot] = record.cpu if record.cpu is not None else MISSING_CPU
            selected += 1
        if not selected:
            raise RuntimeError("No source observations in selected window/population")
    summary = {"total_source_rows": sum(daily_rows.values()), "rows_by_day": dict(sorted(daily_rows.items())),
               "selected_rows": selected, "selected_missing_cpu_rows": int((cpu == MISSING_CPU).sum())}
    yield SourceIndex(cpu, day, summary)
    return None


def reference_rows(path: Path, scale: int) -> Iterator[tuple[int, int, bytes]]:
    """Require the complete, ordered CPU-only TSBS input instead of trusting its file name."""
    count = 0
    with path.open("rb", buffering=BUFFER_BYTES) as handle:
        for position, line in enumerate(handle):
            slot, host = divmod(position, scale)
            try:
                tags, metrics, stamp = line.rstrip(b"\n").split(b" ")
                tag_pairs = [item.split(b"=", 1) for item in tags.split(b",")[1:]]
                field_pairs = [item.split(b"=", 1) for item in metrics.split(b",")]
                valid = (slot < SLOTS and tags.startswith(f"cpu,hostname=host_{host},".encode())
                         and tuple(key.decode() for key, _ in tag_pairs) == TAGS
                         and tuple(key.decode() for key, _ in field_pairs) == CPU_FIELDS
                         and int(stamp) == BASE_NS + slot * INTERVAL_SECONDS * SECOND_NS)
                if not valid or any(not token.endswith(b"i") or not 0 <= int(token[:-1]) <= 100 for _, token in field_pairs):
                    raise ValueError("schema, type, ordering or timestamp mismatch")
            except ValueError as error:
                raise RuntimeError(f"TSBS reference {path}, row {position+1}: {error}") from error
            count += 1
            yield host, slot, line
    if count != scale * SLOTS:
        raise RuntimeError(f"TSBS reference count {count}; expected {scale*SLOTS}")
    return None
