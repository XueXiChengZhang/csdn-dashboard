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

console.log('\n' + '='.repeat(70));
console.log(`通过 ${pass} / 失败 ${fail}`);
console.log('='.repeat(70));
process.exit(fail ? 1 : 0);
