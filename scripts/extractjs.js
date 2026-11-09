/**
 * extractjs.js — 从 HTML 里抠出一个具名 JS 函数的源码。
 *
 * 流程:
 *   1. 把 HTML 注释 `<!-- -->` 和带引号的标签属性 (src= / href= ...) 遮成等长空白。
 *      否则 `<script src="a.js"></script>` 会被上一条语句的 `//` 行注释吞掉,
 *      `</body></html>` 就逃进 JS 代码里。
 *   2. 按花括号配平找函数体; 注释、字符串、模板字符串、正则字面量里的括号不计数。
 *   3. 抠出来的片段必须能用 vm.Script 编译通过, 否则从同名出现的下一个位置再试
 *      (嵌套同名函数、调用 `fn({\n ... })` 里的对象字面量都会干扰起点)。
 *
 * 旧版只数 `{` / `}`, 从名字的第一个出现处开数, 也不会跳过正则字面量:
 * 正则 `/[&<>"']/` 里的引号让后面整段被当成字符串, 片段截断后 new Function
 * 抛 SyntaxError —— 测试跑到一半崩掉, 后面的断言一条都没执行。
 */
'use strict';

const vm = require('vm');

const MASKABLE = [/<!--[\s\S]*?-->/g, /(?:src|href)\s*=\s*(?:"[^"]*"|'[^']*')/g];
// `/` 出现在这些记号之后才是正则字面量的开头
const REGEX_PREFIX = /[(,=:[!&|?{};+\-~%<>]}*%]$/;
// 引号在本行内能找到配对 (模板字符串例外, 可以跨行)
const STRING_END = { "'": /' /, '"': /"/, '`': /`/ };

/** HTML 注释和带引号的标签属性遮成等长空白 (保持行号与 CRLF 不变) */
function maskHtml(html) {
  for (const re of MASKABLE) {
    html = html.replace(re, m => m.replace(/[^\r\n]/g, ' '));
  }
  return html;
}

function extractFunction(rawHtml, name) {
  const html = maskHtml(rawHtml);
  const re = new RegExp(`function\\s+${name}\\s*\\S*|const\\s+${name}\\s*=`, 'g');
  let m, src = '';
  while ((m = re.exec(html))) {
    src = braceSpan(html, m.index);
    if (src && isParseable(src)) return rawHtml.slice(m.index, m.index + src.length);
  }
  throw new Error(`找不到可用的函数 ${name}`);
}

function isParseable(src) {
  if (!src.includes('{')) return false;
  try {
    new vm.Script(src);
    return true;
  } catch {
    return false;
  }
}

/** 从 start 起找第一个 `{` 并配平; 配平到 0 (且不是 `else` 等续接) 时返回片段, 失败返回 '' */
function braceSpan(html, start) {
  let depth = 0, began = false, quote = null;
  // 停在行尾的 '\n' 上: for 的 i++ 再把它跳过, 行注释里的 `}` 就不会被数到
  const lineEnd = p => { const s = html.slice(p).search(/\n/); return s < 0 ? html.length : p + s; };
  for (let i = html.indexOf('{', start); i >= 0 && i < html.length; i++) {
    const c = html[i], n = html[i + 1];
    // 正则只在 `( , = : [ ! & | ? { } ;` 等记号之后出现; 往回多看几个字符,
    // 才能覆盖 `String(s).replace(/...` 这种 `.replace(` 结尾的情况
    const prev = html.slice(Math.max(start, i - 12), i).trimEnd();

    if (quote) {
      if (c === '\\') i++;
      else if (c === quote) quote = null;
      continue;
    }
    if (c === '/' && n === '/') { i = lineEnd(i); continue; }
    if (c === '/' && n === '*') {
      const e = html.indexOf('*/', i + 2);
      if (e < 0) return '';
      i = e + 1;
      continue;
    }
    if (c === '/' && REGEX_PREFIX.test(prev)) {
      let j = i + 1, inClass = false;
      for (; j < html.length; j++) {
        const d = html[j];
        if (d === '\n') break;
        if (d === '\\') j++;
        else if (d === '[') inClass = true;
        else if (d === ']') inClass = false;
        else if (d === '/' && !inClass) break;
      }
      // 没在本行收尾 = 不是正则 (多半是 `// http://...` 里的第二个斜杠), 按行注释跳过
      if (j >= html.length || html[j] === '\n') { i = lineEnd(i); continue; }
      i = j;
      while (/[a-z]/.test(html[i + 1] || '')) i++;
      continue;
    }
    // 模板字符串整体当字符串跳过 (插值里的 { } 不计数, 与模板文字里的抵消)
    if (c === '"' || c === "'" || c === '`') {
      // 字符串字面量在本行内必须收尾; 不收尾的引号是正则字符类里的 (如 `[&<>"']`)
      const rest = html.slice(i + 1, c === '`' ? html.length : lineEnd(i));
      if (!STRING_END[c].test(rest)) continue;
      quote = c;
      continue;
    }

    if (c === '{') { depth++; began = true; }
    else if (c === '}') {
      depth--;
      if (!began || depth !== 0) continue;
      const rest = html.slice(i + 1, i + 40).replace(/\/\/.*$/, '').trim();
      if (/^(else|catch|finally|while)\b/.test(rest) || /^\.\w+\(/.test(rest)) continue;
      return html.slice(start, i + 1);
    }
  }
  return '';
}

module.exports = { extractFunction };
