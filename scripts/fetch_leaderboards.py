#!/usr/bin/env python3
"""
Fetch arena.ai leaderboard data using Jina Reader + LLM parsing.
Validates each result against JSON Schema before writing.
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


def fetch_page(url: str, jina_api_key: str | None = None) -> str:
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


def discover_leaderboards(overview_text: str) -> list[tuple[str, str]]:
    pattern = r'arena\.ai/leaderboard/([a-z][a-z0-9-]*)'
    slugs = sorted(set(re.findall(pattern, overview_text)))
    return [(s, s) for s in slugs]


GENERAL_SYSTEM_PROMPT = """You are a data extraction assistant. Extract the FULL leaderboard table from the provided text.
Return ONLY valid JSON with this exact structure:
{
  "last_updated": "string or null",
  "models": [
    {
      "rank": 1,
      "model": "model-name-exactly-as-shown",
      "vendor": "OpenAI",
      "license": "proprietary",
      "score": 1502,
      "ci": 8,
      "votes": 11671
    }
  ]
}
Rules:
- Extract ALL models in the leaderboard, every single row
- "rank": integer rank
- "model": exact model name string as displayed
- "vendor": organization/company name. null if not shown
- "license": MUST be exactly "proprietary" or "open". Map any open-source license (MIT, Apache, etc.) to "open". null only if not shown
- "score": the leaderboard score as number
- "ci": the confidence interval number (e.g. ±8 or +13/-13 => 8 or 13). null if not shown
- "votes": vote count as integer. Remove commas
- If any field is missing or shows '-', use null
- Return raw JSON only, no markdown fences, no commentary"""


AGENT_SYSTEM_PROMPT = """You are a data extraction assistant. Extract the FULL agent leaderboard table from the provided text.
Return ONLY valid JSON with this exact structure:
{
  "last_updated": "string or null",
  "dimensions": ["Net Improvement", "Confirmed Success"],
  "models": [
    {
      "rank": 1,
      "model": "model-name-exactly-as-shown",
      "vendor": "Anthropic",
      "license": "proprietary",
      "scores": [
        {"name": "Net Improvement", "score": 13.94, "ci": 1.56},
        {"name": "Confirmed Success", "score": 17.27, "ci": 2.75}
      ],
      "sessions": 16059
    }
  ]
}
Rules:
- Extract ALL models in the leaderboard, every single row
- Discover the scoring dimensions dynamically from the table headers. Do NOT assume a fixed set of dimensions.
- Current examples may include Net Improvement, Confirmed Success, Praise vs Complaint, Steerability, Bash Recovery, Tool Hallucination, but future dimensions may change.
- "dimensions" must list the discovered scoring dimension display names in left-to-right table order.
- For each model, "scores" must include one entry per discovered dimension, in the same order as "dimensions".
- Each score entry contains: exact dimension name, numeric score without %, numeric ci without %, ci null if not shown.
- If a cell is "13.94%±1.56%", extract score=13.94 and ci=1.56.
- If a cell is "13.94%", extract score=13.94 and ci=null.
- "sessions": integer session count. Remove commas.
- "rank": integer rank shown for the row.
- "model": exact model name string as displayed.
- "vendor": organization/company name. null if not shown.
- "license": MUST be exactly "proprietary" or "open". Map any open-source license (MIT, Apache, etc.) to "open". null only if not shown.
- Ignore non-scoring columns such as rank spread, filters, price, context, methodology text, or decorative content.
- Return raw JSON only, no markdown fences, no commentary"""


def _parse_llm_response(content: str) -> dict:
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1]
        if content.endswith("```"):
            content = content[:-3]
    return json.loads(content)


def parse_with_azure(text: str, slug: str, system_prompt: str, api_key: str, endpoint: str, deployment: str, api_version: str) -> dict:
    url = f"{endpoint.rstrip('/')}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"
    payload = json.dumps({
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f'Extract the "{slug}" leaderboard data:\n\n{text[:20000]}'}
        ],
        "temperature": 0,
        "max_tokens": 20000,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json", "api-key": api_key}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return _parse_llm_response(data["choices"][0]["message"]["content"])


def parse_with_openai(text: str, slug: str, system_prompt: str, api_key: str) -> dict:
    payload = json.dumps({
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f'Extract the "{slug}" leaderboard data:\n\n{text[:20000]}'}
        ],
        "temperature": 0,
        "max_tokens": 20000,
    }).encode("utf-8")
    req = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=payload, headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return _parse_llm_response(data["choices"][0]["message"]["content"])


def parse_with_llm(text: str, slug: str, system_prompt: str, *, use_azure: bool, azure_key: str | None, azure_endpoint: str | None, azure_deployment: str, azure_api_version: str, openai_key: str | None) -> dict:
    if use_azure:
        return parse_with_azure(text, slug, system_prompt, azure_key, azure_endpoint, azure_deployment, azure_api_version)
    return parse_with_openai(text, slug, system_prompt, openai_key)


def normalize_license(lic):
    if not isinstance(lic, str):
        return None
    lic_lower = lic.lower()
    if lic_lower == "proprietary":
        return "proprietary"
    if lic_lower in ("open", "open source", "open-source"):
        return "open"
    if any(kw in lic_lower for kw in ("mit", "apache", "bsd", "gpl", "cc-", "community", "non-commercial")):
        return "open"
    return "open"


def _coerce_number(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip().replace('%', '').replace(',', '')
        if not s or s == '-':
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _coerce_int(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        s = value.strip().replace(',', '')
        if not s or s == '-':
            return None
        try:
            return int(float(s))
        except ValueError:
            return None
    return None


def build_general_output(parsed: dict, slug: str, url: str, now: datetime) -> dict:
    output = {"meta": {"leaderboard": slug, "source_url": url, "fetched_at": now.isoformat(), "last_updated": parsed.get("last_updated"), "model_count": len(parsed["models"])}, "models": parsed["models"]}
    for m in output["models"]:
        m.setdefault("rank", None)
        m.setdefault("model", None)
        m.setdefault("vendor", None)
        m.setdefault("score", None)
        m.setdefault("ci", None)
        m.setdefault("votes", None)
        m["rank"] = _coerce_int(m.get("rank"))
        m["score"] = _coerce_number(m.get("score"))
        m["ci"] = _coerce_number(m.get("ci"))
        m["votes"] = _coerce_int(m.get("votes"))
        m["license"] = normalize_license(m.get("license"))
    return output


def build_agent_output(parsed: dict, slug: str, url: str, now: datetime) -> dict:
    dimensions = []
    seen = set()
    for d in (parsed.get("dimensions") or []):
        if isinstance(d, str):
            name = d.strip()
            if name and name not in seen:
                seen.add(name)
                dimensions.append(name)
    models = parsed["models"]
    output = {"meta": {"leaderboard": slug, "source_url": url, "fetched_at": now.isoformat(), "last_updated": parsed.get("last_updated"), "model_count": len(models), "dimensions": dimensions}, "models": models}
    for m in output["models"]:
        m.setdefault("rank", None)
        m.setdefault("model", None)
        m.setdefault("vendor", None)
        m.setdefault("scores", [])
        m.setdefault("sessions", None)
        m["rank"] = _coerce_int(m.get("rank"))
        m["sessions"] = _coerce_int(m.get("sessions"))
        m["license"] = normalize_license(m.get("license"))
        scores = []
        for s in (m.get("scores") or []):
            if not isinstance(s, dict):
                continue
            name = s.get("name")
            if isinstance(name, str):
                name = name.strip()
            if not name:
                continue
            scores.append({"name": name, "score": _coerce_number(s.get("score")), "ci": _coerce_number(s.get("ci"))})
        m["scores"] = scores
        if not output["meta"]["dimensions"]:
            output["meta"]["dimensions"] = [s["name"] for s in scores]
    return output


def main():
    jina_key = os.environ.get("JINA_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")
    azure_key = os.environ.get("AZURE_OPENAI_KEY")
    azure_endpoint = os.environ.get("AZURE_ENDPOINT")
    azure_deployment = os.environ.get("AZURE_DEPLOYMENT", "gpt-4o")
    azure_api_version = os.environ.get("AZURE_API_VERSION", "2025-01-01-preview")
    use_azure = bool(azure_key and azure_endpoint)
    use_openai = bool(openai_key)
    if not (use_azure or use_openai):
        print("ERROR: Set OPENAI_API_KEY or AZURE_OPENAI_KEY + AZURE_ENDPOINT", file=sys.stderr)
        sys.exit(1)
    print(f"Using {'Azure OpenAI' if use_azure else 'OpenAI'}", file=sys.stderr)
    repo_root = Path(__file__).resolve().parent.parent
    schema_dir = repo_root / "schemas"
    lb_schema = json.loads((schema_dir / "leaderboard.json").read_text())
    agent_schema = json.loads((schema_dir / "agent_leaderboard.json").read_text())
    idx_schema = json.loads((schema_dir / "index.json").read_text())
    lb_validator = Draft202012Validator(lb_schema)
    agent_validator = Draft202012Validator(agent_schema)
    idx_validator = Draft202012Validator(idx_schema)
    print("\nDiscovering leaderboards from overview...", file=sys.stderr)
    overview_text = fetch_page(ARENA_BASE, jina_key)
    leaderboards = discover_leaderboards(overview_text)
    print(f"Found {len(leaderboards)} leaderboards: {[s for s, _ in leaderboards]}", file=sys.stderr)
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    day_dir = repo_root / "data" / date_str
    day_dir.mkdir(parents=True, exist_ok=True)
    results, errors = {}, []
    for slug, url_path in leaderboards:
        print(f"\n{'='*50}", file=sys.stderr)
        print(f"Processing: {slug}", file=sys.stderr)
        try:
            url = f"{ARENA_BASE}{url_path}"
            text = fetch_page(url, jina_key)
            is_agent = slug == "agent"
            parsed = parse_with_llm(text, slug, AGENT_SYSTEM_PROMPT if is_agent else GENERAL_SYSTEM_PROMPT, use_azure=use_azure, azure_key=azure_key, azure_endpoint=azure_endpoint, azure_deployment=azure_deployment, azure_api_version=azure_api_version, openai_key=openai_key)
            if not isinstance(parsed.get("models"), list) or len(parsed["models"]) == 0:
                raise ValueError("LLM returned no models")
            output = build_agent_output(parsed, slug, url, now) if is_agent else build_general_output(parsed, slug, url, now)
            validator = agent_validator if is_agent else lb_validator
            schema_errors = list(validator.iter_errors(output))
            if schema_errors:
                err_msgs = [f"{e.json_path}: {e.message}" for e in schema_errors[:5]]
                raise ValueError(f"Schema validation failed: {'; '.join(err_msgs)}")
            with open(day_dir / f"{slug}.json", "w") as f:
                json.dump(output, f, indent=2, ensure_ascii=False)
            results[slug] = len(parsed["models"])
            time.sleep(2)
        except Exception as e:
            print(f"  ❌ Error: {e}", file=sys.stderr)
            errors.append({"leaderboard": slug, "error": str(e)})
    index = {"date": date_str, "fetched_at": now.isoformat(), "leaderboards": {slug: {"model_count": count} for slug, count in results.items()}, "errors": errors}
    with open(day_dir / "_index.json", "w") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
    latest = {"date": date_str, "path": date_str}
    with open(repo_root / "data" / "latest.json", "w") as f:
        json.dump(latest, f, indent=2)
    idx_errors = list(idx_validator.iter_errors(index))
    if idx_errors:
        print(f"  ❌ Index schema invalid: {idx_errors[0].message}", file=sys.stderr)
    if errors:
        for e in errors:
            print(f"  {e['leaderboard']}: {e['error']}", file=sys.stderr)
        sys.exit(1)
