#!/usr/bin/env python
"""端到端跑数值自洽审查，输出 Markdown 报告。

用法：
    # 审查已解析的论文（先跑 parse_pdf.py / build_index.py 生成 *.json）
    pixi run py scripts/run_audit.py --parsed data/index/parsed/C7KQG54K.json

    # 按 Zotero item key 审查（会自动补标题 / DOI）
    pixi run py scripts/run_audit.py --key C7KQG54K

    # 注入已知缺陷后审查 —— 验证「能不能抓到」（Day 5 评测的基础）
    pixi run py scripts/run_audit.py --key C7KQG54K --inject

    # 不用 LLM（纯确定性报告）
    pixi run py scripts/run_audit.py --key C7KQG54K --no-llm

    # 报告写文件
    pixi run py scripts/run_audit.py --key C7KQG54K -o report.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rsrc.audit.inject import inject_all  # noqa: E402
from rsrc.graph import run_audit  # noqa: E402

INDEX_DIR = ROOT / "data" / "index" / "parsed"


def _meta_from_zotero(key: str) -> dict:
    """取 Zotero 条目的标题 / DOI（失败不阻断审查）。"""
    try:
        from rsrc.zotero import ZoteroLibrary

        lib = ZoteroLibrary()
        item = lib._get(f"/items/{key}")
        data = item.get("data", {})
        return {"title": data.get("title", ""), "doi": data.get("DOI", "")}
    except Exception:  # noqa: BLE001 - 审查不依赖元数据
        return {}


def main() -> int:
    ap = argparse.ArgumentParser(description="数值自洽审查")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--parsed", help="Docling 解析产物路径（*.json）")
    src.add_argument("--key", help="Zotero item key（从 data/index/parsed/<key>.json 读）")
    ap.add_argument("--inject", action="store_true", help="注入已知缺陷后再审查（验证召回）")
    ap.add_argument("--no-llm", action="store_true", help="不调用 LLM（纯确定性报告）")
    ap.add_argument("-o", "--output", help="报告输出路径（默认 stdout）")
    args = ap.parse_args()

    if args.key:
        parsed_path = INDEX_DIR / f"{args.key}.json"
        if not parsed_path.is_file():
            print(f"未找到解析产物：{parsed_path}", file=sys.stderr)
            print("先跑：pixi run -e parser python scripts/parse_pdf.py <pdf> <out.json>", file=sys.stderr)
            return 2
        key = args.key
    else:
        parsed_path = Path(args.parsed)
        if not parsed_path.is_file():
            print(f"文件不存在：{parsed_path}", file=sys.stderr)
            return 2
        key = parsed_path.stem

    meta = _meta_from_zotero(key) if key else {}

    # 注入：改写解析产物后写入临时文件，再交给审查链
    if args.inject:
        data = json.loads(parsed_path.read_text(encoding="utf-8"))
        blocks, injections = inject_all(data["blocks"])
        data["blocks"] = blocks
        tmp = ROOT / "data" / "index" / f"_injected_{key}.json"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        parsed_path = tmp
        print("=== 注入的已知缺陷（ground truth）===")
        for n, inj in enumerate(injections, 1):
            print(f"  {n}. [{inj.kind}] p{inj.page} 期望被 {inj.expected_check} 抓到")
            print(f"       {inj.description}")
            print(f"       原文 → `{inj.before}`")
            print(f"       改后 → `{inj.after}`")
        print()

    state = run_audit(
        parsed_path,
        title=meta.get("title", ""),
        item_key=key,
        doi=meta.get("doi", ""),
        # 默认用 LLM；跑对照实验时用 --no-llm 保持纯净
        use_llm=not args.no_llm,
    )

    report = state.get("report", "")
    if args.output:
        Path(args.output).write_text(report, encoding="utf-8")
        print(f"报告已写入：{args.output}")
    else:
        print(report)

    counts = {"high": 0, "medium": 0, "info": 0}
    for f in state.get("findings", []):
        counts[f.severity] = counts.get(f.severity, 0) + 1
    print(
        f"\n[摘要] 事实 {len(state.get('facts', []))} 条 | "
        f"finding {len(state.get('findings', []))} 条 "
        f"(高 {counts['high']} / 中 {counts['medium']} / 提示 {counts['info']})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
