"""Zotero 本地库适配层。

主路径走 **本地 HTTP API**（需要 Zotero 在运行，且已开启
「设置 → 高级 → 允许其它应用程序与 Zotero 通信」）。

未开启 / 未运行时会抛 `LocalApiUnavailable`，由上层降级到
`sqlite_ro`（只读副本）。

⚠️ 官方立场（PLAN §4）：SQLite 库是**内部数据库**，结构可能随版本变化；
只读可以，**绝不能写**。所以 sqlite 只作降级，且不依赖具体 schema 细节。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from rsrc.config import LINK_MODE_FILE, PDF_CONTENT_TYPE, local_api_base


class LocalApiUnavailable(RuntimeError):
    """本地 API 不可用（Zotero 未运行 / 未开启通信开关 / 返回 403）。"""


@dataclass
class Attachment:
    """一个 PDF 附件。"""

    key: str
    parent_key: str | None
    link_mode: int
    path: Path | None  # 真实文件路径；取不到或不存在时为 None
    filename: str = ""


@dataclass
class Document:
    """一条文献条目（含其 PDF 附件）。"""

    key: str
    item_type: str
    title: str = ""
    creators: list[str] = field(default_factory=list)
    year: str = ""
    publication: str = ""
    doi: str = ""
    abstract: str = ""
    attachments: list[Attachment] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    def pdf(self) -> Attachment | None:
        """首个存在的 PDF 附件。"""
        for att in self.attachments:
            if att.path and att.path.is_file():
                return att
        return None


class ZoteroLibrary:
    """只读的 Zotero 库访问器（本地 API 为主）。"""

    def __init__(self, base: str | None = None, timeout: int = 20) -> None:
        self.base = base or local_api_base()
        self.timeout = timeout
        self.api_version: str | None = None
        self.schema_version: str | None = None
        self.server_id: str | None = None

    # ── 底层请求 ────────────────────────────────────────────────
    def _get(self, path: str, *, raw: bool = False):
        url = f"{self.base}{path}"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
                if raw:
                    return body.strip()
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                raise LocalApiUnavailable(
                    "本地 API 返回 403 —— 请在 Zotero 中勾选"
                    "「设置 → 高级 → 允许其它应用程序与 Zotero 通信」"
                ) from exc
            raise LocalApiUnavailable(f"本地 API HTTP {exc.code}: {path}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise LocalApiUnavailable(
                f"连不上 Zotero 本地 API（{self.base}）。请确认 Zotero 正在运行。"
            ) from exc

    # ── 版本协商（官方建议：不要硬编码 schema）──────────────────
    def probe(self) -> dict[str, str | None]:
        """读取 API / Schema 版本与 Server-ID。"""
        url = self.base.rsplit("/users/", 1)[0] + "/"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                self.api_version = resp.headers.get("Zotero-API-Version")
                self.schema_version = resp.headers.get("Zotero-Schema-Version")
                self.server_id = resp.headers.get("Zotero-Server-ID")
        except Exception as exc:  # noqa: BLE001 - 探测失败不应阻断
            raise LocalApiUnavailable(f"版本探测失败：{exc}") from exc
        return {
            "api_version": self.api_version,
            "schema_version": self.schema_version,
            "server_id": self.server_id,
        }

    # ── 条目 ────────────────────────────────────────────────────
    def top_items(
        self,
        item_type: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """顶层条目。本地 API **默认不分页**（一次返回全部）。"""
        query = []
        if item_type:
            query.append(f"itemType={urllib.parse.quote(item_type)}")
        if limit:
            query.append(f"limit={int(limit)}")
        suffix = ("?" + "&".join(query)) if query else ""
        return self._get(f"/items/top{suffix}")

    def children(self, item_key: str) -> list[dict]:
        return self._get(f"/items/{item_key}/children")

    def attachment_path(self, attachment_key: str) -> Path | None:
        """拿附件的真实本地路径。

        走 `/items/<key>/file/view/url`，返回纯文本 `file://` URL。
        ⚠️ 路径是 **URL 编码** 的，必须解码后再落到 Path。
        """
        try:
            url = self._get(f"/items/{attachment_key}/file/view/url", raw=True)
        except LocalApiUnavailable:
            return None
        if not url.startswith("file:"):
            return None
        parsed = urllib.parse.urlparse(url)
        # file:///F:/...  →  /F:/...  → 去掉前导斜杠
        raw_path = urllib.parse.unquote(parsed.path)
        if len(raw_path) > 2 and raw_path[0] == "/" and raw_path[2] == ":":
            raw_path = raw_path[1:]
        return Path(raw_path)

    def fulltext(self, attachment_key: str) -> str:
        """Zotero 已索引的全文。

        ⚠️ 返回的是 **JSON**（`{"content": "..."}`），不是纯文本。
        这是「档 0」快速通道 —— 省去自己解析 PDF，但**无页码无版式**。
        """
        body = self._get(f"/items/{attachment_key}/fulltext", raw=True)
        if not body:
            return ""
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return body
        if isinstance(data, dict):
            return data.get("content", "") or ""
        return body

    # ── 组合 ────────────────────────────────────────────────────
    def document(self, item: dict) -> Document:
        """把一个条目 JSON 组装成 Document（含 PDF 附件）。"""
        data = item.get("data", {}) if "data" in item else item
        creators = []
        for creator in data.get("creators", []) or []:
            name = creator.get("name") or " ".join(
                p for p in (creator.get("firstName"), creator.get("lastName")) if p
            )
            if name:
                creators.append(name)

        doc = Document(
            key=item.get("key", ""),
            item_type=data.get("itemType", ""),
            title=data.get("title", "") or "",
            creators=creators,
            year=(data.get("date", "") or "")[:4],
            publication=data.get("publicationTitle", "") or data.get("proceedingsTitle", "") or "",
            doi=data.get("DOI", "") or "",
            abstract=data.get("abstractNote", "") or "",
            raw=data,
        )

        for child in self.children(doc.key):
            cdata = child.get("data", {})
            if cdata.get("itemType") != "attachment":
                continue
            if cdata.get("contentType") != PDF_CONTENT_TYPE:
                continue
            # API 给字符串、SQLite 给整数，两套兼容
            link_mode = cdata.get("linkMode")
            if link_mode not in LINK_MODE_FILE:
                continue
            link_mode_name = (
                link_mode
                if isinstance(link_mode, str)
                else {0: "imported_file", 1: "imported_url", 2: "linked_file"}.get(link_mode, "")
            )
            att_key = child.get("key", "")
            doc.attachments.append(
                Attachment(
                    key=att_key,
                    parent_key=doc.key,
                    link_mode=link_mode_name,
                    path=self.attachment_path(att_key),
                    filename=cdata.get("filename", "") or "",
                )
            )
        return doc

    def iter_documents(
        self,
        item_type: str | None = None,
        limit: int | None = None,
        with_pdf_only: bool = False,
    ):
        """迭代文献条目。"""
        for item in self.top_items(item_type=item_type, limit=limit):
            doc = self.document(item)
            if with_pdf_only and doc.pdf() is None:
                continue
            yield doc
