"""
extract_fields_qwen.py
======================

读 data/pubmed_test_set.json,对每条 PubMed 摘要调用 Qwen (qwen-plus) 抽 14 字段,
输出严格 JSON,落到 data/pubmed_extracted.json。

特性:
- OpenAI 兼容 SDK 调用 dashscope
- 严格 JSON 解析(剥离 markdown 围栏后 json.loads)
- 限速 0.5s/次 + 429 指数退避
- 失败重试 3 次;3 次仍败则填 null + confidence=low + needs_human_review
- 断点续跑:已抽取 PMID 跳过
- 一遍主 prompt 跑完后,对 confidence=low 的非错误记录做二审 prompt

依赖:openai>=1.0, python-dotenv
运行:python scripts/extract_fields_qwen.py
可选:--limit N(只跑前 N 条,试跑用) --two-pass on|off(默认 on)
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
import os

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

QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen-plus"
PROMPT_VERSION = "v2"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("extract_qwen")


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

# ---------- Prompt ----------

SYSTEM_PROMPT_TEMPLATE = (
    "You are a biomedical knowledge extractor for a neurotransmitter GPCR database. "
    "Read the PubMed title+abstract and output ONE strict JSON object with exactly these 14 fields: "
    "pmid, source, receptor, receptor_gene, receptor_family, ligand, location, cell_type, "
    "downstream_pathway, function, species, literature, evidence, confidence.\n"
    "Strict rules:\n"
    "- Output JSON only. No markdown, no ```json fences, no explanation before or after.\n"
    "- source ∈ {{\"review\", \"original_research\"}}.\n"
    "- receptor_gene MUST be one of the 24 standard HGNC symbols listed below.\n"
    "- receptor_family MUST match the standard family name listed below.\n"
    "- ligand ∈ one of {{dopamine, serotonin, norepinephrine/epinephrine, acetylcholine, "
    "glutamate, GABA, histamine}} or null.\n"
    "- location / cell_type / downstream_pathway / function / species: short noun phrases; null if absent.\n"
    "- literature = {{pmid, doi, title, year, journal}}; doi is null if unknown.\n"
    "- evidence = the SHORTEST sentence in the abstract that directly supports the "
    "receptor+ligand+function claim. Verbatim quote, ≤ 30 words.\n"
    "- confidence:\n"
    '  * "high" — abstract clearly names receptor, ligand, and at least one of '
    "{{location, cell_type, downstream_pathway, function}}; evidence is direct.\n"
    '  * "medium" — receptor/ligand clear, some fields missing or lightly inferred.\n'
    '  * "low" — receptor unclear, broad review with no focal receptor, or multiple '
    "receptors discussed without a single focal one.\n"
    "- If a field cannot be confirmed, use null AND lower confidence by one step "
    "(high→medium, medium→low, low stays low).\n"
    "\nStandard receptor gene symbols and their families/aliases:\n"
    "{alias_hint}"
)

USER_TEMPLATE = """Input:
PMID: {pmid}
Title: {title}
Abstract: {abstract}

Output the JSON object now."""


SYSTEM_PROMPT_REVIEW = (
    "You are reviewing a previous low-confidence extraction. Re-read the abstract and try to:\n"
    "(a) find a more specific evidence sentence (verbatim, ≤ 30 words),\n"
    "(b) tighten the receptor name and gene symbol,\n"
    "(c) fill in location / cell_type / downstream_pathway / species if mentioned even once.\n"
    "If you cannot improve specificity, keep confidence at \"low\".\n"
    "Output the same 14-field JSON object, no markdown, no explanation."
)


# ---------- JSON 解析 ----------

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)
_JSON_OBJ_RE = re.compile(r"\{.*\}", re.S)


def parse_strict_json(text: str) -> dict | None:
    """从 LLM 输出中提取 JSON 对象。容忍 markdown 围栏。"""
    text = (text or "").strip()
    # 1. 显式 ```json ... ``` 围栏
    m = _JSON_FENCE_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # 2. 第一个 {...} 块
    m = _JSON_OBJ_RE.search(text)
    if m:
        candidate = m.group(0)
        # 简单防越界:从第一个 { 到最后一个 }
        first = candidate.find("{")
        last = candidate.rfind("}")
        if first != -1 and last != -1 and last > first:
            try:
                return json.loads(candidate[first : last + 1])
            except json.JSONDecodeError:
                pass
    return None


# ---------- 字段规整 ----------

REQUIRED_FIELDS = [
    "pmid", "source", "receptor", "receptor_gene", "receptor_family",
    "ligand", "location", "cell_type", "downstream_pathway", "function",
    "species", "literature", "evidence", "confidence",
]

ALLOWED_LIGANDS = {
    "dopamine", "serotonin", "norepinephrine", "epinephrine",
    "norepinephrine/epinephrine", "acetylcholine", "glutamate", "GABA", "histamine",
}
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
    s = _normalize_str(v)
    if not s:
        return None
    s_low = s.lower()
    if s_low in ALLOWED_LIGANDS:
        return s_low if s_low != "norepinephrine" and s_low != "epinephrine" else "norepinephrine/epinephrine"
    # 模糊匹配
    if "dopamine" in s_low:
        return "dopamine"
    if "serotonin" in s_low or "5-ht" in s_low or "5ht" in s_low:
        return "serotonin"
    if "norepinephrine" in s_low or "noradrenalin" in s_low:
        return "norepinephrine/epinephrine"
    if "epinephrine" in s_low or "adrenalin" in s_low:
        return "norepinephrine/epinephrine"
    if "acetylcholine" in s_low or "ach" == s_low:
        return "acetylcholine"
    if "glutamate" in s_low:
        return "glutamate"
    if "gaba" in s_low:
        return "GABA"
    if "histamine" in s_low:
        return "histamine"
    return None


def _strip_html(text: str) -> str:
    """剥 HTML 标签,避免 LLM 输出中残留 <sub>、</sub> 等。"""
    if not text:
        return ""
    # 先剥开标签 <sub>, <i>, <sup> 等
    text = re.sub(r"<[a-zA-Z][^>]*>", "", text)
    # 再剥闭标签 </sub>, </i>, </sup> 等
    text = re.sub(r"</[a-zA-Z][^>]*>", "", text)
    return text


def _truncate_evidence(text: str, max_words: int = 30) -> str:
    """截断 evidence 到 max_words,优先在句号/分号处截断。"""
    words = text.split()
    if len(words) <= max_words:
        return text
    truncated = " ".join(words[:max_words])
    # 尝试在最近句号处截断
    for sep in [". ", "; "]:
        last_sep = truncated.rfind(sep)
        if last_sep > len(truncated) // 2:
            return truncated[: last_sep + 1].strip()
    return truncated


def normalize_record(
    parsed: dict,
    source_pmid: str,
    source_record: dict,
    family_map: dict[str, str] | None = None,
    valid_genes: set[str] | None = None,
) -> dict:
    """把 LLM 输出的 dict 规整到 14 字段,补默认值。"""
    pmid = _normalize_str(parsed.get("pmid")) or source_pmid

    # source: 优先用 PubMed 的 pub_types 判定
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

    receptor = _normalize_str(parsed.get("receptor"))
    if receptor:
        receptor = _strip_html(receptor)

    receptor_gene = _normalize_str(parsed.get("receptor_gene"))
    if receptor_gene:
        receptor_gene = receptor_gene.upper()

    # receptor_gene 白名单校验
    query_gene = _normalize_str(source_record.get("query_receptor_gene"))
    if query_gene:
        query_gene = query_gene.upper()
    if valid_genes and receptor_gene and receptor_gene not in valid_genes:
        if query_gene and query_gene in valid_genes:
            receptor_gene = query_gene
        # mismatch 会在后面标记

    # receptor_family 白名单校验
    receptor_family = _normalize_str(parsed.get("receptor_family"))
    if family_map and receptor_gene and receptor_gene in family_map:
        standard_family = family_map[receptor_gene]
        if receptor_family and receptor_family != standard_family:
            receptor_family = standard_family
        elif not receptor_family:
            receptor_family = standard_family

    ligand = _normalize_ligand(parsed.get("ligand"))
    location = _strip_html(_normalize_str(parsed.get("location")) or "")
    location = location or None
    cell_type = _strip_html(_normalize_str(parsed.get("cell_type")) or "")
    cell_type = cell_type or None
    downstream_pathway = _strip_html(_normalize_str(parsed.get("downstream_pathway")) or "")
    downstream_pathway = downstream_pathway or None
    function = _strip_html(_normalize_str(parsed.get("function")) or "")
    function = function or None
    species = _strip_html(_normalize_str(parsed.get("species")) or "")
    species = species or None
    evidence = _strip_html(_normalize_str(parsed.get("evidence")) or "")
    evidence = _truncate_evidence(evidence) if evidence else None

    confidence = _normalize_str(parsed.get("confidence"))
    if confidence and confidence.lower() in {"high", "medium", "low"}:
        confidence = confidence.lower()
    else:
        confidence = "low"

    # literature: 强制用 PubMed 源数据
    literature = parsed.get("literature") or {}
    if not isinstance(literature, dict):
        literature = {}
    literature_out = {
        "pmid": source_pmid,
        "doi": _normalize_str(literature.get("doi")) or _normalize_str(source_record.get("doi")),
        "title": _normalize_str(source_record.get("title")),
        "year": _normalize_str(source_record.get("year")),
        "journal": _normalize_str(source_record.get("journal")),
    }

    # 核心字段缺失就降档
    missing_core = sum(
        1 for v in (receptor, receptor_gene, ligand, function) if not v
    )
    if missing_core >= 2 and confidence == "high":
        confidence = "medium"
    if missing_core >= 3 and confidence == "medium":
        confidence = "low"

    # query 对照:如果源记录有 query_receptor_gene,记录并标记 mismatch
    mismatch = bool(query_gene) and bool(receptor_gene) and query_gene != receptor_gene
    if mismatch and confidence != "low":
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
        "needs_human_review": confidence == "low" or mismatch,
        "receptor_gene_query": query_gene,
        "receptor_gene_mismatch": mismatch,
    }


# ---------- 调用 ----------

def make_client(api_key: str) -> OpenAI:
    return OpenAI(api_key=api_key, base_url=QWEN_BASE_URL, timeout=60.0)


def call_qwen(client: OpenAI, model: str, system: str, user: str, max_retries: int = 3):
    """调用一次,带限速和指数退避。返回 (raw_text, error_str|None)。"""
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
                max_tokens=1500,
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


# ---------- 主流程 ----------

def load_env() -> tuple[str, str]:
    load_dotenv(REPO_ROOT / "scripts" / ".env")
    api_key = os.getenv("QWEN_API_KEY", "").strip()
    if not api_key:
        sys.stderr.write(
            "缺少 QWEN_API_KEY,先在 scripts/.env 中配置(参考 scripts/.env.qwen.example)\n"
        )
        sys.exit(1)
    model = os.getenv("QWEN_MODEL", "").strip() or DEFAULT_MODEL
    return api_key, model


def load_input(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_output(path: Path) -> dict[str, dict]:
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return {r["pmid"]: r for r in data if "pmid" in r}
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_output(path: Path, by_pmid: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(list(by_pmid.values()), f, ensure_ascii=False, indent=2)


def extract_one(
    client: OpenAI,
    model: str,
    record: dict,
    system_prompt: str,
    prompt_version: str = PROMPT_VERSION,
    family_map: dict[str, str] | None = None,
    valid_genes: set[str] | None = None,
) -> dict:
    """对单条 record 抽 14 字段,带 parse_error / api_error 标记。"""
    pmid = record["pmid"]
    user = USER_TEMPLATE.format(
        pmid=pmid,
        title=record.get("title", ""),
        abstract=record.get("abstract", ""),
    )
    raw, err = call_qwen(client, model, system_prompt, user)
    extracted_at = datetime.now(timezone.utc).isoformat()
    if err and not raw:
        # 兜底
        out = normalize_record({}, pmid, record, family_map, valid_genes)
        out["extraction_meta"] = {
            "model": model,
            "prompt_version": prompt_version,
            "attempt_count": 3,
            "extracted_at": extracted_at,
            "api_error": err,
        }
        out["needs_human_review"] = True
        return out
    parsed = parse_strict_json(raw)
    if not parsed:
        out = normalize_record({}, pmid, record, family_map, valid_genes)
        out["extraction_meta"] = {
            "model": model,
            "prompt_version": prompt_version,
            "attempt_count": 1,
            "extracted_at": extracted_at,
            "parse_error": True,
            "raw_first_300": raw[:300],
        }
        out["needs_human_review"] = True
        return out
    out = normalize_record(parsed, pmid, record, family_map, valid_genes)
    out["extraction_meta"] = {
        "model": model,
        "prompt_version": prompt_version,
        "attempt_count": 1,
        "extracted_at": extracted_at,
    }
    return out


def review_one(
    client: OpenAI,
    model: str,
    record: dict,
    previous: dict,
    system_prompt: str,
    family_map: dict[str, str] | None = None,
    valid_genes: set[str] | None = None,
) -> dict:
    """对 confidence=low 或 mismatch 的记录做二审。"""
    pmid = record["pmid"]
    user = USER_TEMPLATE.format(
        pmid=pmid,
        title=record.get("title", ""),
        abstract=record.get("abstract", ""),
    ) + "\n\nPrevious extraction:\n" + json.dumps(previous, ensure_ascii=False, indent=2)
    raw, err = call_qwen(client, model, SYSTEM_PROMPT_REVIEW, user)
    extracted_at = datetime.now(timezone.utc).isoformat()
    if err and not raw:
        previous["extraction_meta"]["review_attempted_at"] = extracted_at
        previous["extraction_meta"]["review_error"] = err
        return previous
    parsed = parse_strict_json(raw)
    if not parsed:
        previous["extraction_meta"]["review_attempted_at"] = extracted_at
        previous["extraction_meta"]["review_parse_error"] = True
        previous["extraction_meta"]["review_raw_first_300"] = raw[:300]
        return previous
    out = normalize_record(parsed, pmid, record, family_map, valid_genes)
    out["extraction_meta"] = {
        "model": model,
        "prompt_version": PROMPT_VERSION + "-review",
        "attempt_count": 1,
        "extracted_at": extracted_at,
    }
    out["needs_human_review"] = out["confidence"] == "low"
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Qwen 抽 14 字段")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--xlsx", type=Path, default=DEFAULT_XLSX)
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条,0 = 全部")
    parser.add_argument(
        "--two-pass",
        choices=["on", "off"],
        default="on",
        help="对 confidence=low 或 mismatch 的记录做二审",
    )
    parser.add_argument(
        "--force-rerun",
        action="store_true",
        help="忽略已有输出,全部重跑",
    )
    args = parser.parse_args()

    api_key, model = load_env()
    client = make_client(api_key)

    # 加载白名单
    family_map, valid_genes, aliases_map = load_xlsx_metadata(args.xlsx)
    log.info("白名单: %d 个 valid_genes, %d 个 family 映射, %d 个别名组",
             len(valid_genes), len(family_map), len(aliases_map))

    # 构建 system prompt (含别名提示)
    alias_hint = _build_alias_hint(aliases_map)
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(alias_hint=alias_hint)

    records = load_input(args.input)
    log.info("读取 %d 条输入", len(records))
    if args.limit:
        records = records[: args.limit]
        log.info("限制为前 %d 条", len(records))

    if args.force_rerun:
        by_pmid: dict[str, dict] = {}
    else:
        by_pmid = load_output(args.output)
        log.info("已抽取 %d 条 (将跳过)", len(by_pmid))

    todo = [r for r in records if r["pmid"] not in by_pmid]
    log.info("本次待抽 %d 条", len(todo))

    t0 = time.time()
    for i, r in enumerate(todo, 1):
        log.info("[%d/%d] pmid=%s", i, len(todo), r["pmid"])
        out = extract_one(client, model, r, system_prompt, PROMPT_VERSION, family_map, valid_genes)
        by_pmid[r["pmid"]] = out
        save_output(args.output, by_pmid)
        time.sleep(0.5)
    log.info("第一轮完成,耗时 %.1fs", time.time() - t0)

    # 二审: confidence=low 或 receptor_gene_mismatch 的非错误记录
    if args.two_pass == "on":
        review_targets = [
            r for r in records
            if r["pmid"] in by_pmid
            and (
                by_pmid[r["pmid"]].get("confidence") == "low"
                or by_pmid[r["pmid"]].get("receptor_gene_mismatch")
            )
            and not by_pmid[r["pmid"]].get("extraction_meta", {}).get("api_error")
            and not by_pmid[r["pmid"]].get("extraction_meta", {}).get("parse_error")
            and not by_pmid[r["pmid"]].get("extraction_meta", {}).get("review_attempted_at")
        ]
        log.info("二审目标 %d 条", len(review_targets))
        t1 = time.time()
        for i, r in enumerate(review_targets, 1):
            log.info("[review %d/%d] pmid=%s", i, len(review_targets), r["pmid"])
            prev = by_pmid[r["pmid"]]
            out = review_one(client, model, r, prev, system_prompt, family_map, valid_genes)
            by_pmid[r["pmid"]] = out
            save_output(args.output, by_pmid)
            time.sleep(0.5)
        log.info("二审完成,耗时 %.1fs", time.time() - t1)

    save_output(args.output, by_pmid)
    # 简单统计
    n = len(by_pmid)
    conf = {"high": 0, "medium": 0, "low": 0}
    parse_err = api_err = 0
    for v in by_pmid.values():
        c = v.get("confidence", "low")
        conf[c] = conf.get(c, 0) + 1
        if v.get("extraction_meta", {}).get("parse_error"):
            parse_err += 1
        if v.get("extraction_meta", {}).get("api_error"):
            api_err += 1
    log.info("最终 %d 条 | high=%d medium=%d low=%d | parse_err=%d api_err=%d",
             n, conf["high"], conf["medium"], conf["low"], parse_err, api_err)
    log.info("输出: %s", args.output)


if __name__ == "__main__":
    main()
