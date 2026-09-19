"""数值自洽检查项 C1–C6。

## 设计前提：误报率是成败关键

作者真实论文里同一指标有多种**合法的不同取值**（实测论文4 的 R² 有
`0.75 / 0.13 / 0.74-0.75 / 0 / 0.055 / 0.0036` —— 对象不同）。
所以 **C2 绝不能是「所有 R² 必须相等」**，必须先抽「对象」再比。
同理，每条 finding 都带 `subject` 与原文 `context`，**供人工复核**。

## 检查项分层

| 编号 | 类型 | 是否需要对象匹配 |
| --- | --- | --- |
| C3 | **纯确定性**（指标间数学关系） | 否 —— 最可靠，优先做 |
| C1 | 样本量跨位置一致 | 需（按「被计数的名词」分组） |
| C2 | 精度指标跨位置一致 | 需（按指标 + 对象分组） |
| C5 | 单位量纲一致 | 需（按指标分组） |
| C4 | 百分比可复算 | 需（依赖表格联动，v1 只做分母存在性） |
| C6 | 时间/空间范围一致 | 需（年份与经纬度区间） |
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from rsrc.audit.extract import NumericFact, _sentence_of

# ── 判定阈值 ────────────────────────────────────────────────────
ABS_TOL = 5e-4  # 绝对容差：小于此视为同值（应对 0.74 vs 0.740）
REL_TOL = 0.02  # 相对容差 2%：报告四舍五入常见


@dataclass
class Finding:
    """一条检查结论。每条都必须可被人复核。"""

    check: str  # C1..C6
    title: str
    verdict: str  # conflict / ok / unverifiable
    severity: str  # high / medium / info
    subject: str = ""
    detail: str = ""
    items: list[NumericFact] = field(default_factory=list)

    def line(self) -> str:
        mark = {"conflict": "⚠️", "ok": "✓", "unverifiable": "?"}[self.verdict]
        head = f"[{self.check}] {mark} {self.title}"
        if self.subject:
            head += f"（对象：{self.subject}）"
        out = [head]
        for f in self.items:
            loc = f"p{f.page}/{f.block_type}" if f.page else f.block_type
            val = f"{f.relation}{f.value}{(' ' + f.unit) if f.unit else ''}"
            out.append(f"    {loc:<16} {f.metric} {val}")
        if self.detail:
            out.append(f"    → {self.detail}")
        return "\n".join(out)


# ── 对象抽取 ────────────────────────────────────────────────────
# 目的：让「同一对象的指标」才能互比，避免跨对象误报。
_PAREN_RE = re.compile(r"\(([^()]{2,40})\)")
_STOP = {
    "the", "a", "an", "of", "for", "and", "or", "in", "on", "to", "is", "are",
    "was", "were", "with", "by", "at", "as", "that", "this", "these", "those",
    "than", "from", "between", "both", "also", "e.g", "i.e", "vs",
    # 实测：报告动词/泛指词不应成为对象，否则「report tri」与「tri」无法匹配
    "report", "reports", "reported", "show", "shows", "showed", "shown",
    "observed", "obtained", "found", "yield", "yields", "yielded", "give",
    "gives", "gave", "value", "values", "result", "results", "respectively",
    "reaching", "reached", "reaches", "about", "approximately", "which", "while",
    "where", "when", "here", "there", "our", "its", "their", "we", "it",
}

# 对象取「指标左侧最近的实词」——实测最稳：
#   `report for TRI R² = 0.21` 与 `(TRI) (R² ≈0.75)` 都应得 `tri`
_SUBJ_WORD_RE = re.compile(r"[A-Za-z][A-Za-z\-]{2,}")


_NUM_IN_TEXT = re.compile(r"-?\d+(?:\.\d+)?")


def extract_subject(fact: NumericFact) -> str:
    """从 context 里粗抽「这条数值说的是什么」。

    ⚠️ 实测踩过的坑：context 是**对称窗口**，会把邻居的括号也括进来。
    例如 `croplands (CRO; 9 sites), mixed forests (MF; 4 sites)` ——
    值为 9 的那条，窗口里含 `MF; 4 sites`，直接取最后一个括号就会拿错对象
    （导致把 CRO 的 9 和 MF 的 4 当成「同一对象冲突」）。

    所以：**含“不等于本事实值”的数字的括号一律丢弃**；再退回实词短语。
    抽不准时返回空串 —— **宁可不比，也不要乱比**（宁可漏报不可误报）。
    """
    ctx = fact.context or ""
    metric_token = re.sub(r"[^a-z0-9]", "", fact.metric.lower())
    for candidate in reversed(_PAREN_RE.findall(ctx)):
        nums = _NUM_IN_TEXT.findall(candidate)
        if nums and any(abs(float(n) - fact.value) > ABS_TOL for n in nums):
            continue  # 括号里的数字与本事实不符 → 不是它的对象
        # ⚠️ 括号里装的**就是「指标+值」本身**时（如 `(R² ≈0.75)`），
        #    剔掉数字后只剩指标名 —— 那不是对象，别把它当对象（实测出过 `r² ≈`）。
        # ⚠️ 不能用「指标 token 是否为子串」判定：`R²` 归一后只剩单字母 `r`，
        #    `"r" in "tri"` 恒真（误杀合法对象）；而关掉守卫又会让 `(R² ≈0.75)` 通过。
        #    正确做法：**剔掉数字与指标名后，看还剩不剩实词**。
        residue = _NUM_IN_TEXT.sub("", candidate)
        residue = re.sub(re.escape(fact.metric), "", residue, flags=re.IGNORECASE)
        if len(re.sub(r"[^A-Za-z]", "", residue)) < 2:
            continue  # 剩下的只是符号/指标本身 → 不是对象
        cleaned = re.sub(r"\s+", " ", _NUM_IN_TEXT.sub("", candidate)).strip(" ;,.")
        if cleaned:
            return cleaned.lower()[:40]

    # 退路：**指标左侧最近的实词**。
    # 右侧往往是数值与句末，拿不到东西；左侧才是对象所在（如 `for TRI R² = …`）。
    # ⚠️ 必须用 rpartition（**最后一次**出现）：同一段里若前面还有另一个同名指标，
    #    partition 会切到别人那里去 —— 实测把 `for TRI R² = 0.21` 的对象抽成了
    #    上一句的形容词 `strong`。
    left = ctx.rpartition(fact.metric)[0] if fact.metric in ctx else ctx
    left = re.sub(r"[-−]?\d+(?:\.\d+)?", " ", left)
    words = [w for w in _SUBJ_WORD_RE.findall(left) if w.lower() not in _STOP]
    if not words:
        return ""
    # ⚠️ 质量门槛：退路很容易吐出垃圾，垃圾对象会直接变成误报 ——
    #    **宁可返回空串（不比），不要返回垃圾（乱比）**。
    subject = words[-1].lower()
    if len(subject) < 3:
        return ""
    if subject.replace("²", "") == fact.metric.lower().replace("²", ""):
        return ""
    return subject


# 实测误报源：退路抽出的「对象」大量是通用词
# （`low` / `day` / `compared` / `method` / `coverage` / `regions` / `std` /
#   `achieved` / `correction` / `rmse` …）—— 它们不是实体，
# 两组数字本就属于不同对象，强行归为一组就是误报。
_GENERIC_SUBJECTS = {
    "low", "high", "day", "night", "year", "month", "summer", "winter",
    "method", "methods", "model", "models", "result", "results", "compared",
    "coverage", "conditions", "regions", "region", "achieved", "std", "correction",
    "rmse", "bias", "mae", "value", "values", "case", "cases", "set", "sets",
    "study", "paper", "data", "dataset", "scale", "site", "sites", "pixel",
    "range", "mean", "average", "total", "overall", "general", "different",
}


def distinctive_subject(fact: NumericFact) -> str:
    """只返回**有辨识度**的对象；否则返回空串（意味着不比较）。

    判定“有辨识度”的三类（实测能稳定区分的）：
      1. 来自括号（如 `(TRI)`）
      2. 缩写（全大写 ≥3，如 `LST` / `LSE` / `DEM`）
      3. 含连字符或数字（如 `18CrNiMo7-6`、`FY-3`）
    其余退路抽出的普通词一律不信任 —— **宁漏报不误报**。
    """
    ctx = fact.context or ""
    for candidate in reversed(_PAREN_RE.findall(ctx)):
        nums = _NUM_IN_TEXT.findall(candidate)
        if nums and any(abs(float(n) - fact.value) > ABS_TOL for n in nums):
            continue
        residue = _NUM_IN_TEXT.sub("", candidate)
        residue = re.sub(re.escape(fact.metric), "", residue, flags=re.IGNORECASE)
        cleaned = re.sub(r"[^A-Za-z0-9\-]", "", residue)
        if len(cleaned) >= 3 and cleaned.lower() not in _GENERIC_SUBJECTS:
            return cleaned.lower()

    subject = extract_subject(fact)
    if not subject or subject in _GENERIC_SUBJECTS:
        return ""
    if re.search(r"[-\d]", subject):  # 带连字符/数字的型号
        return subject.lower()
    # 缩写：在**原文里以全大写形式出现**（如 `… report for TRI R² = 0.21`）。
    # 实测必需：对象常以缩写出现而不带括号，漏了这条会丢掉真实对比
    #（同一对象的跨位置一致性就抓不到了）。
    if re.search(rf"\b{re.escape(subject.upper())}\b", ctx):
        return subject.lower()
    return ""


def _same(a: float, b: float) -> bool:
    if abs(a - b) <= ABS_TOL:
        return True
    denom = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / denom <= REL_TOL


# ── C3：指标间的数学关系（纯确定性，最可靠）────────────────────
def check_c3_relations(facts: list[NumericFact]) -> list[Finding]:
    """检查不可能成立的数值关系。

    这类错误**与对象无关**：只要同一（局部）范围内出现即成矛盾。
    目前覆盖：
      - R² 必须落在 [0, 1]（负值需显式说明，v1 只报 <0 或 >1）
      - RMSE ≥ MAE（同一场景下）
      - Bias 与 RMSE 的绝对值关系（|Bias| ≤ RMSE，数学上恒成立；违反即解析或原文错误）
    """
    out: list[Finding] = []

    for f in facts:
        if f.metric == "R²" and not (0.0 <= f.value <= 1.0):
            out.append(Finding(
                check="C3", title="R² 超出 [0,1] 值域", verdict="conflict",
                severity="high", subject=extract_subject(f),
                detail=f"取值 {f.value}，R² 定义域为 [0,1]（负值需在文中显式说明）",
                items=[f],
            ))

    # |Bias| ≤ RMSE / RMSE ≥ MAE —— 只在**同一个 block（同段句）**内成立才算。
    # ⚠️ 不能用 context 当分组键：context 是各自居中的窗口，同句两条事实的
    #    context 并不相同（实测因此完全抓不到矛盾）。
    # 数学关系只在**同一对象的同一句**内成立。
    #
    # ⚠️ 仅靠「同句」不够 —— 实测在 12 篇语料上产生 8 条「|Bias| > RMSE」误报：
    #    同一句里 Bias 与 RMSE 讲的**不是同一个对象**。所以再要求两者共享一个
    #    **有辨识度的对象**；对象不可得则不比较（宁漏报，不要报出错误的“数学不可能”）。
    by_ctx: dict[tuple, dict[str, list[NumericFact]]] = defaultdict(lambda: defaultdict(list))
    for f in facts:
        if f.metric in ("RMSE", "Bias", "MAE"):
            key = (f.page, f.block_index, f.sentence, distinctive_subject(f))
            by_ctx[key][f.metric].append(f)

    for ctx, group in by_ctx.items():
        if not ctx[3]:
            continue  # 对象不可辨识 → 不做关系判定
        rmse = group.get("RMSE", [])
        for other in ("MAE", "Bias"):
            for a in rmse:
                for b in group.get(other, []):
                    if b.relation in ("<", ">"):  # 不等式不参与关系判定
                        continue
                    if other == "MAE" and a.value < b.value - ABS_TOL:
                        out.append(Finding(
                            check="C3", title="RMSE 小于 MAE（数学上不可能）",
                            verdict="conflict", severity="high",
                            subject=extract_subject(a),
                            detail=f"RMSE {a.value} < MAE {b.value}",
                            items=[a, b],
                        ))
                    if other == "Bias" and abs(b.value) > a.value + ABS_TOL:
                        out.append(Finding(
                            check="C3", title="|Bias| 大于 RMSE（数学上不可能）",
                            verdict="conflict", severity="high",
                            subject=extract_subject(a),
                            detail=f"|Bias| {abs(b.value)} > RMSE {a.value}",
                            items=[a, b],
                        ))
    return out


# ── C2：同一对象的指标跨位置一致 ────────────────────────────────
def check_c2_metric_consistency(facts: list[NumericFact]) -> list[Finding]:
    """只比较**同一指标 + 同一对象**的不同出现位置。"""
    out: list[Finding] = []
    groups: dict[tuple[str, str], list[NumericFact]] = defaultdict(list)
    for f in facts:
        if f.metric not in ("R²", "RMSE", "MAE", "Bias", "MBE", "ubRMSE", "NSE", "KGE"):
            continue
        # 不等式不是确定值，不可比
        if f.relation in ("<", ">"):
            continue
        # ⚠️ relation="to" 是**区间声明的端点**（reduced from 0.055 to 0.0036），
        #    不是两个互相冲突的独立值 —— 拿它互比会产生 100% 误报。
        if f.relation == "to":
            continue
        # ⚠️ 只用**有辨识度**的对象。退路抽出的通用词（low/method/compared…）
        #    会把不同对象的数字归为一组 —— 实测在 12 篇语料上产生 14 条误报。
        subject = distinctive_subject(f)
        if not subject:
            continue  # 对象不可靠 → 不比较（宁漏报不误报）
        groups[(f.metric, subject)].append(f)

    for (metric, subject), items in groups.items():
        if len(items) < 2:
            continue
        values = [f.value for f in items]
        if all(_same(values[0], v) for v in values[1:]):
            continue  # 一致，不必出 finding
        lo, hi = min(values), max(values)
        out.append(Finding(
            check="C2", title=f"{metric} 同一对象出现不同取值",
            verdict="conflict", severity="medium", subject=subject,
            detail=f"取值 {lo} ~ {hi}（相对差 {abs(hi - lo) / max(abs(hi), 1e-9):.1%}），需人工确认是否为同一对象",
            items=items,
        ))
    return out


# ── C1：样本量跨位置一致 ────────────────────────────────────────
_COUNT_RE = re.compile(
    r"(\d[\d,]{0,7})\s*(?:个)?\s*"
    r"(sites?|stations?|samples?|pixels?|windows?|points?|站点|站|样本|像元|窗口|点)\b",
    re.IGNORECASE,
)


def _count_subject(text: str, end: int, window: int = 46) -> str:
    """样本数的对象——在**右侧**（`… 12 sites from the AmeriFlux Dataset`）。

    ⚠️ 与指标不同：样本数的对象几乎总在值的右边（数据来源/数据集名）。
    """
    # ⚠️ 右侧窗口必须在**第一个句读**处截断：否则会跳到下一分句，
    #    把「12 sites from FLUXNET; … 22 sites from FLUXNET」抽成两个不同对象。
    tail = re.split(r"[,.;;；，]", text[end : end + window], maxsplit=1)[0]
    tail = re.sub(r"[-−]?\d+(?:\.\d+)?", " ", tail)
    words = [w for w in _SUBJ_WORD_RE.findall(tail) if w.lower() not in _STOP]
    # ⚠️ 取**三个**实词而不是一个：地类名常共享首词 ——
    #    `evergreen needleleaf forests` 与 `evergreen broadleaf forests`
    #    只取首词会双双缩成 `evergreen` 而撞车，产生误报。
    return " ".join(words[:3]).lower() if words else ""


def check_c1_counts(blocks: list[dict]) -> list[Finding]:
    """比较样本数跨位置是否一致。

    ⚠️ 极容易变成噪音：一篇论文里「sites」本来就有多种合法取值
    （实测论文1 有 1/2/3/4/5/9/10/11/12/17 个站的不同分析）。

    **句级切分不够**——实测论文1 那批站点数就在**同一句**里（逗号分隔）：
    `… 1 2 sites from the AmeriFlux Dataset, 2 2 sites from the European …, 3 and 11 sites …`
    区分它们的是**数据集名**（右侧对象）。所以分三档：
      - 右侧对象不同 → 合法的不同数据集，**不报**
      - 右侧对象相同但取值不同 → conflict
      - 对象抽不出 → 只报 info，交人工；**不报 conflict**
    """
    groups: dict[str, list[tuple[NumericFact, str]]] = defaultdict(list)
    for block_index, b in enumerate(blocks):
        if b.get("type") in ("picture", "formula"):
            continue
        text = re.sub(r"\s+", " ", b.get("text") or "")
        for m in _COUNT_RE.finditer(text):
            unit = m.group(2).lower()
            norm_unit = {
                "sites": "sites", "site": "sites", "stations": "stations", "station": "stations",
                "samples": "samples", "sample": "samples", "pixels": "pixels", "pixel": "pixels",
                "windows": "windows", "window": "windows", "points": "points", "point": "points",
            }.get(unit, unit)
            fact = NumericFact(
                metric="样本量",
                value=float(m.group(1).replace(",", "")),
                unit=norm_unit,
                relation="=",
                page=b.get("page"),
                block_type=b.get("type", ""),
                context=re.sub(r"\s+", " ", text[max(0, m.start() - 40): m.end() + 30]),
                block_index=block_index,
                sentence=_sentence_of(text, m.start()),
            )
            groups[norm_unit].append((fact, _count_subject(text, m.end())))

    out: list[Finding] = []
    for unit, pairs in groups.items():
        items = [f for f, _ in pairs]
        values = sorted({f.value for f in items})
        if len(values) < 2:
            continue

        # ① 右侧对象相同但取值不同 → 真矛盾
        by_subject: dict[str, list[NumericFact]] = defaultdict(list)
        for f, subj in pairs:
            if subj:
                by_subject[subj].append(f)
        hit = False
        for subj, g in by_subject.items():
            vals = sorted({f.value for f in g})
            if len(vals) > 1:
                out.append(Finding(
                    check="C1", title=f"同一来源「{subj}」的「{unit}」样本数不一致",
                    verdict="conflict", severity="medium", subject=subj,
                    detail=f"取值 {vals}", items=g[:6],
                ))
                hit = True
        if hit:
            continue

        # ③ 对象抽不出 → 只报 info，交人工；**不报 conflict，避免刷误报**
        out.append(Finding(
            check="C1", title=f"「{unit}」共有 {len(values)} 种样本数取值",
            verdict="unverifiable", severity="info", subject=unit,
            detail=(f"取值 {values[:10]}"
                    + (" …" if len(values) > 10 else "")
                    + " —— 不同数据集属正常；若指同一批数据则为口径不一致"),
            items=items[:4],
        ))
    return out


# ── C5：单位量纲一致 ────────────────────────────────────────────
def check_c5_units(facts: list[NumericFact]) -> list[Finding]:
    """单位量纲一致性。

    分两档（与 C2 同思路）：
      - **同一对象**用了不同单位 → conflict（真矛盾）
      - 不同对象用不同单位 → 合法，不报
      - 对象抽不出 → 报 info 交人工
    """
    groups: dict[tuple[str, str], set[str]] = defaultdict(set)
    examples: dict[tuple[str, str], list[NumericFact]] = defaultdict(list)
    for f in facts:
        if f.metric == "样本量" or not f.unit:
            continue
        key = (f.metric, distinctive_subject(f))
        groups[key].add(f.unit)
        examples[key].append(f)

    out: list[Finding] = []
    for (metric, subject), units in groups.items():
        if len(units) < 2:
            continue
        out.append(Finding(
            check="C5",
            title=(f"同一对象「{subject}」的 {metric} 用了不同单位"
                   if subject else f"{metric} 使用了多种单位"),
            verdict="conflict" if subject else "unverifiable",
            severity="medium" if subject else "info",
            subject=subject or metric,
            detail=f"单位：{sorted(units)}",
            items=[f for f in examples[(metric, subject)] if f.unit][:6],
        ))
    return out


# ── 汇总 ────────────────────────────────────────────────────────
def run_checks(blocks: list[dict], facts: list[NumericFact]) -> list[Finding]:
    """跑 C1–C6，按严重度排序返回。"""
    findings: list[Finding] = []
    findings += check_c3_relations(facts)
    findings += check_c2_metric_consistency(facts)
    findings += check_c1_counts(blocks)
    findings += check_c5_units(facts)
    order = {"high": 0, "medium": 1, "info": 2}
    findings.sort(key=lambda f: (order.get(f.severity, 9), f.check))
    return findings
