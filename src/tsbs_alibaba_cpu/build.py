"""Build complete relocatable datasets directly from raw trace and TSBS inputs."""

from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from uuid import uuid4

import numpy as np

from .queries import WORKLOAD_VERSION, catalog, query_shape
from .records import (ABSENT, BUFFER_BYTES, DATASET_FORMAT_VERSION, MISSING_CPU, SLOTS, VIEWS,
                      SourceIndex, index_source, read_mapping,
                      reference_rows, save_json, write_mapping)


def build_scale(index: SourceIndex, references: Path, root: Path, scale: int) -> dict[str, object]:
    """Export the compatible and matched-control views of valid CPU observations.

    Args:
        index: Original source index for the selected day.
        references: Directory of independently generated per-SF TSBS files.
        root: Unpublished staging directory.
        scale: Number of selected machines.

    Returns:
        Paired row counts and excluded missing-CPU accounting.
    """
    files = {view: root / view / f"sf{scale}" / "cpu.ilp" for view in VIEWS}
    paired_rows = 0
    control_grid = np.full((scale, SLOTS), ABSENT, dtype=np.uint8)
    with ExitStack() as resources:
        handles = {}
        for view, path in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            handles[view] = resources.enter_context(path.open("xb", buffering=BUFFER_BYTES))
        for host, slot, reference in reference_rows(references / f"sf{scale}" / "cpu.ilp", scale):
            cpu = int(index.cpu[host, slot])
            if cpu > 100:
                continue
            tags, fields, timestamp = reference.split(b" ")
            first, auxiliary = fields.split(b",", 1)
            handles["compatible"].write(tags + b" usage_user=" + str(cpu).encode() + b"i," + auxiliary + b" " + timestamp)
            handles["matched_tsbs"].write(reference)
            control_grid[host, slot] = int(first.split(b"=", 1)[1][:-1])
            paired_rows += 1
    if paired_rows != int((index.cpu[:scale] <= 100).sum()):
        raise RuntimeError("Export/source row conservation failed")
    return {"selected_source_rows": int((index.cpu[:scale] != ABSENT).sum()), "paired_rows": paired_rows,
            "observed_compatible_hosts": int((index.cpu[:scale] <= 100).any(axis=1).sum()),
            "excluded_missing_cpu_rows": int((index.cpu[:scale] == MISSING_CPU).sum()),
            "files": {view: str(path.relative_to(root)) for view, path in files.items()},
            "bytes": {view: path.stat().st_size for view, path in files.items()},
            "control_grid": control_grid}


def write_queries(root: Path, scale: int, samples: int, cpu: np.ndarray, control: np.ndarray) -> dict[str, object]:
    """Freeze workload parameters and shape evidence for both sides of the paired experiment."""
    path = root / "queries" / f"sf{scale}.jsonl"
    path.parent.mkdir(exist_ok=True)
    records = catalog(scale, samples)
    empty = {"compatible": 0, "matched_tsbs": 0}
    with path.open("x", encoding="utf-8") as handle:
        for record in records:
            shapes = {"compatible": query_shape(record, cpu, scale),
                      "matched_tsbs": query_shape(record, control, scale)}
            handle.write(json.dumps({**record, "shape_by_view": shapes}) + "\n")
            for view in empty:
                empty[view] += shapes[view]["empty_output"]
    return {"instances": len(records), "empty_output_instances": empty,
            "file": str(path.relative_to(root)), "all_families_present": True}


def build_dataset(source: Path, references: Path, mapping: Path, output: Path,
                  *, scales: tuple[int, ...] = (1, 10, 100, 1000), day: int = 0,
                  query_samples: int = 20) -> dict[str, object]:
    """Construct an independent dataset and publish it only when all stages finish.

    Args:
        source: Original Alibaba machine_usage.csv.
        references: Explicit TSBS inputs with sfN/cpu.ilp layout.
        mapping: Explicit machine mapping CSV.
        output: New destination; existing versions are never overwritten.
        scales: Requested positive, unique machine counts.
        day: Zero-based one-day source window, normalized to the experiment day.
        query_samples: Number of fixed seeds per family before deduplication.

    Returns:
        Manifest describing the completed dataset.
    """
    source, references, mapping, output = (path.resolve() for path in (source, references, mapping, output))
    if output.exists():
        raise ValueError(f"Dataset already exists: {output}")
    machines = read_mapping(mapping)
    if not scales or tuple(sorted(set(scales))) != scales or scales[0] < 1 or scales[-1] > len(machines):
        raise ValueError("Scales must be positive, unique, ordered, and covered by mapping")
    if day < 0 or query_samples < 1:
        raise ValueError("Invalid day or query sample count")
    machines = machines[:scales[-1]]
    output.parent.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat()
    try:
        with tempfile.TemporaryDirectory(prefix=f".{output.name}-", suffix=".partial", dir=output.parent) as temporary:
            root = Path(temporary)
            write_mapping(root / "machine_mapping.csv", machines)
            reports, queries = {}, {}
            with index_source(source, machines, day) as index:
                for scale in scales:
                    print(f"Building independent SF{scale}", flush=True)
                    report = build_scale(index, references, root, scale)
                    control = report.pop("control_grid")
                    queries[str(scale)] = write_queries(root, scale, query_samples, index.cpu, control)
                    reports[str(scale)] = report
                save_json(root / "source_profile.json", index.summary)
            manifest = {
                "format_version": DATASET_FORMAT_VERSION, "views": list(VIEWS), "status": "constructed", "day": day,
                "source_window_seconds": [day * 86400, (day + 1) * 86400],
                "experiment_time_range_utc": ["2016-01-01T00:00:00Z", "2016-01-02T00:00:00Z"],
                "mapping": "machine_mapping.csv", "scales": reports, "queries": queries,
                "workload_version": WORKLOAD_VERSION, "query_samples": query_samples,
                "source_provenance": {"original_path": str(source), "bytes": source.stat().st_size,
                                      "mtime_ns": source.stat().st_mtime_ns},
                "reference_provenance": {"original_directory": str(references)},
                "pair_policy": "Only valid CPU observations. Matched TSBS retains original reference bytes; compatible changes usage_user only.",
                "portability": "All dataset members are regular files with bundle-relative paths. Provenance paths are not used for validation; source/references are explicit CLI inputs.",
                "started_at_utc": started, "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            }
            save_json(root / "manifest.json", manifest)
            root.rename(output)
        return manifest
    except (OSError, ValueError, RuntimeError) as error:
        save_json(output.parent / f"{output.name}.failure-{uuid4().hex}.json",
                  {"status": "failed", "started_at_utc": started, "error": str(error)})
        raise
