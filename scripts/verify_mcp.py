from __future__ import annotations

import asyncio
import argparse
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
    parser = argparse.ArgumentParser(description="Verify the current social-image MCP through stdio")
    parser.add_argument("--query", default="", help="Run a real image search after tool discovery")
    parser.add_argument("--platform", default="douyin")
    parser.add_argument("--max-results", type=int, default=4)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--output-dir", default="downloads/verify-mcp")
    return parser


async def main(args: argparse.Namespace) -> int:
    exit_code = 0
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "scripts" / "run_mcp.py")],
        env=env,
        encoding="utf-8",
        encoding_error_handler="replace",
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            report = {"tools": [tool.name for tool in tools.tools]}
            if args.query:
                started = time.perf_counter()
                result = await session.call_tool(
                    "search_images",
                    {
                        "query": args.query,
                        "platforms": [args.platform],
                        "max_results": args.max_results,
                        "use_cache": False,
                        "download": args.download,
                        "output_dir": args.output_dir,
                        "retrieval_mode": "sources",
                    },
                    read_timeout_seconds=timedelta(seconds=90),
                )
                payload = json.loads(result.content[0].text)
                report.update({
                    "elapsed_seconds": round(time.perf_counter() - started, 2),
                    "item_count": len(payload.get("items", [])),
                    "ids": [item.get("id") for item in payload.get("items", [])],
                    "ranking": [
                        {
                            "id": item.get("id"),
                            "score": item.get("score"),
                            "vision_similarity": (item.get("source_payload") or {}).get("vision_similarity"),
                            "vision_negative_similarity": (item.get("source_payload") or {}).get("vision_negative_similarity"),
                            "vision_margin": (item.get("source_payload") or {}).get("vision_margin"),
                        }
                        for item in payload.get("items", [])
                    ],
                    "platform": payload.get("platforms", {}).get(args.platform, {}),
                    "downloads": [
                        {key: item.get(key) for key in ("candidate_id", "path", "width", "height", "status", "error")}
                        for item in payload.get("downloads", [])
                    ],
                })
                platform = report["platform"]
                reasons: list[str] = []
                if report["item_count"] <= 0:
                    reasons.append("item_count=0")
                if platform.get("error"):
                    reasons.append(f"platform_error={platform['error'].get('code', 'unknown')}")
                if args.download:
                    downloaded = [item for item in report["downloads"] if item.get("status") == "downloaded"]
                    if not downloaded:
                        reasons.append("no_downloaded_record")
                report["ok"] = not reasons
                if reasons:
                    report["failure_reasons"] = reasons
                    exit_code = 2
            print(json.dumps(report, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(_parser().parse_args())))
