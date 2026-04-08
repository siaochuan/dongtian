"""Embedding API client for Dongtian (OpenAI-compatible: SiliconFlow, etc.)."""

import struct
from typing import Optional

import httpx


class EmbeddingClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.siliconflow.cn/v1",
        model: str = "BAAI/bge-m3",
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = httpx.Client(timeout=60)

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "input": texts, "encoding_format": "float"},
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        data.sort(key=lambda x: x["index"])
        return [d["embedding"] for d in data]

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]

    def close(self) -> None:
        self._client.close()


def pack_embedding(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def unpack_embedding(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


_client: Optional[EmbeddingClient] = None


def get_client(config: dict) -> Optional[EmbeddingClient]:
    global _client
    if _client is not None:
        return _client
    api_key = config.get("embedding_api_key")
    if not api_key:
        return None
    _client = EmbeddingClient(
        api_key=api_key,
        base_url=config.get("embedding_base_url", "https://api.siliconflow.cn/v1"),
        model=config.get("embedding_model", "BAAI/bge-m3"),
    )
    return _client
