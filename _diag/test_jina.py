#!/usr/bin/env python3
import requests
import json
import sys

url = 'https://r.jina.ai/https://arena.ai/ranking/vision'
headers = {
    'Accept': 'application/json',
    'X-Return-Format': 'markdown',
    'X-Timeout': '60',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

print("Fetching...", file=sys.stderr)
r = requests.get(url, headers=headers, timeout=120)
data = r.json()

http_status = data.get('data', {}).get('httpStatus', 200)
content = data.get('data', {}).get('content', '')
warning = data.get('data', {}).get('warning', '')

print(f"HTTP Status: {http_status}")
print(f"Content Length: {len(content)}")
print(f"Warning: {warning}")

if len(content) < 2000:
    print(f"\n=== Content Preview ===\n{content[:2000]}")
else:
    print(f"\n=== First 500 chars ===\n{content[:500]}")

# 检查是否有表格
if '|' in content and '---' in content:
    print("\n✅ 发现 markdown 表格标记")
else:
    print("\n❌ 未发现 markdown 表格")
