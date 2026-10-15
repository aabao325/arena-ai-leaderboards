#!/usr/bin/env python3
"""
系统测试：通过 Jina Reader 抓取新 URL，对比不同 header 组合
"""
import sys
import json
import urllib.request
import urllib.error

sys.stdout.reconfigure(encoding='utf-8')

JINA = "https://r.jina.ai/"

# 测试目标：新 URL 结构
TARGETS = [
    ("chat/text", "https://arena.ai/leaderboard/chat/text"),
    ("image/image-edit", "https://arena.ai/leaderboard/image/image-edit"),
    ("agent/agent", "https://arena.ai/leaderboard/agent/agent"),
]

# 对比不同 header 组合
HEADER_VARIANTS = [
    ("minimal", {
        "Accept": "application/json",
        "X-Return-Format": "markdown",
    }),
    ("browser-ua", {
        "Accept": "application/json",
        "X-Return-Format": "markdown",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    }),
    ("with-generated-alt", {
        "Accept": "application/json",
        "X-Return-Format": "markdown",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "X-With-Generated-Alt": "true",
    }),
]


def test_fetch(url, headers, timeout=60):
    """返回 (http_status, content_len, has_table, error)"""
    try:
        req = urllib.request.Request(f"{JINA}{url}", headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
        payload = json.loads(raw)
        d = payload.get("data", {})
        content = d.get("content", "")
        status = d.get("httpStatus", 200)
        warning = d.get("warning", "")
        has_table = "|" in content and "---" in content
        return (status, len(content), has_table, warning[:80])
    except urllib.error.HTTPError as e:
        return (e.code, 0, False, f"HTTPError: {e.reason}")
    except Exception as e:
        return (0, 0, False, str(e)[:80])


print("=== Jina Reader 对新 URL 的支持测试 ===\n")

for name, url in TARGETS:
    print(f"### {name}")
    print(f"    {url}")
    for hname, headers in HEADER_VARIANTS:
        status, clen, has_table, err = test_fetch(url, headers)
        mark = "OK " if (status == 200 and clen > 500) else "FAIL"
        table_mark = "TABLE" if has_table else "no-table"
        print(f"    [{mark}] {hname:22s} status={status:3d} len={clen:6d} {table_mark} {err}")
    print()
