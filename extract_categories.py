#!/usr/bin/env python3
"""提取 arena.ai 的完整分类导航"""
import sys
from playwright.sync_api import sync_playwright
import json
import time

def extract_categories():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # 访问主页，确保能加载完整导航
        print("正在访问 arena.ai/leaderboard...", file=sys.stderr)
        page.goto("https://arena.ai/leaderboard/chat/text", wait_until="networkidle")
        time.sleep(2)  # 等待动态内容

        # 提取导航结构
        categories = {}

        # 尝试多种选择器定位导航
        nav_selectors = [
            'nav a[href*="/leaderboard/"]',
            '[role="navigation"] a[href*="/leaderboard/"]',
            'a[href^="/leaderboard/"]',
            'header a[href*="/leaderboard/"]'
        ]

        all_links = []
        for selector in nav_selectors:
            try:
                links = page.query_selector_all(selector)
                if links:
                    all_links = links
                    print(f"找到 {len(links)} 个链接（选择器: {selector}）", file=sys.stderr)
                    break
            except:
                continue

        # 提取链接并解析结构
        for link in all_links:
            try:
                href = link.get_attribute('href')
                text = link.inner_text().strip()

                if not href or not text:
                    continue

                # 解析 URL: /leaderboard/{category}/{subcategory}
                parts = href.strip('/').split('/')
                if len(parts) >= 3 and parts[0] == 'leaderboard':
                    category = parts[1]
                    subcategory = parts[2]

                    if category not in categories:
                        categories[category] = []
                    if subcategory not in categories[category]:
                        categories[category].append(subcategory)
            except Exception as e:
                print(f"解析链接出错: {e}", file=sys.stderr)

        # 如果上述方式失败，直接读取页面 HTML
        if not categories:
            print("尝试从 HTML 中提取...", file=sys.stderr)
            html = page.content()

            # 保存 HTML 供调试
            with open('D:/aabao/Documents/GitHub/aabao-tools/_temp_arena/page.html', 'w', encoding='utf-8') as f:
                f.write(html)
            print("已保存 page.html", file=sys.stderr)

            # 简单正则提取 /leaderboard/* 链接
            import re
            pattern = r'/leaderboard/([^/]+)/([^/"\']+)'
            matches = re.findall(pattern, html)

            for cat, subcat in matches:
                if cat not in categories:
                    categories[cat] = []
                if subcat not in categories[cat]:
                    categories[cat].append(subcat)

        browser.close()
        return categories

if __name__ == "__main__":
    result = extract_categories()
    print(json.dumps(result, indent=2, ensure_ascii=False))
