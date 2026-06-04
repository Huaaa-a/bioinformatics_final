# Checklist

- [x] `requirements.txt` 存在并包含 `biopython`、`openpyxl`、`pandas`、`python-dotenv`
- [x] `scripts/fetch_pubmed_test_set.py` 实现并能成功执行
- [x] 脚本能正确读取 xlsx 的 24 个受体,无空字段
- [x] 每个系统下载 3-5 篇摘要(实际 7 系统,43 条,每系统 3-8 篇)
- [x] 限速正确(无 API key 时 0.34s/次)
- [x] 缺 `ENTREZ_EMAIL` 时脚本退出并打印明确错误
- [x] 缺 `ENTREZ_API_KEY` 时仅打印提示,继续运行
- [x] `data/pubmed_test_set.json` 结构符合 spec(包含 `pmid`、`title`、`abstract`、`authors`、`journal`、`year`、`query_receptor_gene`、`query_receptor_name`、`neurotransmitter_system`、`fetched_at`)
- [x] `data/pubmed_test_set_summary.csv` 包含 7 行(每行一个系统)
- [x] 重复运行脚本不会产生重复 PMID(日志出现 `Skipped N already-fetched PMIDs`)
- [x] 7 个神经递质系统(dopamine / serotonin / adrenergic / muscarinic acetylcholine / metabotropic glutamate / GABA_B / histamine)均有命中
- [x] 12 个目标抽取字段已记录到 spec.md 供下一阶段 LLM 抽取使用
