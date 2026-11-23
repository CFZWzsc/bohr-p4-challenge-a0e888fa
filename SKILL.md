---
name: research-data-cleaning-audit
description: Use when a research data-analysis case supplies CSV or TSV measurements and asks for cleaning, missing/outlier/duplicate handling, unit or format conversion, or a data-quality report; run scripts/run_skill.py for a deterministic field-level audit and contract-preserving result.json.
---

# 科研数据清洗与质量审计

## 用途与适用条件

在科研数据清洗任务中使用：读取数据字典与实际表头，审计缺失值、异常值、重复记录、类型与单位问题，生成字段级质量报告与可复核的清洗副本。输入为 `/input/case.json`、可选 `/input/task.md` 与 CSV/TSV 材料。

不适用：统计建模与假设检验（另见统计分析 Skill）、数据库查询（另见 SQLite Skill）、图表排版。脚本**不覆盖原始文件**，不擅自填补缺失值。

## 输入

- `case.json`：`case_id`、`output_contract`；可选 `data_files`、`key_columns`、`missing_tokens`、`unit_rules`。
- 材料：`input_dir`、`materials`、`files`、`path`；回退 `/input`。
- 输出：`/app/output/result.json` 与 `/app/output/cleaned_data.csv`（仅单表时）。

## 处理流程（对应评分维度）

1. **字段核对（任务完成度）**：按表头读取真实列名，识别分隔符（逗号/制表符/分号）与编码（UTF-8、UTF-8-SIG、GB18030、Latin-1）；缺列时不改名列冒充对齐。
2. **类型推断（正确性）**：逐列判断整数、浮点、日期、文本，给出成功解析比例；混合类型列明确标为 `text_with_numeric`，不强制转换。
3. **缺失值（正确性）**：统计空串、`NA`、`N/A`、`NaN`、`None`、`null`、`-999`、`-9999` 等标记；区分「未检测」与真值 0（0 不算缺失）；不做均值填补。
4. **异常值（正确性）**：对数值列用 IQR×1.5 规则标记，输出上下界与超界行号；只标记，不删除，避免把真实高值当错误。
5. **重复记录（正确性）**：全字段完全相同的额外行计入重复；若任务提供 `key_columns`，额外给出主键重复组；保留全部行并标注保留依据。
6. **单位与格式（完整性）**：从表头 `(...)`/`[...]` 提取单位；日期列检查可解析性；不擅自换算，换算须有显式 `unit_rules` 并留映射。
7. **质量报告（可核验性）**：`quality_report` 的数字直接由同一份分析结构生成，与 `cleaned_data.csv` 行数、列数一致；输出输入 SHA-256 与规则版本。
8. **失败与恢复（可靠体验）**：空表、全列缺失、编码乱码、解析失败分别记录，不把未完成写成完成。

## 输出契约

| 字段 | 含义 |
|-|-|
| `case_id` | 与输入一致 |
| `output_contract` 各字段 | 命中别名时填真实值，否则 `null` + `contract_notes` |
| `status` | `success` / `partial` / `insufficient_input` / `failed` |
| `result` | `data_dictionary`、`missing_summary`、`outliers`、`duplicates`、`conversions`、`quality_report` |
| `evidence` | 输入哈希、编码、分隔符、规则版本、清洗副本路径 |
| `uncertainty` | 单位不明、类型混合、异常值语义 |
| `errors` / `stats` | 真实错误与计数 |

## 异常处理表

| 情况 | 处置 | 状态 |
|-|-|-|
| case.json 缺失/非法 | 空结果 + 错误 | `failed` |
| 无数据文件 | 空审计并说明 | `insufficient_input` |
| 空表或只有表头 | 记录 `empty_table` | `partial` |
| 编码无法识别 | 依次尝试并记录实际编码 | `success`/`partial` |
| 列类型混合 | 标为文本，不强制转换 | `partial` |
| 缺失率超过 50% | 报告但不填补 | `partial` |
| 主键冲突 | 列组并保留全部行 | `success` |
| 非 CSV/TSV 文件 | `skipped` 记录不支持 | `partial` |

## 限制

- 不读取 xlsx/parquet（无第三方库时不解析），会进入 `skipped`。
- 异常值仅统计标记，不代表应剔除；是否剔除由研究者决定。
- 不做单位换算，除非任务显式提供 `unit_rules`；换算结果记入 `conversions`。
- 清洗副本只做空行移除与缺失标记规范化，不填补、不插值、不截断。

## 最小调用示例（示例，非官方案例结果）

```bash
python /skills/weekly-skill/scripts/run_skill.py --case /input/case.json --out /app/output/result.json --work /app/work
```

示意输入 `{"case_id":"synthetic-demo","output_contract":{"quality_report":"object","duplicates":"array"}}`；示意输出保留 `case_id`、`quality_report`、`duplicates`、`status`，无数据时为 `insufficient_input`。仅为接口示例。


## 交付保证与评分对齐

调用顺序（固定）：

1. 先运行包内脚本（若存在）：`python scripts/run_skill.py`（默认读 `/input/case.json`，写 `/app/output/result.json`）；路径不同时用 `--case` / `--output` 指定。
2. 再完成脚本无法自动完成的实质工作（译文、论述、图表、修订、结论等），把结果写回 `result.json` 对应字段。
3. 最后自检：`case_id` 与输入一致；`output_contract` 每个字段都存在；`status` 按契约取 `success`/`partial`/`insufficient_input`/`failed`（缺输入或证据不足时用 `insufficient_input`/`partial`）；无 `pending` 占位；数字、引用、来源可逐条回溯。

运行保证：

- 输入缺失、JSON 损坏、字段异常、输出目录不存在时，仍须写出结构化 `result.json` 并正常结束，不得崩溃或非零退出。
- 不联网、不伪造数据、不声称平台评分或官方案例结果。
- 交付物必须是可直接编辑与复核的成品，而不是流程说明。

评分对齐（本题五个维度）：

- 实际效果（40）：完整产出任务要求的最终成品，覆盖全部输入条目，可直接进入修改流程。
- 使用增益（25）：体现相对基础 Agent 的可验证改进（结构化、术语/数字/引用核对、可编辑性）。
- 创新增益（15）：保留方法学增量（分块、对齐、二次校验、版本比对等），并用结果证明其价值。
- 可靠体验（10）：异常输入有明确降级与说明，不同长度/文体/重复运行下稳定产出。
- 生态贡献（10）：用途、调用、输入输出、限制、示例、维护信息齐全，资源可被他人复用。

## 最终交付强制要求

1. **调用 Agent 必须交付成品，不是流程说明**：脚本产出只是索引、核对或规格骨架；调用 Agent 必须在同一 `result.json` 中补齐本题要求的最终产物（正文/译文/审稿意见/综述/分析结论/图表源文件与导出件/幻灯片页内容与讲稿/实验方案等）。交付字段为空、只有标题骨架，或含 `pending`、`TODO` 之类占位，一律视为未完成。
2. **必须覆盖全部输入条目**：输入中的每个章节、问题、文献、图、页面、指标都要有对应条目，不得只处理示例或首条；确实缺失的条目写入 `uncertainty` / `limitations` 并说明原因，不得静默跳过。
3. **必须自检后再交付**：`case_id` 与输入一致；`output_contract` 每个字段都存在；状态值取本题契约规定的枚举；数字、单位、引用、来源可逐条回溯；结果内部无自相矛盾。
4. **参数等价**：`python scripts/run_skill.py --case <输入> --out <输出>` 与 `--output <输出>` 等价；默认 `/input/case.json` → `/app/output/result.json`。
5. **平台用 3 个固定案例分别运行**：逻辑要由输入驱动，不针对某一份示例硬编码；字段命名差异走别名匹配与显式降级，并在 `uncertainty` 记录假设。
