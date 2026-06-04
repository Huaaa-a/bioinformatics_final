# 神经递质 GPCR 知识库 · Neurotransmitter GPCR Knowledge Base

> 北京大学生物信息学实验期末项目 · 大二 · 第一版

经典神经递质 GPCR 的文献知识库:从 PubMed 自动抓摘要 → LLM 抽 14 个结构化字段 → 网页可视化。本仓库是**第一版**的实现,只覆盖 `receptor_list_classic_neurotransmitter_gpcr.xlsx` 中的 24 个受体(多巴胺 / 5-羟色胺 / 肾上腺素能 / 毒蕈碱型乙酰胆碱 / 代谢型谷氨酸 / GABA_B / 组胺)。

---

## 目录

- [当前进度](#当前进度)
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
| ① 测试文献集准备 | ✓ 完成 | `data/pubmed_test_set.json`(121 条 PubMed 摘要) |
| ② LLM 字段抽取 | ✓ 完成 | `data/pubmed_extracted.json`(121 条 × 14 字段) |
| ③ 质量审计 | ✓ 完成 | `_check_assignments.py`,错配率 1%(剩 1 条牡蛎异源) |
| ④ 二审 / needs_human_review | ✓ 完成 | 37 条标 review,8 条做了二审升档 |
| ⑤ Web UI / 检索 | ⏳ 待做 | — |

**最终数据:121 条,high 41 / medium 52 / low 28,0 parse_error / 0 api_error。**

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
├── c6.自选实验.pdf                               # 课程实验要求
├── c6b.基于LLM的生物医学文献知识库建立.pdf        # 参考实现 PDF
│
├── scripts/                                      # 全部可执行脚本
│   ├── fetch_pubmed_test_set.py                  # ① PubMed 抓取 + 文本扫描
│   ├── extract_fields_qwen.py                    # ② Qwen 抽 14 字段
│   ├── .env.example                              # Entrez 配置模板
│   └── .env.qwen.example                         # Qwen API 配置模板
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

把两个 `.env.example` 复制成 `.env` 并填值:

```powershell
copy scripts\.env.example scripts\.env
copy scripts\.env.qwen.example scripts\.env
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
│  · efetch 拿 abstract + journal + year                 │
│  · 文本扫描:抽 mentioned_receptors / mentioned_names   │
│    (剥 HTML <sub>、去连字符 HRH-4 → HRH4)              │
│  · 跨受体合并:同一 PMID 多受体提及则合一处              │
│  · 输出:data/pubmed_test_set.json (121 条)             │
└──────────────────┬─────────────────────────────────────┘
                   │
                   ▼
┌────────────────────────────────────────────────────────┐
│  ② extract_fields_qwen.py                              │
│  · 第一遍:基础 prompt 抽 14 字段                       │
│  · 解析失败 / API 失败 → 兜底填 null + confidence=low │
│  · 第二遍:对 confidence=low 的非错误记录               │
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

---

## 已知问题与下一步

### 已知
- **1% 错配(1 条)**:PMID 41621550 是牡蛎 `Cg5-HTR1A-like`,被人类 HTR1A 检索命中。物种歧义靠正则解决不了,已标 `low_confidence`。
- **30% mismatch 标记**:见上"关键设计决策 3"。
- **20% ligand=null**:部分论文实际讨论非经典 7 配体(HCAR1 / CXCR3 / TACR3 等)的 GPCR,这些不该进经典神经递质库,建议在 Web UI 阶段单独分区。
- **Oyster 论文**触发了一次"误识别为人类基因"的回归测试,顺便验证了连字符归一化反而更严格(牡蛎 `Cg5HTR1Alike` 不会被误判为 `HTR1A`)。

### 下一步
- [ ] Web UI(项目目标要求):按受体/通路/位置浏览,`needs_human_review` 单列
- [ ] 关键词检索(receptor / pathway / ligand)
- [ ] 导出 CSV / 引用 BibTeX
- [ ] 扩大检索:per-receptor 50-200 条做完整第一版知识库(从 121 → ~1000 条)
- [ ] 把 37 条 needs_human_review 的人工复核反馈写回 `_review_notes` 字段,形成闭环

---

## 依赖与致谢

**Python 库**:`biopython` `openpyxl` `pandas` `python-dotenv` `openai`

**外部 API**:
- [NCBI Entrez (PubMed)](https://www.ncbi.nlm.nih.gov/books/NBK25501/) — 摘要抓取
- [阿里云百炼 Qwen](https://bailian.console.aliyun.com/) — 字段抽取

**参考实现**:课程材料 `c6b.基于LLM的生物医学文献知识库建立.pdf`

**数据集**:PubMed 为公开领域数据,本仓库只存摘要(abstract)与抽取出的结构化字段,无全文,不涉及版权风险。
