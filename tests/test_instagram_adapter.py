import asyncio

import httpx

from social_image_mcp.adapters.platforms import InstagramAdapter
from social_image_mcp.intent import parse_intent


def test_instagram_direct_media_normalizes_graph_response():
    def handler(request):
        assert request.url.params["access_token"] == "token"
        return httpx.Response(200, json={"id": "m1", "caption": "coffee shop", "media_url": "https://cdn.test/ig.jpg", "permalink": "https://instagram.com/p/m1/", "username": "creator"}, request=request)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = InstagramAdapter(client, "token", None)
            items = await adapter.search(parse_intent("instagram:m1"), 10, True)
            assert len(items) == 1
            assert items[0].id == "m1"
            assert items[0].author == "creator"

    asyncio.run(run())
