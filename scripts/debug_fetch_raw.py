#!/usr/bin/env python3
"""
调试脚本：只把 Jina Reader 转换出的原始 markdown 文本落盘，
不做任何解析。用于人工核对 arena.ai 的 text/agent 榜单页面
经 Jina 转换后的真实格式（表格是否规整、有没有 price/context 列），
以便针对性编写确定性解析器，而不是盲写正则。
跑完后手动删除本脚本和 debug/ 目录，不进入正式抓取流程。
"""

import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

JINA_READER_BASE = "https://r.jina.ai/"
ARENA_BASE = "https://arena.ai/leaderboard/"


def fetch_page(url: str, jina_api_key: str | None = None) -> str:
    reader_url = f"{JINA_READER_BASE}{url}"
    headers = {
        "Accept": "application/json",
        "X-Return-Format": "markdown",
        "User-Agent": "Mozilla/5.0 (compatible; arena-leaderboard-bot/1.0)",
    }
    if jina_api_key:
        headers["Authorization"] = f"Bearer {jina_api_key}"

    req = urllib.request.Request(reader_url, headers=headers)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read().decode("utf-8")
        except urllib.error.URLError as e:
            print(f"  Attempt {attempt+1} failed: {e}", file=sys.stderr)
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"Failed to fetch {url} after 3 attempts")


def main():
    jina_key = os.environ.get("JINA_API_KEY")
    repo_root = Path(__file__).resolve().parent.parent
    out_dir = repo_root / "debug"
    out_dir.mkdir(exist_ok=True)

    # 抓全部分类，核对每个分类页面经 Jina 转换后的表头/列结构是否一致
    targets = [
        "text", "agent", "code", "vision", "document", "search",
        "text-to-image", "image-edit", "text-to-video", "image-to-video", "video-edit",
    ]
    for slug in targets:
        url = f"{ARENA_BASE}{slug}"
        print(f"Fetching {url} ...", file=sys.stderr)
        try:
            text = fetch_page(url, jina_key)
        except Exception as e:
            text = f"ERROR: {e}"
        fp = out_dir / f"{slug}.raw.md"
        fp.write_text(text, encoding="utf-8")
        print(f"  wrote {fp} ({len(text)} chars)", file=sys.stderr)
        time.sleep(2)


if __name__ == "__main__":
    main()
