"""缺陷注入 —— 用于验证检查项的**召回**（Day 5 评测也用）。

## 为什么必须做注入

真实论文（尤其作者自己的）**不该有内部矛盾** —— 实测 4 篇上 0 conflict。
所以「能不能抓到」无法靠真实数据验证，必须**注入已知缺陷**做 ground truth。

## 设计原则

- **在真实文本上原地改**，不是凭空拼一段假论文 —— 这样验证的是真实解析产物上的行为
- 每次注入都记录 `{类型, 位置, 原值, 新值, 期望被哪个检查项抓到}` —— 即可算检出率
- 注入是**确定性的**（无随机），可复现
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass

from rsrc.audit.checks import extract_subject
from rsrc.audit.extract import NumericFact, extract_facts


@dataclass
class Injection:
    kind: str  # C1 / C2 / C3
    block_index: int
    page: int | None
    before: str  # 注入前的片段
    after: str  # 注入后的片段
    expected_check: str
    description: str = ""


def _blocks_of(blocks: list[dict]) -> list[dict]:
    return copy.deepcopy(blocks)


def _patch(blocks: list[dict], index: int, old: str, new: str) -> bool:
    text = blocks[index].get("text") or ""
    if old not in text:
        return False
    blocks[index]["text"] = text.replace(old, new, 1)
    return True


# ── C3：R² 超出值域 ─────────────────────────────────────────────
def inject_r2_out_of_range(blocks: list[dict], facts: list[NumericFact]) -> Injection | None:
    for f in facts:
        if f.metric != "R²" or f.block_index < 0:
            continue
        old = f"{f.relation}{f.value}"
        new = f"{f.relation}1.24"
        if _patch(blocks, f.block_index, old, new):
            return Injection(
                kind="C3", block_index=f.block_index, page=f.page,
                before=old, after=new, expected_check="C3",
                description="把 R² 改成 1.24（超出 [0,1] 定义域）",
            )
    return None


# ── C3：RMSE < MAE（数学上不可能）────────────────────────────────
def inject_relation_violation(blocks: list[dict], facts: list[NumericFact]) -> Injection | None:
    for f in facts:
        if f.metric != "RMSE" or f.block_index < 0 or f.relation == "to":
            continue
        # 在同一句内插入一个更大的 MAE
        inserted = f", MAE = {round(f.value + 0.75, 3)}"
        old = f"{f.relation}{f.value}"
        if _patch(blocks, f.block_index, old, old + inserted):
            return Injection(
                kind="C3", block_index=f.block_index, page=f.page,
                before=old, after=old + inserted, expected_check="C3",
                description=f"在同句插入 MAE={round(f.value + 0.75, 3)}，使 RMSE < MAE",
            )
    return None


# ── C2：同一对象跨位置取值不一致 ─────────────────────────────────
def inject_cross_position_conflict(
    blocks: list[dict], facts: list[NumericFact]
) -> Injection | None:
    """找同一「指标 + 对象」的两处出现，改掉后一处的值。"""
    seen: dict[tuple[str, str], NumericFact] = {}
    for f in facts:
        if f.metric not in ("R²", "RMSE", "MAE", "Bias") or f.relation in ("to", "<", ">"):
            continue
        subject = extract_subject(f)
        if not subject:
            continue
        key = (f.metric, subject)
        first = seen.get(key)
        if first is None:
            seen[key] = f
            continue
        if first.block_index == f.block_index:
            continue  # 同块内改一处会同时影响两处，换别的
        old = f"{f.relation}{f.value}"
        new = f"{f.relation}{round(f.value * 0.35 + 0.11, 3)}"
        if _patch(blocks, f.block_index, old, new):
            return Injection(
                kind="C2", block_index=f.block_index, page=f.page,
                before=old, after=new, expected_check="C2",
                description=f"{f.metric}（对象 {subject}）在 p{f.page} 被改成 {new}，与 p{first.page} 的 {old} 冲突",
            )
    return None


# ── C1：同一来源样本数不一致 ────────────────────────────────────
# ⚠️ 来源短语要吃到**完整**（如 `from the AmeriFlux Dataset`）。
#    首版只吃到首个大写词（`from the AmeriFlux`），注入后变成
#    `…from the AmeriFlux, and 12 sites from the AmeriFlux Dataset`：
#    后半句的来源被逗号截断成 `ameriflux`，与前半句的 `ameriflux dataset`
#    不同 —— 检查器会**正确地**判为不同来源而不报，使“注入的缺陷”抓不到。
_COUNT_CTX = re.compile(
    r"(\d[\d,]{0,6}\s*(?:sites?|stations?|samples?|windows?))"
    r"((?:\s+from)?\s+(?:the\s+)?([A-Z][A-Za-z]{2,}(?:\s+[A-Z][A-Za-z]{2,})?))",
    re.IGNORECASE,
)


def inject_count_conflict(blocks: list[dict]) -> Injection | None:
    """找 `N sites from X`，在同句补一个不同 N 的**同一来源**。

    ⚠️ 必须**原样复用**原文的来源短语。首版重新拼了 `from {source}`，
    而原文是 `from the AmeriFlux Dataset` —— 两边对象串不同，检查器会正确地
    判为「不同来源」而不报，导致**注入的缺陷抓不到**（误以为是检查器漏报）。
    """
    for i, b in enumerate(blocks):
        if b.get("type") in ("picture", "formula"):
            continue
        text = b.get("text") or ""
        m = _COUNT_CTX.search(text)
        if not m:
            continue
        source = m.group(3).split()[0]
        # 过滤掉误匹配到的普通副词（如 "collectively"）—— 那不是数据来源
        if len(source) < 5 or source.lower().endswith("ly"):
            continue
        num = int(re.sub(r"[^\d]", "", m.group(1)) or 0)
        if num <= 0:
            continue
        unit = m.group(1).split()[-1]  # sites / stations / …
        src_phrase = m.group(2).strip()  # 原样的 "from the AmeriFlux"
        inserted = f", and {num + 10} {unit} {src_phrase}"
        old = m.group(1) + m.group(2)
        if _patch(blocks, i, old, old + inserted):
            return Injection(
                kind="C1", block_index=i, page=b.get("page"),
                before=old, after=old + inserted, expected_check="C1",
                description=(
                    f"同句补一个「{num + 10} … {src_phrase}」，"
                    f"与原有的「{num} … {src_phrase}」冲突"
                ),
            )
    return None


# ── 入口 ────────────────────────────────────────────────────────
INJECTORS = (
    inject_r2_out_of_range,
    inject_relation_violation,
    inject_cross_position_conflict,
    inject_count_conflict,
)


def inject_all(blocks: list[dict]) -> tuple[list[dict], list[Injection]]:
    """对一份解析产物注入全部可用缺陷。返回 (新 blocks, 注入清单)。"""
    working = _blocks_of(blocks)
    injections: list[Injection] = []
    for fn in INJECTORS:
        facts = extract_facts(working)  # 每次重新抽取，保证基于当前状态
        result = fn(working, facts) if fn is not inject_count_conflict else fn(working)
        if result is not None:
            injections.append(result)
    return working, injections
