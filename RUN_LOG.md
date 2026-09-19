# RUN_LOG — 迭代与失败记录

> 记录设计取舍、踩过的坑、bad case。面试时「你怎么定位失败」的答案来源。

---

## 2026-09-19 · Day 0：方向收敛与可行性摸底

**结论**：三份独立调研从三个方向指向**同一个空白** —— 论文内部数值自洽审查。

| 调研覆盖面 | 判定原话 |
| --- | --- |
| 通用评测/工具生态 | 「论文内部数值自洽性检查的公开工具或产品：**未找到**」；PaperQA2（9,219★）也不做数值核算 |
| 遥感领域基准 | 遥感/地学**文献问答**公开基准：**未找到**；中文遥感文献 QA 基准：**未找到** |
| Zotero 生态（11 个项目，含 5,083★ zotero-mcp） | 「以上项目**都没有**『论文数值自洽性审查』这类可核验断言能力」 |

### 解析器选型：Docling 胜出（实测）

在同一篇论文上实测 Docling vs MinerU：

| | Docling | MinerU 4.0.3 |
| --- | --- | --- |
| 许可 | **MIT** | Apache-2.0 + 附加条款 |
| 本地跑 | ✅ 开箱可用 | ⚠️ 仅 `flash` 档可用；高质量档需另起 parse-server + 模型权重 |
| **表格** | ✅ 9 个表格 → 正确 DataFrame | ❌ flash 档无表格 |
| caption 独立类 | ✅ `caption=20` | 需高质量档 |
| 页码 provenance | ✅ 311/311、315/315 全覆盖 | ✅ `<!-- page N of M -->` |

**决定性差异**：数值自洽审查必须拿到**表格结构**并**区分摘要/表格/图注**。Docling 本地开箱就给，MinerU 要拿到得先搭服务。MinerU 的 `flash` 快路径价值又被 Zotero 的全文缓存吃掉了 → **不引入第三个组件**。

### ❌ 失败 1：conda-forge 装不上 Docling

`deepsearch-glm` 只有 PyPI 有 → **Docling 必须走 PyPI**。

### ❌ 失败 2：Docling 与主环境依赖硬冲突

`typer==0.27.2`、`huggingface-hub==1.31.0` 被 conda solve **pin 住**，docling 要求不同版本 → 无解。
试过 `index-strategy = "unsafe-best-match"` 无效。

**解法**：把解析器**隔离成独立环境**（`no-default-feature = true`，不继承主环境依赖）。主环境保持干净，解析器以子进程 worker 形式调用。这比强行混装更合理。

### ⚠️ 修正 1：成本模型从 40 小时降到 5 小时

最初被一篇 1982 年扫描件（**668 秒**）吓到，推算全库要 40 小时。抽样 40 篇实测：**95% 有文本层**（26s/篇），仅 5% 需 OCR → **全库 474 篇约 5 小时**。

**教训**：单点样本不能外推。**抽样测分布**才算成本。

### ⚠️ 修正 2：PyMuPDF 是 AGPL-3.0

前几轮方案默认用它做 PDF 解析 —— **AGPL 会污染开源许可**。已移出依赖，README 显式声明不使用。

### ⚠️ 发现 3：真实文献库里必然有畸形 PDF

`Shang 等 - 2023` 那篇 PyMuPDF 报 **页数 0**，Docling 报 `not valid`。查 Zotero 侧：**它也没有 `.zotero-ft-cache`** —— 说明 **Zotero 同样抽不出，没有兜底**。
→ 流水线必须「跳过 + 写日志」，不能假设所有 PDF 可解析。

---

## 2026-09-19 · Day 1：Zotero 适配层

**产出**：`src/rsrc/config.py`、`src/rsrc/zotero/library.py`、`tests/test_zotero.py`。

**验收**：`4/4 通过`。完整链路打通 —— `itemKey → children → attachment → file/view/url → file:// 路径 → 文件存在`，5/5 验证。

### 库的实测形态（作者本机）

```text
Zotero 10.0.2 (64-bit)   API v3 / Schema v44 / Server-ID KUxHSVVBgELj
数据目录      F:\ZoteroPaper（prefs.js 自定义，不是默认 ~/Zotero）
条目（2000 条样本）
  annotation      799
  attachment      645
  journalArticle  480      ← 研究文献主体
  conferencePaper  16
  thesis            6
  dataset           6
storage/  751 目录 / 474 PDF（3.3 GB）
fulltext.sqlite  25 MB，726 篇已索引（其中 CJK 249 篇）
```

### 🐛 Bug 1：`linkMode` 在 API 里是**字符串**，不是整数

**现象**：自检报「5 篇期刊论文里没找到任何可用 PDF」，但手工 curl 明明能拿到。

**定位**：`config.py` 按 Zotero **源码常量**写成了整数 `(0, 1, 2)`，而 **HTTP API 返回的是 `"imported_url"` 这样的字符串**。于是 `linkMode not in (0,1,2)` 恒为真，**所有附件被静默过滤**。

**修法**：字符串与整数两套都认（SQLite 侧确实是整数），内部统一归一成名字。

> **教训**：官方**源码常量**与**API 序列化形式**是两回事。文档给的枚举值不一定是线上传的形态 —— **以实测响应为准**。
> 另一个教训：`attachment_path()` 里把异常吞成 `None` 是必要的健壮性，但也**掩盖了真错误** —— 幸好自检在端到端层面把它逼出来了。

### ⚠️ 发现 4：`/fulltext` 返回 JSON，不是纯文本

返回体是 `{"content": "..."}`，**必须解包**。实测单篇 41,601 字符。
这是「档 0」快速通道 —— 省去自己解析 PDF，但**无页码无版式**。

### ⚠️ 发现 5：附件路径是 URL 编码的

`file:///F:/ZoteroPaper/storage/RKA4MEL9/Zhang%20%E7%AD%89%20-%202015%20-...`
→ 必须 `unquote` 并处理 `file:///F:/` 前导斜杠，否则 Path 不可用。

---

---

## 2026-09-19 · Day 1 续：Embedding 选型实测

### 调研结论修正了优化目标

按 474 篇 × ~8,000 token ≈ **3.8M token** 估算：

| 渠道 | 全量成本 | 结论 |
| --- | --- | --- |
| 百炼 `text-embedding-v4` | 免费额度 100 万 token 后约 **¥1.4** | 中文最强（同族 8B C-MTEB 73.84） |
| SiliconFlow `BAAI/bge-m3` | 标价 ¥0/M | **实测零余额被拒，见下** |
| OpenRouter `:free` | $0 | ⚠️ 无充值仅 **50 请求/天**（要跑 12 天）+ `:free` 变体「**可能被留存用于训练**」 |
| 本地 fastembed | **¥0** | 见下 |

**核心判断**：这个规模下**免费额度根本不是瓶颈**（全量最多约 2 元）。该优化的是**中英混合检索质量** —— 库里中文占 **249/474 ≈ 52%**，接近一半。

### ❌ 失败 4：SiliconFlow 零余额账户全线 402

调研查到 `BAAI/bge-m3` 在 SiliconFlow 标价 **¥0/M token**，据此推断可以零成本使用。

**实测**：`BAAI/bge-m3`、`Pro/BAAI/bge-m3`、`Qwen/Qwen3-Embedding-0.6B`、`BAAI/bge-large-zh-v1.5` **全部返回 `402` "Sorry, your account balance is insufficient"**。
`/v1/user/info` 也返回 `410 deprecated`，查不到余额。

**教训**：「模型单价为 0」≠「零余额可用」。平台可能仍要求账户有正余额。**价格页数字不能替代一次真实的 API 调用验证。**

### ❌ 失败 5：`.env` 里写死 `EMBED_MODEL` 会跨 provider 泄漏

`.env` 写了 `EMBED_MODEL=BAAI/bge-m3`，切到 `EMBED_PROVIDER=local` 后这个值被沿用 → fastembed 报 `Model BAAI/bge-m3 is not supported`。

**根因**：`EMBED_MODEL` 是全局的，但模型名是 **provider 私有**的。
**修法**：`.env` 不再写 `EMBED_MODEL`/`EMBED_DIM`（由 provider 预设驱动），只在需要覆盖时才填。

### ❌ 失败 6：fastembed 不支持 bge-m3

`TextEmbedding.list_supported_models()` 实测 30 个模型，**不含 `BAAI/bge-m3`**。
多语/中文可选：`MiniLM-L12-v2`(384/0.22GB)、`mpnet-base-v2`(768/1.0GB)、`multilingual-e5-large`(1024/2.24GB)、`jina-v2-base-zh`(768/0.64GB)、`bge-small-zh-v1.5`(512/0.09GB)。
→ 默认改用 **`paraphrase-multilingual-mpnet-base-v2`（768 维）**，并在代码里加了「不支持就列出可选模型」的报错。

### ✅ 本地兜底实测：可行

| 指标 | 实测 |
| --- | --- |
| 模型 | `paraphrase-multilingual-mpnet-base-v2`，768 维 |
| 首次调用（含 ONNX 初始化） | 3.0 s |
| **预热后吞吐** | **293 ms/chunk（3.4 条/秒）** |
| **全库 13,000 chunk 外推** | **约 64 分钟** |

> ⚠️ 注意第一次粗测得 22,307 ms/条 —— 那是**首次调用把模型初始化也算进去了**（5 条样本）。
> **教训**：性能测量必须先预热、且样本量足够，否则会得出「慢 76 倍」的错误结论。

语义区分正常（实测余弦）：「地表发射率地形校正」↔「terrain correction of LSE」**0.7766** vs 无关句 **0.2749**。

### 结论

**默认走本地兜底**：¥0、离线、不需要 key，符合「本地可跑通」验收标准，约 1 小时建完索引。
有 API key 的用户可切百炼拿到更好的中文检索质量（约 ¥1.4），**但换模型必须全量重建索引**（`EmbeddingSpec.fingerprint()` 绑定）。

---

## 2026-09-19 · Embedding 定选：SiliconFlow `BAAI/bge-m3`

账户充值后重测，**402 消失**，但仍撞到一个 400 —— 详见下。

### ❌ 失败 7：给固定维度模型传 `dimensions` 会 400

`BAAI/bge-m3` 是**固定 1024 维**（不支持 MRL 降维），但我的代码无条件发送
`dimensions` → SiliconFlow 返回 `400 code 20015 The parameter is invalid`。

**修法**：**只在用户显式设置 `EMBED_DIM` 时才向 API 请求降维**；预设里的维度只用于
`EmbeddingSpec`（指纹与索引元数据），不发出去。
→ 原则：**「声明维度」与「请求降维」是两件事，不能混。**

### ⚠️ 发现 8：限流会触发 SDK 静默重试，表现为单个批次异常慢

批量吞吐实测（64 条一批，文本约 400 token）：

| 批次 | 耗时 | 每 chunk |
| --- | --- | --- |
| 16 条 | 0.24 s | 15 ms |
| 32 条 | 0.33 s | 10 ms |
| **64 条（第一次）** | **61.26 s** | **957 ms** |
| 64 条（第二次） | 0.68 s | 11 ms |

第一次 64 条慢了 **约 90 倍** —— 不是模型慢，是**触发了限流后 `openai` SDK 自动退避重试**。

**结论**：批量入库**必须自己控速**（客户端令牌桶 + 按 `Retry-After` 退避），
不能依赖 SDK 的隐式重试 —— 否则进度不可预测、日志看不出原因。

### 定选对比

| | 本地 fastembed `mpnet-base-v2` | **SiliconFlow `BAAI/bge-m3`** |
| --- | --- | --- |
| 全库 13,000 chunk | **约 64 分钟** | **约 11 分钟**（受 TPM 500k 约束） |
| 维度 / 上下文 | 768 / 512 | **1024 / 8192** |
| 中英混合 | 可用 | **原生多语（100+）** |
| 成本 | ¥0 | 约 ¥1.4 |
| 需网络 | 否 | 是 |

**结论**：默认用 SiliconFlow `bge-m3`；本地 fastembed 保留为**离线兜底**
（无 key 时自动启用，符合「本地可跑通」）。

语义区分验证（bge-m3）：同义 `0.7419` vs 无关 `0.3248`。
