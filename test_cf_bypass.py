#!/usr/bin/env python3
"""
测试各种 Cloudflare 绕过方案的本地可行性
"""
import sys

sys.stdout.reconfigure(encoding='utf-8')

TARGET = "https://arena.ai/leaderboard/chat/text"

print("=" * 70)
print("测试 1: curl_cffi（TLS 指纹伪装，最有可能绕过 Cloudflare）")
print("=" * 70)

try:
    from curl_cffi import requests as cffi_requests

    for imp in ["chrome", "chrome124", "chrome131", "safari17_0", "firefox133"]:
        try:
            r = cffi_requests.get(TARGET, impersonate=imp, timeout=30)
            content = r.text
            has_table = "<table" in content or "| Rank |" in content
            is_cf = "Just a moment" in content or "challenge-platform" in content
            status = "TABLE" if has_table else ("CF-CHALLENGE" if is_cf else "OTHER")
            print(f"  [{'OK ' if has_table else '.. '}] impersonate={imp:12s} "
                  f"status={r.status_code} len={len(content):7d} -> {status}")
            if has_table:
                print(f"\n  ✅✅✅ {imp} 成功绕过！")
                # 保存样本
                with open("sample_curl_cffi.html", "w", encoding="utf-8") as f:
                    f.write(content)
                print(f"  样本已保存到 sample_curl_cffi.html")
                sys.exit(0)
        except Exception as e:
            print(f"  [!] impersonate={imp:12s} 错误: {str(e)[:60]}")

except ImportError:
    print("  curl_cffi 未安装，跳过")

print()
print("=" * 70)
print("测试 2: cloudscraper")
print("=" * 70)

try:
    import cloudscraper

    scraper = cloudscraper.create_scraper(
        browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False}
    )
    r = scraper.get(TARGET, timeout=40)
    content = r.text
    has_table = "<table" in content or "| Rank |" in content
    is_cf = "Just a moment" in content or "challenge-platform" in content
    status = "TABLE" if has_table else ("CF-CHALLENGE" if is_cf else "OTHER")
    print(f"  [{'OK ' if has_table else '.. '}] status={r.status_code} len={len(content):7d} -> {status}")
    if has_table:
        print(f"\n  ✅✅✅ cloudscraper 成功绕过！")
        sys.exit(0)

except ImportError:
    print("  cloudscraper 未安装，跳过")

print()
print("=" * 70)
print("测试 3: curl_cffi 的 session + 重试（模拟多次尝试）")
print("=" * 70)

try:
    from curl_cffi import requests as cffi_requests

    s = cffi_requests.Session(impersonate="chrome")
    success = 0
    for i in range(5):
        try:
            r = s.get(TARGET, timeout=30)
            if "<table" in r.text or "| Rank |" in r.text:
                success += 1
                print(f"  第 {i+1} 次: ✅ 成功（{len(r.text)} 字符）")
                with open("sample_session.html", "w", encoding="utf-8") as f:
                    f.write(r.text)
            else:
                is_cf = "Just a moment" in r.text or "challenge-platform" in r.text
                print(f"  第 {i+1} 次: ❌ {'CF 挑战' if is_cf else '其他'}（{len(r.text)} 字符）")
        except Exception as e:
            print(f"  第 {i+1} 次: ❌ 错误 {str(e)[:50]}")
    print(f"\n  成功率: {success}/5")

except ImportError:
    pass
