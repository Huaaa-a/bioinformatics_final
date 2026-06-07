"""
演示：更客观的 low 置信度判定，引入位置和词频规则
目的：减少 need_human_review 比例，把明显信息不足的标记为 discard
"""
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
DATA_PATH = REPO_ROOT / "data" / "pubmed_extracted.json"
TEST_SET_PATH = REPO_ROOT / "data" / "pubmed_test_set.json"

# 加载本地的测试集（里面有完整摘要）
with open(TEST_SET_PATH, "r", encoding="utf-8") as f:
    test_set = {d["pmid"]: d for d in json.load(f)}

# 加载抽取结果
with open(DATA_PATH, "r", encoding="utf-8") as f:
    extracted = json.load(f)

# 定义受体别名
ALIASES = {
    "DRD1": ["DRD1", "D1 receptor", "dopamine receptor D1"],
    "DRD2": ["DRD2", "D2 receptor", "dopamine receptor D2"],
    "DRD3": ["DRD3", "D3 receptor", "dopamine receptor D3"],
    "HTR1A": ["HTR1A", "5-HT1A", "serotonin 1A receptor"],
    "HTR2A": ["HTR2A", "5-HT2A", "serotonin 2A receptor"],
    "HTR2C": ["HTR2C", "5-HT2C", "serotonin 2C receptor"],
    "ADRB1": ["ADRB1", "β1-adrenergic receptor", "beta-1 adrenergic receptor"],
    "ADRB2": ["ADRB2", "β2-adrenergic receptor", "beta-2 adrenergic receptor"],
    "ADRA1A": ["ADRA1A", "α1A-adrenergic receptor"],
    "CHRM1": ["CHRM1", "M1 muscarinic receptor"],
    "CHRM4": ["CHRM4", "M4 muscarinic receptor"],
    "HRH1": ["HRH1", "H1 histamine receptor"],
    "HRH2": ["HRH2", "H2 histamine receptor"],
    "HRH3": ["HRH3", "H3 histamine receptor"],
    "GRM2": ["GRM2", "mGluR2", "metabotropic glutamate receptor 2"],
    "GRM5": ["GRM5", "mGluR5", "metabotropic glutamate receptor 5"],
    "GRM7": ["GRM7", "mGluR7", "metabotropic glutamate receptor 7"],
    "HTR7": ["HTR7", "5-HT7", "serotonin 7 receptor"],
}

BACKGROUND_RATIO = 0.3
MIN_WORD_COUNT_FOR_DISCARD = 3
MIN_FUNCTION_FIELDS_FOR_REVIEW = 1  # 至少有一个位置/细胞/通路/功能信息才值得审核


def analyze_entry(entry, test_data):
    pmid = entry["pmid"]
    receptor_gene = entry["receptor_gene"]
    abstract = test_data.get("abstract", "")
    confidence = entry["confidence"]
    needs_human_review = entry.get("needs_human_review", False)

    # 1. 位置分析
    targets = ALIASES.get(receptor_gene, [receptor_gene])
    positions = []
    for target in targets:
        for match in re.finditer(re.escape(target), abstract, re.IGNORECASE):
            positions.append(match.start())

    abstract_len = len(abstract)
    background_cutoff = int(abstract_len * BACKGROUND_RATIO) if abstract_len > 0 else 0

    in_background = any(p < background_cutoff for p in positions)
    in_result = any(p >= background_cutoff for p in positions)

    # 2. 词频分析
    mention_count = len(positions)

    # 3. 字段完整性分析
    has_location = bool(entry.get("location"))
    has_cell_type = bool(entry.get("cell_type"))
    has_pathway = bool(entry.get("downstream_pathway"))
    has_function = bool(entry.get("function"))
    num_function_fields = sum([has_location, has_cell_type, has_pathway, has_function])

    # 4. 客观 discard 判定
    # 判定条件：
    # a. 只在背景部分出现
    # b. 词频 <= 2
    # c. 没有任何功能相关字段
    discard = False
    discard_reasons = []

    if in_background and not in_result:
        discard = True
        discard_reasons.append("只在背景部分出现")
    if mention_count <= 2:
        discard = True
        discard_reasons.append(f"只出现 {mention_count} 次")
    if num_function_fields == 0:
        discard = True
        discard_reasons.append("没有位置/细胞/通路/功能信息")

    # 5. 优化后的需要审核判定
    # 如果不是 discard，且有部分信息但置信度低，才需要审核
    new_needs_human_review = needs_human_review and not discard

    return {
        "pmid": pmid,
        "receptor_gene": receptor_gene,
        "original_confidence": confidence,
        "original_needs_human_review": needs_human_review,
        "discard": discard,
        "discard_reasons": discard_reasons,
        "new_needs_human_review": new_needs_human_review,
        "in_background": in_background,
        "in_result": in_result,
        "mention_count": mention_count,
        "num_function_fields": num_function_fields,
        "positions": positions,
        "abstract_len": abstract_len,
    }


# 分析所有条目
results = []
for entry in extracted:
    pmid = entry["pmid"]
    test_data = test_set.get(pmid, {})
    result = analyze_entry(entry, test_data)
    results.append(result)

# 统计
original_need_review = sum(1 for r in results if r["original_needs_human_review"])
new_need_review = sum(1 for r in results if r["new_needs_human_review"])
discard_count = sum(1 for r in results if r["discard"])
total = len(results)

print("=" * 100)
print("客观 discard 规则演示")
print("=" * 100)

print(f"\n总条目数：{total}")
print(f"原始 need_human_review：{original_need_review} ({100*original_need_review/total:.1f}%)")
print(f"优化后 need_human_review：{new_need_review} ({100*new_need_review/total:.1f}%)")
print(f"标记为 discard（直接删除）：{discard_count} ({100*discard_count/total:.1f}%)")

# 按置信度统计
print("\n" + "=" * 100)
print("按置信度统计：")
print("=" * 100)

from collections import defaultdict
stats_by_conf = defaultdict(lambda: {"total": 0, "discard": 0, "original_review": 0, "new_review": 0})
for r in results:
    conf = r["original_confidence"]
    stats_by_conf[conf]["total"] += 1
    if r["discard"]:
        stats_by_conf[conf]["discard"] += 1
    if r["original_needs_human_review"]:
        stats_by_conf[conf]["original_review"] += 1
    if r["new_needs_human_review"]:
        stats_by_conf[conf]["new_review"] += 1

for conf in ["high", "medium", "low"]:
    s = stats_by_conf[conf]
    print(f"\n{conf.upper()} ({s['total']}):")
    print(f"  discard: {s['discard']} ({100*s['discard']/s['total']:.1f}%)")
    print(f"  original_need_review: {s['original_review']}")
    print(f"  new_need_review: {s['new_review']}")

# 示例
print("\n" + "=" * 100)
print("示例（discard 的条目）：")
print("=" * 100)

discard_examples = [r for r in results if r["discard"]][:5]
for i, r in enumerate(discard_examples, 1):
    print(f"\n{i}. PMID {r['pmid']} | {r['receptor_gene']}")
    print(f"   置信度: {r['original_confidence']}")
    print(f"   discard原因: {', '.join(r['discard_reasons'])}")
    print(f"   出现次数: {r['mention_count']}")
    print(f"   位置: {'background' if r['in_background'] and not r['in_result'] else 'both' if r['in_background'] and r['in_result'] else 'result'}")

print("\n" + "=" * 100)
print("示例（仍然需要审核的条目）：")
print("=" * 100)

review_examples = [r for r in results if r["new_needs_human_review"]][:5]
for i, r in enumerate(review_examples, 1):
    print(f"\n{i}. PMID {r['pmid']} | {r['receptor_gene']}")
    print(f"   置信度: {r['original_confidence']}")
    print(f"   出现次数: {r['mention_count']}")
    print(f"   功能字段数: {r['num_function_fields']}")

# 保存结果
OUTPUT_PATH = REPO_ROOT / "data" / "objective_discard_analysis.json"
with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

print(f"\n详细结果已保存至：{OUTPUT_PATH}")
