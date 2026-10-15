#!/usr/bin/env python3
"""
不依赖 Jina，直接用 curl 检测 URL 是否返回 200
"""
import sys
import subprocess

sys.stdout.reconfigure(encoding='utf-8')

TEST_URLS = [
    ("chat/text", "https://arena.ai/leaderboard/chat/text"),
    ("agent/agent", "https://arena.ai/leaderboard/agent/agent"),
    ("image/image-edit", "https://arena.ai/leaderboard/image/image-edit"),
    ("code/webdev", "https://arena.ai/leaderboard/code/webdev"),
]

print("=== 检测新 URL 可访问性 ===\n")

results = []
for name, url in TEST_URLS:
    print(f"[{name}] {url}")
    try:
        # 用 curl 检测 HTTP 状态码
        result = subprocess.run(
            ["curl", "-sI", "-w", "%{http_code}", "-o", "/dev/null", url],
            capture_output=True,
            text=True,
            timeout=10
        )
        status_code = result.stdout.strip()

        if status_code == "200":
            print(f"  ✅ {status_code} OK\n")
            results.append((name, True))
        elif status_code in ["301", "302", "307", "308"]:
            print(f"  ⚠️  {status_code} 重定向\n")
            results.append((name, False))
        else:
            print(f"  ❌ {status_code}\n")
            results.append((name, False))
    except Exception as e:
        print(f"  ❌ 错误: {e}\n")
        results.append((name, False))

print("=" * 60)
success = sum(1 for _, ok in results if ok)
print(f"可访问: {success}/{len(TEST_URLS)}")

if success == len(TEST_URLS):
    print("🎉 所有 URL 均可访问，配置正确！")
    sys.exit(0)
else:
    print(f"⚠️  {len(TEST_URLS) - success} 个 URL 不可访问")
    sys.exit(1)
