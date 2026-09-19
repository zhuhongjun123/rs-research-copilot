# 数值自洽审查报告 · A multi-scale assessment of topographic anisotropy on land surface emissivity and temperature retrieval over the yarlung tsangpo river basin

> Zotero `C7KQG54K` | DOI 10.1080/01431161.2026.2710368

## 结论

发现 **2** 处需关注的不一致（高 1 / 中 1），另有 0 处提示。

> 检查范围：自动抽取并核验了 **13** 条数值型事实。所有条目均标注页码与原文片段，可回原文复核。

## 需关注项

### 1. [C3] R² 超出 [0,1] 值域

- **对象**：tri
- **严重度**：高
- **判据**：取值 1.24，R² 定义域为 [0,1]（负值需在文中显式说明）

| 位置 | 指标 | 取值 |
| --- | --- | --- |
| `p2/text` | R² | ≈1.24 |

    > `onential growth relationship with the Terrain Ruggedness Index (TRI) (R² ≈1.24), identifying TRI, rather tha`

### 2. [C2] R² 同一对象出现不同取值

- **对象**：tri
- **严重度**：中
- **判据**：取值 0.369 ~ 1.24（相对差 70.2%），需人工确认是否为同一对象

| 位置 | 指标 | 取值 |
| --- | --- | --- |
| `p2/text` | R² | ≈1.24 |
| `p18/text` | R² | ≈0.369 |

    > `onential growth relationship with the Terrain Ruggedness Index (TRI) (R² ≈1.24), identifying TRI, rather tha`
    > `ionship with both local slope and the Terrain Ruggedness Index (TRI) (R² ≈0.369-0.75). The substantial magnit`

## 抽取到的数值型事实

| 位置 | 指标 | 取值 |
| --- | --- | --- |
| `p2/text` | R² | =0.055 |
| `p2/text` | R² | to0.0036 |
| `p2/text` | R² | ≈1.24 |
| `p3/text` | Bias | =0.02 |
| `p13/text` | Bias | ≈0.4 K |
| `p16/text` | R² | =0.055 |
| `p16/text` | R² | =0.0036 |
| `p17/text` | R² | ≈0.0 |
| `p17/text` | R² | =0.045 |
| `p17/text` | R² | to0.037 |
| `p18/text` | R² | ≈0.13 |
| `p18/text` | R² | ≈0.369 |
| `p18/text` | R² | to0.75 |

## 执行轨迹

- load: _injected_C7KQG54K.json（25 页 / 221 块）
- extract: 抽到 13 条数值型事实
- check: 2 条 finding（其中矛盾 2 条）

---

*本报告由 `rs-research-copilot` 自动生成。检查项为确定性规则（指标值域、指标间数学关系、同一对象的跨位置一致性、单位量纲），**不含 LLM 的自由判断**；LLM 仅用于生成「摘要」段落。*