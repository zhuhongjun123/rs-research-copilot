"""数值自洽审查报告的渲染。

## 原则：每一条结论都必须可溯源

报告里出现的每个数值都带 `p<页码>/<block类型>` 与原文片段，
读者能直接回 PDF 复核 —— 这是「降低幻觉」的具体形式，
也避免报告本身变成新的不可核验断言。

## 无 LLM 也能出报告

`render_report` 是纯函数（结构化 → Markdown），不依赖模型。
LLM 只用于**附加**一段叙述摘要，失败/不可用时整条链路照常工作。
"""

from __future__ import annotations

from dataclasses import dataclass

from rsrc.audit.checks import Finding
from rsrc.audit.extract import NumericFact

_SEV_CN = {"high": "高", "medium": "中", "info": "提示"}
_VERDICT_CN = {"conflict": "存在不一致", "ok": "通过", "unverifiable": "需人工确认"}


@dataclass
class AuditResult:
    source: str
    title: str = ""
    item_key: str = ""
    doi: str = ""
    findings: list[Finding] = None  # type: ignore[assignment]
    facts: list[NumericFact] = None  # type: ignore[assignment]
    narrative: str = ""
    trace: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.findings = self.findings or []
        self.facts = self.facts or []
        self.trace = self.trace or []

    def counts(self) -> dict[str, int]:
        out = {"high": 0, "medium": 0, "info": 0}
        for f in self.findings:
            out[f.severity] = out.get(f.severity, 0) + 1
        return out


def _fact_row(f: NumericFact) -> str:
    loc = f"p{f.page}/{f.block_type}" if f.page else f.block_type
    unit = f" {f.unit}" if f.unit else ""
    return f"| `{loc}` | {f.metric} | {f.relation}{f.value}{unit} |"


def render_report(r: AuditResult) -> str:
    """把审查结果渲染成 Markdown。"""
    counts = r.counts()
    lines: list[str] = []
    lines.append(f"# 数值自洽审查报告 · {r.title or r.source}")
    lines.append("")
    if r.item_key or r.doi:
        meta = " | ".join(x for x in (f"Zotero `{r.item_key}`" if r.item_key else "",
                                      f"DOI {r.doi}" if r.doi else "") if x)
        lines.append(f"> {meta}")
        lines.append("")

    lines.append("## 结论")
    lines.append("")
    if counts["high"] or counts["medium"]:
        lines.append(
            f"发现 **{counts['high'] + counts['medium']}** 处需关注的不一致"
            f"（高 {counts['high']} / 中 {counts['medium']}），"
            f"另有 {counts['info']} 处提示。"
        )
    elif counts["info"]:
        lines.append(
            f"**未发现内部矛盾。** 另有 {counts['info']} 处提示（属正常情况，仅列出供参考）。"
        )
    else:
        lines.append("**未发现内部矛盾。**")
    lines.append("")
    lines.append(
        f"> 检查范围：自动抽取并核验了 **{len(r.facts)}** 条数值型事实。"
        "所有条目均标注页码与原文片段，可回原文复核。"
    )
    lines.append("")

    if r.narrative:
        lines.append("## 摘要")
        lines.append("")
        lines.append(r.narrative.strip())
        lines.append("")

    conflicts = [f for f in r.findings if f.verdict == "conflict"]
    if conflicts:
        lines.append("## 需关注项")
        lines.append("")
        for i, f in enumerate(conflicts, 1):
            lines.append(f"### {i}. [{f.check}] {f.title}")
            lines.append("")
            if f.subject:
                lines.append(f"- **对象**：{f.subject}")
            lines.append(f"- **严重度**：{_SEV_CN.get(f.severity, f.severity)}")
            if f.detail:
                lines.append(f"- **判据**：{f.detail}")
            lines.append("")
            lines.append("| 位置 | 指标 | 取值 |")
            lines.append("| --- | --- | --- |")
            for item in f.items:
                lines.append(_fact_row(item))
            lines.append("")
            for item in f.items[:2]:
                if item.context:
                    lines.append(f"    > `{item.context}`")
            lines.append("")

    info = [f for f in r.findings if f.verdict != "conflict"]
    if info:
        lines.append("## 提示（非矛盾）")
        lines.append("")
        for f in info:
            lines.append(f"- [{f.check}] {f.title}")
            if f.detail:
                lines.append(f"  - {f.detail}")
        lines.append("")

    lines.append("## 抽取到的数值型事实")
    lines.append("")
    lines.append("| 位置 | 指标 | 取值 |")
    lines.append("| --- | --- | --- |")
    for f in sorted(r.facts, key=lambda x: (x.page or 0, x.metric)):
        lines.append(_fact_row(f))
    lines.append("")

    if r.trace:
        lines.append("## 执行轨迹")
        lines.append("")
        for step in r.trace:
            lines.append(f"- {step}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "*本报告由 `rs-research-copilot` 自动生成。检查项为确定性规则"
        "（指标值域、指标间数学关系、同一对象的跨位置一致性、单位量纲），"
        "**不含 LLM 的自由判断**；LLM 仅用于生成「摘要」段落。*"
    )
    return "\n".join(lines)
