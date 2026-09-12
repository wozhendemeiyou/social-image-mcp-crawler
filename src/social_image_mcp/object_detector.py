from __future__ import annotations

import asyncio
import io
import json
from typing import Any

import httpx
from PIL import Image

from .object_semantics import ContentSpec, ObjectObservation, decide_observation
from .models import ImageCandidate


# COCO labels exposed by the default YOLO detector. Clothing/body regions are
# intentionally handled by the semantic model unless a custom detector maps
# them to these stable labels.
COCO_TO_OBJECT = {
    "person": "person", "tie": "clothing", "handbag": "bag", "backpack": "bag",
    "suitcase": "bag", "bottle": "drink", "cup": "drink", "bowl": "food",
    "cake": "food", "pizza": "food", "sandwich": "food", "hot dog": "food",
    "car": "vehicle", "bus": "vehicle", "truck": "vehicle", "motorcycle": "vehicle",
    "bicycle": "vehicle", "airplane": "vehicle", "boat": "vehicle", "train": "vehicle",
    "bench": "scene", "chair": "scene", "couch": "scene", "bed": "scene",
    "dining table": "scene", "tv": "scene", "potted plant": "scene", "vase": "scene",
}


class ObjectDetector:
    """Coarse object gate with optional local detector or CLIP backend.

    `model_name` must point to a local weight file or a model already approved
    by the deployment. No network download is attempted by this class.
    """

    def __init__(self, model_name: str | None, client: httpx.AsyncClient, confidence: float = 0.35, label_map: str | None = None, vision: Any = None) -> None:
        self.model_name = model_name
        self.client = client
        self.confidence = confidence
        self.label_map = self._parse_label_map(label_map)
        self.vision = vision
        self._model: Any = None
        self._error: str | None = None
        self._lock = asyncio.Lock()

    @staticmethod
    def _parse_label_map(value: str | None) -> dict[str, str]:
        if not value:
            return dict(COCO_TO_OBJECT)
        try:
            parsed = json.loads(value)
            if not isinstance(parsed, dict):
                raise ValueError("OBJECT_LABEL_MAP must be a JSON object")
            return {str(key).lower(): str(val).lower() for key, val in parsed.items()}
        except Exception:
            return dict(COCO_TO_OBJECT)

    @property
    def configured(self) -> bool:
        return bool(self.model_name) or bool(self.vision and self.vision.enabled)

    @property
    def ready(self) -> bool:
        return self._model is not None

    @property
    def status(self) -> dict[str, Any]:
        backend = "yolo" if self.model_name else ("clip" if self.vision and self.vision.enabled else None)
        return {"configured": self.configured, "ready": self.ready or bool(self.vision and self.vision.status.get("enabled")), "backend": backend, "model": self.model_name or (self.vision.model_name if self.vision else None), "label_map_size": len(self.label_map), "error": self._error}

    async def _inspect_with_vision(self, items: list[ImageCandidate], spec: ContentSpec, fail_closed: bool) -> tuple[list[ImageCandidate], dict[str, Any]]:
        labels = tuple(dict.fromkeys((*spec.include, *spec.exclude, *spec.required)))
        try:
            classified = await self.vision.classify_objects(items, labels, region=spec.region)
        except Exception as exc:
            return items, {"object_detection_applied": False, "object_detection_error": str(exc)}
        accepted: list[ImageCandidate] = []
        rejected = 0
        unverified = 0
        decisions: dict[str, Any] = {}
        for item in items:
            data = classified.get(item.id)
            if not data:
                unverified += 1
                decisions[item.id] = {"content_decision": "unverified", "rejection_reason": "visual_classification_unavailable"}
                if not fail_closed:
                    accepted.append(item)
                continue
            detected = [str(value) for value in data.get("detected_objects", [])]
            observation = ObjectObservation(tuple(detected), {label: 1.0 for label in detected})
            ok, reason = decide_observation(spec, observation)
            decisions[item.id] = {"detected_objects": detected, "object_scores": data.get("object_scores", {}), "content_decision": "accepted" if ok else "rejected", "rejection_reason": reason, "backend": "clip"}
            if ok:
                accepted.append(item)
            else:
                rejected += 1
        enriched = [item.model_copy(update={"source_payload": {**item.source_payload, "object_detection": decisions.get(item.id)}}) for item in accepted]
        return enriched, {"object_detection_applied": True, "object_detection_error": None, "object_decisions": decisions, "object_rejected": rejected, "object_unverified": unverified, "object_backend": "clip"}

    async def _load(self) -> Any:
        if self._model is not None:
            return self._model
        if not self.model_name:
            return None
        async with self._lock:
            if self._model is not None:
                return self._model
            try:
                from pathlib import Path
                path = Path(self.model_name).expanduser()
                if not path.is_file():
                    raise FileNotFoundError(f"object model file not found: {path}")
                from ultralytics import YOLO
                self._model = YOLO(str(path))
                self._error = None
            except Exception as exc:
                self._error = str(exc)
                self._model = None
        return self._model

    async def _image(self, item: ImageCandidate) -> Image.Image | None:
        try:
            response = await self.client.get(item.thumbnail_url or item.image_url, timeout=8, follow_redirects=True)
            response.raise_for_status()
            with Image.open(io.BytesIO(response.content)) as image:
                image.thumbnail((640, 640))
                return image.convert("RGB")
        except Exception:
            return None

    async def inspect(self, items: list[ImageCandidate], spec: ContentSpec, fail_closed: bool = False) -> tuple[list[ImageCandidate], dict[str, Any]]:
        model = await self._load()
        if model is None:
            if self.vision and self.vision.enabled:
                return await self._inspect_with_vision(items, spec, fail_closed)
            return items, {"object_detection_applied": False, "object_detection_error": self._error or "object model is not configured"}
        images = await asyncio.gather(*(self._image(item) for item in items))
        accepted: list[ImageCandidate] = []
        rejected = 0
        unverified = 0
        decisions: dict[str, Any] = {}
        for item, image in zip(items, images):
            if image is None:
                unverified += 1
                decisions[item.id] = {"content_decision": "unverified", "rejection_reason": "image_unavailable"}
                if not fail_closed:
                    accepted.append(item)
                continue
            try:
                result = await asyncio.to_thread(model.predict, image, conf=self.confidence, verbose=False)
                labels: list[str] = []
                areas: dict[str, float] = {}
                for prediction in result:
                    names = getattr(prediction, "names", {})
                    boxes = getattr(prediction, "boxes", None)
                    if boxes is None:
                        continue
                    for cls, xyxy, conf in zip(boxes.cls.tolist(), boxes.xyxy.tolist(), boxes.conf.tolist()):
                        if float(conf) < self.confidence:
                            continue
                        native = str(names.get(int(cls), ""))
                        label = self.label_map.get(native.lower())
                        if not label:
                            continue
                        if label not in labels:
                            labels.append(label)
                        x1, y1, x2, y2 = xyxy
                        area = max(0.0, (x2 - x1) * (y2 - y1) / max(image.width * image.height, 1))
                        areas[label] = max(areas.get(label, 0.0), area)
                observation = ObjectObservation(tuple(labels), areas)
                ok, reason = decide_observation(spec, observation)
                decisions[item.id] = {"detected_objects": labels, "object_area": areas, "content_decision": "accepted" if ok else "rejected", "rejection_reason": reason}
                if ok:
                    accepted.append(item)
                else:
                    rejected += 1
            except Exception as exc:
                decisions[item.id] = {"content_decision": "unverified", "rejection_reason": str(exc)}
                unverified += 1
                if not fail_closed:
                    accepted.append(item)
        enriched = [item.model_copy(update={"source_payload": {**item.source_payload, "object_detection": decisions.get(item.id)}}) for item in accepted]
        return enriched, {"object_detection_applied": True, "object_detection_error": None, "object_decisions": decisions, "object_rejected": rejected, "object_unverified": unverified}
