#!/usr/bin/env python3
import sys
import requests

sys.stdout.reconfigure(encoding='utf-8')

# 尝试不同的可能 URL
candidates = [
    "https://arena.ai/leaderboard",
    "https://arena.ai/leaderboard/vision",
    "https://arena.ai/ranking/vision",
    "https://arena.ai/leaderboards/vision",
]

for url in candidates:
    print(f"\n尝试: {url}")
    try:
        r = requests.head(url, timeout=10, allow_redirects=True)
        print(f"  状态: {r.status_code}")
        if r.status_code == 200:
            print(f"  ✅ 成功！最终 URL: {r.url}")
        elif 300 <= r.status_code < 400:
            print(f"  跳转到: {r.headers.get('Location')}")
    except Exception as e:
        print(f"  错误: {e}")
