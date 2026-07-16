#!/usr/bin/env python3
"""调试：核实 Agent 页面里的负数百分比到底用的是哪种减号字符
（ASCII hyphen-minus U+002D，还是排版减号 U+2212，还是短/长破折号）。
之前两次人工核查都只搜索了 ASCII '-'，如果页面实际用的是别的 Unicode
字符，会导致我们误判为"没有负值"。这里对整段原始文本逐字符扫描，
只要发现任何"减号类字符 + 数字 + %"的模式就报告出具体 Unicode 码点。
跑完人工核对后删除。"""
import os, sys, time, json, unicodedata, urllib.request, urllib.error
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

    raw = fetch_page(f"{ARENA_BASE}agent", jina_key)
    content = json.loads(raw)["data"]["content"]
    (out_dir / "agent-unicode-check.raw.md").write_text(content, encoding="utf-8")

    dash_chars = set()
    for ch in content:
        if unicodedata.category(ch) == "Pd" or ch == "−":
            dash_chars.add(ch)

    report = {"dash_like_chars_found": [{"char": c, "codepoint": "U+%04X" % ord(c), "name": unicodedata.name(c, "?")} for c in sorted(dash_chars)]}

    hits = []
    for c in dash_chars:
        idx = 0
        while True:
            idx = content.find(c, idx)
            if idx == -1:
                break
            window = content[max(0, idx-5):idx+15]
            if idx + 1 < len(content) and content[idx+1].isdigit():
                hits.append(window)
            idx += 1
    report["candidate_negative_number_contexts"] = hits[:40]
    report["candidate_count"] = len(hits)

    (out_dir / "agent-unicode-check-summary.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False), file=sys.stderr)

if __name__ == "__main__":
    main()
