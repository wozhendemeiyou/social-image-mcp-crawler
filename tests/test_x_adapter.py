import asyncio

import httpx

from social_image_mcp.adapters.platforms import XAdapter
from social_image_mcp.intent import parse_intent


def test_x_direct_id_normalizes_single_post_response():
    def handler(request):
        assert request.url.params["post.fields"]
        return httpx.Response(
            200,
            json={
                "data": {"id": "123", "text": "coffee shop", "attachments": {"media_keys": ["m1"]}},
                "includes": {"media": [{"media_key": "m1", "type": "photo", "url": "https://cdn.test/a.jpg", "width": 1200, "height": 800}]},
            },
            request=request,
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            items = await XAdapter(client, "token").search(parse_intent("x:123"), 10, True)
            assert len(items) == 1
            assert items[0].id == "123"
            assert items[0].width == 1200

    asyncio.run(run())
