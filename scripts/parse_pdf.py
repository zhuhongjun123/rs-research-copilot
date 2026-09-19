#!/usr/bin/env python
"""PDF → 结构化 JSON（Docling，跑在 `parser` 环境）。

用法（必须用 parser 环境）：
    pixi run -e parser python scripts/parse_pdf.py <pdf> <out.json>

输出结构（只保留数值自洽审查需要的东西）：

```json
{
  "source": "<pdf 路径>",
  "page_count": 9,
  "blocks": [
    {"page": 1, "type": "section_header", "text": "3 结果与分析", "bbox": [x0,y0,x1,y1]},
    {"page": 3, "type": "table", "text": "<markdown 表格>", "rows": [["区域","算法"], ...]},
    ...
  ]
}
```

`type` 取值来自 Docling 的 `label`（`text` / `section_header` / `caption` /
`table` / `list_item` / `formula` / `picture` …）。

⚠️ **`caption` 与 `table` 必须与正文区分开** —— 跨位置数值一致性检查
（C1/C2）正是要比较「摘要 / 正文 / 表格 / 图注」四处。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

MAX_TEXT = 20_000  # 单个 block 的文本上限，防异常超长


def parse(pdf: Path) -> dict:
    from docling.document_converter import DocumentConverter

    result = DocumentConverter().convert(str(pdf))
    doc = result.document

    blocks: list[dict] = []
    for item, _level in doc.iterate_items():
        label = str(getattr(item, "label", "unknown"))
        prov = getattr(item, "prov", None)
        page = getattr(prov[0], "page_no", None) if prov else None
        bbox = None
        if prov:
            box = getattr(prov[0], "bbox", None)
            if box is not None:
                bbox = [
                    round(float(getattr(box, k, 0.0)), 1)
                    for k in ("l", "t", "r", "b")
                ]

        text = ""
        rows = None
        if label == "table":
            try:
                frame = item.export_to_dataframe(doc=doc)
                rows = [[str(c) for c in row] for row in frame.values.tolist()]
                text = frame.to_markdown(index=False)
            except Exception as exc:  # noqa: BLE001 - 表格解析失败不应中断整篇
                text = f"<table export failed: {exc}>"
        else:
            text = (getattr(item, "text", "") or "")[:MAX_TEXT]

        blocks.append(
            {"page": page, "type": label, "text": text, "rows": rows, "bbox": bbox}
        )

    return {"source": str(pdf), "page_count": len(getattr(doc, "pages", {})), "blocks": blocks}


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    pdf, out = Path(sys.argv[1]), Path(sys.argv[2])
    if not pdf.is_file():
        print(f"文件不存在：{pdf}", file=sys.stderr)
        return 1

    data = parse(pdf)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    kinds: dict[str, int] = {}
    for block in data["blocks"]:
        kinds[block["type"]] = kinds.get(block["type"], 0) + 1
    with_page = sum(1 for b in data["blocks"] if b["page"] is not None)
    print(
        f"  {pdf.name[:44]:<44} 页 {data['page_count']:<3} "
        f"块 {len(data['blocks']):<4} 带页码 {with_page:<4} "
        f"表格 {kinds.get('table', 0):<3} caption {kinds.get('caption', 0)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
