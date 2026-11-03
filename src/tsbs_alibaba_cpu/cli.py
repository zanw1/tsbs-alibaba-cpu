"""Portable command-line entry points for construction and original-source validation."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

from .build import build_dataset
from .oracle import oracle_check
from .records import VIEWS, reference_rows, save_json
from .validate import validate_dataset


def _scales(value: str) -> tuple[int, ...]:
    """Parse a requested ordered scale list without silently sorting or deduplicating it."""
    return tuple(int(part) for part in value.split(","))


def generate_references(binary: Path, output: Path, scales: tuple[int, ...], seed: int) -> None:
    """Run a supplied TSBS executable and independently validate every generated row.

    Args:
        binary: TSBS CPU-only generator executable from a documented source revision.
        output: New reference dataset directory.
        scales: Requested host counts.
        seed: Explicit nonzero deterministic data seed.
    """
    if output.exists() or seed == 0 or not scales or any(scale < 1 for scale in scales):
        raise ValueError("References need a new directory, positive scales and nonzero seed")
    output.parent.mkdir(parents=True, exist_ok=True)
    commands = []
    with tempfile.TemporaryDirectory(prefix=".tsbs-reference-", dir=output.parent) as temporary:
        stage = Path(temporary)
        for scale in scales:
            path = stage / f"sf{scale}" / "cpu.ilp"
            path.parent.mkdir()
            command = [str(binary.resolve()), "--use-case=cpu-only", f"--scale={scale}",
                       f"--initial-scale={scale}", f"--seed={seed}", "--log-interval=10s",
                       "--timestamp-start=2016-01-01T00:00:00Z", "--timestamp-end=2016-01-02T00:00:00Z", "--format=questdb"]
            with path.open("wb") as handle:
                subprocess.run(command, cwd=stage, stdout=handle, check=True,
                               env={**os.environ, "GODEBUG": "randseednop=0"})
            for _ in reference_rows(path, scale):
                pass
            commands.append(command)
        save_json(stage / "provenance.json", {"commands": commands, "GODEBUG": "randseednop=0"})
        stage.rename(output)
    return None


def main(argv: list[str] | None = None) -> None:
    """Expose commands that work from any directory with explicitly supplied input paths."""
    parser = argparse.ArgumentParser(description="Paired Alibaba/TSBS CPU datasets for TSBS-style experiments")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="Build compatible and matched TSBS datasets directly from inputs")
    for name in ("source", "references", "mapping", "output"):
        build.add_argument(f"--{name}", type=Path, required=True)
    build.add_argument("--scales", type=_scales, default=(1, 10, 100, 1000))
    build.add_argument("--day", type=int, default=0)
    build.add_argument("--query-samples", type=int, default=20)
    verify = commands.add_parser("validate", help="Compare every output to original Alibaba/TSBS inputs")
    for name in ("dataset", "source", "references"):
        verify.add_argument(f"--{name}", type=Path, required=True)
    inspect = commands.add_parser("inspect", help="Read a portable dataset manifest")
    inspect.add_argument("--dataset", type=Path, required=True)
    oracle = commands.add_parser("oracle-check", help="Check either CPU SF1 view via a temporary QuestDB table")
    oracle.add_argument("--dataset", type=Path, required=True)
    oracle.add_argument("--questdb-url", required=True)
    oracle.add_argument("--view", choices=VIEWS, default="compatible")
    generate = commands.add_parser("generate-references", help="Generate reference ILP using an external TSBS binary")
    generate.add_argument("--binary", type=Path, required=True)
    generate.add_argument("--output", type=Path, required=True)
    generate.add_argument("--scales", type=_scales, default=(1, 10, 100, 1000))
    generate.add_argument("--seed", type=int, default=123)
    args = parser.parse_args(argv)
    if args.command == "build":
        result = build_dataset(args.source, args.references, args.mapping, args.output,
                               scales=args.scales, day=args.day, query_samples=args.query_samples)
        print(json.dumps({"status": result["status"], "output": str(args.output.resolve())}))
    elif args.command == "validate":
        result = validate_dataset(args.dataset, args.source, args.references)
        print(json.dumps({"status": result["status"], "scales": list(result["scales"])}))
    elif args.command == "generate-references":
        generate_references(args.binary, args.output, args.scales, args.seed)
    elif args.command == "oracle-check":
        print(json.dumps(oracle_check(args.dataset, args.questdb_url, args.view)))
    else:
        with (args.dataset / "manifest.json").open(encoding="utf-8") as handle:
            print(json.dumps(json.load(handle), ensure_ascii=False, indent=2))
    return None
