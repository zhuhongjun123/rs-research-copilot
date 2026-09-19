#!/usr/bin/env python
"""构建知识库索引（向量 + 词法）。

    pixi run py scripts/build_index.py            # 用 data/index/parsed 下全部论文
    pixi run py scripts/build_index.py --check    # 只核对指纹，不重建

产出（均在 data/index/，已 gitignore）：
    vectors.faiss / chunks.jsonl / manifest.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rsrc.config import INDEX_DIR  # noqa: E402
from rsrc.llm import build_embedder  # noqa: E402
from rsrc.retrieval import HybridIndex, chunk_blocks  # noqa: E402

PARSED = INDEX_DIR / "parsed"


def load_chunks() -> list:
    files = sorted(p for p in PARSED.glob("*.json") if not p.name.startswith("_"))
    chunks = []
    for p in files:
        data = json.loads(p.read_text(encoding="utf-8"))
        got = chunk_blocks(data.get("blocks", []), p.stem)
        chunks.extend(got)
        print(f"    {p.stem:<10} {len(got):>4} chunk")
    return chunks


def main() -> int:
    ap = argparse.ArgumentParser(description="构建知识库索引")
    ap.add_argument("--check", action="store_true", help="只核对指纹，不重建")
    ap.add_argument("--batch", type=int, default=32, help="嵌入批大小")
    args = ap.parse_args()

    if not PARSED.is_dir() or not list(PARSED.glob("*.json")):
        print(f"未找到解析产物：{PARSED}", file=sys.stderr)
        print("先跑：pixi run -e parser python scripts/parse_pdf.py <pdf> <out.json>", file=sys.stderr)
        return 2

    try:
        embedder = build_embedder()
    except Exception as exc:  # noqa: BLE001
        print(f"无法构建 embedder：{exc}", file=sys.stderr)
        return 2

    if args.check:
        man = INDEX_DIR / "manifest.json"
        if not man.is_file():
            print("尚无索引")
            return 1
        cur = json.loads(man.read_text(encoding="utf-8"))["embedding"]
        ok = cur["fingerprint"] == embedder.spec.fingerprint()
        print(f"  索引模型: {cur['provider']}/{cur['model']} ({cur['dim']} 维) {cur['fingerprint']}")
        print(f"  当前模型: {embedder.spec.provider}/{embedder.spec.model} "
              f"({embedder.spec.dim} 维) {embedder.spec.fingerprint()}")
        print("  结论:", "一致，可复用" if ok else "不一致 —— 必须全量重建")
        return 0 if ok else 1

    print("=== 分块 ===")
    chunks = load_chunks()
    total_chars = sum(len(c.text) for c in chunks)
    print(f"  合计 {len(chunks)} chunk / {total_chars:,} 字符")

    print("=== 嵌入（调用 embedding API）===")
    index = HybridIndex.build(chunks, embedder, batch=args.batch)
    index.save(INDEX_DIR)
    print(f"  已保存到 {INDEX_DIR}")
    print(f"  指纹 {index.fingerprint()} / {len(chunks)} chunk")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
