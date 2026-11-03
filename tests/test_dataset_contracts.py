"""Observable contracts for raw fidelity, independent rebuilding and validation."""

from pathlib import Path

import pytest


def make_inputs(root: Path, raw: str | None = None) -> tuple[Path, Path, Path]:
    """Create independently specified source rows and complete TSBS reference keys."""
    source = root / "source.csv"
    source.write_text(raw or "m_1,0,41,92,1.25,4,2.30,3.40,-1\nm_1,10,,88,,,2,3,5\nm_2,0,90,40,,,0,1,2")
    mapping = root / "mapping.csv"
    mapping.write_text("hostname,machine_id\nhost_0,m_1\nhost_1,m_2\n")
    references = root / "references"
    names = ("usage_user", "usage_system", "usage_idle", "usage_nice", "usage_iowait", "usage_irq", "usage_softirq", "usage_steal", "usage_guest", "usage_guest_nice")
    values = (77, 2, 3, 4, 5, 6, 7, 8, 9, 10)
    fields = ",".join(f"{name}={value}i" for name, value in zip(names, values))
    for scale in (1, 2):
        path = references / f"sf{scale}" / "cpu.ilp"
        path.parent.mkdir(parents=True)
        with path.open("w") as handle:
            for slot in range(8640):
                for host in range(scale):
                    tags = f"cpu,hostname=host_{host},region=r,datacenter=d,rack=1,os=o,arch=a,team=t,service=1,service_version=0,service_environment=test"
                    handle.write(f"{tags} {fields} {1451606400000000000+slot*10000000000}\n")
    return source, references, mapping


def make_bundle(root: Path, raw: str | None = None, day: int = 0) -> tuple[Path, Path, Path]:
    """Construct real artifacts using only explicit input paths."""
    from tsbs_alibaba_cpu.build import build_dataset

    source, references, mapping = make_inputs(root, raw)
    target = root / "dataset"
    build_dataset(source, references, mapping, target, scales=(1, 2), day=day, query_samples=2)
    return target, source, references


def test_build_has_only_paired_views_and_counts_missing_cpu(workspace: Path) -> None:
    """Only compatible and matched TSBS data are published; missing CPU stays excluded."""
    import json

    target, _, _ = make_bundle(workspace)
    assert not (target / "native").exists()
    assert len((target / "compatible/sf1/cpu.ilp").read_text().splitlines()) == 1
    assert len((target / "matched_tsbs/sf1/cpu.ilp").read_text().splitlines()) == 1
    manifest = json.loads((target / "manifest.json").read_text())
    assert manifest["views"] == ["compatible", "matched_tsbs"]
    assert manifest["scales"]["1"]["excluded_missing_cpu_rows"] == 1
    assert not any(path.is_symlink() for path in target.rglob("*"))
    return None


def test_validator_rejects_changed_alibaba_cpu_value(workspace: Path) -> None:
    """The compatible CPU value must still agree with the original Alibaba CSV."""
    from tsbs_alibaba_cpu.validate import validate_dataset

    target, source, refs = make_bundle(workspace)
    path = target / "compatible/sf1/cpu.ilp"
    path.write_text(path.read_text().replace("usage_user=41i", "usage_user=42i"))
    with pytest.raises(RuntimeError, match="CPU|source"):
        validate_dataset(target, source, refs)
    return None


def test_validator_rejects_joint_auxiliary_changes(workspace: Path) -> None:
    """Changing both paired files cannot evade comparison with original TSBS."""
    from tsbs_alibaba_cpu.validate import validate_dataset

    target, source, refs = make_bundle(workspace)
    for view in ("compatible", "matched_tsbs"):
        path = target / view / "sf1/cpu.ilp"
        path.write_text(path.read_text().replace("usage_system=2i", "usage_system=3i"))
    with pytest.raises(RuntimeError, match="reference|TSBS|auxiliary"):
        validate_dataset(target, source, refs)
    return None


def test_validator_rejects_empty_query_catalog(workspace: Path) -> None:
    """A dataset cannot claim query validation when its catalog has been removed."""
    from tsbs_alibaba_cpu.validate import validate_dataset

    target, source, refs = make_bundle(workspace)
    (target / "queries/sf1.jsonl").write_text("")
    with pytest.raises(RuntimeError, match="query|catalog"):
        validate_dataset(target, source, refs)
    return None


def test_failed_build_does_not_publish_partial_dataset(workspace: Path) -> None:
    """Duplicate source keys leave a diagnostic but no seemingly complete output directory."""
    from tsbs_alibaba_cpu.build import build_dataset

    source, refs, mapping = make_inputs(workspace, "m_1,0,41,92,,,1,2,3\nm_1,0,42,92,,,1,2,3\n")
    target = workspace / "dataset"
    with pytest.raises(RuntimeError, match="Duplicate"):
        build_dataset(source, refs, mapping, target, scales=(1, 2), query_samples=2)
    assert not target.exists()
    assert not list(workspace.glob("*.partial"))
    return None


def test_later_day_keeps_relative_observation_time(workspace: Path) -> None:
    """An explicit later-day window maps to the same experiment day without rounding."""
    target, _, _ = make_bundle(workspace, "m_1,86410,41,92,,,1,2,3\nm_2,86420,2,40,,,1,2,3\n", day=1)
    assert (target / "compatible/sf1/cpu.ilp").read_text().endswith("1451606410000000000\n")
    return None


def test_lastpoint_counts_only_observed_hosts() -> None:
    """A selected machine with no valid CPU reading contributes no latest compatible row."""
    import numpy as np
    from tsbs_alibaba_cpu.queries import query_shape

    values = np.full((2, 8640), 255, dtype=np.uint8)
    values[0, 0] = 41
    result = query_shape({"query_id": "1", "params_json": {}}, values, 2)
    assert result["expected_output_rows"] == 1
    return None


def test_integral_decimal_timestamp_is_not_rounded_or_lost(workspace: Path) -> None:
    """The documented double timestamp type can spell integral seconds as 10.0."""
    target, _, _ = make_bundle(workspace, "m_1,10.0,41,92,,,1,2,3\nm_2,20.0,2,40,,,1,2,3\n")
    assert (target / "compatible/sf1/cpu.ilp").read_text().endswith("1451606410000000000\n")
    return None


def test_bundle_validates_after_relocation(workspace: Path) -> None:
    """Moving the dataset does not require its original parent directory or absolute mapping path."""
    from tsbs_alibaba_cpu.validate import validate_dataset

    target, source, refs = make_bundle(workspace)
    moved = workspace / "different_parent" / "relocated"
    moved.parent.mkdir()
    target.rename(moved)
    report = validate_dataset(moved, source, refs)
    assert report["status"] == "passed"
    return None


def test_query_generation_preserves_global_rng_and_avoids_duplicate_latest() -> None:
    """Standalone generation must not alter unrelated random sampling or overweight Q1."""
    import random
    from tsbs_alibaba_cpu.queries import catalog, make_instance

    state = random.getstate()
    values = catalog(100, 20)
    assert random.getstate() == state
    assert len([row for row in values if row["query_id"] == "1"]) == 1
    assert "hour(timestamp) AS hour" in make_instance(8, 100, 10001)["sql"]
    assert "AS minute" in make_instance(15, 100, 10001)["sql"]
    return None


def test_oracle_cleanup_failure_cannot_leave_passed_evidence(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed temporary-table cleanup must remain a failed verification attempt."""
    import json
    import tsbs_alibaba_cpu.oracle as oracle

    class Client:
        """Represent only the external database response boundary."""

        def __init__(self, endpoint: str) -> None:
            """Accept the unused external endpoint in this contract test."""
            return None

        def write(self, payload: bytes) -> None:
            """Acknowledge the explicitly modeled one-row compatible write."""
            return None

        def query(self, sql: str) -> dict:
            """Return known source rows, then reproduce a failed DROP operation."""
            if sql.startswith("DROP"):
                raise RuntimeError("cleanup failed")
            if sql == "SELECT build()":
                return {"dataset": [["test server"]]}
            if sql.startswith("SELECT count"):
                return {"dataset": [[1]]}
            if sql.startswith("SELECT timestamp"):
                return {"count": 1, "dataset": [
                    ["2016-01-01T00:00:00.000000Z", "host_0", "r", "d", "1", "o", "a", "t", "1", "0", "test",
                     41, 2, 3, 4, 5, 6, 7, 8, 9, 10]]}
            return {}

    target, _, _ = make_bundle(workspace)
    monkeypatch.setattr(oracle, "QuestDBClient", Client)
    with pytest.raises(RuntimeError, match="cleanup failed"):
        oracle.oracle_check(target, "unused")
    reports = [json.loads(path.read_text()) for path in (target / "oracle_runs").glob("*.json")]
    assert reports[0]["status"] == "failed"
    return None
