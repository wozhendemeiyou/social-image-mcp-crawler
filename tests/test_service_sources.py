import asyncio

from social_image_mcp.config import Settings
from social_image_mcp.models import ImageCandidate, Platform, SearchRequest
from social_image_mcp.service import SocialImageService


class FakeSources:
    def statuses(self):
        return [{"name": "fake-source", "configured": True, "mode": "test", "detail": "", "platforms": ["xhs"]}]

    async def search(self, platform, intent, limit):
        return [ImageCandidate(id="n1", platform=platform, image_url="https://cdn.test/n1.jpg", title="极简咖啡店室内", width=1600, height=900)]


class FailingSources(FakeSources):
    async def search(self, platform, intent, limit):
        raise RuntimeError("source unavailable")


def test_service_uses_recommended_source_before_download(tmp_path):
    async def run():
        service = SocialImageService(Settings(cache_path=str(tmp_path / "cache.sqlite3"), vision_model=None))
        await service.start()
        service.sources = FakeSources()
        result = await service.search(SearchRequest(query="咖啡店 横图", platforms=[Platform.XHS], use_cache=False, retrieval_mode="sources"))
        assert result["items"][0]["id"] == "n1"
        assert result["retrieval"]["mode"] == "sources"
        assert result["retrieval"]["sources"][0]["name"] == "fake-source"
        assert result["platforms"]["xhs"]["status"]["configured"] is True
        assert result["platforms"]["xhs"]["status"]["mode"] == "source-project"
        await service.close()

    asyncio.run(run())


def test_hybrid_keeps_discovery_candidates_when_source_fails(tmp_path):
    async def run():
        service = SocialImageService(Settings(cache_path=str(tmp_path / "cache.sqlite3"), vision_model=None))
        await service.start()
        service.sources = FailingSources()
        service.discovery.search = lambda platform, intent, limit: _discovery_candidate(platform)
        result = await service.search(SearchRequest(query="咖啡店", platforms=[Platform.XHS], use_cache=False, retrieval_mode="hybrid"))
        assert result["items"][0]["id"] == "discovery"
        assert result["platforms"]["xhs"]["error"]["code"] == "partial_error"
        await service.close()

    asyncio.run(run())


async def _discovery_candidate(platform):
    return [ImageCandidate(id="discovery", platform=platform, image_url="https://cdn.test/discovery.jpg", title="咖啡店")]
