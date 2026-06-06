# 神经递质 GPCR 知识库 · Neurotransmitter GPCR Knowledge Base

> 北京大学生物信息学实验期末项目 · 大二 · 第一版

经典神经递质 GPCR 的文献知识库:从 PubMed 自动抓摘要 → LLM 抽 14 个结构化字段 → 网页可视化。本仓库是**第一版**的实现,只覆盖 `receptor_list_classic_neurotransmitter_gpcr.xlsx` 中的 24 个受体(多巴胺 / 5-羟色胺 / 肾上腺素能 / 毒蕈碱型乙酰胆碱 / 代谢型谷氨酸 / GABA_B / 组胺)。

---

## 目录

- [当前进度](#当前进度)
- [数据快照](#数据快照)
- [目录结构](#目录结构)
- [快速开始](#快速开始)
- [数据流](#数据流)
- [14 字段说明](#14-字段说明)
- [关键设计决策](#关键设计决策)
- [已知问题与下一步](#已知问题与下一步)
- [依赖与致谢](#依赖与致谢)

---

## 当前进度

| 阶段 | 状态 | 产物 |
|---|---|---|
| ① 测试文献集准备 | ✓ 完成 | `data/pubmed_test_set.json`(121 条 PubMed 摘要,per-receptor esearch) |
| ② LLM 字段抽取 | ✓ 完成(v4) | `data/pubmed_extracted.v4.json`(170 条 entry,17 字段 + 4 审计字段,逐个受体抽取) |
| ③ 质量审计 | ✓ 完成(v4) | 错配率 / ligand_mismatch 率 / needs_human_review 率 分桶统计 |
| ④ 二审 / needs_human_review | ✓ 完成(v4) | confidence=low / mismatch / ligand_mismatch 触发二审 |
| ⑤ Gold standard + 评估 | ⏳ 建设中 | `data/_human_review_samples.md` / `_review_samples_gold.json` / `_eval_against_gold.py` |
| ⑥ Web UI / 检索 | ⏳ 待做 | — |

### v4 核心改进：逐个受体抽取

**问题**：v3 版本一次处理一整篇论文，LLM 只抽一个 focal receptor，多受体论文会漏抽其他受体。

**解决**：先文本扫描找到所有提到的受体，对每个受体单独调用一次 LLM 抽取，合并结果。

**效果**：121 条 → 170 条（+40%），Ligand mismatch 从有到 0%。

---

## 数据快照

> 数据截至 2026-06-06 (v4 schema)。具体数字以运行分析脚本为准。

### 抓取结果(121 条 PubMed 摘要)

按神经递质系统分布:

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

### v4 字段抽取结果(170 entry,17 标准字段 + 4 审计字段)

**v4 效果对比**：

| 指标 | v3 | v4 | 变化 |
|------|-----|-----|------|
| 总条目数 | 121 | **170** | +40% |
| Ligand mismatch | 有 | **0%** | 消除 |
| 多受体论文覆盖 | 漏抽 | **全抽到** | 解决 |

**confidence 分布**(v4):

| confidence | 数量 | 占比 |
|---|---|---|
| high | 59 | 34.7% |
| medium | 89 | 52.4% |
| low | 22 | 12.9% |
| **合计** | **170** | **100%** |

**需要人工审核**：22 条（12.9%），都是 low 置信度条目。

---

## 目录结构

```
.
├── README.md                                     # 本文件
├── requirements.txt                              # Python 依赖
├── .gitignore                                    # 排除 .env / 历史审计文件 / 临时脚本
│
├── receptor_list_classic_neurotransmitter_gpcr.xlsx   # 24 受体清单(输入)
├── 神经递质GPCR知识库_项目目标.docx                # 项目目标说明
├── c6_experiment.pdf                          # 课程实验要求
├── c6b_llm_biomedical_kb.pdf                  # 参考实现 PDF
│
├── scripts/                                      # 全部可执行脚本
│   ├── fetch_pubmed_test_set.py                # ① PubMed 抓取 + 文本扫描
│   ├── extract_fields_qwen.py                   # ② Qwen 抽字段(v3 JSON 数组)
│   ├── extract_per_receptor.py                  # ② v4:逐个受体抽取(核心改进)
│   ├── generate_mentioned_list.py               # 生成提及表(文本扫描，非LLM)
│   ├── compare_mentioned_vs_extracted.py        # 对比提及表和抽取表
│   └── .env.example                             # API 配置模板(Entrez + Qwen)
│
├── data/                                         # 抓取与抽取结果
│   ├── pubmed_test_set.json                    # 121 条原始摘要(per-receptor)
│   ├── pubmed_test_set_summary.csv             # 按受体汇总
│   ├── pubmed_extracted.v4.json                # 170 entry × 17 字段(v4 当前)
│   ├── pubmed_extracted.v2.json                # v2 数据备份(对照)
│   ├── pubmed_extracted.json                   # v3 数据备份(对照)
│   ├── mentioned_receptors.json                 # 提及表(文本扫描生成)
│   ├── _check_assignments.py                    # 审计:输出 mismatch / ligand_mismatch 率
│   ├── _pick_review_samples.py                  # 挑 high/medium/low 各 6 条供人工审
│   ├── _human_review_samples.md                 # 待人工填 gold 的样本(由 pick 生成)
│   ├── _human_review_samples.json              # 历史样本 json
│   ├── _review_samples_gold.json               # 人工填完后结构化的 gold
│   ├── _eval_against_gold.py                  # per-field P/R/F1 评估脚本
│   └── _eval_errors.md                        # 评估误差样本 dump(由 eval 生成)
│
└── .trae/specs/                                  # 三阶段的规格文档(spec/tasks/checklist)
    ├── prepare-test-literature-set/              # ① 抓文献集
    ├── llm-field-extraction/                     # ② 抽字段 v2
    └── revise-multi-receptor-strategy/          # ③ 升级到 v3/v4(多受体 + ligand 校验)
```

---

## 快速开始

### 1. 准备 Python 环境

```powershell
git clone https://github.com/Huaaa-a/bioinformatics_final.git
cd bioinformatics_final
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. 配置 API 凭据

把 `scripts/.env.example` 复制成 `scripts/.env` 并填值:

```powershell
copy scripts\.env.example scripts\.env
```

| Key | 来源 | 必填 |
|---|---|---|
| `ENTREZ_EMAIL` | 任意能收到邮件的邮箱(NCBI 强制要求) | ✓ |
| `ENTREZ_API_KEY` | [NCBI Account](https://www.ncbi.nlm.nih.gov/account/settings/) 免费申请,有 key 时 3→10 req/s | ✗ |
| `QWEN_API_KEY` | [阿里云百炼](https://bailian.console.aliyun.com/) 申请,走 OpenAI 兼容接口 | ✓ |
| `QWEN_MODEL` | `qwen-turbo` / `qwen-plus` / `qwen-max`,默认 `qwen-plus` | ✗ |

### 3. 跑一遍流程

```powershell
# ① 抓文献集(增量,已有 PMID 自动跳过)
python scripts\fetch_pubmed_test_set.py

# ② 抽字段 v4(推荐，逐个受体抽取，解决多受体论文漏抽)
python scripts\extract_per_receptor.py

# ③ 抽字段 v3(旧版，一次处理一整篇论文)
python scripts\extract_fields_qwen.py
```

**v4 vs v3**：
- v4（逐个受体抽取）：170条，多受体论文全覆盖，推荐使用
- v3（批量抽取）：121条，多受体论文可能漏抽

**耗时参考**:抓 121 条 ≈ 30-40 分钟(Entrez 限速),抽 170 条 ≈ 20 分钟(Qwen 0.5s/次 + API 延迟)。

### 4. 生成提及表（可选）

```powershell
# 生成基于文本扫描的提及表（保证Recall，不依赖LLM）
python scripts\generate_mentioned_list.py

# 对比提及表和抽取表
python scripts\compare_mentioned_vs_extracted.py
```

---

## 数据流

```
┌────────────────────────────────────────────────────────┐
│  输入                                                   │
│  · receptor_list_classic_neurotransmitter_gpcr.xlsx    │
│    (24 个 GPCR:DRD1/DRD2/HTR1A/HTR2A/HRH1/...)      │
└──────────────────┬─────────────────────────────────────┘
                   │
                   ▼
┌────────────────────────────────────────────────────────┐
│  ① fetch_pubmed_test_set.py                            │
│  · per-receptor esearch(receptor_gene[Title/Abstract]) │
│  · efetch 拿 abstract + journal + year + DOI + pub_types│
│  · 文本扫描:抽 mentioned_receptors / mentioned_names   │
│    (剥 HTML <sub>、去连字符 HRH-4 → HRH4、xlsx 别名)  │
│  · 跨受体合并:同一 PMID 多受体提及则合一处              │
│  · 输出:data/pubmed_test_set.json (121 条)             │
└──────────────────┬─────────────────────────────────────┘
                   │
                   ▼
┌────────────────────────────────────────────────────────┐
│  ② extract_per_receptor.py (v4 推荐)                   │
│  · 改进的文本扫描:识别所有提到的受体(含常见缩写)        │
│    - DRD1→D1R, DRD2→D2R, HTR1A→5-HT1A               │
│  · 对每个提到的受体,单独调用一次LLM抽取                  │
│    (解决多受体论文漏抽问题)                              │
│  · 完整规范化逻辑:ligand校验/置信度降档/mismatch检测    │
│  · 输出:data/pubmed_extracted.v4.json (170 entry)      │
└──────────────────┬─────────────────────────────────────┘
                   │
                   ▼
┌────────────────────────────────────────────────────────┐
│  ③ generate_mentioned_list.py (可选)                    │
│  · 纯文本扫描,不用LLM                                  │
│  · 生成mentioned_receptors.json                         │
│  · 保证Recall(不漏抽),但信息少(只有snippet)             │
└──────────────────┬─────────────────────────────────────┘
                   │
                   ▼
┌────────────────────────────────────────────────────────┐
│  ④ 审计 + 评估                                          │
│  · python data/_check_assignments.py  → mismatch 率     │
│  · python data/_pick_review_samples.py → _human_review_samples.md│
│  · 人工填 gold → data/_review_samples_gold.json        │
│  · python data/_eval_against_gold.py → per-field P/R/F1│
└────────────────────────────────────────────────────────┘
```

---

## 字段说明

`pubmed_extracted.v4.json` 是 entry 数组,每条 entry 含 17 个业务字段 + 7 个审计字段:

### 17 个业务字段

| # | 字段 | 类型 | 说明 |
|---|---|---|---|
| 1 | `pmid` | string | PubMed ID |
| 2 | `source` | enum | `review` / `original_research` |
| 3 | `receptor` | string | 受体全名,如 "dopamine D1 receptor" |
| 4 | `receptor_gene` | string | HGNC 基因符号,大写,如 `DRD1` |
| 5 | `receptor_family` | string | 受体家族,从 xlsx 读 |
| 6 | `ligand` | enum | `dopamine` / `serotonin` / `norepinephrine/epinephrine` / `acetylcholine` / `glutamate` / `GABA` / `histamine` / null |
| 7 | `location` | string | 组织/脑区,`null` 若 abstract 未提 |
| 8 | `cell_type` | string | 细胞类型,`null` 若 abstract 未提 |
| 9 | `downstream_pathway` | string | 信号通路,如 `Gαs/cAMP/PKA` |
| 10 | `function` | string | 生理功能,如 "reward" |
| 11 | `species` | string | 物种,`null` 若 abstract 未提 |
| 12 | `literature` | object | `{pmid, doi, title, year, journal}`,PubMed 源数据 |
| 13 | `evidence` | string | abstract 中支持该条目的 ≤ 30 词原句 |
| 14 | `confidence` | enum | `high` / `medium` / `low` |
| 15 | `reasoning` | string | LLM 推断本条 entry 的关键推理(≤ 80 词) |
| 16 | `tested_compound` | string\|null | 论文实际测的药物名(drug),与内源性 ligand 分开 |
| 17 | `extraction_meta` | object | `{model, prompt_version, extracted_at}` |

### 7 个审计 / 对照字段

| # | 字段 | 类型 | 说明 |
|---|---|---|---|
| A | `receptor_gene_query` | string | 检索时用的基因(对照) |
| B | `receptor_gene_mismatch` | bool | LLM 抽出与 query 不一致时为 true |
| C | `ligand_mismatch` | bool | LLM 抽出的 ligand 与 receptor 的 canonical ligand 不一致 |
| D | `ligand_mismatch_reason` | str | 如 `"HTR1A canonical=serotonin, got ligand=norepinephrine/epinephrine"` |
| E | `needs_human_review` | bool | true ⇔ 需人工复核(mismatch / low / 错误) |
| F | `query_receptor_gene` | string | 抓取时 query,等同于 `receptor_gene_query` |
| G | `canonical_ligand_for_query_receptor` | string | 该受体的 canonical ligand(白名单查表) |

---

## 关键设计决策

### 1. v4:逐个受体抽取（核心改进）

**问题**：v3 一次处理一整篇论文，LLM 输出 JSON 数组，但 LLM 倾向于只输出一个 focal receptor。多受体论文（如 DRD1-DRD2 异源二聚体）会漏抽。

**解决**：
1. 用改进的文本扫描找到所有提到的受体（不依赖 LLM 判断 focal）
2. 对每个提到的受体，单独调用一次 LLM 抽取
3. 合并结果

**效果**：121 条 → 170 条（+40%），Ligand mismatch 0%。

### 2. 改进的文本扫描

新增常见缩写识别：
- DRD1 → D1R, D1 receptor
- DRD2 → D2R, D2 receptor
- HTR1A → 5-HT1A
- 连字符归一化：DRD2 → DRD2

### 3. 双表策略：提及表 + 抽取表

- **抽取表**（`pubmed_extracted.v4.json`）：LLM 抽取，信息丰富，但可能有漏抽
- **提及表**（`mentioned_receptors.json`）：纯文本扫描，保证 Recall（100% 不漏），但信息少（只有 snippet）

两表互补：提及表保证不漏，抽取表提供丰富信息。

### 4. 置信度划分逻辑

| 置信度 | 触发条件 |
|---|---|
| high | receptor + ligand + 至少一个功能字段(location/cell_type/pathway/function)都清晰 |
| medium | receptor/ligand 清晰，部分字段缺失 |
| low | receptor 只在列表/缩写表中提到，无功能描述 |

自动降档：
- 缺 2 个以上核心字段：high → medium
- 缺 3 个以上核心字段：medium → low
- mismatch 或 ligand_mismatch：额外降一档

### 5. Recall & Precision 平衡

- **Recall**：用文本扫描保证（100% 不漏抽提到的受体）
- **Precision**：用置信度和规范化保证（只有高质量的才是 high）
- **trade-off**：需要人工审核的只有 low 置信度（12.9%）

---

## 已知问题与下一步

### 已知
- **GABBR1/GABBR2 数量偏少**(GABBR1:2, GABBR2:1),未来扩库时需专门补抓。
- **ligand_mismatch 含义**:不一定是 LLM 错,也可能是 LLM 准确识别了"论文测的不是该受体的内源配体"。人工 review 时要分清"LLM 错" vs "论文就是测 drug"。

### 下一步
- [ ] 完成 18 样本 gold standard 人工填写(用 `data/_pick_review_samples.py` 挑)
- [ ] 跑 `data/_eval_against_gold.py` 拿 per-field P/R/F1
- [ ] Web UI(项目目标要求):按受体/通路/位置浏览,`needs_human_review` 单列
- [ ] 关键词检索(receptor / pathway / ligand)
- [ ] 导出 CSV / 引用 BibTeX
- [ ] 扩大检索:per-receptor 50-200 条做完整第一版知识库(从 121 → ~1000 条)

---

## 依赖与致谢

**Python 库**:`biopython` `openpyxl` `python-dotenv` `openai`

**外部 API**:
- [NCBI Entrez (PubMed)](https://www.ncbi.nlm.nih.gov/books/NBK25501/) — 摘要抓取
- [阿里云百炼 Qwen](https://bailian.console.aliyun.com/) — 字段抽取

**参考实现**:课程材料 [`c6b_llm_biomedical_kb.pdf`](c6b_llm_biomedical_kb.pdf)

**数据集**:PubMed 为公开领域数据,本仓库只存摘要(abstract)与抽取出的结构化字段,无全文,不涉及版权风险。
