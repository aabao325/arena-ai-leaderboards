# Arena CF Test Bench

独立的 Cloudflare 验证与抓取测试台。**不影响生产链路**，验证通过后再往生产搬。

## 一分钟看懂

```
双击 8-fetch.bat          ← 生产抓取，直接出数据
双击 0-cf-solver.bat      ← 只验过 CF 能不能过
双击 9-discover.bat       ← arena 改版后重跑，重算分类清单
```

---

### Windows 本地自动抓取

首次配置或修复缺失依赖时，在仓库根目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-deps.ps1
powershell -ExecutionPolicy Bypass -File scripts\setup-task.ps1 -Hourly
# 如需恢复每天 09:00：powershell -ExecutionPolicy Bypass -File scripts\setup-task.ps1 -Time "09:00"
```

依赖安装在 `scripts\.venv`，脚本也会下载 Camoufox 浏览器。以后任务会优先使用该虚拟环境；运行记录保存在 `scripts\_out\run_and_push.log`。首次安装需要联网，并要求本机已安装 Python 3.10 或更高版本。

计划任务会从本机安装目录显式定位 Git，因此不依赖交互式终端的 PATH。首次自动推送前，请为运行任务的 Windows 用户配置 Git 提交姓名、邮箱和 Git Credential Manager 的 GitHub 凭据；凭据应存放在 Windows 凭据管理器中，不要写入脚本或仓库。详细失败原因请查看同一日志文件。

---

## 结论（2026-10-16 实测）

**Camoufox 能过 Cloudflare。问题从来不在指纹，在入口。**

| 测试 | 结果 |
|---|---|
| Camoufox 访问**首页** `arena.ai/zh` | ✅ **约 3 秒通过** |
| Camoufox 访问**榜单页** `/leaderboard/chat/text` | ❌ 403，带 `Cf-Mitigated: challenge` |
| 首页通过后**再导航**到榜单页 | ❌ 仍然被挑战 |
| 在已通过的页面里 `fetch` 榜单页**文档** | ❌ 403 |
| **`fetch` 榜单页 RSC** `?_rsc=x` + `RSC: 1` | ✅ **200，332 KB，模型名命中 500** |
| 同上，根 RSC `/leaderboard?_rsc=x` | ✅ **200，859 KB，命中 2797** |
| `GET /nextjs-api/model-catalog` | ✅ 200，`application/json` |

**Cloudflare 拦的是 document 导航，不拦同源 XHR。**
在已过验证的页面上下文里 `fetch` RSC 端点，数据直接到手 ——
**榜单页文档根本不需要打开。**

`Cf-Mitigated: challenge` 是 CF 官方文档化的挑战信号，说明
`/leaderboard/*` 有一条**按路径的 WAF 规则**。所以换 IP 最多解决一半。

### 反面对照：为什么调启动参数没用

`3-option-matrix.bat` 的四种组合（baseline / `disable_coop` /
`+humanize+geoip` / 主动点击）**全部失败**，且稳定落在
`cf-input=1 iframes=0` —— 说明 CF 根本没推进到交互阶段，属策略性拒绝。

同一台机器、同一个 Camoufox 首页 3 秒就过，证明指纹没问题。
**在错误的方向上调参数是白费功夫。**

---

## 为什么官方改版不用改代码

抓取脚本全程"按结构找"，不按路径找：

| 维度 | 做法 | 改版后 |
|---|---|---|
| 分类清单 | 从首页载荷的 `pills[]` + 导航 `href` 读，站点自己声明 | 自动跟上 |
| 榜单行位置 | 认"行的形状"（有模型名 + 有分数 + 有名次），不认 `entries` 键 | 键改名也不怕 |
| 字段映射 | 别名表 + 缺值给 `null` | 字段改名也认 |
| 分数量纲 | 按量级推断（全在 [-1,1] 内 → 比率型，放大 10000） | 加新分类也成立 |
| 前端导航 | `_index.json` 驱动，`GROUPS` 由数据重建 | 一行前端不用改 |

**实践检验**：本次改版把 URL 从 `/leaderboard/text` 改成
`/leaderboard/chat/text`，旧脚本直接全灭（见下节）。新脚本不用改。

---

## 踩过的坑（都是实测，不是推测）

### 1. carry-over 会把失败伪装成成功

旧链路抓取失败时，`carry_over_last_snapshot()` 把前一天的旧文件
**原样复制**到今天。结果 2026-10-15 快照里 11/12 个分类是
2026-10-12 的数据，`image-edit` 甚至是 10-08 的 —— **而没有任何报错**。

判据：看 `meta.source_url` 和 `meta.fetched_at`。
复制过来的文件 `source_url` 还是旧 URL，`fetched_at` 还是旧时间戳。

**新脚本不顶替**：失败就让它缺，`_index.json` 的 `failed[]` 里显式列出。

### 2. 判据只能用 `document.title`

早期用 body 里有没有 `security verification` 判断是否还在挑战页。
**那是 Jina Reader 的措辞，CF 挑战页正文里没有这串字** ——
于是在挑战页上也返回"未在挑战"，把失败报成成功。

假阳性比报错危险得多：它会让你以为通了。

### 3. `ci_low` / `ci_high` 是偏移量，不是绝对值

前端这样用：

```javascript
min = Math.min(min, m.score - (m.ci_low ?? 0));
max = Math.max(max, m.score + (m.ci_high ?? 0));
```

RSC 给的是**绝对区间**（`ratingLower=1515.09 / rating=1523.90`）。
直接存进去会让前端把置信区间画到错的位置。
必须存 `abs(rating - ratingLower) = 9`。

### 4. 分数量纲不同，`round()` 会把 agent 全变成 0

- `chat/text` 等：`rating` ≈ 950–1811
- `agent`：`avgScore` ≈ 0.13–0.19

`schema` 的 `score` 是整数，`round(0.1887) = 0` ——
**整个 agent 榜会全部变成 0 分，排名信息彻底消失。**

对策：按量级推断，比率型放大 10000，并在 `meta.score_scale` 记下换算。

### 5. agent 的负分要走 signals

`agent/alignment` 的分数分布是 `-0.20 .. 0.17`，
而 schema 要求 `ci_low / ci_high >= 0`。

对策：**整榜级**判断（不是按行），只要榜内出现负数，
就把 `score` 整列置 `null`、分数挪进 `signals.avg_score`。
按行处理会让一半行有分数一半没有，前端排序和柱状图都会错乱。

### 6. `persistent_context=True` 返回的是 `BrowserContext`

不是 `Browser`。后者才有 `.contexts[]`，前者只能 `await context.new_page()`。
写错会双 `AttributeError`，全崩在创建页面。查 `camoufox/async_api.py:120-122`。

### 7. Firefox profile 用 `parent.lock` 互斥，第二个进程**慢速失败**

不会立刻报错，而是卡到 Playwright 超时（实测 **86–146 秒**）再 SIGTERM。
慢速失败难查，且中途打断有损坏 profile 的风险。
`cf_solver.py` 自己抢锁，抢不到立刻 `ProfileBusy`。

### 8. 代理白名单要放行下划线

`leaderboard-proxy.php` 的正则原本是 `[a-z0-9-]+`，
`_index.json` 会被判非法 → 首页直接 400。已改成 `[a-z0-9_-]{1,64}`。

---

## GitHub Actions 抓不了（已确认）

| 层面 | 事实 |
|---|---|
| IP | GH 托管 runner 每次 run 新 VM、**新出口 IP**，无法跨 run 保持 |
| 后果 | **连"铸 cookie"都过不去**，不只是"复用"过不去 |
| 信誉 | 出口是 Azure 网段，ranges 在 `api.github.com/meta` **公开**（约 7,038 条前缀、2,830 万 IPv4），全世界免费用户共用 |
| 时机 | CF 在**网络层、JS 执行前**就给数据中心 ASN 打分 |

**问题不在指纹，在 IP。** 真浏览器、反检测补丁都救不回来。

`cf_clearance` 也指望不上跨 IP 复用：社区（含 Rust `crw-renderer` 的工程实现，
缓存键 `host|UA|IP`）一致认定它绑定 IP + UA + TLS 指纹。换 IP 是**软失败**
（静默回挑战页，不报错）。官方文档只说 "tied to the visitor and device"，
未提 IP，但按绑定处理才安全。

**⇒ 抓取留在本机，CI 只做校验与发布。**

---

## 文件

```
0-cf-solver.bat      过 CF 自测（六项自检，证明上下文真的可用）
1-path-compare.bat   路径对比（证明 403 的存在）
2-rsc-probe.bat      RSC 探测（当初发现入口的那把钥匙）
3-option-matrix.bat  参数矩阵（反面对照：证明调参数无用）
4-quick-run.bat      单次快速跑
5-rsc-fetch.bat      早期取数原型
6-map-check.bat      载荷 → schema 映射验证（离线）
7-grab.bat           定向抓取（调 URL 清单用）
8-fetch.bat          ★ 生产抓取
9-discover.bat       ★ 分类清单发现（改版后重跑）
```

```
cf_solver.py    Camoufox + 过 CF（独立可复用）
rsc_schema.py   flight 解析 + 结构发现 + 归一（抗改版核心）
fetch_rsc.py    生产抓取
discover.py     分类清单实测
flight.py       早期 flight 解析器（保留）
test_home.mjs   前端首页渲染离线测试（在 ../leaderboard/）
```

`_out/` 是运行产物（日志 / DOM dump / 载荷），已 gitignore。
`profile/` 含 `cf_clearance` 等会话凭据，**已 gitignore，绝不入库**。

---

## 数据结构

```
_out/data/<日期>/
  _index.json          分类索引：分组 + 每榜前三名（首页一次请求就够）
  chat-text.json       各榜完整数据（15 个字段，对齐现有 schema）
  ...
_out/data/latest.json  快照指针（fetched_at 是版本信号）
```

`_index.json` 结构：

```json
{
  "date": "2026-10-18",
  "fetched_at": "2026-10-18T...",
  "board_count": 12,
  "failed": [],
  "groups": [
    { "key": "chat", "label": "对话",
      "boards": [
        { "slug": "chat-text", "title": "Best Text", "score_unit": "rating",
          "model_count": 416,
          "top_models": [{"model":"Gemini 4 Argon (High)","vendor":"Google","score":1523.9,"rank":1}] }
      ] }
  ]
}
```

前端 `GROUPS` 由它重建，所以官方增减分类时**前端零改动**。

---

## 用法细节

```bash
# 只抓一个榜，快速验证
python fetch_rsc.py --only chat/text

# 只抓不写（看统计）
python fetch_rsc.py --dry-run

# 无头（Windows 上没有 Xvfb，--virtual 只在 Linux 可用）
python fetch_rsc.py --headless

# 改版后重算分类清单
python discover.py
```

## 判读日志

```
标题          = 'Just a moment...'   ← 还在挑战页
cf-input      = 1                    ← 页面上有 cf-turnstile-response 元素
iframes       = 0                    ← 验证 iframe 未注入 = 挑战没真正启动
Cf-Mitigated  = challenge            ← CF 官方挑战信号
```

**`cf-input=1` 但 `iframes=0`** = 策略性拒绝，不是破解手法不到位。
看到这个组合就该换 IP 或换入口，不要继续调点击逻辑。

## 注意

- 有头模式是必须的（本机 Windows 无 Xvfb）
- 首次运行会下载 Camoufox 浏览器（约几百 MB）
- 本机 camoufox 是 0.5.4，PyPI 已 0.5.8；上游 README 的新 API 本机没有
