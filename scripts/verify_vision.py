from __future__ import annotations

import asyncio
import io
import json

import httpx
from PIL import Image

from social_image_mcp.intent import parse_intent
from social_image_mcp.models import ImageCandidate, Platform
from social_image_mcp.vision import VisionReranker


async def main() -> None:
    image = io.BytesIO()
    Image.new("RGB", (64, 64), "red").save(image, format="PNG")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=image.getvalue(), request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        reranker = VisionReranker("openai/clip-vit-base-patch32", client)
        try:
            result = await reranker.rerank(
                [ImageCandidate(id="1", platform=Platform.X, image_url="https://cdn.test/a.png", title="coffee")],
                parse_intent("coffee shop interior"),
                1,
            )
            print(json.dumps({"status": reranker.status, "score": result[0].score, "vision_similarity": result[0].source_payload.get("vision_similarity")}, ensure_ascii=False))
        finally:
            await reranker.close()


if __name__ == "__main__":
    asyncio.run(main())
