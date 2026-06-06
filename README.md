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
| ② LLM 字段抽取 | ✓ 完成(v3) | `data/pubmed_extracted.json`(121 条 entry,17 字段 + 4 审计字段,JSON 数组输出) |
| ③ 质量审计 | ✓ 完成(v3) | 错配率 / ligand_mismatch 率 / needs_human_review 率 分桶统计 |
| ④ 二审 / needs_human_review | ✓ 完成(v3) | confidence=low / mismatch / ligand_mismatch 触发二审 |
| ⑤ Gold standard + 评估 | ⏳ 建设中 | `data/_human_review_samples.md` / `_review_samples_gold.json` / `_eval_against_gold.py` |
| ⑥ Web UI / 检索 | ⏳ 待做 | — |

> v2 → v3 的关键变化见 [关键设计决策](#关键设计决策)。v3 备份在 `data/pubmed_extracted.v2.json`。

---

## 数据快照

> 数据截至 2026-06-06 (v3 schema:`reasoning` / `tested_compound` / `ligand_mismatch` 字段均已落地)。具体数字以 `python data/_check_assignments.py` 现场输出为准。

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

### 字段抽取结果(121 entry,17 标准字段 + 4 审计字段)

**数据模型**:1 PMID → N entry(N = focal receptor 数)。LLM 输出 JSON 数组。

**confidence 分布**(以 v3 跑完为准,运行 `python data/_check_assignments.py` 拿当前值):

| confidence | 大致占比 |
|---|---|
| high | ~40% |
| medium | ~40% |
| low | ~20% |

**审计字段**(v3 新增):
- `receptor_gene_mismatch`(bool):LLM 抽出的 `receptor_gene` 与 `query_receptor_gene` 不一致
- `ligand_mismatch`(bool):LLM 抽出的 `ligand` 与 `receptor_gene` 的 canonical ligand 不一致
- `ligand_mismatch_reason`(str):`"receptor_gene=HTR1A canonical=serotonin, got ligand=norepinephrine/epinephrine"` 等
- `needs_human_review`(bool):`confidence=low` 或任一 mismatch 触发
- `reasoning`(str, ≤ 80 词):LLLM 推断本条 entry 的关键推理
- `tested_compound`(str|null):论文实际测的药物名(drug),与内源性 `ligand` 字段分开

**字段非空率**(以 v3 跑完为准,典型值):
| 字段 | 说明 | 典型非空率 |
|---|---|---|
| `source` | 文献类型 | 100% |
| `receptor` | 受体全名 | ~99% |
| `receptor_gene` | 基因符号 | 100% |
| `receptor_family` | 受体家族 | 100% |
| `ligand` | 配体 | ~95% |
| `location` | 位置 | ~55% |
| `cell_type` | 细胞类型 | ~50% |
| `downstream_pathway` | 下游通路 | ~60% |
| `function` | 功能 | ~98% |
| `species` | 物种 | ~93% |
| `evidence` | 证据句 | 100% |
| `reasoning` | 思考过程(v3) | 100% |
| `tested_compound` | 药物名(v3) | 10-30% |

> location / cell_type / pathway 非空率较低是因为许多 abstract 不提这些,非抽取质量问题。

**ligand 分布**(以 v3 跑完为准,典型值):

| ligand | 典型数量 |
|---|---|
| norepinephrine/epinephrine | ~27 |
| dopamine | ~20 |
| acetylcholine | ~17 |
| serotonin | ~16 |
| glutamate | ~16 |
| histamine | ~15 |
| `(null)` | ~5-8(论文只测 drug,tested_compound 非空) |
| GABA | ~3 |

**source 分布**:`review` 与 `original_research` 都有(不再 blanket 排除 Review,改用 `pub_types` 字段标识)。

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
│   ├── fetch_pubmed_test_set.py                  # ① PubMed 抓取 + 文本扫描
│   ├── extract_fields_qwen.py                    # ② Qwen 抽字段(v3 JSON 数组)
│   └── .env.example                              # API 配置模板(Entrez + Qwen)
│
├── data/                                         # 抓取与抽取结果
│   ├── pubmed_test_set.json                      # 121 条原始摘要(per-receptor)
│   ├── pubmed_test_set_summary.csv               # 按受体汇总
│   ├── pubmed_extracted.json                     # 121 entry × 17 字段(v3 当前)
│   ├── pubmed_extracted.v2.json                  # v2 数据备份(对照)
│   ├── _check_assignments.py                     # 审计:输出 mismatch / ligand_mismatch 率
│   ├── _pick_review_samples.py                   # 挑 high/medium/low 各 6 条供人工审
│   ├── _human_review_samples.md                  # 待人工填 gold 的样本(由 pick 生成)
│   ├── _human_review_samples.json                # 历史样本 json
│   ├── _review_samples_gold.json                 # 人工填完后结构化的 gold
│   ├── _eval_against_gold.py                     # per-field P/R/F1 评估脚本
│   └── _eval_errors.md                           # 评估误差样本 dump(由 eval 生成)
│
└── .trae/specs/                                  # 三阶段的规格文档(spec/tasks/checklist)
    ├── prepare-test-literature-set/              # ① 抓文献集
    ├── llm-field-extraction/                     # ② 抽字段 v2
    └── revise-multi-receptor-strategy/           # ③ 升级到 v3(多受体 + ligand 校验)
```

> 不入库的文件(`.gitignore` 兜底):`scripts/.env` 真实凭据、`run.log` / `extract_run.log` / `data/extract_run.log` 运行日志、`data/__pycache__/`、`.trae/documents/` 计划/审计稿。

**核心文件作用**

| 文件 | 作用 |
|---|---|
| `scripts/fetch_pubmed_test_set.py` | 按 24 个受体逐个 esearch PubMed(`gene OR alias` OR 拼接)→ efetch 摘要 + 文本扫描(剥 HTML / 去连字符 / 扫 ligand)→ 跨受体合并 → 落 `pubmed_test_set.json`,支持增量断点续跑 |
| `scripts/extract_fields_qwen.py` | 读 `pubmed_test_set.json` → 调用 qwen-plus 抽 17 字段 + 4 审计字段(LLM 输出 JSON 数组)→ 落 `pubmed_extracted.json`,支持断点续跑、低置信二审、白名单校验、ligand ↔ receptor 强校验 |
| `data/_check_assignments.py` | 审计脚本:按 entry 维度统计 mismatch / ligand_mismatch / needs_human_review 率,按受体 × 系统分桶 |
| `data/_pick_review_samples.py` | 挑 high/medium/low 各 6 条样本,生成 `_human_review_samples.md` 供人工填 gold |
| `data/_eval_against_gold.py` | per-field P/R/F1 评估,误差样本 dump 到 `_eval_errors.md` |
| `scripts/.env.example` | API 配置模板(NCBI Entrez + 阿里云千问);复制为 `scripts/.env` 后填值 |
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

### 4. 验证数据(可选)

直接看 `data/pubmed_test_set.json` / `data/pubmed_extracted.json` 即可,或用 `jq`/`pandas` 自行统计;项目里没有再保留独立审计脚本(都合并进主流程了)。

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
│  ② extract_fields_qwen.py (v3)                         │
│  · 第一遍:JSON 数组 prompt 抽 17 字段 + 4 审计字段      │
│    (prompt 含 24 受体标准 gene/family/aliases 列表 +    │
│     canonical ligand 表 + 候选 focal receptor 提示)      │
│  · literature 字段直接用 PubMed 源数据,不让 LLM 猜    │
│  · source 字段优先用 PubMed PublicationTypeList 判定    │
│  · receptor_gene / receptor_family 白名单校验+fallback │
│  · ligand ↔ receptor 强校验(不匹配 → mismatch + 降档)  │
│  · tested_compound 字段单独记录论文实际测的药物         │
│  · reasoning 字段记录 LLM 推断本条 entry 的关键推理     │
│  · 解析失败 / API 失败 → 兜底填 null + confidence=low │
│  · 第二遍:对 confidence=low / mismatch / ligand_mismatch│
│    的非错误记录用更详细的 review prompt 升档            │
│  · 输出:data/pubmed_extracted.json (121 entry 数组)    │
└──────────────────┬─────────────────────────────────────┘
                   │
                   ▼
┌────────────────────────────────────────────────────────┐
│  ③ 审计 + 评估                                          │
│  · python data/_check_assignments.py  → mismatch 率     │
│  · python data/_pick_review_samples.py → _human_review_samples.md│
│  · 人工填 gold → data/_review_samples_gold.json        │
│  · python data/_eval_against_gold.py → per-field P/R/F1 │
└──────────────────┬─────────────────────────────────────┘
                   │
                   ▼
┌────────────────────────────────────────────────────────┐
│  ④ (待做) Web UI / 检索                                │
│  · 按受体浏览、按 ligand/pathway/location 筛选         │
│  · needs_human_review 一栏单独标出                     │
└────────────────────────────────────────────────────────┘
```

---

## 字段说明

`pubmed_extracted.json` 是 entry 数组,每条 entry 含 17 个业务字段 + 7 个审计字段:

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
| 15 | `reasoning` | string(v3) | LLM 推断本条 entry 的关键推理(≤ 80 词) |
| 16 | `tested_compound` | string\|null(v3) | 论文实际测的药物名(drug),与内源性 ligand 分开 |
| 17 | `extraction_meta` | object | `{model, prompt_version, attempt_count, extracted_at}` |

### 7 个审计 / 对照字段

| # | 字段 | 类型 | 说明 |
|---|---|---|---|
| A | `receptor_gene_query` | string | PubMed 检索时用的基因(对照) |
| B | `receptor_gene_mismatch` | bool | LLM 抽出与 query 不一致时为 true(异源二聚体 / 综述) |
| C | `ligand_mismatch` | bool(v3) | LLM 抽出的 ligand 与 receptor 的 canonical ligand 不一致 |
| D | `ligand_mismatch_reason` | str(v3) | 如 `"HTR1A canonical=serotonin, got ligand=norepinephrine/epinephrine"` |
| E | `needs_human_review` | bool | true ⇔ 需人工复核(mismatch / low / 错误) |
| F | `query_receptor_gene` | string | 抓取时 query,等同于 `receptor_gene_query` |
| G | `canonical_ligand_for_query_receptor` | string | 该受体的 canonical ligand(白名单查表) |

完整 prompt 模板见 [.trae/specs/revise-multi-receptor-strategy/spec.md](.trae/specs/revise-multi-receptor-strategy/spec.md)。

---

## 关键设计决策

### 1. 抓取:per-receptor 查询(72% → 1%)
最初用 per-system(按神经递质)轮询,一个 query 含 5-HT1A/1B/.../7 共 11 个基因,`query_receptor_gene` 是这坨的代表,论文可能只讨论 1A,LLM 抽出 1A → 与 query 不一致 → 算 mismatch(72%)。
**改为 per-receptor 单独 esearch**:`query_receptor_gene` 直接等于搜索词,再用文本扫描 + 跨受体合并消歧,真错配率从 72% → ~1%(剩余的 mismatch 是真的多受体论文,应展开为多条 entry)。
**结论**:每条记录的 `query_receptor_gene` 必须严格 = esearch 的代表基因,不能是"系统"。

### 2. 文本扫描:`_strip_html` + 连字符归一化
PubMed 摘要里有 `<sub>HR</sub>H2</sub>` 这种残留 HTML,直接 `re.sub(r"<[^>]+>", "", text)` 会**误删** `P < 0.05` 里的尖括号。修复:只剥以字母开头的真标签 `<[a-zA-Z][^>]*>`。
同时归一化连字符:基因匹配在 `text_norm = text.replace("-", "")` 上做,让 `HRH-4` / `DRD-2` 命中 `HRH4` / `DRD2`。

### 3. alias 3 处都用了
- **esearch 查询**:`build_query` 把 `common_aliases` OR 拼接进查询,扩大 hit
- **文本扫描**:`scan_mentions` 把 `common_aliases` 喂入,扩大 `mentioned_receptors_in_abstract` 命中
- **LLM prompt**:把 24 受体的 `alias_hint` 拼进 system prompt,让 LLM 知道 "H1 receptor" = HRH1
任何一处缺 alias,都会少命中 / 错识别。

### 4. v3:多受体论文 → JSON 数组(1 PMID → N entry)
旧 prompt 让 LLM 输出 1 个 JSON,DRD1-DRD2 异源二聚体论文只产出 1 条记录,DRD2 被吃掉。
v3 prompt 要求 LLM 输出 **JSON 数组**,每条对应一个 focal receptor(单受体论文也走数组,1 元素)。

### 5. v3:ligand ↔ receptor 强校验(抽完再校验)
不预过滤:不能"abstract 不含 canonical ligand 就跳过",否则全砍掉药理学论文。
抽完用白名单 `GENE_TO_CANONICAL_LIGAND[receptor_gene]` 校验:
- LLM 输出的 `ligand` ∈ {canonical, null} → 通过
- 否则 → `ligand_mismatch=true` + `ligand_mismatch_reason` + confidence 降档
论文只测 drug(haloperidol 等)时允许 `ligand=null, tested_compound="haloperidol"`。

### 6. v3:`tested_compound` 与 ligand 分离
LLM 看到"DRD1 paper"会无脑填 `ligand="dopamine"`,但论文可能实际测了 haloperidol(选择性 D2 拮抗剂)。这种隐性污染用 `tested_compound` 字段把 drug 单独放,与内源性 ligand 分开。

### 7. v3:`reasoning` 字段(思考过程)
参考实现 c6b PDF 强调"保存思考模型的思考过程做细致分析"。v3 每条 entry 加 `reasoning` 字段(≤ 80 词),记录 LLM 推断 focal 受体 / 选 evidence 句 / 判 ligand 的关键推理,供下游误差分析。

### 8. v2:literature 字段用 PubMed 源数据
v1 让 LLM 从 abstract 猜 journal/year/doi,导致 69% journal 名不一致。v2 改为 `literature` 字段直接从 PubMed efetch 的结构化数据填充,LLM 不再负责这部分。DOI 从 `PubmedData/ArticleIdList` 提取。

### 9. mismatch 自动标 review(不丢人工环节)
LLM 抽出的 `receptor_gene` 与 `query_receptor_gene` 不一致时,自动 `needs_human_review=true` + 降档。v3 还把 `ligand_mismatch` 也算进 review 触发条件。人工从结果里挑 high/medium/low 各 N 条 → 写 gold → 跑评估,形成闭环。

### 10. 断点续跑
两个主脚本都支持。每条成功处理后**立即写盘**(覆盖整个 JSON),中断后重跑自动跳过。中途可 Ctrl-C,下次接着跑。

### 11. 顺序 + 0.5s 限速
不并发。原因:Qwen 免费配额下并发会触发 429。本数据量级(121 条)顺序 14 分钟跑完,没必要并发。

### 12. (未来扩展)entry key 升到 (pmid, receptor_gene, ligand)
当前 entry key = (pmid, receptor_gene) 对 24 经典受体 + 7 经典配体够用(1:1 配对)。
未来加入非经典配体 / 非经典受体 / 同一受体多配体时,迁到 (pmid, receptor_gene, ligand) 配对更通用。
迁移路径:把当前每条 entry 拆成 N 条(每提及的 (receptor, ligand) 一条),`tested_compound` 自动成为"无 canonical ligand 时的配对载体"。

---

## 已知问题与下一步

### 已知
- **`needs_human_review` 占比**:v3 跑完后以 `_check_assignments.py` 输出为准。
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
