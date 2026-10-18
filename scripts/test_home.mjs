/*
 * 首页渲染的离线验证：不开浏览器、不连网络
 *
 * 从 index.html 里抽出渲染相关函数，喂一份真实的 _index.json，
 * 检查产出的 HTML 是否包含预期内容。
 *
 * 为什么要这一步：首页改造涉及 applyIndex（用数据重建 GROUPS）、
 * renderHome（卡片）、buildTableHTML（指标列判定）三处交互，
 * 光看代码看不出 GROUPS 重建后 LEAF_INDEX 有没有跟着更新。
 *
 * 用法：
 *   node test_home.mjs                    # 用同目录的 _index_sample.json
 *   node test_home.mjs <path/to/index.json>
 */
import fs from "node:fs";
import path from "node:path";

const here = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"));

// ── 从 index.html 抽脚本 ──
// 这个测试跟着抓取脚本放在**数据仓库**里，但被测的页面在**工具仓库**的
// leaderboard/。两个仓库是平级目录，所以从 scripts/ 往上两级再进
// aabao-tools/leaderboard。也支持 LEADERBOARD_HTML 环境变量直接指定，
// 免得目录再挪一次就找不到。
const CANDIDATES = [
  process.env.LEADERBOARD_HTML || "",
  path.resolve(here, "../../aabao-tools/leaderboard/index.html"),
  path.resolve(here, "../../leaderboard/index.html"),
  path.resolve(here, "../../../aabao-tools/leaderboard/index.html"),
].filter(Boolean);
const htmlPath = CANDIDATES.find(p => fs.existsSync(p));
if (!htmlPath) {
  console.error("找不到 index.html，尝试过:\n  " + CANDIDATES.join("\n  "));
  console.error("可用 LEADERBOARD_HTML=<path> 环境变量指定");
  process.exit(2);
}
const html = fs.readFileSync(htmlPath, "utf8");

function grab(name, kind = "function") {
  // 抓 `function xxx(...) { ... }` 或 `const xxx = ...` 到下一个顶层声明
  if (kind === "function") {
    const re = new RegExp(`^function ${name}\\s*\\([^)]*\\)\\s*\\{`, "m");
    const m = re.exec(html);
    if (!m) throw new Error(`找不到函数 ${name}`);
    let i = m.index + m[0].length, depth = 1;
    while (i < html.length && depth > 0) {
      if (html[i] === "{") depth++;
      else if (html[i] === "}") depth--;
      i++;
    }
    return html.slice(m.index, i);
  }
  const re = new RegExp(`^const ${name}\\s*=[^;]*;`, "ms");
  const m = re.exec(html);
  if (!m) throw new Error(`找不到常量 ${name}`);
  return m[0];
}

// 默认用真实的最新快照索引，这样测的就是线上真会喂给前端的那份结构。
function latestIndexPath() {
  const cands = [
    path.resolve(here, "../data/latest.json"),        // 数据仓库根的 data/
    path.resolve(here, "../data/_index.json"),
  ];
  try {
    const lp = cands.find(p => fs.existsSync(p));
    if (!lp) return null;
    const latest = JSON.parse(fs.readFileSync(lp, "utf8"));
    const dir = latest.path || latest.date;
    if (!dir) return null;
    const idx = path.resolve(path.dirname(lp), dir, "_index.json");
    return fs.existsSync(idx) ? idx : null;
  } catch { return null; }
}

const samplePath = process.argv[2] || latestIndexPath();
if (!samplePath) {
  console.error("找不到 _index.json —— 先跑 fetch_rsc.py 生成数据，或显式传路径");
  process.exit(2);
}
const sample = JSON.parse(fs.readFileSync(samplePath, "utf8"));
console.log(`索引: ${samplePath}\n`);

// ── 最小 DOM/全局桩 ──
const store = {};
globalThis.document = {
  getElementById: (id) => (store[id] ??= {
    id, innerHTML: "", textContent: "", hidden: false,
    classList: { add(){}, remove(){}, toggle(){} },
    addEventListener(){}, querySelectorAll: () => [],
  }),
  querySelectorAll: () => [],
  createElement: () => ({ style:{}, classList:{add(){},remove(){}}, appendChild(){} }),
};
globalThis.localStorage = { getItem: () => null, setItem(){} };
globalThis.history = { replaceState(){} };
globalThis.location = { pathname: "/", search: "" };
globalThis.URLSearchParams = class { get(){ return null; } };

// ── 复制被依赖的定义 ──
const ICONS = (() => {
  const m = /^const ICONS = \{[\s\S]*?^\};/m.exec(html);
  return eval("(" + m[0].replace("const ICONS = ", "").replace(/;$/, "") + ")");
})();

const UI_zero = {};
const LANGS = ["zh", "en"];
function t(key, ...args) {
  const v = UI_zero[key];
  return typeof v === "function" ? v(...args) : (v ?? key);
}

const GROUPS_SEED = "let GROUPS = []; let LEAF_INDEX = {}; let INDEX = null;";
const state = { lang:"zh", activeLeaf:"agent", activeGroup:"agent", path:"2026-10-18/" };

const src = [
  GROUPS_SEED,
  `const ICONS = ${JSON.stringify(ICONS)};`,
  grab("rebuildLeafIndex"),
  `const GROUP_ICONS = ${/const GROUP_ICONS = \{[^}]*\};/.exec(html)[0].slice(15)}`,
  grab("groupIcon"),
  grab("applyIndex"),
  `const escapeAttr = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));`,
  grab("fmtTopScore"),
  grab("renderHome"),
  // buildTableHTML 里用到的几个小函数，统一给最小实现
  `function t(k, ...a){ const R=${JSON.stringify({
    thRank:"排名", thSpread:"区间", thModel:"模型", thLicense:"许可", thScore:"分数",
    thVotes:"票数", thSessions:"会话", thPrice:"价格", thContext:"上下文",
    licenseOpen:"开源", licenseProp:"闭源", preliminary:"预发布", unknownVendor:"未知",
    noMatch:"无匹配", homeNoIndex:"x", homeNoPreview:"暂无预览",
  })}; if(k==="homeBoardCount") return a[0]+" 个榜单"; if(k==="homeViewBoard") return "查看完整榜单 · "+a[0]+" 个模型"; if(k==="homeFailed") return "抓取失败: "+a[0]; return typeof R[k]==="function"?R[k](...a):(R[k]??k); }`,
  `const state = ${JSON.stringify(state)};`,
  grab("computeScoreDomain"),
  `const SIGNAL_LABELS = { zh:{net_improvement:"净提升",confirmed_success:"任务确认成功",praise_vs_complaint:"好评/差评比",steerability:"可纠偏性",bash_recovery:"命令纠错恢复",tool_hallucination:"工具幻觉率",avg_score:"综合得分"}, en:{} };`,
  grab("signalLabel") ,
  `const PRIMARY_SIGNAL_KEY = "net_improvement";`,
  grab("primarySignalKey"),
  `const INTERNAL_SIGNAL_KEYS = new Set(["avg_score"]);`,
  grab("computeSignalMax"),
  `function renderPrimarySignalCell(){ return "<cell-primary/>"; }`,
  `function renderSecondarySignalCell(){ return "<cell-secondary/>"; }`,
  `function renderScoreBar(){ return "<scorebar/>"; }`,
  `function formatPrice(){ return ""; }`,
  `function formatContext(){ return ""; }`,
  `function vendorColor(){ return "#888"; }`,
  `function vendorInitial(v){ return (v||"?").slice(0,1); }`,
  `function escapeHtml(s){ return String(s ?? ""); }`,
  grab("buildTableHTML"),
  "return ({ applyIndex, renderHome, buildTableHTML,",
  "   get GROUPS(){ return GROUPS }, get LEAF_INDEX(){ return LEAF_INDEX },",
  "   get INDEX(){ return INDEX } })",
].join("\n\n");

const api = new Function(src)();

// ── 断言 ──
let pass = 0, fail = 0;
function ok(name, cond, extra = "") {
  if (cond) { pass++; console.log(`  ✅ ${name}`); }
  else { fail++; console.log(`  ❌ ${name} ${extra}`); }
}

console.log("=".repeat(70));
console.log("首页渲染离线测试");
console.log("=".repeat(70));

// 1. applyIndex 用索引重建了 GROUPS / LEAF_INDEX
const applied = api.applyIndex(sample);
ok("applyIndex 返回 true", applied);
ok(`GROUPS 分组数 = ${sample.groups.length}`, api.GROUPS.length === sample.groups.length,
   `实际 ${api.GROUPS.length}`);
const leafCount = Object.keys(api.LEAF_INDEX).length;
const boardCount = sample.groups.reduce((n, g) => n + (g.boards || []).length, 0);
ok(`LEAF_INDEX 覆盖全部 ${boardCount} 个榜`, leafCount === boardCount, `实际 ${leafCount}`);

// 2. 每个 leaf 都能反查到自己的 group
let badLeaf = null;
for (const g of sample.groups) for (const b of g.boards || []) {
  const e = api.LEAF_INDEX[b.slug];
  if (!e || e.group.key !== g.key) badLeaf = b.slug;
}
ok("每个榜都能反查到所属分组", !badLeaf, `坏项 ${badLeaf}`);

// 3. renderHome 产出卡片
api.renderHome();
const homeHtml = document.getElementById("homeView").innerHTML;
const cardCount = (homeHtml.match(/class="home-card"/g) || []).length;
ok(`首页渲染出 ${boardCount} 张卡片`, cardCount === boardCount, `实际 ${cardCount}`);
ok("卡片带 data-slug", homeHtml.includes('data-slug="chat-text"'));
ok("有预览的卡显示前三名", homeHtml.includes("home-top-row"));

// 占位文案要用**构造数据**测，不能用真实索引断言。
// 真实数据里所有榜都有 top_models（16/16），这条断言就永远为假而误报失败；
// 而占位逻辑本身仍需要被覆盖 —— 官方哪天推出个空榜就会走到这里。
{
  const savedIndex = api.INDEX;
  const probe = {
    date: "2026-01-01", fetched_at: "2026-01-01T00:00:00+00:00",
    board_count: 1, failed: [],
    groups: [{ key: "chat", label: "对话", boards: [
      { slug: "empty-board", title: "空榜", model_count: 0, top_models: [] },
    ]}],
  };
  api.applyIndex(probe);
  api.renderHome();
  const probeHtml = document.getElementById("homeView").innerHTML;
  ok("空榜显示占位文案", probeHtml.includes("home-empty"), "");
  // 复原，后续断言仍针对真实数据
  api.applyIndex(savedIndex);
  api.renderHome();
}

// 4. agent 榜走指标列（不能退化成普通表）
const agentRow = {
  rank:1, rank_spread:[1,2], model:"Claude Opus 5.5 (Max)", vendor:"Anthropic",
  license:"proprietary", score:null, ci_low:null, ci_high:null, preliminary:false,
  votes:10551, price_prompt:4, price_completion:20, context_length:null,
  signals:{ task_outcome_explicit:{value:0.20,ci:0.04}, praise_complaint:{value:0.39,ci:0.10},
            avg_score:{value:0.188,ci:0.027} },
};
const agentHtml = api.buildTableHTML([agentRow], [null]);
ok("agent 榜渲染指标列", agentHtml.includes("<cell-primary/>") || agentHtml.includes("<cell-secondary/>"));
ok("内部键 avg_score 不进指标列", !agentHtml.includes("综合得分"), "");
ok("普通榜不渲染指标列", !api.buildTableHTML([{
  rank:1, rank_spread:[1,1], model:"x", vendor:"y", license:"open",
  score:1500, ci_low:5, ci_high:5, preliminary:false, votes:1,
  price_prompt:null, price_completion:null, context_length:null, signals:null,
}], [null]).includes("<cell-primary/>"));

console.log("\n" + "=".repeat(70));
console.log(`${pass} 通过 / ${fail} 失败`);
process.exit(fail ? 1 : 0);
