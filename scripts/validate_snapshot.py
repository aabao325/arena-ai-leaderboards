#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
校验数据快照的结构完整性

跑在 CI 里（.github/workflows/fetch.yml），也可以本地手动跑：
    python scripts/validate_snapshot.py

校验什么、为什么：

  1. latest.json 指向的快照目录存在，且里面有 _index.json
     —— 前端首页完全靠 _index.json 渲染。缺了它会退回静态分类，
        页面看着能用，但新加的分类不会出现。这种"看起来正常"的故障最难发现。

  2. _index.json 的每个 board 都有对应的 <slug>.json
     —— 索引里列了但文件缺失，前端点进去会 404。

  3. 每个榜单文件符合 15 键 schema，且 model_count 与实际行数一致
     —— 少几行不会报错，只会让榜单静默变短。

  4. score / ci_low / ci_high 的值域合法
     —— schema 要求 ci >= 0；分数为负的分类必须整榜走 signals。

任何一项不过就以非 0 退出，让 CI 报警。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

REQUIRED_KEYS = {
    "rank", "rank_spread", "model", "model_url", "vendor", "license",
    "score", "ci_low", "ci_high", "preliminary", "votes",
    "price_prompt", "price_completion", "context_length", "signals",
}

errors: list[str] = []
warnings: list[str] = []


def fail(msg: str):
    errors.append(msg)


def warn(msg: str):
    warnings.append(msg)


def check_board(path: Path, slug: str):
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        fail(f"{path.name}: 不是合法 JSON（{e}）")
        return

    models = doc.get("models")
    if not isinstance(models, list) or not models:
        fail(f"{path.name}: models 不是非空数组")
        return

    meta = doc.get("meta") or {}
    if meta.get("leaderboard") != slug:
        fail(f"{path.name}: meta.leaderboard={meta.get('leaderboard')!r} 与文件名 {slug!r} 不一致")

    if meta.get("model_count") != len(models):
        fail(f"{path.name}: model_count={meta.get('model_count')} 但实际 {len(models)} 行")

    for i, m in enumerate(models):
        missing = REQUIRED_KEYS - set(m)
        if missing:
            fail(f"{path.name} 第 {i} 行缺键: {sorted(missing)}")
            break
        extra = set(m) - REQUIRED_KEYS
        if extra:
            warn(f"{path.name} 第 {i} 行有多余键: {sorted(extra)}")

        for k in ("ci_low", "ci_high"):
            v = m.get(k)
            if v is not None and (not isinstance(v, int) or v < 0):
                fail(f"{path.name} 第 {i} 行 {k}={v!r} 非法（要求整数且 >= 0）")
                break

        rs = m.get("rank_spread")
        if rs is not None and (not isinstance(rs, list) or len(rs) != 2):
            fail(f"{path.name} 第 {i} 行 rank_spread={rs!r} 非法（要求 2 元素数组或 null）")


def main() -> int:
    if not DATA.is_dir():
        print(f"✗ 找不到数据目录 {DATA}")
        return 1

    latest_path = DATA / "latest.json"
    if not latest_path.exists():
        fail("缺少 data/latest.json")
        snap = None
    else:
        try:
            snap = json.loads(latest_path.read_text(encoding="utf-8"))
        except Exception as e:
            fail(f"latest.json 不是合法 JSON（{e}）")
            snap = None

    if snap:
        day = snap.get("path") or snap.get("date")
        if not day:
            fail("latest.json 没有 path/date")
        else:
            day_dir = DATA / day
            if not day_dir.is_dir():
                fail(f"latest.json 指向的目录不存在: data/{day}")
            else:
                idx_path = day_dir / "_index.json"
                if not idx_path.exists():
                    fail(f"data/{day}/_index.json 缺失 —— 前端首页将退回静态分类")
                else:
                    try:
                        idx = json.loads(idx_path.read_text(encoding="utf-8"))
                    except Exception as e:
                        fail(f"_index.json 不是合法 JSON（{e}）")
                        idx = None

                    if idx:
                        boards = [b for g in (idx.get("groups") or [])
                                  for b in (g.get("boards") or [])]
                        if not boards:
                            fail("_index.json 里没有任何 board")
                        for b in boards:
                            slug = b.get("slug")
                            if not slug:
                                fail("_index.json 有 board 缺 slug")
                                continue
                            f = day_dir / f"{slug}.json"
                            if not f.exists():
                                fail(f"_index.json 列了 {slug} 但 data/{day}/{slug}.json 不存在")
                                continue
                            # 索引里的行数要和文件里一致
                            try:
                                doc = json.loads(f.read_text(encoding="utf-8"))
                                if b.get("model_count") != len(doc.get("models") or []):
                                    warn(f"{slug}: 索引写 {b.get('model_count')} 行，"
                                         f"文件里是 {len(doc.get('models') or [])} 行")
                            except Exception:
                                pass
                            check_board(f, slug)

    print(f"检查目录: {DATA}")
    if warnings:
        print(f"\n⚠ {len(warnings)} 条警告：")
        for w in warnings[:20]:
            print(f"  - {w}")
    if errors:
        print(f"\n✗ {len(errors)} 个错误：")
        for e in errors:
            print(f"  - {e}")
        return 1

    print("\n✓ 数据校验通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
