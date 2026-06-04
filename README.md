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
| ① 测试文献集准备 | ✓ 完成 | `data/pubmed_test_set.json`(121 条 PubMed 摘要,含 DOI) |
| ② LLM 字段抽取 | ✓ 完成(v2) | `data/pubmed_extracted.json`(121 条 × 14 字段,白名单校验) |
| ③ 质量审计 | ✓ 完成(v2) | `_check_assignments.py`,错配率 1%;journal/year/DOI 100% 准确 |
| ④ 二审 / needs_human_review | ✓ 完成(v2) | 22 条标 review,24 条做了二审升档 |
| ⑤ Web UI / 检索 | ⏳ 待做 | — |

---

## 数据快照

> 数据截至 2026-06-05 (v2 审计修复后),生成命令 `python data/_stats_for_readme.py`

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

按受体基因分布(每个受体的摘要数):

| 受体 | 系统 | 数量 | 受体 | 系统 | 数量 |
|---|---|---|---|---|---|
| DRD1 | dopamine | 10 | CHRM1 | muscarinic | 8 |
| GRM7 | glutamate | 10 | ADRA2A | adrenergic | 8 |
| DRD2 | dopamine | 9 | HTR1A | serotonin | 7 |
| ADRB1 | adrenergic | 9 | ADRB2 | adrenergic | 7 |
| HRH1 | histamine | 9 | ADRA1A | adrenergic | 5 |
| HTR2A | serotonin | 8 | HRH2 | histamine | 5 |
| CHRM2 | muscarinic | 4 | GRM5 | glutamate | 4 |
| CHRM3 | muscarinic | 3 | CHRM4 | muscarinic | 3 |
| GRM1 | glutamate | 3 | HTR7 | serotonin | 2 |
| GABBR1 | GABA_B | 2 | HRH4 | histamine | 2 |
| HTR2C | serotonin | 1 | GRM2 | glutamate | 1 |
| GABBR2 | GABA_B | 1 | | | |

**抓取质量**:
- 唯一 PMID:121,无重复
- 检索方法:per-receptor 严格 esearch(`query_receptor_gene` = 搜索词)
- 文本扫描(`_strip_html` + 连字符归一化):消歧
- 跨受体合并:同 PMID 多受体提及只入一次
- 纯正则审计错配率:**1%**(1/86,牡蛎 `Cg5-HTR1A-like` 物种歧义)
- `low_confidence_query=True`:4 条

---

### 字段抽取结果(121 条 × 14 字段)

**confidence 分布**:

| confidence | 数量 | 占比 |
|---|---|---|
| high | 50 | 41% |
| medium | 49 | 40% |
| low | 22 | 18% |
| **合计** | **121** | **100%** |

**needs_human_review**:**22 / 121 (18%)**
- 14 条由 `receptor_gene_mismatch` 触发(异源二聚体、数据库类论文)
- 8 条由 LLM 自然低置信触发

**质量**:`parse_error=0`、`api_error=0`,0 失败。

**字段非空率**:

| 字段 | 说明 | 非空率 |
|---|---|---|
| `source` | 文献类型 | 121/121 (100%) |
| `receptor` | 受体全名 | 120/121 (99%) |
| `receptor_gene` | 基因符号 | 121/121 (100%) |
| `receptor_family` | 受体家族 | 121/121 (100%) |
| `ligand` | 配体 | 114/121 (94%) |
| `location` | 位置 | 66/121 (55%) |
| `cell_type` | 细胞类型 | 62/121 (51%) |
| `downstream_pathway` | 下游通路 | 72/121 (60%) |
| `function` | 功能 | 119/121 (98%) |
| `species` | 物种 | 112/121 (93%) |
| `evidence` | 证据句 | 121/121 (100%) |

> location / cell_type / pathway 非空率较低是因为许多 abstract 不提这些,非抽取质量问题。

**ligand 分布**:

| ligand | 数量 |
|---|---|
| norepinephrine/epinephrine | 27 |
| dopamine | 20 |
| acetylcholine | 17 |
| serotonin | 16 |
| glutamate | 16 |
| histamine | 15 |
| `(null)` | 7 |
| GABA | 3 |

> 7 条 `null` 多为非经典 7 配体的 GPCR(HCAR1 / CXCR3 / TACR3 等),或被 mismatch 标记后 ligand 也被冲掉。

**source 分布**:`original_research` 121（查询已排除 Review）

---

## 目录结构

```
.
├── README.md                                     # 本文件
├── requirements.txt                              # Python 依赖
├── .gitignore                                    # 排除 .env / pyc / venv
│
├── receptor_list_classic_neurotransmitter_gpcr.xlsx   # 24 受体清单(输入)
├── 神经递质GPCR知识库_项目目标.docx                # 项目目标说明
├── c6_experiment.pdf                          # 课程实验要求
├── c6b_llm_biomedical_kb.pdf                  # 参考实现 PDF
│
├── scripts/                                      # 全部可执行脚本
│   ├── fetch_pubmed_test_set.py                  # ① PubMed 抓取 + 文本扫描
│   ├── extract_fields_qwen.py                    # ② Qwen 抽 14 字段
│   ├── .env.example                              # 全部 API 配置模板(Entrez + Qwen)
│   └── .env.qwen.example                         # Qwen API 配置模板(旧版,仍可用)
│
├── data/                                         # 全部数据 + 日志
│   ├── pubmed_test_set.json                      # 121 条原始摘要
│   ├── pubmed_test_set_summary.csv               # 按系统汇总
│   ├── pubmed_extracted.json                     # 121 条 × 14 字段抽取结果
│   ├── run.log                                   # 抓取运行日志
│   ├── extract_run.log                           # 抽取运行日志
│   └── _check_assignments.py                     # 独立审计脚本
│
└── .trae/specs/                                  # 两阶段的规格文档
    ├── prepare-test-literature-set/              # ① 抓文献集
    │   ├── spec.md
    │   ├── tasks.md
    │   └── checklist.md
    └── llm-field-extraction/                     # ② 抽字段
        ├── spec.md
        ├── tasks.md
        └── checklist.md
```

**核心文件作用**

| 文件 | 作用 |
|---|---|
| `scripts/fetch_pubmed_test_set.py` | 按 24 个受体逐个 esearch PubMed → efetch 摘要 → 文本扫描校验 → 跨受体合并 → 落 `pubmed_test_set.json` |
| `scripts/extract_fields_qwen.py` | 读 `pubmed_test_set.json` → 调用 qwen-plus 抽 14 字段 → 落 `pubmed_extracted.json`,支持断点续跑和二审 |
| `data/_check_assignments.py` | 独立审计:用纯正则校验 `query_receptor_gene` 与 abstract 实际内容,给出错配率 |
| `.trae/specs/*/spec.md` | 每阶段的"为什么 / 改了什么 / 验收" |

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

# ② 抽字段(增量,已抽 PMID 自动跳过)
python scripts\extract_fields_qwen.py
```

**耗时参考**:抓 121 条 ≈ 30-40 分钟(Entrez 限速),抽 121 条 ≈ 14 分钟(Qwen 0.5s/次 + 7s API 延迟)。

### 4. 审计数据

```powershell
python data\_check_assignments.py
```

输出每条 `query_receptor_gene` 与 abstract 实际内容的匹配情况,以及 `low_confidence_query` 标记是否准确。

---

## 数据流

```
┌────────────────────────────────────────────────────────┐
│  输入                                                   │
│  · receptor_list_classic_neurotransmitter_gpcr.xlsx    │
│    (24 个 GPCR:DRD1/DRD2/HTR1A/HTR2A/HRH1/...)        │
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
│  ② extract_fields_qwen.py                              │
│  · 第一遍:基础 prompt 抽 14 字段                       │
│    (prompt 含 24 受体标准 gene/family/aliases 列表)    │
│  · literature 字段直接用 PubMed 源数据,不让 LLM 猜    │
│  · source 字段优先用 PubMed PublicationTypeList 判定    │
│  · receptor_gene / receptor_family 白名单校验+fallback │
│  · 解析失败 / API 失败 → 兜底填 null + confidence=low │
│  · 第二遍:对 confidence=low 或 mismatch 的非错误记录   │
│    用更详细的 review prompt 升档                       │
│  · 内置:receptor_gene_query 对照 + mismatch 标记       │
│    (query≠LLM 时自动 needs_human_review + 降档)        │
│  · 输出:data/pubmed_extracted.json (121 条 × 14 字段)  │
└──────────────────┬─────────────────────────────────────┘
                   │
                   ▼
┌────────────────────────────────────────────────────────┐
│  ③ (待做) Web UI / 检索                                │
│  · 按受体浏览、按 ligand/pathway/location 筛选         │
│  · needs_human_review 一栏单独标出                     │
└────────────────────────────────────────────────────────┘
```

---

## 14 字段说明

`pubmed_extracted.json` 每条记录含 14 个业务字段 + 3 个审计字段:

| # | 字段 | 类型 | 说明 |
|---|---|---|---|
| 1 | `pmid` | string | PubMed ID |
| 2 | `source` | enum | `review` / `original_research` |
| 3 | `receptor` | string | 受体全名,如 "dopamine D1 receptor" |
| 4 | `receptor_gene` | string | HGNC 基因符号,大写,如 `DRD1` |
| 5 | `receptor_family` | string | 受体家族,如 "Class A dopamine D1-like" |
| 6 | `ligand` | enum | `dopamine` / `serotonin` / `norepinephrine/epinephrine` / `acetylcholine` / `glutamate` / `GABA` / `histamine` / null |
| 7 | `location` | string | 组织/脑区,`null` 若 abstract 未提 |
| 8 | `cell_type` | string | 细胞类型,`null` 若 abstract 未提 |
| 9 | `downstream_pathway` | string | 信号通路,如 `Gαs/cAMP/PKA` |
| 10 | `function` | string | 生理功能,如 "reward" |
| 11 | `species` | string | 物种,`null` 若 abstract 未提 |
| 12 | `literature` | object | `{pmid, doi, title, year, journal}` |
| 13 | `evidence` | string | abstract 中支持该条目的 ≤ 30 词原句 |
| 14 | `confidence` | enum | `high` / `medium` / `low` |
| A | `receptor_gene_query` | string | PubMed 检索时用的基因(对照) |
| B | `receptor_gene_mismatch` | bool | LLM 抽出与 query 不一致时为 true |
| C | `needs_human_review` | bool | true ⇔ 需人工复核(mismatch / low / 错误) |

完整 prompt 模板见 [.trae/specs/llm-field-extraction/spec.md](.trae/specs/llm-field-extraction/spec.md)。

---

## 关键设计决策

### 1. 抓取:per-receptor 查询
最初用 per-system(按神经递质)轮询,72% 错配(论文被分到错受体)。**改为 per-receptor 单独 esearch**,`query_receptor_gene` 直接等于搜索词,再用文本扫描 + 跨受体合并消歧,错配率从 72% → 1%。

### 2. 文本扫描:`_strip_html` 与连字符归一化
PubMed 摘要里有 `<sub>HR</sub>H2</sub>` 这种残留 HTML,直接 `re.sub(r"<[^>]+>", "", text)` 会**误删** `P < 0.05` 里的尖括号。修复:只剥以字母开头的真标签 `<[a-zA-Z][^>]*>`。  
同时归一化连字符:基因匹配在 `text_norm = text.replace("-", "")` 上做,让 `HRH-4` / `DRD-2` 命中 `HRH4` / `DRD2`。

### 3. mismatch 自动标 review
LLM 抽出的 `receptor_gene` 与 `query_receptor_gene` 不一致时,自动 `needs_human_review=true` + 降档。**有 30% 论文触发**,主要是异源二聚体、综述、GPCR 数据库/组学类论文。这给我们一个关键提示:**PubMed 搜索命中 ≠ 论文主题**。

### 4. 断点续跑
两个脚本都支持。每条成功处理后**立即写盘**(覆盖整个 JSON),中断后重跑自动跳过。中途可 Ctrl-C,下次接着跑。

### 5. 顺序 + 0.5s 限速
不并发。原因:Qwen 免费配额下并发会触发 429。本数据量级(121 条)顺序 14 分钟跑完,没必要并发。

### 6. v2 审计修复:literature 字段用 PubMed 源数据
v1 让 LLM 从 abstract 猜 journal/year/doi,导致 69% journal 名不一致。v2 改为 `literature` 字段直接从 PubMed efetch 的结构化数据填充,LLM 不再负责这部分。DOI 从 `PubmedData/ArticleIdList` 提取。

### 7. v2 审计修复:白名单校验
- `receptor_gene`:24 个标准基因符号白名单,LLM 输出不在白名单中则 fallback 到 `query_receptor_gene`
- `receptor_family`:从 xlsx 读取标准映射,LLM 输出不一致时强制替换
- `source`:优先用 PubMed `PublicationTypeList` 判定 review/original_research
- `common_aliases`:xlsx 中的别名传入 `scan_mentions()` 和 LLM prompt,减少漏检

---

## 已知问题与下一步

### 已知
- **18% needs_human_review(22/121)**:14 条由 receptor_gene_mismatch 触发(LLM 抽出与 PubMed 检索词不同),8 条由 LLM 自然低置信触发。
- **6% ligand=null(7 条)**:多为非经典 7 配体的 GPCR(HCAR1 / CXCR3 / TACR3 等)。这些不该进经典神经递质库,建议在 Web UI 阶段单独分区。
- **3 条 GABBR1/GABBR2** 数量偏少(GABBR1:2, GABBR2:1),未来扩库时需专门补抓。

### 下一步
- [ ] Web UI(项目目标要求):按受体/通路/位置浏览,`needs_human_review` 单列
- [ ] 关键词检索(receptor / pathway / ligand)
- [ ] 导出 CSV / 引用 BibTeX
- [ ] 扩大检索:per-receptor 50-200 条做完整第一版知识库(从 121 → ~1000 条)
- [ ] 把 37 条 needs_human_review 的人工复核反馈写回 `_review_notes` 字段,形成闭环

---

## 依赖与致谢

**Python 库**:`biopython` `openpyxl` `python-dotenv` `openai`

**外部 API**:
- [NCBI Entrez (PubMed)](https://www.ncbi.nlm.nih.gov/books/NBK25501/) — 摘要抓取
- [阿里云百炼 Qwen](https://bailian.console.aliyun.com/) — 字段抽取

**参考实现**:课程材料 [`c6b_llm_biomedical_kb.pdf`](c6b_llm_biomedical_kb.pdf)

**数据集**:PubMed 为公开领域数据,本仓库只存摘要(abstract)与抽取出的结构化字段,无全文,不涉及版权风险。
