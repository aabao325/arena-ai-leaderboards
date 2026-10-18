#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cloudflare 过验证 —— 独立可复用模块

    from cf_solver import CFBrowser, CF_COOKIE

用法（独立自测，不需要别的脚本）：
    python cf_solver.py                 # 过 CF + 六项自检
    python cf_solver.py --fresh         # 丢弃旧 profile，从零开始过
    python cf_solver.py --find-rsc      # 顺便探出可用 RSC 路径
    python cf_solver.py --headless      # 验证无头是否可行

────────────── 这个模块凭什么能过 ──────────────

三处曾经的错误做法，对照着看：

  1) 浏览器用 Chrome/DrissionPage + 注入 JS 补丁
     → 错。要用 **Camoufox**（Firefox 定制构建，指纹伪装在二进制/C++ 层）。
       注入 JS 补丁会制造特征不一致，比不注入更容易被识别。

  2) 想尽办法"让受保护的页面打开"
     → 错。CF 拦的是 document 导航。榜单页的 document 永远是 403，
       真正可用的是**同源 XHR / RSC**（实测 200，见下表）。
       我们的做法是：拿到一个已过验证的**干净页面上下文**，
       然后在里面 fetch。榜单页文档根本不需要打开。

  3) 在启动参数上反复调（disable_coop / humanize / geoip / 模拟点击）
     → 无用。四种组合全卡在 cf-input=1 iframes=0，即策略性拒绝。
       同一台机器同一个 Camoufox，首页 3 秒就过 —— 指纹没问题，
       换个参数不会让被拦的路径变得不被拦。

实测数据（2026-10-16）：

    GET /zh                                       → ✅ 约 3 秒通过
    GET /leaderboard/chat/text                    → ❌ 403（CF 挑战页）
    GET /leaderboard/chat/text?_rsc=x   RSC: 1    → ✅ 200  text/x-component
    GET /leaderboard?_rsc=x             RSC: 1    → ✅ 200  text/x-component
    GET /nextjs-api/model-catalog                 → ✅ 200  application/json

────────────── 为什么判据只能用标题 ──────────────

早前用 body 里有没有 "security verification" 判断是否还在挑战页，
结果在挑战页上也返回「未在挑战」，把失败报成成功。
那串措辞是 **Jina Reader 的**,CF 挑战页正文里没有。
唯一的可靠判据是 document.title 里的 "Just a moment"。

────── 关于 cf_clearance 能不能复用：别指望跨 IP ──────

过关后 CF 会下发 cf_clearance，存在持久化 profile 里。

  · **同一台机器同一个 IP 重跑** → cookie 有效，秒过。这是正常路径。
  · **换 IP 复用** → **软失败**：不报错，静默返回挑战页，需要重新过验证。
    社区（含 Rust crw-renderer 的工程实现，缓存键 = host|IP|UA）一致认定
    它绑定 IP + UA + TLS 指纹；CF 官方文档只说"tied to the visitor and
    device"，全文未提 IP，但也从未否认。按绑来处理才是安全的。

**这条直接判了 GitHub Actions 死刑**，理由有三层：

  1. GH 托管 runner 每次 run 都是全新 VM、**新出口 IP**，无法跨 run 保持同一 IP，
     所以连"铸 cookie"这一步都可能过不去，不只是"复用"过不去。
  2. 出口是 Azure 网段，且 ranges 在 api.github.com/meta **公开**（约 7,038 条前缀、
     2,830 万 IPv4）。公开 = 全世界免费用户共用 = 集体信誉，你继承所有人的行为记录。
  3. CF 在**网络层、JS 执行之前**就先给数据中心 ASN 打分。所以真浏览器、反检测补丁、
     完美指纹都救不回来 —— 这不是指纹问题，是 IP 问题。

  另有一个独立证据：arena.ai 的 `/leaderboard/*` 对**住宅 IP** 也返回
  `Cf-Mitigated: challenge`，而首页不返回。说明这条路径还额外有一条
  **按路径的 WAF 规则**。**换 IP 最多只解决一半问题。**

  ⇒ 结论：抓取留在**你本机**（IP 可信 + 页面能过），CI 只负责校验和发布。
     `--headless` 在这里不是"为了在 CI 跑"，而是查无头能否替掉有头窗口。

────── 跨 run 复用与并发 ──────

  · profile 目录含 cf_clearance 等会话凭据，**已在 .gitignore**
  · Firefox 用 profile 根目录的 `parent.lock` 互斥；第二个进程不会立刻报错，
    而是卡到 Playwright 超时（实测 86–146 秒）再以 SIGTERM 死掉 ——
    所以本模块自己先抢锁，抢不到立刻失败（`ProfileBusy`）
  · 顺序连跑同一 profile 没问题（实测 3.88/3.27/3.38 秒），风险只在**时间重叠**
  · 本机 camoufox 是 0.5.4，PyPI 已 0.5.8。上游 README 文档的是新 API，
    本机没有 `canvas_noise` / `pin_cpu_cores` / `launch_server(ws_path=)`
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from camoufox.async_api import AsyncCamoufox

HERE = Path(__file__).parent.resolve()
PROFILE_DIR = HERE / "profile"          # 持久化 profile（cf_clearance 存这里）
OUT_DIR = HERE / "_out"

CF_COOKIE = "cf_clearance"
CHALLENGE_MARK = "Just a moment"

# ─── cf_clearance 到底绑不绑 IP：官方没写，社区一致说绑 ───
#
# Cloudflare 官方 Clearance 文档只说了一句：
#   "securely tied to the specific visitor and device it was issued to,
#    preventing reuse across machines"
# 全文**没有**提到 IP、User-Agent、TLS/JA3 中的任何一项。
#
# 但社区（含 Rust crw-renderer 的工程实现）一致认定缓存键是 (host, IP, UA)，
# 其源码注释原文："bound by Cloudflare to the (IP, User-Agent, TLS/JA3) that
# solved the challenge" / "Different egress → different key → no cross-IP reuse"。
#
# 所以本模块的立场：**不指望 cookie 跨 IP 复用**。
#   - 同机同 IP 重跑：profile 里的 cf_clearance 有效，秒过
#   - 换 IP 复用：会**软失败** —— 不报错，静默返回挑战页
#   这也是为什么 solve() 不信任"cookie 存在"就宣布通过，
#   而是每次都用 verify_context() 实测页内 fetch 能不能取到数据。
#
# 官方 TTL：Challenge Passage 默认 30 分钟，建议配 15–45 分钟；
# 验 cookie 时额外加几分钟抵消时钟偏移，XHR 请求再加 1 小时。
# 另有 Precursor（替代 JSD 的持续客户端验证）会**提前**作废 cookie，
# 官方原文 "may be re-challenged, even if the cookie has not expired"。
# 结论：不要用固定计时器判断有效性，一律以响应信号为准，失效就重铸。

# 官方文档化的挑战检测信号（developers.cloudflare.com/.../detect-response/）：
#   Cf-Mitigated: challenge   ← 唯一合法值，所有 Challenge Page 类型都会带
# 官方明确建议 fetch/XHR 客户端用它做程序化检测。底层状态码是裸 403。
#
# 注意与 "Cloudflare error 1006/1007"（IP 被封）区分：后者换 cookie 无用。
MITIGATED_HEADER = "cf-mitigated"

# 用于验证"页面上下文真的可用"的探针。
# 必须选 CF 会对 document 导航返回 403 的路径 —— 这样 200 才有意义。
RSC_HEADERS = {"RSC": "1", "Accept": "text/x-component"}
PROBE_RSC = "/leaderboard/chat/text"
PROBE_JSON = "/nextjs-api/model-catalog"

MODEL_RE = re.compile(
    r"gemini|gpt-?\d|claude|grok|llama|qwen|deepseek|mistral|kimi|glm-",
    re.I,
)


class Log:
    def __init__(self, path: Path | None = None):
        self.fh = None
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.fh = open(path, "w", encoding="utf-8")

    def __call__(self, m=""):
        print(m, flush=True)
        if self.fh:
            self.fh.write(str(m) + "\n")
            self.fh.flush()

    def close(self):
        if self.fh:
            self.fh.close()


async def page_title(page) -> str:
    try:
        return await page.title() or ""
    except Exception:
        return ""


async def is_challenge(page) -> bool:
    """是否仍在 CF 挑战页。判据只用标题 —— 见模块头部说明。"""
    return CHALLENGE_MARK in (await page_title(page))


async def get_cf_cookie(context) -> dict | None:
    """取当前上下文的 cf_clearance（只回报是否存在与过期时间，不打印值）"""
    try:
        for c in await context.cookies():
            if c.get("name") == CF_COOKIE:
                return c
    except Exception:
        pass
    return None


class ProfileBusy(RuntimeError):
    """profile 被另一个进程占用（Firefox 的 parent.lock）"""


@contextlib.contextmanager
def profile_lock(profile: Path, timeout_s: float = 4.0):
    """用 lock 文件互斥，避免两个进程同时用一个 profile。

    Firefox 自己在 profile 根目录放 `parent.lock` 并用 `-no-remote -profile`
    启动。第二个进程不会立刻报错，而是**卡住直到 Playwright 超时** ——
    实测 86–146 秒，然后以 exitCode=143 (SIGTERM) 退出。慢速失败比快速失败
    难查得多，而且 SIGTERM 中途打断启动有损坏 profile 的风险。

    所以宁可自己先抢锁，抢不到立刻失败。
    """
    profile.mkdir(parents=True, exist_ok=True)
    lock = profile / ".cf_solver.lock"
    deadline = time.monotonic() + timeout_s
    fd = None

    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()} {time.time():.0f}".encode())
            break
        except FileExistsError:
            # 陈旧锁（进程已死）应该能自愈
            try:
                pid = int(lock.read_text(encoding="utf-8").split()[0])
                alive = _pid_alive(pid)
            except Exception:
                alive = False

            if not alive:
                try:
                    lock.unlink()
                    continue
                except OSError:
                    pass

            if time.monotonic() >= deadline:
                raise ProfileBusy(
                    f"profile 被占用: {lock}\n"
                    f"      另一个 cf_solver / rsc_fetch 可能还在跑，先关掉它。\n"
                    f"      确认没有进程在用了，可以删掉这个 lock 文件。"
                )
            time.sleep(0.3)

    try:
        yield
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            lock.unlink()
        except OSError:
            pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import subprocess
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
            return str(pid) in out.stdout
        except Exception:
            return True          # 查不出来就保守认为还活着
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class CFBrowser:
    """Camoufox 浏览器 + Cloudflare 过关。

    典型用法：

        async with CFBrowser(headless=False) as b:
            if not await b.solve():
                raise RuntimeError(b.failure_reason)
            page = b.page
            ...  # 在 b.page 里 page.evaluate 发 fetch 取数据
    """

    def __init__(
        self,
        *,
        headless: bool = False,
        entry: str = "https://arena.ai/zh",
        budget_s: int = 60,
        profile: Path | None = None,
        use_profile: bool = True,
        log: Log | None = None,
        locale: str | None = None,
    ):
        self.headless = headless
        self.entry = entry
        self.budget_s = budget_s
        self.profile = profile if profile is not None else PROFILE_DIR
        self.use_profile = use_profile
        self.locale = locale
        self.log = log or Log()

        self.browser = None
        self.context = None
        self.page = None
        self._lock_cm = None
        self.failure_reason = ""
        self.passed_via = ""          # "cookie"（乐观路径）| "navigate"（真过验证）
        self.elapsed_s = 0.0

    # ── 上下文管理器 ──
    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *exc):
        await self.close()

    async def start(self):
        self.profile.mkdir(parents=True, exist_ok=True)
        kwargs = dict(headless=self.headless)
        if self.use_profile:
            kwargs["persistent_context"] = True
            kwargs["user_data_dir"] = str(self.profile)
        if self.locale:
            kwargs["locale"] = self.locale

        # 先抢 profile 锁，否则两个进程同时跑会各自卡到超时再 SIGTERM 死掉
        if self.use_profile:
            self._lock_cm = profile_lock(self.profile)
            self._lock_cm.__enter__()

        self.browser = await AsyncCamoufox(**kwargs).__aenter__()

        # 关键区别（已核对 async_api.py:120-122）：
        #   persistent_context=False → __aenter__ 返回 playwright Browser
        #   persistent_context=True  → 返回 BrowserContext（launch_persistent_context）
        # Browser 才有 .contexts[]，BrowserContext 没有。
        # 对 BrowserContext 调 .contexts 会 AttributeError，再调 .new_context()
        # 又是另一个 AttributeError —— 整条路都崩在创建页面这一步。
        try:
            obj = self.browser
            if hasattr(obj, "contexts"):          # 是 Browser
                self.context = obj.contexts[0] if obj.contexts else await obj.new_context()
            else:                                  # 是 BrowserContext
                self.context = obj
            self.page = self.context.pages[0] if self.context.pages \
                else await self.context.new_page()
        except Exception as e:
            await self.close()
            raise RuntimeError(f"创建页面失败: {e}") from e
        return self
    async def close(self):
        if self.browser is not None:
            try:
                await self.browser.__aexit__(None, None, None)
            except Exception:
                pass
            self.browser = None
        self.context = self.page = None

        # 必须等浏览器真正关掉才放锁：close() 是把 profile 刷盘的那一步
        if self._lock_cm is not None:
            try:
                self._lock_cm.__exit__(None, None, None)
            except Exception:
                pass
            self._lock_cm = None

    # ── 核心：过验证 ──
    async def solve(self) -> bool:
        t0 = time.monotonic()
        page = self.page

        # ── 乐观路径：profile 里已有 cf_clearance，可能一次导航就过 ──
        cached = await get_cf_cookie(self.context)
        if cached:
            expires = cached.get("expires")
            left = ""
            if isinstance(expires, (int, float)) and expires > 0:
                left = f"，剩余约 {(expires - time.time()) / 3600:.1f} 小时"
            self.log(f"  发现缓存 {CF_COOKIE}{left}（存在时间戳即可，不打印值）")
        else:
            self.log(f"  无缓存 {CF_COOKIE} —— 这次是冷启动，需要真正过一遍验证")

        self.log(f"\n→ 导航到 {self.entry}")
        try:
            await page.goto(self.entry, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            self.log(f"  导航异常（继续等）：{str(e)[:90]}")

        self.log(f"→ 等 Cloudflare（最多 {self.budget_s}s，判据 = 标题不含 "
                 f"{CHALLENGE_MARK!r}）")

        for tick in range(3, self.budget_s + 1, 3):
            await asyncio.sleep(3)
            title = await page_title(page)
            if title and CHALLENGE_MARK not in title:
                self.elapsed_s = time.monotonic() - t0
                self.passed_via = "cookie" if cached else "navigate"
                self.log(f"  [{tick}s] ✅ 通过（{'缓存 cookie' if cached else '实际过验证'}）"
                         f"  耗时 {self.elapsed_s:.1f}s")
                self.log(f"         标题: {title[:60]!r}")

                # 证明"上下文真的可用"，而不只是标题变了
                if await self.verify_context():
                    return True
                self.failure_reason = "标题已变正常，但页内 fetch 取不到数据（上下文不可用）"
                self.log(f"  ✗ {self.failure_reason}")
                return False

            self.log(f"  [{tick}s] 挑战中 {title[:45]!r}")

        self.failure_reason = (
            f"{self.budget_s}s 内未通过。判读：\n"
            f"      首页都过不去 = IP 被策略性拒绝（不是指纹问题）。\n"
            f"      对策：换 IP / 用参考项目里的代理，而不是调启动参数。"
        )
        self.log(f"  ✗ {self.failure_reason}")
        return False

    async def verify_context(self) -> bool:
        """在页内 fetch 一个「document 会被 403」的端点，确认上下文可用。

        这是本模块唯一可信的通过判据。标题变成正常只能说明 CF 放行了
        这次导航；能不能取到数据是另一回事。

        顺带读 Cf-Mitigated 头 —— 这是 CF 官方文档化的挑战信号，
        比猜正文文案可靠得多。
        """
        self.log("\n  自检：页内 fetch（document 会 403 的路径）")
        ok_any = False

        for label, path, headers in (
            ("RSC", PROBE_RSC, RSC_HEADERS),
            ("JSON", PROBE_JSON, {"accept": "application/json"}),
        ):
            url = f"{path}?_rsc=selfcheck" if headers is RSC_HEADERS else path
            try:
                r = await self.page.evaluate(
                    """
                    async ({p, h}) => {
                      try {
                        const r = await fetch(p, {headers: h, credentials:'include'});
                        const t = await r.text();
                        return {ok:true, status:r.status,
                                ct:(r.headers.get('content-type')||'').slice(0,32),
                                mitigated:r.headers.get('cf-mitigated')||'',
                                len:t.length, body:t.slice(0,400000)};
                      } catch(e) { return {ok:false, error:String(e).slice(0,120)}; }
                    }
                    """,
                    {"p": url, "h": headers},
                )
            except Exception as e:
                self.log(f"    {label:5s} 异常 {str(e)[:70]}")
                continue

            if not r.get("ok"):
                self.log(f"    {label:5s} ✗ {r.get('error')}")
                continue

            hits = len(MODEL_RE.findall(r.get("body", "")))
            good = r["status"] == 200 and hits > 0
            ok_any = ok_any or good

            # Cf-Mitigated: challenge 是官方信号，出现即说明被挑战了
            tag = ""
            if r.get("mitigated"):
                tag = f"  ← Cf-Mitigated: {r['mitigated']}（被挑战）"

            self.log(f"    {label:5s} status={r['status']} ct={r['ct']:32s} "
                     f"{r['len']:,}B 模型名命中={hits} {'✅' if good else '✗'}{tag}")

        return ok_any


# ─────────────────────────── 自测 / CLI ───────────────────────────

async def find_rsc_paths(page) -> list[str]:
    """从根 RSC 里提取可用路径，证明过验证之后能拿到什么。"""
    try:
        r = await page.evaluate(
            """
            async ({p, h}) => {
              try {
                const r = await fetch(p, {headers:h, credentials:'include'});
                return {status:r.status, body: await r.text()};
              } catch(e) { return {status:0, body:String(e)}; }
            }
            """,
            {"p": "/leaderboard?_rsc=discover", "h": RSC_HEADERS},
        )
    except Exception as e:
        return [f"异常: {e}"]

    if r["status"] != 200:
        return [f"根 RSC 状态 {r['status']}"]

    paths = sorted(set(re.findall(r'"(/leaderboard/[a-z0-9][a-z0-9/\-]*)"', r["body"])))
    clean = []
    for p in paths:
        rel = p[len("/leaderboard/"):]
        if rel.count("/") == 1 and p not in clean:
            clean.append(f"/leaderboard/{rel}")
    return clean


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--virtual", action="store_true",
                    help="headless='virtual'（Xvfb 虚拟屏，仅 Linux；比真无头更像有头）")
    ap.add_argument("--fresh", action="store_true", help="删掉 profile，验证冷启动")
    ap.add_argument("--url", default="https://arena.ai/zh")
    ap.add_argument("--wait", type=int, default=60, help="等 CF 的秒数")
    ap.add_argument("--find-rsc", action="store_true", help="探出可用 RSC 路径")
    ap.add_argument("--keep", type=int, default=5)
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.fresh and PROFILE_DIR.exists():
        shutil.rmtree(PROFILE_DIR, ignore_errors=True)
        print(f"已删除 profile: {PROFILE_DIR}\n")

    log = Log(OUT_DIR / "cf_solver.log")
    log("=" * 74)
    mode = "virtual(Xvfb)" if args.virtual else ("headless" if args.headless else "headed")
    log(f"Cloudflare 过验证自测   模式={mode}  fresh={args.fresh}")
    log("=" * 74)

    b = CFBrowser(headless=("virtual" if args.virtual else args.headless), entry=args.url,
                  budget_s=args.wait, log=log)
    try:
        await b.start()
        passed = await b.solve()

        log("\n" + "=" * 74)
        log("结果")
        log("=" * 74)
        log(f"  过关: {'✅ 是' if passed else '❌ 否'}"
            f"{f'（{b.passed_via}，{b.elapsed_s:.1f}s）' if passed else ''}")

        if passed:
            ck = await get_cf_cookie(b.context)
            log(f"  {CF_COOKIE}: {'✅ 已获取' if ck else '（本次未用到，可能 profile 里也没有）'}")

            if args.find_rsc:
                log("\n  可用路径（从根 RSC 提取）:")
                for p in await find_rsc_paths(b.page):
                    log(f"    {p}")

        if args.keep:
            log(f"\n浏览器保持 {args.keep} 秒...")
            await asyncio.sleep(args.keep)
    finally:
        await b.close()
        log.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
