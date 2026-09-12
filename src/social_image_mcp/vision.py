from __future__ import annotations

import asyncio
import base64
import io
import json
import sys
import time
from collections import OrderedDict
from collections.abc import Sequence
from pathlib import Path

import httpx
from PIL import Image

from .intent import Intent, visual_contrast_prompts
from .models import ImageCandidate


class VisionReranker:
    """CLIP reranker isolated behind a persistent subprocess JSON protocol."""

    def __init__(
        self,
        model_name: str | None,
        client: httpx.AsyncClient,
        max_image_bytes: int = 8_000_000,
        local_files_only: bool = True,
        image_timeout_seconds: int = 8,
    ) -> None:
        self.model_name = model_name
        self.client = client
        self.max_image_bytes = max_image_bytes
        self.local_files_only = local_files_only
        self.image_timeout_seconds = max(1, image_timeout_seconds)
        self._ready = False
        self._load_error: str | None = None
        self._load_task: asyncio.Task[bool] | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._request_lock = asyncio.Lock()
        self._image_cache: OrderedDict[str, tuple[float, Image.Image]] = OrderedDict()
        self._image_cache_limit = 64
        self._image_fetch_sem = asyncio.Semaphore(8)
        self._next_request_id = 1

    @property
    def enabled(self) -> bool:
        return bool(self.model_name)

    @property
    def status(self) -> dict[str, str | bool | None]:
        if not self.model_name:
            return {"enabled": False, "loading": False, "model": None, "error": None}
        return {
            "enabled": self._ready,
            "loading": bool(self._load_task and not self._load_task.done()),
            "model": self.model_name,
            "error": self._load_error,
        }

    def start_loading(self) -> None:
        """Warm CLIP outside the MCP process without blocking stdio."""
        if self.enabled and self._load_task is None:
            self._load_task = asyncio.create_task(self._start_worker())

    async def _start_worker(self) -> bool:
        try:
            worker = Path(__file__).resolve().parents[2] / "scripts" / "vision_worker.py"
            self._process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(worker),
                "--model",
                self.model_name,
                "--local-only",
                "true" if self.local_files_only else "false",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=2**22,
            )
            line = await self._process.stdout.readline()
            if not line:
                error = await self._process.stderr.read()
                raise RuntimeError(error.decode("utf-8", errors="replace") or "visual worker exited during startup")
            message = json.loads(line)
            if not message.get("ready"):
                raise RuntimeError(str(message.get("error") or "visual worker failed to load"))
            self._ready = True
            self._load_error = None
            return True
        except Exception as exc:
            self._ready = False
            self._load_error = str(exc)
            return False

    async def close(self) -> None:
        load_task = self._load_task
        self._load_task = None
        if load_task and not load_task.done():
            load_task.cancel()
            try:
                await load_task
            except asyncio.CancelledError:
                pass
        process = self._process
        self._process = None
        self._ready = False
        self._image_cache.clear()
        if process and process.returncode is None:
            if process.stdin:
                process.stdin.close()
                try:
                    await process.stdin.wait_closed()
                except (BrokenPipeError, ConnectionResetError):
                    pass
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()

    def mark_timeout(self, timeout_seconds: int) -> None:
        self._load_error = f"visual reranking exceeded {timeout_seconds}s; returned source ranking"

    async def _get_image(self, item: ImageCandidate) -> Image.Image | None:
        # Videos can be large; never download the MP4 just to discover that it
        # is not an image. Their cover is the only suitable visual input.
        if item.media_type == "video":
            urls = [item.thumbnail_url] if item.thumbnail_url else []
        else:
            urls = [item.thumbnail_url, item.image_url] if item.thumbnail_url else [item.image_url]
        for url in urls:
            if not url:
                continue
            cached = self._image_cache.get(url)
            if cached and time.monotonic() - cached[0] < 300:
                self._image_cache.move_to_end(url)
                return cached[1]
            try:
                async with self._image_fetch_sem:
                    response = await self.client.get(
                        url,
                        timeout=httpx.Timeout(float(self.image_timeout_seconds), connect=3.0),
                        follow_redirects=True,
                    )
                response.raise_for_status()
                if len(response.content) > self.max_image_bytes:
                    continue
                with Image.open(io.BytesIO(response.content)) as image:
                    converted = image.convert("RGB")
                self._image_cache[url] = (time.monotonic(), converted)
                self._image_cache.move_to_end(url)
                while len(self._image_cache) > self._image_cache_limit:
                    self._image_cache.popitem(last=False)
                return converted
            except Exception:
                continue
        return None

    @staticmethod
    def _encode_image(image: Image.Image) -> str:
        prepared = image.copy()
        prepared.thumbnail((512, 512))
        payload = io.BytesIO()
        prepared.save(payload, format="JPEG", quality=90)
        return base64.b64encode(payload.getvalue()).decode("ascii")

    async def _score_images(self, prompts: list[str], images: list[Image.Image]) -> list[list[float]]:
        process = self._process
        if not process or process.returncode is not None or not process.stdin or not process.stdout:
            raise RuntimeError("visual worker is not available")
        async with self._request_lock:
            request_id = self._next_request_id
            self._next_request_id += 1
            message = {
                "id": request_id,
                "prompts": prompts,
                "images": [self._encode_image(image) for image in images],
            }
            process.stdin.write((json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8"))
            await process.stdin.drain()
            line = await process.stdout.readline()
            if not line:
                error = await process.stderr.read()
                raise RuntimeError(error.decode("utf-8", errors="replace") or "visual worker stopped")
            response = json.loads(line)
            if response.get("id") != request_id:
                raise RuntimeError("visual worker response ID mismatch")
            if response.get("error"):
                raise RuntimeError(str(response["error"]))
            return [
                [float(value) for value in row]
                for row in response.get("scores", [])
            ]

    async def rerank(
        self,
        items: Sequence[ImageCandidate],
        intent: Intent,
        max_results: int,
    ) -> list[ImageCandidate]:
        if not items or not self.enabled:
            return list(items)[:max_results]

        self.start_loading()
        try:
            loaded = await asyncio.shield(self._load_task)
        except asyncio.CancelledError:
            raise
        if not loaded:
            return list(items)[:max_results]
        images = await asyncio.gather(*(self._get_image(item) for item in items))
        valid = [(item, image) for item, image in zip(items, images) if image is not None]
        if not valid:
            return list(items)[:max_results]
        try:
            positive_prompts, negative_prompts = visual_contrast_prompts(intent)
            score_matrix = await self._score_images(
                positive_prompts + negative_prompts,
                [image for _, image in valid],
            )
            visual_scores: list[tuple[float, float | None, float]] = []
            positive_count = len(positive_prompts)
            for row in score_matrix:
                positive = max(row[:positive_count])
                negative_values = row[positive_count:]
                negative = max(negative_values) if negative_values else None
                margin = positive - negative if negative is not None else 0.0
                visual_scores.append((positive, negative, margin))
            valid_ids = {id(item) for item, _ in valid}
            scored = [
                item.model_copy(
                    update={
                        "score": round(
                            item.score * (0.25 if negative is not None else 0.35)
                            + positive * (2.5 if negative is not None else 3.0)
                            + margin * (5.0 if negative is not None else 0.0),
                            4,
                        ),
                        "source_payload": {
                            **item.source_payload,
                            "vision_similarity": positive,
                            "vision_negative_similarity": negative,
                            "vision_margin": margin,
                        },
                    }
                )
                for (item, _), (positive, negative, margin) in zip(valid, visual_scores)
            ]
            scored.extend(item for item in items if id(item) not in valid_ids)
            scored.sort(key=lambda item: item.score, reverse=True)
            self._load_error = None
            return scored[:max_results]
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._load_error = str(exc)
            return list(items)[:max_results]

    async def classify_objects(
        self,
        items: Sequence[ImageCandidate],
        labels: Sequence[str],
        region: str = "full_image",
    ) -> dict[str, dict[str, float | list[str]]]:
        """Classify coarse visual objects with the same local CLIP worker."""
        if not items or not labels or not self.enabled:
            return {}
        self.start_loading()
        loaded = await asyncio.shield(self._load_task)
        if not loaded:
            raise RuntimeError(self._load_error or "visual worker is not available")
        images = await asyncio.gather(*(self._get_image(item) for item in items))
        valid = [(item, image) for item, image in zip(items, images) if image is not None]
        if not valid:
            raise RuntimeError("object classification images are unavailable")
        descriptions = {
            "person": "a photograph with a person as the main subject",
            "clothing": "a photograph where clothing or an outfit is clearly visible",
            "face": "a close-up photograph of a human face",
            "upper_body": "a photograph focused on a person's upper body or top",
            "lower_body": "a photograph focused on legs, pants, skirt, or lower body",
            "feet": "a photograph focused on shoes or feet",
            "bag": "a photograph showing a bag or handbag",
            "food": "a photograph showing food or a meal",
            "drink": "a photograph showing a drink or beverage",
            "landscape": "an outdoor landscape, mountain, sea, sky, or scenic view",
            "architecture": "a photograph dominated by a building or architecture",
            "street": "a street or city street scene",
            "scene": "an empty scene or background without a clear main subject",
            "vehicle": "a photograph dominated by a vehicle",
        }
        requested = [label for label in dict.fromkeys(labels) if label in descriptions]
        region_hint = {
            "face": ", with the face occupying the requested focus",
            "upper_body": ", with the upper body occupying the requested focus",
            "lower_body": ", with the lower body or legs occupying the requested focus",
            "feet": ", with shoes or feet occupying the requested focus",
        }.get(region, "")
        if region_hint:
            descriptions = {label: text + region_hint for label, text in descriptions.items()}
        prompts = [descriptions[label] for label in requested] + ["a photograph that is none of these categories"]
        matrix = await self._score_images(prompts, [image for _, image in valid])
        results: dict[str, dict[str, float | list[str]]] = {}
        for (item, _), row in zip(valid, matrix):
            if not row:
                continue
            other = float(row[-1])
            scores = {label: float(value) for label, value in zip(requested, row[:-1])}
            detected = [label for label, value in scores.items() if value >= 0.18 and value >= other + 0.015]
            results[item.id] = {"detected_objects": detected, "object_scores": scores}
        return results
