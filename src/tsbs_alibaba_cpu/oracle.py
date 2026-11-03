"""Optional QuestDB wire-format readback; separate from original-source verification."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

from .records import CPU_FIELDS, TAGS, VIEWS, parse_cpu, save_json

HTTP_TIMEOUT_SECONDS = 60


class QuestDBClient:
    """Own short-lived HTTP requests to the caller-selected database endpoint."""

    def __init__(self, endpoint: str) -> None:
        """Store configuration without contacting the server."""
        self.endpoint = endpoint.rstrip("/")
        return None

    def _request(self, request: Request | str) -> bytes:
        """Read complete responses and retain the database's error details."""
        try:
            with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                return response.read()
        except HTTPError as error:
            with error:
                detail = error.read().decode("utf-8")
            raise RuntimeError(f"QuestDB HTTP {error.code}: {detail}") from error

    def query(self, sql: str) -> dict[str, object]:
        """Execute an internally constructed statement and reject query error responses."""
        result = json.loads(self._request(self.endpoint + "/exec?" + urlencode({"query": sql, "count": "true"})))
        if "error" in result:
            raise RuntimeError(str(result["error"]))
        return result

    def write(self, payload: bytes) -> None:
        """Require acknowledgment for the temporary table's ILP batch."""
        self._request(Request(self.endpoint + "/write?precision=n", data=payload,
                              headers={"Content-Type": "text/plain"}, method="POST"))
        return None


def oracle_check(dataset: Path, endpoint: str, view: str = "compatible") -> dict[str, object]:
    """Check complete SF1 serialization while deterministically dropping its table.

    Args:
        dataset: Bundle containing the two paired data views.
        endpoint: Explicit QuestDB HTTP URL.
        view: Compatible data or the matched TSBS control.

    Returns:
        Readback evidence. This tests transport/schema, not raw-source fidelity.
    """
    if view not in VIEWS:
        raise ValueError(f"Unsupported data view: {view}")
    lines = (dataset / view / "sf1" / "cpu.ilp").read_bytes().splitlines(keepends=True)
    expected = []
    for line in lines:
        tags, values, timestamp = parse_cpu(line)
        iso = datetime.fromtimestamp(timestamp // 1000000000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z")
        expected.append([iso, *[tags[name] for name in TAGS], *[values[name] for name in CPU_FIELDS]])
    client = QuestDBClient(endpoint)
    table = "tsbs_alibaba_cpu_check_" + uuid4().hex
    evidence: dict[str, object] = {"status": "failed", "scope": "SF1 CPU database transport/schema readback", "view": view, "table": table}
    directory = dataset / "oracle_runs"
    directory.mkdir(exist_ok=True)
    columns = ["timestamp TIMESTAMP"] + [name + " SYMBOL" for name in TAGS] + [name + " LONG" for name in CPU_FIELDS]
    try:
        evidence["server_build"] = client.query("SELECT build()")["dataset"][0][0]
        client.query(f"CREATE TABLE {table} (" + ",".join(columns) + ") TIMESTAMP(timestamp) PARTITION BY DAY WAL")
        if lines:
            client.write(b"".join(table.encode() + line[len(b"cpu"):] for line in lines))
        deadline = time.monotonic() + HTTP_TIMEOUT_SECONDS
        while client.query(f"SELECT count() FROM {table}")["dataset"][0][0] != len(expected):
            if time.monotonic() > deadline:
                raise RuntimeError("QuestDB rows did not become visible")
            time.sleep(0.1)
        actual = client.query("SELECT timestamp," + ",".join((*TAGS, *CPU_FIELDS)) + f" FROM {table} ORDER BY timestamp")
        if actual.get("count") != len(expected) or actual["dataset"] != expected:
            raise RuntimeError("QuestDB scalar/NULL readback mismatch")
        evidence.update({"status": "passed", "rows": len(expected), "all_21_columns_equal": True})
    except (OSError, ValueError, RuntimeError) as error:
        evidence["error"] = str(error)
        raise
    finally:
        try:
            client.query(f"DROP TABLE IF EXISTS {table}")
            evidence["temporary_table_dropped"] = True
        except (OSError, ValueError, RuntimeError) as error:
            evidence.update({"status": "failed", "temporary_table_dropped": False,
                             "cleanup_error": str(error)})
            raise
        finally:
            evidence["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
            save_json(directory / f"{uuid4().hex}.json", evidence)
    return evidence
