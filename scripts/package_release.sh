#!/usr/bin/env bash
# Build a GitHub Release archive of the generated dataset.
# Excludes the raw Alibaba CSV, TSBS reference ILP, and local validation/oracle runs.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${1:-0.2.0}"
DATASET="${2:-$ROOT/data/alibaba_day1}"
OUT_DIR="${3:-$ROOT/dist/release}"
NAME="alibaba_day1-v${VERSION}"
STAGE="$OUT_DIR/.stage-$NAME"
ARCHIVE="$OUT_DIR/${NAME}.tar.gz"

if [[ ! -f "$DATASET/manifest.json" ]]; then
  echo "Dataset manifest not found: $DATASET/manifest.json" >&2
  exit 1
fi

rm -rf "$STAGE"
mkdir -p "$STAGE/$NAME" "$OUT_DIR"

python3 - "$DATASET" "$STAGE/$NAME" <<'PY'
import json
import shutil
import sys
from pathlib import Path

src = Path(sys.argv[1]).resolve()
dst = Path(sys.argv[2]).resolve()
keep = [
    "machine_mapping.csv",
    "source_profile.json",
    "compatible",
    "matched_tsbs",
    "queries",
]
for name in keep:
    path = src / name
    if not path.exists():
        raise SystemExit(f"missing required dataset member: {path}")
    target = dst / name
    if path.is_dir():
        shutil.copytree(path, target)
    else:
        shutil.copy2(path, target)

manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
manifest["source_provenance"] = {
    "description": "Alibaba cluster-trace-v2018 machine_usage.csv; not redistributed with this release.",
    "upstream": "https://github.com/alibaba/clusterdata/blob/master/cluster-trace-v2018/trace_2018.md",
    "bytes": manifest.get("source_provenance", {}).get("bytes"),
    "mtime_ns": manifest.get("source_provenance", {}).get("mtime_ns"),
}
manifest["reference_provenance"] = {
    "description": "TSBS cpu-only references generated with seed 123; rebuild with generate-references if needed.",
    "upstream": "https://github.com/timescale/tsbs",
}
manifest["release_note"] = (
    "This bundle contains only derived compatible/matched_tsbs views, queries, and mapping. "
    "Raw machine_usage.csv is not included."
)
(dst / "manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
    encoding="utf-8",
)

readme = """# alibaba_day1 release bundle

Contents:
- `compatible/sfN/cpu.ilp` — real Alibaba CPU in `usage_user`, other columns from TSBS
- `matched_tsbs/sfN/cpu.ilp` — same keys, original TSBS values
- `queries/sfN.jsonl` — fixed Q1–Q15 instances with result shapes
- `machine_mapping.csv`, `manifest.json`, `source_profile.json`

Not included:
- Alibaba raw `machine_usage.csv` (download from the official clusterdata project)
- TSBS reference ILP used during construction
- Local `validation_runs/` and `oracle_runs/`

See the repository README for install, verification, and scaling beyond SF1000.
"""
(dst / "README.md").write_text(readme, encoding="utf-8")
PY

tar -C "$STAGE" -czf "$ARCHIVE" "$NAME"
rm -rf "$STAGE"

(
  cd "$OUT_DIR"
  shasum -a 256 "$(basename "$ARCHIVE")" > "${NAME}.tar.gz.sha256"
)

python3 - "$ARCHIVE" <<'PY'
import os, sys
path = sys.argv[1]
size = os.path.getsize(path)
print(f"Wrote {path}")
print(f"Size: {size / (1024**2):.1f} MiB ({size} bytes)")
if size >= 2 * 1024**3:
    raise SystemExit("Archive exceeds GitHub's 2 GiB release-asset limit")
PY

echo "Checksum: $OUT_DIR/${NAME}.tar.gz.sha256"
