# 任务清单

- [x] T1 写 `scripts/extract_fields_qwen.py`
  - [x] 读 `data/pubmed_test_set.json` 与 `scripts/.env` 的 `QWEN_API_KEY`
  - [x] OpenAI 兼容调用 qwen-plus,严格 JSON 解析
  - [x] 限速 0.5s/次 + 指数退避重试
  - [x] 断点续跑:已抽取的 PMID 跳过
  - [x] 写 `data/pubmed_extracted.json` + `data/extract_run.log`
- [x] T2 先在 5 条样本上试跑,人工 review 字段质量
- [x] T3 全量跑 121 条
- [x] T4 二审:对 `confidence=low` 的记录换详细 prompt 重跑
- [x] T5 统计:字段非空率、confidence 分布、parse_error 率
- [x] T6 加入 `receptor_gene_query` 对照 + mismatch 标记
- [ ] T7 提交并推送 GitHub
