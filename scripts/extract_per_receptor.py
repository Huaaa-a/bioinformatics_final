
"""
改进版：逐个受体提取
1. 先用改进的文本扫描找到所有提及的受体
2. 对每个受体单独调用LLM抽取
3. 合并结果
"""
import json
import re
import time
from pathlib import Path
import openpyxl

import os
from dotenv import load_dotenv
from openai import OpenAI

# ---------- 配置 ----------
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
PUBMED_DATA_PATH = DATA_DIR / "pubmed_test_set.json"
OUTPUT_PATH = DATA_DIR / "pubmed_extracted_v3.json"
ENV_PATH = REPO_ROOT / "scripts" / ".env"
RECEPTOR_XLSX = REPO_ROOT / "receptor_list_classic_neurotransmitter_gpcr.xlsx"

# ---------- 加载受体信息 ----------
def load_receptor_metadata(xlsx_path):
    """加载受体列表，包括基因符号、别名、家族、配体等"""
    if not xlsx_path.exists():
        return {}, {}, {}
    
    wb = openpyxl.load_workbook(xlsx_path, data_only=True, read_only=True)
    ws = wb["included_receptors"]
    rows = ws.iter_rows(values_only=True)
    header = next(rows)
    idx = {name: i for i, name in enumerate(header)}
    
    gene_to_family = {}
    gene_to_aliases = {}
    gene_to_ligand = {}
    
    for row in rows:
        if not row or row[idx["receptor_gene"]] is None:
            continue
        
        gene = str(row[idx["receptor_gene"]]).strip().upper()
        family = str(row[idx["receptor_family"]] or "").strip()
        aliases_str = str(row[idx.get("common_aliases", -1)] or "").strip()
        ligand = str(row[idx.get("canonical_ligand", -1)] or "").strip()
        
        gene_to_family[gene] = family
        gene_to_ligand[gene] = ligand
        
        aliases = []
        if aliases_str:
            for a in aliases_str.split(";"):
                a = a.strip()
                if a:
                    aliases.append(a)
        # 添加一些常见的缩写
        if gene == "DRD1":
            aliases.extend(["D1R", "D1 receptor"])
        elif gene == "DRD2":
            aliases.extend(["D2R", "D2 receptor"])
        elif gene.startswith("HTR"):
            # HTR1A → 5-HT1A
            num = gene[3:]  # "1A"
            aliases.append(f"5-HT{num}")
            aliases.append(f"5-HT{num} receptor")
        
        gene_to_aliases[gene] = aliases
    
    wb.close()
    return gene_to_family, gene_to_aliases, gene_to_ligand

GENE_TO_FAMILY, GENE_TO_ALIASES, GENE_TO_CANONICAL_LIGAND = load_receptor_metadata(RECEPTOR_XLSX)
VALID_GENES = set(GENE_TO_FAMILY.keys())

# ---------- 改进的文本扫描 ----------
def improved_scan_mentions(title, abstract):
    """改进的文本扫描：识别基因符号、别名、常见缩写"""
    combined = f"{title} {abstract}".lower()
    combined_no_html = re.sub(r"&lt;[a-zA-Z][^&gt;]*&gt;", "", combined)
    # 连字符归一化
    combined_norm = re.sub(r"-", "", combined_no_html)
    
    found_genes = set()
    
    for gene in VALID_GENES:
        # 1. 直接匹配基因符号
        if re.search(r"\b" + re.escape(gene.lower()) + r"\b", combined_norm):
            found_genes.add(gene)
            continue
        
        # 2. 匹配别名
        aliases = GENE_TO_ALIASES.get(gene, [])
        for alias in aliases:
            if alias.lower() in combined_no_html:
                found_genes.add(gene)
                break
    
    return sorted(found_genes)

# ---------- 针对单个受体的Prompt ----------
SYSTEM_PROMPT_SINGLE = """You are a biomedical knowledge extractor for a neurotransmitter GPCR database.
Your task: EXTRACT INFORMATION ONLY ABOUT THIS SPECIFIC RECEPTOR: {target_receptor_gene}
IGNORE ALL OTHER RECEPTORS MENTIONED IN THE PAPER!

Output ONE JSON OBJECT (not array) with exactly these 17 fields:
pmid, source, receptor, receptor_gene, receptor_family, ligand, location, cell_type, downstream_pathway, 
function, species, literature, evidence, confidence, reasoning, tested_compound.

Rules:
- receptor_gene = {target_receptor_gene} (exactly this, no other)
- ligand = {target_ligand} if the paper mentions this receptor interacting with its canonical ligand; 
  otherwise null. If only a drug is mentioned, ligand = null &amp; put drug in tested_compound.
- receptor_family = {target_family}
- source ∈ {{"review", "original_research"}}
- literature = {{pmid, doi, title, year, journal}}
- evidence = SHORTEST sentence mentioning {target_receptor_gene} or its aliases
- confidence: high (clear info), medium (some missing), low (only brief mention)
- reasoning: explain how you extracted info for THIS receptor (≤ 80 words)
- If no info about this receptor except a brief mention, keep most fields null but set confidence=low.

No markdown, no ```json fences, just raw JSON.
"""

USER_TEMPLATE_SINGLE = """Input:
PMID: {pmid}
Source: {pub_types}
Title: {title}
Abstract: {abstract}

Now extract only about {target_receptor_gene} ({target_receptor_family}).
"""

# ---------- 辅助函数：解析单条JSON ----------
def parse_single_json(text):
    """解析LLM输出的单个JSON对象"""
    text = text.strip()
    
    # 1. 先尝试直接解析
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except:
        pass
    
    # 2. 找第一个{...}块
    match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text)
    if match:
        try:
            obj = json.loads(match.group(0))
            if isinstance(obj, dict):
                return obj
        except:
            pass
    
    return None

# ---------- 主逻辑 ----------
def main():
    print("="*80)
    print("改进版：逐个受体提取")
    print("="*80)
    
    # 1. 加载数据
    print(f"\nLoading PubMed test set from {PUBMED_DATA_PATH}")
    with open(PUBMED_DATA_PATH, "r", encoding="utf-8") as f:
        pubmed_records = json.load(f)
    
    # 2. 初始化OpenAI客户端
    load_dotenv(ENV_PATH)
    client = OpenAI(
        api_key=os.getenv("DASHSCOPE_API_KEY"),
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    
    extracted_results = []
    total_pmids = len(pubmed_records)
    
    # 3. 对每条记录逐个处理
    for idx, record in enumerate(pubmed_records):
        pmid = record.get("pmid", "")
        title = record.get("title", "")
        abstract = record.get("abstract", "")
        pub_types = record.get("pub_types", [])
        doi = record.get("doi", "")
        year = record.get("year", "")
        journal = record.get("journal", "")
        
        print(f"\n[{idx+1}/{total_pmids}] Processing PMID: {pmid}")
        
        # 3.1 用改进的文本扫描找到所有提及的受体
        mentioned_genes = improved_scan_mentions(title, abstract)
        print(f"  - Mentioned genes (text scan): {mentioned_genes}")
        
        if not mentioned_genes:
            print(f"  - No genes mentioned, skipping")
            continue
        
        # 3.2 对每个提到的受体单独调用LLM
        for gene in mentioned_genes:
            print(f"  - Extracting for {gene}...")
            
            # 构建Prompt
            system_prompt = SYSTEM_PROMPT_SINGLE.format(
                target_receptor_gene=gene,
                target_ligand=GENE_TO_CANONICAL_LIGAND.get(gene, "null"),
                target_family=GENE_TO_FAMILY.get(gene, "")
            )
            
            user_prompt = USER_TEMPLATE_SINGLE.format(
                pmid=pmid,
                pub_types=",".join(pub_types),
                title=title,
                abstract=abstract,
                target_receptor_gene=gene,
                target_receptor_family=GENE_TO_FAMILY.get(gene, "")
            )
            
            # 调用LLM
            max_retries = 3
            parsed = None
            
            for retry in range(max_retries):
                try:
                    response = client.chat.completions.create(
                        model="qwen-plus",
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        temperature=0.2
                    )
                    llm_output = response.choices[0].message.content.strip()
                    
                    # 解析JSON
                    parsed = parse_single_json(llm_output)
                    
                    if parsed:
                        # 添加一些元数据
                        parsed["receptor_gene_query"] = gene
                        parsed["extraction_meta"] = {
                            "model": "qwen-plus",
                            "prompt_version": "v3_single_receptor",
                            "extracted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                        }
                        
                        # 确保liturature完整
                        if "literature" not in parsed or not parsed["literature"]:
                            parsed["literature"] = {
                                "pmid": pmid,
                                "doi": doi,
                                "title": title,
                                "year": year,
                                "journal": journal
                            }
                        
                        extracted_results.append(parsed)
                        print(f"    ✓ Success")
                        break
                    else:
                        print(f"    ✗ Failed to parse JSON (retry {retry+1}/{max_retries})")
                        time.sleep(1)
                except Exception as e:
                    print(f"    ✗ API error: {e} (retry {retry+1}/{max_retries})")
                    time.sleep(2)
            
            time.sleep(0.6)  # 限速
        
    # 4. 保存结果
    print(f"\nSaving {len(extracted_results)} extractions to {OUTPUT_PATH}")
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(extracted_results, f, ensure_ascii=False, indent=2)
    
    print("\nDone!")

if __name__ == "__main__":
    main()
