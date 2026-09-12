from __future__ import annotations

import asyncio
from typing import Sequence

from .intent import Intent
from .models import ImageCandidate


class SemanticReranker:
    """Optional sentence-transformers reranker loaded only when configured."""

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = model_name
        self._model = None
        self._load_error: str | None = None
        self._last_applied = False

    @property
    def enabled(self) -> bool:
        return bool(self.model_name)

    @property
    def status(self) -> dict[str, str | bool | None]:
        if not self.model_name:
            return {"enabled": False, "model": None, "error": None}
        if self._load_error:
            return {"enabled": False, "model": self.model_name, "error": self._load_error}
        return {"enabled": True, "model": self.model_name, "error": None}

    def _load(self):
        if self._model is not None:
            return self._model
        if not self.model_name:
            return None
        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
            return self._model
        except Exception as exc:
            self._load_error = str(exc)
            return None

    @staticmethod
    def _text(item: ImageCandidate) -> str:
        return " ".join(part for part in (item.title, item.description, item.author, item.alt_text, item.permalink or "") if part)

    async def rerank(self, items: Sequence[ImageCandidate], intent: Intent, max_results: int) -> list[ImageCandidate]:
        self._last_applied = False
        if not self.enabled or not items:
            return list(items)[:max_results]
        model = await asyncio.to_thread(self._load)
        if model is None:
            return list(items)[:max_results]
        try:
            vectors = await asyncio.to_thread(model.encode, [intent.raw, *[self._text(item) for item in items]], normalize_embeddings=True)
            query_vector = vectors[0]
            scored: list[ImageCandidate] = []
            for item, vector in zip(items, vectors[1:]):
                similarity = float(query_vector @ vector)
                score = item.score * 0.45 + similarity * 2.2
                scored.append(item.model_copy(update={"score": round(score, 4)}))
            scored.sort(key=lambda item: item.score, reverse=True)
            self._last_applied = True
            return scored[:max_results]
        except Exception as exc:
            self._load_error = str(exc)
            return list(items)[:max_results]
