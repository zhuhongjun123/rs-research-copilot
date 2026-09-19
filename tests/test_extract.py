#!/usr/bin/env python
"""数值抽取自检。

用例**全部来自作者 4 篇论文的实测形态**（不是构造的），
见 RUN_LOG.md「发现 9 / 发现 10」。

    pixi run py tests/test_extract.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rsrc.audit.extract import NumericFact, extract_facts, normalize_text  # noqa: E402

PARSED = ROOT / "data" / "index" / "parsed"


def _facts(text: str, page: int = 1, btype: str = "text") -> list[NumericFact]:
    return extract_facts([{"page": page, "type": btype, "text": text}])


# ── 归一化：实测得到的真实变体 ─────────────────────────────────
def test_normalize_superscript_variants() -> None:
    for src in ("R² of 0.981", "R ² of 0.981", "R 2 of 0.981", "R2 of 0.981"):
        assert normalize_text(src) == "R² of 0.981", src
    print("    R² / R ² / R 2 / R2 → R² 全部归一")


def test_normalize_broken_decimal() -> None:
    """论文4 p16 实测：小数点被渲染成冒号。"""
    assert normalize_text("R 2 \ufffd 0 : 055 for") == "R² 0.055 for"
    print("    「0 : 055」→ 0.055；U+FFFD 被移除")


def test_normalize_dashes_and_fullwidth() -> None:
    assert normalize_text("0.85 − 0.02：0.5") == "0.85 - 0.02:0.5"
    print("    破折号 → 减号；全角 → 半角")


# ── 抽取：实测的真实句式 ───────────────────────────────────────
def test_extract_measured_forms() -> None:
    """每例显式给出「指标名 + 实测写法」，不靠启发式拼接。"""
    cases = [
        ("R² ≈0.73", "R²", 0.73, "≈"),
        ("RMSE of 0.0125", "RMSE", 0.0125, "="),
        ("R ² of 0.981", "R²", 0.981, "="),
        ("Bias < 0.001", "Bias", 0.001, "<"),
        ("Bias reached ~0.02", "Bias", 0.02, "≈"),
        ("RMSE (23.88 mm/month)", "RMSE", 23.88, "="),
        ("Bias is 0.02", "Bias", 0.02, "="),
        ("MAE = 0.612", "MAE", 0.612, "="),
    ]
    for text, metric, value, relation in cases:
        found = [f for f in _facts(text) if f.metric == metric]
        assert found, f"未抽到：{text}"
        f = found[0]
        assert abs(f.value - value) < 1e-9, f"{text} → 值 {f.value} ≠ {value}"
        assert f.relation == relation, f"{text} → 关系 {f.relation} ≠ {relation}"
    print(f"    {len(cases)} 种实测句式全部抽对（含值、关系）")


def test_extract_range_from_to() -> None:
    """论文4 摘要的核心发现用的是区间式，早期版本漏抽。"""
    facts = _facts("(R² reduced from 0.055 to 0.0036)")
    vals = sorted(f.value for f in facts)
    assert vals == [0.0036, 0.055], vals
    assert any(f.relation == "to" for f in facts), "区间第二值应标 relation=to"
    print(f"    区间式抽到两值：{vals}")


def test_extract_carries_position() -> None:
    facts = _facts("(R² reduced from 0.055 to 0.0036)", page=16, btype="caption")
    assert facts and all(f.page == 16 and f.block_type == "caption" for f in facts)
    print("    位置信息（页码 + block 类型）随事实一同返回")


def test_context_not_empty() -> None:
    """曾经的 bug：context 因相对/绝对偏移混用而为空。"""
    facts = _facts("The result shows R² = 0.981 for MODIS albedo product.")
    assert facts, "未抽到"
    assert facts[0].context.strip(), "context 为空 —— 偏移换算又错了"
    assert "0.981" in facts[0].context
    print(f"    context 非空：「{facts[0].context[:56]}」")


def test_skips_picture_blocks() -> None:
    """图内文字不可靠，必须跳过。"""
    assert _facts("R² = 0.99", btype="picture") == []
    print("    picture 块被跳过")


def test_regression_on_real_papers() -> None:
    """在真实解析产物上回归：4 篇合计应远超早期版本的 0 条。"""
    if not PARSED.is_dir():
        print("    跳过（未找到 data/index/parsed）")
        return
    files = sorted(PARSED.glob("*.json"))
    if not files:
        print("    跳过（解析产物为空）")
        return
    total = 0
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        total += len(extract_facts(data["blocks"]))
    assert total >= 20, f"真实论文上只抽到 {total} 条（早期实测 21 条）"
    print(f"    {len(files)} 篇真实论文共抽到 {total} 条")


def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        try:
            fn()
        except AssertionError as exc:
            print(f"  ✗ {fn.__name__}: {exc}")
            failed += 1
        else:
            print(f"  ✓ {fn.__name__}")
    print("-" * 58)
    print(f"{len(tests) - failed}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
