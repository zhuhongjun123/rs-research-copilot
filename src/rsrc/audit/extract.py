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

# 指标与数值之间的连接方式。实测有：= : ：  of  is  are  was  were  reached  (  空格
_CONNECTOR_RE = re.compile(
    r"^\s*(?:[=:：]|of\s|is\s|are\s|was\s|were\s|reached\s|equal(?:s|led)?\s|at\s|\()?\s*",
    re.IGNORECASE,
)

# 数值前可能出现的近似标记
_APPROX_RE = re.compile(r"^(?:[≈~\u2248]|about\s+|around\s+|approximately\s+|约\s*)?")
# 不等式
_INEQ_RE = re.compile(r"^(<=|>=|≤|≥|<|>)\s*")
_NUMBER_RE = re.compile(r"^(-?\d+(?:\.\d+)?)")
# 单位（可选，跟在数值后）
_UNIT_RE = re.compile(
    r"^\s*(%|K|°C|℃|mm/month|mm/day|mm|W/m²|W/m2|g/m²|g/m2|m/s|kg/m²)?"
)

# 区间：from X to Y / X–Y / X 到 Y
_RANGE_FROM_RE = re.compile(r"^\s*from\s+", re.IGNORECASE)
_RANGE_TO_RE = re.compile(r"^\s*(?:to|~|-|–|—|至|到)\s*", re.IGNORECASE)


@dataclass(frozen=True)
class NumericFact:
    """一条数值型事实。"""

    metric: str
    value: float
    unit: str
    relation: str  # "=" / "≈" / "<" / ">" / 区间则标 "from"/"to"
    page: int | None
    block_type: str
    context: str  # 原文片段，供人工核验

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

    for block in blocks:
        if block.get("type") in ("picture", "formula"):
            continue  # 图内文字无法从文本层获得；公式另处理
        text = normalize_text(block.get("text") or "")
        if not text:
            continue

        for match in _METRIC_RE.finditer(text):
            metric = _canon_metric(match.group(1))
            rest = text[match.end() :]

            # 1) 连接词（= : ： of is was reached ( 或空白）
            conn = _CONNECTOR_RE.match(rest)
            cursor = conn.end() if conn else 0

            # 2) 不等式 或 近似标记；都没有则是等值
            ineq = _INEQ_RE.match(rest[cursor:])
            if ineq:
                relation = ineq.group(1).strip()
                cursor += ineq.end()
            else:
                approx = _APPROX_RE.match(rest[cursor:])
                if approx and approx.group(0):
                    relation = "≈"
                    cursor += approx.end()
                else:
                    relation = "="

            # 3) 数值
            num = _NUMBER_RE.match(rest[cursor:])
            if not num:
                continue
            value = float(num.group(1))
            num_end = cursor + num.end()

            # 4) 单位（可选）
            unit = ""
            um = _UNIT_RE.match(rest[num_end:])
            if um and um.group(1):
                unit = um.group(1)

            # ⚠️ num_end 是相对 rest 的偏移，必须换算回 text 的绝对坐标
            abs_end = match.end() + num_end
            ctx_start = max(0, match.start() - 20)
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
                )
            )

            # 5) 区间式：from X to Y —— 第二个值也作为一条事实
            tail = rest[num_end:]
            if _RANGE_FROM_RE.match(rest[cursor:] or " ") or _RANGE_TO_RE.match(tail):
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
                        )
                    )

    return facts
