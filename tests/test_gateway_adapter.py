import asyncio

import httpx

from social_image_mcp.adapters.platforms import ConfiguredJsonAdapter
from social_image_mcp.intent import parse_intent
from social_image_mcp.models import Platform


def test_configured_gateway_normalizes_nested_image_records():
    def handler(request):
        assert request.url.params["query"] == "咖啡店"
        return httpx.Response(200, json={"data": [{"note_id": "n1", "title": "咖啡店室内", "user": {"nickname": "a"}, "images": [{"url": "https://cdn.test/a.jpg", "width": 1400, "height": 900}]}]}, request=request)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = ConfiguredJsonAdapter(client, Platform.XHS, "https://gateway.test/search", None)
            items = await adapter.search(parse_intent("咖啡店"), 10, True)
            assert len(items) == 1
            assert items[0].id == "n1"
            assert items[0].title == "咖啡店室内"
            assert items[0].width == 1400

    asyncio.run(run())
