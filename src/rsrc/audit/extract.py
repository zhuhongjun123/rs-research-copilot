"""数值型事实抽取（C1–C6 的输入层）。

## 设计依据

正则**不是想出来的，是在作者真实论文上实测出来的**。实测到的书写形态：

```
R² ≈0.73                            约等号
R ² of 0.981                        上标前有空格 + 连接词 of
R 2 reduced from 0.055 to 0.0036    上标被拆成「空格+2」；区间式
an RMSE of 0.0125                   「指标 of 值」
reduced RMSE (23.88 mm/month)       括号 + 单位
bias < 0.001, RMSE < 0.05           不等式
reached ~0.02                       波浪号近似
```

**教训**：最初按「`指标 = 数值`」写正则，在 4 篇真实论文上命中 **0 处**。
科学论文的数值是**散文式**表达的，归一化层不可省。

## 分层

1. `normalize_text` —— 上标/破折号/全角字符归一
2. `extract_facts` —— 抽 (指标, 值, 关系, 位置)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

# ── 指标名（含同义写法）─────────────────────────────────────────
_METRIC_CANON: dict[str, str] = {
    "r²": "R²",
    "r2": "R²",
    "rmse": "RMSE",
    "ubrmse": "ubRMSE",
    "mae": "MAE",
    "mbe": "MBE",
    "bias": "Bias",
    "ve": "VE",
    "nse": "NSE",
    "kge": "KGE",
}

# 允许「R ²」「R 2」「R2」等被拆开的写法
_METRIC_RE = re.compile(
    r"\b(r\s*²|r\s*2|ubrmse|rmse|mae|mbe|bias|nse|kge|ve)\b",
    re.IGNORECASE,
)

# 指标与数值之间的「连接段」——**白名单 token 扫描**。
#
# 演进过程（两次实测驱动）：
#   1. 枚举完整连接串（`= : of is …`）→ 必然漏，只抽到 21 条
#   2. 盲扫「不含数字的字符」 → 抽到 66 条，但会跨到下一分句：
#      实测「LST bias using DEMs matched to the 5000 m」把 5000 当成了 bias 值
#   3. **白名单 token 扫描** → 既不枚举完整连接串，也不会跨分句
_LINK_MAX = 60
_SENTENCE_END = "。;；\n"

# 允许出现在指标与数值之间的词（小写）
_CONNECTOR_WORDS = {
    "of", "is", "are", "was", "were", "be", "been", "being",
    "reached", "reaches", "reach", "reaching",
    "equal", "equals", "equaled", "equalled", "equalto",
    "at", "from", "to", "by", "about", "approximately", "approx",
    "reduced", "increased", "decreased", "dropped", "decreasing",
    "increasing", "declined", "improved", "higher", "lower", "than",
    "value", "values", "mean", "average", "only", "just", "nearly",
    "almost", "around", "respectively", "and", "or", "up", "down",
}
# 允许的符号（注意：**不含 `.`** —— 句末与小数点都以它开头，一律终止）
_CONNECTOR_SYMS = set("=:：~≈<>≤≥()[],;-+\u2212\u2013\u2014")


def _scan_link(rest: str) -> int:
    """返回连接段长度：从 rest 开头扫到数值前的字符数。

    只允许白名单词与符号；碰到任何其它 token 立即停（不跨分句）。
    """
    i = 0
    limit = min(_LINK_MAX, len(rest))
    while i < limit:
        ch = rest[i]
        if ch.isdigit():
            break
        if ch in _SENTENCE_END:
            break
        if ch.isspace() or ch in _CONNECTOR_SYMS:
            i += 1
            continue
        word = re.match(r"[A-Za-z]+", rest[i:])
        if word and word.group(0).lower() in _CONNECTOR_WORDS:
            i += word.end()
            continue
        break  # 非白名单 token → 中间隔了别的东西，不是数值连接段
    return i

# 数值前可能出现的近似标记
_APPROX_RE = re.compile(r"[≈~\u2248]|about\s+|around\s+|approximately\s+|约")
# 不等式标志
_INEQ_RE = re.compile(r"<=|>=|≤|≥|<|>")
_NUMBER_RE = re.compile(r"^(-?\d+(?:\.\d+)?)")

# 句末判定：**标点后跟大写字母（英文）或 CJK（中文）**才算断句。
#
# ⚠️ 不能用「前面不是数字」来排除小数点 —— 那会把「数字后紧跟句号」也误排除：
#    实测 `…an RMSE of 0.02. After calibration … Bias reduced to 0.003`
#    两句被并成一句，于是拿校准前的 Bias 与校准后的 RMSE 互比 → C3 误报。
#    正确做法：小数点后必跟数字（无空白），句号后跟空白+大写。
_SENT_BOUND_RE = re.compile(r"(?:[.;!?](?=\s+[A-Z])|[。；！？])")


def _sentence_of(text: str, pos: int) -> int:
    """pos 属于 block 内第几句（从 0 开始）。"""
    return len(_SENT_BOUND_RE.findall(text[:pos]))
# 单位（可选，跟在数值后）
_UNIT_RE = re.compile(
    r"^\s*(%|K|°C|℃|mm/month|mm/day|mm|W/m²|W/m2|g/m²|g/m2|m/s|kg/m²)?"
)

# 区间：from X to Y / X–Y / X 到 Y
_RANGE_TO_RE = re.compile(r"^\s*(?:to|~|-|–|—|至|到)\s*", re.IGNORECASE)


@dataclass(frozen=True)
class NumericFact:
    """一条数值型事实。"""

    metric: str
    value: float
    unit: str
    relation: str  # "=" / "≈" / "<" / ">"；区间第二值标 "to"
    page: int | None
    block_type: str
    context: str  # 原文片段，供人工核验
    block_index: int = -1  # 稳定标识：第几个 block
    # context 是**各自居中的窗口**，同一句里两条事实的 context 并不相同 ——
    # 拿 context 当「同句」判据必然失败（实测：C1/C3 因此完全抓不到矛盾）。
    sentence: int = -1  # 稳定标识：block 内的第几句
    # ⚠️ 为什么还要到句级：block 可能是整段，段内多句常谈**不同对象**。
    #    实测论文1 同段内 `MAE up to 10 mm/month`（讲 BBE 差异）与
    #    `reduced RMSE (by 2.29-3.65)`（讲改进后的估算）被放在一起比，
    #    得出「RMSE < MAE 不可能」的**误报**。
    #    数学关系（RMSE≥MAE、|Bias|≤RMSE）只在**同一对象的同一句**内成立。

    def key(self) -> tuple:
        return (self.metric, self.value, self.unit, self.relation)


def normalize_text(text: str) -> str:
    """文本归一化 —— C1/C2 能否工作的前提。"""
    if not text:
        return ""
    out = text
    # 上标被拆：「R ²」「R 2」「R2」→ R²
    out = re.sub(r"\bR\s*²", "R²", out)
    out = re.sub(r"\bR\s*2\b", "R²", out)
    out = re.sub(r"\bR2\b", "R²", out)
    # 各种破折号 → 减号（负值）
    for dash in ("\u2212", "\u2013", "\u2014", "\u2015"):
        out = out.replace(dash, "-")
    # 全角 → 半角（数字与符号）
    for full, half in (("：", ":"), ("（", "("), ("）", ")"), ("％", "%"), ("，", ",")):
        out = out.replace(full, half)
    # 数字被空格拆开：0. 0125 → 0.0125
    out = re.sub(r"(?<=\d)\s+(?=[.,]\d)", "", out)
    # 小数点被渲染成冒号：`0 : 055` → `0.055`（实测论文4 p16）
    out = re.sub(r"\b(\d)\s*:\s*(\d{2,3})\b", r"\1.\2", out)
    # 无法解码的替换字符（实测它替掉了 '='）—— 移除，关系回退为等值
    out = out.replace("\ufffd", "")
    # 移除后可能留下多余空白，统一折叠
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out


def _canon_metric(raw: str) -> str:
    return _METRIC_CANON.get(re.sub(r"\s+", "", raw).lower(), raw.upper())


def extract_facts(
    blocks: Iterable[dict],
    *,
    context_window: int = 70,
) -> list[NumericFact]:
    """从解析后的 block 列表里抽数值型事实。

    `blocks` 是 `scripts/parse_pdf.py` 的输出格式：
    `[{"page": int, "type": str, "text": str, ...}, ...]`
    """
    facts: list[NumericFact] = []

    for block_index, block in enumerate(blocks):
        if block.get("type") in ("picture", "formula"):
            continue  # 图内文字无法从文本层获得；公式另处理
        text = normalize_text(block.get("text") or "")
        if not text:
            continue

        for match in _METRIC_RE.finditer(text):
            metric = _canon_metric(match.group(1))
            rest = text[match.end() :]

            # 1) 连接段：白名单 token 扫描（不枚举完整连接串，也不跨分句）
            window = _scan_link(rest)
            link = rest[:window]

            # 2) 关系：不等式优先，其次近似，否则等值
            ineq = _INEQ_RE.search(link)
            if ineq:
                relation = ineq.group(0)
            elif _APPROX_RE.search(link):
                relation = "≈"
            else:
                relation = "="

            # 3) 数值
            num = _NUMBER_RE.match(rest[window:])
            if not num:
                continue
            value = float(num.group(1))
            num_end = window + num.end()

            # 4) 单位（可选）
            unit = ""
            um = _UNIT_RE.match(rest[num_end:])
            if um and um.group(1):
                unit = um.group(1)

            # ⚠️ num_end 是相对 rest 的偏移，必须换算回 text 的绝对坐标
            abs_end = match.end() + num_end
            # ⚠️ 左侧窗口要够宽：实测只取 20 字符时会把 `(TRI)` 这种对象括号**截断**，
            #    导致括号正则匹配不到 → 对象退化成邻近形容词 → C2 漏报。
            #    context 仅用于人工核验与对象抽取，宁宽勿窄。
            ctx_start = max(0, match.start() - 70)
            context = re.sub(r"\s+", " ", text[ctx_start : abs_end + 30])
            facts.append(
                NumericFact(
                    metric=metric,
                    value=value,
                    unit=unit,
                    relation=relation,
                    page=block.get("page"),
                    block_type=block.get("type", ""),
                    context=context,
                    block_index=block_index,
                    sentence=_sentence_of(text, match.start()),
                )
            )

            # 5) 区间式：from X to Y / X 到 Y —— 第二个值也作为一条事实
            tail = rest[num_end:]
            if _RANGE_TO_RE.match(tail):
                after = _RANGE_TO_RE.sub("", tail, count=1)
                m2 = _NUMBER_RE.match(after.lstrip())
                if m2:
                    facts.append(
                        NumericFact(
                            metric=metric,
                            value=float(m2.group(1)),
                            unit=unit,
                            relation="to",
                            page=block.get("page"),
                            block_type=block.get("type", ""),
                            context=context,
                            block_index=block_index,
                            sentence=_sentence_of(text, match.start()),
                        )
                    )

    return facts
