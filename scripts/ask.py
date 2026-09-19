#!/usr/bin/env python
"""知识库问答（可溯源 + 引用核验 + 拒答）。

用法：
    pixi run py scripts/ask.py "12 个通量站验证的 R² 是多少？"
    pixi run py scripts/ask.py "这篇论文用了什么数据？" --key C7KQG54K
    pixi run py scripts/ask.py "..." --no-llm     # 只看检索结果
    pixi run py scripts/ask.py "..." -o answer.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rsrc.qa import QAEngine, render_answer  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="知识库问答")
    ap.add_argument("question", help="问题")
    ap.add_argument("--top-k", type=int, default=10, help="检索条数")
    ap.add_argument("--key", help="只在该篇文献内检索（Zotero itemKey）")
    ap.add_argument("-o", "--output", help="输出路径（默认 stdout）")
    args = ap.parse_args()

    try:
        engine = QAEngine()
    except FileNotFoundError as exc:
        print(f"未找到索引：{exc}\n先跑：pixi run py scripts/build_index.py", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    result = engine.ask(args.question, top_k=args.top_k)

    if args.key:
        result.hits = [h for h in result.hits if h["chunk"].key == args.key]
        result.trace.append(f"filters: 限定文献 {args.key}，剩余 {len(result.hits)} 命中")

    text = render_answer(result)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"已写入：{args.output}")
    else:
        print(text)

    print(
        f"\n[摘要] {'拒答' if result.refused else '已作答'} | "
        f"命中 {len(result.hits)} | 有支撑 {len(result.verified)} / "
        f"无支撑 {len(result.unsupported)}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
