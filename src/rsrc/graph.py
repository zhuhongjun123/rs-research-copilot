"""LangGraph 编排：数值自洽审查链。

## 状态图

```text
  load ──▶ extract ──▶ check ──┬─(有矛盾 且 LLM 可用)─▶ summarize ──┐
                               └─(否则)──────────────────────────────┴─▶ render
```

**为什么 `summarize` 是条件边**：
- 没有矛盾时，让 LLM 写一段"没发现问题"的叙述纯属浪费 token
- 没配 LLM 时整条链路应降级为**纯确定性报告**，而不是失败
  （核心检查项本来就不依赖 LLM —— 这正是「降幻觉」的设计前提）

## 与「降幻觉」的关系

本链路的**判定全部来自确定性规则**（§checks.py），LLM 只负责把结论转成叙述，
且叙述被要求**只能引用已核验的事实**。所以即便 LLM 出错，
报告里的事实表与 finding 列表仍然可核验。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from rsrc.audit.checks import run_checks
from rsrc.audit.extract import extract_facts
from rsrc.report import AuditResult, render_report


class AuditState(TypedDict, total=False):
    """审查链状态。"""

    source: str
    title: str
    item_key: str
    doi: str
    parsed: dict[str, Any]
    facts: list
    findings: list
    narrative: str
    report: str
    trace: list[str]
    use_llm: bool


# ── 节点 ────────────────────────────────────────────────────────
def node_load(state: AuditState) -> dict:
    """读取解析产物（Docling 的 JSON）。"""
    src = Path(state["source"])
    if src.suffix.lower() != ".json":
        raise ValueError(
            f"需要 Docling 解析产物（*.json），收到 {src.name}。"
            "先跑：pixi run -e parser python scripts/parse_pdf.py <pdf> <out.json>"
        )
    parsed = json.loads(src.read_text(encoding="utf-8"))
    trace = list(state.get("trace", []))
    trace.append(f"load: {src.name}（{parsed.get('page_count')} 页 / {len(parsed.get('blocks', []))} 块）")
    return {"parsed": parsed, "trace": trace}


def node_extract(state: AuditState) -> dict:
    facts = extract_facts(state["parsed"]["blocks"])
    trace = list(state.get("trace", []))
    trace.append(f"extract: 抽到 {len(facts)} 条数值型事实")
    return {"facts": facts, "trace": trace}


def node_check(state: AuditState) -> dict:
    findings = run_checks(state["parsed"]["blocks"], state["facts"])
    conflicts = sum(1 for f in findings if f.verdict == "conflict")
    trace = list(state.get("trace", []))
    trace.append(f"check: {len(findings)} 条 finding（其中矛盾 {conflicts} 条）")
    return {"findings": findings, "trace": trace}


def node_summarize(state: AuditState) -> dict:
    """LLM 生成摘要 —— **只允许引用已核验的事实**。"""
    from rsrc.llm import build_chat

    chat = build_chat()
    trace = list(state.get("trace", []))
    if chat is None:
        trace.append("summarize: 跳过（未配置 LLM）")
        return {"narrative": "", "trace": trace}

    client, spec = chat
    conflicts = [f for f in state["findings"] if f.verdict == "conflict"]
    lines = []
    for f in conflicts:
        vals = " / ".join(f"{i.metric}={i.relation}{i.value}" for i in f.items)
        lines.append(f"- [{f.check}] {f.title}；对象：{f.subject or '未标注'}；{vals}；判据：{f.detail}")

    prompt = (
        "下面是对一篇科研论文做「数值自洽性自动检查」得到的结构化结果。\n"
        "请写一段 120 字以内的中文摘要，说明发现的问题及其可能影响。\n\n"
        "**硬性要求**：只允许提及下面给出的数值与位置，绝对不要引入任何新数字、"
        "不要推测论文内容、不要评价研究质量。若信息不足以判断影响，就直接说明需要作者澄清。\n\n"
        "检查结果：\n" + "\n".join(lines)
    )
    try:
        resp = client.chat.completions.create(
            model=spec.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
        )
        narrative = resp.choices[0].message.content.strip()
        trace.append(f"summarize: {spec.provider}/{spec.model} 生成 {len(narrative)} 字")
    except Exception as exc:  # noqa: BLE001 - LLM 失败不应中断审查
        narrative = ""
        trace.append(f"summarize: LLM 调用失败（{type(exc).__name__}），已降级为无摘要")
    return {"narrative": narrative, "trace": trace}


def node_render(state: AuditState) -> dict:
    result = AuditResult(
        source=state["source"],
        title=state.get("title", ""),
        item_key=state.get("item_key", ""),
        doi=state.get("doi", ""),
        findings=state.get("findings", []),
        facts=state.get("facts", []),
        narrative=state.get("narrative", ""),
        trace=state.get("trace", []),
    )
    return {"report": render_report(result), "trace": result.trace}


# ── 条件边 ──────────────────────────────────────────────────────
def should_summarize(state: AuditState) -> str:
    """有矛盾、且允许用 LLM 时才写摘要。"""
    if not state.get("use_llm", True):
        return "render"
    has_conflict = any(f.verdict == "conflict" for f in state.get("findings", []))
    return "summarize" if has_conflict else "render"


# ── 图 ──────────────────────────────────────────────────────────
def build_audit_graph():
    """构造并编译审查图。"""
    graph = StateGraph(AuditState)
    graph.add_node("load", node_load)
    graph.add_node("extract", node_extract)
    graph.add_node("check", node_check)
    graph.add_node("summarize", node_summarize)
    graph.add_node("render", node_render)

    graph.add_edge(START, "load")
    graph.add_edge("load", "extract")
    graph.add_edge("extract", "check")
    graph.add_conditional_edges(
        "check", should_summarize, {"summarize": "summarize", "render": "render"}
    )
    graph.add_edge("summarize", "render")
    graph.add_edge("render", END)
    return graph.compile()


def run_audit(
    source: str | Path,
    *,
    title: str = "",
    item_key: str = "",
    doi: str = "",
    use_llm: bool = True,
) -> dict:
    """跑一次审查，返回最终 state。"""
    app = build_audit_graph()
    return app.invoke(
        {
            "source": str(source),
            "title": title,
            "item_key": item_key,
            "doi": doi,
            "use_llm": use_llm,
            "trace": [],
        }
    )
