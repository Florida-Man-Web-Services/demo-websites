"""Embedding providers.

- HashingEmbedder: deterministic, dependency-free, offline. Good enough to make
  the whole pipeline run and be tested without any API key.
- OpenAIEmbedder: OpenAI-compatible endpoint (OpenAI, or any gateway that speaks
  the same /embeddings API) for production-quality retrieval.
"""
from __future__ import annotations

import hashlib
import re
from typing import List

import numpy as np

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> List[str]:
    return _TOKEN.findall(text.lower())


class HashingEmbedder:
    """Hashed bag-of-words → L2-normalized vector. No network, no model download."""

    def __init__(self, dim: int = 512):
        self.dim = dim

    def embed(self, texts: List[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            for tok in _tokens(text):
                h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
                out[i, h % self.dim] += 1.0
            n = np.linalg.norm(out[i])
            if n:
                out[i] /= n
        return out


class OpenAIEmbedder:
    """Calls an OpenAI-compatible /embeddings endpoint via plain HTTP."""

    def __init__(self, base_url: str, api_key: str, model: str):
        if not api_key:
            raise RuntimeError("WVS_EMBED_API_KEY is required for the openai provider")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.dim = None  # discovered from the first response

    def embed(self, texts: List[str]) -> np.ndarray:
        import requests  # imported lazily so the offline default has no dep

        resp = requests.post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "input": texts},
            timeout=60,
        )
        resp.raise_for_status()
        vecs = [d["embedding"] for d in resp.json()["data"]]
        arr = np.asarray(vecs, dtype=np.float32)
        self.dim = arr.shape[1]
        # normalize so cosine == dot product
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return arr / norms


def get_embedder(cfg):
    if cfg.embed_provider == "openai":
        return OpenAIEmbedder(cfg.embed_base_url, cfg.embed_api_key, cfg.embed_model)
    return HashingEmbedder(cfg.embed_dim)
