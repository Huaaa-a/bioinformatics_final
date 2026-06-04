# 审计问题全面修复计划

## Summary

基于独立审计发现的 13 个问题（3 严重 + 5 中等 + 5 轻微），全面修复代码、数据与文档。核心改动：让 `literature` 元数据从 PubMed 源数据直接填充而非 LLM 猜测；增加 `receptor_family` 白名单校验；清理 HTML 残留；修正 README 数据错误；修复多个代码缺陷。修复后用 `--force-rerun` 重跑全部 121 条。

## 关于 Qwen 模型选择

当前使用 `qwen-plus`。审计发现的主要幻觉问题（journal 69% 不一致）本质上是**代码设计问题**（让 LLM 猜 PubMed 已有的结构化数据），而非模型能力问题——即使 GPT-4 也会从 abstract 中猜错 journal。修复方案是让 `literature` 字段直接用 PubMed 源数据，LLM 不再负责这部分。

对于其他字段（receptor_family、receptor_gene 等），`qwen-plus` 的表现已经足够好（parse_error=0, api_error=0）。升级到 `qwen-max` 会增加约 3-5 倍成本，改善有限。**建议保持 `qwen-plus` 不变**，通过代码层面的白名单校验和 fallback 机制来保证数据质量，而非依赖更贵的模型。

## Current State Analysis

### 严重问题
1. **journal/year/doi 幻觉**：84/121 journal 不一致，37/121 year 不一致，0/121 DOI 有值。`normalize_record()` 中 `literature` 字段优先取 LLM 输出，PubMed 源数据只作 fallback。
2. **README 数据快照错误**：GABBR 写"6 条"实际 3 条；缺 HRH3；审计数字写死。
3. **receptor_family 31 种不同值**：Prompt 未约束取值范围，LLM 自由发挥。

### 中等问题
4. **.env 配置覆盖**：两个 example copy 到同一 .env，后者覆盖前者。
5. **_stats_for_readme.py 硬编码审计结论**。
6. **_check_assignments.py 硬编码相对路径**。
7. **evidence 超 30 词限制**：10 条超标。
8. **cell_type 残留 HTML 标签**。

### 轻微问题
9. **receptor_gene 出现荒谬值**（如 "GPCROME (377 MANUALLY VALIDATED GPCR GENES)"）。
10. **source 字段判定不准**：应从 PubMed PublicationTypeList 判定。
11. **二审逻辑排除 mismatch 降档记录**。
12. **pandas 在 requirements 但未使用**。
13. **跨受体合并逻辑 bug**。
14. **common_aliases 完全未使用**：xlsx 中 24 个受体的别名（如 "D1R"、"M1 mAChR"、"mGluR1"、"α1A-adrenoceptor"）被加载到 Receptor dataclass 但从未传入 `scan_mentions()`，导致用别名引用受体的论文可能被误标 `low_confidence_query`。LLM prompt 也未提供别名信息。

## Proposed Changes

### Change 1: 修复 literature 幻觉（问题 #1）

**文件**: `scripts/extract_fields_qwen.py`

**What**: 让 `literature` 对象的 `journal`/`year`/`title` 强制使用 PubMed 源数据，`doi` 从 PubMed XML 的 `ArticleId` 中提取（需在 fetch 阶段保存），LLM 输出只作为这些字段缺失时的 fallback。

**Why**: LLM 从 abstract 无法准确推断期刊名（69% 不一致），这些信息 PubMed 已提供。

**How**:
1. 在 `fetch_pubmed_test_set.py` 的 `_parse_articles()` 中，增加从 PubMed XML 提取 `doi` 的逻辑（`ArticleIdList` 中 `IdType="doi"` 的条目），存入源记录的 `doi` 字段。
2. 在 `extract_fields_qwen.py` 的 `normalize_record()` 中，修改 `literature_out` 构建逻辑：
   - `journal`: 强制用 `source_record.get("journal")`，不再用 LLM 输出
   - `year`: 强制用 `source_record.get("year")`
   - `title`: 强制用 `source_record.get("title")`
   - `doi`: 优先用 `source_record.get("doi")`（新增字段），LLM 输出作 fallback
   - `pmid`: 保持现有逻辑

### Change 2: 增加 receptor_family 白名单校验（问题 #3）

**文件**: `scripts/extract_fields_qwen.py`

**What**: 从 xlsx 读取 24 个受体的标准 `receptor_family` 值，建立映射表；LLM 输出不在映射中时，按 `receptor_gene` 查表 fallback。

**Why**: 当前 31 种不同值，如 "GPCR"、"glutamate"、"cholinergic" 等明显不规范。

**How**:
1. 在 `extract_fields_qwen.py` 顶部新增函数 `load_family_mapping(xlsx_path)` → `dict[str, str]`，从 xlsx 读取 `receptor_gene → receptor_family` 映射。
2. 在 `normalize_record()` 中，如果 LLM 输出的 `receptor_family` 不在映射的 values 中，则按 `receptor_gene` 查表替换；查不到则保留 LLM 输出但降档。

### Change 3: 增加 receptor_gene 白名单校验（问题 #9）

**文件**: `scripts/extract_fields_qwen.py`

**What**: 加载 24 个标准基因符号列表，LLM 输出的 `receptor_gene` 不在列表中时，fallback 到 `query_receptor_gene` 并标 mismatch。

**Why**: 出现 "GPCROME (377...)"、"HRH2A"（不存在）等荒谬值。

**How**:
1. 在 `normalize_record()` 中，增加参数 `valid_genes: set[str]`。
2. 如果 `receptor_gene` 不在 `valid_genes` 中，设 `receptor_gene = query_gene`（如果 query_gene 在 valid_genes 中），标 `mismatch=True`。

### Change 4: 修复 source 字段判定（问题 #10）

**文件**: `scripts/fetch_pubmed_test_set.py` + `scripts/extract_fields_qwen.py`

**What**: 在 fetch 阶段从 PubMed XML 的 `PublicationTypeList` 提取文献类型，存入源记录；extract 阶段优先使用此值。

**Why**: 查询已排除 Review，但 LLM 仍标了 3 条 review；有些数据库类论文被标为 original_research。

**How**:
1. 在 `_parse_articles()` 中，提取 `PublicationTypeList`，判断是否含 "Review" 或 "Meta-Analysis"，存入 `pub_types` 字段。
2. 在 `normalize_record()` 中，如果 `source_record` 有 `pub_types`，用它判定 `source`（含 Review → "review"，否则 → "original_research"），不再用 LLM 输出。

### Change 5: 修复 evidence 超长 + HTML 清理（问题 #7, #8）

**文件**: `scripts/extract_fields_qwen.py`

**What**: 加强 evidence 截断逻辑；对所有 LLM 输出的文本字段做 HTML 标签清理。

**Why**: 10 条 evidence 超 30 词；cell_type 残留 `<sub>` 标签。

**How**:
1. 新增 `_strip_html()` 函数（复用 fetch 脚本的逻辑）。
2. 在 `normalize_record()` 中，对 `evidence`、`cell_type`、`location`、`downstream_pathway`、`function`、`species`、`receptor` 等所有文本字段调用 `_strip_html()`。
3. 修改 evidence 截断：如果 > 30 词，尝试在最近句号/分号处截断；找不到则暴力截断。

### Change 6: 扩大二审范围（问题 #11）

**文件**: `scripts/extract_fields_qwen.py`

**What**: 二审目标从 `confidence=low` 扩展到包含 `receptor_gene_mismatch=True` 的记录。

**Why**: mismatch 记录恰恰最需要二审，当前被排除在外。

**How**: 修改 `main()` 中 `review_targets` 的过滤条件，增加 `or by_pmid[r["pmid"]].get("receptor_gene_mismatch")`。

### Change 7: 修复跨受体合并 bug（问题 #13）

**文件**: `scripts/fetch_pubmed_test_set.py`

**What**: 修复第 419 行逻辑：应检查**原记录**的 `query_receptor_gene` 是否在合并后的基因列表中，而非新记录的。

**Why**: 当前逻辑检查的是新记录的 query，但原记录的 query 才是需要验证的。

**How**:
```python
# 旧：
if r["query_receptor_gene"] in merged_genes and existing.get("low_confidence_query"):
# 新：
if existing.get("query_receptor_gene") in merged_genes and existing.get("low_confidence_query"):
```

### Change 8: 启用 common_aliases 用于文本扫描和 LLM prompt（问题 #14）

**文件**: `scripts/fetch_pubmed_test_set.py` + `scripts/extract_fields_qwen.py`

**What**: 将 xlsx 中的 `common_aliases` 传入 `scan_mentions()`，同时将别名信息注入 LLM prompt。

**Why**: 当前 `common_aliases` 被加载但从未使用。别名如 "D1R"、"M1 mAChR"、"mGluR1"、"α1A-adrenoceptor" 在论文中很常见，忽略它们会导致文本扫描漏检和 `low_confidence_query` 误标。

**How**:
1. 在 `fetch_pubmed_test_set.py` 的 `scan_mentions()` 中，新增参数 `all_aliases: dict[str, list[str]]`（gene → [alias1, alias2, ...]），对每个别名做子串匹配（与 receptor_name 同逻辑），匹配到的别名加入 `mentioned_receptor_names`。
2. 在 `main()` 中，构建 `all_aliases = {r.receptor_gene: [a.strip() for a in r.common_aliases.split(";")] if r.common_aliases else [] for r in receptors}`，传入 `fetch_for_receptor()` 和 `scan_mentions()`。
3. 在 `extract_fields_qwen.py` 的 `SYSTEM_PROMPT` 中，增加一段别名提示：列出 24 个受体及其常见别名，帮助 LLM 识别。

### Change 9: 修复 .env 配置覆盖（问题 #4）

**文件**: `scripts/.env.example` + `scripts/.env.qwen.example` → 合并为一个 `scripts/.env.example`

**What**: 合并两个 example 文件为一个，包含所有配置项。

**Why**: 两个文件 copy 到同一 .env 会互相覆盖。

**How**:
1. 创建新的 `scripts/.env.example`，包含 ENTREZ_EMAIL、ENTREZ_API_KEY、QWEN_API_KEY、QWEN_MODEL 四个字段。
2. 删除 `scripts/.env.qwen.example`。
3. 更新 README 中的快速开始指令。

### Change 10: 修复 _check_assignments.py 硬编码路径（问题 #6）

**文件**: `data/_check_assignments.py`

**What**: 改用 `Path(__file__)` 相对路径，与主脚本一致。

**How**: 用 `REPO_ROOT = Path(__file__).resolve().parent.parent` 替代硬编码路径。

### Change 11: 修复 _stats_for_readme.py 硬编码审计结论（问题 #5）

**文件**: `data/_stats_for_readme.py`

**What**: 删除写死的审计结论，改为动态计算或标注为"需手动更新"。

**How**: 移除第 27 行硬编码的 `审计错配率(纯正则):1%(1/86 ...)`，改为运行 `_check_assignments.py` 动态获取，或改为注释说明。

### Change 12: 移除未使用的 pandas 依赖（问题 #12）

**文件**: `requirements.txt`

**What**: 移除 `pandas>=2.0`。

**Why**: 两个主脚本均未 import pandas。

### Change 13: 修正 README 数据快照（问题 #2）

**文件**: `README.md`

**What**: 修正 GABBR 数量（6→3）、补充 HRH3（2 hits 但 0 unique entries）、移除写死的审计数字、更新数据快照为重跑后的实际值。

**How**: 重跑数据后，用 `_stats_for_readme.py` 重新生成快照，手动更新 README。

### Change 14: 重跑全部数据

**What**: 修改完代码后，依次执行：
1. `python scripts/fetch_pubmed_test_set.py`（重新获取含 doi 的源数据）
2. `python scripts/extract_fields_qwen.py --force-rerun`（重跑全部 121 条）
3. `python data/_check_assignments.py`（验证）
4. 用 `_stats_for_readme.py` 生成新快照，更新 README

## Assumptions & Decisions

- **模型保持 qwen-plus**：journal 幻觉是代码设计问题而非模型问题，通过结构化修复解决。
- **literature 字段完全由 PubMed 源数据填充**：LLM 不再负责 journal/year/title/doi，只负责业务字段。
- **receptor_family 白名单来自 xlsx**：24 个标准值，LLM 输出不在白名单中则 fallback。
- **source 字段优先用 PubMed PublicationTypeList**：LLM 输出仅作 fallback。
- **重跑后 README 数据快照需手动更新**：因为数字会变。

## Verification Steps

1. 重跑后检查 `journal` 一致率：应从 31% → 100%
2. 重跑后检查 `year` 一致率：应从 69% → 100%
3. 重跑后检查 `doi` 非空率：应从 0% → 显著提升
4. 检查 `receptor_family` 去重数：应从 31 → ≤ 24
5. 检查 `evidence` 超 30 词数：应从 10 → 0
6. 检查 `cell_type` 中是否还有 HTML 标签
7. 运行 `_check_assignments.py` 验证错配率
8. 对比重跑前后 confidence 分布变化
9. 验证 `common_aliases` 扫描生效：检查 `low_confidence_query` 数量是否下降（当前 4 条，启用别名后应减少）
10. 验证 `receptor_gene` 无荒谬值（白名单校验生效）
