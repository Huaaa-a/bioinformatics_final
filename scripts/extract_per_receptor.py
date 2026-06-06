
"""
改进版：逐个受体抽取（使用完整规范化逻辑）
"""
import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
import openpyxl

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = REPO_ROOT / "data" / "pubmed_test_set.json"
DEFAULT_OUTPUT = REPO_ROOT / "data" / "pubmed_extracted.v4.json"
DEFAULT_LOG = REPO_ROOT / "data" / "extract_v4.log"
DEFAULT_XLSX = REPO_ROOT / "receptor_list_classic_neurotransmitter_gpcr.xlsx"

QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen-plus"
PROMPT_VERSION = "v4_per_receptor"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(DEFAULT_LOG, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("extract_v4")


# ---------- 受体 ↔ canonical ligand 映射 ----------
CANONICAL_LIGANDS: set[str] = {
    "dopamine", "serotonin", "norepinephrine/epinephrine",
    "acetylcholine", "glutamate", "GABA", "histamine",
}
GENE_TO_CANONICAL_LIGAND: dict[str, str] = {
    "DRD1": "dopamine", "DRD2": "dopamine", "DRD3": "dopamine",
    "DRD4": "dopamine", "DRD5": "dopamine",
    "HTR1A": "serotonin", "HTR1B": "serotonin", "HTR1D": "serotonin",
    "HTR1E": "serotonin", "HTR1F": "serotonin",
    "HTR2A": "serotonin", "HTR2B": "serotonin", "HTR2C": "serotonin",
    "HTR3A": "serotonin", "HTR4": "serotonin", "HTR5A": "serotonin",
    "HTR6": "serotonin", "HTR7": "serotonin",
    "ADRA1A": "norepinephrine/epinephrine", "ADRA1B": "norepinephrine/epinephrine",
    "ADRA1D": "norepinephrine/epinephrine", "ADRA2A": "norepinephrine/epinephrine",
    "ADRA2B": "norepinephrine/epinephrine", "ADRA2C": "norepinephrine/epinephrine",
    "ADRB1": "norepinephrine/epinephrine", "ADRB2": "norepinephrine/epinephrine",
    "ADRB3": "norepinephrine/epinephrine",
    "CHRM1": "acetylcholine", "CHRM2": "acetylcholine",
    "CHRM3": "acetylcholine", "CHRM4": "acetylcholine", "CHRM5": "acetylcholine",
    "GRM1": "glutamate", "GRM2": "glutamate", "GRM3": "glutamate",
    "GRM4": "glutamate", "GRM5": "glutamate", "GRM6": "glutamate",
    "GRM7": "glutamate", "GRM8": "glutamate",
    "GABBR1": "GABA", "GABBR2": "GABA",
    "HRH1": "histamine", "HRH2": "histamine",
    "HRH3": "histamine", "HRH4": "histamine",
}


def load_receptor_metadata(xlsx_path: Path):
    """加载受体列表，包括基因符号、别名、家族等"""
    if not xlsx_path.exists():
        return {}, {}, {}, set()
    wb = openpyxl.load_workbook(xlsx_path, data_only=True, read_only=True)
    ws = wb["included_receptors"]
    rows = ws.iter_rows(values_only=True)
    header = next(rows)
    idx = {name: i for i, name in enumerate(header)}
    gene_to_family = {}
    gene_to_aliases = {}
    family_map = {}
    valid_genes = set()
    for row in rows:
        if not row or row[idx["receptor_gene"]] is None:
            continue
        gene = str(row[idx["receptor_gene"]]).strip().upper()
        family = str(row[idx["receptor_family"]] or "").strip()
        alias_str = str(row[idx.get("common_aliases", -1)] or "").strip()
        aliases = []
        if alias_str:
            aliases = [a.strip() for a in alias_str.split(";") if a.strip()]
        gene_to_family[gene] = family
        gene_to_aliases[gene] = aliases
        family_map[gene] = family
        valid_genes.add(gene)
    wb.close()
    return gene_to_family, gene_to_aliases, family_map, valid_genes


# ---------- 改进的文本扫描 ----------
def improved_scan_mentions(title: str, abstract: str, gene_to_aliases):
    """扫描文本找到所有提到的受体基因"""
    combined = f"{title} {abstract}".lower()
    combined_no_html = re.sub(r"<[a-zA-Z][^>]*>", "", combined)
    combined_norm = re.sub(r"-", "", combined_no_html)
    found = set()
    for gene, aliases in gene_to_aliases.items():
        # 匹配基因名
        if re.search(r"\b" + re.escape(gene.lower()) + r"\b", combined_norm):
            found.add(gene)
            continue
        # 匹配别名
        for alias in aliases:
            if alias.lower() in combined_no_html:
                found.add(gene)
                break
    return sorted(found)


# ---------- Prompt (针对单个受体) ----------
SYSTEM_PROMPT_SINGLE = """You are a biomedical knowledge extractor for a neurotransmitter GPCR database.
Your task: EXTRACT INFORMATION ONLY ABOUT THIS SPECIFIC RECEPTOR: {target_receptor_gene}
IGNORE ALL OTHER RECEPTORS MENTIONED IN THE PAPER!

Output ONE JSON OBJECT (NOT array) with exactly these 17 fields:
pmid, source, receptor, receptor_gene, receptor_family, ligand, location, cell_type, downstream_pathway,
function, species, literature, evidence, confidence, reasoning, tested_compound.

Strict rules:
- receptor_gene = {target_receptor_gene} (exactly this, no other).
- receptor_family = {target_family}.
- ligand = {target_ligand} if this receptor interacts with its canonical endogenous ligand in this paper; otherwise null.
  - If the paper only tested a drug (no canonical ligand mentioned), set ligand=null and put drug in tested_compound.
- tested_compound: drug/synthetic compound actually tested that acts on {target_receptor_gene}; null if none.
- source ∈ {{"review", "original_research"}}.
- literature = {{"pmid", "doi", "title", "year", "journal"}}.
- evidence: SHORTEST sentence mentioning {target_receptor_gene} or its aliases; verbatim, ≤ 30 words.
- reasoning (≤ 80 words): explain (a) how you determined ligand vs tested_compound, (b) confidence rationale.
- confidence:
  * "high": receptor, ligand/tested_compound, and at least one of location/cell_type/pathway/function are clear.
  * "medium": receptor/ligand clear, some fields missing or lightly inferred.
  * "low": receptor only briefly mentioned, no functional details.

No markdown, no ```json fences, just raw JSON."""

USER_TEMPLATE_SINGLE = """Input:
PMID: {pmid}
Source: {pub_types}
Title: {title}
Abstract: {abstract}

Now extract only about {target_receptor_gene}."""


# ---------- 规范化函数 ----------
def _normalize_str(v):
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in {"null", "none", "n/a", "na"}:
        return None
    return s


def _normalize_ligand(v):
    s = _normalize_str(v)
    if not s:
        return None
    s_low = s.lower()
    if s_low in {"dopamine", "serotonin", "acetylcholine", "glutamate", "gaba", "histamine"}:
        return "GABA" if s_low == "gaba" else s_low
    if s_low in {"norepinephrine", "epinephrine", "noradrenaline", "adrenaline", "norepinephrine/epinephrine"}:
        return "norepinephrine/epinephrine"
    if "dopamine" in s_low:
        return "dopamine"
    if "serotonin" in s_low or "5-ht" in s_low or "5ht" in s_low:
        return "serotonin"
    if "norepinephrine" in s_low or "noradrenalin" in s_low or "epinephrine" in s_low or "adrenalin" in s_low:
        return "norepinephrine/epinephrine"
    if "acetylcholine" in s_low or s_low == "ach":
        return "acetylcholine"
    if "glutamate" in s_low:
        return "glutamate"
    if "gaba" in s_low:
        return "GABA"
    if "histamine" in s_low:
        return "histamine"
    return None


def _strip_html(text: str):
    if not text:
        return ""
    text = re.sub(r"<[a-zA-Z][^>]*>", "", text)
    text = re.sub(r"</[a-zA-Z][^>]*>", "", text)
    return text


def _truncate_evidence(text: str, max_words: int = 30):
    words = text.split()
    if len(words) <= max_words:
        return text
    truncated = " ".join(words[:max_words])
    for sep in [". ", "; "]:
        last_sep = truncated.rfind(sep)
        if last_sep > len(truncated) // 2:
            return truncated[: last_sep + 1].strip()
    return truncated


def normalize_entry(
    parsed: dict,
    source_pmid: str,
    source_record: dict,
    target_gene: str,
    family_map: dict,
    valid_genes: set,
):
    pmid = _normalize_str(parsed.get("pmid")) or source_pmid

    pub_types = source_record.get("pub_types", [])
    if pub_types:
        is_review = any("review" in pt.lower() or "meta-analysis" in pt.lower() for pt in pub_types)
        source = "review" if is_review else "original_research"
    else:
        source = _normalize_str(parsed.get("source"))
        if source and source.lower() in {"review", "review_article"}:
            source = "review"
        elif source and source.lower() in {"original_research", "research", "article", "original"}:
            source = "original_research"
        else:
            source = None

    receptor = _strip_html(_normalize_str(parsed.get("receptor")) or "") or None
    receptor_gene = _normalize_str(parsed.get("receptor_gene"))
    if receptor_gene:
        receptor_gene = receptor_gene.upper()
    # 强制设为 target_gene（因为是逐个受体抽取）
    receptor_gene = target_gene

    receptor_family = _normalize_str(parsed.get("receptor_family"))
    if family_map and receptor_gene and receptor_gene in family_map:
        standard_family = family_map[receptor_gene]
        if receptor_family and receptor_family != standard_family:
            receptor_family = standard_family
        elif not receptor_family:
            receptor_family = standard_family

    ligand = _normalize_ligand(parsed.get("ligand"))
    tested_compound = _strip_html(_normalize_str(parsed.get("tested_compound")) or "") or None
    location = _strip_html(_normalize_str(parsed.get("location")) or "") or None
    cell_type = _strip_html(_normalize_str(parsed.get("cell_type")) or "") or None
    downstream_pathway = _strip_html(_normalize_str(parsed.get("downstream_pathway")) or "") or None
    function = _strip_html(_normalize_str(parsed.get("function")) or "") or None
    species = _strip_html(_normalize_str(parsed.get("species")) or "") or None
    evidence = _strip_html(_normalize_str(parsed.get("evidence")) or "")
    evidence = _truncate_evidence(evidence) if evidence else None
    reasoning = _strip_html(_normalize_str(parsed.get("reasoning")) or "") or None

    confidence = _normalize_str(parsed.get("confidence"))
    if confidence and confidence.lower() in {"high", "medium", "low"}:
        confidence = confidence.lower()
    else:
        confidence = "low"

    literature_in = parsed.get("literature") or {}
    if not isinstance(literature_in, dict):
        literature_in = {}
    literature_out = {
        "pmid": source_pmid,
        "doi": _normalize_str(literature_in.get("doi")) or _normalize_str(source_record.get("doi")),
        "title": _normalize_str(source_record.get("title")),
        "year": _normalize_str(source_record.get("year")),
        "journal": _normalize_str(source_record.get("journal")),
    }

    canonical_for_gene = GENE_TO_CANONICAL_LIGAND.get(receptor_gene) if receptor_gene else None
    ligand_mismatch = False
    ligand_mismatch_reason = None
    if ligand is not None and canonical_for_gene and ligand != canonical_for_gene:
        ligand_mismatch = True
        ligand_mismatch_reason = (
            f"receptor_gene={receptor_gene} canonical={canonical_for_gene}, got ligand={ligand}"
        )

    missing_core = sum(1 for v in (receptor, receptor_gene, function) if not v)
    if missing_core >= 2 and confidence == "high":
        confidence = "medium"
    if missing_core >= 3 and confidence == "medium":
        confidence = "low"

    # mismatch 逻辑（逐个抽取模式一般不会有，但保留）
    query_gene = target_gene
    mismatch = bool(query_gene) and bool(receptor_gene) and query_gene != receptor_gene
    if mismatch and confidence != "low":
        order = {"high": "medium", "medium": "low"}
        confidence = order.get(confidence, confidence)

    if ligand_mismatch and confidence != "low":
        order = {"high": "medium", "medium": "low"}
        confidence = order.get(confidence, confidence)

    return {
        "pmid": pmid,
        "source": source,
        "receptor": receptor,
        "receptor_gene": receptor_gene,
        "receptor_family": receptor_family,
        "ligand": ligand,
        "location": location,
        "cell_type": cell_type,
        "downstream_pathway": downstream_pathway,
        "function": function,
        "species": species,
        "literature": literature_out,
        "evidence": evidence,
        "confidence": confidence,
        "reasoning": reasoning,
        "tested_compound": tested_compound,
        "ligand_mismatch": ligand_mismatch,
        "ligand_mismatch_reason": ligand_mismatch_reason,
        "needs_human_review": confidence == "low" or mismatch or ligand_mismatch,
        "receptor_gene_query": query_gene,
        "receptor_gene_mismatch": mismatch,
        "extraction_meta": {
            "model": "qwen-plus",
            "prompt_version": PROMPT_VERSION,
            "extracted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    }


# ---------- JSON 解析 ----------
def parse_single_json(text: str):
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    m = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    return None


# ---------- API 调用 ----------
def call_qwen(client: OpenAI, model: str, system: str, user: str, max_retries: int = 3):
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.0,
                max_tokens=2000,
            )
            content = resp.choices[0].message.content or ""
            return content, None
        except Exception as e:
            last_err = str(e)
            log.warning("API call failed (attempt %d/%d): %s", attempt, max_retries, e)
            if "429" in last_err or "rate" in last_err.lower():
                time.sleep(min(2 ** attempt, 8))
            else:
                time.sleep(1.0)
    return "", last_err or "unknown_error"


# ---------- 主函数 ----------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--xlsx", type=Path, default=DEFAULT_XLSX)
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    log.info("Loading receptor metadata from %s", args.xlsx)
    gene_to_family, gene_to_aliases, family_map, valid_genes = load_receptor_metadata(args.xlsx)

    log.info("Loading PubMed test set from %s", args.input)
    with args.input.open("r", encoding="utf-8") as f:
        pubmed_records = json.load(f)
    if args.limit:
        pubmed_records = pubmed_records[: args.limit]

    load_dotenv(REPO_ROOT / "scripts" / ".env")
    api_key = os.getenv("QWEN_API_KEY", "").strip()
    if not api_key:
        log.error("Missing QWEN_API_KEY; configure in scripts/.env")
        sys.exit(1)
    client = OpenAI(api_key=api_key, base_url=QWEN_BASE_URL)

    extracted = []
    for idx, record in enumerate(pubmed_records):
        pmid = record.get("pmid", "")
        title = record.get("title", "")
        abstract = record.get("abstract", "")
        pub_types = record.get("pub_types", [])
        log.info("[%d/%d] Processing PMID %s", idx + 1, len(pubmed_records), pmid)

        mentioned_genes = improved_scan_mentions(title, abstract, gene_to_aliases)
        log.info("  Mentioned genes: %s", mentioned_genes)

        for target_gene in mentioned_genes:
            log.info("  Extracting for %s...", target_gene)
            target_family = family_map.get(target_gene, "")
            target_ligand = GENE_TO_CANONICAL_LIGAND.get(target_gene, "")

            system_prompt = SYSTEM_PROMPT_SINGLE.format(
                target_receptor_gene=target_gene,
                target_family=target_family,
                target_ligand=target_ligand,
            )
            user_prompt = USER_TEMPLATE_SINGLE.format(
                pmid=pmid,
                pub_types=",".join(pub_types),
                title=title,
                abstract=abstract,
                target_receptor_gene=target_gene,
            )

            llm_output, err = call_qwen(client, args.model, system_prompt, user_prompt)
            if err:
                log.warning("  Failed to extract for %s: %s", target_gene, err)
                continue
            parsed = parse_single_json(llm_output)
            if not parsed:
                log.warning("  Failed to parse JSON for %s", target_gene)
                continue
            normalized = normalize_entry(
                parsed,
                pmid,
                record,
                target_gene,
                family_map,
                valid_genes,
            )
            extracted.append(normalized)
            log.info("  Success: confidence=%s", normalized["confidence"])
            time.sleep(0.6)

    log.info("Saving %d extracted entries to %s", len(extracted), args.output)
    with args.output.open("w", encoding="utf-8") as f:
        json.dump(extracted, f, ensure_ascii=False, indent=2)

    log.info("Done!")


if __name__ == "__main__":
    main()
