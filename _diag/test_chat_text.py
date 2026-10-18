#!/usr/bin/env python3
"""快速验证 chat/text 能否正常抓取"""
import sys
import json
import urllib.request

sys.stdout.reconfigure(encoding='utf-8')

JINA = "https://r.jina.ai/"
TARGET = "https://arena.ai/leaderboard/chat/text"

headers = {
    "Accept": "application/json",
    "X-Return-Format": "markdown",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

print(f"正在抓取: {TARGET}\n")

try:
    req = urllib.request.Request(f"{JINA}{TARGET}", headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode("utf-8")

    payload = json.loads(raw)
    data = payload.get("data", {})
    content = data.get("content", "")
    status = data.get("httpStatus", 200)

    # 检查是否有表格
    has_table = "|" in content and "---" in content
    lines = content.split("\n")
    table_lines = [l for l in lines if l.strip().startswith("|")]

    print(f"✅ HTTP {status}")
    print(f"✅ 内容长度: {len(content)} 字符")
    print(f"✅ 包含表格: {'是' if has_table else '否'}")
    print(f"✅ 表格行数: {len(table_lines)}")

    if has_table:
        print("\n前 5 行预览：")
        for line in table_lines[:5]:
            print(f"  {line[:100]}")

        print("\n✅ chat/text 抓取成功！")
        sys.exit(0)
    else:
        print("\n❌ 没有找到表格")
        sys.exit(1)

except Exception as e:
    print(f"\n❌ 错误: {e}")
    sys.exit(1)
