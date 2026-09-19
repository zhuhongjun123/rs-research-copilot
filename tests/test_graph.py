#!/usr/bin/env python
"""审查链端到端自检（LangGraph + 注入召回 + 真实论文精确）。

这里同时验证两件事，缺一不可：
  **召回** —— 注入的已知缺陷必须被对应检查项抓到
  **精确** —— 真实论文（作者自己 4 篇）**不得**报出任何 conflict

    pixi run py tests/test_graph.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rsrc.audit.inject import inject_all  # noqa: E402
from rsrc.graph import build_audit_graph, run_audit  # noqa: E402

PARSED = ROOT / "data" / "index" / "parsed"


def _write_tmp(blocks: list[dict], name: str) -> Path:
    tmp = ROOT / "data" / "index" / f"_test_{name}.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(
        json.dumps({"source": name, "page_count": 1, "blocks": blocks}, ensure_ascii=False),
        encoding="utf-8",
    )
    return tmp


def test_graph_compiles() -> None:
    app = build_audit_graph()
    assert app is not None
    print("    审查图编译通过")


def test_clean_paper_no_conflict() -> None:
    """精确性：真实论文不得报出 conflict。"""
    files = sorted(PARSED.glob("*.json")) if PARSED.is_dir() else []
    files = [f for f in files if not f.name.startswith("_")]
    if not files:
        print("    跳过（无解析产物）")
        return
    checked = 0
    for path in files:
        state = run_audit(path, use_llm=False)
        conflicts = [f for f in state["findings"] if f.verdict == "conflict"]
        assert not conflicts, f"{path.name} 误报：{[f.title for f in conflicts]}"
        checked += 1
    print(f"    {checked} 篇真实论文均无 conflict（误报率 0）")


def test_injected_defects_are_caught() -> None:
    """召回：注入的每个缺陷都应被其期望的检查项抓到。"""
    files = sorted(PARSED.glob("*.json"))
    files = [f for f in files if not f.name.startswith("_")]
    if not files:
        print("    跳过（无解析产物）")
        return

    total_injected = total_caught = 0
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        blocks, injections = inject_all(data["blocks"])
        if not injections:
            continue
        tmp = _write_tmp(blocks, f"inj_{path.stem}")
        state = run_audit(tmp, use_llm=False)
        found = {f.check for f in state["findings"] if f.verdict == "conflict"}

        for inj in injections:
            total_injected += 1
            if inj.expected_check in found:
                total_caught += 1
            else:
                print(f"      ✗ {path.stem}: 注入的 {inj.expected_check} 未被抓到（{inj.description}）")
        tmp.unlink(missing_ok=True)

    assert total_injected > 0, "没有任何注入生效 —— 注入器需要检查"
    assert total_caught == total_injected, f"检出 {total_caught}/{total_injected}"
    print(f"    注入 {total_injected} 处缺陷，全部被对应检查项抓到")


def test_llm_absent_degrades_gracefully() -> None:
    """无 LLM 时必须降级为纯确定性报告，而不是报错。"""
    from rsrc.llm import build_chat

    state = run_audit(_write_tmp([{"page": 1, "type": "text", "text": "R² = 1.5."}], "deg"), use_llm=True)
    assert "report" in state and state["report"], "未产出报告"
    if build_chat() is None:
        assert state.get("narrative", "") == "", "无 LLM 时不应有摘要"
        print("    未配置 LLM 时正常降级（无摘要，报告照出）")
    else:
        print("    已配置 LLM，摘要路径已走通")


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
