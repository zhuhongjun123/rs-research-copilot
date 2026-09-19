"""知识库问答链：检索 → 生成（强制引用）→ **回查核验** → 必要时拒答。

## 与「降幻觉」的关系（本模块的全部意义）

不做这三件事，问答就只是又一个会编数字的 RAG：

1. **只许引用给定片段**：prompt 里明确「只能依据下列片段作答，每个数值断言必须标
   `[来源N]`；片段里没有就回答『无法从文献库中确定』」。
2. **生成后回查核验**（`verify_answer`）：把答案里的数值型断言逐条拿去**被引用的片段**
   里做字符串/数值比对 —— 对不上就标为「无支撑」。**这是确定性检查，不是再问一次模型。**
3. **拒答是一等公民**：检索无命中、或全部命中低于阈值时**直接拒答**，不进入生成，
   避免模型靠先验硬答。

第 2 条是关键：它让「引用」变成可核验的东西，而不只是模型的一个措辞。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from rsrc.config import INDEX_DIR
from rsrc.llm import build_chat, build_embedder
from rsrc.retrieval import DEFAULT_TOP_K, HybridIndex

class AskState(TypedDict, total=False):
    """问答链状态。

    ⚠️ 必须用 TypedDict，**不能用 `StateGraph(dict)`** —— 实测后者会把每个节点的
    返回值当成**整份状态替换**（而不是合并），于是 retrieve 一返回，
    `question` 就从状态里消失了，下游节点直接 KeyError。

    ⚠️ **而且字段必须逐一声明**：LangGraph 会按声明的键过滤输入，
    只写 docstring 不声明字段的话，传入的 state 会被过滤成空 dict。
    """

    question: str
    top_k: int
    hits: list
    answer: str
    refused: bool
    verified: list
    unsupported: list
    trace: list[str]

REFUSAL = "无法从文献库中确定"

# 引用标记：[来源1] / [来源 1] / [1]
_CITE_RE = re.compile(r"\[来源\s*(\d+)\]|\[(\d+)\]")
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
# 相关度阈值：RRF 分数很低说明只是词面偶然重合
MIN_SCORE = 0.012


@dataclass
class QAResult:
    question: str
    answer: str = ""
    refused: bool = False
    hits: list[dict] = field(default_factory=list)
    verified: list[dict] = field(default_factory=list)
    unsupported: list[dict] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "question": self.question, "answer": self.answer, "refused": self.refused,
            "hits": [{"cite": h["cite"], "score": h["score"],
                      "preview": h["chunk"].text[:160]} for h in self.hits],
            "verified": self.verified, "unsupported": self.unsupported,
            "trace": self.trace,
        }


# ── 核验（确定性，不调模型）────────────────────────────────────
def verify_answer(answer: str, hits: list[dict]) -> tuple[list[dict], list[dict]]:
    """逐条核验答案里的数值断言是否被**其引用的片段**支撑。

    规则：一条断言（按句/分号切）引用了 `[来源N]`，就把该断言里的每个数字
    拿去第 N 个片段里找；**找不到即判为无支撑**。

    这是本项目「降幻觉」的核心动作 —— 它是确定性的，不依赖再问一次模型。
    """
    verified: list[dict] = []
    unsupported: list[dict] = []

    for sentence in re.split(r"[。；;\n]", answer):
        s = sentence.strip()
        if not s:
            continue
        cited = [int(a or b) for a, b in _CITE_RE.findall(s)]
        # ⚠️ 必须先剥掉引用标记再抽数字 —— 否则 `[来源1]` 里的 1
        #    会被当成断言中的一个数字，本来自带引用的正确断言反而被判「无支撑」。
        #    （自检抓到过这个 bug）
        body = _CITE_RE.sub("", s)
        nums = _NUM_RE.findall(body)
        if not nums:
            continue  # 没有数值的断言不参与核验
        if not cited:
            unsupported.append({"claim": s[:120], "reason": "带数值的断言未标注引用"})
            continue
        pool = ""
        for idx in cited:
            if 1 <= idx <= len(hits):
                pool += hits[idx - 1]["chunk"].text
        missing = [n for n in nums if n not in pool]
        if missing:
            unsupported.append({"claim": s[:120], "reason": f"数值 {missing[:4]} 未在所引片段中找到"})
        else:
            verified.append({"claim": s[:120], "cited": cited})
    return verified, unsupported


# ── 引擎 ────────────────────────────────────────────────────────
class QAEngine:
    """把索引 + 模型 + 图装配在一起。"""

    def __init__(self, index_dir: Path = INDEX_DIR) -> None:
        self.index, self.manifest = HybridIndex.load(index_dir)
        self.embedder = build_embedder()
        # ⚠️ 指纹必须一致：向量与模型绑定，换模型必须全量重建
        if self.index.fingerprint() != self.embedder.spec.fingerprint():
            raise RuntimeError(
                f"索引指纹 {self.index.fingerprint()} 与当前 embedding 模型 "
                f"{self.embedder.spec.fingerprint()} 不一致 —— 请重建："
                "pixi run py scripts/build_index.py"
            )
        self.chat = build_chat()
        self.graph = build_ask_graph(self)

    # ── 节点：检索 ──────────────────────────────────────────────
    def node_retrieve(self, state: AskState) -> dict:
        q = state["question"]
        hits = self.index.search(q, self.embedder, top_k=state.get("top_k", DEFAULT_TOP_K))
        trace = list(state.get("trace", []))
        trace.append(f"retrieve: {len(hits)} 命中，最高分 {hits[0]['score'] if hits else 0}")
        return {"hits": hits, "trace": trace}

    # ── 条件边：是否有足够证据 ──────────────────────────────────
    def route_after_retrieve(self, state: AskState) -> str:
        hits = state.get("hits", [])
        if not hits or hits[0]["score"] < MIN_SCORE:
            return "refuse"
        return "answer"

    def node_refuse(self, state: AskState) -> dict:
        trace = list(state.get("trace", []))
        trace.append("refuse: 检索证据不足，不进入生成（避免靠先验硬答）")
        return {"answer": REFUSAL, "refused": True, "verified": [], "unsupported": [],
                "trace": trace}

    # ── 节点：生成（强制引用）───────────────────────────────────
    def node_answer(self, state: AskState) -> dict:
        trace = list(state.get("trace", []))
        if self.chat is None:
            trace.append("answer: 未配置 LLM，降级为只返回检索片段")
            lines = [f"[来源{i}]（{h['cite']}）{h['chunk'].text[:200]}"
                     for i, h in enumerate(state["hits"][:5], 1)]
            return {"answer": "（未配置 LLM）检索到的相关片段：\n" + "\n".join(lines),
                    "verified": [], "unsupported": [], "trace": trace}

        excerpts = "\n\n".join(
            f"[来源{i}]（{h['cite']}）\n{h['chunk'].text[:1500]}"
            for i, h in enumerate(state["hits"], 1)
        )
        prompt = (
            "你是科研文献助手。**只能依据下面给出的文献片段回答**。\n\n"
            "硬性要求：\n"
            "1. 每个包含数值或具体结论的断言，必须在句末标注它来自哪个片段，格式为 [来源N]。\n"
            "2. **不要引入片段之外的任何数字或结论**，也不要靠常识补充。\n"
            f"3. 如果片段里没有足够信息回答，只输出这一句：{REFUSAL}\n"
            "4. 用中文回答，简洁；不要复述这些要求。\n\n"
            f"文献片段：\n{excerpts}\n\n问题：{state['question']}"
        )
        try:
            resp = self.chat[0].chat.completions.create(
                model=self.chat[1].model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=900, temperature=0,
            )
            answer = (resp.choices[0].message.content or "").strip()
        except Exception as exc:  # noqa: BLE001 - 生成失败不该让整条链崩
            trace.append(f"answer: LLM 调用失败（{type(exc).__name__}）")
            answer = REFUSAL

        refused = REFUSAL in answer
        trace.append(f"answer: {len(answer)} 字{'（模型拒答）' if refused else ''}")
        return {"answer": answer, "refused": refused, "trace": trace}

    # ── 节点：核验 ──────────────────────────────────────────────
    def node_verify(self, state: AskState) -> dict:
        verified, unsupported = verify_answer(state.get("answer", ""), state.get("hits", []))
        trace = list(state.get("trace", []))
        trace.append(f"verify: 有支撑 {len(verified)} 条 / 无支撑 {len(unsupported)} 条")
        return {"verified": verified, "unsupported": unsupported, "trace": trace}

    # ── 对外 ────────────────────────────────────────────────────
    def ask(self, question: str, top_k: int = DEFAULT_TOP_K) -> QAResult:
        state = self.graph.invoke({"question": question, "top_k": top_k, "trace": []})
        return QAResult(
            question=question,
            answer=state.get("answer", ""),
            refused=state.get("refused", False),
            hits=state.get("hits", []),
            verified=state.get("verified", []),
            unsupported=state.get("unsupported", []),
            trace=state.get("trace", []),
        )


# ── 图 ──────────────────────────────────────────────────────────
def build_ask_graph(engine: QAEngine):
    g = StateGraph(AskState)
    g.add_node("retrieve", engine.node_retrieve)
    g.add_node("answer", engine.node_answer)
    g.add_node("refuse", engine.node_refuse)
    g.add_node("verify", engine.node_verify)

    g.add_edge(START, "retrieve")
    g.add_conditional_edges("retrieve", engine.route_after_retrieve,
                            {"answer": "answer", "refuse": "refuse"})
    g.add_edge("answer", "verify")
    g.add_edge("refuse", END)
    g.add_edge("verify", END)
    return g.compile()


def render_answer(r: QAResult) -> str:
    """把问答结果渲染成 Markdown（含核验结论与引用）。"""
    out = [f"**问**：{r.question}", ""]
    out.append(f"**答**：{r.answer}" if not r.refused else f"**答**：{r.answer}（已拒答）")
    out.append("")
    if r.refused:
        out.append("> 拒答原因：检索到的片段证据不足。这是**设计行为** —— "
                   "宁可不答，也不靠模型先验硬答。")
        out.append("")
    if r.verified or r.unsupported:
        out.append("### 断言核验（确定性回查，非再次问模型）")
        out.append("")
        out.append(f"- 有支撑：**{len(r.verified)}** 条")
        out.append(f"- 无支撑：**{len(r.unsupported)}** 条")
        out.append("")
        for u in r.unsupported[:5]:
            out.append(f"  - ⚠️ {u['reason']}｜`{u['claim'][:70]}`")
        out.append("")
    if r.hits:
        out.append("### 引用片段")
        out.append("")
        for i, h in enumerate(r.hits[:6], 1):
            out.append(f"- **[来源{i}]** `{h['cite']}`（RRF {h['score']}）")
            out.append(f"  > {h['chunk'].text[:180].replace(chr(10), ' ')}")
        out.append("")
    if r.trace:
        out.append("### 执行轨迹")
        out.append("")
        for t in r.trace:
            out.append(f"- {t}")
    return "\n".join(out)
