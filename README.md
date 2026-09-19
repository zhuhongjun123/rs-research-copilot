# rs-research-copilot

遥感科研辅助 Agent —— 基于**本地 Zotero 文献库**，提供可溯源的问答、生成与**论文数值自洽审查**。

> **它解决什么问题**：把论文 PDF 直接丢给大模型时，模型会编造数字、编造结论。
> 本项目通过「强制引用到页码 + 用代码执行器复算论文内部数值」，把幻觉压下来，并**把这件事量化**。

## 数据来源声明

⚠️ **本仓库不含任何论文原文，也不分发任何第三方文献。**

- 只读取**用户本机**的 Zotero 文献库，仅保存 PDF **路径** 与 Zotero **item key**
- 评测基准只存「问题 + 标准答案 + 证据定位（itemKey / 页码 / 元素类型）」，**不复制原文长句**
- 不向任何云服务上传文献内容

## 核心能力

| 能力 | 说明 |
| --- | --- |
| **数值自洽审查** | 对论文内部数值做确定性复算：样本量跨位置一致、精度指标一致、指标间数学关系、百分比可复算、单位量纲、时空范围 |
| 可溯源问答 | 答案必须带 `itemKey + 页码` 引用；生成后回查原文核验 |
| 拒答 | 库中无据时明确拒答，并单独量化（应拒未拒率 / 误拒率） |
| 评测 | 对照实验：裸 LLM vs RAG+引用 vs RAG+引用+数值核验 |

## 关于「幻觉率」的口径

⚠️ **业界对「幻觉率」没有统一口径**，本项目如实声明所采用的口径：

| 口径 | 计算 | 用途 |
| --- | --- | --- |
| **Claim-level**（主） | 无支撑 atomic claim 数 / 总 claim 数（= `1 − FActScore`） | 主指标 |
| **Response-level**（辅） | 至少含 1 条无支撑断言的回答数 / 总回答数（= `1 − faithfulness`） | 对外沟通 |

**数值型幻觉单独统计** —— 自然语言 judge 对数字不敏感，必须用代码执行器精确比对。

## 环境

使用 [pixi](https://pixi.sh) 管理，分两个环境：

```bash
pixi install              # 主环境（Zotero / 检索 / 审查 / 服务）
pixi install -e parser    # 解析环境（Docling，依赖重故隔离）
```

| 环境 | 用途 | 关键依赖 |
| --- | --- | --- |
| `default` | 适配、检索、审查、编排、服务 | LangGraph, FAISS, BM25, pyzotero, sentence-transformers, FastAPI |
| `parser` | PDF 结构化解析（子进程 worker） | Docling |

> **为什么隔离**：Docling 依赖链重（torch / transformers / huggingface-hub），且与 conda 侧被固定的 `typer` 版本冲突。隔离后主环境保持干净。

## 前置：Zotero 设置

本项目**只读**你的本地 Zotero 库。需在 Zotero 中开启：

> 设置 → 高级 → 勾选「**允许其它应用程序与 Zotero 通信**」

未开启时本地 API 会返回 `403`。若 Zotero 未运行，会自动降级为读取 `zotero.sqlite` 的只读副本。

**安全提示**：Zotero 本地 API 的读请求**无需鉴权** —— 请勿将 `23119` 端口暴露到本机之外。

## 可选：解析环境隔离说明

Windows 上如果 PATH 中存在其它 GDAL/PROJ 发行版（如 Anaconda、ArcGIS 自带的），会遮蔽本环境的动态库。本仓库提供 `scripts/bootstrap_env.py`，以干净 PATH 启动解释器规避此问题：

```bash
pixi run py scripts/build_index.py
```

## 许可

本仓库代码的许可见 `LICENSE`。
第三方依赖的许可见 `THIRD_PARTY_LICENSES.md`。

> ⚠️ 本项目**不使用 PyMuPDF** —— 其 AGPL-3.0 许可会污染本项目的开源许可选择。

## 设计文档

- `PLAN.md`（内部方案，不随公开仓库分发）
- `RUN_LOG.md` —— 迭代与失败记录
