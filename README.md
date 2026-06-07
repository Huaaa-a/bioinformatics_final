# 神经递质 GPCR 知识库 · Neurotransmitter GPCR Knowledge Base

> 北京大学生物信息学实验 · 大二下 · 期末项目
> 面向 7 大神经递质系统、24 个经典 GPCR 受体的文献结构化知识库

本项目用 LLM 把 PubMed 摘要自动转成 17 个标准化的结构化字段(受体、配体、组织、信号通路、功能、物种、证据句、置信度、推理过程等),落地成可直接被人工评审 / Web UI 检索 / 二次分析用的 JSON。
当前版本:**v5**(逐个受体抽取 + 客观 discard 规则 + OpenRouter `anthropic/claude-opus-4`)。

参考实现:课程材料 [`c6b_llm_biomedical_kb.pdf`](c6b_llm_biomedical_kb.pdf) (潘汪阅,《基于 LLM 的生物医学文献知识库建立 —— 以转录因子调控为例》)。
我们把"转录因子 ↔ 靶基因"换成"神经递质 GPCR ↔ 内源配体 / 药物 / 通路 / 功能",沿用同一套方法论。

---

## 目录

- [这个项目在做什么](#这个项目在做什么)
- [v5 关键改进](#v5-关键改进)
- [数据快照](#数据快照)
- [快速开始](#快速开始)
- [数据流](#数据流)
- [输出字段说明](#输出字段说明)
- [v5 关键设计决策](#v5-关键设计决策)
- [目录结构](#目录结构)
- [已知问题与下一步](#已知问题与下一步)
- [依赖与致谢](#依赖与致谢)
- [FAQ](#faq)

---

## 这个项目在做什么

### 背景

神经药理学 / 精神药理学领域有大量零散的研究文献,讨论某个 GPCR(多巴胺 D2、5-HT1A、组胺 H1、毒蕈碱 M3……)在某个脑区 / 细胞类型 / 通路 / 疾病中的功能。这些知识:

- **散落在百万级 PubMed 摘要里**,无法高效检索
- **描述方式不统一**,一会儿叫 DRD2,一会儿叫 D2 receptor,一会儿叫 D2R
- **专业数据库更新慢**(人工注释),跟不上文献增长

### 我们的做法

用 LLM(OpenRouter 上的 `anthropic/claude-opus-4`,通过 OpenAI 兼容接口调用)对 121 篇 PubMed 摘要做**结构化抽取**,产出 168 条 entry(平均每篇 1.4 条),每条 entry 严格对齐 17 个业务字段 + 7 个审计字段,让下游可以直接做:
- 按受体 / 通路 / 位置筛选
- 评估 LLM 抽取质量(Precision / Recall / F1)
- 找出"摘要中提到但 LLM 没抽到"的受体(漏抽分析)
- 找出"LLM 抽到但摘要里其实没有"的受体(幻觉分析)

### 覆盖范围

24 个经典神经递质 GPCR,7 大神经递质系统(受体清单见 [`receptor_list_classic_neurotransmitter_gpcr.xlsx`](receptor_list_classic_neurotransmitter_gpcr.xlsx)):

| 神经递质系统 | 受体数 | 代表受体 |
|---|---|---|
| 多巴胺 (dopamine) | 5 | DRD1, DRD2, DRD3, DRD4, DRD5 |
| 5-羟色胺 (serotonin) | 11 | HTR1A, HTR1B, HTR2A, HTR2C, HTR7, ... |
| 肾上腺素能 (adrenergic) | 9 | ADRA1A, ADRA2A, ADRB1, ADRB2, ADRB3, ... |
| 毒蕈碱型乙酰胆碱 (muscarinic ACh) | 5 | CHRM1, CHRM2, CHRM3, CHRM4, CHRM5 |
| 代谢型谷氨酸 (metabotropic glutamate) | 8 | GRM1, GRM2, GRM5, GRM7, GRM8, ... |
| GABA_B | 2 | GABBR1, GABBR2 |
| 组胺 (histamine) | 4 | HRH1, HRH2, HRH3, HRH4 |

---

## v5 关键改进

| 版本 | 抽取策略 | 核心问题 | 结果 |
|---|---|---|---|
| v2 | per-system 轮询 | 一篇论文实际只讨论 1A,query 含 11 个基因 → 72% mismatch | 121 条,几乎不可用 |
| v3 | per-receptor esearch + 整篇 LLM | LLM 看到 prompt 要求"JSON 数组"但只输出 1 个 focal receptor → 多受体论文漏抽 | 121 条(很多受体被吞) |
| v4 | per-receptor esearch + 整篇 LLM(允许数组) | ligand 字段会无脑填 canonical 配体(论文测 haloperidol 也填 dopamine) | 170 条,0% ligand mismatch |
| **v5** | per-receptor esearch + **逐个受体独立 LLM 调用** | LOW 置信度条目里混着"只提一句"和"非 focal"的受体,需要客观规则筛掉 | **168 条(118 有效 + 50 自动 discard)** |

**v5 最大的两个变化**:
1. **逐个受体独立 LLM 调用**:文本扫描找出所有提到的受体 → 每个受体单独问一次 LLM → 合并。彻底解决"多受体论文只抽 1 个"的问题。
2. **客观 discard 规则**(只对 LOW 置信度生效):三条件取其一即 discard——(a) 只在背景部分出现,(b) 全文出现 ≤ 2 次,(c) 无 location / cell_type / pathway / function 信息。把"边缘低质 entry"自动过滤,人工只需审核剩下的。

---

## 数据快照

> 数据截至 2026-06-08 (v5 schema)。具体数字以运行分析脚本为准。

### 抓取结果(121 条 PubMed 摘要,per-receptor esearch)

| 系统 | 数量 |
|---|---|
| adrenergic(肾上腺素能) | 29 |
| dopamine(多巴胺) | 19 |
| serotonin(5-羟色胺) | 18 |
| muscarinic acetylcholine(毒蕈碱型乙酰胆碱) | 18 |
| metabotropic glutamate(代谢型谷氨酸) | 18 |
| histamine(组胺) | 16 |
| GABA_B | 3 |
| **合计** | **121** |

### v5 抽取结果(168 entry,17 业务字段 + 7 审计字段)

**保留 vs 自动 discard**:

| 状态 | 数量 | 说明 |
|---|---|---|
| ✓ 保留 | **118** | 通过客观 discard 规则的 entry |
| ✗ Discard | **50** | LOW 置信度 + (只在背景 / ≤2 次 / 无功能信息) 至少满足 1 条 |
| **合计** | **168** | 121 篇摘要,平均 1.39 entry/篇 |

**保留 entry 的置信度分布**:

| confidence | 数量 | 占比 |
|---|---|---|
| high | 51 | 43.2% |
| medium | 64 | 54.2% |
| low | 3 | 2.5% |

**数据质量指标**:

| 指标 | 数值 | 说明 |
|---|---|---|
| Ligand mismatch | **0** | LLM 抽出的 ligand 与 receptor 的 canonical 内源配体完全一致 |
| Needs human review | **3** | 仅 LOW 置信度 + 未 discard 的 entry 需要人工复核 |
| 受体家族覆盖 | **7 / 7** | 7 大神经递质系统全覆盖 |
| 异源二聚体论文 | ✓ | DRD1-DRD2、HTR2A-mGluR2 等多受体论文已正确展开 |

**Discard 原因分布**(可叠加):

| 原因 | 条数 |
|---|---|
| 只出现 1 次 | 36 |
| 只出现 2 次 | 11 |
| 没有位置/细胞/通路/功能信息 | 10 |
| 只在背景部分出现 | 7 |

---

## 快速开始

### 0. 前置要求

- Python ≥ 3.9
- 一个能访问 [PubMed](https://pubmed.ncbi.nlm.nih.gov/) 的网络(Entrez)
- 一个 [OpenRouter](https://openrouter.ai/) 账号(给 LLM 调用充值或绑免费模型,申请 API key)
- 一个能收到邮件的邮箱(NCBI 强制要求,任意邮箱即可)

### 1. 克隆并安装

```powershell
git clone https://github.com/Huaaa-a/bioinformatics_final.git
cd bioinformatics_final
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. 配置 API 凭据

```powershell
copy scripts\.env.example scripts\.env
```

编辑 `scripts/.env`,填入:

| Key | 来源 | 必填 | 说明 |
|---|---|---|---|
| `ENTREZ_EMAIL` | 任意能收到邮件的邮箱 | ✓ | NCBI 强制要求 |
| `ENTREZ_API_KEY` | [NCBI Account](https://www.ncbi.nlm.nih.gov/account/settings/) 免费申请 | ✗ | 有 key 时速率 3→10 req/s |
| `OPENROUTER_API_KEY` | [OpenRouter Keys](https://openrouter.ai/keys) 申请 | ✓ | 调用 Claude / GPT / Gemini / Qwen 等 |
| `OPENROUTER_MODEL` | (可选)OpenRouter 支持的 model id | ✗ | 默认 `anthropic/claude-opus-4`,v5 脚本可走 CLI `--model` 覆盖 |

### 3. 跑一遍流程

```powershell
# ① 从 PubMed 抓 121 条摘要(增量:已有 PMID 自动跳过,约 30-40 分钟)
python scripts\fetch_pubmed_test_set.py

# ② 抽取字段 v5(逐个受体独立 LLM 调用,约 20 分钟,支持断点续跑)
python scripts\extract_per_receptor.py

# ③ (可选)生成纯文本扫描的提及表,做漏抽分析
python scripts\generate_mentioned_list.py
python scripts\compare_mentioned_vs_extracted.py
```

跑完后,产出物在 `data/`:
- `pubmed_test_set.json`:121 条原始摘要
- `pubmed_extracted.v5.json`:168 条结构化 entry(主结果)
- `mentioned_receptors.json`:基于文本扫描的提及表(不依赖 LLM,保证 Recall)

### 4. 看一眼结果

```powershell
# 统计 v5 结果
python -c "import json; from collections import Counter; d=json.load(open('data/pubmed_extracted.v5.json',encoding='utf-8')); print('Total:',len(d)); print('Confidence:',Counter(e['confidence'] for e in d)); print('Discard:',sum(1 for e in d if e.get('discard')))"
```

期望输出:
```
Total: 168
Confidence: Counter({'medium': 64, 'low': 53, 'high': 51})
Discard: 50
```

---

## 数据流

```
                          输入
                          │
                          ▼
        ┌──────────────────────────────────────────┐
        │  receptor_list_classic_neurotransmitter_ │
        │  gpcr.xlsx  (24 个受体清单)                │
        └────────────────────┬─────────────────────┘
                             │
                             ▼
┌────────────────────────────────────────────────────────┐
│  ① fetch_pubmed_test_set.py                            │
│  · per-receptor esearch(gene OR alias1 OR alias2)      │
│  · efatch 拿 abstract + DOI + journal + year + pub_types│
│  · 文本扫描:抽 mentioned_receptors(去连字符、去 <sub>、 │
│    用 xlsx 里的 common_aliases)                          │
│  · 跨受体合并:同一 PMID 多受体提及则合一处             │
│  · 输出:data/pubmed_test_set.json(121 条)              │
└────────────────────┬───────────────────────────────────┘
                     │
                     ▼
┌────────────────────────────────────────────────────────┐
│  ② extract_per_receptor.py (v5,主流程)                  │
│  · 改进的文本扫描(improved_scan_mentions):识别所有提到 │
│    的受体基因(支持别名/连字符归一化)                    │
│  · 对每个提到的受体,单独调用一次 LLM 抽取              │
│    (强制 receptor_gene = target_gene,避免 LLM 跑偏)    │
│  · 规范化:ligand 校验 / 置信度降档 / HTML 标签清洗     │
│  · 客观 discard 判定(只对 LOW 置信度):                │
│    - 只在背景部分出现 → discard                          │
│    - 全文出现 ≤ 2 次 → discard                          │
│    - 无 location/cell_type/pathway/function → discard  │
│  · 输出:data/pubmed_extracted.v5.json (168 entry)      │
└────────────────────┬───────────────────────────────────┘
                     │
                     ▼
┌────────────────────────────────────────────────────────┐
│  ③ (可选) generate_mentioned_list.py                    │
│  · 纯文本扫描,不用 LLM                                 │
│  · 保证 Recall(100% 不漏抽提到的受体)                  │
│  · 对比 LLM 抽取表,找出漏抽与幻觉                      │
└────────────────────┬───────────────────────────────────┘
                     │
                     ▼
┌────────────────────────────────────────────────────────┐
│  ④ 人工评审 (review_samples_v5.xlsx)                   │
│  · 从 168 entry 抽 HIGH/MEDIUM/LOW + NEEDS_REVIEW 各 6│
│  · 3 位评审人(wyh / dlyy / hjy)各分到 2 条/类别        │
│  · 评审结果填 "Reviewer Comment" 列                    │
│  · 后续转成 _review_samples_gold.json 做 P/R/F1 评估  │
└────────────────────────────────────────────────────────┘
```

---

## 输出字段说明

`pubmed_extracted.v5.json` 是一个 entry 数组,**每条 entry 24 个字段**(17 业务 + 7 审计)。

### 17 个业务字段

| # | 字段 | 类型 | 说明 |
|---|---|---|---|
| 1 | `pmid` | string | PubMed ID |
| 2 | `source` | enum | `review` / `original_research`(从 `pub_types` 推断) |
| 3 | `receptor` | string | 受体全名,如 "dopamine D1 receptor" |
| 4 | `receptor_gene` | string | HGNC 基因符号,大写,如 `DRD1` |
| 5 | `receptor_family` | string | 受体家族,从 xlsx 读,如 `dopamine D1-like receptor` |
| 6 | `ligand` | enum | 7 个 canonical 内源配体之一 / `null`(论文测的是 drug 时) |
| 7 | `location` | string | 组织 / 脑区,`null` 若 abstract 未提 |
| 8 | `cell_type` | string | 细胞类型,`null` 若 abstract 未提 |
| 9 | `downstream_pathway` | string | 信号通路,如 `Gαs/cAMP/PKA` |
| 10 | `function` | string | 生理功能,如 "reward" |
| 11 | `species` | string | 物种,`null` 若 abstract 未提 |
| 12 | `literature` | object | `{pmid, doi, title, year, journal}`,PubMed 源数据 |
| 13 | `evidence` | string | abstract 中支持该条目的 ≤ 80 词原句(可拼接 2-3 邻句) |
| 14 | `confidence` | enum | `high` / `medium` / `low` |
| 15 | `reasoning` | string | LLM 推断本条 entry 的关键推理(≤ 80 词) |
| 16 | `tested_compound` | string \| null | 论文实际测的药物名(drug),与内源性 ligand 分开 |
| 17 | `extraction_meta` | object | `{model, prompt_version, extracted_at}` |

### 7 个审计 / 控制字段

| # | 字段 | 类型 | 说明 |
|---|---|---|---|
| A | `ligand_mismatch` | bool | LLM 抽出的 ligand 与 receptor 的 canonical ligand 不一致时为 true |
| B | `ligand_mismatch_reason` | str | 不一致原因,如 `"HTR1A canonical=serotonin, got ligand=dopamine"` |
| C | `needs_human_review` | bool | true ⇔ 需人工复核(confidence=low 且未 discard / mismatch) |
| D | `discard` | bool | true ⇔ 客观 discard 规则判定为低质 entry,**默认不入库** |
| E | `discard_reasons` | list | discard 触发原因,可叠加:`只在背景部分出现` / `只出现 N 次` / `没有位置/细胞/通路/功能信息` |
| F | `receptor_gene_query` | string | 抓取时用的基因(对照) |
| G | `receptor_gene_mismatch` | bool | LLM 抽出与 query 不一致时为 true |

### 字段填充约定

- **`ligand` vs `tested_compound`**:
  - 论文测了 canonical 内源配体 + 其作用 → `ligand="dopamine"`,`tested_compound=null`
  - 论文只测了药物(如 haloperidol 拮抗 DRD2)→ `ligand=null`,`tested_compound="haloperidol"`
  - 论文测了药物但提了 canonical 配体作为对照 → `ligand="dopamine"`,`tested_compound="haloperidol"`
- **`evidence`**:必须是 abstract 里的**原句**,LLM 不许改写;允许拼接 2-3 邻句(单句丢 ligand 或 function 上下文时);总词数 ≤ 80
- **`reasoning`**:LLM 怎么判断 ligand vs tested_compound、为什么给这个 confidence,自由文本,≤ 80 词
- **`discard=true` 的 entry**:`needs_human_review` 强制 false(已自动判废,不需要人审)

---

## v5 关键设计决策

### 1. per-receptor esearch(检索模型)

**问题**:v2 按"神经递质系统"轮询,一个 query 含 11 个基因(如 5-HT 系统:1A/1B/1D/1E/1F/2A/2B/2C/4/5A/6/7),`query_receptor_gene` 是"系统代表",但论文可能只讨论 1A → 121 条里 87 条 mismatch = **72%**。

**解决**:对 24 个 receptor_gene 逐个 esearch,query = `(gene OR alias1 OR alias2)[Title/Abstract]`,`query_receptor_gene` 严格 = 搜索词。LLM 抽出与 query 不一致时只占 ~1%,且多是真多受体论文。

### 2. 改进的文本扫描(别名 + 连字符 + HTML)

LLM 经常漏抽"摘要里出现但 LLM 不认为是 focal"的受体。我们用**纯文本扫描**预先找出所有提到的受体基因(不依赖 LLM 判断),再对每个单独调用一次 LLM。

支持:
- 基因全名 `DRD1`、HGNC 别名 `D1A`、常用写法 `D1 receptor` / `D1R`
- 连字符归一化:`HRH-4` → `HRH4`、`DRD-2` → `DRD2`
- HTML 标签清洗:`HR<sub>H2</sub>` → `HRH2`

### 3. 逐个受体独立 LLM 调用(解决多受体漏抽)

**问题**:v3/v4 prompt 要求 LLM 输出 JSON 数组,但 LLM 倾向于只输出 1 个 focal receptor,异源二聚体论文的"次要受体"被吞。

**解决**:对文本扫描找到的每个受体,**独立调用一次 LLM**,prompt 里强制 `receptor_gene = {target_gene} EXACTLY`,让 LLM 只抽这一个受体。

**效果**:121 篇摘要 → 168 entry(平均 1.39 entry/篇)。

### 4. ligand ↔ receptor 强校验

LLM 看到"DRD1 论文"会无脑填 `ligand="dopamine"`,但论文可能只测了 haloperidol(D2 拮抗剂)。**抽完后用白名单校验** `ligand ↔ receptor_gene`,不一致则:
- `ligand_mismatch=true`
- `confidence` 自动降一档(high → medium → low)
- 严重不一致时记入 `needs_human_review`

v5 结果:**0 条 ligand mismatch**(因为"测的是 drug" 的论文 LLM 正确识别,把 drug 放进 `tested_compound` 而把 `ligand` 留 null)。

### 5. 客观 discard 规则(只对 LOW 生效)

LOW 置信度 entry 里混着两类:
- **(a) 边缘但合法**:受体确实提到了,但只有 1 句话没功能信息(对知识库是噪音)
- **(b) 真正的低质抽取**:LLM 不知道该不该抽,硬抽了一行

v5 自动判废 LOW 置信度里符合以下任一条件的 entry:
1. 只在前 30% 字符(背景段)出现,正文没出现
2. 全文出现 ≤ 2 次
3. 无 location / cell_type / pathway / function 信息

判废后 `discard=true`,`needs_human_review` 强制 false(不需要人审)。这样 22 条 LOW → 3 条 LOW,人工评审量大幅下降。

> **重要**:HIGH / MEDIUM 置信度的 entry 不受 discard 规则影响,即使只出现 1 次也保留。

### 6. 证据 / 推理 词数放宽到 80 词

v4 限制 evidence ≤ 30 词,实际中遇到"单句无法同时给到 ligand + function"的情况,LLM 只能截断丢上下文。v5 放宽到 ≤ 80 词,允许拼接 2-3 邻句,evidence 质量明显提升。

---

## 目录结构

```
.
├── README.md                                     # 本文件
├── requirements.txt                              # Python 依赖
├── .gitignore                                    # 排除 .env / 旧版数据 / 临时脚本
│
├── receptor_list_classic_neurotransmitter_gpcr.xlsx   # 24 受体清单(输入)
├── 神经递质GPCR知识库_项目目标.docx                # 项目目标说明
├── c6_experiment.pdf                          # 课程实验要求
├── c6b_llm_biomedical_kb.pdf                  # 参考实现 PDF
│
├── scripts/                                      # 全部可执行脚本
│   ├── fetch_pubmed_test_set.py                # ① PubMed 抓取 + 文本扫描
│   ├── extract_per_receptor.py                  # ② v5:逐个受体抽取 + discard 判定
│   ├── extract_fields_qwen.py                   # ③ v3:旧版批量抽取(对照,已弃用)
│   ├── generate_mentioned_list.py               # ④ 纯文本扫描的提及表
│   ├── compare_mentioned_vs_extracted.py        # ⑤ 漏抽 / 幻觉分析
│   └── .env.example                             # API 配置模板(Entrez + OpenRouter)
│
├── data/                                         # 抓取与抽取结果
│   ├── pubmed_test_set.json                    # 121 条原始摘要(per-receptor 抓)
│   ├── pubmed_test_set_summary.csv             # 按受体汇总
│   ├── pubmed_extracted.v5.json                # ★ 168 entry × 24 字段(v5 主结果)
│   ├── mentioned_receptors.json                 # 文本扫描的提及表(保证 Recall)
│   ├── review_samples_v5.xlsx                  # 24 条人工评审样本(3 reviewer × 8)
│   ├── review_samples_v4.xlsx                  # v4 时期的评审样本(历史)
│   ├── _review_samples_gold.json               # 人工评审完成后的 gold 标准
│   ├── high_discarded_analysis.json            # 之前误 discard HIGH 的分析(已修复)
│   └── objective_discard_analysis.json         # discard 规则的逐条分析
│
└── .trae/specs/                                  # 三阶段的规格文档(spec/tasks/checklist)
    ├── prepare-test-literature-set/              # ① 抓文献集
    ├── llm-field-extraction/                     # ② 抽字段 v2
    └── revise-multi-receptor-strategy/          # ③ 升级到 v3/v4/v5(多受体 + ligand 校验 + discard)
```

---

## 已知问题与下一步

### 已知

- **GABBR1 / GABBR2 数量偏少**(GABA_B 系统只有 3 篇摘要),需要补抓。
- **`source` 字段判定不完美**:v5 用 `pub_types` 推断 `review` vs `original_research`,但 `pub_types` 在某些 PMID 上为空,会 fallback 到 LLM 输出 → 偶有判错。
- **`is_review` vs `source`**:抓取时记录了 `is_review`(基于 PubMed 标识),v5 抽取时改用 `source`(基于 pub_types),两个字段不完全等价。

### 下一步

- [ ] 完成 24 条 gold standard 人工评审 → per-field P/R/F1 评估
- [ ] 把每个 v5 entry 的 LLM `reasoning` 字段利用起来(可解释性分析)
- [ ] 扩大检索:per-receptor 50-200 条做完整第一版知识库(从 121 → ~1000 条)
- [ ] Web UI(按 receptor / pathway / location 筛选,`needs_human_review` 单列)
- [ ] 关键词检索 + CSV / BibTeX 导出
- [ ] 接入更便宜的模型做预筛(用 `claude-opus-4` 抽太贵,可以用 `gpt-4o-mini` / `qwen-plus` 做粗筛后,不确定的送 `opus-4`)

---

## 依赖与致谢

**Python 库**:`biopython` `openpyxl` `python-dotenv` `openai`

**外部 API**:
- [NCBI Entrez (PubMed)](https://www.ncbi.nlm.nih.gov/books/NBK25501/) — 摘要抓取
- [OpenRouter](https://openrouter.ai/) — 统一 LLM 网关(本项目用 `anthropic/claude-opus-4`)

**参考实现**:课程材料 [`c6b_llm_biomedical_kb.pdf`](c6b_llm_biomedical_kb.pdf)
- 原实现是"转录因子 ↔ 靶基因",我们替换为"神经递质 GPCR ↔ 配体 / 通路 / 功能"
- prompt 设计、置信度划分、P/R/F1 评估三件套都沿用 c6b 的方法论

**数据集**:PubMed 为公开领域数据,本仓库只存摘要(abstract)与抽取出的结构化字段,无全文,不涉及版权风险。

---

## FAQ

**Q: v5 和 v3 / v4 的 entry 能直接对比吗?**
A: 不能直接对比 key 计数。v3 是 121 条(每 PMID 1 条),v4 是 170 条(每 (pmid, receptor) 1 条),v5 是 168 条(每 (pmid, receptor) 1 条,加上 discard 字段)。同 PMID 跨版本对比时,统一以 `(pmid, receptor_gene)` 为 key。

**Q: 跑 `extract_per_receptor.py` 跑一半挂了,能续跑吗?**
A: 可以。脚本启动时会读 `pubmed_extracted.v5.json` 已有的 PMID 列表,自动跳过已完成的;每 10 条 entry 增量写一次盘。中途中断后直接重跑同一条命令即可。

**Q: 我不想用 Claude Opus 4(太贵),能换模型吗?**
A: 可以,改 `scripts/.env` 里的 `OPENROUTER_MODEL`(v3 脚本用),或 v5 脚本命令行加 `--model anthropic/claude-3.5-sonnet`(任何 OpenRouter 支持的 model id)。换便宜模型后可能 precision / recall 下降,需要重新跑 v5 discard 评估。

**Q: 我的数据想保密,只想在本地跑可以吗?**
A: 完全可以。OpenRouter 接收 API key 后中转到各模型厂商,你的 prompt 和 abstract 会发到对应厂商。**PubMed 摘要是公开数据**,但如果你跑的内容里含敏感信息,建议用本地模型(如 Ollama)替换 OpenRouter。

**Q: 怎么加新的受体?**
A: 1) 在 `receptor_list_classic_neurotransmitter_gpcr.xlsx` 的 `included_receptors` sheet 加一行;2) 在 `scripts/extract_per_receptor.py` 的 `GENE_TO_CANONICAL_LIGAND` 字典加映射;3) 重跑 ① 抓取 + ② 抽取。

**Q: discard 的 entry 我也想看,会丢吗?**
A: 不会。`pubmed_extracted.v5.json` 里 discard 与非 discard 都存,只是 `discard=true` 标记 + `needs_human_review=false`,下游脚本 / Web UI 可以按需过滤。

**Q: 评审用 `review_samples_v5.xlsx` 是怎么挑出来的?**
A: `pick_review_samples_v5.py`(固定随机种子)从 168 条 entry 里按 confidence 桶 + needs_human_review 桶各抽 6 条,3 位评审人每人 8 条。需要换批次改 `random.seed()` 即可。

**Q: 为什么不用 abstract 全文,只存摘要?**
A: PubMed 摘要免费开放,版权清晰;全文涉及版权爬取问题(参考 c6b 第 6 页讨论)。结构化抽取只需要摘要就能覆盖大多数 receptor–ligand–function 关系。
