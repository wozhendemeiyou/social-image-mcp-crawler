import asyncio

from social_image_mcp.intent import parse_intent
from social_image_mcp.models import ImageCandidate, Platform
from social_image_mcp.semantic import SemanticReranker


def test_semantic_reranker_fast_fallback_without_model():
    async def run():
        items = [ImageCandidate(id="1", platform=Platform.X, image_url="https://cdn/1.jpg", title="咖啡店")]
        reranker = SemanticReranker()
        result = await reranker.rerank(items, parse_intent("咖啡店"), 1)
        assert result[0].id == "1"
        assert reranker.status["enabled"] is False

    asyncio.run(run())
