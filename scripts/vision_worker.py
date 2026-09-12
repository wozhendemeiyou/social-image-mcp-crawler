from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
from pathlib import Path

from PIL import Image


def _cached_assets(model_name: str) -> tuple[Path, Path, Path] | None:
    if Path(model_name).exists() or "/" not in model_name:
        return None
    cache_root = Path(os.getenv("HF_HUB_CACHE", "~/.cache/huggingface/hub")).expanduser()
    snapshots = cache_root / ("models--" + model_name.replace("/", "--")) / "snapshots"
    if not snapshots.is_dir():
        return None
    processor_dir = next((item for item in snapshots.iterdir() if (item / "config.json").is_file()), None)
    model_dir = next((item for item in snapshots.iterdir() if (item / "model.safetensors").is_file()), None)
    if not processor_dir or not model_dir:
        return None
    return processor_dir, model_dir, processor_dir


def _load(model_name: str, local_only: bool):
    if os.name == "nt":
        import ctypes

        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000)
    os.environ.setdefault("TRANSFORMERS_NO_TF", "1")
    os.environ.setdefault("USE_TF", "0")
    import torch
    from transformers import CLIPConfig, CLIPModel, CLIPProcessor

    torch.set_num_threads(max(1, min(2, os.cpu_count() or 1)))
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        pass
    processor_source: str | Path = model_name
    model_source: str | Path = model_name
    config = None
    cached = _cached_assets(model_name)
    if cached:
        processor_source, model_source, config_source = cached
        config = CLIPConfig.from_pretrained(str(config_source), local_files_only=True)
    processor = CLIPProcessor.from_pretrained(
        str(processor_source), use_fast=False, local_files_only=local_only
    )
    model = CLIPModel.from_pretrained(
        str(model_source), config=config, use_safetensors=True, local_files_only=local_only
    )
    model.eval()
    return torch, processor, model


def _score(torch, processor, model, prompts: list[str], encoded_images: list[str]) -> list[list[float]]:
    images = []
    for value in encoded_images:
        with Image.open(io.BytesIO(base64.b64decode(value))) as image:
            images.append(image.convert("RGB"))
    inputs = processor(text=prompts, images=images, return_tensors="pt", padding=True)
    with torch.no_grad():
        text_features = model.get_text_features(
            input_ids=inputs["input_ids"], attention_mask=inputs.get("attention_mask")
        )
        image_features = model.get_image_features(pixel_values=inputs["pixel_values"])
        text_features = text_features / text_features.norm(dim=-1, keepdim=True)
        image_features = image_features / image_features.norm(dim=-1, keepdim=True)
        # Return one row per image and one column per prompt. The parent uses
        # this matrix for contrastive intent scoring instead of averaging away
        # the difference between a requested subject and common distractors.
        return (image_features @ text_features.T).tolist()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--local-only", default="true")
    args = parser.parse_args()
    try:
        torch, processor, model = _load(args.model, args.local_only.lower() == "true")
        print(json.dumps({"ready": True, "model": args.model}), flush=True)
    except Exception as exc:
        print(json.dumps({"ready": False, "error": str(exc)}), flush=True)
        return 1
    for line in sys.stdin:
        try:
            request = json.loads(line)
            scores = _score(torch, processor, model, request["prompts"], request["images"])
            response = {"id": request.get("id"), "scores": scores}
        except Exception as exc:
            response = {"id": request.get("id") if "request" in locals() else None, "error": str(exc)}
        print(json.dumps(response, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
