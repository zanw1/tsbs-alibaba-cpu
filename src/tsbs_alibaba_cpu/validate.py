"""Verify all dataset values against original inputs, not another generated output."""

from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

import numpy as np

from .queries import WORKLOAD_VERSION, catalog, query_shape
from .records import (ABSENT, BUFFER_BYTES, DATASET_FORMAT_VERSION, MISSING_CPU, SLOTS, VIEWS,
                      SourceIndex, index_source,
                      read_mapping, reference_rows, save_json)


def _verify_pair(hybrid: bytes, control: bytes, reference: bytes, cpu_value: str) -> int:
    """Use the original TSBS input as reference for both generated paired views."""
    if control != reference:
        raise RuntimeError("Matched TSBS file differs from original reference")
    try:
        tags, fields, timestamp = hybrid.split(b" ")
        ref_tags, ref_fields, ref_time = reference.split(b" ")
        first, auxiliary = fields.split(b",", 1)
        ref_first, ref_auxiliary = ref_fields.split(b",", 1)
    except ValueError as error:
        raise RuntimeError("Malformed compatible/reference row") from error
    if tags != ref_tags or timestamp != ref_time or auxiliary != ref_auxiliary:
        raise RuntimeError("Compatible auxiliary values differ from TSBS reference")
    if first != b"usage_user=" + cpu_value.encode() + b"i":
        raise RuntimeError("Compatible CPU differs from original source")
    return int(ref_first.split(b"=", 1)[1][:-1])


def _verify_queries(root: Path, scale: int, samples: int, cpu: np.ndarray, control: np.ndarray) -> int:
    """Require the complete versioned catalog and recompute both views' cardinalities."""
    path = root / "queries" / f"sf{scale}.jsonl"
    with path.open(encoding="utf-8") as handle:
        actual = [json.loads(line) for line in handle]
    expected = catalog(scale, samples)
    if len(actual) != len(expected):
        raise RuntimeError(f"query catalog count mismatch for SF{scale}")
    for given, wanted in zip(actual, expected):
        if {key: value for key, value in given.items() if key != "shape_by_view"} != wanted:
            raise RuntimeError(f"query catalog parameters/SQL mismatch for SF{scale}")
        shapes = {"compatible": query_shape(wanted, cpu, scale),
                  "matched_tsbs": query_shape(wanted, control, scale)}
        if given.get("shape_by_view") != shapes:
            raise RuntimeError(f"query shape mismatch for SF{scale}, Q{wanted['query_id']}")
    return len(actual)


def _verify_scale(root: Path, references: Path, index: SourceIndex, scale: int, samples: int) -> dict[str, object]:
    """Check completeness by enumerating source keys, not by trusting manifest counts.

    Args:
        root: Portable dataset bundle.
        references: Original TSBS input data.
        index: Fresh index built from original Alibaba CSV for this verification.
        scale: Selected machine count.
        samples: Declared query sample count.

    Returns:
        Verified counts and complete source comparison results.
    """
    paired_count = 0
    control_grid = np.full((scale, SLOTS), ABSENT, dtype=np.uint8)
    with ExitStack() as resources:
        handles = {view: resources.enter_context((root / view / f"sf{scale}" / "cpu.ilp").open("rb", buffering=BUFFER_BYTES))
                   for view in VIEWS}
        for host, slot, reference in reference_rows(references / f"sf{scale}" / "cpu.ilp", scale):
            cpu = int(index.cpu[host, slot])
            if cpu > 100:
                continue
            control_grid[host, slot] = _verify_pair(handles["compatible"].readline(), handles["matched_tsbs"].readline(), reference, str(cpu))
            paired_count += 1
        if any(handle.read(1) for handle in handles.values()):
            raise RuntimeError("Extra observation not present in original source/reference")
    query_count = _verify_queries(root, scale, samples, index.cpu, control_grid)
    return {"status": "passed", "selected_source_rows": int((index.cpu[:scale] != ABSENT).sum()),
            "paired_rows": paired_count, "alibaba_cpu_matches_source": True,
            "paired_auxiliary_values_match_original_tsbs": True,
            "excluded_missing_cpu_rows": int((index.cpu[:scale] == MISSING_CPU).sum()),
            "query_instances_verified": query_count}


def validate_dataset(root: Path, source: Path, references: Path) -> dict[str, object]:
    """Revalidate a relocated bundle with explicit raw/reference inputs and append evidence.

    Args:
        root: Dataset bundle, which may have moved since it was built.
        source: Original CSV supplied by the operator, not an embedded absolute path.
        references: Original TSBS input directory supplied by the operator.

    Returns:
        Complete source-verification evidence; failures also leave an attempt record.
    """
    root = root.resolve()
    identity = uuid4().hex
    evidence_dir = root / "validation_runs"
    evidence_dir.mkdir(exist_ok=True)
    started = datetime.now(timezone.utc).isoformat()
    try:
        with (root / "manifest.json").open(encoding="utf-8") as handle:
            manifest = json.load(handle)
        if manifest.get("format_version") != DATASET_FORMAT_VERSION or manifest.get("workload_version") != WORKLOAD_VERSION:
            raise RuntimeError("Unsupported dataset or workload version")
        if manifest.get("views") != list(VIEWS):
            raise RuntimeError("Dataset must declare exactly the two paired views")
        if any(path.is_symlink() for path in root.rglob("*")):
            raise RuntimeError("Dataset bundle must contain regular files, not external links")
        machines = read_mapping(root / manifest["mapping"])
        scales = tuple(int(value) for value in manifest["scales"])
        if not scales or scales != tuple(sorted(set(scales))) or scales[-1] != len(machines):
            raise RuntimeError("Dataset scales and mapping disagree")
        reports = {}
        with index_source(source, machines, int(manifest["day"])) as index:
            for scale in scales:
                print(f"Verifying raw-source fidelity SF{scale}", flush=True)
                report = _verify_scale(root, references, index, scale, int(manifest["query_samples"]))
                declared = manifest["scales"][str(scale)]
                if any(report[key] != declared[key] for key in ("selected_source_rows", "paired_rows", "excluded_missing_cpu_rows")):
                    raise RuntimeError("Manifest accounting differs from source verification")
                reports[str(scale)] = report
        evidence = {"status": "passed", "started_at_utc": started,
                    "completed_at_utc": datetime.now(timezone.utc).isoformat(), "scales": reports,
                    "source": str(source.resolve()), "references": str(references.resolve()),
                    "scope": "Alibaba CPU, paired keys and TSBS auxiliary values checked against original inputs; query shapes checked, no database timings."}
        save_json(evidence_dir / f"{identity}.json", evidence)
        return evidence
    except (OSError, ValueError, RuntimeError) as error:
        save_json(evidence_dir / f"{identity}.json", {"status": "failed", "started_at_utc": started, "error": str(error)})
        raise
