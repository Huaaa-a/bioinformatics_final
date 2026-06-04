import json
from collections import Counter

e = json.load(open('data/pubmed_extracted.json', encoding='utf-8'))
d = json.load(open('data/pubmed_test_set.json', encoding='utf-8'))

src_by_pmid = {r['pmid']: r for r in d}

journal_diff = 0
journal_hallucination_examples = []
year_diff = 0
title_diff = 0
doi_filled = 0

for ext in e:
    pmid = ext['pmid']
    src = src_by_pmid.get(pmid)
    if not src:
        continue
    ext_lit = ext.get('literature', {})
    # journal
    if (ext_lit.get('journal') or '') != (src.get('journal') or ''):
        journal_diff += 1
        if len(journal_hallucination_examples) < 8:
            journal_hallucination_examples.append((pmid, src.get('journal'), ext_lit.get('journal')))
    if (ext_lit.get('year') or '') != (src.get('year') or ''):
        year_diff += 1
    if (ext_lit.get('title') or '') != (src.get('title') or ''):
        title_diff += 1
    if ext_lit.get('doi'):
        doi_filled += 1

print(f'Journal different: {journal_diff}/{len(e)}')
print(f'Year different: {year_diff}/{len(e)}')
print(f'Title different: {title_diff}/{len(e)}')
print(f'DOI filled: {doi_filled}/{len(e)}')
print()
print('Sample journal hallucinations:')
for pmid, src_j, ext_j in journal_hallucination_examples:
    print(f'  pmid={pmid}')
    print(f'    src: {src_j!r}')
    print(f'    LLM: {ext_j!r}')

# source 分布
print()
print('source 分布:', Counter(r.get('source') for r in e))

# 找出 evidence 包含 markdown 围栏的(说明有 json 解析问题)
print()
print('evidence 异常检查:')
for r in e:
    ev = r.get('evidence') or ''
    if '```' in ev or '{' in ev or '}' in ev:
        print(f'  pmid={r.get("pmid")}: ev[:80]={ev[:80]!r}')
        break

# 检查 30 词限制
print()
over_30 = [r for r in e if r.get('evidence') and len(r['evidence'].split()) > 30]
print(f'evidence > 30 词: {len(over_30)} 条')

# 看错误条目
print()
parse_err = sum(1 for r in e if r.get('extraction_meta', {}).get('parse_error'))
api_err = sum(1 for r in e if r.get('extraction_meta', {}).get('api_error'))
print(f'parse_error={parse_err} | api_error={api_err}')

# prompt_version
print()
print('prompt_version:', Counter(r.get('extraction_meta', {}).get('prompt_version') for r in e))

# mismatch 涉及的所有不同 LLM gene 形式
print()
print('mismatch 的 LLM receptor_gene 类型:')
mismatches = Counter()
for r in e:
    if r.get('receptor_gene_mismatch'):
        mismatches[r.get('receptor_gene')] += 1
for k, v in mismatches.most_common():
    print(f'  {k}: {v}')
