#!/usr/bin/env python
"""跑全部自检。

    pixi run test

跑不过就退出码非 0（可以直接挂到 CI 上）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ("test_extract", "test_checks", "test_graph", "test_zotero", "test_llm", "test_api")


def main() -> int:
    results: list[tuple[str, int]] = []
    for name in TESTS:
        path = ROOT / "tests" / f"{name}.py"
        if not path.is_file():
            continue
        proc = subprocess.run(
            [sys.executable, str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="ignore",
        )
        tail = [ln for ln in (proc.stdout or "").strip().splitlines() if "通过" in ln or "✗" in ln]
        ok = proc.returncode == 0
        results.append((name, proc.returncode))
        print(f"  {'✓' if ok else '✗'} {name:<14} {tail[-1] if tail else '(无输出)'}")

    failed = [n for n, rc in results if rc != 0]
    print("-" * 58)
    print(f"{len(results) - len(failed)}/{len(results)} 个测试文件通过"
          + (f" | 失败：{', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
