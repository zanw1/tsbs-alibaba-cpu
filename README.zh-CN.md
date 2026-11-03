# Alibaba CPU Trace × TSBS：用于时序数据库实验的真实／合成配对 CPU 数据集

[English](README.md)

本项目把 [Alibaba cluster trace v2018](https://github.com/alibaba/clusterdata/blob/master/cluster-trace-v2018/trace_2018.md) 中一天的真实机器 CPU 利用率，转换成两份可直接替换 [TSBS](https://github.com/timescale/tsbs) `cpu-only` 数据的变体，并附带一套固定的 Q1–Q15 查询负载及预先算好的结果规模。

这是一个研究工件，不是阿里巴巴或 Timescale 的官方项目。

## 为什么需要它

TSBS `cpu-only` 数据完全是合成的：每台主机每 10 秒都上报一次，每个指标都是平滑、有界的随机游走。真实监控数据在两个相互独立的方面与之不同：

1. **行的形状。** 真实机器存在缺测。所选的 Alibaba 第一天里，十秒网格只有约 60% 有实际观测，单台机器的行数从 79 到 7,630 不等。
2. **数值。** 真实 CPU 利用率偏斜且有突发，偶尔超过 90%，这一点会影响阈值类查询。

如果直接把 TSBS 换成真实轨迹，这两点会同时改变，无法分辨结果变化来自哪一个。本项目构造的两个视图拥有**完全相同的行**，可以把两种影响分开：

| 比较 | 行的形状 | `usage_user` 数值 | 分离出的影响 |
|---|---|---|---|
| 完整 TSBS → `matched_tsbs` | 稠密 → 真实稀疏 | TSBS | 真实缺测／稀疏性的影响 |
| `matched_tsbs` → `compatible` | 相同 | TSBS → 真实 Alibaba | 真实 CPU 数值的影响 |

两个视图除 `usage_user` 外逐字节一致。下面是同一主机、同一时间戳在两个视图中的记录：

```text
# compatible：usage_user 为真实 Alibaba CPU
cpu,hostname=host_0,region=eu-central-1,...,service_environment=test usage_user=32i,usage_system=31i,usage_idle=21i,... 1451619710000000000
# matched_tsbs：同一键对应的原始 TSBS 记录
cpu,hostname=host_0,region=eu-central-1,...,service_environment=test usage_user=17i,usage_system=31i,usage_idle=21i,... 1451619710000000000
```

## 数据内容

一行对应一个十秒时间槽，且所选的 Alibaba 机器在该时间槽确实上报了有效 CPU 值。缺测保持为缺测，不插值也不填充。

| 规模（主机数） | 每个视图行数 | 完整 TSBS 行数 | 网格覆盖率 | 查询实例数 | `usage_user > 90` 的行数（compatible） |
|---:|---:|---:|---:|---:|---:|
| 1 | 4,800 | 8,640 | 55.6% | 281 | 0 |
| 10 | 50,366 | 86,400 | 58.3% | 281 | 2 |
| 100 | 527,685 | 864,000 | 61.1% | 281 | 98 |
| 1000 | 5,223,307 | 8,640,000 | 60.5% | 281 | 998 |

小规模使用同一份机器列表的前缀，所以机器、时间戳和 CPU 值满足 SF1 ⊂ SF10 ⊂ SF100 ⊂ SF1000。TSBS 合成列按 TSBS 自身的做法对每个规模单独生成，因此不嵌套。

SF1 和 SF10 适合做正确性检查和冒烟测试。它们的 CPU 分布与第一天全体机器差异明显：经验分布函数的最大差距，SF1 约 0.19，SF10 约 0.13；SF100 约 0.016，SF1000 约 0.011。这些距离只作描述用途，不是显著性检验。

### Schema

两个视图都原样使用 TSBS `cpu-only` 的 schema。文件格式是 InfluxDB 行协议，与 `tsbs_generate_data --format=questdb` 的输出布局相同。

- measurement：`cpu`。
- tag（10 个）：`hostname`（`host_0` … `host_{N-1}`），以及 9 个 TSBS 合成 tag：`region`、`datacenter`、`rack`、`os`、`arch`、`team`、`service`、`service_version`、`service_environment`。
- field（10 个，整数，带 `i` 后缀，取值 0–100）：`usage_user`、`usage_system`、`usage_idle`、`usage_nice`、`usage_iowait`、`usage_irq`、`usage_softirq`、`usage_steal`、`usage_guest`、`usage_guest_nice`。
- 时间戳：纳秒，位于 `[2016-01-01T00:00:00Z, 2016-01-02T00:00:00Z)` 内的十秒网格上。Alibaba 的相对秒数原样平移到这一天。

> **语义提示。** `compatible` 中的 `usage_user` 存放的是 Alibaba 的 `cpu_util_percent`，即整机 CPU *总*利用率，而不是用户态 CPU。其余九个指标仍是 TSBS 合成值，与它在物理上并不一致，例如各指标之和不等于 100。

### 查询负载（`queries/sfN.jsonl`）

共 15 类查询，每类对应 TSBS `cpu-only` 的一种查询类型。参数由固定种子 10001–10020 生成，SQL 完全相同的实例会去重。Q1 没有参数，所以去重对它影响最大，每个规模最终有 281 个实例。

| ID | 最接近的 TSBS 类型 | 本负载中的定义 |
|---|---|---|
| Q1 | `lastpoint` | 每台主机的最新一行，全部列 |
| Q2 | `single-groupby-1-1-1` | 1 个指标的每分钟最大值，1 台主机，1 小时 |
| Q3 | `single-groupby-5-1-1` | 5 个指标的每分钟最大值，1 台主机，1 小时 |
| Q4 | `single-groupby-1-1-12` | 1 个指标的每分钟最大值，1 台主机，12 小时 |
| Q5 | `single-groupby-1-8-1` | 1 个指标的每分钟最大值，8 台主机，1 小时 |
| Q6 | `single-groupby-5-1-12` | 5 个指标的每分钟最大值，1 台主机，12 小时 |
| Q7 | `single-groupby-5-8-1` | 5 个指标的每分钟最大值，8 台主机，1 小时 |
| Q8 | `cpu-max-all-8` | 全部 10 个指标的每小时最大值，8 台主机，8 小时 |
| Q9 | `double-groupby-all` | 全部 10 个指标按主机的每小时均值，全部主机，12 小时 |
| Q10 | `cpu-max-all-1` | 全部 10 个指标的每小时最大值，1 台主机，8 小时 |
| Q11 | `high-cpu-1` | `usage_user > 90` 的行，1 台主机，12 小时 |
| Q12 | `high-cpu-all` | `usage_user > 90` 的行，全部主机，12 小时 |
| Q13 | `double-groupby-1` | 1 个指标按主机的每小时均值，全部主机，12 小时 |
| Q14 | `double-groupby-5` | 5 个指标按主机的每小时均值，全部主机，12 小时 |
| Q15 | `groupby-orderby-limit` | 从当天起点到一个随机终点，`usage_user` 的每分钟最大值，取前 5 个桶 |

每行 JSONL 包含：

- `query_id` 和 `scale_factor`；
- `evaluation_seed` 和 `params_json`，后者记录主机、时间范围、阈值和 limit；
- `sql`；
- `workload_version`（`cpu_q1_q15_explicit_calendar_v1`）；
- `shape_by_view`：每个视图在查询范围内的输入行数、预期输出行数，以及该实例是否为空。

`shape_by_view` 可以用来核对数据库返回结果的行数，不需要另外运行一个参考数据库。

使用这些查询前需要注意：

- SQL 是 **QuestDB 方言**（`SAMPLE BY … ALIGN TO CALENDAR`、`LATEST ON … PARTITION BY`）。移植到其他系统时，请依据 `params_json` 改写，而不是改写 SQL 文本。
- Q15 按升序排列，返回的是前五个分钟桶；TSBS 的 `groupby-orderby-limit` 返回的是终点之前的最后五个桶。
- **空结果是有意保留的。** 真实 CPU 很少超过 90%，所以许多 Q11/Q12 实例没有结果。空结果返回得快，并不能说明过滤快。281 个实例中输出为空的数量如下：

| SF | `compatible` | `matched_tsbs` |
|---:|---:|---:|
| 1 | 44 | 8 |
| 10 | 37 | 18 |
| 100 | 19 | 4 |
| 1000 | 27 | 16 |

## 获取数据

有两条路径。多数使用者只需下载 GitHub Release；只有在需要对照原始 CSV 做源验证，或要改日期／机器集合时，才从原始输入重建。

### 1. 安装

```bash
git clone https://github.com/zanw1/tsbs-alibaba-cpu.git && cd tsbs-alibaba-cpu
uv sync --locked
uv run --locked tsbs-alibaba-cpu --help
```

### 2. 下载已发布的数据集（推荐）

GitHub Releases 提供 SF1–SF1000 的 `compatible` 与 `matched_tsbs` 视图，以及查询目录和机器映射。**不包含** Alibaba 原始的 `machine_usage.csv`，也不包含构造时用的 TSBS 参考 ILP。

```bash
mkdir -p data
curl -L -o data/alibaba_day1-v0.2.0.tar.gz \
  https://github.com/zanw1/tsbs-alibaba-cpu/releases/download/v0.2.0/alibaba_day1-v0.2.0.tar.gz
# 可选：curl -L -o data/alibaba_day1-v0.2.0.tar.gz.sha256 \
#   https://github.com/zanw1/tsbs-alibaba-cpu/releases/download/v0.2.0/alibaba_day1-v0.2.0.tar.gz.sha256
#   (cd data && shasum -a 256 -c alibaba_day1-v0.2.0.tar.gz.sha256)
tar -xzf data/alibaba_day1-v0.2.0.tar.gz -C data
# 得到 data/alibaba_day1-v0.2.0/，内含 compatible/、matched_tsbs/、queries/ 等
```

解压后即可导入 ILP，并使用 `queries/sfN.jsonl`。若要做源级 `validate`，仍需按下面的重建路径准备原始 CSV 和 TSBS 参考。

该 Release 面向研究与学习用途，与 Alibaba 对原始 trace 的说明一致；本项目不对原始 trace 重新授权。

### 3. 从原始输入重建（可选）

完整重建的环境要求：

- Python 3.11+ 和 [uv](https://docs.astral.sh/uv/)。
- 约 20 GB 可用磁盘：原始 CSV 9 GB，TSBS 参考 3.3 GB，输出 4 GB，下载压缩包 1.8 GB。
- 只有自己生成 TSBS 参考时才需要 Go。

在 Apple M4（32 GB 内存）上，构造或验证全部四个规模各约 2 分钟。

#### 3a. 下载 Alibaba `machine_usage.csv`

请从官方 [Alibaba clusterdata](https://github.com/alibaba/clusterdata) 获取 v2018 机器使用率表（说明见 [trace_2018.md](https://github.com/alibaba/clusterdata/blob/master/cluster-trace-v2018/trace_2018.md)）。Alibaba 希望使用者先填写一份[简短问卷](http://alibabadeveloper.mikecrm.com/BdJtacN)。下面的直链来自官方 [`fetchData.sh`](https://github.com/alibaba/clusterdata/blob/master/cluster-trace-v2018/fetchData.sh)：

```bash
mkdir -p inputs/raw
curl -L -o inputs/raw/machine_usage.tar.gz \
  https://aliopentrace.oss-cn-beijing.aliyuncs.com/v2018Traces/machine_usage.tar.gz
# 官方 sha256：3e6ee87fd204bb85b9e234c5c75a5096580fdabc8f085b224033080090753a7a
tar -xzf inputs/raw/machine_usage.tar.gz -C inputs/raw
```

压缩包 1.7 GB，解压后得到 `machine_usage.csv`：9.0 GB，246,934,820 行，覆盖 4,023 台机器、8 天。本项目不会再分发该文件。

#### 3b. 生成 TSBS 参考数据

从 [TSBS](https://github.com/timescale/tsbs) 编译 `tsbs_generate_data`，然后运行：

```bash
uv run --locked tsbs-alibaba-cpu generate-references \
  --binary /path/to/tsbs_generate_data \
  --output inputs/tsbs_reference --scales 1,10,100,1000 --seed 123
```

该命令对每个规模执行下面的生成命令，然后逐行检查 schema、顺序和时间戳：

```text
tsbs_generate_data --use-case=cpu-only --scale=N --initial-scale=N --seed=123 \
  --log-interval=10s --timestamp-start=2016-01-01T00:00:00Z \
  --timestamp-end=2016-01-02T00:00:00Z --format=questdb
```

它还会设置 `GODEBUG=randseednop=0`。不设置的话，Go 1.24 及以上版本会忽略 TSBS 的种子。

> 生成参考校验和时所用的 TSBS 版本没有记录下来。如果你的 `inputs/tsbs_reference/*/cpu.ilp` 哈希与 [`docs/checksums.sha256`](docs/checksums.sha256) 不同，你得到的配对对照就与已发布版本不同，但构造和验证流程仍然可用。

#### 3c. 构造

机器映射文件 [`inputs/machine_mapping.csv`](inputs/machine_mapping.csv) 已包含在仓库中，共 1,000 台机器。选取方法是：取第一天有有效 CPU 值的机器，按数字 ID 排序，再执行 `random.Random(123).sample(..., 1000)`。

```bash
uv run --locked tsbs-alibaba-cpu build \
  --source inputs/raw/machine_usage.csv \
  --references inputs/tsbs_reference \
  --mapping inputs/machine_mapping.csv \
  --output data/alibaba_day1 \
  --scales 1,10,100,1000 --day 0 --query-samples 20
```

参数说明：

- `--day` 是从零开始的 trace 日序号，例如 `--day 7` 表示第八天。所选的那一天总是平移到 2016-01-01。
- `--scales` 必须升序排列，并且映射文件和参考数据都要覆盖到。

构造先写入临时目录，全部阶段成功后才发布，也不会覆盖已有输出。失败时会在目标目录旁写入一个 `*.failure-*.json` 诊断文件。

#### 3d. 验证

```bash
shasum -a 256 -c docs/checksums.sha256       # 可选：与参考构造结果比对

uv run --locked tsbs-alibaba-cpu validate \
  --dataset data/alibaba_day1 \
  --source inputs/raw/machine_usage.csv \
  --references inputs/tsbs_reference
```

#### 3e. 可选：数据库回读检查

```bash
uv run --locked tsbs-alibaba-cpu oracle-check \
  --dataset data/alibaba_day1 --view compatible --questdb-url http://127.0.0.1:9000
```

该命令把一个视图的 SF1 导入一张名字唯一的临时 QuestDB WAL 表，读回全部 21 列进行比较，然后删除这张表。它只检查传输和类型，不能代替 `validate`。已在 QuestDB 9.3.4 上通过。

## 超过 SF1000 的规模

当前公开发布的映射到 1,000 台主机为止。这对正确性检查和多数配对实验已经够用。更大的规模不在默认 Release 里，但可以用同一套工具自行构造。

硬上限与嵌套规则：

- Trace 第 0 天大约有 **4,016** 台机器至少有一条有效 CPU 观测，这是单日构造的实际上限；v2018 全量共 4,023 台。
- 较小规模必须是同一有序映射的**前缀**：SF1 ⊂ SF10 ⊂ SF100 ⊂ SF1000 ⊂ SF*N*。若扩展到超过 1,000 台，请保持现有 [`inputs/machine_mapping.csv`](inputs/machine_mapping.csv) 的前 1,000 行不变，这样已发布的 SF1000 校验和仍然有效。
- 不能从 SF1000 文件外推得到 SF4000。多出来的主机需要新的 mapping 行、按该规模重新生成的 TSBS 参考，以及从原始 CSV 重建。即便机器／时间／CPU 键嵌套，TSBS 辅助列仍按每个规模单独生成，因此并不嵌套。
- 不要在第 0 天硬造 SF10000：没有足够的不重复机器；重复主机或悄悄换一天都会改变实验语义。

示例：扩展到 4,000 台主机，同时保留已发布的前缀。

```bash
# 1) 构造新 mapping：前 1000 行与 inputs/machine_mapping.csv 完全一致，
#    其余行按固定规则从第 0 天机器中追加。
# 2) 为所有需要的规模生成参考，包括新增规模。
uv run --locked tsbs-alibaba-cpu generate-references \
  --binary /path/to/tsbs_generate_data \
  --output inputs/tsbs_reference_sf4000 \
  --scales 1,10,100,1000,4000 --seed 123

# 3) 输出到新目录（不会覆盖已有结果）。
uv run --locked tsbs-alibaba-cpu build \
  --source inputs/raw/machine_usage.csv \
  --references inputs/tsbs_reference_sf4000 \
  --mapping inputs/machine_mapping_sf4000.csv \
  --output data/alibaba_day1_sf4000 \
  --scales 1,10,100,1000,4000 --day 0 --query-samples 20
```

体积提示：SF4000 下完整的 TSBS `cpu-only` 一天是 `4000 × 8640` 行（稀疏过滤之前）。参考数据和输出占用大约是 SF1000 的数倍。

维护者若要从本地构造结果打包 GitHub Release，可运行：

```bash
bash scripts/package_release.sh 0.2.0 data/alibaba_day1 dist/release
```

会生成 `dist/release/alibaba_day1-v0.2.0.tar.gz` 及同名 `.sha256`。归档中不含 `machine_usage.csv`、TSBS 参考，以及本地的 `validation_runs/`、`oracle_runs/`。

## 正确性约定

`validate` 不是拿两个视图互相比较，而是重新读取**原始** CSV 和**原始** TSBS 参考数据，检查以下规则：

1. 每个 `compatible.usage_user` 都等于对应机器、对应时间戳的原始 Alibaba CPU 值。
2. 每行 `matched_tsbs` 都与同一键的 TSBS 参考行逐字节一致；每行 `compatible` 与该参考行相比只有 `usage_user` 不同。
3. 每个有效的源观测在输出中恰好出现一次，输出中也没有多余的行。CPU 缺失的源记录从两个视图中同时排除并计数。不在十秒网格上的时间戳、重复键和小数秒都会直接拒绝，不会被四舍五入。
4. 每个查询目录都与带版本号的生成器输出完全一致，两个视图的 `shape_by_view` 会重新计算并核对。
5. 数据目录只包含使用相对路径的普通文件，搬到别处后仍能通过验证。

每次运行都会在 `validation_runs/` 中写一条结果记录，失败的运行也会记录。测试套件在小规模合成输入上检查这些规则，包括篡改检测、残缺构造、目录搬迁和清理失败等情况。

## 输出目录结构

```text
data/alibaba_day1/
  manifest.json              格式版本、规模、行数统计、查询摘要
  source_profile.json        原始 trace 每天的行数
  machine_mapping.csv        host_i -> Alibaba machine_id
  compatible/sfN/cpu.ilp
  matched_tsbs/sfN/cpu.ilp
  queries/sfN.jsonl
  validation_runs/*.json     每次 validate 一条记录
  oracle_runs/*.json         每次 oracle-check 一条记录
```

## 局限

- **只有一天、一个真实指标。** 只有 `usage_user` 是真实值，而且只来自 trace 第 0 天。Alibaba 的其他六个指标没有使用；在第 0 天所选的机器中，`mem_gps` 和 `mkpi` 完全为空。
- **第 0 天不能代表全部 8 天。** 平均 CPU 从第 1 天的 33.0% 到第 8 天的 42.3% 不等。第 8 天超过 90% 的样本占比也高得多，为 1.56%，其他各天为 0.01%–0.04%。
- **缺测是真实的，但原因不明。** trace 没有说明机器为什么停止上报，因此不应把缺测解读为故障。
- **不包含性能结论。** 本项目只产出数据和预期的结果规模，不对任何数据库的速度下结论。

这些设计选择背后的研究笔记保存在 [`docs/history/`](docs/history/) 中。它们描述的是早期版本，不属于当前接口。

## 开发

```bash
uv run --locked pytest -q
uv build
```

## 许可与数据条款

- **代码：** [MIT](LICENSE)。
- **Alibaba cluster trace v2018：** 由阿里巴巴提供，用于研究和学习目的，见 [clusterdata README](https://github.com/alibaba/clusterdata)。`compatible` 中的 CPU 值由它派生。本项目不对该 trace 重新授权；再分发生成的数据之前，请先核对阿里巴巴的条款。
- **TSBS：** 参考数据来自 [TSBS](https://github.com/timescale/tsbs)（MIT）。

如果使用本数据集，请同时按 Alibaba trace 仓库的要求引用该 trace。
