"""知识库检索层：分块 → 向量 + 词法 → RRF 融合 → 带溯源返回。

## 设计要点（每条都对应一次实测教训）

1. **chunk 必须保留位置信息**（itemKey / 页码 / block 类型）——
   否则问答链无法给出「哪一页哪一段」的引用，也就回到了「编数字」的老问题。
2. **混合检索而非纯向量**：科研论文里大量专有名词与缩写（LSE / TRI / GLASS
   AVHRR），纯向量对这类精确词召回不稳；BM25 兜住。两路用 **RRF** 融合。
3. **RRF 而非加权求和**：两路分数量纲不同，归一化权重需要调参且易过拟合；RRF 只用排名。
4. **CJK 用字符二元组做词法切分**：不引入 jieba 依赖，对中文 BM25 足够。
5. **embedding 必须记录指纹**：向量与模型绑定，换模型必须全量重建
   （`EmbeddingSpec.fingerprint()`）。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

# ── 分块参数 ────────────────────────────────────────────────────
TARGET_CHARS = 1200  # 约 500 token（中英混排）；bge-m3 支持 8192，留足余量
MAX_CHARS = 1800
RRF_K = 60  # RRF 平滑常数，无需调参
DEFAULT_TOP_K = 10

# 不作为可检索内容的块类型（图内文字拿不到；公式单独处理）
SKIP_TYPES = {"picture"}


@dataclass
class Chunk:
    """一个可检索片段。**带完整位置信息**，供引用溯源。"""

    chunk_id: str
    key: str  # Zotero itemKey 或文件名
    text: str
    pages: list[int] = field(default_factory=list)
    block_types: list[str] = field(default_factory=list)

    @property
    def page(self) -> int | None:
        return self.pages[0] if self.pages else None

    def cite(self) -> str:
        """人类可读的引用标签。"""
        pages = "、".join(f"p{p}" for p in self.pages[:3])
        types = "/".join(sorted(set(self.block_types)))
        return f"{self.key} · {pages} · {types}"

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id, "key": self.key, "text": self.text,
            "pages": self.pages, "block_types": self.block_types,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Chunk":
        return cls(**d)


# ── 分块 ────────────────────────────────────────────────────────
def chunk_blocks(blocks: list[dict], key: str) -> list[Chunk]:
    """把解析产物按段落合并成 chunk，保留页码与 block 类型。"""
    chunks: list[Chunk] = []
    buf: list[str] = []
    pages: list[int] = []
    types: list[str] = []
    size = 0

    def flush() -> None:
        nonlocal buf, pages, types, size
        if not buf:
            return
        text = "\n".join(buf).strip()
        if text:
            cid = hashlib.sha1(f"{key}|{pages[0] if pages else 0}|{text[:80]}".encode()).hexdigest()[:12]
            chunks.append(Chunk(chunk_id=cid, key=key, text=text,
                                pages=sorted(set(pages)), block_types=sorted(set(types))))
        buf, pages, types, size = [], [], [], 0

    for b in blocks:
        if b.get("type") in SKIP_TYPES:
            continue
        text = (b.get("text") or "").strip()
        if not text:
            continue
        if size + len(text) > MAX_CHARS and buf:
            flush()
        buf.append(text)
        if b.get("page") is not None:
            pages.append(int(b["page"]))
        types.append(b.get("type", ""))
        size += len(text)
        if size >= TARGET_CHARS:
            flush()

    flush()
    return chunks


# ── 词法切分（中英混排）────────────────────────────────────────
_CJK = re.compile(r"[\u4e00-\u9fff]+")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9\-_.]{1,}")


def tokenize(text: str) -> list[str]:
    """英文按词；CJK 用**字符二元组**（不引 jieba 依赖，BM25 够用）。"""
    tokens: list[str] = [w.lower() for w in _WORD.findall(text)]
    for run in _CJK.findall(text):
        if len(run) == 1:
            tokens.append(run)
            continue
        tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


# ── 索引 ────────────────────────────────────────────────────────
def _l2_normalize(vecs: list[list[float]]) -> "list[list[float]]":
    out = []
    for v in vecs:
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / n for x in v])
    return out


class HybridIndex:
    """向量 + 词法的混合索引，持久化到 `data/index/`。"""

    def __init__(self, chunks: list[Chunk], vectors, spec) -> None:
        self.chunks = chunks
        self._vectors = vectors  # numpy array (n, d)
        self.spec = spec
        self._bm25 = None

    # ── 构建 ────────────────────────────────────────────────────
    @classmethod
    def build(cls, chunks: list[Chunk], embedder, *, batch: int = 32,
              progress: bool = True) -> "HybridIndex":
        import numpy as np

        vecs: list[list[float]] = []
        t0 = time.time()
        for i in range(0, len(chunks), batch):
            part = chunks[i : i + batch]
            for attempt in range(4):
                try:
                    vecs.extend(embedder.embed([c.text for c in part]))
                    break
                except Exception as exc:  # noqa: BLE001 - 限流需自行退避
                    if "429" not in str(exc) and "rate" not in str(exc).lower():
                        raise
                    time.sleep(2 ** attempt * 5)
            if progress:
                done = min(i + batch, len(chunks))
                print(f"    嵌入 {done}/{len(chunks)}（{time.time()-t0:.0f}s）")
        arr = np.asarray(_l2_normalize(vecs), dtype="float32")
        return cls(chunks, arr, embedder.spec)

    # ── 检索 ────────────────────────────────────────────────────
    @property
    def bm25(self):
        if self._bm25 is None:
            from rank_bm25 import BM25Okapi

            self._bm25 = BM25Okapi([tokenize(c.text) for c in self.chunks])
        return self._bm25

    def _vector_ranks(self, query_vec: list[float], k: int) -> list[int]:
        # 向量已 L2 归一化，内积即余弦相似度。
        # 这里直接用 numpy 而不是 FAISS 检索：664 chunk × 1024 维的暴力内积是毫秒级，
        # 引入 FAISS 只会多一个路径相关故障面（见 save() 的注释）。
        import numpy as np

        q = np.asarray(_l2_normalize([query_vec])[0], dtype="float32")
        scores = self._vectors @ q
        return list(np.argsort(-scores)[:k])

    def _lexical_ranks(self, query: str, k: int) -> list[int]:
        scores = self.bm25.get_scores(tokenize(query))
        return sorted(range(len(scores)), key=lambda i: -scores[i])[:k]

    def search(self, query: str, embedder, *, top_k: int = DEFAULT_TOP_K,
               per_route: int | None = None) -> list[dict]:
        """混合检索：向量 + BM25 → RRF 融合 → 去重守卫。"""
        per_route = per_route or max(top_k * 2, 12)
        qvec = embedder.embed([query], is_query=True)[0]

        vec_rank = self._vector_ranks(qvec, per_route)
        lex_rank = self._lexical_ranks(query, per_route)

        # RRF：只用排名，零调参
        fused: dict[int, float] = {}
        for rank_list in (vec_rank, lex_rank):
            for rank, idx in enumerate(rank_list):
                fused[idx] = fused.get(idx, 0.0) + 1.0 / (RRF_K + rank + 1)

        ordered = sorted(fused.items(), key=lambda x: -x[1])

        # 去重守卫：同一 chunk 只出一次；文本高度重叠的也压掉
        seen: set[str] = set()
        out: list[dict] = []
        for idx, score in ordered:
            c = self.chunks[idx]
            fp = c.text[:120]
            if fp in seen:
                continue
            seen.add(fp)
            out.append({"chunk": c, "score": round(score, 6),
                        "cite": c.cite()})
            if len(out) >= top_k:
                break
        return out

    # ── 持久化 ──────────────────────────────────────────────────
    def save(self, index_dir: Path) -> None:
        """持久化。

        ⚠️ **不用 `faiss.write_index`** —— 实测在 Windows 上它会失败：
            `RuntimeError: could not open E:\\实习\\...\\vectors.faiss for writing`
            FAISS 内部用窄字符 `fopen`，路径含非 ASCII（本项目路径就有「实习」）就开不了文件。
            改用 numpy 存原始向量；`IndexFlatIP` 是暴力检索索引，
            加载时用 `add()` 重建完全等价，而且不依赖 FAISS 的文件 IO。
        """
        import numpy as np

        index_dir.mkdir(parents=True, exist_ok=True)
        np.save(index_dir / "vectors.npy", self._vectors)
        with (index_dir / "chunks.jsonl").open("w", encoding="utf-8") as fh:
            for c in self.chunks:
                fh.write(json.dumps(c.to_dict(), ensure_ascii=False) + "\n")
        n_keys = sorted({c.key for c in self.chunks})
        (index_dir / "manifest.json").write_text(
            json.dumps({
                "embedding": {"provider": self.spec.provider, "model": self.spec.model,
                              "dim": self.spec.dim, "fingerprint": self.spec.fingerprint()},
                "chunks": len(self.chunks), "papers": n_keys,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, index_dir: Path):
        import numpy as np

        manifest = json.loads((index_dir / "manifest.json").read_text(encoding="utf-8"))
        chunks = [Chunk.from_dict(json.loads(ln))
                  for ln in (index_dir / "chunks.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
        vectors = np.load(index_dir / "vectors.npy").astype("float32")

        from rsrc.llm import EmbeddingSpec

        e = manifest["embedding"]
        spec = EmbeddingSpec(provider=e["provider"], model=e["model"], dim=e["dim"])
        return cls(chunks, vectors, spec), manifest

    def fingerprint(self) -> str:
        return self.spec.fingerprint()
