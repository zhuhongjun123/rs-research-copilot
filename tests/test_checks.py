#!/usr/bin/env python
"""C1–C6 检查项自检。

**必须同时证明两件事**：
  ① **召回**：注入的已知矛盾能被抓到
  ② **精确**：合法情形（不同对象/区间声明/垃圾对象）**不被误报**

误报是这个工具的死穴 —— 实测中「区间端点被当成两个冲突值」「把邻居括号当成本条对象」
「把图轴刻度当指标值」都曾造成过 100% 误报。

    pixi run py tests/test_checks.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rsrc.audit.checks import (  # noqa: E402
    check_c1_counts,
    check_c2_metric_consistency,
    check_c3_relations,
    extract_subject,
    run_checks,
)
from rsrc.audit.extract import extract_facts  # noqa: E402


def _blk(text: str, page: int = 1, btype: str = "text") -> dict:
    return {"page": page, "type": btype, "text": text}


def _findings(text: str, page: int = 1):
    blocks = [_blk(text, page)]
    return run_checks(blocks, extract_facts(blocks))


# ── ① 召回：注入的已知矛盾必须被抓到 ────────────────────────────
def test_c3_catches_rmse_lt_mae() -> None:
    f = _findings("The model has RMSE = 0.42 and MAE = 0.61 in this region.")
    assert any(x.check == "C3" and "MAE" in x.title for x in f), [x.title for x in f]
    print("    RMSE < MAE 被抓到（数学上不可能）")


def test_c3_catches_bias_gt_rmse() -> None:
    f = _findings("Performance: RMSE = 0.30, Bias = 0.85 for the same site.")
    assert any(x.check == "C3" and "Bias" in x.title for x in f), [x.title for x in f]
    print("    |Bias| > RMSE 被抓到")


def test_c3_catches_r2_out_of_range() -> None:
    f = _findings("The agreement was excellent with R² = 1.24 across all sites.")
    assert any(x.check == "C3" and "值域" in x.title for x in f), [x.title for x in f]
    print("    R² 超出 [0,1] 被抓到")


def test_c2_catches_same_subject_conflict() -> None:
    """同一对象（TRI）在不同位置给了不同值 —— 这是 C2 的目标场景。"""
    text = (
        "The relationship with the Terrain Ruggedness Index (TRI) was strong, "
        "reaching R² = 0.75. In the discussion we again report for TRI R² = 0.21."
    )
    f = _findings(text)
    hit = [x for x in f if x.check == "C2"]
    assert hit, [x.title for x in f]
    print(f"    同一对象跨位置冲突被抓到：{hit[0].subject}")


def test_c1_catches_same_source_conflict() -> None:
    """**同一来源**（FLUXNET）给了两个不同的站点数 —— 这是真矛盾。"""
    f = check_c1_counts([_blk("We validated at 12 sites from FLUXNET; later we report 22 sites from FLUXNET.")])
    assert any(x.verdict == "conflict" for x in f), [x.title for x in f]
    print("    同一来源的样本数不一致被抓到")


def test_c1_ignores_different_sources() -> None:
    """实测误报源：同一句里不同数据集的站点数是**合法**的（论文1 p2）。"""
    text = (
        "The validation data were obtained from 2 sites from the AmeriFlux Dataset, "
        "2 sites from the European FluxNet Dataset, and 11 sites from a Tibetan dataset."
    )
    f = check_c1_counts([_blk(text)])
    assert not [x for x in f if x.verdict == "conflict"], [x.title for x in f]
    print("    不同数据集的站点数未被误报")


# ── ② 精确：合法情形绝不能被误报 ────────────────────────────────
def test_c2_ignores_range_endpoints() -> None:
    """实测误报源：`reduced from 0.055 to 0.0036` 是一个区间声明，不是两个冲突值。"""
    f = _findings("The correction removed the pseudo-correlation (R² reduced from 0.055 to 0.0036).")
    assert not [x for x in f if x.check == "C2" and x.verdict == "conflict"], \
        [x.title for x in f]
    print("    区间端点未被当成冲突（曾 100% 误报）")


def test_c2_ignores_different_subjects() -> None:
    """不同对象的不同取值是合法的。"""
    text = (
        "Stratified analysis shows R² ≈ 0.75 with the Terrain Ruggedness Index (TRI). "
        "Separately, the residuals vary with absolute elevation (elevation) and give R² ≈ 0.13."
    )
    f = _findings(text)
    assert not [x for x in f if x.check == "C2" and x.verdict == "conflict"], \
        [x.title for x in f]
    print("    不同对象的不同取值未被误报")


def test_c2_ignores_when_subject_unavailable() -> None:
    """对象抽不出时**必须放弃比较** —— 宁漏报不误报。"""
    f = _findings("R² = 0.91. R² = 0.12.")
    assert not [x for x in f if x.check == "C2" and x.verdict == "conflict"], \
        [x.title for x in f]
    print("    对象不可得时不比较（宁漏报不误报）")


def test_extract_subject_rejects_metric_itself() -> None:
    """实测误报源：括号里装的就是「指标+值」本身（如 `(R² ≈0.75)`）。"""
    facts = extract_facts([_blk("The index dominates (R² ≈0.75) and nothing else.")])
    assert facts
    subj = extract_subject(facts[0])
    assert "r" != subj.strip() and not subj.startswith("r²"), f"对象抽成了指标本身：{subj!r}"
    print(f"    指标名未被当作对象（抽到：{subj!r}）")


def test_c2_ignores_inequality_facts() -> None:
    """不等式不是确定值，不可互比。"""
    f = _findings("Prior work reported bias < 0.001 and later bias < 0.005 at other sites.")
    assert not [x for x in f if x.check == "C2" and x.verdict == "conflict"], \
        [x.title for x in f]
    print("    不等式未被当作确定值比较")


def test_scan_does_not_cross_clause() -> None:
    """实测误报源：`LST bias using DEMs matched to the 5000 m` 里的 5000 是尺度不是 bias。"""
    facts = extract_facts([_blk("Figure 5 shows the impact on the LST Bias. At the 5000 m scale, values rise.")])
    bad = [f for f in facts if f.metric == "Bias" and f.value == 5000.0]
    assert not bad, f"跨分句误抽：{[(f.metric, f.value) for f in facts]}"
    print("    连接段未跨分句（图轴刻度未被当成指标值）")


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
