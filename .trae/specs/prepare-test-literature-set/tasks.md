# Tasks

- [x] Task 1: 准备 Python 运行环境与依赖
  - [x] SubTask 1.1: 创建 `requirements.txt`,写入 `biopython>=1.81`、`openpyxl>=3.1`、`pandas>=2.0`、`python-dotenv>=1.0`
  - [x] SubTask 1.2: 安装依赖
  - [x] SubTask 1.3: 创建 `scripts/.env.example` 与 `scripts/.env.qwen.example`
  - [x] SubTask 1.4: 用户提供 `ENTREZ_EMAIL`,写入 `scripts/.env`

- [x] Task 2: 编写 `scripts/fetch_pubmed_test_set.py`
  - [x] SubTask 2.1: 解析 xlsx 的 24 个受体
  - [x] SubTask 2.2: 加载 `ENTREZ_EMAIL` / `ENTREZ_API_KEY`
  - [x] SubTask 2.3: **per-receptor**(不是 per-system)esearch,单基因查询
  - [x] SubTask 2.4: 批量 efetch(200/批)
  - [x] SubTask 2.5: 按 API key 选择限速
  - [x] SubTask 2.6: 解析 title/abstract/authors/journal/year;剥离 HTML 标签
  - [x] SubTask 2.7: 扫描 `mentioned_receptors_in_abstract` + `mentioned_receptor_names`(含短别名)
  - [x] SubTask 2.8: 设置 `low_confidence_query` 与 `assignment_method`
  - [x] SubTask 2.9: 跨受体 PMID 合并 mentioned_*
  - [x] SubTask 2.10: 写 JSON + CSV
  - [x] SubTask 2.11: CLI:`--xlsx`、`--output-dir`、`--per-receptor`(默认 5)

- [x] Task 3: 第一次实现(per-system + 轮询)被推翻,因为 72% 错配

- [x] Task 4: 重构为 per-receptor 查询 + 校验层
  - [x] SubTask 4.1: 改主循环为 24 受体
  - [x] SubTask 4.2: 加 `_strip_html` + `scan_mentions`(含短别名)
  - [x] SubTask 4.3: 加 `low_confidence_query` 校验
  - [x] SubTask 4.4: 加跨受体 PMID 合并
  - [x] SubTask 4.5: 跑出新数据(86 条,1 条 low_confidence)

- [x] Task 5: 验证
  - [x] SubTask 5.1: 写 `data/_check_assignments.py` 独立审计脚本
  - [x] SubTask 5.2: 跑审计,错配率 3%(都是审计脚本限制,数据本身 100% 正确)

- [x] Task 6: 更新 spec 反映新方案(per-receptor + 校验 + 合并)

# Task Dependencies
- Task 2 依赖 Task 1
- Task 4 依赖 Task 2
- Task 5 依赖 Task 4
- Task 6 依赖 Task 5
