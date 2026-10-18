#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
arena.ai RSC 载荷的结构化发现与归一 —— 纯标准库，不碰网络

设计目标只有一条：**官方改版后，这里不用改。**

所以全程"按结构找"，绝不按路径找：

  ✗ 不写死 flight 行号（`$.3e[3]` 那种，一行改动就全废）
  ✗ 不写死分类清单（官方随时增减分类）
  ✗ 不写死字段名（`rating` 可能变成 `score`，`entries` 可能变成 `rows`）

  ✓ 找"像榜单行"的对象：含模型名 + 含数值字段
  ✓ 从官网首页载荷里读它自己声明的分类清单和跳转链接
  ✓ 字段按别名表映射，取不到就给 null（schema 允许 null，不允许缺键）

结构实测（2026-10-16）：

  官网首页 /leaderboard          → pills[]（分类清单 + 每类 top 模型 + trends）
  普通榜单 /leaderboard/x/y      → leaderboard.entries[]（15 个字段）
  agent 榜 /leaderboard/agent    → snapshot.rows[]（结构不同，见 _norm_agent_row）
  分类清单                       → arena.categories（Map，行号会变）
"""

from __future__ import annotations

import json
import re

# ─────────────────────────── flight 解码 ───────────────────────────

_DEC = json.JSONDecoder()
_ROW_HEAD = re.compile(r"([0-9a-fA-F]{1,10}):")

# flight 的文本块行是 `T<hex 字节长度>,<正好这么多字节>`。
# **不能用 split('\n') 切** —— 文本块体内可以有裸换行，会切错。
# 实测 chat/text 载荷有 1 个 T 行（26983 字节），naive split 会多切 4 段。
_T_ROW = re.compile(r"T([0-9a-fA-F]+),")


def parse_flight(text: str):
    """flight 流 → {row_id: value}。返回 (raw, 跳过行数)。"""
    raw: dict[str, object] = {}
    skipped = 0
    i, n = 0, len(text)

    while i < n:
        m = _ROW_HEAD.match(text, i)
        if not m:
            nl = text.find("\n", i)
            if nl < 0:
                break
            i = nl + 1
            continue

        rid = m.group(1)
        j = m.end()

        # T 行：按字节长度切，长度单位是 **UTF-8 字节**
        tm = _T_ROW.match(text, j)
        if tm:
            try:
                blen = int(tm.group(1), 16)
            except ValueError:
                blen = 0
            start = j + tm.end() - j
            # 内容按字符计算长度会偏差，用字节数从起始处累加
            chunk = text[start:start + blen]
            # 若字节与字符不等（含非 ASCII），逐步扩张到字节数满足
            if len(chunk.encode("utf-8", "surrogatepass")) != blen:
                lo, hi = 0, len(text) - start
                while lo < hi:
                    mid = (lo + hi) // 2
                    if len(text[start:start + mid].encode("utf-8", "surrogatepass")) < blen:
                        lo = mid + 1
                    else:
                        hi = mid
                chunk = text[start:start + lo]
            raw[rid] = chunk
            end = start + len(chunk)
            i = end + 1 if end < n and text[end] == "\n" else end
            continue

        try:
            val, end = _DEC.raw_decode(text, j)
        except ValueError:
            skipped += 1
            nl = text.find("\n", j)
            if nl < 0:
                break
            i = nl + 1
            continue

        raw[rid] = val
        i = end + 1 if end < n and text[end] == "\n" else end

    return raw, skipped


def materialize(raw: dict, max_depth: int = 40, max_nodes: int = 400_000):
    """展开 `"$3"` / `"$3:0:1"` / `{"$":"3"}` 引用。"""
    budget = [max_nodes]

    def deref(key: str, depth: int, seen: frozenset):
        if key not in raw or key in seen or depth > max_depth or budget[0] <= 0:
            return None
        budget[0] -= 1
        return resolve(raw[key], depth + 1, seen | {key})

    def resolve(v, depth: int, seen: frozenset):
        if depth > max_depth or budget[0] <= 0:
            return None
        budget[0] -= 1

        if isinstance(v, str):
            if len(v) > 1 and v[0] == "$":
                # 标量哨兵
                if v in ("$undefined", "$null"):
                    return None
                if v == "$NaN":
                    return None
                if v in ("$Infinity", "$-Infinity"):
                    return None
                if v.startswith("$$"):
                    return v[1:]           # 转义的字面量 $
                if v.startswith("$S"):
                    return v                  # Symbol，保持原样
                if v.startswith("$D"):
                    return v[2:]              # Date → ISO 串
                k = v[1:]
                # 带属性路径：$id:a:b
                if ":" in k:
                    parts = k.split(":")
                    base = deref(parts[0], depth, seen)
                    cur = base
                    for seg in parts[1:]:
                        if cur is None:
                            return None
                        if isinstance(cur, list) and seg.isdigit():
                            idx = int(seg)
                            cur = cur[idx] if 0 <= idx < len(cur) else None
                        elif isinstance(cur, dict) and seg in cur:
                            cur = cur[seg]
                        else:
                            return None
                    return cur
                # 前缀修饰：$@ $L $h $H $Q $W $K $B $i $T $n
                if k[:1] in ("@", "L", "h", "H", "Q", "W", "K", "B", "i", "T", "n"):
                    return deref(k[1:], depth, seen)
                return deref(k, depth, seen)
            return v

        if isinstance(v, list):
            return [resolve(x, depth + 1, seen) for x in v]

        if isinstance(v, dict):
            if len(v) == 1:
                k0, v0 = next(iter(v.items()))
                if k0.startswith("$") and isinstance(v0, str):
                    return deref(v0, depth, seen)
            return {k: resolve(val, depth + 1, seen) for k, val in v.items()}

        return v

    return resolve(dict(raw), 0, frozenset())


# ─────────────────────── 结构发现（抗改版核心）───────────────────────

MODEL_NAME_KEYS = (
    "modelDisplayName", "modelName", "publicName", "displayName",
    "model", "name",
)

# 分数来源，按优先级试。arena 有时叫 rating，有时叫 score / avgScore
SCORE_SOURCE_KEYS = ("rating", "score", "avgScore", "elo", "value")

TREND_PLACEHOLDER = re.compile(
    r"gemini|gpt|claude|grok|llama|qwen|deepseek|mistral|kimi|glm|"
    r"muse|fable|opus|sonnet|haiku|banana|imagine|omni|-next|-pro",
    re.I,
)


def _iter_objects(node, max_depth=70, seen=None):
    """深度优先遍历，产出 (path, dict)。大载荷要注意去重。"""
    if seen is None:
        seen = set()
    stack = [("$", node, 0)]
    while stack:
        path, cur, d = stack.pop()
        if cur is None or d > max_depth:
            continue
        if isinstance(cur, (dict, list)):
            if id(cur) in seen:
                continue
            seen.add(id(cur))
        if isinstance(cur, dict):
            yield path, cur
            for k, v in cur.items():
                if isinstance(v, (dict, list)):
                    stack.append((f"{path}.{k}", v, d + 1))
        elif isinstance(cur, list):
            for i, v in enumerate(cur):
                if isinstance(v, (dict, list)):
                    stack.append((f"{path}[{i}]", v, d + 1))


def _name_of(d: dict):
    for k in MODEL_NAME_KEYS:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _score_of(d: dict):
    for k in SCORE_SOURCE_KEYS:
        v = d.get(k)
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, dict):
            inner = v.get("value")
            if isinstance(inner, (int, float)):
                return float(inner)
    return None


def looks_like_row(d: dict) -> bool:
    """一行榜单数据的特征：有模型名 + 有分数 + 有名次。"""
    return bool(_name_of(d)) and _score_of(d) is not None and isinstance(d.get("rank"), (int, float))


def find_board_rows(tree):
    """找出载荷里所有榜单行，返回 (rows, 说明)。

    不认 `entries` / `rows` 这种键名，只认**行的形状**。
    这比写死键名抗改版得多：官方把 entries 改名成别的也不影响。
    """
    cands = []
    for path, d in _iter_objects(tree):
        if looks_like_row(d):
            cands.append((path, d))

    if not cands:
        return [], "没有符合榜单行特征的字典"

    # 去重：同一个 dict 可能被多处引用
    uniq = {}
    for path, d in cands:
        uniq.setdefault(id(d), (path, d))
    items = list(uniq.values())

    # 取"行数最多的那个父前缀"作为主榜，避免把图表副本也当成榜
    from collections import Counter
    def parent(p):
        # 去掉最后一段 [n] 或 .key
        p = re.sub(r"\[\d+\]$", "", p)
        p = re.sub(r"\.[^.\[]+$", "", p)
        return p
    pref = Counter(parent(p) for p, _ in items)
    best_pref, cnt = pref.most_common(1)[0]
    rows = [d for p, d in items if parent(p) == best_pref]

    note = (f"主榜前缀 {best_pref}，{len(rows)} 行"
            + (f"；另有 {len(items) - len(rows)} 个散落在别处" if len(items) > len(rows) else ""))
    return rows, note


def find_categories(tree):
    """从任意载荷里读出分类清单。

    官网首页载荷里每个分类都带 href=/leaderboard/...，
    以 `href` 为准 —— 这是站点自己声明的导航，比我们猜路径可靠。
    """
    out = []
    seen = set()

    for _, d in _iter_objects(tree):
        href = d.get("href")
        if not isinstance(href, str) or not href.startswith("/leaderboard"):
            continue
        if href in seen:
            continue
        seen.add(href)

        out.append({
            "href": href,
            "id": d.get("id"),
            "label": d.get("label"),
            "badge_label": d.get("badgeLabel"),
            "sub_label": d.get("subLabel"),
            "icon": d.get("icon"),
            "arena_slug": d.get("arenaSlug"),
            "score_unit": d.get("scoreUnit"),
            "pricing_unit": d.get("pricingUnit"),
        })

    return out


def find_arena_meta(tree):
    """榜单页自述：arena.slug / categories / capabilitiesTitle。"""
    for _, d in _iter_objects(tree):
        arena = d.get("arena")
        if isinstance(arena, dict) and arena.get("slug"):
            cats = arena.get("categories")
            norm = []
            if isinstance(cats, dict):
                for k, v in cats.items():
                    if isinstance(v, dict):
                        norm.append({"key": k, **v})
            elif isinstance(cats, list):
                for item in cats:
                    if isinstance(item, list) and len(item) == 2 and isinstance(item[1], dict):
                        norm.append({"key": item[0], **item[1]})
            return {
                "slug": arena.get("slug"),
                "title": arena.get("title"),
                "description": arena.get("description"),
                "content_key": arena.get("contentKey"),
                "has_pricing": arena.get("hasPricing"),
                "has_style_control": arena.get("hasStyleControl"),
                "categories": norm,
            }
    return None


def sub_boards(tree):
    """从 arena.categories 挖出**真正可抓的子榜地址**。

    这是解决"三个 agent pill 共用 /leaderboard/agent"的关键。

    官网导航里 Agent 下面有四块（Overall / Code / Chat / Work），
    但它们唯一的 href 都是 `/leaderboard/agent` —— 同一个页面，
    靠 `arena.categories[].dataSource.leaderboardSlug` 切换数据源：

        overall → leaderboardSlug = "all"
        code    → leaderboardSlug = "code"
        chat    → leaderboardSlug = "consumer"
        work    → leaderboardSlug = "gdp"

    只按 href 抓会拿到**三份完全相同的数据**（实测 52 行 × 3），
    必须拼成 `/leaderboard/agent/leaderboardSlug` 才拿到各自的榜。
    """
    meta = find_arena_meta(tree)
    if not meta:
        return []

    out = []
    for c in meta.get("categories") or []:
        if not isinstance(c, dict):
            continue
        ds = c.get("dataSource")
        if not isinstance(ds, dict):
            continue
        arena = ds.get("arenaSlug") or meta.get("slug")
        lb = ds.get("leaderboardSlug")
        if not arena or not lb:
            continue
        # "all" 就是主榜本身，不需要再加一段
        href = f"/leaderboard/{arena}" if lb == "all" else f"/leaderboard/{arena}/{lb}"
        out.append({
            "href": href,
            "key": c.get("key") or c.get("name"),
            "title": c.get("title") or c.get("name") or lb,
            "icon": c.get("icon"),
            # 主榜那条的 title 常是 "Overall"，跟 arena 名字拼起来更好认
            "is_primary": lb == "all",
        })
    return out


def find_snapshot(tree):
    """榜单页的 snapshot 块（agent 类走这个）。"""
    for _, d in _iter_objects(tree):
        snap = d.get("snapshot")
        if isinstance(snap, dict) and isinstance(snap.get("rows"), list):
            return snap
    return None


def find_last_updated(tree):
    """取榜单的数据截止时间（ISO 串）。

    不同类别的字段名不一样，按可信度依次试：
      lastUpdated            agent 类（snapshot.lastUpdated）
      voteCutoffISOString    普通榜（投票截止时间，就是数据日期）
    实测：chat/text 没有 lastUpdated，只有 voteCutoffISOString=2026-10-15T13:00:00Z。
    """
    keys = ("lastUpdated", "voteCutoffISOString", "updatedAt", "asOf", "cutoffTime")
    best = None
    for _, d in _iter_objects(tree):
        for k in keys:
            v = d.get(k)
            if isinstance(v, str) and re.match(r"\d{4}-\d{2}-\d{2}T", v):
                # 取最晚的那个：一个载荷里可能有多个时间点
                if best is None or v > best:
                    best = v
    return best


def find_total_models(tree):
    """站点自述的模型总数 —— 用来校验解析有没有漏行。

    实测 chat/text：totalModels=416，解析出 416 行，正好对上。
    这个校验很重要：`entries` 少读几行不会报错，只会让榜单悄悄变短。
    """
    for _, d in _iter_objects(tree):
        v = d.get("totalModels")
        if isinstance(v, (int, float)) and v > 0:
            return int(v)
        mc = d.get("modelCount")
        if isinstance(mc, (int, float)) and mc > 0:
            return int(mc)
    return None


def find_pill_previews(tree, full=False):
    """首页每个 pill 自带的模型数据 —— 官网首页就是靠这个渲染的。

    这是**首页**需要的全部内容：每类一张小卡，列出前三名。
    比让前端分别拉 10 个榜单再各自取前三快得多，一次请求就够。

    `full=True` 时返回**完整的 entries**（不截断）。这一点很关键：

    Agent Arena 的四个子榜（Overall / Code / Chat / Work）**没有各自的 URL**
    —— `/leaderboard/agent/consumer`、`/leaderboard/agent/gdp` 实测都是 404，
    页面只声明了 `/leaderboard/agent` 和 `/leaderboard/agent/alignment`。
    它们的完整数据（各 52 行）就躺在这份首页载荷的 pill.entries 里，只是分数不同：

        Overall  0.1887   Code  0.1993   Work  0.1797   Alignment 87.9（指数）

    所以抓首页时顺手把这几榜存下来，比再发一轮请求既快又准。
    """
    out = {}
    for _, d in _iter_objects(tree):
        href = d.get("href")
        if not isinstance(href, str) or not href.startswith("/leaderboard/"):
            continue
        if not (d.get("label") or d.get("badgeLabel")):
            continue

        tops = []
        entries = d.get("entries")
        if isinstance(entries, list) and entries:
            src = entries if full else entries[:12]
            for e in src:
                if not isinstance(e, dict):
                    continue
                name = _name_of(e)
                if not name:
                    continue
                tops.append({
                    "model": name,
                    "vendor": (e.get("modelOrganization") or e.get("organization")
                               or e.get("vendor") or None),
                    "score": _score_of(e),
                    "rank": e.get("rank"),
                    "license": norm_license(e.get("license")),
                    "_raw": e,
                })
            tops.sort(key=lambda x: (x["rank"] is None, x["rank"] or 10**9))
        else:
            # 没有 entries 的（如 alignment）退回到 trends 里的最新点
            tr = d.get("trends")
            if isinstance(tr, dict):
                for plot in (tr.get("plots") or []):
                    for series in (plot.get("series") or []):
                        pts = series.get("points") or []
                        if pts:
                            last = pts[-1]
                            nm = last.get("modelName")
                            if nm:
                                tops.append({
                                    "model": nm,
                                    "vendor": last.get("organization"),
                                    "score": last.get("score"),
                                    "rank": None,
                                    "license": None,
                                })
                        if len(tops) >= 3:
                            break
                    if len(tops) >= 3:
                        break

        key = (href, d.get("subLabel") or d.get("id") or "")
        if key not in out:
            out[key] = {
                "href": href,
                "id": d.get("id"),
                "label": d.get("label"),
                "badge_label": d.get("badgeLabel"),
                "sub_label": d.get("subLabel"),
                "icon": d.get("icon"),
                "arena_slug": d.get("arenaSlug"),
                "score_unit": d.get("scoreUnit"),
                "pricing_unit": d.get("pricingUnit"),
                "top_models": tops if full else tops[:10],
            }
    return list(out.values())


# ─────────────────────────── 归一成 schema ───────────────────────────

LICENSE_OPEN = re.compile(
    r"(apache|mit\b|bsd|gpl|lgpl|cc[- ]?by|creative ?commons|open|llama|gemma|"
    r"qwen|mpl|isc\b|unlicense|public ?domain|non-?commercial|apache-2)",
    re.I,
)
LICENSE_PROPRIETARY = re.compile(r"(proprietary|closed|commercial)", re.I)


def norm_license(raw):
    """官方给具体许可证名，schema 只要 proprietary/open/null。"""
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if LICENSE_PROPRIETARY.search(s):
        return "proprietary"
    if LICENSE_OPEN.search(s):
        return "open"
    return None


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, dict):
        inner = v.get("value")
        if isinstance(inner, (int, float)):
            return inner
    return None


def _spread(d):
    """名次区间 → [小, 大]。兼容三种写法。"""
    rs = d.get("rankSpread")
    if isinstance(rs, dict) and isinstance(rs.get("min"), (int, float)) and isinstance(rs.get("max"), (int, float)):
        return [int(min(rs["min"], rs["max"])), int(max(rs["min"], rs["max"]))]

    lo, hi = d.get("rankLower"), d.get("rankUpper")
    if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
        return [int(min(lo, hi)), int(max(lo, hi))]

    r = d.get("rank")
    if isinstance(r, (int, float)):
        return [int(r), int(r)]
    return None


# ─────────────── 量纲：不同分类的 score 不是一个东西 ───────────────
#
# 实测两种完全不同的分数：
#   chat/text 等    rating   ≈ 950–1700   两位小数足够
#   agent           avgScore ≈ 0.13–0.19  四位有效数字才不丢区分度
#
# 现有 schema 的 score 是**整数**。直接 round(0.1887) = 0，整个 agent 榜
# 会全部变成 0 分 —— 排名信息彻底消失（这是踩过的坑，不是假设）。
#
# 对策：按量纲放大成整数，并在 meta.score_unit / meta.score_scale 里记下
# 换算方式，前端按 scale 还原显示。放大 10000 可以保留 4 位小数，
# 而 0–1 区间的分数正好需要这么多位才区分得开。
SCORE_SCALES = {
    "rating": 1,        # 已经是 ~1500 量级，不动
    "index": 10000,     # 0–1 的指数 → 放大成 4 位小数的整数
    "net-improvement": 10000,
    "elo": 1,
    "score": 1,
    None: 1,            # 不知道就不动
}


def score_scale(unit: str | None) -> int:
    return SCORE_SCALES.get(unit, 1)


def infer_scale(rows_raw) -> tuple:
    """榜单载荷里没有 scoreUnit（那只在首页 pills 有），所以**按量级推断**。

    实测三种量纲，跨了三个数量级 —— 用同一个换算规则必然有一类被搞坏：

        rating        950–1811    → 已是整数尺度
        index(0–1)    0.13–0.19   → 必须放大，否则 round 后全是 0
        index(0–100)  87.9        → **不能放大**，放大一万倍变成 879430

    判据：只有当**所有**分数都落在 [-1, 1] 内，才认定是 0–1 比率型并放大 10000。
    0–100 的指数（agent alignment）落在 [0,100]，条件不成立，保持原值。
    """
    vals = []
    for d in rows_raw:
        v = _score_of(d) if isinstance(d, dict) else None
        if v is not None:
            vals.append(v)
    if not vals:
        return 1, None
    if all(-1.0 <= v <= 1.0 for v in vals):
        return 10000, "ratio*10000"
    return 1, None


def to_schema_row(d: dict, unit: str | None = None, scale: int | None = None,
                  score_in_signals: bool = False) -> dict:
    """任意榜单行 → 现有 schema 的 15 键。

    两个**数值口径**必须注意（读前端代码确认的）：
      · ci_low / ci_high 前端当**偏移量**用：min = score - ci_low
        而 RSC 给的是绝对区间，必须存差值
      · score 前端当整数用，RSC 是浮点，要 round
    """
    score = _score_of(d)
    ll = _num(d.get("ratingLower")) or _num(d.get("ciLower"))
    hh = _num(d.get("ratingUpper")) or _num(d.get("ciUpper"))
    scale = scale if scale is not None else score_scale(unit)

    ci_low = ci_high = None
    if score is not None:
        if ll is not None:
            ci_low = abs(round((score - ll) * scale))
        if hh is not None:
            ci_high = abs(round((hh - score) * scale))
        # agent 只有对称 CI（avgScore.ci），两边都填
        if ci_low is None and ci_high is None:
            c = _num(d.get("avgScore"))
            ci_d = None
            if isinstance(d.get("avgScore"), dict):
                ci_d = _num({"value": d["avgScore"].get("ci")})
            if ci_d is not None:
                ci_low = ci_high = abs(round(ci_d * scale))

    def price(keys):
        for k in keys:
            v = _num(d.get(k))
            if v is not None and v >= 0:
                return float(v)
        return None

    rank = d.get("rank")
    sig = _signals(d)
    score_out = int(round(score * scale)) if score is not None else None

    # schema 要求 ci_low / ci_high >= 0，但 agent 类有负分（index / net-improvement
    # 本来就是有正负的指标）。负分时整榜改走 signals，见 normalize_board 的注释。
    if score_in_signals and score_out is not None:
        if sig is None:
            sig = {}
        sig.setdefault("avg_score", {
            "value": float(score_out),
            "ci": float(ci_high) if ci_high is not None else 0.0,
        })
        score_out = ci_low = ci_high = None

    return {
        "rank": int(rank) if isinstance(rank, (int, float)) else None,
        "rank_spread": _spread(d),
        "model": _name_of(d),
        "model_url": d.get("modelUrl") or d.get("model_url") or d.get("url") or None,
        "vendor": (d.get("modelOrganization") or d.get("organization")
                   or d.get("vendor") or d.get("provider") or None),
        "license": norm_license(d.get("license")),
        "score": score_out,
        "ci_low": ci_low,
        "ci_high": ci_high,
        # official 的 RSC 里没有 preliminary；releaseType == "pre_release" 是最接近的信号
        "preliminary": d.get("releaseType") == "pre_release",
        "votes": (int(d["votes"]) if isinstance(d.get("votes"), (int, float))
                  else int(d["sessions"]) if isinstance(d.get("sessions"), (int, float))
                  else None),
        "price_prompt": price(("inputPricePerMillion", "pricePrompt")),
        "price_completion": price(("outputPricePerMillion", "priceCompletion")),
        "context_length": (int(d["contextLength"]) if isinstance(d.get("contextLength"), (int, float))
                           else int(d["context_length"]) if isinstance(d.get("context_length"), (int, float))
                           else None),
        "signals": sig,
    }


def _signals(d):
    """agent 类的 signals：{key: {value, ci}}。其它类返回 None。"""
    ss = d.get("signalScores")
    cs = d.get("signalCi")
    if not isinstance(ss, dict) or not ss:
        return None
    out = {}
    for k, v in ss.items():
        if not isinstance(v, (int, float)):
            continue
        ci = None
        if isinstance(cs, dict) and isinstance(cs.get(k), (int, float)):
            ci = float(cs[k])
        out[k] = {"value": float(v), "ci": ci if ci is not None else 0.0}
    return out or None


def normalize_board(tree, label=None):
    """一份榜单载荷 → (rows, meta 补充信息)。"""
    arena = find_arena_meta(tree)
    snap = find_snapshot(tree)

    # agent 类的主榜在 snapshot.rows，和普通榜的 leaderboard.entries 不是一个地方。
    # 而且 snapshot.signals[] 里每个指标下还套着一份完整的模型表（1199 行），
    # 按"行的形状"去扫会把子表当主榜 —— 实测首行会变成单指标榜的某一行。
    # 所以有 snapshot 就**优先用 snapshot.rows**，那才是主榜。
    if snap and isinstance(snap.get("rows"), list) and snap["rows"]:
        rows_raw = snap["rows"]
        note = f"snapshot.rows（agent 类主榜）{len(rows_raw)} 行"
    else:
        rows_raw, note = find_board_rows(tree)

    unit = None
    if arena:
        for c in arena.get("categories") or []:
            if isinstance(c, dict) and c.get("scoreUnit"):
                unit = c["scoreUnit"]
                break

    # 以**数据实际量级**为准，**不要**再用 unit 覆盖。
    # 反例（实测）：agent alignment 的 scoreUnit 是 "index"，但数值是 87.94
    # （0–100 量级）。按 unit 查表会以为它是 0–1 而放大一万倍 → 879430。
    # 单位名是站点给的语义标签，数值才是事实，冲突时信数值。
    scale, scale_note = infer_scale(rows_raw)

    # 负数判断必须是**整榜级**的，不能按行。
    # agent/alignment 的分数分布是 -0.20..0.17：按行处理会让一半行有 score、
    # 一半行 score=null，前端排序和柱状图会错乱，且同一列出现两种语义。
    # 只要榜内出现负数，就整榜 score 置 null，分数统一挪进 signals.avg_score。
    all_scores = [v for v in (_score_of(d) for d in rows_raw if isinstance(d, dict)) if v is not None]
    board_uses_signals = bool(all_scores) and min(all_scores) < 0

    seen = set()
    rows = []
    for d in rows_raw:
        name = _name_of(d)
        if not name:
            continue
        key = (name, d.get("rank"))
        if key in seen:
            continue
        seen.add(key)
        rows.append(to_schema_row(d, unit, scale, board_uses_signals))

    rows = [r for r in rows if r["model"]]
    rows.sort(key=lambda r: (r["rank"] is None, r["rank"] or 10**9))

    return rows, {
        "discovery_note": note,
        "arena": arena,
        "last_updated": find_last_updated(tree),
        "model_count_upstream": find_total_models(tree),
        "score_unit": unit or scale_note,
        "score_scale": scale,
        "score_in_signals": board_uses_signals,
    }
