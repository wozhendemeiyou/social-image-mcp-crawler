from __future__ import annotations

import hashlib
import asyncio
import json
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from .intent import Intent, expand_token
from .models import ImageCandidate, Platform


_SITE_FILTERS = {
    Platform.DOUYIN: "(site:douyin.com/video OR site:douyin.com/note)",
    Platform.XHS: "(site:xiaohongshu.com/explore OR site:xiaohongshu.com/discovery/item)",
    Platform.WEIBO: "site:weibo.com",
    Platform.BILIBILI: "(site:bilibili.com/video OR site:b23.tv)",
    Platform.X: "(site:x.com OR site:twitter.com)",
    Platform.INSTAGRAM: "site:instagram.com/p",
}

_ALLOWED_DOMAINS = {
    Platform.DOUYIN: ("douyin.com",),
    Platform.XHS: ("xiaohongshu.com", "xhslink.com"),
    Platform.WEIBO: ("weibo.com", "weibo.cn"),
    Platform.BILIBILI: ("bilibili.com", "b23.tv"),
    Platform.X: ("x.com", "twitter.com"),
    Platform.INSTAGRAM: ("instagram.com",),
}


class DiscoveryError(RuntimeError):
    pass


def _clean_query(intent: Intent) -> str:
    control = {"横图", "竖图", "方图", "高清", "高分辨率", "原图", "无水印", "不要水印", "去水印", "无文字", "不要文字", "无字"}
    tokens = [token for token in intent.tokens if token not in control]
    expanded: list[str] = []
    for token in tokens:
        expanded.extend(sorted(expand_token(token), key=lambda value: (len(value), value)))
    return " ".join(dict.fromkeys(expanded)) or intent.raw


class BingImageDiscovery:
    """Cookie/API-free image recall using Bing's public image index."""

    provider = "bing-images"

    def __init__(self, client: httpx.AsyncClient, timeout: float = 20.0) -> None:
        self.client = client
        self.timeout = timeout

    @property
    def status(self) -> dict[str, Any]:
        return {"provider": self.provider, "configured": True, "mode": "public-index", "detail": "Public image index recall; no platform cookie or API token required"}

    def _queries(self, platform: Platform, intent: Intent) -> list[str]:
        site = _SITE_FILTERS[platform]
        brief = _clean_query(intent)
        if intent.identifier:
            domain = urlparse(intent.url).netloc if intent.url else {Platform.DOUYIN: "douyin.com", Platform.XHS: "xiaohongshu.com", Platform.WEIBO: "weibo.com", Platform.BILIBILI: "bilibili.com", Platform.X: "x.com", Platform.INSTAGRAM: "instagram.com"}[platform]
            return [f'{site} "{intent.identifier}"', f'site:{domain} "{intent.identifier}"']
        queries = [f"{site} {brief}"]
        if len(intent.tokens) > 2:
            queries.append(f"{site} {' '.join(intent.tokens[:8])}")
        if intent.quality_preference == "high":
            queries.append(f"{site} {brief} high resolution")
        return list(dict.fromkeys(queries))

    async def _fetch(self, platform: Platform, query: str, limit: int) -> list[ImageCandidate]:
        try:
            response = await self.client.get("https://www.bing.com/images/search", params={"q": query, "form": "HDRSC2", "first": 1, "count": min(max(limit, 10), 150)}, timeout=self.timeout, follow_redirects=True)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise DiscoveryError(f"bing image search failed: {exc}") from exc
        soup = BeautifulSoup(response.text, "html.parser")
        result: list[ImageCandidate] = []
        for index, node in enumerate(soup.select("a.iusc")):
            try:
                meta = json.loads(node.get("m", ""))
            except (TypeError, ValueError):
                continue
            image_url = meta.get("murl") or meta.get("turl")
            if not isinstance(image_url, str) or not image_url.startswith(("http://", "https://")):
                continue
            permalink = meta.get("purl") or node.get("href")
            source_host = urlparse(permalink).netloc.lower() if isinstance(permalink, str) else ""
            if not any(source_host == domain or source_host.endswith("." + domain) for domain in _ALLOWED_DOMAINS[platform]):
                continue
            stable = hashlib.sha1(f"{permalink}|{image_url}".encode("utf-8", "ignore")).hexdigest()[:20]
            result.append(ImageCandidate(id=stable, platform=Platform.X, image_url=image_url, thumbnail_url=meta.get("turl"), permalink=permalink, title=str(meta.get("t") or meta.get("desc") or ""), description=str(meta.get("desc") or meta.get("t") or ""), width=meta.get("w"), height=meta.get("h"), source_payload={"provider": self.provider, "index": index, "metadata": meta}))
            if len(result) >= limit:
                break
        return result

    async def search(self, platform: Platform, intent: Intent, limit: int) -> list[ImageCandidate]:
        candidates: list[ImageCandidate] = []
        queries = self._queries(platform, intent)
        batches = await asyncio.gather(*(self._fetch(platform, query, limit) for query in queries))
        for query, rows in zip(queries, batches):
            for row in rows:
                candidates.append(row.model_copy(update={"platform": platform, "source_payload": {**row.source_payload, "platform": platform.value, "query": query}}))
        return candidates
