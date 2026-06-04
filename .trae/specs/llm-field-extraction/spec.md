# LLM 字段抽取 Spec

## Why
测试文献集 121 条摘要已经准备好,现在要让 LLM 从每篇 abstract 中抽出 14 个结构化字段,作为知识库的核心数据。手动抽 121 条不现实(主观不一致、太慢),LLM 抽可以保证字段一致、可批量、留 evidence 句方便复核。

## What Changes
- 新增 `scripts/extract_fields_qwen.py`:读 `data/pubmed_test_set.json`,对每条记录调用 Qwen (qwen-plus) 抽 14 字段,严格 JSON 输出,落到 `data/pubmed_extracted.json`。
- 新增 `data/pubmed_extracted.json`:121 条 × 14 字段 + `needs_human_review` 标记。
- 新增 `data/extract_run.log`:每次运行的耗时、token、错误明细。
- 新增 `scripts/.env` 中的 `QWEN_API_KEY`(已在 `.gitignore`)。

## Impact
- Affected specs:在 `prepare-test-literature-set` 之上,这一步把"原始摘要"转成"知识库条目"。
- Affected code:
  - 新建 `scripts/extract_fields_qwen.py`
  - 新建 `data/pubmed_extracted.json` / `data/extract_run.log`
  - 读取:`data/pubmed_test_set.json`
- 依赖:新增 `openai>=1.0`(Qwen 走 OpenAI 兼容 SDK 比手写 requests 简单、容错好)。

## ADDED Requirements

### Requirement: 14 字段定义
系统 SHALL 对每条 PubMed 摘要抽取以下 14 个字段,值无法在 abstract 中确认时填 `null`,并把 confidence 降一档。

| # | 字段 | 类型 | 取值/示例 |
|---|---|---|---|
| 1 | `pmid` | string | "39094559" |
| 2 | `source` | enum | "review" / "original_research" |
| 3 | `receptor` | string | "dopamine D1 receptor" / "DRD1" |
| 4 | `receptor_gene` | string | "DRD1" |
| 5 | `receptor_family` | string | "Class A dopamine D1-like receptor" |
| 6 | `ligand` | enum | "dopamine" / "serotonin" / "norepinephrine/epinephrine" / "acetylcholine" / "glutamate" / "GABA" / "histamine" / null |
| 7 | `location` | string | "brain" / "striatum" / "hippocampus" / "heart" / null |
| 8 | `cell_type` | string | "neuron" / "astrocyte" / "medium spiny neuron" / null |
| 9 | `downstream_pathway` | string | "Gαs/cAMP/PKA" / "Gαi/o" / "Gαq/PLC/IP3" / "β-arrestin" / null |
| 10 | `function` | string | "neurotransmission" / "motor control" / "reward" / "learning and memory" / null |
| 11 | `species` | string | "human" / "mouse" / "rat" / cell line name / null |
| 12 | `literature` | object | `{pmid, doi, title, year, journal}` |
| 13 | `evidence` | string | 直接引用 abstract 中的短句(≤ 30 词) |
| 14 | `confidence` | enum | "high" / "medium" / "low" |

#### Scenario: 字段缺失
- **WHEN** 字段无法在 abstract 中确认(如未提物种)
- **THEN** 填 `null` 并把 confidence 降一档(原 high → medium,medium → low,low 保持)

### Requirement: 严格 JSON 输出
系统 SHALL 用 system prompt 强制 LLM 只输出一个 JSON 对象,无任何前后文字、Markdown 围栏或解释。

#### Scenario: 解析失败
- **WHEN** LLM 输出包含 ```json 围栏或前后有解释
- **THEN** 用正则提取第一个 `{...}` 块,再 `json.loads`;若仍失败,记录原文到 `extract_run.log` 并把该条标 `parse_error: true`,不重试 3 次后放弃

### Requirement: 调用限速与重试
- 调用基地址:`https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions`
- 模型:`qwen-plus`(从 `QWEN_MODEL` 读,默认 `qwen-plus`)
- `temperature=0`、`max_tokens=1500`
- 限速:每请求后 `time.sleep(0.5)`,触发 429 时指数退避 2/4/8 秒
- 失败重试:最多 3 次
- 失败兜底:该条 `confidence="low"`、`needs_human_review=true`、其他字段填 `null`

#### Scenario: 限速触发
- **WHEN** Qwen 返回 429
- **THEN** `time.sleep(2 ** retry_count)` 后重试,3 次仍失败则标 `api_error: true`

### Requirement: 断点续跑
系统 SHALL 在每条记录成功抽取后立即写盘(覆盖式保存整个 JSON),中断后再次运行自动跳过已成功的 PMID。

#### Scenario: 中断恢复
- **WHEN** 第二次运行脚本且 `pubmed_extracted.json` 已有部分记录
- **THEN** 日志输出 "Resuming: N already extracted, M to do",只对未完成的 PMID 调 LLM

### Requirement: confidence 二审
- 第一遍:全量 121 条用基础 prompt。
- 第二遍:对 `confidence="low"` 的记录(且非 parse_error / api_error)用更详细的二审 prompt 重跑,允许 LLM 补充 evidence 句。
- 仍为 `low` 的记录标 `needs_human_review: true`。

#### Scenario: 二审后升档
- **WHEN** 二审 prompt 让 LLM 找到更具体的 evidence 句
- **THEN** confidence 升一档(low → medium 或 medium → high),更新 evidence 字段

### Requirement: 审计字段
- 每条记录新增 `extraction_meta` 字段,含:`model`、`prompt_version`(string,如 "v1")、`attempt_count`、`extracted_at`(ISO8601)
- `literature` 对象中的 `doi` 若 abstract / PubMed 记录里没拿到,填 `null`

### Requirement: query 对照与 mismatch 标记
系统 SHALL 把 `pubmed_test_set.json` 中每条记录的 `query_receptor_gene` 写入 `receptor_gene_query` 字段;若 LLM 抽出的 `receptor_gene` 与之不一致:
- `receptor_gene_mismatch = true`
- `confidence` 降一档(high→medium,medium→low)
- `needs_human_review = true`(若尚未标)
- 这能识别"PubMed 命中但论文主题非该受体"的情况(例如综述/异源二聚体/数据库类论文)

#### Scenario: 异源二聚体
- **WHEN** 论文是 `DRD1-DRD2` 异源二聚体研究,query=DRD1
- **THEN** LLM 抽出 `receptor_gene="DRD1-DRD2"`,`receptor_gene_query="DRD1"`,`mismatch=true`,`needs_human_review=true`

## Non-Goals(本阶段不做)
- 不调 LLM 抽取的二次校验(留人工)
- 不做向量检索 / 知识图谱
- 不批量并发请求(顺序 + 限速,避免触发 Qwen 限流)

## LLM 选型
- 提供方:阿里云百炼(Qwen),OpenAI 兼容接口
- 模型:`qwen-plus`(中文友好、速度快、成本低)
- API key 写在 `scripts/.env` 的 `QWEN_API_KEY`,已在 `.gitignore`

## 关键 Prompt(主)

```
You are a biomedical knowledge extractor. Read the PubMed abstract and
output ONE strict JSON object (no markdown, no explanation) with these 14
fields: pmid, source, receptor, receptor_gene, receptor_family, ligand,
location, cell_type, downstream_pathway, function, species, literature,
evidence, confidence.

Rules:
- source ∈ {"review", "original_research"}.
- receptor_gene MUST match the gene symbol (e.g. "DRD1", "HTR2A").
- ligand ∈ one of: dopamine, serotonin, norepinephrine/epinephrine,
  acetylcholine, glutamate, GABA, histamine (or null if uncertain).
- location, cell_type, downstream_pathway, function, species: short
  noun phrases; null if not in the abstract.
- literature = {pmid, doi (or null), title, year, journal}.
- evidence = the SHORTEST sentence in the abstract that directly
  supports the receptor + ligand + function claim. Quote it verbatim,
  ≤ 30 words.
- confidence:
  * "high" — abstract clearly names the receptor, ligand, and at least
    one of {location, cell_type, downstream_pathway, function}; quote
    is direct.
  * "medium" — receptor/ligand clear but some fields missing or
    inferred from context.
  * "low" — receptor unclear, OR the abstract is a broad review, OR
    multiple receptors discussed without a clear focal one.
- If a field cannot be confirmed, use null AND lower confidence by one
  step.

Input:
Title: {title}
Abstract: {abstract}
PMID: {pmid}
```

## 关键 Prompt(二审)

```
You previously extracted low-confidence fields. Re-read the abstract and
try to (a) find a more specific evidence sentence, (b) tighten the
receptor name, (c) fill in location / cell_type / pathway if mentioned
even once. Output the same 14-field JSON. If you still cannot improve
specificity, keep "confidence": "low".
```
