# 准备小型测试文献集 Spec

## Why
在第一版中,先准备 20-50 篇 PubMed 摘要(每个神经递质系统 3-5 篇)用于验证文献获取管道与后续的 LLM 字段抽取。课程材料提示大规模下载会受版权和爬虫限制,小型测试集可在受控范围内验证整条数据获取与解析管道,避免一开始就消耗大量 Entrez 配额。

## What Changes
- 新增 `scripts/fetch_pubmed_test_set.py`:从 `receptor_list_classic_neurotransmitter_gpcr.xlsx` 读取受体清单,按**神经递质系统**分组(7 个组),对每个组用 Biopython `Entrez` 检索 PubMed,拉取该系统前 N 篇摘要。
- 新增 `data/pubmed_test_set.json`:保存所有下载到的摘要及元数据。
- 新增 `data/pubmed_test_set_summary.csv`:每个系统(7 行)的命中数与下载数汇总,便于人工核查。
- 新增 `scripts/.env.example` / `scripts/.env`:占位/实际配置文件,提供 `ENTREZ_EMAIL`(必填)与 `ENTREZ_API_KEY`(可选,提供后速率从 3 req/s 升到 10 req/s)。
- 引入 `requirements.txt` 锁定 `biopython>=1.81`、`openpyxl>=3.1`、`pandas>=2.0`、`python-dotenv>=1.0`。

## Impact
- Affected specs:本阶段是"文献数据获取"的第一步,后续的 LLM 字段抽取会复用 `pubmed_test_set.json` 作为输入。
- Affected code:
  - 新建 `scripts/fetch_pubmed_test_set.py`
  - 新建 `requirements.txt`
  - 新建 `scripts/.env.example` / `scripts/.env`
  - 新建 `data/pubmed_test_set.json` / `data/pubmed_test_set_summary.csv`
  - 读取:`receptor_list_classic_neurotransmitter_gpcr.xlsx`(`included_receptors` 表)

## ADDED Requirements

### Requirement: 受体清单解析
系统 SHALL 读取 `receptor_list_classic_neurotransmitter_gpcr.xlsx` 的 `included_receptors` 表(7 个神经递质系统、24 个受体基因),跳过 `excluded_receptors` 中的非 GPCR 受体。
- 字段:`neurotransmitter_system`、`ligand`、`receptor_gene`、`receptor_name`、`common_aliases`、`receptor_family`、`source_url`。

#### Scenario: 成功解析 xlsx
- **WHEN** 脚本启动并指向正确的 xlsx 路径
- **THEN** 返回 24 个受体的结构化列表,按 `neurotransmitter_system` 分组后形成 7 组

### Requirement: PubMed 搜索查询构造(per system)
系统 SHALL 把同一系统的多个受体基因用 `OR` 合并,生成一条 `esearch` 查询,查询字符串由 `("GENE1"[Title/Abstract] OR "GENE2"[Title/Abstract] ...)` 加上 `(GPCR OR "G protein-coupled receptor"[Title/Abstract])` 组成,限定 `pubmed` 数据库、近 10 年、排除 `Review` 综述。

#### Scenario: 构造系统级查询
- **WHEN** 输入系统 `dopamine`(包含 DRD1、DRD2)
- **THEN** 生成查询 `("DRD1"[Title/Abstract] OR "DRD2"[Title/Abstract]) AND (GPCR OR "G protein-coupled receptor"[Title/Abstract]) AND "<Y-10>/01/01"[Date - Publication] : "<Y>/12/31"[Date - Publication] NOT "Review"[Publication Type]`

### Requirement: 摘要获取与限速
系统 SHALL 使用 `Entrez.esearch` 获取 PMID 列表,再用 `Entrez.efetch` 以 `xml` 格式下载摘要;调用间插入 `time.sleep`,未提供 `ENTREZ_API_KEY` 时按 0.34s/次暂停,提供后按 0.1s/次暂停。

#### Scenario: 限速提示
- **WHEN** 未设置 `ENTREZ_API_KEY`
- **THEN** 启动日志显示"未提供 ENTREZ_API_KEY,请求速率 3 req/s",并按 0.34s 间隔调用

#### Scenario: API key 提速
- **WHEN** `ENTREZ_API_KEY` 已设置
- **THEN** 启动日志显示"已加载 ENTREZ_API_KEY,请求速率 10 req/s",并按 0.1s 间隔调用

### Requirement: API 配置加载
系统 SHALL 在启动时从环境变量(优先)或 `scripts/.env` 加载 `ENTREZ_EMAIL` 与 `ENTREZ_API_KEY`。
- `ENTREZ_EMAIL` 缺失时,脚本 SHALL 抛出明确错误信息并退出。
- `ENTREZ_API_KEY` 缺失时,脚本 SHALL 输出提示信息(未提供 API key,速率限制为 3 req/s)但继续运行。

#### Scenario: 缺邮箱
- **WHEN** `ENTREZ_EMAIL` 未设置
- **THEN** 脚本退出并打印 "Entrez 需要邮箱,请设置 ENTREZ_EMAIL 环境变量或在 scripts/.env 中提供"

### Requirement: 输出数据格式
系统 SHALL 将每篇摘要保存为 JSON 对象,顶层为数组,每个元素包含字段:
- `pmid`(string)
- `title`(string)
- `abstract`(string,缺失时为空字符串)
- `authors`(string 数组)
- `journal`(string)
- `year`(string,4 位年份)
- `query_receptor_gene`(string):该 PMID 在被轮询分配到该组内的某个具体受体
- `query_receptor_name`(string)
- `neurotransmitter_system`(string)
- `fetched_at`(ISO8601 时间戳)

并额外生成 `data/pubmed_test_set_summary.csv`,列为:`neurotransmitter_system`、`receptor_gene`(组内所有基因用 `/` 连接)、`receptor_name`(组内第一个)、`hits`、`downloaded`(本次新增数)、`status`。

#### Scenario: 输出文件可读
- **WHEN** 脚本运行完成
- **THEN** `data/pubmed_test_set.json` 包含 20-50 个元素,每个 `pmid` 唯一;`summary.csv` 7 行(7 个系统),每行 `status=ok` 时对应 JSON 中可找到该系统的记录

### Requirement: 可重复运行
系统 SHALL 支持重复运行而不产生重复数据:已存在于 JSON 中的 PMID 在下次运行时跳过(按 PMID 去重)。

#### Scenario: 重复运行
- **WHEN** 第二次运行脚本
- **THEN** 日志出现 "Skipped N already-fetched PMIDs for <system>",本次新增数累加为 0,JSON 总数不变

## 下一步(LLM 字段抽取,本阶段不实现)
测试集准备好之后,下一步会对每篇摘要用 LLM 抽取以下 13 个字段,作为知识库的核心数据。字段含义与允许值见下表。

| # | 字段 | 含义 | 取值/示例 |
|---|---|---|---|
| 1 | `pmid` | PubMed ID(从 `literature` 中取,也可独立存) | "39094559" |
| 2 | `source` | 综述 vs 原始研究 | "review" / "original_research",由 `Publication Type` 或 LLM 判读 |
| 3 | `receptor` | 受体全名或基因符号 | "dopamine receptor D2" / "DRD2" / "HTR2A" / "CHRM1" / "GRM5" |
| 4 | `receptor_gene` | 基因符号(只取 gene symbol) | "DRD2" |
| 5 | `receptor_family` | GPCR 大类、神经递质系统、受体家族/亚型 | "Class A dopamine D2-like receptor" |
| 6 | `ligand` | 配体 / 神经递质 | "dopamine"、"serotonin"、"norepinephrine/epinephrine"、"acetylcholine"、"glutamate"、"GABA"、"histamine" |
| 7 | `location` | 器官、组织或脑区分布 | "brain"、"striatum"、"hippocampus"、"heart"(可多值) |
| 8 | `cell_type` | 细胞类型 | "neuron"、"astrocyte"、"medium spiny neuron"、"interneuron"、"microglia"(可多值) |
| 9 | `downstream_pathway` | 下游分子/通路 | "Gαs"、"Gαi/o"、"Gαq"、"cAMP"、"PKA"、"PLC"、"IP3/Ca2+"、"β-arrestin"(可多值) |
| 10 | `function` | 生物学功能 / 疾病相关功能 | "neurotransmission"、"motor control"、"reward"、"learning and memory"、"inflammation"(可多值) |
| 11 | `species` | 研究对象 | "human"、"mouse"、"rat",或具体细胞系 |
| 12 | `literature` | 来源信息集合 | `{pmid, doi, title, year, journal}` |
| 13 | `evidence` | 原文证据句 | 从 abstract 中**直接引用**支持该记录的句子,用于追溯与人工复核(短句) |
| 14 | `confidence` | 抽取结果对原文的贴合度 | 三档枚举,见下 |

`confidence` 三档判定标准:

- `high`:原文证据句直接、明确地支持主要字段;受体名称清楚;物种、组织/脑区、细胞类型、通路或功能至少有一部分明确出现;不需要明显推断
- `medium`:核心关系基本明确,但部分字段缺失、需要结合上下文,或者证据来自相邻句而非同一句;仍然不应该靠常识补全
- `low`:文本支持较弱、较泛泛、综述性较强、实验细节不清,或者受体别名/上下文存在一定歧义;需要人工重点复核

## LLM 选型(下一阶段)
- 提供方:阿里云千问(Qwen),OpenAI 兼容接口 `https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions`
- 模型:`qwen-plus`(中文友好、长上下文、成本适中)
- API key 已配置在 `scripts/.env` 的 `QWEN_API_KEY`(`.gitignore` 已排除,不会随仓库泄露)
- 输出:严格 JSON,只输出 13 个字段;遇到字段无法在原文中确认,填 `null` 并降一档 confidence
- `confidence=low` 的记录:用更详细的 prompt 让 LLM 自查一次,看是否能补出证据句;若仍为 low,标 `needs_human_review: true` 等人工复核

## Non-Goals(本阶段不做)
- 不做 LLM 字段抽取,留给下一阶段。
- 不下载全文,只取摘要(abstract)。
- 不接入正式数据库,先用 JSON 文件存储。
- 不做大规模并发抓取,只做顺序 + 限速。
