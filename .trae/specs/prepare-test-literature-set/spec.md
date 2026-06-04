# 准备小型测试文献集 Spec

## Why
在第一版中,先准备 20-50 篇 PubMed 摘要(每个神经递质 GPCR 受体 3-5 篇)用于验证文献获取管道与后续的 LLM 字段抽取。课程材料提示大规模下载会受版权和爬虫限制,小型测试集可在受控范围内验证整条数据获取与解析管道,避免一开始就消耗大量 Entrez 配额。

## What Changes
- 新增 `scripts/fetch_pubmed_test_set.py`:从 `receptor_list_classic_neurotransmitter_gpcr.xlsx` 读取 24 个受体清单,**按受体逐个** esearch PubMed,带 abstract 文本扫描验证与跨受体合并。
- 新增 `data/pubmed_test_set.json`:保存所有下载到的摘要及元数据,字段含 `mentioned_receptors_in_abstract` 与 `assignment_method` 用于审计。
- 新增 `data/pubmed_test_set_summary.csv`:24 行,每受体命中数、下载数、low_confidence_count。
- 新增 `data/_check_assignments.py`:独立审计脚本,可重跑以验证 `query_receptor_gene` 与 abstract 真实内容是否一致。
- 新增 `scripts/.env.example` / `scripts/.env` / `scripts/.env.qwen.example`:占位/实际配置文件。
- 引入 `requirements.txt` 锁定 `biopython>=1.81`、`openpyxl>=3.1`、`pandas>=2.0`、`python-dotenv>=1.0`。

## Impact
- Affected specs:本阶段是"文献数据获取"的第一步,后续的 LLM 字段抽取会复用 `pubmed_test_set.json` 作为输入。
- Affected code:
  - 新建 `scripts/fetch_pubmed_test_set.py`
  - 新建 `requirements.txt`
  - 新建 `scripts/.env.example` / `scripts/.env` / `scripts/.env.qwen.example`
  - 新建 `data/pubmed_test_set.json` / `data/pubmed_test_set_summary.csv` / `data/_check_assignments.py`
  - 读取:`receptor_list_classic_neurotransmitter_gpcr.xlsx`(`included_receptors` 表)

## ADDED Requirements

### Requirement: 受体清单解析
系统 SHALL 读取 `receptor_list_classic_neurotransmitter_gpcr.xlsx` 的 `included_receptors` 表(7 个神经递质系统、24 个受体基因)。

#### Scenario: 成功解析 xlsx
- **WHEN** 脚本启动并指向正确的 xlsx 路径
- **THEN** 返回 24 个受体的结构化列表,字段非空,无重复 `receptor_gene`

### Requirement: per-receptor 搜索查询构造
系统 SHALL **对每个受体** 单独发 esearch 查询(而不是 per-system OR 合并),查询字符串由 `"<GENE>"[Title/Abstract] AND (GPCR OR "G protein-coupled receptor"[Title/Abstract])` 组成,限定 `pubmed` 数据库、近 10 年、排除 `Review`。
- 24 次 esearch,每次对应一个受体。
- `query_receptor_gene` 字段等于本次 esearch 的搜索词,**天然正确**。

#### Scenario: 构造查询
- **WHEN** 输入受体基因 `DRD1`
- **THEN** 生成查询 `"DRD1"[Title/Abstract] AND (GPCR OR "G protein-coupled receptor"[Title/Abstract]) AND "<Y-10>/01/01"[Date - Publication] : "<Y>/12/31"[Date - Publication] NOT "Review"[Publication Type]`

### Requirement: 摘要获取、限速与批量
系统 SHALL 使用 `Entrez.esearch` 获取 PMID 列表,再用 `Entrez.efetch` 以 `xml` 格式下载摘要;efetch 分批,单批 ≤ 200 个 PMID(NCBI 稳定上限);调用间插入 `time.sleep`,未提供 `ENTREZ_API_KEY` 时按 0.34s/次暂停,提供后按 0.1s/次暂停。

#### Scenario: 单次抓取预算
- **WHEN** 一次完整运行 24 受体
- **THEN** esearch 24 次 + efetch 批 1-12 次;无 API key 时总耗时 < 30s,有 API key 时 < 10s

### Requirement: API 配置加载
系统 SHALL 在启动时从环境变量(优先)或 `scripts/.env` 加载 `ENTREZ_EMAIL` 与 `ENTREZ_API_KEY`。
- `ENTREZ_EMAIL` 缺失时,脚本 SHALL 抛出明确错误信息并退出。
- `ENTREZ_API_KEY` 缺失时,脚本 SHALL 输出提示信息(未提供 API key,速率限制为 3 req/s)但继续运行。

### Requirement: 文本扫描与验证
系统 SHALL 对每条记录的 `title+abstract` 做以下处理:
1. 剥离 HTML 标签(`<sub>` 等 PubMed XML 残存),避免打断匹配
2. 扫描 24 个基因符号(`\b<gene>\b`,大小写不敏感)
3. 扫描 24 个受体全名(子串匹配)
4. 扫描派生的常见短别名(如 `HRH2 → "H2 receptor"`,`CHRM1 → "M1 receptor"`,`HTR2A → "5-HT2A"`)
5. 设置 `mentioned_receptors_in_abstract` = 上述扫描到的基因列表(保序去重)
6. 设置 `mentioned_receptor_names` = 上述扫描到的全名/短别名(保序去重)
7. 设置 `low_confidence_query` = (查询词既不在 `mentioned_receptors_in_abstract` 也不在 `mentioned_receptor_names`)

#### Scenario: 正常命中
- **WHEN** 论文 abstract 包含 "Histamine receptor 2 (HRH2)"(`HR<sub>H2</sub>` 形式)
- **THEN** `mentioned_receptors_in_abstract` 含 `["HRH2"]`,`low_confidence_query=False`

#### Scenario: HTML 干扰
- **WHEN** PubMed 残留 `HR<sub>H2</sub>` 形式
- **THEN** HTML 剥离后 `\bHRH2\b` 仍能匹配

#### Scenario: 短别名匹配
- **WHEN** abstract 包含 "the M1 receptor is activated"
- **THEN** `mentioned_receptor_names` 含 `["M1 receptor"]`

### Requirement: 跨受体 PMID 合并
当一个 PMID 同时被多个受体的 esearch 命中时(同篇论文讨论多受体),系统 SHALL:
- 把该 PMID 仅入库一次,`query_receptor_gene` 保留**首次**抓取时的搜索词
- 后续命中时,合并 `mentioned_receptors_in_abstract` 与 `mentioned_receptor_names`,不去重
- 若后续命中让 `mentioned_receptors_in_abstract` 包含了原 `query_receptor_gene`,将 `low_confidence_query` 翻为 `false`

#### Scenario: 双受体论文
- **WHEN** PMID-A 首次被 HRH4 检索命中并入库;之后 HRH1 的 esearch 也返回 PMID-A
- **THEN** JSON 中 PMID-A 仅 1 条记录,`query_receptor_gene=HRH4`,`mentioned_receptors_in_abstract=["HRH1","HRH4"]` 或 `["HRH4","HRH1"]`

### Requirement: 输出数据格式
每条 JSON 记录 SHALL 包含:
- `pmid`、`title`、`abstract`、`authors`、`journal`、`year`
- `query_receptor_gene`(string)、`query_receptor_name`(string)、`neurotransmitter_system`(string)
- `mentioned_receptors_in_abstract`(string 数组)
- `mentioned_receptor_names`(string 数组)
- `low_confidence_query`(bool)
- `assignment_method`(固定 `"per_receptor_search"`)
- `fetched_at`(ISO8601 时间戳)

`data/pubmed_test_set_summary.csv` 24 行(每受体 1 行),列:`neurotransmitter_system`、`receptor_gene`、`receptor_name`、`hits`、`new_pmids`、`downloaded`、`low_confidence_count`、`status`。

#### Scenario: 输出可读
- **WHEN** 脚本运行完成
- **THEN** JSON 含 ≥ 20 条记录;每条 `pmid` 唯一;CSV 24 行

### Requirement: 可重复运行
系统 SHALL 支持重复运行而不产生重复数据:已存在于 JSON 中的 PMID 在下次运行时跳过(按 PMID 去重)。

#### Scenario: 重复运行
- **WHEN** 第二次运行脚本
- **THEN** 日志出现 "Skipped N already-fetched PMIDs for <gene>",本次新增数累加为 0,JSON 总数不变

### Requirement: 审计脚本
`data/_check_assignments.py` SHALL 独立验证 `query_receptor_gene` 与 abstract 真实内容的匹配情况,统计错配率;运行后输出:
- 错配数 / 总数
- 错配记录列表(每条含 PMID、assigned、true_genes、title)
- 按系统错配分布

#### Scenario: 错配率 < 5%
- **WHEN** 跑完生产脚本后跑审计脚本
- **THEN** 错配率 ≤ 5%(剩余 5% 大概率是审计脚本自身的字符串匹配盲点,不是数据错)

## 下一步(LLM 字段抽取,本阶段不实现)
测试集准备好之后,下一步会对每篇摘要用 LLM 抽取以下 14 个字段,作为知识库的核心数据。字段含义与允许值见下表。

| # | 字段 | 含义 | 取值/示例 |
|---|---|---|---|
| 1 | `pmid` | PubMed ID | "39094559" |
| 2 | `source` | 综述 vs 原始研究 | "review" / "original_research" |
| 3 | `receptor` | 受体全名或基因符号 | "dopamine D1 receptor" / "DRD2" |
| 4 | `receptor_gene` | 基因符号 | "DRD2" |
| 5 | `receptor_family` | GPCR 大类、神经递质系统、受体家族/亚型 | "Class A dopamine D2-like receptor" |
| 6 | `ligand` | 配体 / 神经递质 | "dopamine"、"serotonin"、"norepinephrine/epinephrine"、"acetylcholine"、"glutamate"、"GABA"、"histamine" |
| 7 | `location` | 器官、组织或脑区分布 | "brain"、"striatum"、"hippocampus"、"heart" |
| 8 | `cell_type` | 细胞类型 | "neuron"、"astrocyte"、"medium spiny neuron"、"interneuron"、"microglia" |
| 9 | `downstream_pathway` | 下游分子/通路 | "Gαs"、"Gαi/o"、"Gαq"、"cAMP"、"PKA"、"PLC"、"IP3/Ca2+"、"β-arrestin" |
| 10 | `function` | 生物学功能 / 疾病相关功能 | "neurotransmission"、"motor control"、"reward"、"learning and memory"、"inflammation" |
| 11 | `species` | 研究对象 | "human"、"mouse"、"rat" 或具体细胞系 |
| 12 | `literature` | 来源信息集合 | `{pmid, doi, title, year, journal}` |
| 13 | `evidence` | 原文证据句 | 从 abstract 中**直接引用**支持该记录的句子(短句) |
| 14 | `confidence` | 抽取结果对原文的贴合度 | 三档枚举,见下 |

`confidence` 三档判定标准:

- `high`:原文证据句直接、明确地支持主要字段;受体名称清楚;物种、组织/脑区、细胞类型、通路或功能至少有一部分明确出现;不需要明显推断
- `medium`:核心关系基本明确,但部分字段缺失、需要结合上下文,或者证据来自相邻句而非同一句;仍然不应该靠常识补全
- `low`:文本支持较弱、较泛泛、综述性较强、实验细节不清,或者受体别名/上下文存在一定歧义;需要人工重点复核

## LLM 选型(下一阶段)
- 提供方:阿里云千问(Qwen),OpenAI 兼容接口 `https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions`
- 模型:`qwen-plus`(中文友好、长上下文、成本适中)
- API key 已配置在 `scripts/.env` 的 `QWEN_API_KEY`(`.gitignore` 已排除)
- 输出:严格 JSON,只输出 14 个字段;遇到字段无法在原文中确认,填 `null` 并降一档 confidence
- `confidence=low` 的记录:用更详细的 prompt 让 LLM 自查一次,看是否能补出证据句;若仍为 low,标 `needs_human_review: true` 等人工复核

## Non-Goals(本阶段不做)
- 不做 LLM 字段抽取,留给下一阶段。
- 不下载全文,只取摘要(abstract)。
- 不接入正式数据库,先用 JSON 文件存储。
- 不做大规模并发抓取,只做顺序 + 限速。
