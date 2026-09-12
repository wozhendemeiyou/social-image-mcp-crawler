from __future__ import annotations

"""Small, bounded Bilibili public API client.

The client only reads public metadata and media URLs. It does not solve WBI
challenges, bypass login walls, or download media; the shared downloader owns
validation, retries and persistence.
"""

import asyncio
import html
import re
from dataclasses import dataclass
from typing import Any
import httpx

from .creator_protocol import creator_target, pack_cursor, unpack_cursor
from .models import CreatorFetchRequest, CreatorIdentity, ImageCandidate, Platform


class BilibiliError(RuntimeError):
    pass


@dataclass(frozen=True)
class BilibiliCreatorResult:
    identity: CreatorIdentity
    items: list[ImageCandidate]
    posts_fetched: int
    next_cursor: str | None = None
    post_ids: tuple[str, ...] = ()
    rejected_posts: int = 0
    pages_fetched: int = 0
    warnings: tuple[str, ...] = ()


def _text(value: Any) -> str:
    value = "" if value is None else str(value)
    value = html.unescape(value)
    return re.sub(r"<[^>]+>", "", value).strip()


def _url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    if value.startswith("//"):
        return "https:" + value
    return value if value.startswith(("http://", "https://")) else ""


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bvid(value: Any) -> str:
    value = _text(value)
    return value if value else ""


def _aid_params(identifier: str) -> dict[str, str]:
    identifier = identifier.strip()
    if identifier.lower().startswith("av"):
        return {"aid": identifier[2:]}
    if identifier.isdigit():
        return {"aid": identifier}
    return {"bvid": identifier}


class BilibiliApi:
    base_url = "https://api.bilibili.com"

    def __init__(self, client: httpx.AsyncClient | None, cookie: str | None = None, timeout_seconds: float = 12) -> None:
        self.client = client
        self.cookie = cookie or ""
        self.timeout_seconds = max(2.0, float(timeout_seconds))
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36",
            "Referer": "https://www.bilibili.com/",
        }
        if self.cookie:
            self.headers["Cookie"] = self.cookie

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if self.client is None:
            raise BilibiliError("Bilibili API client is not initialized")
        try:
            response = await self.client.get(
                self.base_url + path,
                params=params,
                headers=self.headers,
                timeout=httpx.Timeout(self.timeout_seconds, connect=4.0),
                follow_redirects=True,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (412, 429):
                raise BilibiliError("bilibili public API temporarily rejected this request (rate limit or WBI verification); retry later or configure BILIBILI_COOKIE") from exc
            raise BilibiliError(f"bilibili api request failed: {exc}") from exc
        if not isinstance(payload, dict):
            raise BilibiliError("bilibili api returned a non-object response")
        code = payload.get("code", 0)
        if code not in (0, "0", None):
            message = _text(payload.get("message") or payload.get("msg") or "request rejected")
            raise BilibiliError(f"bilibili api error {code}: {message}")
        return payload

    @staticmethod
    def _cover_candidate(row: dict[str, Any], index: int = 1) -> ImageCandidate | None:
        post_id = _bvid(row.get("bvid") or row.get("arcurl", "").rstrip("/").rsplit("/", 1)[-1])
        if not post_id:
            post_id = _text(row.get("aid"))
        cover = _url(row.get("pic") or row.get("cover"))
        if not post_id or not cover:
            return None
        owner = row.get("owner") if isinstance(row.get("owner"), dict) else {}
        author = _text(owner.get("name") or row.get("author") or row.get("uname"))
        creator_id = _text(owner.get("mid") or row.get("mid")) or None
        permalink = _url(row.get("arcurl") or row.get("uri")) or f"https://www.bilibili.com/video/{post_id}"
        title = _text(row.get("title") or row.get("name"))
        description = _text(row.get("description") or row.get("desc") or row.get("tag"))
        published = row.get("pubdate") or row.get("created") or row.get("ctime")
        engagement = max(
            0.0,
            _number(row.get("play")) + _number(row.get("like")) * 4 + _number(row.get("favorites")) * 2,
        )
        return ImageCandidate(
            id=f"{post_id}:{index}",
            platform=Platform.BILIBILI,
            image_url=cover,
            thumbnail_url=cover,
            permalink=permalink,
            title=title,
            description=description,
            author=author,
            width=None,
            height=None,
            published_at=str(published) if published not in (None, "") else None,
            creator_id=creator_id,
            creator_name=author,
            post_id=post_id,
            media_index=index,
            engagement_score=engagement,
            source_payload={"source": "bilibili-api", "record": row},
        )

    async def _view(self, identifier: str) -> dict[str, Any]:
        payload = await self._get("/x/web-interface/view", _aid_params(identifier))
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    async def _video_url(self, row: dict[str, Any]) -> str | None:
        bvid = _bvid(row.get("bvid"))
        if not bvid:
            return None
        try:
            detail = await self._view(bvid)
            pages = detail.get("pages") if isinstance(detail.get("pages"), list) else []
            page = pages[0] if pages else {}
            cid = page.get("cid") if isinstance(page, dict) else None
            if not cid:
                return None
            payload = await self._get(
                "/x/player/playurl",
                # Progressive ``durl`` is a complete audio/video file. DASH
                # tracks require muxing and therefore cannot be handed to the
                # shared single-file downloader.
                {"bvid": bvid, "cid": str(cid), "fnval": 1, "fnver": 0, "fourk": 1, "qn": 80},
            )
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            durl = data.get("durl") if isinstance(data.get("durl"), list) else []
            urls = [_url(item.get("url")) for item in durl if isinstance(item, dict)]
            if urls:
                return urls[0]
        except BilibiliError:
            return None
        return None

    async def _original_video_candidate(self, row: dict[str, Any]) -> ImageCandidate | None:
        base = self._cover_candidate(row, 2)
        if base is None:
            return None
        video_url = await self._video_url(row)
        if not video_url:
            return None
        return base.model_copy(update={
            "id": f"{base.post_id}:video",
            "image_url": video_url,
            "media_type": "video",
            "thumbnail_url": base.image_url,
            "media_index": 2,
        })

    async def resolve_identity(self, request: CreatorFetchRequest) -> CreatorIdentity:
        requested = request.profile_url or request.creator_id or request.creator_name or ""
        if request.creator_name:
            name = request.creator_name.strip()
            payload = await self._get(
                "/x/web-interface/search/type",
                {"search_type": "bili_user", "keyword": name, "page": 1, "page_size": 20},
            )
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            rows = data.get("result") if isinstance(data.get("result"), list) else []
            matches = [row for row in rows if isinstance(row, dict) and _text(row.get("uname") or row.get("name")) == name and _text(row.get("mid"))]
            mids = {str(row["mid"]): row for row in matches}
            if len(mids) != 1:
                raise BilibiliError(f"creator_identity_unresolved: exact Bilibili name matched {len(mids)} users")
            target = next(iter(mids))
            matched_by = "exact_account_name"
        else:
            target = creator_target("bilibili", requested)
            matched_by = "profile_url" if requested.startswith(("http://", "https://")) else "exact_uid"
        # The public card endpoint is both lighter and less likely to require
        # WBI signing than the full space profile endpoint.
        payload = await self._get("/x/web-interface/card", {"mid": target})
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        profile = data.get("card") if isinstance(data.get("card"), dict) else {}
        confirmed = _text(profile.get("mid"))
        if confirmed and confirmed != target:
            raise BilibiliError("creator_identity_mismatch: Bilibili profile UID differs from the requested UID")
        return CreatorIdentity(
            platform=Platform.BILIBILI,
            requested_id=requested,
            canonical_id=target,
            name=_text(profile.get("name")),
            profile_url=f"https://space.bilibili.com/{target}",
            source="bilibili-api",
            matched_by=matched_by,
        )

    async def search(self, intent: Any, limit: int, safe_mode: bool = True) -> list[ImageCandidate]:
        if intent.identifier and intent.identifier_platform in (None, "bilibili"):
            row = await self._view(intent.identifier)
            candidate = self._cover_candidate(row)
            return [candidate] if candidate else []
        query = intent.raw.strip()
        payload = await self._get(
            "/x/web-interface/search/type",
            {"search_type": "video", "keyword": query, "page": 1, "page_size": min(max(limit, 1), 50)},
        )
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        rows = data.get("result") if isinstance(data.get("result"), list) else []
        result: list[ImageCandidate] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            candidate = self._cover_candidate(row)
            if candidate:
                result.append(candidate)
            if len(result) >= limit:
                break
        return result

    async def fetch_creator(self, request: CreatorFetchRequest) -> BilibiliCreatorResult:
        identity = await self.resolve_identity(request)
        native, _ = unpack_cursor(request.cursor)
        page = max(1, int(native or "1"))
        page_size = min(50, max(1, request.max_posts))
        payload = await self._get(
            "/x/space/arc/search",
            {"mid": identity.canonical_id, "pn": page, "ps": page_size, "order": "pubdate", "tid": 0, "platform": "web"},
        )
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        listing = data.get("list") if isinstance(data.get("list"), dict) else {}
        rows = listing.get("vlist") if isinstance(listing.get("vlist"), list) else []
        items: list[ImageCandidate] = []
        post_ids: list[str] = []
        warnings: list[str] = []
        video_tasks: list[tuple[dict[str, Any], asyncio.Task[ImageCandidate | None]]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            post_id = _bvid(row.get("bvid")) or _text(row.get("aid"))
            if post_id:
                post_ids.append(post_id)
            if request.media_type != "videos":
                candidate = self._cover_candidate(row)
                if candidate:
                    items.append(candidate.model_copy(update={"creator_id": identity.canonical_id, "creator_name": identity.name, "author": identity.name}))
            if request.media_type in ("videos", "all"):
                video_tasks.append((row, asyncio.create_task(self._original_video_candidate(row))))
        if video_tasks:
            results = await asyncio.gather(*(task for _, task in video_tasks), return_exceptions=True)
            for (row, _), result in zip(video_tasks, results):
                if isinstance(result, Exception) or result is None:
                    warnings.append(f"video URL unavailable: {_bvid(row.get('bvid')) or _text(row.get('aid'))}")
                    continue
                items.append(result.model_copy(update={"creator_id": identity.canonical_id, "creator_name": identity.name, "author": identity.name}))
        page_info = data.get("page") if isinstance(data.get("page"), dict) else {}
        total = int(_number(page_info.get("count"), 0))
        next_cursor = pack_cursor(page + 1) if rows and (not total or page * page_size < total) else None
        return BilibiliCreatorResult(
            identity=identity,
            items=items,
            posts_fetched=len(rows),
            next_cursor=next_cursor,
            post_ids=tuple(dict.fromkeys(post_ids)),
            pages_fetched=1,
            warnings=tuple(warnings),
        )
