# Changelog

## 0.2.0 (dataset format 4)

- Keep only the two paired views, `compatible` and `matched_tsbs`. The `native` seven-metric Alibaba view and its schema, export and validation were removed.
- The kept views are byte-identical to 0.1.0 at all four scale factors.
- `oracle-check` reads back either kept view (SF1, all 21 columns).
- README documents GitHub Release downloads (derived data only; raw `machine_usage.csv` stays upstream) and how to scale beyond SF1000.
- `scripts/package_release.sh` builds the release tarball without raw CSV, TSBS references, or local validation/oracle runs.

## 0.1.0

- First standalone package with three views: `native`, `compatible` and `matched_tsbs`. See [`docs/history/v3/`](docs/history/v3/).
