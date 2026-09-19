#!/usr/bin/env python
"""API 自检（用 FastAPI TestClient，不需要真起服务）。

覆盖三件必须成立的事：
  1. 依赖状态可诊断（/health）
  2. 缺解析产物时返回 404 + **可执行的修复提示**，不是 500 堆栈
  3. 审查端点能跑通，且**注入缺陷时能找到**（演示路径）

    pixi run py tests/test_api.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from rsrc.api import PARSED_DIR, app  # noqa: E402

client = TestClient(app)


def _any_key() -> str | None:
    if not PARSED_DIR.is_dir():
        return None
    files = sorted(p.stem for p in PARSED_DIR.glob("*.json") if not p.name.startswith("_"))
    return files[0] if files else None


def test_root() -> None:
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "rs-research-copilot"
    print(f"    {body['name']} v{body['version']}")


def test_health_diagnoses_dependencies() -> None:
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    for key in ("zotero", "llm", "embedding"):
        assert key in body, f"/health 未报告 {key}"
    print(f"    zotero={body['zotero']['available']} llm={body['llm']['available']} "
          f"解析产物={body['parsed_papers']} 篇")


def test_missing_paper_gives_actionable_404() -> None:
    """缺产物必须给修复指引 —— 而不是 500。"""
    r = client.post("/audit", json={"key": "__not_exist__"})
    assert r.status_code == 404, r.status_code
    detail = r.json()["detail"]
    assert "parse_pdf.py" in detail, f"404 里没有修复指引：{detail[:120]}"
    print("    缺产物返回 404 + 修复指引")


def test_papers_list() -> None:
    r = client.get("/papers")
    assert r.status_code == 200
    papers = r.json()
    print(f"    列出 {len(papers)} 篇")
    assert isinstance(papers, list)


def test_facts_and_audit() -> None:
    key = _any_key()
    if key is None:
        print("    跳过（无解析产物）")
        return

    r = client.get(f"/papers/{key}/facts")
    assert r.status_code == 200
    facts = r.json()
    assert facts, "未抽到任何事实"
    print(f"    {key} 抽到 {len(facts)} 条事实")

    r = client.post("/audit", json={"key": key, "use_llm": False})
    assert r.status_code == 200
    body = r.json()
    assert "report" in body and body["report"]
    assert body["counts"]["high"] == 0, "干净论文不应有高严重度 finding"
    print(f"    审查通过：高 {body['counts']['high']} / 中 {body['counts']['medium']}")


def test_inject_demo_catches_defects() -> None:
    """演示端点：注入已知缺陷后必须能找到。"""
    key = None
    for p in sorted(PARSED_DIR.glob("*.json")) if PARSED_DIR.is_dir() else []:
        if p.name.startswith("_"):
            continue
        key = p.stem
        r = client.post("/audit", json={"key": key, "inject": True, "use_llm": False})
        if r.status_code == 200 and r.json().get("injections"):
            body = r.json()
            conflicts = [f for f in body["findings"] if f["verdict"] == "conflict"]
            assert conflicts, f"{key} 注入了 {len(body['injections'])} 处却未报出任何 conflict"
            print(f"    {key}：注入 {len(body['injections'])} 处 → 报出 {len(conflicts)} 条 conflict")
            return
    print("    跳过（没有可注入的论文）")


def test_ask_endpoint() -> None:
    """问答端点：要么作答并核验，要么**明确拒答**（不能两者都不是）。"""
    r = client.post("/ask", json={"question": "用了多少个 FLUXNET 站点做验证？", "top_k": 5})
    if r.status_code == 404:
        print("    跳过（索引未构建）")
        return
    assert r.status_code == 200, r.text[:160]
    body = r.json()
    assert body["answer"], "既未作答也未拒答"
    assert body["refused"] or body["hits"], "作答但没有任何检索命中"
    print(f"    {'拒答' if body['refused'] else '作答'} | 命中 {len(body['hits'])} | "
          f"有支撑 {len(body['verified'])} / 无支撑 {len(body['unsupported'])}")


def test_ask_refuses_out_of_library() -> None:
    r = client.post("/ask", json={"question": "2024 年诺贝尔物理学奖颁给了谁？"})
    if r.status_code == 404:
        print("    跳过（索引未构建）")
        return
    assert r.status_code == 200, r.text[:160]
    body = r.json()
    assert body["refused"], "库外问题未拒答"
    print("    库外问题经 API 也被拒答")


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
