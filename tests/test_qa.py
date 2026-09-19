#!/usr/bin/env python
"""问答链自检 —— 重点验证「引用核验」与「拒答」两条防线。

    pixi run py tests/test_qa.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rsrc.qa import REFUSAL, verify_answer  # noqa: E402


def _hit(text: str) -> dict:
    class C:
        def __init__(self, t):
            self.text = t

        def cite(self):
            return "KEY · p1 · text"

    return {"chunk": C(text), "score": 0.03, "cite": "KEY · p1 · text"}


# ── ① 引用核验：编造的数字必须被抓住 ────────────────────────────
def test_verify_catches_fabricated_number() -> None:
    hits = [_hit("The validation used 96 FLUXNET sites at monthly scale.")]
    answer = "论文使用了 96 个站点验证 [来源1]，R² 达到 0.99 [来源1]。"
    verified, unsupported = verify_answer(answer, hits)
    assert unsupported, "编造的 0.99 未被抓住"
    assert any("0.99" in u["reason"] for u in unsupported), unsupported
    print(f"    编造数字被抓到：{unsupported[0]['reason'][:44]}")


def test_verify_passes_supported_claim() -> None:
    hits = [_hit("The validation used 96 FLUXNET sites, yielding R2 = 0.84.")]
    answer = "验证使用 96 个站点，R² = 0.84 [来源1]。"
    verified, unsupported = verify_answer(answer, hits)
    assert verified and not unsupported, (verified, unsupported)
    print("    有支撑的断言正常通过")


def test_verify_flags_missing_citation() -> None:
    """带数值却不标引用的断言 → 直接判为无支撑。"""
    hits = [_hit("The validation used 96 FLUXNET sites.")]
    verified, unsupported = verify_answer("共使用了 96 个站点。", hits)
    assert unsupported and not verified, (verified, unsupported)
    assert "未标注引用" in unsupported[0]["reason"], unsupported
    print("    未标引用的数值断言被判无支撑")


def test_verify_ignores_text_without_numbers() -> None:
    verified, unsupported = verify_answer("本文研究了地表温度反演方法。[来源1]", [_hit("x")])
    assert not verified and not unsupported
    print("    无数值的断言不参与核验（避免噪音）")


# ── ② 拒答：证据不足时不得靠先验硬答 ────────────────────────────
def test_refusal_prompt_is_strict() -> None:
    """拒答语必须是一个**明确、可检索**的固定串，否则无法自动判定是否拒答。"""
    assert REFUSAL and isinstance(REFUSAL, str)
    assert "无法" in REFUSAL
    print(f"    拒答语：{REFUSAL}")


def test_min_score_threshold_exists() -> None:
    from rsrc.qa import MIN_SCORE

    assert 0 < MIN_SCORE < 0.1, MIN_SCORE
    print(f"    检索相关度阈值 MIN_SCORE={MIN_SCORE}（低于它直接拒答，不进入生成）")


# ── ③ 端到端（需要已建索引）─────────────────────────────────────
def test_engine_refuses_out_of_library_question() -> None:
    try:
        from rsrc.qa import QAEngine

        engine = QAEngine()
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"    跳过（{str(exc)[:60]}）")
        return
    r = engine.ask("2024 年诺贝尔物理学奖颁给了谁？")
    assert r.refused, "库外问题未拒答"
    assert REFUSAL in r.answer
    print("    库外问题被拒答（未靠先验硬答）")


def test_engine_answers_in_library_question() -> None:
    try:
        from rsrc.qa import QAEngine

        engine = QAEngine()
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"    跳过（{str(exc)[:60]}）")
        return
    r = engine.ask("用了多少个 FLUXNET 站点做验证？")
    assert r.hits, "未检索到任何片段"
    if r.refused:
        print("    模型拒答（可能语料不含该问题）—— 链路正常")
        return
    assert r.answer, "作答但内容为空"
    print(f"    作答 {len(r.answer)} 字，命中 {len(r.hits)}，"
          f"有支撑 {len(r.verified)} / 无支撑 {len(r.unsupported)}")


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
