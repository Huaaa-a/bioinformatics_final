"""
fetch_pubmed_test_set.py
========================

从 PubMed 按 24 个神经递质 GPCR 受体逐个拉取摘要,保证每条记录
的 `query_receptor_gene` 与搜索词一致(避免之前的轮询错配)。

数据流:
  for receptor in 24:
    pmids = esearch("(gene OR alias1 ...)[Title/Abstract] AND GPCR ... (保留 Review)")
    new = filter(pmids not in known_pmids)[:per_receptor]
    records = efetch_batched(new, batch=200)         # 批量 efetch
    for r in records:
        r["query_receptor_gene"] = receptor.gene    # = 搜索词,天然正确
        r["mentioned_receptors_in_abstract"] = ...  # 扫描 abstract
        r["mentioned_receptor_names"]      = ...
        r["mentioned_ligands_in_abstract"] = ...    # canonical ligand 扫描
        r["low_confidence_query"]          = ...
        r["assignment_method"]             = "per_receptor_search"
        # 若 PMID 已被其他受体先抓到,合并 mentioned_*,不重复入库

依赖:biopython, openpyxl, python-dotenv
运行:python scripts/fetch_pubmed_test_set.py
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import openpyxl
from Bio import Entrez
from dotenv import load_dotenv

# 路径默认在仓库根
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_XLSX = REPO_ROOT / "receptor_list_classic_neurotransmitter_gpcr.xlsx"
DEFAULT_OUT_DIR = REPO_ROOT / "data"
JSON_PATH = DEFAULT_OUT_DIR / "pubmed_test_set.json"
CSV_PATH = DEFAULT_OUT_DIR / "pubmed_test_set_summary.csv"

EFETCH_BATCH = 200  # NCBI 单次 efetch 的稳定上限

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("fetch_pubmed")


@dataclass
class Receptor:
    neurotransmitter_system: str
    ligand: str
    receptor_gene: str
    receptor_name: str
    common_aliases: str
    receptor_family: str
    source_url: str


# ---------- 受体清单 ----------

def load_receptors(xlsx_path: Path) -> list[Receptor]:
    if not xlsx_path.exists():
        raise FileNotFoundError(f"找不到 xlsx: {xlsx_path}")
    wb = openpyxl.load_workbook(xlsx_path, data_only=True, read_only=True)
    ws = wb["included_receptors"]
    rows = ws.iter_rows(values_only=True)
    header = next(rows)
    idx = {name: i for i, name in enumerate(header)}
    receptors: list[Receptor] = []
    for row in rows:
        if not row or row[idx["receptor_gene"]] is None:
            continue
        receptors.append(
            Receptor(
                neurotransmitter_system=row[idx["neurotransmitter_system"]] or "",
                ligand=row[idx["ligand"]] or "",
                receptor_gene=row[idx["receptor_gene"]] or "",
                receptor_name=row[idx["receptor_name"]] or "",
                common_aliases=row[idx["common_aliases"]] or "",
                receptor_family=row[idx["receptor_family"]] or "",
                source_url=row[idx["source_url"]] or "",
            )
        )
    wb.close()
    return receptors


# ---------- Entrez 配置 ----------

def configure_entrez() -> None:
    load_dotenv(REPO_ROOT / "scripts" / ".env")
    email = os.getenv("ENTREZ_EMAIL", "").strip()
    if not email:
        log.error("Entrez 需要邮箱,请设置 ENTREZ_EMAIL 环境变量或在 scripts/.env 中提供")
        sys.exit(1)
    Entrez.email = email

    api_key = os.getenv("ENTREZ_API_KEY", "").strip()
    if api_key:
        Entrez.api_key = api_key
        log.info("已加载 ENTREZ_API_KEY,请求速率 10 req/s")
    else:
        log.info("未提供 ENTREZ_API_KEY,请求速率 3 req/s")


def sleep_for_rate_limit() -> None:
    if Entrez.api_key:
        time.sleep(0.1)
    else:
        time.sleep(0.34)


# ---------- 查询构造 ----------

# canonical ligand 名(由 spec 规范化)→ 出现在文本中的写法(用于文本扫描)
# 注意:scan 只在 title+abstract 里找,大小写不敏感,别用过于宽泛的词
CANONICAL_LIGAND_PATTERNS: dict[str, list[str]] = {
    "dopamine": [r"\bdopamine", r"\bdopaminergic"],
    "serotonin": [r"\bserotonin", r"\bserotonergic", r"5-HT(?![0-9])"],  # 5-HT 单独,后面不带数字
    "norepinephrine/epinephrine": [
        r"\bnorepinephrine", r"\bnoradrenaline", r"\bepinephrine",
        r"\badrenaline", r"\badrenergic",
    ],
    "acetylcholine": [r"\bacetylcholine", r"\bACh\b", r"\bcholinergic"],
    "glutamate": [r"\bglutamate", r"\bglutamatergic"],
    "GABA": [r"\bGABA\b", r"\bGABAergic", r"gamma-aminobutyric acid"],
    "histamine": [r"\bhistamine", r"\bhistaminergic"],
}

# 受体基因 → canonical ligand(spec 中 14 字段 ligand 的取值)
GENE_TO_CANONICAL_LIGAND: dict[str, str] = {
    # dopamine
    "DRD1": "dopamine", "DRD2": "dopamine", "DRD3": "dopamine",
    "DRD4": "dopamine", "DRD5": "dopamine",
    # serotonin
    "HTR1A": "serotonin", "HTR1B": "serotonin", "HTR1D": "serotonin",
    "HTR1E": "serotonin", "HTR1F": "serotonin",
    "HTR2A": "serotonin", "HTR2B": "serotonin", "HTR2C": "serotonin",
    "HTR4": "serotonin", "HTR5A": "serotonin", "HTR6": "serotonin", "HTR7": "serotonin",
    # adrenergic
    "ADRA1A": "norepinephrine/epinephrine", "ADRA1B": "norepinephrine/epinephrine",
    "ADRA1D": "norepinephrine/epinephrine",
    "ADRA2A": "norepinephrine/epinephrine", "ADRA2B": "norepinephrine/epinephrine",
    "ADRA2C": "norepinephrine/epinephrine",
    "ADRB1": "norepinephrine/epinephrine", "ADRB2": "norepinephrine/epinephrine", "ADRB3": "norepinephrine/epinephrine",
    # muscarinic acetylcholine
    "CHRM1": "acetylcholine", "CHRM2": "acetylcholine", "CHRM3": "acetylcholine",
    "CHRM4": "acetylcholine", "CHRM5": "acetylcholine",
    # metabotropic glutamate
    "GRM1": "glutamate", "GRM2": "glutamate", "GRM3": "glutamate", "GRM4": "glutamate",
    "GRM5": "glutamate", "GRM6": "glutamate", "GRM7": "glutamate", "GRM8": "glutamate",
    # GABA_B
    "GABBR1": "GABA", "GABBR2": "GABA",
    # histamine
    "HRH1": "histamine", "HRH2": "histamine", "HRH3": "histamine", "HRH4": "histamine",
}


def build_query(gene: str, aliases: list[str] | None = None) -> str:
    """单受体查询:基因 + 别名 OR 拼接,近 10 年,GPCR 上下文,不再排除 Review。

    aliases 来自 xlsx `common_aliases` 列(以 ';' 分隔),为空时只 query gene。
    """
    current_year = datetime.now().year
    terms: list[str] = [gene]
    if aliases:
        for a in aliases:
            a = a.strip()
            if a and a.lower() != gene.lower():
                terms.append(a)
    # 把每个 term 包成 "[Title/Abstract]" 形式,多词/带连字符/希腊字母都加引号
    quoted = " OR ".join(f'"{t}"[Title/Abstract]' for t in terms)
    return (
        f'({quoted}) '
        f'AND (GPCR OR "G protein-coupled receptor"[Title/Abstract]) '
        f'AND ("{current_year - 10}/01/01"[Date - Publication] : '
        f'"{current_year}/12/31"[Date - Publication])'
    )


def esearch(query: str, retmax: int) -> list[str]:
    handle = Entrez.esearch(db="pubmed", term=query, retmax=retmax, sort="relevance")
    record = Entrez.read(handle)
    handle.close()
    return list(record.get("IdList", []))


# ---------- 摘要解析 ----------

def efetch_abstracts(pmids: list[str]) -> list[dict]:
    """单批 efetch(xml),解析为字段列表。"""
    if not pmids:
        return []
    handle = Entrez.efetch(
        db="pubmed", id=",".join(pmids), rettype="xml", retmode="xml"
    )
    records = Entrez.read(handle)
    handle.close()
    return _parse_articles(records)


def _parse_articles(records) -> list[dict]:
    parsed: list[dict] = []
    for art in records.get("PubmedArticle", []):
        medline = art.get("MedlineCitation", {})
        article = medline.get("Article", {})
        pmid = str(medline.get("PMID", ""))
        title = str(article.get("ArticleTitle", "")) or ""
        abstract_parts = article.get("Abstract", {}).get("AbstractText", [])
        if abstract_parts:
            abstract = " ".join(str(p) for p in abstract_parts)
        else:
            abstract = ""
        authors_list = article.get("AuthorList", [])
        authors = [
            " ".join(filter(None, [str(a.get("ForeName", "")), str(a.get("LastName", ""))]))
            for a in authors_list
            if a.get("LastName")
        ]
        journal = str(article.get("Journal", {}).get("Title", "")) or ""
        pub_date = article.get("Journal", {}).get("JournalIssue", {}).get("PubDate", {})
        year = str(pub_date.get("Year", "")) or ""
        if not year and pub_date.get("MedlineDate"):
            year = str(pub_date.get("MedlineDate", "")).split(" ")[0]
        # 提取 DOI
        doi = ""
        article_ids = art.get("PubmedData", {}).get("ArticleIdList", [])
        for aid in article_ids:
            if str(aid.attributes.get("IdType", "")) == "doi":
                doi = str(aid)
                break
        # 提取文献类型
        pub_type_list = medline.get("Article", {}).get("PublicationTypeList", [])
        pub_types = [str(pt) for pt in pub_type_list]
        is_review = any("review" in pt.lower() or "meta-analysis" in pt.lower() for pt in pub_types)
        parsed.append(
            {
                "pmid": pmid,
                "title": title,
                "abstract": abstract,
                "authors": authors,
                "journal": journal,
                "year": year,
                "doi": doi or None,
                "pub_types": pub_types,
                "is_review": is_review,
            }
        )
    return parsed


def efetch_batched(pmids: list[str], batch: int = EFETCH_BATCH) -> list[dict]:
    """分批 efetch。NCBI 单批 ID 数过多会 414/400,这里用 200 截断。"""
    out: list[dict] = []
    for i in range(0, len(pmids), batch):
        chunk = pmids[i : i + batch]
        out.extend(efetch_abstracts(chunk))
        if i + batch < len(pmids):
            sleep_for_rate_limit()
    return out


# ---------- 文本扫描:在 abstract 中找 24 个受体 ----------

def _strip_html(text: str) -> str:
    """把 <sub>HR</sub>H2</sub> 这类 PubMed XML 残存的 HTML 标签剥掉,避免打断匹配。

    注意:只剥真正的 HTML 标签(以字母开头、标签内不含数学符号),避免误删
    "P < 0.05" / "(>35 years)" 这类统计表达式里的尖括号。
    """
    if not text:
        return ""
    return re.sub(r"<[a-zA-Z][^>]*>", "", text)


def scan_mentions(text: str, all_genes: list[str], all_names: list[str], all_aliases: dict[str, list[str]] | None = None) -> tuple[list[str], list[str]]:
    """在 text 中扫描所有 24 个基因符号 + 受体全名 + 常见短别名 + xlsx 别名。"""
    text_clean = _strip_html(text or "")
    text_low = text_clean.lower()
    # 把 "HRH-4" / "DRD-2" 这种带连字符的写法归一为 "HRH4" / "DRD2",再做词边界匹配
    text_norm = re.sub(r"-", "", text_clean)
    genes: list[str] = []
    for g in all_genes:
        if re.search(rf"\b{re.escape(g)}\b", text_norm, re.IGNORECASE) and g not in genes:
            genes.append(g)
    names: list[str] = []
    for n in all_names:
        n_l = n.lower()
        if n_l and n_l in text_low and n not in names:
            names.append(n)
    # 额外:由基因符号派生的常见短别名("5-HT1A" / "H1 receptor" 等),用原文本(保留连字符和空格)匹配
    gene_to_short = {
        "HTR1A": "5-HT1A", "HTR2A": "5-HT2A", "HTR2C": "5-HT2C", "HTR7": "5-HT7",
        "HRH1": "H1 receptor", "HRH2": "H2 receptor",
        "HRH3": "H3 receptor", "HRH4": "H4 receptor",
        "CHRM1": "M1 receptor", "CHRM2": "M2 receptor",
        "CHRM3": "M3 receptor", "CHRM4": "M4 receptor",
    }
    for g, short in gene_to_short.items():
        if g in genes and short.lower() in text_low and short not in names:
            names.append(short)
    # xlsx 中的 common_aliases
    if all_aliases:
        for g, aliases in all_aliases.items():
            if g in genes:
                for alias in aliases:
                    if alias and alias.lower() in text_low and alias not in names:
                        names.append(alias)
    return genes, names


def scan_ligands(text: str) -> list[str]:
    """在 text 中扫 spec 规定的 canonical ligand 名,返回标准名列表(保序去重)。

    canonical ligand 名(标准值)= {dopamine, serotonin, norepinephrine/epinephrine,
    acetylcholine, glutamate, GABA, histamine}。
    """
    if not text:
        return []
    text_clean = _strip_html(text)
    found: list[str] = []
    for canon, patterns in CANONICAL_LIGAND_PATTERNS.items():
        for p in patterns:
            if re.search(p, text_clean, re.IGNORECASE):
                if canon not in found:
                    found.append(canon)
                break
    return found


# ---------- 持久化 ----------

def load_existing() -> tuple[list[dict], dict[str, dict]]:
    """读取已存在的 JSON,返回 (records_list, by_pmid_dict)。"""
    if JSON_PATH.exists():
        try:
            with JSON_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                by_pmid = {r["pmid"]: r for r in data if "pmid" in r}
                return data, by_pmid
        except (json.JSONDecodeError, OSError) as e:
            log.warning("读取已有 JSON 失败,按空记录继续: %s", e)
    return [], {}


def save_records(records: list[dict]) -> None:
    DEFAULT_OUT_DIR.mkdir(parents=True, exist_ok=True)
    with JSON_PATH.open("w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def save_summary(rows: list[dict]) -> None:
    DEFAULT_OUT_DIR.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "neurotransmitter_system",
        "receptor_gene",
        "receptor_name",
        "hits",
        "new_pmids",
        "downloaded",
        "low_confidence_count",
        "status",
    ]
    with CSV_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# ---------- 单受体抓取 ----------

def fetch_for_receptor(
    receptor: Receptor,
    per_receptor: int,
    by_pmid: dict[str, dict],
    all_genes: list[str],
    all_names: list[str],
    all_aliases: dict[str, list[str]] | None = None,
) -> tuple[list[dict], int, int, int, str]:
    """单受体 esearch + efetch,带校验。

    返回 (new_records, hits_count, new_pmid_count, low_conf_count, status)。
    若 PMID 已被其他受体先抓过,合并 mentioned_* 字段,不重复入库。
    """
    label = receptor.receptor_gene
    # 从 all_aliases 拿当前受体的 alias 列表
    aliases_for_query = (all_aliases or {}).get(receptor.receptor_gene, [])
    try:
        pmids = esearch(build_query(receptor.receptor_gene, aliases_for_query), retmax=per_receptor * 2)
    except Exception as e:
        log.error("esearch 失败 %s: %s", label, e)
        return [], 0, 0, 0, f"esearch_error: {e}"

    hits = len(pmids)
    sleep_for_rate_limit()
    if not pmids:
        return [], hits, 0, 0, "no_hits"

    known = set(by_pmid.keys())
    target_pmids = [p for p in pmids if p not in known][:per_receptor]
    skipped = len([p for p in pmids[:per_receptor] if p in known])
    if skipped:
        log.info("Skipped %d already-fetched PMIDs for %s", skipped, label)
    if not target_pmids:
        return [], hits, 0, 0, "all_already_fetched"

    try:
        records = efetch_batched(target_pmids)
    except Exception as e:
        log.error("efetch 失败 %s: %s", label, e)
        return [], hits, 0, 0, f"efetch_error: {e}"
    sleep_for_rate_limit()

    fetched_at = datetime.now(timezone.utc).isoformat()
    canonical_for_this = GENE_TO_CANONICAL_LIGAND.get(receptor.receptor_gene)
    new_records: list[dict] = []
    low_conf = 0
    for r in records:
        combined = _strip_html((r.get("title") or "") + " " + (r.get("abstract") or ""))
        genes_found, names_found = scan_mentions(combined, all_genes, all_names, all_aliases)
        ligands_found = scan_ligands(combined)
        in_abstract = (
            receptor.receptor_gene in genes_found
            or receptor.receptor_name in names_found
        )
        if not in_abstract:
            low_conf += 1
        r["query_receptor_gene"] = receptor.receptor_gene
        r["query_receptor_name"] = receptor.receptor_name
        r["neurotransmitter_system"] = receptor.neurotransmitter_system
        r["fetched_at"] = fetched_at
        r["mentioned_receptors_in_abstract"] = genes_found
        r["mentioned_receptor_names"] = names_found
        r["mentioned_ligands_in_abstract"] = ligands_found
        r["canonical_ligand_for_query_receptor"] = canonical_for_this
        r["canonical_ligand_mentioned"] = (
            canonical_for_this in ligands_found if canonical_for_this else None
        )
        r["low_confidence_query"] = not in_abstract
        r["assignment_method"] = "per_receptor_search"
        new_records.append(r)
    return new_records, hits, len(target_pmids), low_conf, "ok"


# ---------- 主流程 ----------

def main() -> None:
    global JSON_PATH, CSV_PATH, DEFAULT_OUT_DIR
    parser = argparse.ArgumentParser(description="按受体逐个从 PubMed 拉取摘要")
    parser.add_argument("--xlsx", type=Path, default=DEFAULT_XLSX)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--per-receptor",
        type=int,
        default=5,
        help="每个受体最多下载的 PMID 数(去重后),默认 5",
    )
    args = parser.parse_args()

    DEFAULT_OUT_DIR = args.output_dir
    JSON_PATH = DEFAULT_OUT_DIR / "pubmed_test_set.json"
    CSV_PATH = DEFAULT_OUT_DIR / "pubmed_test_set_summary.csv"

    configure_entrez()

    log.info("读取受体清单: %s", args.xlsx)
    receptors = load_receptors(args.xlsx)
    log.info("共 %d 个受体", len(receptors))

    all_genes = [r.receptor_gene for r in receptors]
    all_names = [r.receptor_name for r in receptors]
    all_aliases = {
        r.receptor_gene: [a.strip() for a in r.common_aliases.split(";") if a.strip()]
        for r in receptors if r.common_aliases
    }

    records, by_pmid = load_existing()
    log.info("已有 %d 条历史记录", len(records))

    summary_rows: list[dict] = []
    new_total = 0
    low_conf_total = 0
    for idx, rec in enumerate(receptors, start=1):
        log.info(
            "处理受体 [%d/%d] %s (%s)",
            idx,
            len(receptors),
            rec.receptor_gene,
            rec.neurotransmitter_system,
        )
        new_recs, hits, new_pmid_n, low_conf_n, status = fetch_for_receptor(
            rec, args.per_receptor, by_pmid, all_genes, all_names, all_aliases
        )

        for r in new_recs:
            pmid = r["pmid"]
            if pmid in by_pmid:
                # 跨受体:合并 mentioned_*,保留首次 query_receptor_gene
                existing = by_pmid[pmid]
                merged_genes = list(
                    dict.fromkeys(
                        existing.get("mentioned_receptors_in_abstract", [])
                        + r["mentioned_receptors_in_abstract"]
                    )
                )
                merged_names = list(
                    dict.fromkeys(
                        existing.get("mentioned_receptor_names", [])
                        + r["mentioned_receptor_names"]
                    )
                )
                merged_ligands = list(
                    dict.fromkeys(
                        existing.get("mentioned_ligands_in_abstract", [])
                        + r.get("mentioned_ligands_in_abstract", [])
                    )
                )
                existing["mentioned_receptors_in_abstract"] = merged_genes
                existing["mentioned_receptor_names"] = merged_names
                existing["mentioned_ligands_in_abstract"] = merged_ligands
                # 如果原记录的 query_receptor_gene 现在在合并后的基因列表中了,取消 low_confidence_query
                if existing.get("query_receptor_gene") in merged_genes and existing.get("low_confidence_query"):
                    existing["low_confidence_query"] = False
                # 重新评估 canonical_ligand_mentioned
                canonical_existing = existing.get("canonical_ligand_for_query_receptor")
                if canonical_existing:
                    existing["canonical_ligand_mentioned"] = canonical_existing in merged_ligands
                log.info(
                    "  -> PMID %s 已存在,合并 mentioned_* (新增基因 %s)",
                    pmid,
                    r["query_receptor_gene"],
                )
            else:
                records.append(r)
                by_pmid[pmid] = r
                new_total += 1
        low_conf_total += low_conf_n

        summary_rows.append(
            {
                "neurotransmitter_system": rec.neurotransmitter_system,
                "receptor_gene": rec.receptor_gene,
                "receptor_name": rec.receptor_name,
                "hits": hits,
                "new_pmids": new_pmid_n,
                "downloaded": len(new_recs),
                "low_confidence_count": low_conf_n,
                "status": status,
            }
        )

    save_records(records)
    save_summary(summary_rows)
    log.info("本次新增 %d 条,累计 %d 条", new_total, len(records))
    log.info("其中 low_confidence_query=%d 条", low_conf_total)
    log.info("JSON: %s", JSON_PATH)
    log.info("CSV : %s", CSV_PATH)


if __name__ == "__main__":
    main()
