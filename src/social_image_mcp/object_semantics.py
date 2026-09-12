from __future__ import annotations

"""Structured object-level content requirements for creator filtering.

This module deliberately contains no model code. It turns natural-language
briefs into a stable contract consumed by YOLO/pose/CLIP backends, so model
availability never changes how the user's request is interpreted.
"""

from dataclasses import dataclass

from .intent import parse_intent


OBJECT_ALIASES: dict[str, set[str]] = {
    "person": {"人物", "人像", "人", "person", "people", "portrait", "lifestyle", "生活"},
    "clothing": {"穿搭", "服装", "衣服", "outfit", "fashion", "clothing", "street style"},
    "face": {"脸", "脸部", "面部", "face", "head"},
    "upper_body": {"上身", "上衣", "upper body", "top", "shirt", "jacket"},
    "lower_body": {"下身", "腿", "腿部", "下装", "lower body", "legs", "pants", "skirt"},
    "feet": {"脚", "脚部", "鞋", "鞋子", "feet", "shoes", "footwear"},
    "bag": {"包", "包包", "bag", "handbag"},
    "food": {"食物", "美食", "food", "dish", "meal"},
    "drink": {"饮品", "饮料", "咖啡", "drink", "beverage", "coffee"},
    "landscape": {"风景", "山水", "自然", "landscape", "scenery", "nature"},
    "architecture": {"建筑", "房屋", "楼", "建筑物", "architecture", "building", "exterior"},
    "street": {"街景", "城市", "街道", "street", "street view", "city scene"},
    "scene": {"场景", "纯场景", "背景", "scene", "setting", "background", "empty scene"},
}


@dataclass(frozen=True)
class ContentSpec:
    raw: str
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    required: tuple[str, ...]
    region: str = "full_image"
    min_required_area: float = 0.03
    min_excluded_area: float = 0.08

    @property
    def object_level(self) -> bool:
        return bool(self.include or self.exclude or self.required)


def _terms_to_objects(terms: tuple[str, ...] | list[str]) -> list[str]:
    result: list[str] = []
    for term in terms:
        for object_name, aliases in OBJECT_ALIASES.items():
            if term in aliases or any(alias in term for alias in aliases if len(alias) > 1):
                if object_name not in result:
                    result.append(object_name)
    return result


def parse_content_spec(query: str) -> ContentSpec:
    intent = parse_intent(query)
    include = _terms_to_objects(intent.tokens)
    exclude = _terms_to_objects(intent.negative_tokens)
    region = "full_image"
    if any(term in query for term in ("脸", "脸部", "面部", "face", "head")):
        region = "face"
    elif any(term in query for term in ("腿", "腿部", "下身", "下装", "lower body", "legs")):
        region = "lower_body"
    elif any(term in query for term in ("鞋", "脚", "feet", "footwear")):
        region = "feet"
    elif any(term in query for term in ("上身", "上衣", "upper body", "top")):
        region = "upper_body"
    # "只要人物穿搭" means a person is mandatory, while a generic "穿搭"
    # still benefits from person detection but need not fail on a flat-lay.
    required = ["person"] if "person" in include and any(term in query for term in ("只要", "必须", "人物", "人像", "生活")) else []
    if "clothing" in include and any(term in query for term in ("人物穿搭", "穿搭照", "穿搭图片")):
        if "person" not in required:
            required.append("person")
        required.append("clothing")
    return ContentSpec(query, tuple(include), tuple(exclude), tuple(required), region)


@dataclass(frozen=True)
class ObjectObservation:
    labels: tuple[str, ...]
    area_by_label: dict[str, float]

    def has(self, label: str) -> bool:
        return label in self.labels


def decide_observation(spec: ContentSpec, observation: ObjectObservation) -> tuple[bool, str | None]:
    """Apply deterministic include/exclude/required rules to model output."""
    missing = [label for label in spec.required if not observation.has(label) or observation.area_by_label.get(label, 0.0) < spec.min_required_area]
    if missing:
        return False, "required_object_missing:" + ",".join(missing)
    conflicts = [label for label in spec.exclude if observation.has(label) and observation.area_by_label.get(label, 0.0) >= spec.min_excluded_area]
    if conflicts:
        return False, "excluded_object_detected:" + ",".join(conflicts)
    if spec.include and not any(observation.has(label) and observation.area_by_label.get(label, 0.0) >= spec.min_required_area for label in spec.include):
        return False, "included_object_not_detected"
    return True, None
