#!/usr/bin/env python3
"""调试脚本：验证 arena.ai 的 Code 分类下是否真的存在 code/webdev 与
code/image-to-webdev 两个独立子榜（而不是单一的 code 页面）。
只落盘原始 markdown，不解析，跑完人工核对后删除。"""

import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

JINA_READER_BASE = "https://r.jina.ai/"
ARENA_BASE = "https://arena.ai/leaderboard/"


def fetch_page(url, jina_api_key=None):
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
            print(f"  attempt {attempt+1} failed: {e}", file=sys.stderr)
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"failed to fetch {url}")


def main():
    jina_key = os.environ.get("JINA_API_KEY")
    repo_root = Path(__file__).resolve().parent.parent
    out_dir = repo_root / "debug"
    out_dir.mkdir(exist_ok=True)

    targets = ["code", "code/webdev", "code/image-to-webdev"]
    for slug in targets:
        url = f"{ARENA_BASE}{slug}"
        print(f"fetching {url} ...", file=sys.stderr)
        try:
            text = fetch_page(url, jina_key)
        except Exception as e:
            text = f"ERROR: {e}"
        fname = slug.replace("/", "__") + ".raw.md"
        fp = out_dir / fname
        fp.write_text(text, encoding="utf-8")
        print(f"  wrote {fp} ({len(text)} chars)", file=sys.stderr)
        time.sleep(2)


if __name__ == "__main__":
    main()
