from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify CLIP warmup and scoring through MCP stdio")
    parser.add_argument("--query", default="douyin:7639200056281136754")
    parser.add_argument("--platform", default="douyin")
    parser.add_argument("--warmup-seconds", type=float, default=45)
    return parser


async def main(args: argparse.Namespace) -> int:
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "scripts" / "run_mcp.py")],
        env=env,
        encoding="utf-8",
        encoding_error_handler="replace",
    )
    reports: list[dict] = []
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for label, wait_before in (("cold", 0.0), ("warm", args.warmup_seconds)):
                if wait_before:
                    await asyncio.sleep(wait_before)
                started = time.perf_counter()
                result = await session.call_tool(
                    "search_images",
                    {
                        "query": args.query,
                        "platforms": [args.platform],
                        "max_results": 1,
                        "use_cache": False,
                        "retrieval_mode": "sources",
                    },
                    read_timeout_seconds=timedelta(seconds=70),
                )
                payload = json.loads(result.content[0].text)
                item = (payload.get("items") or [{}])[0]
                reports.append(
                    {
                        "request": label,
                        "elapsed_seconds": round(time.perf_counter() - started, 2),
                        "item_count": len(payload.get("items", [])),
                        "item_id": item.get("id"),
                        "platform_error": (payload.get("platforms", {}).get(args.platform) or {}).get("error"),
                        "vision": (payload.get("retrieval") or {}).get("vision"),
                        "vision_similarity": (item.get("source_payload") or {}).get("vision_similarity"),
                    }
                )
    cold, warm = reports
    reasons: list[str] = []
    if cold["item_count"] <= 0 or cold["platform_error"]:
        reasons.append("cold request did not return a valid platform candidate")
    if cold["elapsed_seconds"] >= 20:
        reasons.append("cold request exceeded the 20-second acceptance limit")
    if warm["item_count"] <= 0 or warm["platform_error"]:
        reasons.append("warm request did not return a valid platform candidate")
    if warm["vision_similarity"] is None:
        reasons.append("warm request has no vision_similarity")
    report = {"ok": not reasons, "requests": reports}
    if reasons:
        report["failure_reasons"] = reasons
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not reasons else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(_parser().parse_args())))
