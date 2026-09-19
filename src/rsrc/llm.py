"""LLM 与 Embedding 的 Provider 抽象。

## 为什么要抽象

调研给出的硬约束：**不同 provider 的向量互不兼容，绝不能混存**。
一旦中途换模型，索引必须**全量重建** —— 所以索引元数据里必须记录
`provider / model / dim`，并在加载时校验指纹。

本模块只做「接口 + 指纹」；具体选型由 `.env` 决定，可自由切换。

## 兼容性

生成侧与向量侧**都是 OpenAI 兼容接口**（DeepSeek / 阿里云百炼 /
SiliconFlow / OpenRouter），所以一套 `openai` SDK 代码即可，只换
`base_url` 与 `model`。

## 本地兜底

没有 API key 时用 `fastembed`（ONNX Runtime，**不拉 PyTorch**）。
默认模型 `paraphrase-multilingual-MiniLM-L12-v2`（384 维 / 0.22 GB），
中英混合最省。
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

# ── 常用 provider 的预设（base_url 与默认模型）──────────────────
# ⚠️ 阿里云百炼的 OpenAI 兼容域名已变更：旧的
#    https://dashscope.aliyuncs.com/compatible-mode/v1 官方兼容页已不再列出，
#    新项目应使用 {WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "llm_model": "deepseek-chat",
    },
    "dashscope": {  # 阿里云百炼 / 通义（北京地域）
        "base_url": "",  # 需填 {WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
        "llm_model": "qwen-plus",
        "embed_model": "text-embedding-v4",
        "embed_dim": "1024",
        # 免费额度 100 万 token / 90 天；单请求上限 10 条 / 33,000 token
        "batch_size": "10",
        "max_tokens_per_batch": "33000",
    },
    "siliconflow": {
        "base_url": "https://api.siliconflow.cn/v1",
        "llm_model": "Qwen/Qwen3-8B",
        "embed_model": "BAAI/bge-m3",  # ¥0 / M token，RPM 2000 / TPM 500k
        "embed_dim": "1024",
        "batch_size": "32",
        "max_tokens_per_batch": "100000",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "llm_model": "deepseek/deepseek-chat",
        # ⚠️ :free 变体官方描述「可能被留存并用于训练」，且无充值仅 50 请求/天
        #     —— 不建议作为主力；详见 PLAN.md
        "embed_model": "nvidia/nemotron-3-embed-1b:free",
        "embed_dim": "1024",
        "batch_size": "20",
        "max_tokens_per_batch": "32000",
    },
    # 离线兜底（fastembed / ONNX，不拉 PyTorch）。
    # 候选（实测 fastembed 支持的多语/中文模型）：
    #   sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2  384  0.22 GB
    #   sentence-transformers/paraphrase-multilingual-mpnet-base-v2   768  1.00 GB  ← 默认
    #   intfloat/multilingual-e5-large                               1024  2.24 GB
    #   jinaai/jina-embeddings-v2-base-zh                             768  0.64 GB（仅中文，8192 上下文）
    #   BAAI/bge-small-zh-v1.5                                        512  0.09 GB（仅中文）
    # ⚠️ fastembed **不支持** BAAI/bge-m3（实测报 ValueError）
    "local": {
        "embed_model": "sentence-transformers/paraphrase-multilingual-mpnet-base-v2",
        "embed_dim": "768",
    },
}


@dataclass(frozen=True)
class EmbeddingSpec:
    """一次索引构建所用的向量模型指纹。

    索引与向量必须与它绑定；不匹配即视为不可复用，必须重建。
    """

    provider: str
    model: str
    dim: int

    def fingerprint(self) -> str:
        """稳定指纹，用于校验索引与模型是否匹配。"""
        raw = f"{self.provider}|{self.model}|{self.dim}".encode()
        return hashlib.sha256(raw).hexdigest()[:16]


@runtime_checkable
class Embedder(Protocol):
    """向量化接口。"""

    spec: EmbeddingSpec

    def embed(self, texts: list[str], *, is_query: bool = False) -> list[list[float]]:
        """把一批文本向量化。

        `is_query` 用于支持 query/document 非对称检索的模型
        （百炼的 `text_type`、OpenRouter 的 `input_type`）。
        """
        ...


# ── OpenAI 兼容实现 ─────────────────────────────────────────────
@dataclass
class OpenAICompatEmbedder:
    """走 OpenAI 兼容 `/embeddings` 的 provider（百炼 / SiliconFlow / OpenRouter）。"""

    spec: EmbeddingSpec
    api_key: str
    base_url: str
    batch_size: int = 20
    timeout: float = 60.0
    _client: object = field(default=None, repr=False)

    def _lazy_client(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=self.api_key, base_url=self.base_url, timeout=self.timeout
            )
        return self._client

    def embed(self, texts: list[str], *, is_query: bool = False) -> list[list[float]]:
        client = self._lazy_client()
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            chunk = texts[start : start + self.batch_size]
            kwargs: dict = {"model": self.spec.model, "input": chunk}
            # MRL 模型才支持降维；不支持的模型传了可能报 400
            if self.spec.dim:
                kwargs["dimensions"] = self.spec.dim
            resp = client.embeddings.create(**kwargs)
            vectors.extend([item.embedding for item in resp.data])
        return vectors


# ── 离线兜底 ────────────────────────────────────────────────────
@dataclass
class LocalEmbedder:
    """fastembed（ONNX Runtime）实现，无需 API key、无需 GPU。"""

    spec: EmbeddingSpec
    _model: object = field(default=None, repr=False)

    def _lazy_model(self):
        if self._model is None:
            try:
                from fastembed import TextEmbedding
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError(
                    "本地兜底需要 fastembed：pixi add fastembed"
                ) from exc
            supported = {m["model"] for m in TextEmbedding.list_supported_models()}
            if self.spec.model not in supported:
                raise ValueError(
                    f"fastembed 不支持 `{self.spec.model}`。可选的多语/中文模型：\n  "
                    + "\n  ".join(sorted(supported))
                )
            self._model = TextEmbedding(model_name=self.spec.model)
        return self._model

    def embed(self, texts: list[str], *, is_query: bool = False) -> list[list[float]]:
        model = self._lazy_model()
        return [vec.tolist() for vec in model.embed(texts)]


# ── 工厂 ────────────────────────────────────────────────────────
def build_embedder(provider: str | None = None) -> Embedder:
    """按 `.env` 构造 embedder。

    优先级：显式参数 > `EMBED_PROVIDER` > 有 key 的 provider > 本地兜底。
    """
    name = (provider or os.environ.get("EMBED_PROVIDER") or "").lower()
    if not name:
        if os.environ.get("DASHSCOPE_API_KEY"):
            name = "dashscope"
        elif os.environ.get("SILICONFLOW_API_KEY"):
            name = "siliconflow"
        elif os.environ.get("OPENROUTER_API_KEY"):
            name = "openrouter"
        else:
            name = "local"

    preset = PROVIDER_PRESETS.get(name)
    if preset is None:
        raise ValueError(f"未知 provider：{name}（可选：{'/'.join(PROVIDER_PRESETS)}）")

    model = os.environ.get("EMBED_MODEL") or preset.get("embed_model", "")
    dim = int(os.environ.get("EMBED_DIM") or preset.get("embed_dim", "0") or 0)
    spec = EmbeddingSpec(provider=name, model=model, dim=dim)

    if name == "local":
        return LocalEmbedder(spec=spec)

    key = os.environ.get(f"{name.upper()}_API_KEY", "")
    if not key:
        raise RuntimeError(
            f"{name} 缺少 API key（设置 {name.upper()}_API_KEY），"
            f"或改用 EMBED_PROVIDER=local 走离线兜底"
        )
    base_url = os.environ.get("EMBED_BASE_URL") or preset.get("base_url", "")
    if not base_url:
        raise RuntimeError(f"{name} 未配置 base_url，请设置 EMBED_BASE_URL")
    return OpenAICompatEmbedder(
        spec=spec,
        api_key=key,
        base_url=base_url,
        batch_size=int(preset.get("batch_size", "20")),
    )
