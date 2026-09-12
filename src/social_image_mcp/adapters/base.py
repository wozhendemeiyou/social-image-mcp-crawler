from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

from ..intent import Intent
from ..models import ImageCandidate, Platform


class AdapterError(RuntimeError):
    pass


class AdapterUnavailable(AdapterError):
    pass


@dataclass(frozen=True)
class AdapterStatus:
    platform: Platform
    configured: bool
    mode: str
    detail: str


class PlatformAdapter(ABC):
    platform: Platform

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    @property
    @abstractmethod
    def status(self) -> AdapterStatus:
        raise NotImplementedError

    @abstractmethod
    async def search(self, intent: Intent, limit: int, safe_mode: bool) -> list[ImageCandidate]:
        raise NotImplementedError

    async def inspect(self, item_id: str) -> list[ImageCandidate]:
        return await self.search(Intent(raw=item_id, normalized=item_id.lower(), tokens=(item_id.lower(),), negative_tokens=(), identifier=item_id), 20, True)
