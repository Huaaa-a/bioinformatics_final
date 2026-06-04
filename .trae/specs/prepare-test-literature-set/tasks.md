# Tasks

- [x] Task 1: 准备 Python 运行环境与依赖
  - [x] SubTask 1.1: 创建 `requirements.txt`,写入 `biopython>=1.81`、`openpyxl>=3.1`、`pandas>=2.0`、`python-dotenv>=1.0`
  - [x] SubTask 1.2: 在项目根目录执行 `pip install -r requirements.txt` 安装依赖
  - [x] SubTask 1.3: 创建 `scripts/.env.example` 并在其中写好 `ENTREZ_EMAIL=` 与 `ENTREZ_API_KEY=` 占位符
  - [x] SubTask 1.4: 提示用户提供真实 `ENTREZ_EMAIL`(必填)与 `ENTREZ_API_KEY`(可选),并复制 `scripts/.env.example` 为 `scripts/.env` 后填入

- [x] Task 2: 编写 `scripts/fetch_pubmed_test_set.py`
  - [x] SubTask 2.1: 解析 `receptor_list_classic_neurotransmitter_gpcr.xlsx` 的 `included_receptors` 表,生成 24 个受体的结构化列表
  - [x] SubTask 2.2: 实现 `Entrez` 邮箱/API key 加载与 `Entrez.email`、`Entrez.api_key` 设置
  - [x] SubTask 2.3: 按神经递质系统分组,对每个组构造 `esearch` 查询(`GENE1 OR GENE2 ... AND GPCR ...`),取前 N 个 PMID
  - [x] SubTask 2.4: 用 `efetch(xml)` 拉取摘要,解析为 `pmid / title / abstract / authors / journal / year`
  - [x] SubTask 2.5: 根据是否存在 `ENTREZ_API_KEY` 选择 `time.sleep(0.34)` 或 `time.sleep(0.1)`
  - [x] SubTask 2.6: 追加写入 `data/pubmed_test_set.json` 与 `data/pubmed_test_set_summary.csv`,按 PMID 去重
  - [x] SubTask 2.7: 顶层 `if __name__ == "__main__":` 提供 CLI:`--xlsx`、`--output-dir`、`--per-receptor`(默认 5)

- [x] Task 3: 执行与验证
  - [x] SubTask 3.1: 运行 `python scripts/fetch_pubmed_test_set.py`,观察日志无致命错误
  - [x] SubTask 3.2: 检查 `data/pubmed_test_set.json` 中 `len(records)` 处于 20-50 之间(实际 43),且 7 个系统均覆盖
  - [x] SubTask 3.3: 检查 `data/pubmed_test_set_summary.csv` 中 7 行,均 `status=ok` 或有明确原因
  - [x] SubTask 3.4: 重新运行脚本,确认 "Skipped N already-fetched PMIDs" 日志出现且总下载数不再增加

# Task Dependencies
- Task 2 依赖 Task 1
- Task 3 依赖 Task 2
