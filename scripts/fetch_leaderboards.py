#!/usr/bin/env python3
"""
Fetch arena.ai leaderboard data via Jina Reader (a public read-only page-to-text
proxy), then parse the returned markdown table with plain regex/string logic —
no LLM involved anywhere in this pipeline. This intentionally replaces the
original repo's LLM-based extraction: same fetch mechanism, fully deterministic
parsing, and an expanded schema that also captures Rank Spread, Price ($/M
tokens), and Context Length whenever arena.ai actually shows them for a given
category (it does not for the image/video categories — we leave those null
rather than inventing data).
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
import urllib.request
import urllib.error

from jsonschema import Draft202012Validator

JINA_READER_BASE = "https://r.jina.ai/"
ARENA_BASE = "https://arena.ai/leaderboard/"

# 已验证可用的榜单分类，对应 arena.ai 页面 footer "LEADERBOARD RANKINGS" 列表。
# 每项是 (文件名用的 slug, 实际请求路径)：Code 分类下 arena.ai 有两个独立子榜
# —— /leaderboard/code 与 /leaderboard/code/webdev 内容完全相同（前者只是后者的别名），
# 真正独立的第二个子榜是 /leaderboard/code/image-to-webdev，之前的版本遗漏了它。
# 文件名不能带斜杠，所以 "code/webdev" 落盘成 code-webdev.json。
LEADERBOARDS = [
    ("agent", "agent"),
    ("text", "text"),
    ("code-webdev", "code/webdev"),
    ("code-image-to-webdev", "code/image-to-webdev"),
    ("vision", "vision"),
    ("document", "document"),
    ("search", "search"),
    ("text-to-image", "text-to-image"),
    ("image-edit", "image-edit"),
    ("text-to-video", "text-to-video"),
    ("image-to-video", "image-to-video"),
    ("video-edit", "video-edit"),
]

# 厂商白名单：按长度降序匹配，避免短词（如 "AI"）在匹配更长厂商名（如 "Microsoft AI"）
# 时截断出错。这份名单来自对 2026-06 至 2026-07 期间所有分类历史快照的人工核对，
# 覆盖了当前活跃模型的绝大多数厂商；长尾的停用/实验性模型如果匹配不到，
# vendor 会诚实地留 null，不做猜测。
VENDORS = sorted([
    "Alibaba-ATH", "Alibaba", "Amazon", "Ant Group", "Anthropic", "Baidu",
    "Black Forest Labs", "Bytedance", "Cohere", "DeepSeek", "Diffbot",
    "Genmo AI", "Google", "HiDream", "IBM", "Ideogram", "Inception AI",
    "Kandinsky", "KlingAI", "Krea", "Leonardo AI", "Luma AI", "Meituan",
    "Meta", "Microsoft AI", "Microsoft", "MiniMax", "Mistral", "Moonshot",
    "NexusFlow", "Nvidia", "OpenAI", "Perplexity AI", "Pika", "Pixverse",
    "Pruna", "Recraft", "Reve", "Runway", "Shengshu", "SpaceXAI",
    "Stability AI", "StepFun", "Tencent", "Xiaomi", "Z.ai", "lightricks",
    "xAI", "Ai2",
], key=len, reverse=True)

# 没有展示厂商的老模型，用 license 短语兜底切分模型名（同样按长度降序）
LICENSE_PHRASES = sorted([
    "Apache-2.0", "Apache 2.0", "CC-BY-NC-SA-4.0", "CC-BY-NC-4.0",
    "Non-commercial", "Jamba Open", "DBRX LICENSE", "Yi License",
    "Falcon-180B TII License", "AI2 ImpACT Low-risk", "Qianwen LICENSE",
    "Gemma license", "Llama 2 Community", "Llama 3.1 Community",
    "Llama 3 Community", "Proprietary", "MIT", "Other",
], key=len, reverse=True)

SIGNAL_COLUMNS = {
    "Net Improvement": "net_improvement",
    "Confirmed Success": "confirmed_success",
    "Praise vs Complaint": "praise_vs_complaint",
    "Steerability": "steerability",
    "Bash Recovery": "bash_recovery",
    "Tool Hallucination": "tool_hallucination",
}


def fetch_page(url: str, jina_api_key: str | None = None) -> str:
    """Fetch a page via Jina Reader; returns the raw JSON-wrapped response text."""
    reader_url = f"{JINA_READER_BASE}{url}"
    headers = {
        "Accept": "application/json",
        "X-Return-Format": "markdown",
        "User-Agent": "Mozilla/5.0 (compatible; arena-leaderboard-bot/1.0)",
    }
    if jina_api_key:
        headers["Authorization"] = f"Bearer {jina_api_key}"

    req = urllib.request.Request(reader_url, headers=headers)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read().decode("utf-8")
        except urllib.error.URLError as e:
            print(f"  Attempt {attempt+1} failed: {e}", file=sys.stderr)
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"Failed to fetch {url} after 3 attempts")


def strip_md_link(text: str):
    """'[name](url) rest' -> (name, url, rest). If no link, (None, None, text).

    Handles two edge cases seen in real arena.ai data:
    - a leading markdown image badge before the actual model link, e.g.
      '![Image 1: Kandinsky](thumb.png) [kandinsky-5.0-t2v-pro](url) rest'
    - model names that themselves contain a literal '[...]' segment, e.g.
      '[gemini-3.1-flash-image (nano-banana-2) [web-search]](url)' -- a
      non-greedy '[^\\]]+' would stop at the first ']' inside the name and
      truncate it, so the link-name capture below is intentionally greedy.
    """
    text = re.sub(r"^!\[[^\]]*\]\([^)]*\)\s*", "", text)
    m = re.match(r"^\[(.+)\]\(([^)]+)\)\s*(.*)$", text)
    if m:
        return m.group(1).strip(), m.group(2), m.group(3).strip()
    return None, None, text.strip()


def normalize_license(raw: str | None) -> str | None:
    if not raw:
        return None
    low = raw.lower()
    if low.startswith("proprietary"):
        return "proprietary"
    return "open"


def split_vendor_license(block: str):
    """'Anthropic · Proprietary' -> ('Anthropic', 'proprietary')."""
    if " · " in block:
        vendor_part, _, license_part = block.partition(" · ")
        return vendor_part.strip(), normalize_license(license_part.strip())
    return None, None


def parse_model_cell(raw: str) -> dict:
    """Parse a 'Model' column cell into model/model_url/vendor/license."""
    name, url, remainder = strip_md_link(raw)

    if name is not None:
        vendor, license_ = split_vendor_license(remainder)
        return {"model": name, "model_url": url, "vendor": vendor, "license": license_}

    block = remainder
    if " · " in block:
        left, _, license_part = block.partition(" · ")
        license_norm = normalize_license(license_part.strip())
        for v in VENDORS:
            if left == v or left.endswith(" " + v):
                model_name = left[: -len(v)].strip()
                return {"model": model_name, "model_url": None, "vendor": v, "license": license_norm}
        return {"model": left.strip(), "model_url": None, "vendor": None, "license": license_norm}

    for phrase in LICENSE_PHRASES:
        if block == phrase or block.endswith(" " + phrase):
            model_name = block[: -len(phrase)].strip()
            return {"model": model_name, "model_url": None, "vendor": None, "license": normalize_license(phrase)}
    for v in VENDORS:
        if block == v or block.endswith(" " + v):
            model_name = block[: -len(v)].strip()
            return {"model": model_name, "model_url": None, "vendor": v, "license": None}
    return {"model": block.strip(), "model_url": None, "vendor": None, "license": None}


def parse_score(raw: str) -> dict:
    """'1508±7' or '1631+17/-17', optionally trailed by ' Preliminary'."""
    preliminary = "Preliminary" in raw
    text = raw.replace("Preliminary", "").strip()
    m = re.match(r"^(-?\d+)±(\d+)$", text)
    if m:
        score, ci = int(m.group(1)), int(m.group(2))
        return {"score": score, "ci_low": ci, "ci_high": ci, "preliminary": preliminary}
    m = re.match(r"^(-?\d+)\+(\d+)/-(\d+)$", text)
    if m:
        score, ci_high, ci_low = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return {"score": score, "ci_low": ci_low, "ci_high": ci_high, "preliminary": preliminary}
    m = re.match(r"^(-?\d+)$", text)
    if m:
        return {"score": int(m.group(1)), "ci_low": None, "ci_high": None, "preliminary": preliminary}
    return {"score": None, "ci_low": None, "ci_high": None, "preliminary": preliminary}


def parse_int(raw: str):
    text = raw.replace(",", "").strip()
    return int(text) if re.match(r"^-?\d+$", text) else None


def parse_price(raw: str):
    """'$10 / $50' -> (10.0, 50.0). 'N/A' -> (None, None)."""
    m = re.match(r"^\$([\d.]+)\s*/\s*\$([\d.]+)$", raw.strip())
    if m:
        return float(m.group(1)), float(m.group(2))
    return None, None


def parse_context(raw: str):
    """'1M' -> 1000000, '1.1M' -> 1100000, '200K' -> 200000. 'N/A' -> None."""
    m = re.match(r"^([\d.]+)([KM])$", raw.strip(), re.IGNORECASE)
    if not m:
        return None
    n = float(m.group(1))
    mult = 1_000_000 if m.group(2).upper() == "M" else 1_000
    return int(round(n * mult))


def parse_signal(raw: str):
    """'13.94%±1.56%' -> {'value': 13.94, 'ci': 1.56}."""
    m = re.match(r"^(-?[\d.]+)%±([\d.]+)%$", raw.strip())
    if not m:
        return None
    return {"value": float(m.group(1)), "ci": float(m.group(2))}


def split_table_row(line: str) -> list[str]:
    parts = line.split("|")
    return [c.strip() for c in parts[1:-1]]


def find_table(content: str):
    lines = content.split("\n")
    header_idx = next((i for i, l in enumerate(lines) if re.match(r"^\|\s*Rank\s*\|", l)), None)
    if header_idx is None:
        return None
    header = split_table_row(lines[header_idx])
    rows = []
    for i in range(header_idx + 2, len(lines)):
        if not lines[i].startswith("|"):
            break
        rows.append(lines[i])
    return header, rows, header_idx


def extract_last_updated(content: str, header_idx_hint: int | None) -> str | None:
    """Best-effort: find a 'Mon DD, YYYY' date line in the page preamble
    (before the leaderboard table starts), to avoid matching unrelated dates
    that might appear deep inside model names or links."""
    preamble = content if header_idx_hint is None else "\n".join(content.split("\n")[:header_idx_hint])
    m = re.search(r"\b([A-Z][a-z]{2} \d{1,2}, \d{4})\b", preamble)
    return m.group(1) if m else None


def parse_leaderboard(content: str) -> list[dict]:
    table = find_table(content)
    if not table:
        raise ValueError("no leaderboard table found in fetched content")
    header, rows, _ = table

    has_rank_spread_col = "Rank Spread" in header
    has_price_col = "Price $/M" in header
    has_context_col = "Context" in header
    has_score_col = "Score" in header
    votes_col_name = "Sessions" if "Sessions" in header else "Votes"
    idx = {name: i for i, name in enumerate(header)}

    models = []
    skipped = 0
    for row_line in rows:
        cells = split_table_row(row_line)
        if len(cells) != len(header):
            skipped += 1
            continue

        rank_cell = cells[idx["Rank"]]
        if has_rank_spread_col:
            rank = parse_int(rank_cell)
            spread_parts = cells[idx["Rank Spread"]].split()
            rank_spread = [int(spread_parts[0]), int(spread_parts[1])] if len(spread_parts) == 2 else None
        else:
            # agent 分类没有独立的 Rank Spread 列，区间数字挤在 Rank 单元格里：
            # "1 1 2" = 排名1，区间[1,2]
            parts = rank_cell.split()
            rank = int(parts[0]) if parts else None
            rank_spread = [int(parts[1]), int(parts[2])] if len(parts) == 3 else None

        model_info = parse_model_cell(cells[idx["Model"]])

        record = {
            "rank": rank,
            "rank_spread": rank_spread,
            "model": model_info["model"],
            "model_url": model_info["model_url"],
            "vendor": model_info["vendor"],
            "license": model_info["license"],
            "votes": parse_int(cells[idx[votes_col_name]]) if votes_col_name in idx else None,
        }

        if has_score_col:
            record.update(parse_score(cells[idx["Score"]]))
        else:
            record.update({"score": None, "ci_low": None, "ci_high": None, "preliminary": False})

        if has_price_col:
            p_in, p_out = parse_price(cells[idx["Price $/M"]])
            record["price_prompt"] = p_in
            record["price_completion"] = p_out
        else:
            record["price_prompt"] = None
            record["price_completion"] = None

        record["context_length"] = parse_context(cells[idx["Context"]]) if has_context_col else None

        signals = {}
        for col_name, key in SIGNAL_COLUMNS.items():
            if col_name in idx:
                signals[key] = parse_signal(cells[idx[col_name]])
        record["signals"] = signals if signals else None

        models.append(record)

    if skipped:
        print(f"  (skipped {skipped} malformed row(s))", file=sys.stderr)
    return models


def main():
    jina_key = os.environ.get("JINA_API_KEY")

    repo_root = Path(__file__).resolve().parent.parent
    schema_dir = repo_root / "schemas"
    lb_schema = json.loads((schema_dir / "leaderboard.json").read_text())
    idx_schema = json.loads((schema_dir / "index.json").read_text())
    lb_validator = Draft202012Validator(lb_schema)
    idx_validator = Draft202012Validator(idx_schema)

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    day_dir = repo_root / "data" / date_str
    day_dir.mkdir(parents=True, exist_ok=True)

    results = {}
    errors = []

    for file_slug, url_path in LEADERBOARDS:
        print(f"\n{'='*50}", file=sys.stderr)
        print(f"Processing: {file_slug} ({url_path})", file=sys.stderr)
        url = f"{ARENA_BASE}{url_path}"
        try:
            raw = fetch_page(url, jina_key)
            payload = json.loads(raw)
            content = payload["data"]["content"]

            table = find_table(content)
            if not table:
                raise ValueError("no leaderboard table found in fetched content")
            _, _, header_idx = table
            models = parse_leaderboard(content)
            if not models:
                raise ValueError("parser found zero models")

            output = {
                "meta": {
                    "leaderboard": file_slug,
                    "source_url": url,
                    "fetched_at": now.isoformat(),
                    "last_updated": extract_last_updated(content, header_idx),
                    "model_count": len(models),
                },
                "models": models,
            }

            schema_errors = list(lb_validator.iter_errors(output))
            if schema_errors:
                err_msgs = [f"{e.json_path}: {e.message}" for e in schema_errors[:5]]
                raise ValueError(f"Schema validation failed: {'; '.join(err_msgs)}")

            fp = day_dir / f"{file_slug}.json"
            with open(fp, "w") as f:
                json.dump(output, f, indent=2, ensure_ascii=False)
            print(f"  wrote {fp} ({len(models)} models)", file=sys.stderr)
            results[file_slug] = len(models)
            time.sleep(2)

        except Exception as e:
            print(f"  ERROR: {e}", file=sys.stderr)
            errors.append({"leaderboard": file_slug, "error": str(e)})

    index = {
        "date": date_str,
        "fetched_at": now.isoformat(),
        "leaderboards": {slug: {"model_count": count} for slug, count in results.items()},
        "errors": errors,
    }
    idx_errors = list(idx_validator.iter_errors(index))
    if idx_errors:
        print(f"  Index schema invalid: {idx_errors[0].message}", file=sys.stderr)

    with open(day_dir / "_index.json", "w") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)

    latest = {"date": date_str, "path": date_str}
    with open(repo_root / "data" / "latest.json", "w") as f:
        json.dump(latest, f, indent=2)
    print(f"\nUpdated data/latest.json -> {date_str}", file=sys.stderr)

    print(f"\n{'='*50}", file=sys.stderr)
    print(f"Done: {len(results)}/{len(LEADERBOARDS)} leaderboards", file=sys.stderr)
    for slug, count in results.items():
        print(f"  {slug}: {count} models", file=sys.stderr)
    if errors:
        print(f"Errors: {len(errors)}", file=sys.stderr)
        for e in errors:
            print(f"  {e['leaderboard']}: {e['error']}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
