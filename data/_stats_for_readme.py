"""为 README 生成数据快照(纯文本表格,方便人眼读)。"""
import json
from collections import Counter

src = json.load(open('data/pubmed_test_set.json', encoding='utf-8'))
ext = json.load(open('data/pubmed_extracted.json', encoding='utf-8'))

print('## 抓取结果(121 条摘要)')
print()
print(f'**总文献数**:{len(src)} (唯一 PMID)')
print()
print('### 按神经递质系统')
sys_count = Counter(r['neurotransmitter_system'] for r in src)
for k, v in sys_count.most_common():
    print(f'- {k}: {v}')

print()
print('### 按受体基因(query_receptor_gene)')
gene_count = Counter(r['query_receptor_gene'] for r in src)
for k, v in gene_count.most_common():
    print(f'- {k}: {v}')

print()
print(f'### 来源 / 置信度')
n_review = sum(1 for r in src if r.get('low_confidence_query'))
print(f'- per-receptor 严格查询(query_receptor_gene = 搜索词)')
print(f'- 审计错配率(纯正则):1%(1/86 牡蛎 Cg5-HTR1A-like)')
print(f'- low_confidence_query=True:{n_review}')

print()
print('---')
print()
print('## 字段抽取结果(121 条 × 14 字段)')
print()
print('### confidence 分布')
cc = Counter(r['confidence'] for r in ext)
n = len(ext)
print(f'| confidence | 数量 | 占比 |')
print(f'|---|---|---|')
for k in ['high', 'medium', 'low']:
    v = cc.get(k, 0)
    print(f'| {k} | {v} | {v/n*100:.0f}% |')
print(f'| **合计** | **{n}** | **100%** |')

print()
print('### needs_human_review')
n_review = sum(1 for r in ext if r.get('needs_human_review'))
n_mismatch = sum(1 for r in ext if r.get('receptor_gene_mismatch'))
n_api = sum(1 for r in ext if r.get('extraction_meta', {}).get('api_error'))
n_parse = sum(1 for r in ext if r.get('extraction_meta', {}).get('parse_error'))
print(f'- needs_human_review:{n_review}/{n} ({n_review/n*100:.0f}%)')
print(f'  - receptor_gene_mismatch 触发:{n_mismatch}')
print(f'  - confidence=low 触发:{n_review - n_mismatch}')
print(f'- parse_error:{n_parse} | api_error:{n_api}')

print()
print('### 字段非空率')
fields = [
    ('source', '文献类型'),
    ('receptor', '受体全名'),
    ('receptor_gene', '基因符号'),
    ('receptor_family', '受体家族'),
    ('ligand', '配体'),
    ('location', '位置'),
    ('cell_type', '细胞类型'),
    ('downstream_pathway', '下游通路'),
    ('function', '功能'),
    ('species', '物种'),
    ('evidence', '证据句'),
]
print(f'| 字段 | 说明 | 非空率 |')
print(f'|---|---|---|')
for f, zh in fields:
    nn = sum(1 for r in ext if r.get(f))
    print(f'| `{f}` | {zh} | {nn}/{n} ({nn/n*100:.0f}%) |')

print()
print('### ligand 分布')
lig = Counter(r.get('ligand') for r in ext)
for k, v in lig.most_common():
    print(f'- {k or "(null)"}: {v}')

print()
print('### source 分布')
src_count = Counter(r.get('source') for r in ext)
for k, v in src_count.most_common():
    print(f'- {k or "(null)"}: {v}')
