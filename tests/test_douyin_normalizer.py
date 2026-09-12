import httpx

from social_image_mcp.adapters.platforms import DouyinSkillAdapter


def test_douyin_skill_normalizer_extracts_gallery_urls():
    adapter = DouyinSkillAdapter(httpx.AsyncClient(), "cookie", ".")
    rows = adapter._normalize({"aweme_id": "42", "desc": "咖啡店", "images": [{"url_list": ["https://cdn.test/1.jpg", "https://cdn.test/2.jpg"]}]}, 0)
    assert [row.image_url for row in rows] == ["https://cdn.test/1.jpg", "https://cdn.test/2.jpg"]
