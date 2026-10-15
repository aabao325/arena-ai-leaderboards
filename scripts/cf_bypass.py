#!/usr/bin/env python3
"""
Cloudflare 验证破解模块（基于 Playwright）

核心策略：
1. Stealth 模式启动浏览器
2. 注入 turnstilePatch（修复 MouseEvent）
3. 检测 CF 验证 iframe
4. 多重点击策略破解
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional, Tuple
from playwright.async_api import Page, Browser, BrowserContext

logger = logging.getLogger(__name__)


# turnstilePatch 脚本（修复 MouseEvent.screenX/screenY）
TURNSTILE_PATCH = """
(function() {
    function getRandomInt(min, max) {
        return Math.floor(Math.random() * (max - min + 1)) + min;
    }

    // 核心 CF 绕过：修复 MouseEvent
    Object.defineProperty(MouseEvent.prototype, 'screenX', {
        get: function() { return getRandomInt(800, 1200); },
        configurable: true
    });

    Object.defineProperty(MouseEvent.prototype, 'screenY', {
        get: function() { return getRandomInt(400, 600); },
        configurable: true
    });

    // 隐藏 webdriver
    Object.defineProperty(navigator, 'webdriver', {
        get: () => false,
        configurable: true
    });

    console.log('✅ turnstilePatch 已激活');
})();
"""


class CloudflareBypass:
    """Cloudflare 验证破解器"""

    def __init__(self, page: Page):
        self.page = page

    async def inject_patch(self) -> bool:
        """注入 turnstilePatch 补丁"""
        try:
            await self.page.evaluate(TURNSTILE_PATCH)
            print("✅ turnstilePatch 补丁注入成功")
            return True
        except Exception as e:
            print(f"❌ turnstilePatch 补丁注入失败: {e}")
            return False

    async def check_cf_challenge(self) -> bool:
        """检测是否存在 CF 验证"""
        try:
            # 检查 cf-turnstile-response 元素
            challenge = await self.page.query_selector('[name="cf-turnstile-response"]')

            if not challenge:
                #logger.debug("未检测到 cf-turnstile-response 元素")
                return False

            #logger.debug("检测到 cf-turnstile-response 元素")

            # 检查是否有可见的 iframe
            iframes = await self.page.query_selector_all('iframe')

            for iframe in iframes:
                try:
                    is_visible = await iframe.is_visible()
                    if is_visible:
                        # 检查 iframe 内容是否包含验证按钮
                        frame = await iframe.content_frame()
                        if frame:
                            checkbox = await frame.query_selector('input[type="checkbox"]')
                            if checkbox:
                                print("🖥️ 检测到可见的 CF 验证 iframe")
                                return True
                except Exception:
                    continue

            #logger.debug("未检测到可见的 CF 验证 iframe")
            return False

        except Exception as e:
            #logger.debug(f"CF 验证检测异常: {e}")
            return False

    async def solve_challenge(self, max_attempts: int = 3) -> bool:
        """
        破解 CF 验证

        Args:
            max_attempts: 最大尝试次数

        Returns:
            是否成功破解
        """
        print("🖥️ 开始破解 CF 验证...")

        for attempt in range(max_attempts):
            try:
                # 1. 查找验证 iframe
                iframes = await self.page.query_selector_all('iframe')
                challenge_iframe = None

                for iframe in iframes:
                    try:
                        is_visible = await iframe.is_visible()
                        if not is_visible:
                            continue

                        frame = await iframe.content_frame()
                        if not frame:
                            continue

                        # 检查是否包含验证按钮
                        checkbox = await frame.query_selector('input[type="checkbox"]')
                        if checkbox:
                            challenge_iframe = frame
                            print(f"🖥️ [尝试 {attempt + 1}/{max_attempts}] 定位到验证 iframe")
                            break
                    except Exception:
                        continue

                if not challenge_iframe:
                    print("🖥️ 未找到可见的验证 iframe")
                    await asyncio.sleep(2)
                    continue

                # 2. 注入反检测 JS
                print("🖥️ 注入反检测 JS...")
                try:
                    await challenge_iframe.evaluate(TURNSTILE_PATCH)
                    print("🖥️ ✅ 反检测 JS 注入成功")
                except Exception as e:
                    print(f"🖥️ ⚠️  反检测 JS 注入失败: {e}")

                # 3. 尝试点击验证按钮
                print("🖥️ 尝试点击验证按钮...")

                # 方法1：直接点击
                try:
                    checkbox = await challenge_iframe.query_selector('input[type="checkbox"]')
                    if checkbox:
                        is_enabled = await checkbox.is_enabled()
                        is_visible_check = await checkbox.is_visible()

                        if is_enabled and is_visible_check:
                            await checkbox.click(timeout=2000)
                            print("🖥️ ✅ 直接点击成功")

                            # 等待验证通过
                            await asyncio.sleep(3)
                            return True
                        else:
                            print("🖥️ 验证按钮不可用（disabled 或不可见），改走 JS 强制点击")
                except Exception as e:
                    print(f"🖥️ 直接点击失败，改走 JS 强制点击: {e}")

                # 方法2：JS 强制点击
                print("🖥️ 尝试 JS 强制点击...")

                click_js = """
                (function() {
                    try {
                        const checkbox = document.querySelector('input[type="checkbox"]');
                        if (!checkbox) {
                            console.error('未找到验证按钮');
                            return false;
                        }

                        // 多阶段点击
                        checkbox.click();

                        setTimeout(() => {
                            checkbox.focus();
                            checkbox.click();
                        }, 50);

                        setTimeout(() => {
                            const rect = checkbox.getBoundingClientRect();
                            const mouseEvent = new MouseEvent('click', {
                                view: window,
                                bubbles: true,
                                cancelable: true,
                                clientX: rect.left + 10,
                                clientY: rect.top + 10
                            });
                            checkbox.dispatchEvent(mouseEvent);
                        }, 100);

                        console.log('✅ JS 强制点击完成');
                        return true;

                    } catch (error) {
                        console.error('JS 点击失败:', error);
                        return false;
                    }
                })();
                """

                try:
                    click_result = await challenge_iframe.evaluate(click_js)

                    if click_result:
                        print("🖥️ ✅ JS 强制点击成功")

                        # 等待验证通过
                        await asyncio.sleep(5)
                        return True
                    else:
                        print("🖥️ ⚠️  JS 强制点击返回失败")
                except Exception as e:
                    print(f"🖥️ ❌ JS 强制点击异常: {e}")

                # 失败后等待一会儿再重试
                if attempt < max_attempts - 1:
                    print(f"🖥️ 等待 5 秒后重试...")
                    await asyncio.sleep(5)

            except Exception as e:
                print(f"🖥️ [尝试 {attempt + 1}/{max_attempts}] 破解过程异常: {e}")
                if attempt < max_attempts - 1:
                    await asyncio.sleep(5)

        print("🖥️ ❌ 所有破解尝试均失败")
        return False

    async def wait_for_page_ready(self, timeout: int = 30) -> bool:
        """
        等待页面完全加载（验证通过后）

        Args:
            timeout: 超时时间（秒）

        Returns:
            是否成功加载
        """
        try:
            # 等待页面稳定
            await self.page.wait_for_load_state('networkidle', timeout=timeout * 1000)
            print("✅ 页面已完全加载")
            return True
        except Exception as e:
            print(f"⚠️  页面加载超时: {e}")
            return False


async def bypass_cloudflare(page: Page, max_retries: int = 3) -> bool:
    """
    自动绕过 Cloudflare 验证的辅助函数

    Args:
        page: Playwright Page 对象
        max_retries: 最大重试次数

    Returns:
        是否成功绕过
    """
    bypass = CloudflareBypass(page)

    # 先注入补丁（预防性）
    await bypass.inject_patch()

    # 等待页面稳定
    await asyncio.sleep(3)

    # 检测并破解 CF 验证
    for retry in range(max_retries):
        has_challenge = await bypass.check_cf_challenge()

        if not has_challenge:
            print("✅ 未检测到 CF 验证，页面正常")
            return True

        print(f"🖥️ 检测到 CF 验证，开始破解（重试 {retry + 1}/{max_retries}）")

        success = await bypass.solve_challenge()

        if success:
            print("🖥️ ✅ CF 验证破解成功")

            # 等待页面完全加载
            await bypass.wait_for_page_ready()
            return True
        else:
            print(f"🖥️ ⚠️  破解失败，等待后重试...")
            await asyncio.sleep(5)

    print("🖥️ ❌ 所有重试均失败")
    return False
