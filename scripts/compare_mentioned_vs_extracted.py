
"""
对比提及表和 LLM 抽取表的差异
统计：
1. 总提及数 vs 总抽取数
2. 哪些是提及了但没抽取的（漏抽）
3. 哪些是抽取了但没提及的（幻觉？）
4. 漏抽最多的受体
"""

import json
from pathlib import Path
from collections import defaultdict

REPO_ROOT = Path(__file__).resolve().parent.parent
MENTIONED_PATH = REPO_ROOT / "data" / "mentioned_receptors.json"
EXTRACTED_PATH = REPO_ROOT / "data" / "pubmed_extracted.json"

def load_json(path):
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def main():
    print("Loading data...")
    mentioned = load_json(MENTIONED_PATH)
    extracted = load_json(EXTRACTED_PATH)
    
    # 过滤掉 API 错误和解析错误的 entry
    extracted_valid = []
    for e in extracted:
        if not e.get("api_error") and not e.get("parse_error") and e.get("receptor_gene"):
            extracted_valid.append(e)
    
    print(f"Total mentioned entries: {len(mentioned)}")
    print(f"Total valid extracted entries: {len(extracted_valid)}")
    
    # 构建索引：(pmid, receptor_gene) -> entry
    mentioned_idx = {}
    for m in mentioned:
        key = (m["pmid"], m["receptor_gene"])
        mentioned_idx[key] = m
    
    extracted_idx = {}
    for e in extracted_valid:
        key = (e["pmid"], e["receptor_gene"])
        extracted_idx[key] = e
    
    # 统计
    common = []
    missed = []
    extra = []
    
    # 检查漏抽（提及了但没抽取）
    for key in mentioned_idx:
        if key not in extracted_idx:
            missed.append(mentioned_idx[key])
        else:
            common.append((mentioned_idx[key], extracted_idx[key]))
    
    # 检查幻觉（抽取了但没提及）
    for key in extracted_idx:
        if key not in mentioned_idx:
            extra.append(extracted_idx[key])
    
    print(f"\n===== 统计结果 =====")
    print(f"共同匹配: {len(common)} (提及且抽取)")
    print(f"漏抽: {len(missed)} (提及但未抽取)")
    print(f"疑似幻觉: {len(extra)} (抽取但未提及)")
    
    # 漏抽统计（按受体）
    print(f"\n===== 漏抽最多的受体 =====")
    missed_by_gene = defaultdict(int)
    for m in missed:
        missed_by_gene[m["receptor_gene"]] += 1
    sorted_genes = sorted(missed_by_gene.items(), key=lambda x: -x[1])
    for gene, count in sorted_genes[:10]:
        print(f"{gene}: {count}")
    
    # 保存详细结果
    results = {
        "common": common,
        "missed": missed,
        "extra": extra
    }
    
    output_path = REPO_ROOT / "data" / "comparison_results.json"
    print(f"\nSaving detailed results to {output_path}...")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    # 打印几个漏抽的例子
    print(f"\n===== 漏抽例子 (前5个) =====")
    for i, m in enumerate(missed[:5]):
        print(f"{i+1}. PMID {m['pmid']}, {m['receptor_gene']}")
        print(f"   Title: {m['source']['title']}")
        if m['snippet']:
            print(f"   Snippet: {m['snippet']}")
        print()

if __name__ == "__main__":
    main()

