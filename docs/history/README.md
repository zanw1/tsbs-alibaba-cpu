# Historical records

These files are frozen records from earlier versions of this dataset. They are kept for traceability. They are **not** the current interface. Paths, view names and internal links inside them may refer to directories or projects that no longer exist. For the current behaviour, see the top-level [README](../../README.md).

| Directory | Contents |
|---|---|
| `v2/` | Statistics from the second dataset iteration: source profile per trace day, CPU distribution distance of the selected machines, query coverage, validation results. |
| `v3/` | Package 0.1.0, which had three views including a `native` seven-metric Alibaba view (since removed). Includes the research report on how other TSDB benchmarks construct data, its source list, and a code review. |
| `0.2.0/` | The scope change that removed the `native` view, plus migration checks. The checks show that the kept views are byte-identical to v3 and that the new query parameters match the original generator across 1,200 combinations. |
