/**
 * test_trend_page.js — trend.html 前端测试
 *
 * 覆盖:
 *   1. trend.html 存在且结构完整 (关键 DOM 节点)
 *   2. 核心统计口径: 「总访问量」与「文章阅读合计」不能混称
 *   3. 图表绘制函数: loadAll / renderLine / renderBar / renderRank 存在且可抠出
 *   4. 每日取峰值逻辑 (05:30 覆盖会写残缺值, 峰值才是真实值)
 *   5. history-real/index.json 格式正确, 且列出的文件都真实存在
 *   6. 无外部依赖 (不引第三方图表库, 与项目风格一致)
 *   7. 无横向溢出风险 (CSS 有 overflow 处理 / 表格不固定宽)
 *
 * 用法: node scripts/test_trend_page.js
 */
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
let pass = 0, fail = 0;

function check(name, cond, extra) {
  if (cond) { pass++; console.log('  [PASS] ' + name); }
  else { fail++; console.log('  [FAIL] ' + name + (extra ? '  → ' + extra : '')); }
}

/** 从 html 里抠出某个 JS 函数体 (括号配平) */
function extractFn(html, name) {
  const src = html.replace(/<script[^>]*>/g, '').replace(/<\/script>/g, '');
  let start = src.indexOf('function ' + name);
  if (start < 0) start = src.indexOf('async function ' + name);
  if (start < 0) return null;
  let i = src.indexOf('{', start), depth = 0, began = false;
  for (; i < src.length; i++) {
    if (src[i] === '{') { depth++; began = true; }
    else if (src[i] === '}') { depth--; if (began && depth === 0) { i++; break; } }
  }
  return src.slice(start, i);
}

// ---------------------------------------------------------------- 1. 结构
console.log('\n[1] trend.html 结构完整性');
const tp = path.join(ROOT, 'trend.html');
check('trend.html 存在', fs.existsSync(tp));
if (!fs.existsSync(tp)) { console.log('\n结果: 通过 0 / 失败 1'); process.exit(1); }
const html = fs.readFileSync(tp, 'utf8');

for (const id of ['line-chart', 'bar-chart', 'rank-body', 's-current', 's-delta',
                  's-median', 's-posts', 's-students', 'range', 'pts']) {
  check(`有 #${id} 节点`, html.includes(`id="${id}"`), '');
}
check('有 <nav class="topnav"> 导航', html.includes('class="topnav"'), '');
check('nav 里有 trend.html 自身链接 (active)', /class="tab active" href="trend\.html"/.test(html), '');
check('nav 里有 index/analytics/ranking 三页链接',
  html.includes('href="index.html"') && html.includes('href="analytics.html"') &&
  html.includes('href="ranking.html"'), '');
check('引用了 history-real/ 目录', html.includes('history-real/'), '');
check('有暗色模式切换按钮', html.includes('dark-toggle'), '');

// ---------------------------------------------------------------- 2. 口径
console.log('\n[2] 口径: 不混称「总访问量」与「文章阅读合计」');
check('页面用「总阅读」表述且非「总阅读量」混称', html.includes('总阅读'), '');
// 趋势页统计的是班级总阅读 (total_views), 不应出现文章阅读合计的口径混淆
check('trend.html 未错误引入「文章阅读合计」标签', !html.includes('文章阅读合计'),
  '趋势页是班级累计口径, 不该混入文章累加口径');
check('注释说明「峰值才是真实值」', html.includes('峰值'), '');
check('注释说明 05:30 覆盖问题', html.includes('05:30'), '');

// ---------------------------------------------------------------- 3. 函数
console.log('\n[3] 图表绘制函数');
for (const fn of ['loadAll', 'renderLine', 'renderBar', 'renderRank']) {
  const body = extractFn(html, fn);
  check(`能抠出 function ${fn}`, !!body && body.length > 50, body ? `长度=${body.length}` : '找不到');
}
const loadAll = extractFn(html, 'loadAll') || '';
check('loadAll 用 fetch 读 history-real/', loadAll.includes('fetch('), '');
check('loadAll 优先读 index.json 而不是盲探', loadAll.includes('index.json'), '');
check('loadAll 解析 class_stats.csdn.total_views', loadAll.includes('total_views'), '');
check('loadAll 收集 students_summary', loadAll.includes('students_summary'), '');
const renderLine = extractFn(html, 'renderLine') || '';
check('renderLine 画折线 path', renderLine.includes('class="line"') || renderLine.includes(".line"), '');
check('renderLine 画面积 path', renderLine.includes('area'), '');
check('renderLine 加 hover 提示', renderLine.includes('mousemove'), '');
const renderBar = extractFn(html, 'renderBar') || '';
check('renderBar 画 rect 柱子', renderBar.includes('rect'), '');
check('renderBar 区分正负 (neg 类)', renderBar.includes('neg'), '');
check('renderBar 有零基线', renderBar.includes('zeroY'), '');

// ---------------------------------------------------------------- 4. 取峰值
console.log('\n[4] 每日取峰值逻辑');
check('按天分组 byDay', loadAll.includes('byDay'), '');
check('分组时取更大值 (峰值)', /byDay\[p\.day\]\s*\|\|\s*p\.total_views\s*>\s*byDay\[p\.day\]\.total_views/.test(loadAll), '');

// ---------------------------------------------------------------- 5. index.json
console.log('\n[5] history-real/index.json 格式');
const hrDir = path.join(ROOT, 'history-real');
const idxPath = path.join(hrDir, 'index.json');
check('history-real/index.json 存在', fs.existsSync(idxPath), '');
if (fs.existsSync(idxPath)) {
  const idx = JSON.parse(fs.readFileSync(idxPath, 'utf8'));
  check('index.json 有 files 数组', Array.isArray(idx.files) && idx.files.length > 0, `count=${idx.count}`);
  check('index.json 有 count 字段', typeof idx.count === 'number', '');
  check('files 里不含 index.json 自身', !idx.files.includes('index.json'), '');
  let missing = 0;
  for (const f of idx.files) if (!fs.existsSync(path.join(hrDir, f))) missing++;
  check('index.json 列出的文件全部存在', missing === 0, `缺失 ${missing} 个`);
  check('快照文件数 >= 30 (有足够趋势)', idx.files.length >= 30, `实际 ${idx.files.length}`);
}

// ---------------------------------------------------------------- 6. 无外部依赖
console.log('\n[6] 无外部依赖');
const extScript = html.match(/<script[^>]*src=["']([^"']+)["']/gi) || [];
check('没有外部 <script src>', extScript.length === 0, extScript.join(', '));
const extLink = html.match(/<link[^>]*href=["'](http[^"']+)["']/gi) || [];
check('没有外部 CSS 引用', extLink.length === 0, extLink.join(', '));
check('用了原生 SVG (svg 标签)', html.includes('<svg'), '');
check('未引入 Chart.js/ECharts 等库',
  !/chart\.js|echarts|plotly|apexchart/i.test(html), '');

// ---------------------------------------------------------------- 7. 布局健壮性
console.log('\n[7] 布局健壮性');
check('有 viewport meta (移动端)', html.includes('name="viewport"'), '');
check('body 有横向 padding', /body\s*\{[^}]*padding/.test(html), '');
check('表格宽度 100% (不撑破)', /table\s*\{[^}]*width:\s*100%/.test(html), '');
check('SVG 有 viewBox (自适应宽度)', html.includes('viewBox='), '');
check('SVG 用 width:100%', /chart-svg\s*\{[^}]*width:\s*100%/.test(html), '');
check('使用 CSS 变量 (暗色模式可切换)', html.includes('--parchment') && html.includes('--brand'), '');

// ---------------------------------------------------------------- 汇总
console.log('\n' + '='.repeat(50));
console.log(`结果: 通过 ${pass} / 失败 ${fail}`);
console.log('='.repeat(50));
process.exit(fail === 0 ? 0 : 1);
