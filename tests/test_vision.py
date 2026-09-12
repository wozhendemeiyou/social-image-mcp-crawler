import asyncio
import io
import time

import httpx
from PIL import Image

from social_image_mcp.intent import parse_intent
from social_image_mcp.models import ImageCandidate, Platform
from social_image_mcp.vision import VisionReranker


def test_vision_reranker_returns_fast_fallback_when_disabled():
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), "red").save(buffer, format="PNG")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=buffer.getvalue(), request=request))) as client:
            reranker = VisionReranker(None, client)
            items = [ImageCandidate(id="1", platform=Platform.X, image_url="https://cdn.test/a.png", title="coffee")]
            result = await reranker.rerank(items, parse_intent("咖啡店"), 1)
            assert result[0].id == "1"
            assert reranker.status["enabled"] is False

    asyncio.run(run())


def test_vision_status_distinguishes_loading_from_ready(monkeypatch):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(404, request=request))) as client:
            reranker = VisionReranker("local-test", client)

            async def start_stub():
                await asyncio.sleep(0)
                reranker._ready = True
                return True

            monkeypatch.setattr(reranker, "_start_worker", start_stub)
            assert reranker.status["enabled"] is False
            assert reranker.status["loading"] is False
            reranker.start_loading()
            assert reranker.status["loading"] is True
            await reranker._load_task
            assert reranker.status["enabled"] is True
            assert reranker.status["loading"] is False
            assert reranker.status["error"] is None
            await reranker.close()

    asyncio.run(run())


def test_close_cancels_visual_worker_startup(monkeypatch):
    async def run():
        async with httpx.AsyncClient() as client:
            reranker = VisionReranker("local-test", client)

            async def hanging_start():
                await asyncio.sleep(30)
                return True

            monkeypatch.setattr(reranker, "_start_worker", hanging_start)
            reranker.start_loading()
            assert reranker.status["loading"] is True
            await reranker.close()
            assert reranker.status["loading"] is False
            assert reranker._load_task is None

    asyncio.run(run())


def test_cpu_inference_can_be_timed_out_without_blocking_event_loop(monkeypatch):
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), "red").save(buffer, format="PNG")

    async def run():
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, content=buffer.getvalue(), request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            reranker = VisionReranker("local-test", client)
            reranker._ready = True
            reranker._load_task = asyncio.create_task(asyncio.sleep(0, result=True))

            async def slow_score(prompts, images):
                await asyncio.to_thread(time.sleep, 0.25)
                return [0.5]

            monkeypatch.setattr(reranker, "_score_images", slow_score)
            item = ImageCandidate(id="1", platform=Platform.X, image_url="https://cdn.test/a.png")
            started = time.perf_counter()
            try:
                await asyncio.wait_for(
                    reranker.rerank([item], parse_intent("coffee"), 1),
                    timeout=0.03,
                )
                assert False, "slow inference should exceed the request budget"
            except asyncio.TimeoutError:
                pass
            assert time.perf_counter() - started < 0.15
            await reranker.close()

    asyncio.run(run())


def test_timeout_is_visible_even_after_model_is_ready():
    async def run():
        async with httpx.AsyncClient() as client:
            reranker = VisionReranker("local-test", client)
            reranker._ready = True
            reranker.mark_timeout(8)
            assert reranker.status["enabled"] is True
            assert reranker.status["error"] == "visual reranking exceeded 8s; returned source ranking"

    asyncio.run(run())


def test_contrastive_scoring_demotes_a_drink_closeup_for_interior_intent(monkeypatch):
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(buffer, format="PNG")

    async def run():
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, content=buffer.getvalue(), request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            reranker = VisionReranker("local-test", client)
            reranker._ready = True
            reranker._load_task = asyncio.create_task(asyncio.sleep(0, result=True))

            async def score_stub(prompts, images):
                # Columns: two positive interior prompts, then two distractors.
                return [
                    [0.26, 0.28, 0.18, 0.19],
                    [0.25, 0.27, 0.29, 0.23],
                ]

            monkeypatch.setattr(reranker, "_score_images", score_stub)
            items = [
                ImageCandidate(id="interior", platform=Platform.WEIBO, image_url="https://cdn.test/interior.png", score=1.0),
                ImageCandidate(id="drink", platform=Platform.WEIBO, image_url="https://cdn.test/drink.png", score=1.0),
            ]
            result = await reranker.rerank(items, parse_intent("咖啡店室内"), 2)

            assert [item.id for item in result] == ["interior", "drink"]
            assert result[0].source_payload["vision_margin"] > 0
            assert result[1].source_payload["vision_margin"] < 0

    asyncio.run(run())


def test_video_visual_input_uses_cover_without_fetching_mp4():
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), "green").save(buffer, format="PNG")
    requested = []

    def handler(request):
        requested.append(str(request.url))
        return httpx.Response(200, content=buffer.getvalue(), request=request)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            reranker = VisionReranker("local-test", client)
            item = ImageCandidate(id="v", platform=Platform.DOUYIN, image_url="https://cdn.test/video.mp4", thumbnail_url="https://cdn.test/cover.jpg", media_type="video")
            image = await reranker._get_image(item)
            assert image is not None
            assert requested == ["https://cdn.test/cover.jpg"]

    asyncio.run(run())
