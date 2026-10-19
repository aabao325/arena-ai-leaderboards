#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
定时抓取 + 推送：一条命令完成 抓 → 提交 → 推送

给 Windows 任务计划调用，也可以手动跑。设计目标是**失败要响亮**：
任务计划里的脚本失败是静默的，没人看得到，所以任何一步出错都必须
以非 0 退出码结束并留日志，配合任务计划的通知才有意义。

流程：
  1. 抓取（fetch_rsc.py）→ 写 data/<日期>/
  2. 有变化才提交（git add data/ + scripts/）
  3. 推送；失败则重试一次（网络抖动很常见）

用法：
    python run_and_push.py                # 抓 + 提交 + 推送
    python run_and_push.py --dry-run      # 只抓，不动 git
    python run_and_push.py --no-push      # 抓 + 提交，不推送

退出码：0 成功 / 1 抓取失败 / 2 提交或推送失败
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = Path(__file__).parent.resolve()
REPO = HERE.parent
LOG_DIR = HERE / "_out"
LOG = LOG_DIR / "run_and_push.log"


def log(msg=""):
    line = str(msg)
    print(line, flush=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {line}\n")


def resolve_git():
    """Find Git explicitly; scheduled tasks do not inherit the interactive PATH."""
    candidates = []
    configured = os.environ.get("ARENA_GIT_EXE")
    if configured:
        candidates.append(Path(configured))

    on_path = shutil.which("git")
    if on_path:
        candidates.append(Path(on_path))

    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if base:
            candidates.append(Path(base) / "Git" / "cmd" / "git.exe")

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        installs = sorted(
            Path(local_app_data).glob("github-copilot-git-*/cmd/git.exe"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        candidates.extend(installs)

    seen = set()
    for candidate in candidates:
        normalized = str(candidate.resolve()).casefold()
        if normalized not in seen and candidate.is_file():
            return str(candidate.resolve())
        seen.add(normalized)

    raise FileNotFoundError(
        "Git executable not found. Install Git for Windows or set ARENA_GIT_EXE."
    )


def run(cmd, cwd=None, timeout=900):
    """跑一条命令，返回 (退出码, 合并输出)。"""
    try:
        p = subprocess.run(
            cmd, cwd=str(cwd or REPO), capture_output=True,
            text=True, timeout=timeout, encoding="utf-8", errors="replace",
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"超时（{timeout}s）"
    except OSError as exc:
        return 127, "Unable to start command: " + str(exc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只抓取，不碰 git")
    ap.add_argument("--no-push", action="store_true", help="提交但不推送")
    ap.add_argument("--keep", type=int, default=10, help="保留最近 N 份快照")
    args = ap.parse_args()

    log("=" * 66)
    log(f"定时抓取开始  {datetime.now():%Y-%m-%d %H:%M:%S}")

    # Resolve Git before spending time on a fetch.
    git_exe = None
    if not args.dry_run:
        try:
            git_exe = resolve_git()
        except OSError as exc:
            log(f"Git preflight failed: {exc}")
            return 2
        log(f"Git: {git_exe}")

    # ── 1. 抓取 ──
    code, out = run(
        [sys.executable, str(HERE / "fetch_rsc.py"), "--keep", str(args.keep)],
        timeout=900,
    )
    # fetch_rsc 的输出很长，只留尾部
    tail = "\n".join(out.strip().splitlines()[-25:])
    log("--- 抓取输出（尾部）---")
    log(tail)

    if code != 0:
        log(f"✗ 抓取失败，退出码 {code}")
        return 1

    # ── 2. 提交 ──
    if args.dry_run:
        log("--dry-run：跳过提交与推送")
        return 0

    code, out = run([git_exe, "add", "data", "scripts"])
    if code != 0:
        log(f"✗ git add 失败: {out[:300]}")
        return 2

    code, out = run([git_exe, "diff", "--cached", "--quiet"])
    if code == 0:
        log("数据无变化，无需提交")
        return 0
    if code != 1:
        log(f"git diff --cached failed: {out[:400]}")
        return 2

    date_str = datetime.now().strftime("%Y-%m-%d")
    msg = f"data: update leaderboards {date_str}"
    code, out = run([git_exe, "commit", "-m", msg])
    if code != 0:
        log(f"✗ git commit 失败: {out[:400]}")
        return 2
    log(f"✓ 已提交: {msg}")

    # ── 3. 推送（失败重试一次，网络抖动常见）──
    if args.no_push:
        log("--no-push：跳过推送")
        return 0

    for attempt in (1, 2):
        code, out = run([git_exe, "push"], timeout=300)
        if code == 0:
            log(f"✓ 已推送（第 {attempt} 次尝试）")
            return 0
        log(f"⚠ 推送失败（第 {attempt} 次）: {out.strip()[:300]}")
        if attempt == 1:
            # 可能是远程有别人的提交，先 rebase 再试
            pull_code, pull_out = run(
                [git_exe, "pull", "--rebase", "--autostash"], timeout=300
            )
            if pull_code != 0:
                log(f"git pull --rebase failed: {pull_out.strip()[:400]}")
                return 2
            time.sleep(5)

    log("✗ 推送连续两次失败 —— 需要人工检查网络或凭据")
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        log("被中断")
        raise SystemExit(130)
