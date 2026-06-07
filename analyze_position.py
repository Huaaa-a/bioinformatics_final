"""
分析：受体在摘要中的出现位置 vs 当前置信度
"""
import json
import re
from pathlib import Path
from Bio import Entrez
from dotenv import load_dotenv
import os

REPO_ROOT = Path(__file__).resolve().parent
DATA_PATH = REPO_ROOT / "data" / "pubmed_extracted.json"
TEST_SET_PATH = REPO_ROOT / "data" / "pubmed_test_set.json"

# 加载本地的测试集（里面有完整摘要）
with open(TEST_SET_PATH, "r", encoding="utf-8") as f:
    test_set = {d["pmid"]: d for d in json.load(f)}

# 加载抽取结果
with open(DATA_PATH, "r", encoding="utf-8") as f:
    extracted = json.load(f)

print("=" * 100)
print("分析：受体在摘要中的出现位置 vs 当前置信度")
print("=" * 100)

# 定义位置分段比例
BACKGROUND_RATIO = 0.3
RESULT_RATIO = 0.7

stats = []
conf_pos_count = {
    "high": {"background_only": 0, "result_only": 0, "both": 0},
    "medium": {"background_only": 0, "result_only": 0, "both": 0},
    "low": {"background_only": 0, "result_only": 0, "both": 0},
}

for entry in extracted:
    pmid = entry["pmid"]
    test = test_set.get(pmid, {})
    abstract = test.get("abstract", "")
    receptor_gene = entry["receptor_gene"]
    confidence = entry["confidence"]

    if not abstract:
        continue

    # 查找受体基因和常见别名在摘要中的位置
    aliases = {
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

    targets = aliases.get(receptor_gene, [receptor_gene])
    positions = []
    for target in targets:
        for match in re.finditer(re.escape(target), abstract, re.IGNORECASE):
            positions.append(match.start())

    if not positions:
        continue

    # 计算位置分段
    abstract_len = len(abstract)
    background_cutoff = int(abstract_len * BACKGROUND_RATIO)
    result_cutoff = int(abstract_len * RESULT_RATIO)

    # 判断出现区域
    in_background = any(p < background_cutoff for p in positions)
    in_result = any(p > background_cutoff for p in positions)

    if in_background and in_result:
        category = "both"
    elif in_background:
        category = "background_only"
    else:
        category = "result_only"

    # 统计
    conf_pos_count[confidence][category] += 1
    stats.append({
        "pmid": pmid,
        "receptor_gene": receptor_gene,
        "confidence": confidence,
        "category": category,
        "abstract_len": abstract_len,
        "positions": positions,
    })

# 打印统计
print("\n置信度分布 vs 出现位置：")
for conf in ["high", "medium", "low"]:
    total = sum(conf_pos_count[conf].values())
    if total == 0:
        continue
    print(f"\n{conf.upper()} (n={total})")
    for cat in ["background_only", "result_only", "both"]:
        cnt = conf_pos_count[conf][cat]
        pct = 100 * cnt / total
        print(f"  {cat}: {cnt} ({pct:.1f}%)")

# 打印几个例子
print("\n" + "=" * 100)
print("例子：")
print("=" * 100)

for conf in ["high", "medium", "low"]:
    examples = [s for s in stats if s["confidence"] == conf][:3]
    print(f"\n{conf.upper()}：")
    for ex in examples:
        print(f"  PMID {ex['pmid']} | {ex['receptor_gene']} | {ex['category']} | 位置={ex['positions']} | 摘要长度={ex['abstract_len']}")
