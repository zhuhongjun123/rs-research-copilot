# 第三方依赖许可清单

本项目的代码以 `LICENSE`（MIT）授权。**第三方依赖各自适用其自身许可**，
下表是其声明值（来源：各依赖的包元数据 / 官方仓库）。

⚠️ **本清单未逐条人工核验上游 LICENSE 原文**，仅用于快速筛查；
对外分发前建议对关键依赖复核。

| 依赖 | 用途 | 许可（据上游声明） |
| --- | --- | --- |
| `langgraph` | 审查链编排 | MIT |
| `langchain-core` | 被 langgraph 依赖 | MIT |
| `openai` | LLM / Embedding 客户端 | Apache-2.0 |
| `fastapi` | HTTP 服务 | MIT |
| `uvicorn` | ASGI 服务器 | BSD-3-Clause |
| `httpx` | 测试客户端 | BSD-3-Clause |
| `faiss-cpu` | 向量检索（预留） | MIT |
| `rank-bm25` | 词法检索 | Apache-2.0 |
| `sentence-transformers` | 本地 embedding 兜底 | Apache-2.0 |
| `pyzotero` | Zotero 本地 API 客户端 | Blue Oak Model License 1.0.0 |
| `requests` | HTTP | Apache-2.0 |
| `PyYAML` | 配置 | MIT |
| `pytest` | 测试 | MIT |
| `ruff` | 静态检查 | MIT |

## parser 环境（独立）

| 依赖 | 用途 | 许可（据上游声明） |
| --- | --- | --- |
| `docling` | PDF → 结构化（页码 / 表格 / caption） | MIT |

## 明确**未**使用的依赖

| 依赖 | 为何不用 |
| --- | --- |
| **PyMuPDF** | **AGPL-3.0** —— 会污染本项目的开源许可选择。只用它做过一次性调试，不进依赖 |

## 数据来源

本项目**不分发任何论文原文**：

- 只读取用户本机的 Zotero 文献库，仅保存 PDF **路径** 与 Zotero **item key**
- `reports/`（含论文片段）与 `data/index/`（解析产物）均已 gitignore
- 评测语料为**作者本人的论文**，注入缺陷为程序化生成
