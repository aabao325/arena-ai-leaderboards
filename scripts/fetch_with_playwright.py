#!/usr/bin/env python3
"""
使用 Playwright 浏览器自动化抓取 arena.ai 排行榜
绕过 Cloudflare 防护和 CAPTCHA
集成 CF 破解模块
"""
import sys
import json
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

sys.stdout.reconfigure(encoding='utf-8')

# 导入 CF 破解模块
sys.path.insert(0, str(Path(__file__).parent))
from cf_bypass import bypass_cloudflare

# 新的 URL 结构
LEADERBOARDS = [
    ("agent", "https://arena.ai/leaderboard/agent/agent"),
    ("text", "https://arena.ai/leaderboard/chat/text"),
    ("search", "https://arena.ai/leaderboard/chat/search"),
    ("vision", "https://arena.ai/leaderboard/chat/vision"),
    ("document", "https://arena.ai/leaderboard/chat/document"),
    ("code-webdev", "https://arena.ai/leaderboard/code/webdev"),
    ("code-image-to-webdev", "https://arena.ai/leaderboard/code/image-to-webdev"),
    ("image-edit", "https://arena.ai/leaderboard/image/image-edit"),
    ("text-to-image", "https://arena.ai/leaderboard/image/text-to-image"),
    ("image-to-video", "https://arena.ai/leaderboard/image/image-to-video"),
    ("text-to-video", "https://arena.ai/leaderboard/video/text-to-video"),
    ("video-edit", "https://arena.ai/leaderboard/video/video-edit"),
]


async def fetch_leaderboard(page, name, url):
    """抓取一个排行榜分类"""
    print(f"[{name}] 正在访问 {url}")

    try:
        # 访问页面，等待 DOM 加载完成
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)

        # 🔥 关键：自动绕过 Cloudflare 验证
        print(f"[{name}] 检测并破解 Cloudflare 验证...")
        cf_success = await bypass_cloudflare(page, max_retries=3)

        if not cf_success:
            print(f"[{name}] ⚠️  CF 验证破解失败，但尝试继续...")

        # 等待表格出现（最多等待 30 秒）
        try:
            await page.wait_for_selector("table", timeout=30000)
        except:
            print(f"[{name}] ⚠️  30 秒内未检测到表格，尝试继续...")

        # 额外等待 2 秒，确保动态内容加载完成
        await asyncio.sleep(2)

        # 提取表格数据
        rows = await page.locator("table tbody tr").all()

        if not rows:
            print(f"[{name}] ❌ 未找到表格行")
            return None

        data = []
        for row in rows:
            cells = await row.locator("td").all()
            if len(cells) >= 3:
                # 提取基本信息：排名、模型名、分数
                rank_text = await cells[0].inner_text()
                model_text = await cells[1].inner_text()
                score_text = await cells[2].inner_text()

                data.append({
                    "rank": rank_text.strip(),
                    "model": model_text.strip(),
                    "score": score_text.strip(),
                })

        print(f"[{name}] ✅ 成功抓取 {len(data)} 条数据")
        return {
            "category": name,
            "url": url,
            "models": data,
            "count": len(data),
        }

    except Exception as e:
        print(f"[{name}] ❌ 错误: {e}")
        return None


async def main():
    async with async_playwright() as p:
        # 启动浏览器（使用 chromium，headless 模式）
        # 🔥 增强 Stealth 参数，绕过 Cloudflare 检测
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',  # 隐藏自动化标识
                '--disable-dev-shm-usage',  # Docker 环境需要
                '--no-sandbox',  # CI 环境需要
                '--disable-quic',  # 禁用 QUIC（强制 TCP）
                '--webrtc-ip-handling-policy=disable_non_proxied_udp',  # WebRTC 防护
                '--force-webrtc-ip-handling-policy',
                '--disable-webrtc-multiple-routes',
                '--enforce-webrtc-ip-permission-check',
            ]
        )

        # 创建上下文，设置真实的浏览器指纹
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )

        page = await context.new_page()

        # 抓取所有分类（顺序执行，避免并发触发限流）
        results = []
        for name, url in LEADERBOARDS:
            result = await fetch_leaderboard(page, name, url)
            if result:
                results.append(result)

            # 分类间延迟 5 秒，降低被检测为机器人的概率
            await asyncio.sleep(5)

        await browser.close()

        # 输出结果
        output_dir = Path("data_playwright")
        output_dir.mkdir(exist_ok=True)

        for result in results:
            output_file = output_dir / f"{result['category']}.json"
            output_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"✅ 已保存: {output_file}")

        print(f"\n=== 抓取完成 ===")
        print(f"成功: {len(results)}/{len(LEADERBOARDS)}")


if __name__ == "__main__":
    asyncio.run(main())
