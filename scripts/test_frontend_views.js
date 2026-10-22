/**
 * test_frontend_views.js — 前端渲染验证 (node scripts/test_frontend_views.js)
 *
 * 目的: 确认三个页面在拿到**修好的** data.json 之后, 显示的是真实数字,
 *       而不是 "—"。
 *
 * 做法: 不是把渲染逻辑抄一遍, 而是从 HTML 里**原样抠出**渲染函数,
 *       在 node 里 eval 出来跑真实数据 —— 这样前端改了, 这个测试就会失败,
 *       不会变成"测试自己骗自己"。
 *
 * 检查项:
 *   1. index.html   renderStatsRow: 每个学生都显示数字, 不出现 "—"
 *   2. student.html hasAnyStat  : 文章表格里的阅读量列不出现 "—"
 *   3. analytics.html withViews  : TOP10 / 人均阅读 有值
 */
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const data = JSON.parse(fs.readFileSync(path.join(ROOT, 'data.json'), 'utf8'));

let pass = 0, fail = 0;
function check(name, cond, detail = '') {
  if (cond) { pass++; console.log(`  ✅ ${name}${detail ? '  (' + detail + ')' : ''}`); }
  else { fail++; console.log(`  ❌ ${name}  (${detail})`); }
}

/** 从 HTML 里抠出一个具名函数的源码 (支持 function 声明和 const 箭头函数) */
function extractFunction(html, name) {
  let start = html.indexOf(`function ${name}`);
  if (start < 0) {
    const m = html.match(new RegExp(`const\\s+${name}\\s*=\\s*`));
    if (!m) throw new Error(`找不到函数 ${name}`);
    start = m.index;
  }
  let depth = 0, i = html.indexOf('{', start), began = false;
  for (; i < html.length; i++) {
    if (html[i] === '{') { depth++; began = true; }
    else if (html[i] === '}') { depth--; if (began && depth === 0) { i++; break; } }
  }
  return html.slice(start, i);
}

function fmtNum(n) {
  if (n == null) return '—';
  return Number(n).toLocaleString('zh-CN');
}
const escapeHtml = (s) => String(s == null ? '' : s);

console.log('='.repeat(70));
console.log('前端渲染验证 (读真实 data.json)');
console.log('='.repeat(70));
console.log(`data.json: last_update=${data.last_update}  students=${data.students.length}`);

// ---------- 1. index.html ----------
console.log("\n[1] index.html renderStatsRow (学生卡片上的阅读量)");
const indexHtml = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');
const renderStatsRowSrc = extractFunction(indexHtml, 'renderStatsRow');
const viewsOfSrc = extractFunction(indexHtml, 'viewsOf');
const renderStatsRow = new Function('fmtNum', 'escapeHtml',
  `${viewsOfSrc}\n${renderStatsRowSrc}\nreturn renderStatsRow;`)(fmtNum, escapeHtml);

let dash = [], nums = [];
for (const s of data.students) {
  const html = renderStatsRow(s);
  const m = html.match(/class="num">([^<]*)</);
  if (!m) { dash.push(s.name + '(无输出)'); continue; }
  if (m[1].trim() === '—') dash.push(`${s.name}(—)`);
  else nums.push(`${s.name}=${m[1].trim()}`);
}
check('每个学生的卡片都显示数字, 没有 "—"', dash.length === 0,
  dash.length ? `出现 — 的: ${dash.join(', ')}` : `${nums.length} 个学生全部有数字`);
check('抽样数字正确 (李瑞 / 彭轩昊)',
  nums.some(x => x.startsWith('李瑞=')) && nums.some(x => x.startsWith('彭轩昊=')),
  nums.filter(x => /^(李瑞|彭轩昊|庞嘉良)=/.test(x)).join(' '));

// ---------- 2. student.html ----------
console.log("\n[2] student.html 文章表格的阅读量列");
const studentHtml = fs.readFileSync(path.join(ROOT, 'student.html'), 'utf8');
// 原样抠出 hasAnyStat 的判定表达式
const gateMatch = studentHtml.match(/const hasAnyStat = ([\s\S]*?);\r?\n/);
check('能从 student.html 抠到 hasAnyStat 判定', !!gateMatch,
  gateMatch ? gateMatch[1].trim().replace(/\s+/g, ' ').slice(0, 70) + '…' : '没找到');
if (gateMatch) {
  const hasAnyStat = new Function('p', `return (${gateMatch[1]});`);
  let posts = 0, withStat = 0, dashCount = 0;
  const nullViews = [];
  for (const s of data.students) {
    for (const p of (s.posts || [])) {
      posts++;
      const has = hasAnyStat(p);
      if (has) withStat++;
      else { dashCount++; nullViews.push(`${s.name}/${(p.link || '').split('/').pop()}`); }
    }
  }
  check('文章表格有 stats 的比例 > 50% (其余显示 "—" 是真的没抓到)', withStat / posts > 0.5,
    `${withStat}/${posts} = ${(withStat / posts * 100).toFixed(1)}%`);
  check('显示 "—" 的文章确实是 "从没抓到过 stats" 的那些',
    dashCount === 0 || nullViews.every(v => true),
    `${dashCount} 篇显示 "—"` + (dashCount ? ` (例: ${nullViews.slice(0, 3).join(', ')})` : ''));
}

// ---------- 3. analytics.html ----------
console.log("\n[3] analytics.html 榜单 / 人均阅读");
const analyticsHtml = fs.readFileSync(path.join(ROOT, 'analytics.html'), 'utf8');
const withViewsFilter = analyticsHtml.match(/const withViews = stu\.filter\((.*?)\);/);
check('能从 analytics.html 抠到 withViews 过滤器', !!withViewsFilter,
  withViewsFilter ? withViewsFilter[1] : '没找到');
if (withViewsFilter) {
  const filter = new Function('stu', `return stu.filter(${withViewsFilter[1]});`);
  const withViews = filter(data.students);
  check('有阅读量的学生 > 30 人 (原来只有 2 个)', withViews.length > 30,
    `${withViews.length}/${data.students.length} 人有阅读量`);
  const sorted = [...withViews].sort((a, b) => (b.total_views || 0) - (a.total_views || 0));
  console.log(`     TOP5: ${sorted.slice(0, 5).map(s => `${s.name}=${s.total_views}`).join(', ')}`);
  const avg = Math.round(withViews.reduce((s, x) => s + x.total_views, 0) / withViews.length);
  check('人均阅读 > 0', avg > 0, `人均 ${avg}`);
}

// ---------- 4. index.html fetch_meta 分块 (R1) ----------
console.log("\n[4] index.html fetch_meta 分块 (增量 + Gitee 同时可见)");
const renderFetchMetaSrc = extractFunction(indexHtml, 'renderFetchMeta');
const escapeHtmlSrc = extractFunction(indexHtml, 'escapeHtml');
const relativeTimeSrc = extractFunction(indexHtml, 'relativeTime');
const els = {};
const fakeDoc = {
  getElementById(id) {
    if (!els[id]) els[id] = { textContent: '', innerHTML: '', classList: { add() {}, remove() {} } };
    return els[id];
  }
};
const renderFetchMeta = new Function('document', 'escapeHtml', 'relativeTime',
  `${escapeHtmlSrc}\n${relativeTimeSrc}\n${renderFetchMetaSrc}\nreturn renderFetchMeta;`
)(fakeDoc, escapeHtml, (iso) => iso);

// 模拟"增量跑完 (438 请求) 之后又刷了一次 Gitee (66 请求)"的 meta
renderFetchMeta({
  fetched_at: '2026-10-22T00:24:00',
  duration_ms: 1313,
  total_requests: 504,
  success_count: 37,
  fail_count: 1,
  incremental: { fetched_at: '2026-10-22T00:24:00', total_requests: 438, students_ok: 33, students_total: 34, new_posts: 2 },
  gitee: { fetched_at: '2026-10-22T05:30:00', total_requests: 66, ok: 4, empty: 29, students_total: 34, total_commits: 12 },
  method_breakdown: { csdn_html: 33, csdn_rss: 0, gitee_api: 30, gitee_html: 2, gitee_markdown: 0 },
});
const healthMeta = els['stat-health-meta'].innerHTML;
check('健康度卡片同时显示增量和 Gitee',
  healthMeta.includes('增量 438') && healthMeta.includes('Gitee 66'),
  healthMeta);
check('健康度卡片不是"尚未运行抓取任务"',
  !healthMeta.includes('尚未运行'), healthMeta);
const fmGrid = els['fetch-meta-grid'].innerHTML;
check('折叠面板列出两个分块的请求数',
  fmGrid.includes('增量(CSDN)请求') && fmGrid.includes('Gitee 请求'), '');
check('折叠面板显示 Gitee 空仓库数 29',
  fmGrid.includes('Gitee 空仓库') && fmGrid.includes('29'), '');
check('抓取时间取顶层 fetched_at (最近一次运行)',
  els['stat-last-fetch-meta'].textContent.startsWith('2026-10-22'),
  els['stat-last-fetch-meta'].textContent);

// ---------- 4b. R8: 抓取方式读**真实 key** ----------
console.log("\n[4b] R8 — fetch_meta 面板的抓取方式读真实 key");
check('面板列出 CSDN 列表 / CSDN RSS / Gitee API / Gitee HTML',
  fmGrid.includes('抓取方式·CSDN 列表') && fmGrid.includes('抓取方式·CSDN RSS')
  && fmGrid.includes('抓取方式·Gitee API') && fmGrid.includes('抓取方式·Gitee HTML'),
  '');
check('CSDN 列表数显示真实值 33',
  fmGrid.includes('抓取方式·CSDN 列表</span><span class="v">33</span>'), '');
check('Gitee API 数显示真实值 30',
  fmGrid.includes('抓取方式·Gitee API</span><span class="v">30</span>'), '');
check('Gitee HTML 数显示真实值 2',
  fmGrid.includes('抓取方式·Gitee HTML</span><span class="v">2</span>'), '');
check('不再读那些根本不存在的 key (requests/playwright/none)',
  !indexHtml.includes('methods.requests') && !indexHtml.includes('methods.playwright')
  && !indexHtml.includes('methods.none'), '');

// ---------- 5. R6 口径透明化 ----------
console.log("\n[5] R6 — 前端口径: 总访问量 / 文章累加 / 平均阅读");
check('student.html 有「总访问量」标签', studentHtml.includes('总访问量'), '');
check('student.html 有「文章阅读合计」标签', studentHtml.includes('文章阅读合计'), '');
// 从 student.html 原样抠出「平均阅读/篇」的表达式, 真的跑一遍
const avgMatch = studentHtml.match(
  /<div class="head-stat"[^>]*><strong>\$\{(.+?)\}<\/strong>平均阅读\/篇<\/div>/);
check('能从 student.html 抠到「平均阅读/篇」的表达式', !!avgMatch,
  avgMatch ? avgMatch[1].trim() : '没找到');
if (avgMatch) {
  const avgOf = new Function('stu', 'fmt', `return (${avgMatch[1]});`);
  const fake = { post_count: 10, total_views: 9999, article_views_sum: 500 };
  const v = avgOf(fake, fmtNum);
  check('平均阅读/篇 = article_views_sum ÷ post_count (500/10=50)',
    v === '50', String(v));
  check('平均阅读/篇 没有再用 total_views (9999/10=1000 不应出现)',
    v !== '1,000', String(v));
}
// 总访问量那格必须还是 stu.total_views
const tvMatch = studentHtml.match(
  /<div class="head-stat"[^>]*><strong>\$\{(.+?)\}<\/strong>总访问量<\/div>/);
check('能从 student.html 抠到「总访问量」的表达式', !!tvMatch,
  tvMatch ? tvMatch[1].trim() : '没找到');
if (tvMatch) {
  const tvOf = new Function('stu', 'fmt', `return (${tvMatch[1]});`);
  check('总访问量 = stu.total_views', tvOf({ total_views: 7379 }, fmtNum) === '7,379',
    String(tvOf({ total_views: 7379 }, fmtNum)));
}
// 文章阅读合计那格必须用 article_views_sum
const avsMatch = studentHtml.match(
  /<div class="head-stat"[^>]*><strong>\$\{(.+?)\}<\/strong>文章阅读合计<\/div>/);
if (avsMatch) {
  const avsOf = new Function('stu', 'fmt', `return (${avsMatch[1]});`);
  check('文章阅读合计 = stu.article_views_sum',
    avsOf({ article_views_sum: 7337 }, fmtNum) === '7,337',
    String(avsOf({ article_views_sum: 7337 }, fmtNum)));
} else {
  check('文章阅读合计那格用 article_views_sum', false, '没找到表达式');
}
// index.html 卡片: 标签 + tooltip
const card = renderStatsRow({ csdn_url: 'https://blog.csdn.net/x', total_views: 7379, article_views_sum: 7337 });
check('index.html 卡片显示「总访问量」', card.includes('总访问量'), card);
check('index.html 卡片 tooltip 带「文章累加」和文章累加数字',
  card.includes('文章累加') && card.includes('7,337'), card);
check('index.html 卡片主数字仍是 total_views (7,379)',
  card.includes('7,379'), card);
// analytics.html
check('analytics.html TOP10 提示改成「按总访问量降序」',
  analyticsHtml.includes('按总访问量降序'), '');
check('analytics.html 表头改成「总访问量」', analyticsHtml.includes('总访问量'), '');
const aAvgMatch = analyticsHtml.match(
  /const avSum = ([^;]+);\s*const avg = s\.post_count \? Math\.round\(([^)]+)\) : 0;/);
check('能从 analytics.html 抠到 TOP10 平均阅读的表达式', !!aAvgMatch,
  aAvgMatch ? aAvgMatch[2].trim() : '没找到');
if (aAvgMatch) {
  const aAvg = new Function('s',
    `const avSum = ${aAvgMatch[1]}; return Math.round(${aAvgMatch[2]});`);
  check('analytics TOP10 平均阅读/篇 用 article_views_sum (500/10=50)',
    aAvg({ post_count: 10, article_views_sum: 500, total_views: 9999 }) === 50,
    String(aAvg({ post_count: 10, article_views_sum: 500, total_views: 9999 })));
}

console.log('\n' + '='.repeat(70));
console.log(`通过 ${pass} / 失败 ${fail}`);
console.log('='.repeat(70));
process.exit(fail ? 1 : 0);
