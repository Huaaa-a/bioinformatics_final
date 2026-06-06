
"""
生成提及表：基于文本扫描，每个被提到的受体一条记录
不需要 LLM，完全从 pubmed_test_set.json 的 mentioned_receptors_in_abstract 生成
"""

import json
import re
from pathlib import Path
from typing import List, Dict, Any
import openpyxl

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = REPO_ROOT / "data" / "pubmed_test_set.json"
DEFAULT_OUTPUT = REPO_ROOT / "data" / "mentioned_receptors.json"

# 从 fetch_pubmed_test_set.py 复制的 receptor 元数据加载
def load_xlsx_metadata(xlsx_path: Path):
    if not xlsx_path.exists():
        return {}, set(), {}
    wb = openpyxl.load_workbook(xlsx_path, data_only=True, read_only=True)
    ws = wb["included_receptors"]
    rows = ws.iter_rows(values_only=True)
    header = next(rows)
    idx = {name: i for i, name in enumerate(header)}
    family_map: Dict[str, str] = {}
    valid_genes: set[str] = set()
    aliases_map: Dict[str, List[str]] = {}
    for row in rows:
        if not row or row[idx["receptor_gene"]] is None:
            continue
        gene = str(row[idx["receptor_gene"]]).strip().upper()
        valid_genes.add(gene)
        family = str(row[idx["receptor_family"]] or "").strip()
        if gene and family:
            family_map[gene] = family
        common = str(row[idx.get("common_aliases", -1)] or "").strip() if "common_aliases" in idx else ""
        if common:
            aliases_map[gene] = [a.strip() for a in common.split(";") if a.strip()]
    wb.close()
    return family_map, valid_genes, aliases_map

# 从 fetch_pubmed_test_set.py 复制的 _strip_html
def _strip_html(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"<[a-zA-Z][^>]*>", "", text)

# 从 abstract 中提取提到某个受体的 snippet
def extract_snippet(abstract: str, gene: str, receptor_name: str = None, aliases: List[str] = None) -> str:
    """
    从 abstract 中提取提到该基因/受体的那一句话
    返回最相关的一句话（不超过 100 字符）
    """
    abstract_clean = _strip_html(abstract)
    sentences = re.split(r'[.!?]+', abstract_clean)
    
    keywords = [gene]
    if receptor_name:
        keywords.append(receptor_name)
    if aliases:
        keywords.extend(aliases)
    
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        # 检查是否包含任何关键词
        found = False
        for kw in keywords:
            if kw.lower() in sentence.lower():
                found = True
                break
        if found:
            # 截断太长的句子
            if len(sentence) > 150:
                sentence = sentence[:147] + "..."
            return sentence
    return None

def generate_mentioned_list(input_path: Path = DEFAULT_INPUT, output_path: Path = DEFAULT_OUTPUT):
    print(f"Loading {input_path}...")
    with open(input_path, "r", encoding="utf-8") as f:
        test_set = json.load(f)
    
    family_map, _, aliases_map = load_xlsx_metadata(REPO_ROOT / "receptor_list_classic_neurotransmitter_gpcr.xlsx")
    
    mentioned_entries = []
    for record in test_set:
        pmid = record.get("pmid")
        title = record.get("title", "")
        abstract = record.get("abstract", "")
        mentioned_genes = record.get("mentioned_receptors_in_abstract", [])
        mentioned_names = record.get("mentioned_receptor_names", [])
        mentioned_ligands = record.get("mentioned_ligands_in_abstract", [])
        query_gene = record.get("query_receptor_gene")
        
        # 对每个提到的基因生成一条记录
        for i, gene in enumerate(mentioned_genes):
            receptor_name = mentioned_names[i] if i < len(mentioned_names) else None
            aliases = aliases_map.get(gene, [])
            snippet = extract_snippet(abstract, gene, receptor_name, aliases)
            
            entry = {
                "pmid": pmid,
                "receptor_gene": gene,
                "receptor_name": receptor_name,
                "receptor_family": family_map.get(gene),
                "mentioned_ligands": mentioned_ligands,
                "snippet": snippet,
                "is_query_receptor": (gene == query_gene),
                "source": {
                    "title": title,
                    "year": record.get("year"),
                    "journal": record.get("journal"),
                    "doi": record.get("doi")
                }
            }
            mentioned_entries.append(entry)
    
    print(f"Generated {len(mentioned_entries)} mentioned receptor entries")
    print(f"Saving to {output_path}...")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(mentioned_entries, f, ensure_ascii=False, indent=2)
    
    print(f"Saved {len(mentioned_entries)} entries")
    return mentioned_entries

if __name__ == "__main__":
    generate_mentioned_list()

