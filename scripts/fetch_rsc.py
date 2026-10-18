#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
arena.ai 榜单抓取 —— 生产脚本（RSC 入口）

替换原来的 Jina Reader 链路。核心事实（2026-10-16 实测）：

    GET /leaderboard/chat/text                     → 403  document 被 CF 拦
    GET /leaderboard/chat/text?_rsc=x  + RSC: 1    → 200  text/x-component
    GET /leaderboard?_rsc=x            + RSC: 1    → 200  首页载荷（含分类清单）

**CF 拦 document 导航，不拦同源 XHR。** 所以流程是：首页过 CF →
页内 fetch RSC → 解析。榜单页文档从头到尾不需要打开。

────── 官方改版时这里为什么不用跟着改 ──────

  分类清单   ← 从首页载荷的 pills[] 读（站点自己声明 href/label/分组）
  榜单行位置 ← 按"行的形状"找（有模型名 + 有分数 + 有名次），不认键名
  字段映射   ← 别名表，取不到就给 null（schema 允许 null，不允许缺键）
  分数量纲   ← 按量级推断（全在 [-1,1] 内 → 比率型，放大 10000）

官方把 entries 改名、加新分类、调 URL，都会自动跟上。

用法：
    python fetch_rsc.py                    # 全量
    python fetch_rsc.py --only chat/text   # 单个，快速验证
    python fetch_rsc.py --headless         # 无头
    python fetch_rsc.py --dry-run          # 只抓不发，打印统计
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import rsc_schema as S
from cf_solver import CFBrowser, Log, RSC_HEADERS

HERE = Path(__file__).parent.resolve()
# 仓库根：scripts/ 的上一级。数据写到 <repo_root>/data/，和前端/代理的约定一致
# （代理读 raw.githubusercontent.com/<repo>/main/data，latest.json 在 data/ 顶层）。
REPO_ROOT = HERE.parent
OUT = HERE / "_out"                     # 运行产物：日志、原始载荷，不入库
DATA_ROOT = REPO_ROOT / "data"          # 数据快照，要入库

HOME = "https://arena.ai/zh"
HOME_BOARD = "/leaderboard"

# 官方首页 pill 的 subLabel 形如 "Agent | Overall" / "Text | Overall"。
# 左侧是分组，按这个建导航层级。未知分组自动落到 extra。
GROUP_LABELS = {
    "agent": ("agent", "智能体"),
    "text": ("chat", "对话"),
    "chat": ("chat", "对话"),
    "code": ("code", "代码"),
    "image": ("image", "图像"),
    "video": ("video", "视频"),
    "document": ("chat", "对话"),
    "search": ("chat", "对话"),
    "vision": ("chat", "对话"),
}

FETCH_JS = """
async ({p, h}) => {
  try {
    const r = await fetch(p, {headers: h, credentials:'include', cache:'no-store'});
    const t = await r.text();
    return {ok:true, status:r.status,
            ct:(r.headers.get('content-type')||'').slice(0,40),
            mitigated:r.headers.get('cf-mitigated')||'',
            len:t.length, body:t};
  } catch(e) { return {ok:false, error:String(e).slice(0,200)}; }
}
"""


def prune_snapshots(root: Path, keep: int = 10, log=None) -> list:
    """只保留最近 N 份日期快照，其余删掉。

    为什么要在抓取脚本里做：data/ 每抓一次多一个日期目录，几年下来
    会堆成几百份、几百 MB。仓库体积是有限资源，而历史快照的边际价值很低
    （页面只用 latest 指向的那一份）。

    **只删 YYYY-MM-DD 形状的目录**，别的文件/目录一律不碰 ——
    这个函数会在用户的数据仓库里执行 rm，误删代价不可逆，所以判据必须窄。
    """
    if keep < 1:
        return []
    if not root.is_dir():
        return []

    days = []
    for d in root.iterdir():
        if d.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", d.name):
            days.append(d)
    days.sort(key=lambda p: p.name)          # 日期目录，名字排序即时间排序

    doomed = days[:-keep] if len(days) > keep else []
    removed = []
    for d in doomed:
        try:
            shutil.rmtree(d)
            removed.append(d.name)
        except Exception as e:
            if log:
                log(f"  ⚠ 删除失败 {d.name}: {str(e)[:60]}")
    return removed


def slug_from_href(href: str) -> str:
    """`/leaderboard/code/image-to-webdev` → `code-image-to-webdev`"""
    rel = href[len("/leaderboard/") if href.startswith("/leaderboard/") else "/leaderboard":]
    rel = rel.strip("/")
    return re.sub(r"[^a-z0-9]+", "-", rel.lower()) or "leaderboard"


def group_of(pill: dict, href: str = "") -> tuple[str, str]:
    """定分组。三级兜底，从最可信到最兜底：

    1. `sub_label`  形如 "Text | Overall"      ← 首页 pill，最可信
    2. href 的第一段 `/leaderboard/chat/xxx`    ← 没有 sub_label 时（导航树里的项）
    3. arenaSlug    形如 "text-to-image"        ← 最后兜底
    """
    sub = (pill.get("sub_label") or "").split("|")[0].strip().lower()
    if sub:
        for key, val in GROUP_LABELS.items():
            if key in sub:
                return val

    # 从 href 推断 —— 导航树里的项没有 sub_label，但路径本身就是分组
    if href.startswith("/leaderboard/"):
        seg = href[len("/leaderboard/"):].split("/")[0].lower()
        if seg in GROUP_LABELS:
            return GROUP_LABELS[seg]
        # `image-edit` / `text-to-image` 这类要按前缀再匹一次
        for key, val in GROUP_LABELS.items():
            if seg.startswith(key):
                return val

    arena = (pill.get("arena_slug") or "").lower()
    for key, val in GROUP_LABELS.items():
        if key == arena or arena.startswith(key):
            return val
    return ("extra", "其他")


def clean_board_title(pill: dict) -> str:
    """`Best Text Models` 这种营销说法太长，取 badgeLabel 更紧凑。"""
    return (pill.get("badge_label") or pill.get("label") or pill.get("id") or "Board").strip()


async def grab(page, path: str, tag: str):
    url = f"{path}?_rsc={tag}"
    return f"{path}?_rsc={tag}", await page.evaluate(FETCH_JS, {"p": url, "h": RSC_HEADERS})


def slug_for_pill(p: dict) -> str:
    """按 href 算 slug，同一 href 的多个 pill 用 sub_label 区分。

    agent 的四个子榜 href 全是 `/leaderboard/agent`，必须靠 sub_label
    拆成 agent / agent-code / agent-work；其余榜 sub_label 的右半段
    通常是 "Overall"，和 href 末段重复，不加后缀。
    """
    href = p.get("href") or ""
    slug = slug_from_href(href)
    sub = (p.get("sub_label") or "").split("|")[-1].strip().lower()
    seg = re.sub(r"[^a-z0-9]+", "-", sub).strip("-")
    if seg and seg not in ("overall", "") and seg != slug.split("-")[-1]:
        slug = f"{slug}-{seg}"
    return slug


def make_slugs(previews):
    """给每个榜分配稳定的 slug，并保证唯一。

    难点在于官网导航和实际抓取地址不是一一对应的：
    三个 agent pill（Overall / Code / Work）在导航里 href 全是
    `/leaderboard/agent`，但真正的榜在 `/leaderboard/agent/code` 等
    子路径上（见 rsc_schema.sub_boards / expand_sub_boards）。

    slug 必须**稳定**：它是数据文件名，也是 URL 里 ?leaf= 的值。
    换名字等于把老链接全打死，所以规则一旦定下就别动。
    """
    out = []
    used_href = {}
    for p in previews:
        href = p["href"]
        base = slug_from_href(href)
        if href not in used_href:
            used_href[href] = True
            slug = base
        else:
            sub = (p.get("sub_label") or "").split("|")[-1].strip().lower()
            seg = re.sub(r"[^a-z0-9]+", "-", sub).strip("-") or (p.get("id") or "alt")
            slug = f"{re.sub(r'[^a-z0-9]+', '-', base).strip('-')}-{seg}"
        n = 2
        orig = slug
        while any(x["slug"] == slug for x in out):
            slug = f"{orig}-{n}"
            n += 1
        g, gl = group_of(p, href)
        # 已知清单带的分组比推断可靠（像 chat/vision 这种导航里没 sub_label）
        if g == "extra" and p.get("_group"):
            g, gl = p["_group"]
        title = clean_board_title(p)
        if title in ("Board", slug) or not title:
            title = slug.replace("-", " ").title()
        out.append({**p, "slug": slug, "group": g, "group_label": gl,
                    "title": title})
    return out


def expand_sub_boards(tree, parent):
    """把一个榜的 arena.categories 展开成若干子榜。

    Agent Arena 的四个子榜在导航里是同一行 href（`/leaderboard/agent`），
    只按 href 抓会拿到**三份完全相同的数据**（实测 52 行 × 3）。
    真正的地址靠 `dataSource.leaderboardSlug` 区分：

        overall → /leaderboard/agent          （leaderboardSlug = all）
        code    → /leaderboard/agent/code     （= code）
        chat    → /leaderboard/agent/consumer （= consumer）
        work    → /leaderboard/agent/gdp      （= gdp）

    返回 [] 表示这个榜没有子榜（普通榜都是这样）。
    """
    subs = S.sub_boards(tree)
    if len(subs) <= 1:
        return []

    # 只对 agent 这类"多个并列主榜"展开。
    # 普通榜的 categories 是**同一页面的筛选维度**（chat/text 有 29 个：Math /
    # Coding / Chinese / French…），展开会变成几十个榜，把首页撑爆，而且
    # 它们多数没有 dataSource（只能页内切换，拼不出独立 URL）。
    # agent 的 4 个子榜才是真的并列榜，且都带 dataSource。
    arena_slug = (S.find_arena_meta(tree) or {}).get("slug") or ""
    if arena_slug != "agent":
        return []

    base_title = parent.get("title") or parent.get("group_label") or ""
    # 父级标题是"Best Overall / Best Coding Agents"这类营销短语，
    # 拼上子榜名会变成 "Best Overall · Code"，读着不对。
    # 子榜标题用官方自己的 category.title（Overall / Code / Chat / Work），
    # 前缀只留分组名（智能体 / Agent），得到 "智能体 · Code" 这种。
    group_label = parent.get("group_label") or ""
    out = []
    for s in subs:
        title = (s.get("title") or s.get("key") or "").strip()
        if group_label and title:
            label = f"{group_label} · {title}"
        else:
            label = title or base_title
        out.append({
            **parent,
            "href": s["href"],
            "slug": slug_from_href(s["href"]),
            "title": label,
            "icon": parent.get("icon") or s.get("icon"),
            "_expanded_from": parent.get("slug"),
        })
    return out


# 已知存在、但首页导航不一定暴露的榜。
# 每次运行都会实测一遍：200 且有行才采用，否则记为失败（不猜）。
#
# 为什么需要这份清单：官网首页只推 8 个 pill，导航树这次的快照只暴露出 7 项，
# 而 chat/vision、chat/search、chat/document 这些榜客观上还在。
# 靠导航发现会漏，靠猜会拿到空壳，所以"已知 + 实测"最稳。
KNOWN_BOARDS = [
    ("/leaderboard/chat/text", "chat", "对话"),
    ("/leaderboard/chat/search", "chat", "对话"),
    ("/leaderboard/chat/vision", "chat", "对话"),
    ("/leaderboard/chat/document", "chat", "对话"),
    ("/leaderboard/code/webdev", "code", "代码"),
    ("/leaderboard/code/image-to-webdev", "code", "代码"),
    ("/leaderboard/image/text-to-image", "image", "图像"),
    ("/leaderboard/image/image-edit", "image", "图像"),
    ("/leaderboard/video/text-to-video", "video", "视频"),
    ("/leaderboard/video/image-to-video", "video", "视频"),
    ("/leaderboard/video/video-edit", "video", "视频"),
    ("/leaderboard/agent", "agent", "智能体"),
    ("/leaderboard/agent/alignment", "agent", "智能体"),
]


def merge_sources(pill_previews, all_categories):
    """合并发现来源，补齐官网首页没暴露但客观存在的榜。

    三个来源，优先级从高到低：
      1. 首页 pill     —— 带 label / 分组 / 前三名预览（官网重点推荐）
      2. 导航树 href   —— 覆盖面更广，但元数据少
      3. 已知清单      —— 兜底；每次都会实测，无效的会被标成失败

    以 href 为键合并，pill 的元数据覆盖其余来源。
    """
    by_href = {}

    # 3. 已知清单先垫底
    for href, gkey, glabel in KNOWN_BOARDS:
        by_href[href] = {
            "href": href,
            "label": None,
            "badge_label": None,
            "sub_label": None,
            "icon": None,
            "arena_slug": None,
            "score_unit": None,
            "pricing_unit": None,
            "top_models": [],
            "_group": (gkey, glabel),
        }

    # 2. 导航树
    for c in all_categories:
        href = c.get("href")
        if not href or not href.startswith("/leaderboard/"):
            continue
        base = by_href.get(href, {"href": href, "top_models": []})
        by_href[href] = {**base, **{k: v for k, v in c.items() if v}, "_group": None}

    # 1. pill 覆盖（同 href 的多个 pill 各占一项，后面的循环靠 slug 去重）
    extra = []
    for p in pill_previews:
        href = p["href"]
        if href in by_href:
            by_href[href] = {**by_href[href], **p,
                             "_group": by_href[href].get("_group")}
        else:
            extra.append(dict(p))

    return list(by_href.values()) + extra


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--only", default="", help="只抓指定 slug，逗号分隔")
    ap.add_argument("--dry-run", action="store_true", help="不写文件，只打印")
    ap.add_argument("--wait", type=int, default=60)
    ap.add_argument("--out-dir", default="", help="输出目录，默认 _out/data/<日期>")
    ap.add_argument("--keep", type=int, default=10,
                    help="只保留最近 N 份日期快照（默认 10，0 表示不清理）")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    log = Log(OUT / "fetch_rsc.log")
    started = datetime.now(timezone.utc)
    date_str = started.strftime("%Y-%m-%d")
    data_dir = Path(args.out_dir) if args.out_dir else DATA_ROOT / date_str
    rsc_dir = OUT / "rsc"
    rsc_dir.mkdir(parents=True, exist_ok=True)

    log("=" * 76)
    log(f"arena.ai 榜单抓取   {started:%Y-%m-%d %H:%M:%S} UTC   headless={args.headless}")
    log("=" * 76)

    b = CFBrowser(headless=args.headless, entry=HOME, budget_s=args.wait, log=log)
    boards = []
    try:
        await b.start()
        if not await b.solve():
            log("\n✗ 过 CF 失败，无法继续")
            log("  首页都过不去 = IP 被策略性拒绝，换网络/代理，别调启动参数")
            return 1

        await asyncio.sleep(3)

        # ── 1. 首页载荷 → 官方自述的分类清单 ──
        log("\n[1] 拉首页载荷，读官方分类清单")
        _, home = await grab(b.page, HOME_BOARD, "home")
        if not home.get("ok") or home.get("status") != 200:
            log(f"  ✗ 首页载荷失败: {home.get('status')} {home.get('error','')}")
            return 1

        (rsc_dir / "_home.txt").write_text(home["body"], encoding="utf-8")
        raw, _ = S.parse_flight(home["body"])
        tree = S.materialize(raw)

        # 首页 pill 自带数据 —— 官网首页就是靠它渲染的。
        #
        # 关键区分（实测得出的）：
        #   · 大部分 pill 只带截断的预览（entries 十几条），完整数据要去各自页面抓
        #   · **agent 各子榜没有独立 URL**，完整数据（各 52 行）直接躺在 pill 里，
        #     `/leaderboard/agent/consumer`、`/leaderboard/agent/gdp` 实测都是 404
        #
        # 所以按 entries 长度分流：够长就当"内联榜"直接入库，不再抓 URL。
        previews = S.find_pill_previews(tree, full=True)
        all_cats = S.find_categories(tree)

        INLINE_MIN_ROWS = 20
        inline = []      # 数据已在首页载荷里，无需抓取
        to_fetch = []    # 需要单独抓的

        # 只有 agent 的并列子榜走内联。
        #
        # 依据：首页 pill 里的 entries 对大多数榜是**截断预览**
        # （chat-text 只给 80 条，实际有 416 条），拿它当完整数据会丢一半行。
        # 而 agent 的子榜没有独立 URL（/leaderboard/agent/code 实测 404），
        # pill 里的 52 条就是全量 —— 这两类必须区别对待。
        for p in previews:
            rows = p.get("top_models") or []
            href = p.get("href") or ""
            # agent 判定要看两个来源：arena_slug（多数 pill 有）和 href
            # （alignment 的 pill 没有 arena_slug，但路径是 /leaderboard/agent/alignment）
            is_agent = ((p.get("arena_slug") or "").lower() == "agent"
                        or href.startswith("/leaderboard/agent"))
            # agent 类一律优先用内联数据（理由见下方 inline_hrefs 的注释）：
            # 服务端对 agent 的子路径都返回同一份主榜，只有首页 pill 才按子榜算过。
            # 唯一的例外是 pill 里压根没数据（entries 为空），那才退回抓取。
            if is_agent and len(rows) >= INLINE_MIN_ROWS:
                inline.append(p)
            else:
                to_fetch.append(p)

        boards = make_slugs(merge_sources(to_fetch, all_cats))

        log(f"  首页载荷 {home['len']:,} 字节")
        log(f"  pill {len(previews)} 个"
            f"（其中 {len(inline)} 个自带完整数据，{len(to_fetch)} 个需单独抓）"
            f" + 导航 {len(all_cats)} 项 → 待抓 {len(boards)} 个榜")

        if not boards and not inline:
            log("  ✗ 没从首页载荷里解析出任何榜 —— 官方可能改了首页结构")
            log("    对策：看 _out/rsc/_home.txt，把新的分类位置补进 find_pill_previews()")
            return 1

        log("\n  分类清单（官方声明）:")
        for x in inline:
            log(f"    {x['group'] if 'group' in x else '?':6s} "
                f"{slug_from_href(x['href']):34s} {x['href']}  ← 内联 {len(x['top_models'])} 行")
        for x in boards:
            log(f"    {x['group']:6s} {x['slug']:34s} {x['href']}")

        if args.only:
            want = [s.strip() for s in args.only.split(",") if s.strip()]
            boards = [x for x in boards if any(
                w == x["slug"] or w == x["href"] or x["href"].endswith("/" + w)
                for w in want)]
            inline = [x for x in inline if any(
                w == slug_from_href(x["href"]) or w == x["href"] for w in want)]
            log(f"\n  --only 过滤后: 待抓 {len(boards)} 个，内联 {len(inline)} 个")
            if not boards and not inline:
                log("  ✗ --only 没匹配到任何榜")
                return 1

        # ── 2. 逐榜取数（遇到有子榜的就地展开）──
        log(f"\n[2] 逐榜 fetch RSC（初始 {len(boards)} 个）")
        data_dir.mkdir(parents=True, exist_ok=True)
        results = []
        done_hrefs = set()      # 防止展开出的子榜被重复抓
        # 内联优先：同一 href 既可能出现在内联榜里、又可能被导航/已知清单带进待抓队列。
        #
        # 为什么内联一定优先于抓取：agent 那几个并列子榜在服务端是**同一个页面**，
        # 抓 `/leaderboard/agent/code`、`/leaderboard/agent/alignment` 拿回来的
        # 都是主榜（实测 avg 全部 1888.0、#1 全是同一个模型），只有首页 pill
        # 里的 entries 才按子榜分开算过（Overall 1888 / Code 1993 / Work 1797）。
        # 所以对 agent 类，内联数据是**唯一正确的**，抓取反而会把它覆盖成错误值。
        inline_hrefs = {p["href"] for p in inline}
        queue = [b for b in boards if b["href"] not in inline_hrefs]

        # ── 2a. 先落盘"内联榜"（数据就在首页载荷里，无需抓取）──
        for p in inline:
            href = p["href"]
            totals = p.get("top_models") or []
            raw_rows = [t["_raw"] for t in totals if t.get("_raw")]
            if not raw_rows:
                continue

            unit = p.get("score_unit")
            # 以**数据实际量级**为准，不看 score_unit。
            # 反例（实测）：agent alignment 的 unit 是 "index"，但数值是 87.94
            # （0–100 量级）；按 unit 映射会以为它是 0–1 而放大一万倍，
            # 变成 879430。infer_scale 只看数值，不会犯这个错。
            scale, scale_note = S.infer_scale(raw_rows)
            allv = [v for v in (S._score_of(r) for r in raw_rows) if v is not None]
            in_signals = bool(allv) and min(allv) < 0

            rows = [S.to_schema_row(r, unit, scale, in_signals) for r in raw_rows]
            rows = [r for r in rows if r["model"]]
            rows.sort(key=lambda r: (r["rank"] is None, r["rank"] or 10**9))

            slug = slug_for_pill(p)

            g, gl = group_of(p, href)
            title = (p.get("badge_label") or p.get("label") or slug)
            if p.get("sub_label"):
                title = p["sub_label"].replace(" | ", " · ")

            doc = {
                "meta": {
                    "leaderboard": slug,
                    "source_url": f"https://arena.ai{href}",
                    "fetched_at": started.isoformat(),
                    "last_updated": None,
                    "model_count": len(rows),
                    "title": title,
                    "title_zh": gl,
                    "sub_label": p.get("sub_label"),
                    "group": g,
                    "icon": p.get("icon"),
                    "score_unit": unit,
                    "score_scale": scale,
                    "score_in_signals": in_signals,
                    "inline_from_home": True,
                },
                "models": rows,
            }

            if not args.dry_run:
                (data_dir / f"{slug}.json").write_text(
                    json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

            log(f"\n  [内联] ✓ {slug}: {len(rows)} 行  scale={scale}"
                f"  signals={in_signals}")
            if rows:
                log(f"      #1 {rows[0]['model']}  score={rows[0]['score']}")
            results.append(({
                "slug": slug, "href": href, "title": title, "group": g,
                "group_label": gl, "sub_label": p.get("sub_label"),
                "icon": p.get("icon"), "score_unit": unit, "pricing_unit": None,
            }, doc, len(rows), "ok"))

        while queue:
            bd = queue.pop(0)
            href = bd["href"]
            if href in done_hrefs:
                continue
            done_hrefs.add(href)
            i = len(done_hrefs)
            total = len(done_hrefs) + len(queue)

            url, res = await grab(b.page, href, f"b{i}")

            if not res.get("ok"):
                log(f"\n  [{i}/{total}] ✗ {bd['slug']}: {res.get('error')}")
                results.append((bd, None, 0, "网络错误"))
                continue
            if res.get("status") != 200:
                extra = f" Cf-Mitigated={res['mitigated']}" if res.get("mitigated") else ""
                log(f"\n  [{i}/{total}] ✗ {bd['slug']}: HTTP {res['status']}{extra}")
                results.append((bd, None, 0, f"HTTP {res['status']}"))
                continue

            body = res["body"]
            (rsc_dir / f"{bd['slug']}.txt").write_text(body, encoding="utf-8")

            try:
                raw2, _ = S.parse_flight(body)
                tree2 = S.materialize(raw2)
                rows, meta = S.normalize_board(tree2)
            except Exception as e:
                log(f"\n  [{i}/{total}] ✗ {bd['slug']}: 解析异常 {str(e)[:70]}")
                results.append((bd, None, 0, "解析异常"))
                continue

            if not rows:
                log(f"\n  [{i}/{total}] ⚠ {bd['slug']}: 0 行（{meta['discovery_note'][:50]}）")
                results.append((bd, None, 0, "0 行"))
                continue

            # 行数校验：站点自述的 totalModels 和解析结果必须对上。
            # 少读几行不会报错，只会让榜单悄悄变短 —— 必须主动比。
            declared = meta.get("model_count_upstream")
            short = (declared is not None and len(rows) < declared)
            if short:
                log(f"      ⚠ 行数不符：站点自述 {declared}，解析出 {len(rows)}"
                    f"（缺 {declared - len(rows)} 行，解析器可能漏了结构）")

            # 有子榜就排队继续抓。注意：agent 的并列子榜**不走这条路** ——
            # 它们的 URL 是 404，数据在首页 pill 里，已由 [2a] 处理。
            # 这里保留是为了将来官方真的拆出独立 URL 时能自动跟上。
            subs = expand_sub_boards(tree2, bd)
            if subs:
                log(f"      ↩ 有 {len(subs)} 个子榜，加入队列: "
                    + ", ".join(s["slug"] for s in subs))
                queue.extend(subs)

            doc = {
                "meta": {
                    "leaderboard": bd["slug"],
                    "source_url": f"https://arena.ai{bd['href']}",
                    "fetched_at": started.isoformat(),
                    "last_updated": meta.get("last_updated"),
                    "model_count": len(rows),
                    "model_count_upstream": declared,
                    # 以下是**扩展字段**，给前端渲染用
                    "title": bd["title"],
                    "title_zh": bd["group_label"],
                    "sub_label": bd.get("sub_label"),
                    "group": bd["group"],
                    "icon": bd.get("icon"),
                    "score_unit": meta.get("score_unit"),
                    "score_scale": meta.get("score_scale"),
                    "score_in_signals": meta.get("score_in_signals"),
                },
                "models": rows,
            }

            if not args.dry_run:
                (data_dir / f"{bd['slug']}.json").write_text(
                    json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

            log(f"\n  [{i}/{total}] ✓ {bd['slug']}: {len(rows)} 行  "
                f"scale={meta['score_scale']}  lastUpdated={meta.get('last_updated') or '—'}")
            if rows:
                log(f"      #1 {rows[0]['model']}  score={rows[0]['score']}")
            results.append((bd, doc, len(rows), "ok"))

            await asyncio.sleep(1.2)   # 别把站点打急

        # ── 3. 索引：**前端完全靠它渲染**，这是抗改版的关键产物 ──
        #
        # 索引里带上首页需要的全部内容（分组、每类前三名、分值单位），
        # 前端不再写死 GROUPS 常量。官方增减分类时：
        #   抓取脚本自动发现 → 写进 _index.json → 前端自动多出一张卡
        # 一行前端代码都不用改。
        ok_boards = [bd for bd, doc, n, st in results if st == "ok"]

        groups = []
        seen_g = {}
        for bd, doc, n, st in [(b, d, c, s) for b, d, c, s in results if s == "ok"]:
            g = bd["group"]
            if g not in seen_g:
                seen_g[g] = {"key": g, "label": bd["group_label"], "boards": []}
                groups.append(seen_g[g])
                # 分组内部按"重要性"排：带 top 预览的（官网重点推荐）在前
            seen_g[g]["boards"].append({
                "slug": bd["slug"],
                "title": bd["title"],
                "sub_label": bd.get("sub_label"),
                "icon": bd.get("icon"),
                "score_unit": bd.get("score_unit"),
                "pricing_unit": bd.get("pricing_unit"),
                "score_scale": (doc or {}).get("meta", {}).get("score_scale", 1),
                "model_count": n,
                # 前三名从**最终入库的数据**里取，而不是从 pill 预览取。
                # 理由：pill 只覆盖首页推荐的 8 个榜，其余十来榜没有预览；
                # 而且内联榜的预览和落盘数据是同一份，取值口径要统一。
                # 前端首页一次请求即可渲染，不必再拉十几个榜。
                "top_models": [
                    {
                        "model": r["model"],
                        "vendor": r["vendor"],
                        "score": r["score"],
                        "rank": r["rank"],
                        "license": r["license"],
                    }
                    for r in ((doc or {}).get("models") or [])[:5]
                ],
            })

        # 组内排序：有预览的排前面，否则保持抓取顺序
        for g in groups:
            g["boards"].sort(key=lambda b: 0 if b["top_models"] else 1)

        # 失败清单也要写进索引：前端可以显式提示"这类暂缺"，
        # 而不是像以前那样用陈旧快照静静顶替（那是 11/12 分类过期的成因）
        failed = [{"slug": bd["slug"], "title": bd["title"], "href": bd["href"], "reason": st}
                  for bd, doc, n, st in results if st != "ok"]

        index = {
            "date": date_str,
            "fetched_at": started.isoformat(),
            "board_count": len(ok_boards),
            "groups": groups,
            "failed": failed,
        }
        if not args.dry_run:
            (data_dir / "_index.json").write_text(
                json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
            # latest 指针：前端靠 fetched_at 判断是否有新版本（同一天内再抓也能被发现）。
            # path 不带尾斜杠 —— 前端拼 `${path}/${slug}.json`，带斜杠会出现 `//`，
            # 而且旧版 latest.json 就是 "2026-10-15" 这种形状，保持一致。
            (data_dir.parent / "latest.json").write_text(
                json.dumps({"date": date_str, "path": date_str,
                            "fetched_at": started.isoformat(),
                            "board_count": len(ok_boards),
                            "failed_count": len(failed)},
                           ensure_ascii=False, indent=1), encoding="utf-8")

        # ── 4. 汇总 ──
        log("\n" + "=" * 76)
        log("汇总")
        log("=" * 76)
        okn = 0
        for bd, doc, n, st in results:
            mark = "✅" if st == "ok" else "❌"
            okn += 1 if st == "ok" else 0
            log(f"  {mark} {bd['slug']:28s} {n:>4d} 行  {st}")

        total = len(results)
        log(f"\n  {okn}/{total} 个榜成功")
        if total and okn < total:
            log(f"  ⚠ 有 {total - okn} 个失败。**不要用旧快照顶替** ——")
            log(f"    这就是此前 11/12 分类静默用 4 天前数据的成因。")
            log(f"    失败就让它缺，前端显示缺失比显示陈数据诚实。")

        if not args.dry_run:
            log(f"\n  输出目录: {data_dir}")

            # ── 5. 清理：只保留最近 N 份日期快照 ──
            if args.keep > 0:
                root = data_dir.parent
                before = len([d for d in root.iterdir()
                              if d.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", d.name)])
                removed = prune_snapshots(root, args.keep, log)
                if removed:
                    log(f"\n  清理旧快照: 删除 {len(removed)} 份"
                        f"（{removed[0]} … {removed[-1]}），保留最近 {args.keep} 份")
                    log(f"  现有快照: {before - len(removed)} 份")
                else:
                    log(f"\n  快照 {before} 份，未超过保留上限 {args.keep}，无需清理")
        return 0 if okn else 1

    finally:
        await b.close()
        log.close()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        pass
