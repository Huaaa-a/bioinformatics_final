"""
extract_fields_qwen.py
======================

读 data/pubmed_test_set.json,对每条 PubMed 摘要调用 Qwen (qwen-plus) 抽字段,
输出严格 JSON 数组,落到 data/pubmed_extracted.json。

新数据模型(spec:revise-multi-receptor-strategy):
  pubmed_extracted.json = [
    {pmid, receptor_gene, ...14 fields, reasoning, tested_compound,
     ligand_mismatch, ligand_mismatch_reason, ...审计字段}
  ]

一篇 abstract 产生 N 条 entry(每个 focal receptor 一条)。

特性:
- OpenAI 兼容 SDK 调用 dashscope
- 严格 JSON 数组解析(允许围栏,允许单对象包装成 1 元素数组)
- 限速 0.5s/次 + 429 指数退避
- 失败重试 3 次;3 次仍败则该 PMID 标 api_error,不入数组
- 断点续跑:按 (pmid, receptor_gene) 跳过已抽成功的 entry
- 一遍主 prompt 跑完后,对 confidence=low 的非错误 entry 做二审

依赖:openai>=1.0, python-dotenv
运行:python scripts/extract_fields_qwen.py
可选:--limit N(只跑前 N 条,试跑用) --two-pass on|off(默认 on)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

try:
    from openai import OpenAI
except ImportError:
    sys.stderr.write("缺少 openai 库:pip install openai>=1.0\n")
    sys.exit(1)

import openpyxl

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = REPO_ROOT / "data" / "pubmed_test_set.json"
DEFAULT_OUTPUT = REPO_ROOT / "data" / "pubmed_extracted.json"
DEFAULT_LOG = REPO_ROOT / "data" / "extract_run.log"
DEFAULT_XLSX = REPO_ROOT / "receptor_list_classic_neurotransmitter_gpcr.xlsx"

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "anthropic/claude-opus-4"
PROMPT_VERSION = "v4"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("extract_qwen")


# ---------- 受体 ↔ canonical ligand 映射(与 fetch_pubmed_test_set.py 一致) ----------

# canonical ligand 名(由 spec 规范化,就是 ligand 字段的合法值)
CANONICAL_LIGANDS: set[str] = {
    "dopamine", "serotonin", "norepinephrine/epinephrine",
    "acetylcholine", "glutamate", "GABA", "histamine",
}

# 受体基因 → canonical ligand
GENE_TO_CANONICAL_LIGAND: dict[str, str] = {
    "DRD1": "dopamine", "DRD2": "dopamine", "DRD3": "dopamine",
    "DRD4": "dopamine", "DRD5": "dopamine",
    "HTR1A": "serotonin", "HTR1B": "serotonin", "HTR1D": "serotonin",
    "HTR1E": "serotonin", "HTR1F": "serotonin",
    "HTR2A": "serotonin", "HTR2B": "serotonin", "HTR2C": "serotonin",
    "HTR4": "serotonin", "HTR5A": "serotonin", "HTR6": "serotonin", "HTR7": "serotonin",
    "ADRA1A": "norepinephrine/epinephrine", "ADRA1B": "norepinephrine/epinephrine",
    "ADRA1D": "norepinephrine/epinephrine",
    "ADRA2A": "norepinephrine/epinephrine", "ADRA2B": "norepinephrine/epinephrine",
    "ADRA2C": "norepinephrine/epinephrine",
    "ADRB1": "norepinephrine/epinephrine", "ADRB2": "norepinephrine/epinephrine",
    "ADRB3": "norepinephrine/epinephrine",
    "CHRM1": "acetylcholine", "CHRM2": "acetylcholine", "CHRM3": "acetylcholine",
    "CHRM4": "acetylcholine", "CHRM5": "acetylcholine",
    "GRM1": "glutamate", "GRM2": "glutamate", "GRM3": "glutamate", "GRM4": "glutamate",
    "GRM5": "glutamate", "GRM6": "glutamate", "GRM7": "glutamate", "GRM8": "glutamate",
    "GABBR1": "GABA", "GABBR2": "GABA",
    "HRH1": "histamine", "HRH2": "histamine", "HRH3": "histamine", "HRH4": "histamine",
}


# ---------- 白名单加载 ----------

def load_xlsx_metadata(xlsx_path: Path) -> tuple[dict[str, str], set[str], dict[str, list[str]]]:
    """从 xlsx 加载 receptor_family 映射、valid_genes 集合、common_aliases 映射。"""
    if not xlsx_path.exists():
        log.warning("xlsx 不存在: %s,跳过白名单校验", xlsx_path)
        return {}, set(), {}
    wb = openpyxl.load_workbook(xlsx_path, data_only=True, read_only=True)
    ws = wb["included_receptors"]
    rows = ws.iter_rows(values_only=True)
    header = next(rows)
    idx = {name: i for i, name in enumerate(header)}
    family_map: dict[str, str] = {}
    valid_genes: set[str] = set()
    aliases_map: dict[str, list[str]] = {}
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


def _build_alias_hint(aliases_map: dict[str, list[str]]) -> str:
    """为 LLM prompt 生成别名提示。"""
    lines = []
    for gene, aliases in sorted(aliases_map.items()):
        if aliases:
            lines.append(f"  {gene}: {', '.join(aliases)}")
    return "\n".join(lines)


def _build_canonical_ligand_hint() -> str:
    """为 LLM prompt 生成 receptor→canonical ligand 提示。"""
    lines = []
    for gene, lig in sorted(GENE_TO_CANONICAL_LIGAND.items()):
        lines.append(f"  {gene} → {lig}")
    return "\n".join(lines)


# ---------- Prompt ----------

SYSTEM_PROMPT_TEMPLATE = (
    "You are a biomedical knowledge extractor for a neurotransmitter GPCR database. "
    "Read the PubMed title+abstract and output ONE STRICT JSON ARRAY of objects, where "
    "EACH object corresponds to ONE focal receptor discussed in the paper. "
    "If the paper clearly focuses on a single receptor, output a 1-element array. "
    "If it discusses multiple focal receptors (e.g. heterodimer, broad review), output "
    "one object per focal receptor in the order they appear in the abstract.\n"
    "\n"
    "Each object MUST have exactly these 17 fields: pmid, source, receptor, receptor_gene, "
    "receptor_family, ligand, location, cell_type, downstream_pathway, function, species, "
    "literature, evidence, confidence, reasoning, tested_compound.\n"
    "\n"
    "Strict rules:\n"
    "- Output JSON ARRAY only. No markdown, no ```json fences, no explanation before or after.\n"
    "- source ∈ {{\"review\", \"original_research\"}}.\n"
    "- receptor_gene MUST be one of the 24 standard HGNC symbols listed below.\n"
    "- receptor_family MUST match the standard family name listed below.\n"
    "- ligand ∈ one of {{dopamine, serotonin, norepinephrine/epinephrine, acetylcholine, "
    "glutamate, GABA, histamine}} — and MUST be the canonical endogenous ligand of the "
    "receptor in the row, not a drug. If the paper only tested a drug (no canonical ligand "
    "mentioned for this receptor), set ligand=null AND put the drug name in tested_compound.\n"
    "- tested_compound: drug or synthetic compound actually tested in the study that "
    "acts on this receptor (e.g. \"haloperidol\", \"LSD\", \"tianeptine\"); null if only "
    "the canonical ligand is discussed.\n"
    "- location / cell_type / downstream_pathway / function / species: short noun phrases; "
    "null if absent.\n"
    "- literature = {{pmid, doi, title, year, journal}}; doi is null if unknown.\n"
    "- evidence = the SHORTEST sentence in the abstract that directly supports the "
    "receptor+ligand+function claim for THIS focal receptor. Verbatim quote, ≤ 30 words.\n"
    "- reasoning (≤ 80 words): explain (a) why you picked this receptor as focal, "
    "(b) how you determined ligand vs tested_compound, (c) the confidence rationale. "
    "This is recorded for downstream error analysis.\n"
    "- confidence:\n"
    '  * "high" — abstract clearly names receptor, ligand (or tested_compound), and at '
    "least one of {{location, cell_type, downstream_pathway, function}}; evidence is direct.\n"
    '  * "medium" — receptor/ligand clear, some fields missing or lightly inferred.\n'
    '  * "low" — receptor unclear, broad review, or multiple receptors without a single '
    "focal focal one.\n"
    "- If a field cannot be confirmed, use null AND lower confidence by one step "
    "(high→medium, medium→low, low stays low).\n"
    "\nStandard receptor gene symbols and their families/aliases:\n"
    "{alias_hint}\n"
    "\nReceptor → canonical endogenous ligand (ligand field MUST match this when set):\n"
    "{ligand_hint}\n"
    "\nCandidate focal receptors (auto-detected by text scan, treat as starting point — "
    "include a receptor only if it is the main subject of at least one result in the abstract):\n"
    "{candidate_receptors}"
)

USER_TEMPLATE = """Input:
PMID: {pmid}
Source: {pub_types}
Title: {title}
Abstract: {abstract}

Output the JSON array now."""

SYSTEM_PROMPT_REVIEW = (
    "You are reviewing a previous low-confidence extraction. The previous output is provided "
    "below. Re-read the abstract and try to improve the entry:\n"
    "(a) find a more specific evidence sentence (verbatim, ≤ 30 words),\n"
    "(b) tighten the receptor name and gene symbol,\n"
    "(c) fill in location / cell_type / downstream_pathway / species if mentioned even once,\n"
    "(d) verify ligand matches the receptor's canonical ligand, move drugs to tested_compound.\n"
    "Output ONE STRICT JSON ARRAY (the same number of objects as the previous output, in the same "
    "order, with the same 17 fields). If you cannot improve specificity, keep the previous values."
    " No markdown, no explanation."
)


# ---------- JSON 数组解析 ----------

_JSON_FENCE_OBJ_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)
_JSON_FENCE_ARR_RE = re.compile(r"```(?:json)?\s*(\[.*?\])\s*```", re.S)
_JSON_ARR_RE = re.compile(r"\[.*\]", re.S)
_JSON_OBJ_RE = re.compile(r"\{.*\}", re.S)


def parse_strict_json_array(text: str) -> list[dict] | None:
    """从 LLM 输出中提取 JSON 数组。容忍围栏、单对象被误输出。"""
    text = (text or "").strip()
    if not text:
        return None

    # 1. ```json [...]``` 围栏
    m = _JSON_FENCE_ARR_RE.search(text)
    if m:
        try:
            arr = json.loads(m.group(1))
            if isinstance(arr, list):
                return [e for e in arr if isinstance(e, dict)]
        except json.JSONDecodeError:
            pass

    # 2. 裸 [...] 数组
    m = _JSON_ARR_RE.search(text)
    if m:
        candidate = m.group(0)
        first = candidate.find("[")
        last = candidate.rfind("]")
        if first != -1 and last != -1 and last > first:
            try:
                arr = json.loads(candidate[first: last + 1])
                if isinstance(arr, list):
                    return [e for e in arr if isinstance(e, dict)]
                if isinstance(arr, dict):
                    return [arr]
            except json.JSONDecodeError:
                pass

    # 3. 退路: ```json {...}``` 单对象
    m = _JSON_FENCE_OBJ_RE.search(text)
    if m:
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict):
                return [obj]
        except json.JSONDecodeError:
            pass

    # 4. 退路: 第一个 {...} 块
    m = _JSON_OBJ_RE.search(text)
    if m:
        candidate = m.group(0)
        first = candidate.find("{")
        last = candidate.rfind("}")
        if first != -1 and last != -1 and last > first:
            try:
                obj = json.loads(candidate[first: last + 1])
                if isinstance(obj, dict):
                    return [obj]
            except json.JSONDecodeError:
                pass

    return None


# ---------- 字段规整 ----------

REQUIRED_FIELDS = [
    "pmid", "source", "receptor", "receptor_gene", "receptor_family",
    "ligand", "location", "cell_type", "downstream_pathway", "function",
    "species", "literature", "evidence", "confidence",
    "reasoning", "tested_compound",  # 新增 v3
]
ALL_FIELDS_V3 = REQUIRED_FIELDS + ["ligand_mismatch", "ligand_mismatch_reason"]

ALLOWED_SOURCE = {"review", "original_research"}
ALLOWED_CONFIDENCE = {"high", "medium", "low"}


def _normalize_str(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in {"null", "none", "n/a", "na"}:
        return None
    return s


def _normalize_ligand(v) -> str | None:
    """把 LLM 输出归一到 CANONICAL_LIGANDS;不识别的值返回 None。"""
    s = _normalize_str(v)
    if not s:
        return None
    s_low = s.lower()
    if s_low in {"dopamine", "serotonin", "acetylcholine", "glutamate", "gaba", "histamine"}:
        return "GABA" if s_low == "gaba" else s_low
    if s_low in {"norepinephrine", "epinephrine", "noradrenaline", "adrenaline",
                 "norepinephrine/epinephrine"}:
        return "norepinephrine/epinephrine"
    # 模糊匹配
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


def _strip_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[a-zA-Z][^>]*>", "", text)
    text = re.sub(r"</[a-zA-Z][^>]*>", "", text)
    return text


def _truncate_evidence(text: str, max_words: int = 30) -> str:
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
    family_map: dict[str, str] | None = None,
    valid_genes: set[str] | None = None,
) -> dict:
    """把 LLM 输出的单条 entry dict 规整到标准字段,加 ligand ↔ receptor 校验。"""
    pmid = _normalize_str(parsed.get("pmid")) or source_pmid

    # source: 优先 PubMed pub_types
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

    # receptor_gene 白名单校验:若 LLM 给了非白名单值,优先用 query
    query_gene = _normalize_str(source_record.get("query_receptor_gene"))
    if query_gene:
        query_gene = query_gene.upper()
    if valid_genes and receptor_gene and receptor_gene not in valid_genes:
        if query_gene and query_gene in valid_genes:
            receptor_gene = query_gene

    # receptor_family 白名单校验
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
    if confidence and confidence.lower() in ALLOWED_CONFIDENCE:
        confidence = confidence.lower()
    else:
        confidence = "low"

    # literature: 强制用 PubMed 源数据
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

    # ligand ↔ receptor 强校验
    canonical_for_gene = GENE_TO_CANONICAL_LIGAND.get(receptor_gene) if receptor_gene else None
    ligand_mismatch = False
    ligand_mismatch_reason: str | None = None
    if ligand is not None and canonical_for_gene and ligand != canonical_for_gene:
        # 例外:论文只测了 drug(tested_compound 非空)且 ligand=null — 已放过
        # 此处 ligand 已经被 _normalize_ligand 限制到 CANONICAL_LIGANDS,所以"不匹配"=给错系统
        ligand_mismatch = True
        ligand_mismatch_reason = (
            f"receptor_gene={receptor_gene} canonical={canonical_for_gene}, got ligand={ligand}"
        )

    # 缺核心字段降档
    missing_core = sum(1 for v in (receptor, receptor_gene, function) if not v)
    if missing_core >= 2 and confidence == "high":
        confidence = "medium"
    if missing_core >= 3 and confidence == "medium":
        confidence = "low"

    # query 对照 + mismatch
    mismatch = bool(query_gene) and bool(receptor_gene) and query_gene != receptor_gene
    if mismatch and confidence != "low":
        order = {"high": "medium", "medium": "low"}
        confidence = order.get(confidence, confidence)

    # ligand_mismatch 也要降档(在 mismatch 之后处理,避免覆盖 mismatch 的降档)
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
    }


# ---------- API 调用 ----------

def make_client(api_key: str) -> OpenAI:
    return OpenAI(
        api_key=api_key,
        base_url=OPENROUTER_BASE_URL,
        timeout=60.0,
        default_headers={
            "HTTP-Referer": "https://github.com/bioinfo-extract",
            "X-Title": "BioInfo Extract",
        },
    )


def call_qwen(client: OpenAI, model: str, system: str, user: str, max_retries: int = 3, max_tokens: int = 2000):
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
                max_tokens=max_tokens,
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


# ---------- 持久化 ----------

def load_env() -> tuple[str, str]:
    load_dotenv(REPO_ROOT / "scripts" / ".env")
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        sys.stderr.write(
            "缺少 OPENROUTER_API_KEY,先在 scripts/.env 中配置(参考 scripts/.env.example)\n"
        )
    model = os.getenv("OPENROUTER_MODEL", "").strip() or DEFAULT_MODEL
    return api_key, model


def load_input(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_output(path: Path) -> list[dict]:
    """读已有 entry 数组。"""
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return []


def save_output(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


def entry_key(e: dict) -> tuple[str, str]:
    return (str(e.get("pmid", "")), str(e.get("receptor_gene") or ""))


# ---------- 单篇抽取 ----------

def extract_paper(
    client: OpenAI,
    model: str,
    record: dict,
    system_prompt: str,
    family_map: dict[str, str],
    valid_genes: set[str],
) -> list[dict]:
    """对单条 record 调一次 LLM,返回 entry 列表(可能空,带 api_error/parse_error 标记)。"""
    pmid = record["pmid"]
    user = USER_TEMPLATE.format(
        pmid=pmid,
        pub_types=", ".join(record.get("pub_types") or []),
        title=record.get("title", ""),
        abstract=record.get("abstract", ""),
    )
    raw, err = call_qwen(client, model, system_prompt, user, max_retries=3, max_tokens=2000)
    extracted_at = datetime.now(timezone.utc).isoformat()
    if err and not raw:
        return [{
            "pmid": pmid,
            "api_error": err,
            "needs_human_review": True,
            "extraction_meta": {
                "model": model,
                "prompt_version": PROMPT_VERSION,
                "attempt_count": 3,
                "extracted_at": extracted_at,
            },
        }]
    parsed_arr = parse_strict_json_array(raw)
    if not parsed_arr:
        return [{
            "pmid": pmid,
            "parse_error": True,
            "needs_human_review": True,
            "extraction_meta": {
                "model": model,
                "prompt_version": PROMPT_VERSION,
                "attempt_count": 1,
                "extracted_at": extracted_at,
                "raw_first_300": raw[:300],
            },
        }]
    out: list[dict] = []
    for one in parsed_arr:
        norm = normalize_entry(one, pmid, record, family_map, valid_genes)
        norm["extraction_meta"] = {
            "model": model,
            "prompt_version": PROMPT_VERSION,
            "attempt_count": 1,
            "extracted_at": extracted_at,
        }
        out.append(norm)
    return out


def review_paper(
    client: OpenAI,
    model: str,
    record: dict,
    previous_entries: list[dict],
    system_prompt: str,
    family_map: dict[str, str],
    valid_genes: set[str],
) -> list[dict]:
    """对单条 PMID 的低置信 entry 做二审,返回新 entry 列表(同长度同顺序)。"""
    pmid = record["pmid"]
    user = USER_TEMPLATE.format(
        pmid=pmid,
        pub_types=", ".join(record.get("pub_types") or []),
        title=record.get("title", ""),
        abstract=record.get("abstract", ""),
    ) + "\n\nPrevious extraction (improve each entry):\n" + json.dumps(
        previous_entries, ensure_ascii=False, indent=2
    )
    raw, err = call_qwen(client, model, SYSTEM_PROMPT_REVIEW, user, max_retries=3, max_tokens=2000)
    extracted_at = datetime.now(timezone.utc).isoformat()
    if err and not raw:
        for e in previous_entries:
            e.setdefault("extraction_meta", {})
            e["extraction_meta"]["review_attempted_at"] = extracted_at
            e["extraction_meta"]["review_error"] = err
        return previous_entries
    parsed_arr = parse_strict_json_array(raw)
    if not parsed_arr:
        for e in previous_entries:
            e.setdefault("extraction_meta", {})
            e["extraction_meta"]["review_attempted_at"] = extracted_at
            e["extraction_meta"]["review_parse_error"] = True
            e["extraction_meta"]["review_raw_first_300"] = raw[:300]
        return previous_entries
    out: list[dict] = []
    for i, prev in enumerate(previous_entries):
        if i < len(parsed_arr) and isinstance(parsed_arr[i], dict):
            one = parsed_arr[i]
        else:
            # 二审 LLM 没回这么多条,保留原 entry
            out.append(prev)
            continue
        norm = normalize_entry(one, pmid, record, family_map, valid_genes)
        norm["extraction_meta"] = {
            "model": model,
            "prompt_version": PROMPT_VERSION + "-review",
            "attempt_count": 1,
            "extracted_at": extracted_at,
        }
        out.append(norm)
    return out


# ---------- 主流程 ----------

def main() -> None:
    parser = argparse.ArgumentParser(description="Qwen 抽字段(数组 + 多受体 + ligand 校验)")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--xlsx", type=Path, default=DEFAULT_XLSX)
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条,0 = 全部")
    parser.add_argument("--two-pass", choices=["on", "off"], default="on")
    parser.add_argument("--force-rerun", action="store_true")
    args = parser.parse_args()

    api_key, model = load_env()
    client = make_client(api_key)

    family_map, valid_genes, aliases_map = load_xlsx_metadata(args.xlsx)
    log.info("白名单: %d 个 valid_genes, %d 个 family 映射, %d 个别名组",
             len(valid_genes), len(family_map), len(aliases_map))

    alias_hint = _build_alias_hint(aliases_map)
    ligand_hint = _build_canonical_ligand_hint()

    records = load_input(args.input)
    log.info("读取 %d 条输入", len(records))
    if args.limit:
        records = records[: args.limit]
        log.info("限制为前 %d 条", len(records))

    if args.force_rerun:
        entries: list[dict] = []
    else:
        entries = load_output(args.output)
    existing_keys = {entry_key(e) for e in entries if e.get("pmid") and e.get("receptor_gene")}
    log.info("已抽取 %d 个 entry (将跳过)", len(entries))

    # 按 PMID 分组已有 entry,二审时一次取一组的 entry
    by_pmid: dict[str, list[dict]] = {}
    for e in entries:
        by_pmid.setdefault(e.get("pmid", ""), []).append(e)

    t0 = time.time()
    n_new = 0
    for i, r in enumerate(records, 1):
        pmid = r["pmid"]
        # 跳过的条件:这篇 PMID 已经至少有一个正常 entry
        existing_for_pmid = by_pmid.get(pmid, [])
        normal_existing = [e for e in existing_for_pmid
                           if e.get("receptor_gene") and not e.get("api_error") and not e.get("parse_error")]
        if normal_existing and not args.force_rerun:
            log.info("[%d/%d] pmid=%s 跳过(已有 %d 个 entry)",
                     i, len(records), pmid, len(normal_existing))
            continue
        log.info("[%d/%d] pmid=%s 抽取", i, len(records), pmid)
        # 候选 focal 受体 = mentioned_receptors_in_abstract(已被文本扫到)
        mentioned = r.get("mentioned_receptors_in_abstract") or [r.get("query_receptor_gene", "")]
        if not mentioned or mentioned == [""]:
            mentioned = [r.get("query_receptor_gene", "")]
        candidate_lines = []
        for g in mentioned:
            lig = GENE_TO_CANONICAL_LIGAND.get(g, "?")
            candidate_lines.append(f"  - {g} (canonical ligand: {lig})")
        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            alias_hint=alias_hint,
            ligand_hint=ligand_hint,
            candidate_receptors="\n".join(candidate_lines),
        )
        new_entries = extract_paper(client, model, r, system_prompt, family_map, valid_genes)
        # 把新 entry 合并到 entries 列表和 by_pmid
        for e in new_entries:
            entries.append(e)
            by_pmid.setdefault(pmid, []).append(e)
        n_new += len([e for e in new_entries if not e.get("api_error") and not e.get("parse_error")])
        save_output(args.output, entries)
        time.sleep(0.5)
    log.info("第一轮完成,新增 %d 个 entry,耗时 %.1fs", n_new, time.time() - t0)

    # 二审:对 confidence=low 或 mismatch 或 ligand_mismatch 的非错误 entry 做
    if args.two_pass == "on":
        review_targets: list[tuple[str, list[dict]]] = []
        for pmid, ents in by_pmid.items():
            targets = [
                e for e in ents
                if not e.get("api_error") and not e.get("parse_error")
                and (
                    e.get("confidence") == "low"
                    or e.get("receptor_gene_mismatch")
                    or e.get("ligand_mismatch")
                )
                and not (e.get("extraction_meta") or {}).get("review_attempted_at")
            ]
            if targets:
                review_targets.append((pmid, targets))
        log.info("二审目标 %d 篇(共 %d 个 entry)", len(review_targets),
                 sum(len(t) for _, t in review_targets))
        t1 = time.time()
        for i, (pmid, targets) in enumerate(review_targets, 1):
            log.info("[review %d/%d] pmid=%s (%d entries)", i, len(review_targets), pmid, len(targets))
            record = next((r for r in records if r["pmid"] == pmid), None)
            if not record:
                continue
            # 找到 entries 列表里这些 target 的索引,二审后替换
            updated = review_paper(client, model, record, targets, None, family_map, valid_genes)
            for j, new_e in enumerate(updated):
                # 找到 entries 里对应的旧 entry 索引(用 entry_key 配对)
                old_key = entry_key(targets[j])
                for k, e in enumerate(entries):
                    if entry_key(e) == old_key and not e.get("extraction_meta", {}).get("review_attempted_at"):
                        entries[k] = new_e
                        break
            save_output(args.output, entries)
            time.sleep(0.5)
        log.info("二审完成,耗时 %.1fs", time.time() - t1)

    # 统计
    conf = {"high": 0, "medium": 0, "low": 0, "unknown": 0}
    n_api = n_parse = n_lig_mm = n_mm = 0
    for e in entries:
        c = e.get("confidence", "unknown")
        conf[c] = conf.get(c, 0) + 1
        if e.get("api_error"):
            n_api += 1
        if e.get("parse_error"):
            n_parse += 1
        if e.get("ligand_mismatch"):
            n_lig_mm += 1
        if e.get("receptor_gene_mismatch"):
            n_mm += 1
    log.info("最终 %d 个 entry | high=%d medium=%d low=%d unknown=%d | "
             "parse_err=%d api_err=%d | ligand_mismatch=%d receptor_mismatch=%d",
             len(entries), conf["high"], conf["medium"], conf["low"], conf.get("unknown", 0),
             n_parse, n_api, n_lig_mm, n_mm)
    log.info("输出: %s", args.output)


if __name__ == "__main__":
    main()
