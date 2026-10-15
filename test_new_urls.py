#!/usr/bin/env python3
"""快速验证新 URL 配置：抓取 3 个代表性分类"""
import sys
import os

# 添加 scripts 到路径以便导入
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'scripts'))

from fetch_leaderboards import fetch_page, parse_leaderboard, ARENA_BASE

# 测试 3 个分类：chat/text（原 text）、agent/agent（原 agent）、image/image-edit（新三级）
TEST_CASES = [
    ("text", "chat/text", "Chat: Text（原 text 分类）"),
    ("agent", "agent/agent", "Agent（原 agent 分类）"),
    ("image-edit", "image/image-edit", "Image Edit Arena（新增三级结构）"),
]

print("=== 验证新 URL 配置 ===\n")

results = []
for key, url_path, desc in TEST_CASES:
    print(f"[{key}] {desc}")
    print(f"  URL: {ARENA_BASE}{url_path}")

    try:
        # 尝试抓取（30s 超时）
        content = fetch_page(f"{ARENA_BASE}{url_path}", wait_timeout=30, user_agent_idx=0)

        if not content or len(content) < 100:
            print(f"  ❌ 失败：内容为空或过短（{len(content or '')} 字符）\n")
            results.append((key, False, "空内容"))
            continue

        # 尝试解析表格
        try:
            rows = parse_leaderboard(content, key)
            if rows and len(rows) > 0:
                print(f"  ✅ 成功：解析到 {len(rows)} 个模型\n")
                results.append((key, True, len(rows)))
            else:
                print(f"  ⚠️  抓取成功但解析失败：没有找到表格\n")
                results.append((key, False, "无表格"))
        except Exception as e:
            print(f"  ⚠️  抓取成功但解析失败：{e}\n")
            results.append((key, False, str(e)))

    except Exception as e:
        print(f"  ❌ 抓取失败：{e}\n")
        results.append((key, False, str(e)))

print("=" * 50)
print("总结：")
success = sum(1 for _, ok, _ in results if ok)
print(f"  成功：{success}/{len(TEST_CASES)}")
for key, ok, info in results:
    status = "✅" if ok else "❌"
    detail = f"{info} 个模型" if ok else info
    print(f"  {status} {key}: {detail}")

if success == len(TEST_CASES):
    print("\n🎉 所有测试通过，新配置正确！")
    sys.exit(0)
else:
    print(f"\n⚠️  {len(TEST_CASES) - success} 个测试失败，需要调试")
    sys.exit(1)
