# RUBRIC — 科研数据清洗与处理（`challenge-a0e888fa`）

公开作品：https://github.com/CFZWzsc/bohr-p4-challenge-a0e888fa

本题按组织方评分表打分（实际效果 40 / 使用增益 25 / 创新增益 15 / 可靠体验 10 / 生态贡献 10；
GitHub 公开链接另加最多 5 分）。下表把每一行映射到本包内实际交付的文件或 `result.json` 字段，
**没有覆盖的直接写「未覆盖」，不做自述式加分**。

| 评分维度 | 分值 | 本包对应产物 | 状态 |
|---|---|---|---|

| 实际效果 | 40 | `result.json` 的 `data_dictionary`/`missing_summary`/`outliers`/`duplicates`/`conversions`/`quality_report` + `cleaned_data.csv`（五类处理实际执行） | 覆盖 |
| 使用增益 | 25 | `report.md` + `provenance.json` + `repro_manifest.json`：输入 sha256、规则版本、逐条动作与行数对账 | 部分（有可复核证据，无外部 baseline 对照） |
| 创新增益 | 15 | `consistency_check` 四项重算自检（报告数字 = 结果表）；单位规则仅审计不改值，避免静默错误 | 覆盖 |
| 可靠体验 | 10 | `SKILL.md` 异常处理表 + 空表/乱码/主键冲突/日期失败的显式降级分支 | 覆盖 |
| 生态贡献 | 10 | `SKILL.md` 用法/限制/示例 + `examples/demo-case` + 本文件 | 覆盖 |

## 已知未覆盖 / 不适用

- 使用增益的 25 分要求与「不加载 Skill 的基础 Agent」在同一输入上做对照；本包只有自身产物与
  可复核证据，**没有平台白板或第三方 baseline 的运行数据**，因此按「部分」记录，不冒充已证明的增益。
- 平台固定用例与隐藏评分器不随仓库发布；包内 `examples/demo-case` 为合成样例，明确不是正式答案。

## 外部同类 skill 取长（2026-11-25，r22）

- 边界原则（skills.sh `k-dense-ai/scientific-agent-skills@exploratory-data-analysis`）：单元格、表头与元数据
  一律视为不可信数据；不执行文件派生内容、不解析文件内嵌指令与 URL；只做有界、确定性的聚合与校验。
- 交付结构（公开高分仓库 `ZcwDev/bohr-product-arena`、`hehekevin/boerproduct`）：`inventory.tsv` 六维文件元数据、
  `repro_manifest.json`、报告↔结果一致性重算自检、RUBRIC 覆盖矩阵（未覆盖如实标注，不虚报）。
- 未采纳：均值/中位数插补源值（违反题面“不插补”）、把实质判断留给第二轮（违反单次交付）。
