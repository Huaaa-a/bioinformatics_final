"""
fetch_pubmed_test_set.py
========================

按受体清单从 PubMed 拉取 20-50 篇摘要(每个受体 3-5 篇),
输出到 JSON + CSV。

依赖:biopython, openpyxl, pandas, python-dotenv
运行:python scripts/fetch_pubmed_test_set.py
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import openpyxl
from Bio import Entrez
from dotenv import load_dotenv

# 路径默认在仓库根,可通过 CLI 覆盖
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_XLSX = REPO_ROOT / "receptor_list_classic_neurotransmitter_gpcr.xlsx"
DEFAULT_OUT_DIR = REPO_ROOT / "data"
JSON_PATH = DEFAULT_OUT_DIR / "pubmed_test_set.json"
CSV_PATH = DEFAULT_OUT_DIR / "pubmed_test_set_summary.csv"

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


def load_receptors(xlsx_path: Path) -> list[Receptor]:
    """读取 xlsx 的 included_receptors 表。"""
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


def configure_entrez() -> None:
    """从环境变量或 .env 加载邮箱与 API key。"""
    load_dotenv(REPO_ROOT / "scripts" / ".env")
    email = os.getenv("ENTREZ_EMAIL", "").strip()
    if not email:
        log.error(
            "Entrez 需要邮箱,请设置 ENTREZ_EMAIL 环境变量或在 scripts/.env 中提供"
        )
        sys.exit(1)
    Entrez.email = email

    api_key = os.getenv("ENTREZ_API_KEY", "").strip()
    if api_key:
        Entrez.api_key = api_key
        log.info("已加载 ENTREZ_API_KEY,请求速率 10 req/s")
    else:
        log.info("未提供 ENTREZ_API_KEY,请求速率 3 req/s")


def sleep_for_rate_limit() -> None:
    """根据是否提供 API key 选择暂停时长。"""
    if Entrez.api_key:
        time.sleep(0.1)
    else:
        time.sleep(0.34)


def build_query(genes: list[str]) -> str:
    """按一组基因(同一神经递质系统)构造 esearch 查询,限定近 10 年、非综述。"""
    current_year = datetime.now().year
    gene_clause = " OR ".join(f'"{g}"[Title/Abstract]' for g in genes)
    return (
        f"({gene_clause}) "
        f'AND (GPCR OR "G protein-coupled receptor"[Title/Abstract]) '
        f'AND ("{current_year - 10}/01/01"[Date - Publication] : '
        f'"{current_year}/12/31"[Date - Publication]) '
        f'NOT "Review"[Publication Type]'
    )


def esearch(query: str) -> list[str]:
    """返回 PMID 列表(最多 per_receptor 个)。"""
    handle = Entrez.esearch(
        db="pubmed",
        term=query,
        retmax=20,  # 给 efetch 留余量,实际取前 per_receptor 个
        sort="relevance",
    )
    record = Entrez.read(handle)
    handle.close()
    return list(record.get("IdList", []))


def efetch_abstracts(pmids: list[str]) -> list[dict]:
    """efetch xml 并解析为字段列表。"""
    if not pmids:
        return []
    handle = Entrez.efetch(
        db="pubmed", id=",".join(pmids), rettype="xml", retmode="xml"
    )
    records = Entrez.read(handle)
    handle.close()
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
            " ".join(
                filter(
                    None,
                    [
                        str(a.get("ForeName", "")),
                        str(a.get("LastName", "")),
                    ],
                )
            )
            for a in authors_list
            if a.get("LastName")
        ]
        journal = str(article.get("Journal", {}).get("Title", "")) or ""
        pub_date = article.get("Journal", {}).get("JournalIssue", {}).get(
            "PubDate", {}
        )
        year = str(pub_date.get("Year", "")) or ""
        if not year and pub_date.get("MedlineDate"):
            # e.g. "2022 Jan-Feb"
            year = str(pub_date.get("MedlineDate", "")).split(" ")[0]
        parsed.append(
            {
                "pmid": pmid,
                "title": title,
                "abstract": abstract,
                "authors": authors,
                "journal": journal,
                "year": year,
            }
        )
    return parsed


def load_existing() -> tuple[dict, set]:
    """读取已存在的 JSON,返回 (records_list, known_pmids_set)。"""
    if JSON_PATH.exists():
        try:
            with JSON_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data, {r["pmid"] for r in data if "pmid" in r}
        except (json.JSONDecodeError, OSError) as e:
            log.warning("读取已有 JSON 失败,按空记录继续: %s", e)
    return [], set()


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
        "downloaded",
        "status",
    ]
    with CSV_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def fetch_for_receptor(
    receptor: Receptor, per_receptor: int, known_pmids: set
) -> tuple[list[dict], int, str]:
    """单个受体的检索 + 摘要拉取(兼容旧 CLI,实际 main 已走 per-system 路径)。"""
    return _fetch_and_label([receptor], per_receptor, known_pmids)


def _fetch_and_label(
    receptors: list[Receptor], per_group: int, known_pmids: set
) -> tuple[list[dict], int, str, str]:
    """按一组受体(同一系统)检索,统一打 query_receptor 标签。返回 (records, hits, status, label)。"""
    genes = [r.receptor_gene for r in receptors]
    label = receptors[0].neurotransmitter_system  # 用系统名做日志标签
    try:
        pmids = esearch(build_query(genes))
    except Exception as e:
        log.error("esearch 失败 %s: %s", label, e)
        return [], 0, f"esearch_error: {e}", label
    hits = len(pmids)
    sleep_for_rate_limit()
    if not pmids:
        return [], hits, "no_hits", label

    target_pmids = [p for p in pmids if p not in known_pmids][:per_group]
    skipped_existing = len([p for p in pmids[:per_group] if p in known_pmids])
    if skipped_existing:
        log.info("Skipped %d already-fetched PMIDs for %s", skipped_existing, label)
    if not target_pmids:
        return [], hits, "all_already_fetched", label

    try:
        records = efetch_abstracts(target_pmids)
    except Exception as e:
        log.error("efetch 失败 %s: %s", label, e)
        return [], hits, f"efetch_error: {e}", label
    sleep_for_rate_limit()

    fetched_at = datetime.now(timezone.utc).isoformat()
    # 把每个 PMID 随机分配到一个受体(轮询),保证 query_receptor 字段有具体值
    recs = list(receptors)
    for i, r in enumerate(records):
        owner = recs[i % len(recs)]
        r["query_receptor_gene"] = owner.receptor_gene
        r["query_receptor_name"] = owner.receptor_name
        r["neurotransmitter_system"] = owner.neurotransmitter_system
        r["fetched_at"] = fetched_at
    return records, hits, "ok", label


def main() -> None:
    global JSON_PATH, CSV_PATH, DEFAULT_OUT_DIR
    parser = argparse.ArgumentParser(description="拉取 PubMed 测试文献集")
    parser.add_argument("--xlsx", type=Path, default=DEFAULT_XLSX)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--per-receptor", type=int, default=5,
                        help="每系统抓取篇数(per system),默认 5")
    args = parser.parse_args()

    DEFAULT_OUT_DIR = args.output_dir
    JSON_PATH = DEFAULT_OUT_DIR / "pubmed_test_set.json"
    CSV_PATH = DEFAULT_OUT_DIR / "pubmed_test_set_summary.csv"

    configure_entrez()

    log.info("读取受体清单: %s", args.xlsx)
    receptors = load_receptors(args.xlsx)
    log.info("共 %d 个受体", len(receptors))

    records, known_pmids = load_existing()
    log.info("已有 %d 条历史记录,跳过对应 PMID", len(records))

    # 按系统分组,每个系统一次 esearch + 一次 efetch
    by_system: dict[str, list[Receptor]] = {}
    for r in receptors:
        by_system.setdefault(r.neurotransmitter_system, []).append(r)

    summary_rows: list[dict] = []
    new_total = 0
    for system_name, recs in by_system.items():
        log.info(
            "处理系统 [%d/%d] %s (%d 个受体)",
            len(summary_rows) // max(1, len(recs)) + 1,
            len(by_system),
            system_name,
            len(recs),
        )
        new_recs, hits, status, _ = _fetch_and_label(
            recs, args.per_receptor, known_pmids
        )
        records.extend(new_recs)
        known_pmids.update(r["pmid"] for r in new_recs)
        new_total += len(new_recs)
        # CSV 中按系统合并,status 写最差的一个
        genes = "/".join(r.receptor_gene for r in recs)
        first = recs[0]
        summary_rows.append(
            {
                "neurotransmitter_system": system_name,
                "receptor_gene": genes,
                "receptor_name": first.receptor_name,
                "hits": hits,
                "downloaded": len(new_recs),
                "status": status,
            }
        )

    save_records(records)
    save_summary(summary_rows)
    log.info("本次新增 %d 条,累计 %d 条", new_total, len(records))
    log.info("JSON: %s", JSON_PATH)
    log.info("CSV : %s", CSV_PATH)


if __name__ == "__main__":
    main()
