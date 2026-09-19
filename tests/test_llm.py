#!/usr/bin/env python
"""Provider 抽象自检（不触发网络调用）。

    pixi run py tests/test_llm.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rsrc.llm import (  # noqa: E402
    PROVIDER_PRESETS,
    EmbeddingSpec,
    build_embedder,
)


def test_fingerprint_is_stable_and_model_bound() -> None:
    a = EmbeddingSpec(provider="dashscope", model="text-embedding-v4", dim=1024)
    b = EmbeddingSpec(provider="dashscope", model="text-embedding-v4", dim=1024)
    c = EmbeddingSpec(provider="dashscope", model="text-embedding-v4", dim=512)
    d = EmbeddingSpec(provider="siliconflow", model="BAAI/bge-m3", dim=1024)
    assert a.fingerprint() == b.fingerprint(), "同规格指纹应一致"
    assert a.fingerprint() != c.fingerprint(), "维度不同必须换指纹"
    assert a.fingerprint() != d.fingerprint(), "provider/model 不同必须换指纹"
    print(f"    dashscope/1024 → {a.fingerprint()}")


def test_presets_cover_expected_providers() -> None:
    for name in ("deepseek", "dashscope", "siliconflow", "openrouter", "local"):
        assert name in PROVIDER_PRESETS, f"缺少预设：{name}"
    print(f"    预设齐全: {'/'.join(PROVIDER_PRESETS)}")


def test_local_embedder_builds_without_key() -> None:
    spec_before = os.environ.pop("EMBED_PROVIDER", None)
    try:
        emb = build_embedder("local")
    finally:
        if spec_before is not None:
            os.environ["EMBED_PROVIDER"] = spec_before
    assert emb.spec.provider == "local"
    # 断言与预设一致，不写死数字 —— 预设调整时不会造成假失败
    assert emb.spec.dim == int(PROVIDER_PRESETS["local"]["embed_dim"]), (
        f"本地兜底维度与预设不一致：{emb.spec.dim}"
    )
    print(f"    local → {emb.spec.model} ({emb.spec.dim} 维)")


def test_missing_key_gives_actionable_error() -> None:
    """缺 key 时不能静默回退 —— 报错要指向解法。"""
    saved = {k: os.environ.pop(k, None) for k in ("DASHSCOPE_API_KEY", "OPENROUTER_API_KEY")}
    try:
        build_embedder("dashscope")
    except RuntimeError as exc:
        assert "API key" in str(exc) and "local" in str(exc), str(exc)
        print(f"    报错文案: {str(exc)[:60]}…")
    else:
        raise AssertionError("缺 key 时应报错")
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


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
    print("-" * 56)
    print(f"{len(tests) - failed}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
