# 验收清单

## 必做
- [x] `scripts/extract_fields_qwen.py` 在无 QWEN_API_KEY 时给出明确报错
- [x] 主 prompt 输出严格 JSON,无 markdown 围栏
- [x] 14 字段全部出现,值类型与 spec 一致
- [x] 字段缺失时填 `null` 且 confidence 降档
- [x] 断点续跑:第二次跑会跳过已成功的 PMID
- [x] 失败兜底:API / parse 错误不会让整个脚本崩
- [x] confidence 二审 pass 后字段有提升或保持 low+needs_human_review
- [x] `data/pubmed_extracted.json` 121 条,字段与 spec.md 一致
- [x] `extract_run.log` 包含每次请求耗时、token、错误明细
- [x] 加入 `receptor_gene_query` 与 `receptor_gene_mismatch` 标记

## 不做
- [ ] 不并发请求
- [ ] 不做向量 / 知识图谱
- [ ] 不做人工复核(只标 needs_human_review)
