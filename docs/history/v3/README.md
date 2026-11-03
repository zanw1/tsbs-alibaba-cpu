# Alibaba TSDB Dataset

独立的 Alibaba 真实轨迹与 TSBS 配对数据项目。只依赖 Python 3.11+ 和 NumPy；安装、构造和源数据验证不导入原 TSDB 项目，也不调用 LLM。

## 快速使用

在本项目目录执行：

```bash
uv sync --locked
uv run --locked alibaba-dataset --help

uv run --locked alibaba-dataset inspect --dataset data/alibaba_day1

uv run --locked alibaba-dataset validate \
  --dataset data/alibaba_day1 \
  --source inputs/raw/machine_usage.csv \
  --references inputs/tsbs_reference
```

验证会重新扫描原始 CSV 并逐值核对，耗时明显高于查看 manifest。每次验证都单独保存结果到 `validation_runs/`，失败结果不会覆盖成功记录。

## 当前已经构建的数据

窗口为 Alibaba 相对时间 `[0, 86400)`，映射到 UTC `2016-01-01`。沿用原来固定种子 123 得到的机器映射，较小 SF 使用前缀机器集合。

| SF／选中机器数 | 原生行数 | 兼容／配对行数 | 去重后查询数 |
|---:|---:|---:|---:|
| 1 | 4,800 | 4,800 | 281 |
| 10 | 50,366 | 50,366 | 281 |
| 100 | 527,685 | 527,685 | 281 |
| 1000 | 5,223,307 | 5,223,307 | 281 |

三种视图均为实际文件，没有外部符号链接：

- `data/alibaba_day1/native/sfN/machine_usage.ilp`：真实机器 ID、七指标 schema、原始缺失和异常值。CPU 缺失的记录也保留在原生版中。
- `data/alibaba_day1/compatible/sfN/cpu.ilp`：`hostname=host_i`，`usage_user` 是真实 CPU，其他列来自该 SF 的 TSBS 参考。
- `data/alibaba_day1/matched_tsbs/sfN/cpu.ilp`：与兼容版完全相同的机器／时间键，所有数值取自 TSBS，用于配对对照。

当前样本没有缺失 CPU 的选中记录，因此三种视图行数恰好相等；这不是对未来窗口的约束。原生版七列中 `mem_gps`、`mkpi` 在本次第一天样本全为 NULL，实际有五个活跃指标。数据没有填补真实时间缺口。

## 独立重建

原始文件、机器映射和参考数据均是显式输入，不需要 v1/v2 目录。已有输出拒绝覆盖，请使用新目录：

```bash
uv run --locked alibaba-dataset build \
  --source inputs/raw/machine_usage.csv \
  --references inputs/tsbs_reference \
  --mapping inputs/machine_mapping.csv \
  --output data/alibaba_day1_rebuilt \
  --scales 1,10,100,1000 --day 0 --query-samples 20
```

`--day` 为零基日序号，例如 `--day 7` 选择第八天。各窗口均平移到固定实验日，保留窗内相对时间；窗口选择必须在比较前声明。`--scales` 可以取映射覆盖的其他有序正整数，但须提供对应 `sfN/cpu.ilp` TSBS 参考文件。当前构造合同为一天、十秒网格，不会把非网格时间戳四舍五入。

构造先写临时目录，完成后才发布目标目录。失败时临时输出会删除，独立失败说明保存在目标父目录，不会留下看似完成的数据集。

## 输入与下载

本机 `inputs/raw/machine_usage.csv` 和 `inputs/tsbs_reference/` 已经放入独立实际文件。原始 CSV 约 9 GB，来自 [Alibaba 官方 v2018 说明](https://github.com/alibaba/clusterdata/blob/master/cluster-trace-v2018/trace_2018.md)，[原始压缩包下载](https://aliopentrace.oss-cn-beijing.aliyuncs.com/v2018Traces/machine_usage.tar.gz)。参考数据生成条件保存在 `inputs/reference_provenance.json`，其中原路径只是历史来源记录。

大数据文件不进入 Git 或 Python wheel。只复制／克隆代码时，需要另行准备原始数据和 TSBS 参考；复制完整项目目录时，它们已经包含在本机项目中。原始数据的研究使用和再分发条件以各发布者为准，项目没有给第三方数据重新授权。

如需生成新的 TSBS 参考，先获取支持本项目整数 CPU-only 格式的 TSBS 可执行程序，再运行：

```bash
uv run --locked alibaba-dataset generate-references \
  --binary /path/to/tsbs_generate_data \
  --output inputs/tsbs_reference_new --scales 1,10,100,1000 --seed 123
```

命令显式启用固定种子兼容设置，检查生成行数、顺序、字段和时间。TSBS 是可选的外部数据生成工具；构造已有参考数据时不需要 Go 或 TSBS SDK。

## 验证边界

`validate` 从原始 CSV 重建临时索引，对原生版七个指标及 NULL 位置逐值比较；配对两侧分别对照原始 TSBS，而不只是相互比较；再检查记录完整性、查询目录和两侧查询结果行数。

可选的 QuestDB 检查仅验证 SF1 的数据库导入、类型和 NULL 回读；它不代替上述源数据验证：

```bash
uv run --locked alibaba-dataset oracle-check \
  --dataset data/alibaba_day1 --questdb-url http://127.0.0.1:9000
```

此命令预建唯一名称的临时 WAL 表，完成后删除；失败及清理失败均记录。它不会覆盖既有实验表。原生表定义见 [schema](docs/native_schema.sql)。

## 查询版本与研究边界

当前查询版本为 `cpu_q1_q15_explicit_calendar_v1`：保留历史 Q1–Q15 的参数语义，显式说明日历分桶和排序；Q8/Q10 仍是八小时，Q9/Q13/Q14 仍是十二小时，Q15 仍取前五个非空桶。不要按上游文档的简短描述擅自替换这些语义。

1,200 组参数组合已做迁移对照，证据见 [迁移检查](docs/query_migration_check.json)。新目录移除了 Q1 的无参数重复项，每个 SF 有 281 个不同实例。空查询仍保留，查询样本数量不等于有效复杂查询覆盖；更不能当作性能优势。

现有 C++ 主项目对稀疏数据的准备门槛和部分查询假设仍需另外评估。本项目不包含数据库性能结论、不修复主项目，也不宣称已有 C++ 引擎支持七指标原生 schema。

## 项目结构与开发

```text
src/alibaba_dataset/    构造、格式合同、查询、源验证、可选数据库检查、CLI
tests/                 独立 pytest 契约测试，临时文件自动清理
inputs/                原始数据、机器映射、TSBS 参考与来源记录
data/                  新格式数据及逐次验证记录
docs/                  深度审查、研究报告与历史证据
pyproject.toml          最小依赖与命令入口
uv.lock                可复现依赖锁
```

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --locked python -B -m pytest -q
uv build --no-sources
```

详见 [深度审查与改动记录](docs/deep_review.md)。[既有研究报告](docs/research_report.md) 是 v2 历史研究，相关统计原件保存在 `docs/history/v2/`；当前接口和数据政策以本 README 和新 manifest 为准。
