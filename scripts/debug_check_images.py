#!/usr/bin/env python3
"""调试：核实 arena.ai 页面里，各分类表格中到底有多少行的 Model 单元格
里带 markdown 图片语法 ![...](...)（代表真实 <img> 标签，Jina 才会转出来），
用于判断"直接抓 arena.ai 自带的 logo"这条路是否可行。跑完人工核对后删除。"""
import os, sys, time, re, json, urllib.request, urllib.error
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

    targets = ["text", "text-to-image", "text-to-video"]
    summary = {}
    for slug in targets:
        url = f"{ARENA_BASE}{slug}"
        text = fetch_page(url, jina_key)
        content = json.loads(text)["data"]["content"]
        lines = content.split("\n")
        header_idx = next((i for i, l in enumerate(lines) if re.match(r"^\|\s*Rank\s*\|", l)), None)
        rows = []
        for i in range(header_idx + 2, len(lines)):
            if not lines[i].startswith("|"):
                break
            rows.append(lines[i])
        with_img = [r for r in rows if "![" in r]
        summary[slug] = {"total_rows": len(rows), "rows_with_img": len(with_img), "examples": with_img[:5]}
        time.sleep(2)

    (out_dir / "image-check-summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False), file=sys.stderr)

if __name__ == "__main__":
    main()
