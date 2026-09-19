#!/usr/bin/env python
"""检索层自检（不调 embedding API 的部分用假向量）。

    pixi run py tests/test_retrieval.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rsrc.retrieval import (  # noqa: E402
    MAX_CHARS, Chunk, HybridIndex, chunk_blocks, tokenize,
)


def _blocks():
    return [
        {"page": 1, "type": "section_header", "text": "3 Results"},
        {"page": 1, "type": "text", "text": "The validation used 96 FLUXNET sites."},
        {"page": 2, "type": "table", "text": "| site | R2 |\n| A | 0.84 |"},
        {"page": 3, "type": "picture", "text": "figure pixels"},  # 应被跳过
        {"page": 3, "type": "caption", "text": "Figure 2. Scatter plot of R2."},
    ]


def test_chunk_keeps_position_metadata() -> None:
    """chunk 必须带页码与 block 类型 —— 否则无法给引用。"""
    chunks = chunk_blocks(_blocks(), "KEY1")
    assert chunks, "未产出任何 chunk"
    all_pages, all_types = set(), set()
    for c in chunks:
        all_pages |= set(c.pages)
        all_types |= set(c.block_types)
    assert {1, 2, 3} <= all_pages, all_pages
    assert "caption" in all_types and "table" in all_types
    assert all(c.key == "KEY1" for c in chunks)
    print(f"    {len(chunks)} chunk，页码 {sorted(all_pages)}，类型 {sorted(all_types)}")


def test_chunk_skips_picture() -> None:
    chunks = chunk_blocks(_blocks(), "KEY1")
    assert not any("figure pixels" in c.text for c in chunks), "picture 块未被跳过"
    print("    picture 块被跳过（图内文字不可靠）")


def test_chunk_size_bounded() -> None:
    long_blocks = [{"page": i, "type": "text", "text": "x" * 400} for i in range(1, 20)]
    chunks = chunk_blocks(long_blocks, "KEY2")
    assert all(len(c.text) <= MAX_CHARS + 400 for c in chunks), "chunk 超长"
    assert len(chunks) > 1, "长文本未切分"
    print(f"    长文本切成 {len(chunks)} 块，最大 {max(len(c.text) for c in chunks)} 字符")


def test_tokenize_cjk_bigrams() -> None:
    """CJK 用字符二元组；英文按词 —— 不引 jieba 依赖。"""
    toks = tokenize("地表温度 LST retrieval")
    assert "地表" in toks and "表温" in toks and "温度" in toks, toks
    assert "lst" in toks and "retrieval" in toks, toks
    print(f"    中英混排切分：{toks[:6]}…")


def test_chunk_cite_format() -> None:
    c = Chunk(chunk_id="x", key="C7KQG54K", text="t", pages=[7, 8], block_types=["text", "caption"])
    assert "C7KQG54K" in c.cite() and "p7" in c.cite() and "caption" in c.cite()
    print(f"    引用标签：{c.cite()}")


def test_index_roundtrip_without_api() -> None:
    """用假向量验证 索引构建 → 保存 → 加载 → 检索 的链路（不调 API）。"""
    import numpy as np

    class FakeSpec:
        provider, model, dim = "fake", "fake-embed", 8

        def fingerprint(self):
            return "deadbeef"

    class FakeEmbedder:
        spec = FakeSpec()

        def embed(self, texts, is_query=False):
            # 确定性假向量：按文本哈希铺开
            out = []
            for t in texts:
                h = abs(hash(t)) % 1000
                out.append([((h >> i) & 1) + 0.1 for i in range(8)])
            return out

    chunks = chunk_blocks(_blocks(), "KEY1")
    # 手工铺向量，绕过 api
    vecs = np.asarray(FakeEmbedder().embed([c.text for c in chunks]), dtype="float32")
    n = np.linalg.norm(vecs, axis=1, keepdims=True)
    vecs = vecs / np.where(n == 0, 1, n)
    idx = HybridIndex(chunks, vecs, FakeSpec())

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        idx.save(d)
        assert (d / "vectors.npy").is_file(), "向量未落盘为 .npy（避开 FAISS 的中文路径问题）"
        assert (d / "chunks.jsonl").is_file() and (d / "manifest.json").is_file()
        loaded, man = HybridIndex.load(d)
        assert len(loaded.chunks) == len(chunks)
        assert man["chunks"] == len(chunks)
        print(f"    保存/加载往返一致（{len(chunks)} chunk，manifest 记录 {man['chunks']}）")


def test_rrf_fusion_and_dedupe() -> None:
    """RRF 融合两路排名；重复文本必须被去重守卫挡掉。"""
    import numpy as np

    class Spec:
        provider, model, dim = "fake", "f", 4

        def fingerprint(self):
            return "x"

    chunks = [
        Chunk(chunk_id="a", key="K", text="alpha beta gamma", pages=[1], block_types=["text"]),
        Chunk(chunk_id="b", key="K", text="delta epsilon", pages=[2], block_types=["text"]),
        Chunk(chunk_id="c", key="K", text="alpha beta gamma", pages=[3], block_types=["text"]),  # 重复
    ]
    vecs = np.asarray([[1, 0, 0, 0], [0, 1, 0, 0], [1, 0, 0, 0]], dtype="float32")
    idx = HybridIndex(chunks, vecs, Spec())

    class E:
        spec = Spec()

        def embed(self, texts, is_query=False):
            return [[1.0, 0, 0, 0] for _ in texts]

    res = idx.search("alpha", E(), top_k=10)
    texts = [h["chunk"].text for h in res]
    assert texts.count("alpha beta gamma") == 1, f"去重守卫失效：{texts}"
    assert all("score" in h and "cite" in h for h in res)
    print(f"    融合命中 {len(res)} 条，重复文本被去重（A/B 两路）")


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
