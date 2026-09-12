import asyncio

import httpx

from social_image_mcp.bilibili import BilibiliApi
from social_image_mcp.intent import parse_intent
from social_image_mcp.models import CreatorFetchRequest, Platform


def _client(routes):
    def handler(request):
        for prefix, payload in routes.items():
            if request.url.path == prefix:
                return httpx.Response(200, json=payload)
        return httpx.Response(404, json={"code": -404, "message": "missing"})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_bilibili_search_maps_cover_and_identifier():
    async def run():
        client = _client({"/x/web-interface/search/type": {"code": 0, "data": {"result": [{"bvid": "BV1abc", "pic": "//img.test/a.jpg", "title": "咖啡店"}]}}})
        async with client:
            items = await BilibiliApi(client).search(parse_intent("咖啡店"), 3)
        assert items[0].platform == Platform.BILIBILI
        assert items[0].image_url == "https://img.test/a.jpg"
        assert items[0].post_id == "BV1abc"
    asyncio.run(run())


def test_weibo_creator_request_accepts_numeric_uid():
    request = CreatorFetchRequest(platform=Platform.WEIBO, creator_id="123456789", download=False)
    assert request.creator_id == "123456789"


def test_bilibili_profile_url_is_parsed_as_creator():
    intent = parse_intent("https://space.bilibili.com/2")
    assert intent.identifier_platform == "bilibili"
    assert intent.identifier_scope == "creator"
    assert intent.identifier == "2"
