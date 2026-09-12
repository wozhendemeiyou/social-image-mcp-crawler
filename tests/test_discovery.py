import asyncio

import httpx

from social_image_mcp.discovery import BingImageDiscovery
from social_image_mcp.intent import parse_intent
from social_image_mcp.models import Platform


def test_bing_discovery_parses_public_image_index_metadata():
    html = '<a class="iusc" m=\'{"purl":"https://www.xiaohongshu.com/explore/n1","murl":"https://cdn.test/a.jpg","turl":"https://cdn.test/t.jpg","t":"coffee shop","desc":"minimal interior","w":1600,"h":900}\'></a>'

    def handler(request):
        return httpx.Response(200, text=html, request=request)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            discovery = BingImageDiscovery(client)
            items = await discovery.search(Platform.XHS, parse_intent("咖啡店 横图"), 5)
            assert len(items) == 1
            assert items[0].permalink.endswith("n1")
            assert items[0].source_payload["provider"] == "bing-images"

    asyncio.run(run())


def test_bing_discovery_rejects_non_platform_source_pages():
    html = '<a class="iusc" m=\'{"purl":"https://example.com/post/1","murl":"https://cdn.test/a.jpg","t":"coffee"}\'></a>'

    def handler(request):
        return httpx.Response(200, text=html, request=request)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            discovery = BingImageDiscovery(client)
            items = await discovery.search(Platform.XHS, parse_intent("咖啡店"), 5)
            assert items == []

    asyncio.run(run())
