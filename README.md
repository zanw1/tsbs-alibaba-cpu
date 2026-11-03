# Alibaba CPU Trace × TSBS: paired real/synthetic CPU datasets for time-series database experiments

[中文说明](README.zh-CN.md)

This project turns one day of real machine CPU utilization from the [Alibaba cluster trace v2018](https://github.com/alibaba/clusterdata/blob/master/cluster-trace-v2018/trace_2018.md) into two drop-in variants of the [TSBS](https://github.com/timescale/tsbs) `cpu-only` dataset, plus a fixed Q1–Q15 query workload with precomputed result sizes.

It is a research artifact, not an official Alibaba or Timescale project.

## Why

TSBS `cpu-only` data is fully synthetic: every host reports every 10 seconds, and every metric is a smooth bounded random walk. Real monitoring data looks different in two independent ways:

1. **Row shape.** Real machines have gaps. Only about 60% of the 10-second grid is actually observed in the selected Alibaba day, and per-machine row counts range from 79 to 7,630.
2. **Values.** Real CPU utilization is skewed and bursty. It has rare spikes above 90%, which matter for threshold queries.

If you swap TSBS for a real trace, both change at once, and you cannot tell which one moved your results. This project builds two views that share **exactly the same rows** so you can separate the two effects:

| Comparison | Row shape | `usage_user` values | What it isolates |
|---|---|---|---|
| full TSBS → `matched_tsbs` | dense → real sparse | TSBS | effect of real gaps / sparsity |
| `matched_tsbs` → `compatible` | identical | TSBS → real Alibaba | effect of real CPU values |

The two views are byte-identical except for the `usage_user` field. The same host and timestamp in each view:

```text
# compatible: usage_user is real Alibaba CPU
cpu,hostname=host_0,region=eu-central-1,...,service_environment=test usage_user=32i,usage_system=31i,usage_idle=21i,... 1451619710000000000
# matched_tsbs: the original TSBS row for the same key
cpu,hostname=host_0,region=eu-central-1,...,service_environment=test usage_user=17i,usage_system=31i,usage_idle=21i,... 1451619710000000000
```

## What you get

Rows are the 10-second slots where the selected Alibaba machine actually reported a valid CPU value. Gaps are kept as gaps; nothing is interpolated or filled.

| Scale factor (hosts) | Rows per view | Full TSBS rows | Grid coverage | Query instances | Rows with `usage_user > 90` (compatible) |
|---:|---:|---:|---:|---:|---:|
| 1 | 4,800 | 8,640 | 55.6% | 281 | 0 |
| 10 | 50,366 | 86,400 | 58.3% | 281 | 2 |
| 100 | 527,685 | 864,000 | 61.1% | 281 | 98 |
| 1000 | 5,223,307 | 8,640,000 | 60.5% | 281 | 998 |

Smaller scale factors use a prefix of the same machine list, so SF1 ⊂ SF10 ⊂ SF100 ⊂ SF1000 for machines, timestamps and CPU values. The synthetic TSBS columns are generated separately for each scale factor, as TSBS itself does, so they are not nested.

SF1 and SF10 are suitable for correctness and smoke tests. Their CPU distribution differs noticeably from the full first-day population: the maximum empirical CDF distance is about 0.19 for SF1 and 0.13 for SF10, against about 0.016 for SF100 and 0.011 for SF1000. These distances are descriptive only, not a significance test.

### Schema

Each view uses the TSBS `cpu-only` schema unchanged. The files are in InfluxDB line protocol, the same layout that `tsbs_generate_data --format=questdb` produces.

- Measurement: `cpu`.
- Tags (10): `hostname` (`host_0` … `host_{N-1}`) plus nine synthetic TSBS tags: `region`, `datacenter`, `rack`, `os`, `arch`, `team`, `service`, `service_version`, `service_environment`.
- Fields (10, integer `i` suffix, 0–100): `usage_user`, `usage_system`, `usage_idle`, `usage_nice`, `usage_iowait`, `usage_irq`, `usage_softirq`, `usage_steal`, `usage_guest`, `usage_guest_nice`.
- Timestamps: nanoseconds, on a 10-second grid within `[2016-01-01T00:00:00Z, 2016-01-02T00:00:00Z)`. Alibaba's relative seconds are shifted onto this day unchanged.

> **Semantics caveat.** In `compatible`, `usage_user` holds Alibaba's `cpu_util_percent`, which is *total* machine CPU utilization, not user-mode CPU. The other nine fields remain TSBS-synthetic and are not physically consistent with it. For example, the fields do not sum to 100.

### Query workload (`queries/sfN.jsonl`)

There are 15 query families, each mirroring one TSBS `cpu-only` query type. Parameters come from fixed seeds 10001–10020. Instances with identical SQL are deduplicated, which matters for Q1 because it has no parameters, so each scale factor has 281 instances.

| ID | Closest TSBS type | Definition in this workload |
|---|---|---|
| Q1 | `lastpoint` | latest row per host, all columns |
| Q2 | `single-groupby-1-1-1` | per-minute max of 1 metric, 1 host, 1 h |
| Q3 | `single-groupby-5-1-1` | per-minute max of 5 metrics, 1 host, 1 h |
| Q4 | `single-groupby-1-1-12` | per-minute max of 1 metric, 1 host, 12 h |
| Q5 | `single-groupby-1-8-1` | per-minute max of 1 metric, 8 hosts, 1 h |
| Q6 | `single-groupby-5-1-12` | per-minute max of 5 metrics, 1 host, 12 h |
| Q7 | `single-groupby-5-8-1` | per-minute max of 5 metrics, 8 hosts, 1 h |
| Q8 | `cpu-max-all-8` | hourly max of all 10 metrics, 8 hosts, 8 h |
| Q9 | `double-groupby-all` | hourly mean of all 10 metrics per host, all hosts, 12 h |
| Q10 | `cpu-max-all-1` | hourly max of all 10 metrics, 1 host, 8 h |
| Q11 | `high-cpu-1` | rows with `usage_user > 90`, 1 host, 12 h |
| Q12 | `high-cpu-all` | rows with `usage_user > 90`, all hosts, 12 h |
| Q13 | `double-groupby-1` | hourly mean of 1 metric per host, all hosts, 12 h |
| Q14 | `double-groupby-5` | hourly mean of 5 metrics per host, all hosts, 12 h |
| Q15 | `groupby-orderby-limit` | per-minute max of `usage_user` from day start to a random end, first 5 buckets |

Each JSONL line contains:

- `query_id` and `scale_factor`;
- `evaluation_seed` and `params_json`, which hold the hosts, time range, threshold and limit;
- `sql`;
- `workload_version` (`cpu_q1_q15_explicit_calendar_v1`);
- `shape_by_view`: for each view, the number of input rows in range, the expected number of output rows, and whether the instance is empty.

You can use `shape_by_view` to check a database's result cardinality without running a reference database.

Things to know before using the queries:

- The SQL uses the **QuestDB dialect** (`SAMPLE BY … ALIGN TO CALENDAR`, `LATEST ON … PARTITION BY`). For other systems, translate from `params_json`, not from the SQL text.
- Q15 sorts ascending, so it returns the first five minute buckets. TSBS's `groupby-orderby-limit` returns the last five buckets before the end time.
- **Empty results are kept on purpose.** Real CPU rarely exceeds 90%, so many Q11/Q12 instances return nothing. A fast empty result is not evidence of fast filtering. The number of instances with empty output, out of 281, is:

| SF | `compatible` | `matched_tsbs` |
|---:|---:|---:|
| 1 | 44 | 8 |
| 10 | 37 | 18 |
| 100 | 19 | 4 |
| 1000 | 27 | 16 |

## Getting the data

The generated datasets (about 4 GB for both views at all four scale factors) are **not hosted yet**. You rebuild them from the public inputs below. The build is deterministic, so you can compare your output with [`docs/checksums.sha256`](docs/checksums.sha256).

Requirements:

- Python 3.11+ and [uv](https://docs.astral.sh/uv/).
- About 20 GB of free disk space: 9 GB for the raw CSV, 3.3 GB for the TSBS references, 4 GB for the output, and 1.8 GB for the downloaded archive.
- Go, only if you generate the TSBS references yourself.

On an Apple M4 with 32 GB of RAM, building or validating all four scale factors takes about 2 minutes each. Peak memory is small, because the CPU grid is 1000 × 8640 bytes.

### 1. Install

```bash
git clone https://github.com/zanw1/tsbs-alibaba-cpu.git && cd tsbs-alibaba-cpu
uv sync --locked
uv run --locked tsbs-alibaba-cpu --help
```

### 2. Download the Alibaba machine usage table

Alibaba asks users to fill in a [short survey](http://alibabadeveloper.mikecrm.com/BdJtacN) first. The direct link below comes from the official [`fetchData.sh`](https://github.com/alibaba/clusterdata/blob/master/cluster-trace-v2018/fetchData.sh).

```bash
mkdir -p inputs/raw
curl -L -o inputs/raw/machine_usage.tar.gz \
  https://aliopentrace.oss-cn-beijing.aliyuncs.com/v2018Traces/machine_usage.tar.gz
# official sha256: 3e6ee87fd204bb85b9e234c5c75a5096580fdabc8f085b224033080090753a7a
tar -xzf inputs/raw/machine_usage.tar.gz -C inputs/raw
```

The archive is 1.7 GB. It extracts to `machine_usage.csv`: 9.0 GB, 246,934,820 rows, 4,023 machines over 8 days.

### 3. Generate the TSBS references

Build `tsbs_generate_data` from [TSBS](https://github.com/timescale/tsbs), then run:

```bash
uv run --locked tsbs-alibaba-cpu generate-references \
  --binary /path/to/tsbs_generate_data \
  --output inputs/tsbs_reference --scales 1,10,100,1000 --seed 123
```

This runs the following command for each scale factor, and then checks every generated row's schema, order and timestamp:

```text
tsbs_generate_data --use-case=cpu-only --scale=N --initial-scale=N --seed=123 \
  --log-interval=10s --timestamp-start=2016-01-01T00:00:00Z \
  --timestamp-end=2016-01-02T00:00:00Z --format=questdb
```

It also sets `GODEBUG=randseednop=0`. Without that setting, Go 1.24+ ignores TSBS's seed.

> The TSBS revision used for the reference checksums was not recorded. If your `inputs/tsbs_reference/*/cpu.ilp` hashes differ from [`docs/checksums.sha256`](docs/checksums.sha256), your matched control will differ from the published one. The pipeline and validation still work.

### 4. Build

The machine mapping, [`inputs/machine_mapping.csv`](inputs/machine_mapping.csv), is part of the repository. It lists 1,000 machines and was selected as follows: take the first-day machines with a valid CPU value, sort them by numeric ID, and then apply `random.Random(123).sample(..., 1000)`.

```bash
uv run --locked tsbs-alibaba-cpu build \
  --source inputs/raw/machine_usage.csv \
  --references inputs/tsbs_reference \
  --mapping inputs/machine_mapping.csv \
  --output data/alibaba_day1 \
  --scales 1,10,100,1000 --day 0 --query-samples 20
```

Options:

- `--day` is a zero-based trace day. For example, `--day 7` selects the eighth day. The selected day is always shifted onto 2016-01-01.
- `--scales` must be ascending and covered by both the mapping and the references.

The build writes to a temporary directory and publishes only after every stage succeeds. It refuses to overwrite an existing output. If it fails, it writes a `*.failure-*.json` diagnostic next to the target.

### 5. Verify

```bash
shasum -a 256 -c docs/checksums.sha256       # optional: compare with the reference build

uv run --locked tsbs-alibaba-cpu validate \
  --dataset data/alibaba_day1 \
  --source inputs/raw/machine_usage.csv \
  --references inputs/tsbs_reference
```

### 6. Optional: database readback check

```bash
uv run --locked tsbs-alibaba-cpu oracle-check \
  --dataset data/alibaba_day1 --view compatible --questdb-url http://127.0.0.1:9000
```

This loads SF1 of one view into a uniquely named, temporary QuestDB WAL table. It reads all 21 columns back, compares them, and drops the table. It checks transport and types only and does not replace `validate`. It passed on QuestDB 9.3.4.

## Correctness contract

`validate` does not compare the two views with each other. It re-reads the **original** raw CSV and the **original** TSBS references, and checks these rules:

1. Every `compatible.usage_user` equals the raw Alibaba CPU value for that machine and timestamp.
2. Every `matched_tsbs` row is byte-identical to the TSBS reference row for the same key. Every `compatible` row differs from that reference only in `usage_user`.
3. Every valid source observation appears in the output exactly once, and nothing else does. Source rows with missing CPU are excluded from both views and counted. Timestamps that are not on the 10-second grid, duplicate keys, and fractional seconds are rejected rather than rounded.
4. Each query catalog matches the versioned generator exactly, and the `shape_by_view` values are recomputed for both views.
5. The dataset bundle contains only regular files with relative paths, so it still validates after being moved.

Each run writes a result record to `validation_runs/`, and failed runs are recorded too. The test suite checks these rules on small synthetic inputs, including tampering, partial builds, relocation and cleanup failures.

## Output layout

```text
data/alibaba_day1/
  manifest.json              format version, scales, row accounting, query summary
  source_profile.json        raw-trace row counts per day
  machine_mapping.csv        host_i -> Alibaba machine_id
  compatible/sfN/cpu.ilp
  matched_tsbs/sfN/cpu.ilp
  queries/sfN.jsonl
  validation_runs/*.json     one record per validate run
  oracle_runs/*.json         one record per oracle-check run
```

## Limitations

- **One day, one real metric.** Only `usage_user` is real, and only for trace day 0. The other six Alibaba metrics are not used. In the selected machines on day 0, `mem_gps` and `mkpi` are entirely empty.
- **Day 0 is not representative of all 8 days.** Mean CPU ranges from 33.0% on day 1 to 42.3% on day 8. Day 8 also has a much higher share of samples above 90%: 1.56%, against 0.01–0.04% on the other days.
- **Gaps are real but unexplained.** The trace does not say why a machine stopped reporting, so the gaps should not be read as failures.
- **No performance results.** This project produces data and expected cardinalities. It makes no claims about any database's speed.

The research notes behind these design choices are kept in [`docs/history/`](docs/history/). They describe earlier versions and are not part of the current interface.

## Development

```bash
uv run --locked pytest -q
uv build
```

## License and data terms

- **Code:** [MIT](LICENSE).
- **Alibaba cluster trace v2018:** provided by Alibaba for research and study purposes; see the [clusterdata README](https://github.com/alibaba/clusterdata). The CPU values in `compatible` are derived from it. This project does not relicense the trace. Check Alibaba's terms before redistributing generated data.
- **TSBS:** the reference data comes from [TSBS](https://github.com/timescale/tsbs) (MIT).

If you use this dataset, please also cite the Alibaba trace as requested in its repository.
