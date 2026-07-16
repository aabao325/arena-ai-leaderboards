#!/usr/bin/env python3
"""调试：只抓 agent 页面原始 markdown，用于人工核对表格里是否真的存在负值百分比
（排除是我们的解析器把负号丢掉的可能性）。跑完删除。"""
import os, sys, time, urllib.request, urllib.error
from pathlib import Path

JINA_READER_BASE = "https://r.jina.ai/"
ARENA_BASE = "https://arena.ai/leaderboard/"

def fetch_page(url, jina_api_key=None):
    reader_url = f"{JINA_READER_BASE}{url}"
    headers = {"Accept": "application/json", "X-Return-Format": "markdown",
               "User-Agent": "Mozilla/5.0 (compatible; arena-leaderboard-bot/1.0)"}
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
    raise RuntimeError("failed")

def main():
    jina_key = os.environ.get("JINA_API_KEY")
    repo_root = Path(__file__).resolve().parent.parent
    out_dir = repo_root / "debug"
    out_dir.mkdir(exist_ok=True)
    text = fetch_page(f"{ARENA_BASE}agent", jina_key)
    (out_dir / "agent-recheck.raw.md").write_text(text, encoding="utf-8")
    print("wrote agent-recheck.raw.md", len(text), file=sys.stderr)

if __name__ == "__main__":
    main()
