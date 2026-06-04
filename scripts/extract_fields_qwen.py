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

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = REPO_ROOT / "data" / "pubmed_test_set.json"
DEFAULT_OUTPUT = REPO_ROOT / "data" / "pubmed_extracted.json"
DEFAULT_LOG = REPO_ROOT / "data" / "extract_run.log"

QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen-plus"
PROMPT_VERSION = "v1"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("extract_qwen")

# ---------- Prompt ----------

SYSTEM_PROMPT = (
    "You are a biomedical knowledge extractor for a neurotransmitter GPCR database. "
    "Read the PubMed title+abstract and output ONE strict JSON object with exactly these 14 fields: "
    "pmid, source, receptor, receptor_gene, receptor_family, ligand, location, cell_type, "
    "downstream_pathway, function, species, literature, evidence, confidence.\n"
    "Strict rules:\n"
    "- Output JSON only. No markdown, no ```json fences, no explanation before or after.\n"
    "- source ∈ {\"review\", \"original_research\"}.\n"
    "- receptor_gene MUST be a HGNC-style gene symbol (e.g. DRD1, HTR2A, CHRM3, GRM5).\n"
    "- ligand ∈ one of {dopamine, serotonin, norepinephrine/epinephrine, acetylcholine, "
    "glutamate, GABA, histamine} or null.\n"
    "- location / cell_type / downstream_pathway / function / species: short noun phrases; null if absent.\n"
    "- literature = {pmid, doi, title, year, journal}; doi is null if unknown.\n"
    "- evidence = the SHORTEST sentence in the abstract that directly supports the "
    "receptor+ligand+function claim. Verbatim quote, ≤ 30 words.\n"
    "- confidence:\n"
    '  * "high" — abstract clearly names receptor, ligand, and at least one of '
    "{location, cell_type, downstream_pathway, function}; evidence is direct.\n"
    '  * "medium" — receptor/ligand clear, some fields missing or lightly inferred.\n'
    '  * "low" — receptor unclear, broad review with no focal receptor, or multiple '
    "receptors discussed without a single focal one.\n"
    "- If a field cannot be confirmed, use null AND lower confidence by one step "
    "(high→medium, medium→low, low stays low)."
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


def normalize_record(parsed: dict, source_pmid: str, source_record: dict) -> dict:
    """把 LLM 输出的 dict 规整到 14 字段,补默认值。"""
    pmid = _normalize_str(parsed.get("pmid")) or source_pmid
    source = _normalize_str(parsed.get("source"))
    if source and source.lower() in {"review", "review_article"}:
        source = "review"
    elif source and source.lower() in {"original_research", "research", "article", "original"}:
        source = "original_research"
    else:
        source = None

    receptor = _normalize_str(parsed.get("receptor"))
    receptor_gene = _normalize_str(parsed.get("receptor_gene"))
    if receptor_gene:
        receptor_gene = receptor_gene.upper()
    receptor_family = _normalize_str(parsed.get("receptor_family"))
    ligand = _normalize_ligand(parsed.get("ligand"))
    location = _normalize_str(parsed.get("location"))
    cell_type = _normalize_str(parsed.get("cell_type"))
    downstream_pathway = _normalize_str(parsed.get("downstream_pathway"))
    function = _normalize_str(parsed.get("function"))
    species = _normalize_str(parsed.get("species"))
    evidence = _normalize_str(parsed.get("evidence"))
    if evidence and len(evidence.split()) > 35:
        evidence = " ".join(evidence.split()[:30])

    confidence = _normalize_str(parsed.get("confidence"))
    if confidence and confidence.lower() in {"high", "medium", "low"}:
        confidence = confidence.lower()
    else:
        confidence = "low"

    literature = parsed.get("literature") or {}
    if not isinstance(literature, dict):
        literature = {}
    literature_out = {
        "pmid": _normalize_str(literature.get("pmid")) or source_pmid,
        "doi": _normalize_str(literature.get("doi")),
        "title": _normalize_str(literature.get("title")) or _normalize_str(source_record.get("title")),
        "year": _normalize_str(literature.get("year")) or _normalize_str(source_record.get("year")),
        "journal": _normalize_str(literature.get("journal")) or _normalize_str(source_record.get("journal")),
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
    query_gene = _normalize_str(source_record.get("query_receptor_gene"))
    if query_gene:
        query_gene = query_gene.upper()
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
    prompt_version: str = PROMPT_VERSION,
) -> dict:
    """对单条 record 抽 14 字段,带 parse_error / api_error 标记。"""
    pmid = record["pmid"]
    user = USER_TEMPLATE.format(
        pmid=pmid,
        title=record.get("title", ""),
        abstract=record.get("abstract", ""),
    )
    raw, err = call_qwen(client, model, SYSTEM_PROMPT, user)
    extracted_at = datetime.now(timezone.utc).isoformat()
    if err and not raw:
        # 兜底
        out = normalize_record({}, pmid, record)
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
        out = normalize_record({}, pmid, record)
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
    out = normalize_record(parsed, pmid, record)
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
) -> dict:
    """对 confidence=low 的非错误记录做二审。"""
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
    out = normalize_record(parsed, pmid, record)
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
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条,0 = 全部")
    parser.add_argument(
        "--two-pass",
        choices=["on", "off"],
        default="on",
        help="对 confidence=low 的记录做二审",
    )
    parser.add_argument(
        "--force-rerun",
        action="store_true",
        help="忽略已有输出,全部重跑",
    )
    args = parser.parse_args()

    api_key, model = load_env()
    client = make_client(api_key)

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
        out = extract_one(client, model, r)
        by_pmid[r["pmid"]] = out
        save_output(args.output, by_pmid)
        time.sleep(0.5)
    log.info("第一轮完成,耗时 %.1fs", time.time() - t0)

    # 二审
    if args.two_pass == "on":
        review_targets = [
            r for r in records
            if r["pmid"] in by_pmid
            and by_pmid[r["pmid"]].get("confidence") == "low"
            and not by_pmid[r["pmid"]].get("extraction_meta", {}).get("api_error")
            and not by_pmid[r["pmid"]].get("extraction_meta", {}).get("parse_error")
            and not by_pmid[r["pmid"]].get("extraction_meta", {}).get("review_attempted_at")
        ]
        log.info("二审目标 %d 条", len(review_targets))
        t1 = time.time()
        for i, r in enumerate(review_targets, 1):
            log.info("[review %d/%d] pmid=%s", i, len(review_targets), r["pmid"])
            prev = by_pmid[r["pmid"]]
            out = review_one(client, model, r, prev)
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
