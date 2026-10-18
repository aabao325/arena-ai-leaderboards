#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分类清单发现：把 arena.ai 到底有哪些榜**实测出来**

为什么需要这个：
  站点的导航结构里只暴露一部分榜（首页 pill 8 个 + 导航 10 条，合并 12 个），
  但实际存在的榜更多（旧版有 image-edit / image-to-video / video-edit）。
  纯靠导航会漏，纯靠猜会拿到空壳（`agent/alignment` 就是先例：
  路径存在但那是 hub 页）。

做法：
  1. 首页过 CF
  2. 拉根载荷 + 递归：从每个榜单页的 `arena.categories` 里挖出子分类，
     每个子分类的 dataSource 里带 arenaSlug + leaderboardSlug
  3. 按 arenaSlug/leaderboardSlug 拼出候选 URL，逐个 fetch 验证有没有数据行
  4. 输出实测清单 → categories.json

**这一步只要跑一次**，产出的清单可以固化用很久。官方加分类后重跑即可。

用法：
    python discover.py                  # 完整发现
    python discover.py --probe-only     # 只验证已知清单，不递归
    python discover.py --headless
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import rsc_schema as S
from cf_solver import CFBrowser, Log, RSC_HEADERS

HERE = Path(__file__).parent.resolve()
OUT = HERE / "_out"
RSC_DIR = OUT / "rsc"

HOME = "https://arena.ai/zh"
ROOT = "/leaderboard"

FETCH_JS = """
async ({p, h}) => {
  try {
    const r = await fetch(p, {headers: h, credentials:'include', cache:'no-store'});
    const t = await r.text();
    return {ok:true, status:r.status, len:t.length,
            mitigated:r.headers.get('cf-mitigated')||'', body:t};
  } catch(e) { return {ok:false, error:String(e).slice(0,160)}; }
}
"""

# 已知存在的路径。用来验证"存在性"，不当作权威清单。
SEED = [
    "/leaderboard/agent",
    "/leaderboard/agent/alignment",
    "/leaderboard/chat/text",
    "/leaderboard/chat/search",
    "/leaderboard/chat/vision",
    "/leaderboard/chat/document",
    "/leaderboard/code/webdev",
    "/leaderboard/code/image-to-webdev",
    "/leaderboard/image/text-to-image",
    "/leaderboard/image/image-edit",
    "/leaderboard/image/image-edit/single-image",
    "/leaderboard/image/image-edit/multi-image",
    "/leaderboard/video/text-to-video",
    "/leaderboard/video/image-to-video",
    "/leaderboard/video/video-edit",
]


def slug_of(href: str) -> str:
    rel = href[len("/leaderboard/"):] if href.startswith("/leaderboard/") else href.strip("/")
    return re.sub(r"[^a-z0-9]+", "-", rel.lower()).strip("-") or "root"


def child_paths(tree) -> list:
    """从 arena.categories 里挖子分类路径。

    每项形如 {"dataSource": {"arenaSlug": "agent", "leaderboardSlug": "code"}}，
    拼成 /leaderboard/<arenaSlug>/<leaderboardSlug>。
    这是站点自己声明的层级，比猜 URL 可靠。
    """
    out = []
    meta = S.find_arena_meta(tree)
    if not meta:
        return out
    for c in meta.get("categories") or []:
        if not isinstance(c, dict):
            continue
        ds = c.get("dataSource")
        if not isinstance(ds, dict):
            continue
        arena = ds.get("arenaSlug")
        lb = ds.get("leaderboardSlug")
        if not arena:
            continue
        if lb and lb not in ("all", "overall"):
            out.append(f"/leaderboard/{arena}/{lb}")
        else:
            out.append(f"/leaderboard/{arena}")
    return out


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--wait", type=int, default=60)
    ap.add_argument("--probe-only", action="store_true", help="只验证种子清单")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    log = Log(OUT / "discover.log")
    log("=" * 76)
    log(f"分类清单发现   headless={args.headless}  probe_only={args.probe_only}")
    log("=" * 76)

    b = CFBrowser(headless=args.headless, entry=HOME, budget_s=args.wait, log=log)
    try:
        await b.start()
        if not await b.solve():
            log("✗ 过 CF 失败")
            return 1
        await asyncio.sleep(3)

        seen = {}          # href -> {rows, arena, note}
        queue = list(SEED)

        if not args.probe_only:
            log("\n[1] 根载荷：取导航暴露出的一层")
            r = await b.page.evaluate(FETCH_JS,
                                      {"p": f"{ROOT}?_rsc=d0", "h": RSC_HEADERS})
            if r.get("ok") and r["status"] == 200:
                (RSC_DIR / "_root.txt").write_text(r["body"], encoding="utf-8")
                raw, _ = S.parse_flight(r["body"])
                tree = S.materialize(raw)
                for c in S.find_categories(tree):
                    h = c.get("href")
                    if h and h.startswith("/leaderboard/") and h not in queue:
                        queue.append(h)
                for p in S.find_pill_previews(tree):
                    h = p.get("href")
                    if h and h not in queue:
                        queue.append(h)
                log(f"    根载荷 {r['len']:,} 字节，初始队列 {len(queue)} 个")

        log(f"\n[2] 逐个验证（队列 {len(queue)} 个），同时递归挖子分类")
        while queue:
            href = queue.pop(0)
            if href in seen:
                continue

            r = await b.page.evaluate(FETCH_JS,
                                      {"p": f"{href}?_rsc=p{len(seen)}", "h": RSC_HEADERS})
            if not r.get("ok"):
                seen[href] = {"ok": False, "reason": r.get("error")}
                log(f"  ✗ {href}  网络错误")
                continue
            if r["status"] != 200:
                seen[href] = {"ok": False, "reason": f"HTTP {r['status']}"}
                log(f"  ✗ {href}  HTTP {r['status']}")
                continue

            try:
                raw, _ = S.parse_flight(r["body"])
                tree = S.materialize(raw)
                rows, meta = S.normalize_board(tree)
            except Exception as e:
                seen[href] = {"ok": False, "reason": f"解析: {str(e)[:40]}"}
                log(f"  ✗ {href}  解析异常")
                continue

            arena = meta.get("arena") or {}
            kids = child_paths(tree)
            new_kids = [k for k in kids if k not in seen and k not in queue]
            queue.extend(new_kids)

            if rows:
                (RSC_DIR / f"{slug_of(href)}.txt").write_text(r["body"], encoding="utf-8")
                seen[href] = {
                    "ok": True,
                    "rows": len(rows),
                    "arena_slug": arena.get("slug"),
                    "title": arena.get("title"),
                    "content_key": arena.get("content_key"),
                    "children": kids,
                    "scale": meta.get("score_scale"),
                }
                log(f"  ✓ {href}  {len(rows):>4d} 行  arena={arena.get('slug')}"
                    + (f"  子分类+{len(new_kids)}" if new_kids else ""))
            else:
                seen[href] = {
                    "ok": False,
                    "reason": "0 行（hub 页或空榜）",
                    "arena_slug": arena.get("slug"),
                    "title": arena.get("title"),
                    "children": kids,
                }
                log(f"  ⚠ {href}  0 行  arena={arena.get('slug')}"
                    + (f"  子分类+{len(new_kids)}" if new_kids else ""))

            await asyncio.sleep(1.0)

        # ── 汇总 ──
        good = {h: v for h, v in seen.items() if v.get("ok")}
        bad = {h: v for h, v in seen.items() if not v.get("ok")}

        log("\n" + "=" * 76)
        log(f"可用榜单 {len(good)} 个 / 探测 {len(seen)} 个")
        log("=" * 76)
        for h, v in sorted(good.items(), key=lambda kv: -kv[1]["rows"]):
            log(f"  ✓ {v['rows']:>4d}行  {slug_of(h):34s} {h}")

        if bad:
            log(f"\n不可用 {len(bad)} 个（路径存在但无数据，别当榜用）:")
            for h, v in sorted(bad.items()):
                log(f"  ✗ {slug_of(h):34s} {h}  ← {v.get('reason')}")

        cats = {
            "discovered_at": datetime.now(timezone.utc).isoformat(),
            "boards": [
                {"slug": slug_of(h), "href": h, "rows": v["rows"],
                 "arena_slug": v.get("arena_slug"), "title": v.get("title")}
                for h, v in sorted(good.items(), key=lambda kv: -kv[1]["rows"])
            ],
            "empty": [{"slug": slug_of(h), "href": h, "reason": v.get("reason")}
                      for h, v in sorted(bad.items())],
        }
        (HERE / "categories.json").write_text(
            json.dumps(cats, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"\n已写入 categories.json")

        return 0
    finally:
        await b.close()
        log.close()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        pass
