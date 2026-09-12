import asyncio
import time
import pytest

from social_image_mcp.creator_protocol import CreatorCollector, creator_target, douyin_identity, pack_cursor, unpack_cursor
from social_image_mcp.creator_service import CreatorImageService
from social_image_mcp.models import CreatorFetchRequest, CreatorIdentity, ImageCandidate, Platform
from social_image_mcp.sources import CreatorSourceResult


def _identity() -> CreatorIdentity:
    return CreatorIdentity(platform=Platform.DOUYIN, requested_id="Gracebb0722", canonical_id="sec-1", name="放学小野猪", source="test", matched_by="exact_account_id")


def test_creator_target_requires_platform_specific_ids():
    assert creator_target("douyin", "douyin-user:Gracebb0722") == "Gracebb0722"
    assert creator_target("weibo", "https://weibo.com/u/5756404150") == "5756404150"
    with pytest.raises(ValueError):
        creator_target("weibo", "weibo-user:alice")


def test_creator_name_request_is_supported():
    request = CreatorFetchRequest(platform=Platform.DOUYIN, creator_name="放学小野猪", media_type="videos", download=False)
    assert request.creator_name == "放学小野猪"
    assert request.media_type == "videos"
    chinese_id = CreatorFetchRequest(platform=Platform.DOUYIN, creator_id="放学小野猪", download=False)
    assert chinese_id.creator_name == "放学小野猪" and chinese_id.creator_id is None


def test_different_creator_ids_run_concurrently(tmp_path):
    class Settings:
        cache_path = str(tmp_path / "cache.sqlite3")
        creator_timeout_seconds = 5

    async def run():
        service = CreatorImageService(Settings(), object(), object())

        async def fake_fetch(request):
            await asyncio.sleep(0.12)
            return {"items": [], "downloads": [], "error": None}

        service._fetch = fake_fetch
        requests = [
            CreatorFetchRequest(platform=Platform.WEIBO, creator_id="10001", download=False),
            CreatorFetchRequest(platform=Platform.WEIBO, creator_id="10002", download=False),
        ]
        started = time.perf_counter()
        await asyncio.gather(*(service.fetch(request) for request in requests))
        assert time.perf_counter() - started < 0.22

    asyncio.run(run())


def test_douyin_identity_resolves_exact_nickname():
    class Client:
        def search_users(self, value, count=20):
            return {"user_list": [{"user_info": {"sec_uid": "sec-1", "nickname": value}}]}
        def get_user_profile(self, sec_uid):
            return {"sec_uid": sec_uid, "nickname": "放学小野猪"}
    identity = douyin_identity(Client(), "放学小野猪", nickname=True)
    assert identity.canonical_id == "sec-1"
    assert identity.matched_by == "exact_account_name"


def test_creator_collector_hard_filters_author_and_keeps_media_order():
    identity = _identity()
    request = CreatorFetchRequest(platform=Platform.DOUYIN, creator_id="Gracebb0722", max_posts=2, max_images=10, download=False)
    collector = CreatorCollector(identity, request)
    rows = [
        {"aweme_id": "p1", "author": {"sec_uid": "sec-1"}, "desc": "one", "images": [{"url_list": ["https://img.test/1.jpg"]}, {"url_list": ["https://img.test/2.jpg"]}]},
        {"aweme_id": "foreign", "author": {"sec_uid": "other"}, "images": [{"url_list": ["https://img.test/foreign.jpg"]}]},
    ]
    assert collector.consume(rows, "1", True) is False
    result = collector.result()
    assert result["post_ids"] == ["p1"]
    assert [item["id"] for item in result["items"]] == ["p1:1", "p1:2"]
    assert result["rejected_posts"] == 1


def test_creator_cursor_round_trip():
    cursor = pack_cursor(18, 2)
    assert unpack_cursor(cursor) == ("18", 2)
    with pytest.raises(ValueError):
        unpack_cursor("bad")


def test_creator_service_does_not_call_semantic_or_vision(tmp_path):
    class Settings:
        cache_path = str(tmp_path / "cache.sqlite3")
        output_dir = str(tmp_path / "out")
        creator_timeout_seconds = 5

        def ensure_output_dir(self, value=None):
            from pathlib import Path
            path = Path(value or self.output_dir)
            path.mkdir(parents=True, exist_ok=True)
            return path

    candidate = ImageCandidate(id="p1:1", platform=Platform.DOUYIN, image_url="https://img.test/1.jpg", creator_id="sec-1", post_id="p1", media_index=1)

    class Sources:
        async def fetch_creator(self, request):
            return CreatorSourceResult(identity=_identity(), items=[candidate], posts_fetched=1, post_ids=("p1",), pages_fetched=1)

    class Downloader:
        async def download_many(self, *args, **kwargs):
            raise AssertionError("download should not run when download=false")

    async def run():
        service = CreatorImageService(Settings(), Sources(), Downloader())
        result = await service.fetch(CreatorFetchRequest(platform=Platform.DOUYIN, creator_id="Gracebb0722", max_images=1, download=False, output_dir=str(tmp_path / "out"), resume=False))
        assert result["error"] is None
        assert result["semantic_applied"] is False
        assert result["vision_applied"] is False
        assert result["items"][0]["creator_id"] == "sec-1"

    asyncio.run(run())


def test_creator_default_mode_does_not_run_object_filter(tmp_path):
    class Settings:
        cache_path = str(tmp_path / "cache.sqlite3")
        output_dir = str(tmp_path / "out")
        creator_timeout_seconds = 5

        def ensure_output_dir(self, value=None):
            from pathlib import Path
            path = Path(value or self.output_dir)
            path.mkdir(parents=True, exist_ok=True)
            return path

    candidate = ImageCandidate(id="p1", platform=Platform.DOUYIN, image_url="https://img.test/1.jpg", creator_id="sec-1", post_id="p1", media_index=1)

    class Sources:
        async def fetch_creator(self, request):
            return CreatorSourceResult(identity=_identity(), items=[candidate], posts_fetched=1, post_ids=("p1",), pages_fetched=1)

    class Detector:
        configured = True
        async def inspect(self, *args):
            raise AssertionError("object detector must not run in default account mode")

    async def run():
        service = CreatorImageService(Settings(), Sources(), object(), object_detector=Detector())
        result = await service.fetch(CreatorFetchRequest(platform=Platform.DOUYIN, creator_id="Gracebb0722", max_images=1, download=False, output_dir=str(tmp_path / "out"), resume=False))
        assert result["items"]
        assert result["semantic_requested"] is False
        assert result["object_detection_applied"] is False

    asyncio.run(run())


def test_creator_required_object_filter_fails_closed_without_detector(tmp_path):
    class Settings:
        cache_path = str(tmp_path / "cache.sqlite3")
        output_dir = str(tmp_path / "out")
        creator_timeout_seconds = 5
        def ensure_output_dir(self, value=None):
            from pathlib import Path
            path = Path(value or self.output_dir); path.mkdir(parents=True, exist_ok=True); return path

    candidate = ImageCandidate(id="p1", platform=Platform.DOUYIN, image_url="https://img.test/1.jpg", creator_id="sec-1", post_id="p1", media_index=1)
    class Sources:
        async def fetch_creator(self, request):
            return CreatorSourceResult(identity=_identity(), items=[candidate], posts_fetched=1, post_ids=("p1",), pages_fetched=1)

    async def run():
        service = CreatorImageService(Settings(), Sources(), object())
        result = await service.fetch(CreatorFetchRequest(platform=Platform.DOUYIN, creator_id="Gracebb0722", content_query="只要人物穿搭，排除风景", filter_mode="required", download=False, output_dir=str(tmp_path / "out"), resume=False))
        assert result["items"] == []
        assert result["error"]["code"] == "content_filter_required"
        assert result["object_detection_applied"] is False

    asyncio.run(run())


def test_creator_optional_object_filter_keeps_detector_decisions(tmp_path):
    class Settings:
        cache_path = str(tmp_path / "cache.sqlite3")
        output_dir = str(tmp_path / "out")
        creator_timeout_seconds = 5
        def ensure_output_dir(self, value=None):
            from pathlib import Path
            path = Path(value or self.output_dir); path.mkdir(parents=True, exist_ok=True); return path

    items = [
        ImageCandidate(id="person", platform=Platform.DOUYIN, image_url="https://img.test/person.jpg", creator_id="sec-1", post_id="p1", media_index=1),
        ImageCandidate(id="landscape", platform=Platform.DOUYIN, image_url="https://img.test/landscape.jpg", creator_id="sec-1", post_id="p2", media_index=1),
    ]
    class Sources:
        async def fetch_creator(self, request):
            return CreatorSourceResult(identity=_identity(), items=items, posts_fetched=2, post_ids=("p1", "p2"), pages_fetched=1)
    class Detector:
        configured = True
        async def inspect(self, values, spec, fail_closed=False):
            return values[:1], {"object_detection_applied": True, "object_detection_error": None, "object_decisions": {}, "object_rejected": 1, "object_unverified": 0, "object_backend": "fake"}

    async def run():
        service = CreatorImageService(Settings(), Sources(), object(), object_detector=Detector())
        result = await service.fetch(CreatorFetchRequest(platform=Platform.DOUYIN, creator_id="Gracebb0722", max_images=2,
            content_query="只要人物穿搭，排除风景", filter_mode="optional", download=False, output_dir=str(tmp_path / "out"), resume=False))
        assert [item["id"] for item in result["items"]] == ["person"]
        assert result["object_detection_applied"] is True
        assert result["object_rejected"] == 1

    asyncio.run(run())


def test_creator_required_object_filter_reports_when_all_items_are_rejected(tmp_path):
    class Settings:
        cache_path = str(tmp_path / "cache.sqlite3")
        output_dir = str(tmp_path / "out")
        creator_timeout_seconds = 5

        def ensure_output_dir(self, value=None):
            from pathlib import Path
            path = Path(value or self.output_dir); path.mkdir(parents=True, exist_ok=True); return path

    candidate = ImageCandidate(id="scene", platform=Platform.DOUYIN, image_url="https://img.test/scene.jpg", creator_id="sec-1", post_id="p1", media_index=1)

    class Sources:
        async def fetch_creator(self, request):
            return CreatorSourceResult(identity=_identity(), items=[candidate], posts_fetched=1, post_ids=("p1",), pages_fetched=1)

    class Detector:
        configured = True

        async def inspect(self, values, spec, fail_closed=False):
            return [], {
                "object_detection_applied": True, "object_detection_error": None,
                "object_decisions": {candidate.id: {"content_decision": "rejected"}},
                "object_rejected": 1, "object_unverified": 0, "object_backend": "fake",
            }

    async def run():
        service = CreatorImageService(Settings(), Sources(), object(), object_detector=Detector())
        result = await service.fetch(CreatorFetchRequest(
            platform=Platform.DOUYIN, creator_id="Gracebb0722", max_images=1,
            content_query="只要人物", filter_mode="required", download=False,
            output_dir=str(tmp_path / "out"), resume=False,
        ))
        assert result["items"] == []
        assert result["error"]["code"] == "content_filter_required"
        assert "no image matched" in result["error"]["message"]

    asyncio.run(run())


def test_creator_object_filter_timeout_returns_without_blocking(tmp_path):
    class Settings:
        cache_path = str(tmp_path / "cache.sqlite3")
        output_dir = str(tmp_path / "out")
        creator_timeout_seconds = 5
        object_filter_timeout_seconds = 0.05
        def ensure_output_dir(self, value=None):
            from pathlib import Path
            path = Path(value or self.output_dir); path.mkdir(parents=True, exist_ok=True); return path

    candidate = ImageCandidate(id="p1", platform=Platform.DOUYIN, image_url="https://img.test/1.jpg", creator_id="sec-1", post_id="p1", media_index=1)
    class Sources:
        async def fetch_creator(self, request):
            return CreatorSourceResult(identity=_identity(), items=[candidate], posts_fetched=1, post_ids=("p1",), pages_fetched=1)
    class Detector:
        configured = True
        async def inspect(self, *args, **kwargs):
            await asyncio.sleep(1)
            return [candidate], {"object_detection_applied": True}

    async def run():
        service = CreatorImageService(Settings(), Sources(), object(), object_detector=Detector())
        started = asyncio.get_running_loop().time()
        result = await service.fetch(CreatorFetchRequest(platform=Platform.DOUYIN, creator_id="Gracebb0722", max_images=1,
            content_query="只要人物", filter_mode="optional", download=False, output_dir=str(tmp_path / "out"), resume=False))
        assert asyncio.get_running_loop().time() - started < 0.5
        assert result["items"]
        assert result["object_detection_applied"] is False
        assert "time budget" in result["filter_warning"]

    asyncio.run(run())


def test_creator_resume_keeps_cumulative_counts_when_pending_images_remain(tmp_path):
    class Settings:
        cache_path = str(tmp_path / "cache.sqlite3")
        output_dir = str(tmp_path / "out")
        creator_timeout_seconds = 5

        def ensure_output_dir(self, value=None):
            from pathlib import Path
            path = Path(value or self.output_dir)
            path.mkdir(parents=True, exist_ok=True)
            return path

    candidate = ImageCandidate(
        id="p1:1", platform=Platform.DOUYIN, image_url="https://img.test/1.jpg",
        creator_id="sec-1", post_id="p1", media_index=1,
    )

    class Sources:
        calls = 0

        async def fetch_creator(self, request):
            self.calls += 1
            return CreatorSourceResult(
                identity=_identity(), items=[candidate], posts_fetched=1,
                post_ids=("p1",), pages_fetched=1, next_cursor='{"native":"2","offset":0}',
            )

    class Downloader:
        async def download_many(self, *args, **kwargs):
            raise AssertionError("download should not run when download=false")

    async def run():
        sources = Sources()
        service = CreatorImageService(Settings(), sources, Downloader())
        request = CreatorFetchRequest(
            platform=Platform.DOUYIN, creator_id="Gracebb0722", max_images=1,
            download=False, output_dir=str(tmp_path / "out"), resume=True,
        )
        first = await service.fetch(request)
        second = await service.fetch(request)
        assert first["posts_fetched"] == 1
        assert second["posts_fetched"] == 1
        assert second["post_ids"] == ["p1"]
        assert second["pages_fetched"] == 1
        assert sources.calls == 1

    asyncio.run(run())
