"""检查 query_receptor_gene 正确性 + low_confidence_query 标记准确性。"""
import json
import re
import sys
import openpyxl

sys.stdout.reconfigure(encoding="utf-8")

wb = openpyxl.load_workbook(
    "receptor_list_classic_neurotransmitter_gpcr.xlsx", data_only=True, read_only=True
)
ws = wb["included_receptors"]
rows = list(ws.iter_rows(values_only=True))
hdr = rows[0]
idx_gene = hdr.index("receptor_gene")
idx_system = hdr.index("neurotransmitter_system")
idx_name = hdr.index("receptor_name")
receptors = []
for r in rows[1:]:
    if r[idx_gene]:
        receptors.append({"gene": r[idx_gene], "system": r[idx_system], "name": r[idx_name]})
all_genes = [r["gene"] for r in receptors]
all_names = [r["name"] for r in receptors]

d = json.load(open("data/pubmed_test_set.json", encoding="utf-8"))
print(f"测试集 {len(d)} 条\n")


def find(text, genes, names):
    text = text or ""
    text_l = text.lower()
    g = [x for x in genes if re.search(rf"\b{re.escape(x)}\b", text, re.I)]
    n = [x for x in names if x and x.lower() in text_l]
    return g, n


ok, wrong, low_conf = 0, [], 0
for rec in d:
    assigned = rec.get("query_receptor_gene")
    combined = (rec.get("title") or "") + " " + (rec.get("abstract") or "")
    found_g, found_n = find(combined, all_genes, all_names)
    found = list(dict.fromkeys(found_g + found_n))
    if rec.get("low_confidence_query"):
        low_conf += 1
    if assigned in found:
        ok += 1
    else:
        wrong.append(
            {
                "pmid": rec["pmid"],
                "year": rec.get("year"),
                "system": rec.get("neurotransmitter_system"),
                "assigned": assigned,
                "true_genes": found,
                "low_conf": rec.get("low_confidence_query"),
                "title": (rec.get("title") or "")[:90],
            }
        )

print(f"=== 错配: {len(wrong)} / {len(d)} ({len(wrong)/len(d)*100:.0f}%) ===")
print(f"=== 正确: {ok} / {len(d)} ({ok/len(d)*100:.0f}%) ===")
print(f"=== low_confidence_query 标记: {low_conf} 条 ===\n")
for w in wrong:
    print(f"PMID {w['pmid']} ({w['year']}) | system={w['system']} | low_conf={w['low_conf']}")
    print(f"  分配: {w['assigned']}")
    print(f"  真实提到: {w['true_genes']}")
    print(f"  title: {w['title']}\n")

# 按系统统计
from collections import Counter
c = Counter(w["system"] for w in wrong)
print("=== 按系统错配分布 ===")
for k, v in sorted(c.items()):
    total_sys = sum(1 for r in d if r.get("neurotransmitter_system") == k)
    print(f"  {k:30s} {v}/{total_sys}")
