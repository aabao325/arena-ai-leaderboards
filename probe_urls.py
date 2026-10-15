#!/usr/bin/env python3
"""探测 agent 和 image 分类的正确 URL 路径"""
import sys
import json
import urllib.request
import time

sys.stdout.reconfigure(encoding='utf-8')

JINA = "https://r.jina.ai/"

# 候选 URL —— 探测 agent 和 image-edit 的正确路径
CANDIDATES = [
    # agent 的几种可能
    "https://arena.ai/leaderboard/agent",
    "https://arena.ai/leaderboard/agent/agent",
    "https://arena.ai/leaderboard/agent/overall",
    # image-edit 的几种可能
    "https://arena.ai/leaderboard/image",
    "https://arena.ai/leaderboard/image/image-edit",
    "https://arena.ai/leaderboard/image/image-edit/overall",
    "https://arena.ai/leaderboard/image/image-edit/single-image-edit",
    "https://arena.ai/leaderboard/image/image-edit/multi-image-edit",
    # 其他已知能用的
    "https://arena.ai/leaderboard/chat/text",
]

HEADERS = {
    "Accept": "application/json",
    "X-Return-Format": "markdown",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
}


def probe(url, timeout=45):
    try:
        req = urllib.request.Request(f"{JINA}{url}", headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
        d = json.loads(raw).get("data", {})
        content = d.get("content", "")
        status = d.get("httpStatus", 200)
        has_table = "|" in content and "---" in content
        # 判断是否是错误页
        is_404 = "cannot find the page" in content.lower()
        is_captcha = "security verification" in content.lower() or "CAPTCHA" in content
        kind = "TABLE" if has_table else ("404" if is_404 else ("CAPTCHA" if is_captcha else "other"))
        return (status, len(content), kind, content[:100].replace("\n", " "))
    except Exception as e:
        return (0, 0, "ERROR", str(e)[:100])


print("=== 探测正确 URL ===\n")
for url in CANDIDATES:
    short = url.replace("https://arena.ai/leaderboard/", "")
    status, clen, kind, preview = probe(url)
    mark = "✅" if kind == "TABLE" else "❌"
    print(f"{mark} {short:45s} status={status:3d} len={clen:6d} [{kind}]")
    if kind not in ("TABLE",):
        print(f"     {preview}")
    time.sleep(2)
