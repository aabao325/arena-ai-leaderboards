#!/usr/bin/env python3
"""调试：核实"符号/方向箭头是否是纯视觉元素，文本节点里根本没有负号"这个猜测。
用 Jina 的 HTML 模式（而不是 markdown 模式）抓一次 agent 页面，
在 HTML 源码里搜索 Qwen3.7 Plus 那一行附近，看它的负号/箭头到底是用什么
实现的（aria-label？svg path？css class 名里带 negative/down？还是真的
有一个我们没找到的负号字符）。跑完人工核对后删除。"""
import os, sys, time, json, urllib.request, urllib.error
from pathlib import Path

JINA_READER_BASE = "https://r.jina.ai/"
ARENA_BASE = "https://arena.ai/leaderboard/"

def fetch_page(url, jina_api_key=None, fmt="html"):
    reader_url = f"{JINA_READER_BASE}{url}"
    headers = {"Accept": "application/json", "X-Return-Format": fmt,
               "User-Agent": "Mozilla/5.0 (compatible; arena-leaderboard-bot/1.0)"}
    if jina_api_key:
        headers["Authorization"] = f"Bearer {jina_api_key}"
    req = urllib.request.Request(reader_url, headers=headers)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
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

    raw = fetch_page(f"{ARENA_BASE}agent", jina_key, fmt="html")
    try:
        content = json.loads(raw)["data"]["content"]
    except Exception:
        content = raw
    (out_dir / "agent-html.raw.html").write_text(content, encoding="utf-8")
    print(f"wrote agent-html.raw.html ({len(content)} chars)", file=sys.stderr)

    # locate context around "Qwen3.7 Plus" and "0.61"
    idx = content.find("Qwen3.7 Plus")
    if idx == -1:
        idx = content.find("0.61")
    if idx != -1:
        snippet = content[max(0, idx-800):idx+800]
        (out_dir / "agent-html-snippet.txt").write_text(snippet, encoding="utf-8")
        print("wrote snippet around Qwen3.7 Plus / 0.61", file=sys.stderr)
    else:
        print("could not locate Qwen3.7 Plus or 0.61 in html content", file=sys.stderr)

if __name__ == "__main__":
    main()
