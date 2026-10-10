/**
 * apply-dark.js — 4 个 HTML 共用的暗色模式 toggle
 * P3 #10 修复: 抽出重复逻辑, 4 个页面引这一个文件
 *
 * 用法: HTML 里加 <script src="assets/apply-dark.js"></script> (放在 </body> 前)
 * 需要的 HTML 元素: <button id="dark-toggle" class="dark-toggle">🌙</button>
 * 需要的 CSS: html.dark { ... } 暗色变量
 *
 * 状态存 localStorage['csdn-dashboard-dark']
 */
(function () {
  'use strict';
  var DARK_KEY = 'csdn-dashboard-dark';

  function applyDark(on) {
    document.documentElement.classList.toggle('dark', on);
    var btn = document.getElementById('dark-toggle');
    if (btn) btn.textContent = on ? '☀' : '🌙';
  }

  // 首次加载恢复
  try {
    if (localStorage.getItem(DARK_KEY) === '1') applyDark(true);
  } catch (e) { /* localStorage 不可用 (隐私模式) */ }

  // DOM 就绪后绑定按钮
  function bind() {
    var btn = document.getElementById('dark-toggle');
    if (!btn) return;
    btn.addEventListener('click', function () {
      var isDark = !document.documentElement.classList.contains('dark');
      applyDark(isDark);
      try { localStorage.setItem(DARK_KEY, isDark ? '1' : '0'); } catch (e) {}
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bind);
  } else {
    bind();
  }
})();
