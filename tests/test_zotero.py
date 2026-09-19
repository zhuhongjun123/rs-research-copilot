#!/usr/bin/env python
"""Zotero 适配层自检。

需要 Zotero 正在运行且已开启本地通信；否则整体跳过（不算失败）。

    pixi run py tests/test_zotero.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rsrc.config import LINK_MODE_FILE, zotero_data_dir  # noqa: E402
from rsrc.zotero import LocalApiUnavailable, ZoteroLibrary  # noqa: E402


def test_data_dir_not_hardcoded() -> None:
    """数据目录必须从 prefs.js 发现，不能是默认路径。"""
    d = zotero_data_dir()
    assert isinstance(d, Path)
    print(f"    发现数据目录: {d}")
    # 若目录存在，应当有 zotero.sqlite
    if d.is_dir():
        assert (d / "zotero.sqlite").is_file(), f"{d} 下没有 zotero.sqlite"


def _library_or_skip() -> ZoteroLibrary | None:
    lib = ZoteroLibrary()
    try:
        lib.probe()
    except LocalApiUnavailable as exc:
        print(f"    跳过（{exc}）")
        return None
    return lib


def test_probe_versions() -> None:
    lib = _library_or_skip()
    if lib is None:
        return
    assert lib.api_version, "未取到 Zotero-API-Version"
    print(f"    API v{lib.api_version} / Schema v{lib.schema_version} / Server {lib.server_id}")


def test_item_to_pdf_path() -> None:
    """核心链路：条目 → 子附件 → PDF 真实路径，且文件必须存在。"""
    lib = _library_or_skip()
    if lib is None:
        return

    checked = 0
    for doc in lib.iter_documents(item_type="journalArticle", limit=5):
        for att in doc.attachments:
            assert att.link_mode in LINK_MODE_FILE, f"linkMode 未过滤: {att.link_mode}"
        pdf = doc.pdf()
        if pdf is None:
            continue
        assert pdf.path is not None and pdf.path.is_file(), f"PDF 路径不存在: {pdf.path}"
        checked += 1
        if checked == 1:
            print(f"    {doc.key} → {pdf.path.name[:52]}")
    assert checked > 0, "5 篇期刊论文里没找到任何可用 PDF"
    print(f"    {checked} 个 PDF 路径均存在 ✓")


def test_fulltext_is_json_wrapped() -> None:
    """Zotero /fulltext 返回 JSON（{content: ...}），代码必须解包。"""
    lib = _library_or_skip()
    if lib is None:
        return
    for doc in lib.iter_documents(item_type="journalArticle", limit=3):
        pdf = doc.pdf()
        if pdf is None:
            continue
        text = lib.fulltext(pdf.key)
        assert len(text) > 500, f"全文过短（{len(text)}），可能没解包 JSON"
        assert not text.lstrip().startswith("{"), "全文未解包，仍是 JSON 字符串"
        print(f"    全文 {len(text)} 字符 ✓")
        return
    print("    跳过（未取到带 PDF 的条目）")


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
