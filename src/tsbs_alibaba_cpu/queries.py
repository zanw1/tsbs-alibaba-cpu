"""Versioned query workload; no imports or state mutation in the TSDB project."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import random

import numpy as np

from .records import BASE_NS, CPU_FIELDS, DAY_SECONDS, SLOTS, TAGS

WORKLOAD_VERSION = "cpu_q1_q15_explicit_calendar_v1"


@dataclass(frozen=True)
class QuerySpec:
    """One query family's visible host, width and duration contract."""

    operation: str
    metrics: int
    hosts: int
    hours: int


SPECS = {
    1: QuerySpec("latest", 10, 0, 0),
    2: QuerySpec("minute_max", 1, 1, 1),
    3: QuerySpec("minute_max", 5, 1, 1),
    4: QuerySpec("minute_max", 1, 1, 12),
    5: QuerySpec("minute_max", 1, 8, 1),
    6: QuerySpec("minute_max", 5, 1, 12),
    7: QuerySpec("minute_max", 5, 8, 1),
    8: QuerySpec("hour_max", 10, 8, 8),
    9: QuerySpec("hour_mean", 10, 0, 12),
    10: QuerySpec("hour_max", 10, 1, 8),
    11: QuerySpec("high_cpu", 10, 1, 12),
    12: QuerySpec("high_cpu", 10, 0, 12),
    13: QuerySpec("hour_mean", 1, 0, 12),
    14: QuerySpec("hour_mean", 5, 0, 12),
    15: QuerySpec("prefix_max", 1, 0, 1),
}


def iso_time(second: int) -> str:
    """Render the fixed experiment time axis in UTC, without local timezone defaults."""
    return (datetime(2016, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=second)).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_instance(query_id: int, scale: int, seed: int) -> dict[str, object]:
    """Generate stable parameters using local RNGs and the documented family definitions."""
    spec = SPECS[query_id]
    params: dict[str, object] = {}
    if spec.hosts:
        params["hostnames"] = [f"host_{host}" for host in random.Random(seed).sample(range(scale), min(spec.hosts, scale))]
    if spec.hours:
        start = random.Random(seed).randint(0, DAY_SECONDS - spec.hours * 3600)
        if spec.operation != "prefix_max":
            params["time_start"] = iso_time(start)
        params["time_end"] = iso_time(start + spec.hours * 3600)
    if spec.operation == "high_cpu":
        params["threshold"] = 90.0
    if spec.operation == "prefix_max":
        params["limit"] = 5
    return {"query_id": str(query_id), "scale_factor": scale, "evaluation_seed": seed,
            "params_json": params, "sql": render_sql(query_id, params),
            "workload_version": WORKLOAD_VERSION}


def render_sql(query_id: int, params: dict[str, object]) -> str:
    """Emit the same workload semantics with explicit bucket alignment and output order."""
    spec = SPECS[query_id]
    wide = ", ".join(("timestamp", *TAGS, *CPU_FIELDS))
    if spec.operation == "latest":
        return f"SELECT {wide} FROM cpu LATEST ON timestamp PARTITION BY hostname"
    predicates = []
    if "hostnames" in params:
        predicates.append("hostname IN (" + ",".join("'" + host + "'" for host in params["hostnames"]) + ")")
    if "time_start" in params:
        predicates.append(f"timestamp >= '{params['time_start']}'")
    predicates.append(f"timestamp < '{params['time_end']}'")
    if spec.operation == "high_cpu":
        predicates.append(f"usage_user > {params['threshold']}")
        return f"SELECT {wide} FROM cpu WHERE " + " AND ".join(predicates) + " ORDER BY timestamp, hostname"
    aggregate = "avg" if spec.operation == "hour_mean" else "max"
    metrics = ", ".join(f"{aggregate}({name}) AS {aggregate}_{name}" for name in CPU_FIELDS[:spec.metrics])
    keys = "timestamp, hostname" if spec.operation == "hour_mean" else "timestamp"
    order = keys
    if spec.operation == "hour_max":
        keys, order = "hour(timestamp) AS hour", "hour"
    elif spec.operation == "prefix_max":
        keys, order = "timestamp AS minute", "minute"
    bucket = "1h" if spec.operation in {"hour_mean", "hour_max"} else "1m"
    sql = f"SELECT {keys}, {metrics} FROM cpu WHERE " + " AND ".join(predicates)
    sql += f" SAMPLE BY {bucket} ALIGN TO CALENDAR ORDER BY {order}"
    if spec.operation == "prefix_max":
        sql += f" LIMIT {params['limit']}"
    return sql


def catalog(scale: int, samples: int) -> list[dict[str, object]]:
    """Keep fixed seeds and collapse duplicate parameters rather than overweighting Q1."""
    if scale < 1 or samples < 1:
        raise ValueError("scale and query samples must be positive")
    records = []
    for query_id in SPECS:
        seen = set()
        for index in range(samples):
            record = make_instance(query_id, scale, 10001 + index)
            if record["sql"] not in seen:
                records.append(record)
                seen.add(record["sql"])
    return records


def query_shape(instance: dict[str, object], cpu: np.ndarray, scale: int) -> dict[str, object]:
    """Compute observable cardinalities, preserving empty ranges and strict thresholds."""
    params = instance["params_json"]
    query_id = int(instance["query_id"])
    hosts = [int(host.removeprefix("host_")) for host in params.get("hostnames", ())] or list(range(scale))
    start = _second(params.get("time_start"), 0)
    end = _second(params.get("time_end"), DAY_SECONDS)
    begin_slot, end_slot = (start + 9) // 10, (end + 9) // 10
    if not 0 <= begin_slot <= end_slot <= SLOTS:
        raise ValueError("Query window is outside the selected experiment day")
    values = cpu[hosts, begin_slot:end_slot]
    valid = values <= 100
    input_rows = int(valid.sum())
    if query_id == 1:
        output_rows = int(valid.any(axis=1).sum())
    elif query_id in (11, 12):
        output_rows = int(((values > params["threshold"]) & valid).sum())
    else:
        width = 6 if query_id in (2, 3, 4, 5, 6, 7, 15) else 360
        bucket_ids = np.arange(begin_slot, end_slot) // width
        if query_id in (9, 13, 14):
            output_rows = sum(len(np.unique(bucket_ids[row])) for row in valid)
        else:
            output_rows = len(np.unique(bucket_ids[valid.any(axis=0)]))
        if query_id == 15:
            output_rows = min(output_rows, params["limit"])
    return {"input_rows": input_rows, "expected_output_rows": int(output_rows),
            "empty_input": input_rows == 0, "empty_output": output_rows == 0}


def _second(value: str | None, default: int) -> int:
    """Decode the catalog's whole-second UTC parameters."""
    if value is None:
        return default
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()) - BASE_NS // 1000000000
