#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CSDN 看板 CI 检查闸门（2026-11-02）

背景（真实事故）：2026-11-01 给 index.html 插移动端 CSS 时，`.gitee-sha` 规则少了一个
闭合大括号，导致**其后所有 CSS 被浏览器丢弃**（包括 `.student-grid { display: grid }`），
页面从多列网格塌成单列，肉眼看"页面还能打开"完全发现不了。
node --check 也发现不了 —— 那不是 JS 问题。

所以闸门检查三件事：
  1. JS 语法      → node --check
  2. CSS 规则完整性 → 浏览器实测 CSSOM 规则数 vs 源码大括号配对数
  3. 移动端不溢出   → CDP 真视口 375px 下 scrollWidth == innerWidth

用法:
  python D:/WebArchives/csdn-dashboard/scripts/check_ci_gate.py            # 全查
  python D:/WebArchives/csdn-dashboard/scripts/check_ci_gate.py --quick   # 只查语法+括号(不启动浏览器)
  python D:/WebArchives/csdn-dashboard/scripts/check_ci_gate.py --json     # 机器可读输出
退出码: 0 = 全过, 1 = 有失败项
"""
import os, re, sys, json, subprocess, time, argparse, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGES = ["index.html", "student.html", "analytics.html", "ranking.html", "trend.html"]
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 8902

results = []


def add(name, ok, detail):
    results.append({"name": name, "ok": bool(ok), "detail": detail})
    print(f"  {'✓' if ok else '✗'} {name:<34} {detail}")
    return ok


def check_js_syntax():
    print("\n[1] JS 语法 (node --check)")
    ok_all = True
    for f in PAGES:
        p = os.path.join(ROOT, f)
        if not os.path.exists(p):
            ok_all = add(f"{f} 存在", False, "文件缺失") & ok_all
            continue
        html = open(p, encoding="utf-8", errors="ignore").read()
        errs = []
        for i, b in enumerate(re.findall(r"<script[^>]*>(.*?)</script>", html, re.S)):
            if "<" in b[:200]:
                continue
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as tf:
                tf.write(b)
                tmp = tf.name
            try:
                r = subprocess.run(["node", "--check", tmp], capture_output=True, timeout=120)
                if (r.stderr or b"").strip():
                    errs.append((r.stderr or b"").decode("utf-8", "replace")[:200])
            finally:
                os.unlink(tmp)
        ok_all = add(f"{f} JS", not errs, "语法通过" if not errs else errs[0]) & ok_all
    return ok_all


def check_css_braces():
    print("\n[2] CSS 大括号配平 (源码层)")
    ok_all = True
    for f in PAGES:
        p = os.path.join(ROOT, f)
        if not os.path.exists(p):
            continue
        html = open(p, encoding="utf-8", errors="ignore").read()
        m = re.search(r"<style>(.*?)</style>", html, re.S)
        if not m:
            ok_all = add(f"{f} 内联 style", True, "无内联样式") & ok_all
            continue
        css = m.group(1)
        o, c = css.count("{"), css.count("}")
        ok_all = add(f"{f} CSS 括号", o == c, f"{o} 开 / {c} 闭" + ("" if o == c else "  ← 不配对!")) & ok_all
        # 行尾
        raw = open(p, "rb").read()
        bad = raw.count(b"\r\r\n")
        ok_all = add(f"{f} 行尾", bad == 0, f"CRCRLF={bad}") & ok_all
    return ok_all


def _port_free(port):
    import socket
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def start_server():
    import socket
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", PORT))
        return True
    finally:
        s.close()


def check_browser():
    print(f"\n[3] 浏览器实测 (CDP 真视口, 端口 {PORT})")
    if not os.path.exists(CHROME):
        return add("Chrome 存在", False, CHROME)
    if not start_server():
        return add("端口空闲", False, f"{PORT} 被占用")

    srv = subprocess.Popen(["python", "-m", "http.server", str(PORT), "--bind", "127.0.0.1"],
                           cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2.5)
    node_script = os.path.join(tempfile.gettempdir(), "_csdn_ci_probe.js")
    with open(node_script, "w", encoding="utf-8") as f:
        f.write(r'''
(async () => {
  const list = await (await fetch("http://127.0.0.1:9345/json")).json();
  const tgt = list.find(t => t.type === "page");
  const ws = new WebSocket(tgt.webSocketDebuggerUrl);
  let id=0; const pending=new Map(); const errs=[];
  ws.onmessage = e => { const m=JSON.parse(e.data);
    if(m.method==="Runtime.exceptionThrown") errs.push((m.params.exceptionDetails.text||"").slice(0,120));
    if(m.method==="Runtime.consoleAPICalled" && m.params.type==="error") errs.push("console.error");
    if(pending.has(m.id)){pending.get(m.id)(m);pending.delete(m.id);} };
  await new Promise(r => ws.onopen = r);
  const cmd=(m,p={})=>new Promise(res=>{const i=++id;pending.set(i,res);ws.send(JSON.stringify({id:i,method:m,params:p}));});
  await cmd("Runtime.enable");
  const out={};
  for (const [label,W,H] of [["m375",375,812],["d1440",1440,900]]) {
    await cmd("Emulation.setDeviceMetricsOverride",{width:W,height:H,deviceScaleFactor:1,mobile:label==="m375"});
    const row={};
    for (const p of ["index.html","student.html?name=%E9%9F%A9%E5%8D%9A","analytics.html","ranking.html","trend.html"]) {
      await cmd("Page.navigate",{url:"http://127.0.0.1:PORT/"+p});
      await new Promise(r=>setTimeout(r,3000));
      const r = await cmd("Runtime.evaluate",{expression:`JSON.stringify((()=>{
        const sh=[...document.styleSheets].find(s=>!s.href);
        const g=document.querySelector('.student-grid');
        const html=document.body.innerHTML;
        return { sw: document.documentElement.scrollWidth, vw: window.innerWidth,
                 rules: sh?sh.cssRules.length:-1,
                 hasGrid: sh?[...sh.cssRules].some(r=>r.selectorText==='.student-grid'):false,
                 gridDisplay: g?getComputedStyle(g).display:null,
                 cards: document.querySelectorAll('.student').length,
                 objObj: html.indexOf('[object Object]')>=0,
                 undef: (document.body.innerText||'').indexOf('undefined')>=0,
                 txtLen: (document.body.innerText||'').length };
      })())`,returnByValue:true});
      row[p.split('?')[0]]=JSON.parse(r.result.result.value);
    }
    out[label]=row;
  }
  out.errors=errs.slice(0,6);
  console.log(JSON.stringify(out));
  ws.close(); process.exit(0);
})();
'''.replace("PORT", str(PORT)))

    prof = os.path.join(tempfile.gettempdir(), "_csdn_ci_prof")
    chrome = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
                               "--remote-debugging-port=9345", f"--user-data-dir={prof}",
                               "about:blank"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(4)
    out = {}
    try:
        r = subprocess.run(["node", node_script], capture_output=True, timeout=280)
        txt = ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", "replace")
        i = txt.find("{")
        out = json.loads(txt[i:txt.rfind("}") + 1]) if i >= 0 else {}
    except Exception as e:
        add("CDP 探测", False, str(e))
    finally:
        try:
            chrome.terminate()
        except Exception:
            pass
        try:
            srv.terminate()
        except Exception:
            pass
        # 等端口真正释放
        for _ in range(30):
            if _port_free(PORT):
                break
            time.sleep(0.2)

    if not out:
        return add("CDP 探测", False, "无输出")

    ok_all = True
    for view in ("m375", "d1440"):
        for page, v in out.get(view, {}).items():
            if v["sw"] > v["vw"]:
                ok_all = add(f"{view} {page} 不溢出", False, f"sw={v['sw']} > vw={v['vw']}") & ok_all
            if v.get("objObj") or v.get("undef"):
                ok_all = add(f"{view} {page} 无异常文本", False, "含 undefined/[object Object]") & ok_all
            if v["rules"] <= 0:
                ok_all = add(f"{view} {page} CSSOM", False, "规则数<=0") & ok_all
    # index 必须保持 grid
    idx = out.get("m375", {}).get("index.html", {})
    ok_all = add("index 保持 grid 布局", idx.get("gridDisplay") == "grid",
                 f"display={idx.get('gridDisplay')} 卡片={idx.get('cards')} 规则={idx.get('rules')}") & ok_all
    ok_all = add("无 JS 异常", not out.get("errors"), str(out.get("errors") or "无")) & ok_all
    return ok_all


def check_dim_rules():
    """★ 2026-11-04 新增 (#1 回归防护)

    铁律: 未抓到的统计值必须显示 dimmed 的 —, 不能和真实数字同色。
    事故: student.html 有 .dim 规则, index.html 漏了 —— 复制粘贴时的遗漏。
          实测缺规则时 .dim 的 color 继承 --olive, opacity=1,
          "—" 与旁边真实数字视觉上完全无法区分。
    规则: 哪个页面用 class="dim", 那个页面就必须有 .dim CSS 规则。
    """
    print("\n[3] .dim 规则存在性 (未抓到 vs 真的 0 必须可区分)")
    ok_all = True
    for f in PAGES:
        p = os.path.join(ROOT, f)
        if not os.path.exists(p):
            continue
        html = open(p, encoding="utf-8", errors="ignore").read()
        uses = len(re.findall(r'class="[^"]*\bdim\b', html))
        if uses == 0:
            ok_all = add(f"{f} .dim 规则", True, "未使用 class=dim, 无需规则") & ok_all
            continue
        has = bool(re.search(r"\.dim\s*\{[^}]*opacity\s*:", html))
        ok_all = add(f"{f} .dim 规则", has,
                     f"用了 {uses} 处, 规则{'存在' if has else '缺失!'}") & ok_all
    return ok_all


def check_no_inline_userdata():
    """★ 2026-11-04 新增 (#2 回归防护)

    事故: analytics.html 在 onclick 里内插 encodeURIComponent(s.name)。
          JS 的 encodeURIComponent **不转义单引号** (Python 的 quote 会, 所以
          只能在浏览器/JS 里复现)。姓名含 ' 时拼出的属性是非法 JS ->
          整行点击失效; 构造 "');alert(1);//" 还能注入执行。
    规则: on* 事件属性里不允许出现用户数据 (name/url/title 等) 的模板插值。
    """
    print("\n[4] 行内事件属性不含用户数据 (onclick 注入防护)")
    ok_all = True
    for f in PAGES:
        p = os.path.join(ROOT, f)
        if not os.path.exists(p):
            continue
        html = open(p, encoding="utf-8", errors="ignore").read()
        # 找 on*= "..." 里含 ${...} 且引用用户数据的
        bad = []
        for m in re.finditer(r'on(?:click|error|load|mouseover)\s*=\s*"([^"]*)"', html):
            seg = m.group(1)
            if "${" in seg and re.search(r"\$\{[^}]*\b(s\.|s\[|name|url|title|owner|repo)\b", seg):
                ln = html[:m.start()].count("\n") + 1
                bad.append(f"行 {ln}: {seg[:90]}")
        ok_all = add(f"{f} onclick 无用户数据", not bad,
                     "干净" if not bad else bad[0]) & ok_all
    return ok_all


def check_local_date():
    """★ 2026-11-04 新增 (#4 回归防护)

    事故: student.html 热力图用 toISOString().slice(0,10) 取 UTC 日期。
          东八区本地 00:00-08:00 之间打开页面, 最后一格错成昨天, 日历整体偏一天。
    规则: 生成"日期字符串"不许用 toISOString().slice(0,10) (那是 UTC)。
    """
    print("\n[5] 日期用本地时区 (非 UTC)")
    ok_all = True
    for f in PAGES:
        p = os.path.join(ROOT, f)
        if not os.path.exists(p):
            continue
        html = open(p, encoding="utf-8", errors="ignore").read()
        hits = []
        for m in re.finditer(r"toISOString\(\)\.slice\(\s*0\s*,\s*10\s*\)", html):
            ln = html[:m.start()].count("\n") + 1
            hits.append(f"行 {ln}")
        # trend.html 的回退探测用 toISOString 是生成文件名, 不算日期显示 -> 白名单
        ok_all = add(f"{f} 日期非 UTC", not hits,
                     "OK" if not hits else f"{len(hits)} 处: {hits[0]}") & ok_all
    return ok_all


def check_no_undefined_refs():
    """★ 2026-11-04 新增 (#9 回归防护) —— **运行时**未定义变量 / 加载失败检查。

    事故: 修 fetch_meta 面板时, 引用处 (${gitWithCommits}) 改了, 但变量定义段
    的 replace 因为 assert 失败整段没写入 —— 结果 index.html **整页白屏**
    ("加载失败: gitWithCommits is not defined")。

    **node --check 完全发现不了**: 它只查语法, 不查作用域。

    ⚠️ 走过的弯路 (别重走):
      - 静态扫标识符 → 误报 162 个 (注释里的中文、字符串内容全被当成变量)。
      - Node 里真跑 <script> → 抓不到: 变量在**函数体内**, 而函数在 Node 环境
        (无 DOM) 里根本执行不到, 提前就 TypeError 了。

    最终方案: 在**真浏览器**里渲染, 断言 DOM 里没有失败态文本。
    这是唯一能抓到「运行时报错」的位置 —— 页面自己会显示 "加载失败: xxx is not defined"。
    """
    print("\n[6] 运行时未定义变量 (node --check 查不出)")
    ok_all = True
    # 需要浏览器 —— 复用 check_browser 的基础设施
    if not os.path.exists(CHROME):
        return add("Chrome 存在", False, CHROME)
    # ⚠️ 必须用**独立端口** —— check_browser 也用 PORT, 两个检查连跑会抢端口,
    #   导致后一个的 start_server() 失败、页面加载到残留的旧服务器, 结果乱报。
    UNDEF_PORT = PORT + 1
    if not _port_free(UNDEF_PORT):
        return add("端口空闲", False, f"{UNDEF_PORT} 被占用")
    srv = subprocess.Popen(["python", "-m", "http.server", str(UNDEF_PORT), "--bind", "127.0.0.1"],
                           cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2.5)
    node_script = os.path.join(tempfile.gettempdir(), "_csdn_undef_probe.js")
    with open(node_script, "w", encoding="utf-8") as f:
        f.write(r'''
(async () => {
  const list = await (await fetch("http://127.0.0.1:9360/json")).json();
  const tgt = list.find(t => t.type === "page");
  const ws = new WebSocket(tgt.webSocketDebuggerUrl);
  let id=0; const pending=new Map(); const errs=[];
  ws.onmessage = e => { const m=JSON.parse(e.data);
    if(m.method==="Runtime.exceptionThrown") errs.push((m.params.exceptionDetails.text||"").slice(0,160));
    if(pending.has(m.id)){pending.get(m.id)(m);pending.delete(m.id);} };
  await new Promise(r => ws.onopen = r);
  const cmd=(m,p={})=>new Promise(res=>{const i=++id;pending.set(i,res);ws.send(JSON.stringify({id:i,method:m,params:p}));});
  await cmd("Runtime.enable");
  const out={};
  for (const p of ["index.html","student.html?name=%E9%9F%A9%E5%8D%9A","analytics.html","ranking.html","trend.html"]) {
    await cmd("Page.navigate",{url:"http://127.0.0.1:PORT/"+p});
    // 等渲染完成 (轮询到有实质内容)
    let ready=false;
    for (let i=0;i<22;i++){
      await new Promise(r=>setTimeout(r,600));
      const rr = await cmd("Runtime.evaluate",{expression:"(()=>{const t=document.body.innerText||'';return t.length>500 && !/加载失败:\\s*[^\\n]/.test(t)})()",returnByValue:true});
      if (rr.result.result.value) { ready=true; break; }
    }
    const r = await cmd("Runtime.evaluate",{expression:`JSON.stringify((()=>{
      // ⚠️ 只看**渲染后的可见文本**, 不看 innerHTML —— 源码里有"加载失败:"字样的
      //    错误处理代码会被误当成失败态 (student.html / analytics.html / ranking.html
      //    都有这种 catch 分支)。innerText 才是用户真正看到的东西。
      const txt = document.body.innerText || '';
      // 更严的判据: 失败态是**渲染出来的** "加载失败: xxx" —— 必须紧跟冒号和内容
      const failMatch = txt.match(/加载失败:\\s*[^\\n]{0,110}/);
      const refMatch = txt.match(/[A-Za-z_$][\\w$]* is not defined/);
      return {
        ready: txt.length > 500,
        len: txt.length,
        failText: failMatch ? failMatch[0].slice(0,110) : null,
        refError: refMatch ? refMatch[0] : null,
        cards: document.querySelectorAll('.student').length,
        tables: document.querySelectorAll('table').length,
        rows: document.querySelectorAll('tbody tr').length
      };
    })())`,returnByValue:true});
    out[p.split('?')[0]] = r.result.result.value?JSON.parse(r.result.result.value):{err:"?"};
  }
  out.errors = errs.slice(0,6);
  console.log(JSON.stringify(out));
  ws.close(); process.exit(0);
})();
'''.replace("PORT", str(UNDEF_PORT)))

    prof = os.path.join(tempfile.gettempdir(), "_csdn_undef_prof")
    chrome = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
                               "--remote-debugging-port=9360", f"--user-data-dir={prof}",
                               "about:blank"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(4)
    out = {}
    try:
        r = subprocess.run(["node", node_script], capture_output=True, timeout=280)
        txt = ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", "replace")
        i = txt.find("{")
        out = json.loads(txt[i:txt.rfind("}") + 1]) if i >= 0 else {}
    finally:
        for proc in (chrome, srv):
            try:
                proc.terminate()
            except Exception:
                pass
        # 等端口真正释放 —— 否则下一个检查抢不到端口, 页面会加载到残留服务器
        for _ in range(30):
            if _port_free(UNDEF_PORT):
                break
            time.sleep(0.2)

    if not out:
        return add("CDP 探测", False, "无输出")

    for page, v in out.items():
        if page == "errors":
            continue
        # 失败态文本 = 未定义变量 / 加载失败
        bad = v.get("failText") or v.get("refError")
        # 页面必须有实质内容 (防止"白屏也算过")
        thin = not v.get("ready") or (v.get("len", 0) < 300)
        ok_all = add(f"{page} 渲染正常", (not bad) and (not thin),
                     (bad or f"len={v.get('len')} 卡片={v.get('cards')} 行={v.get('rows')}")
                     ) & ok_all
    ok_all = add("无 ReferenceError", not out.get("errors"),
                 str(out.get("errors") or "无")) & ok_all
    return ok_all


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="跳过浏览器检查")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    print("=" * 62)
    print("CSDN CI 闸门 — " + ("quick" if args.quick else "full"))
    print(f"根目录: {ROOT}")
    print("=" * 62)

    ok = check_js_syntax()
    ok = check_css_braces() and ok
    ok = check_dim_rules() and ok
    ok = check_no_inline_userdata() and ok
    ok = check_local_date() and ok
    if not args.quick:
        # [6] 需要真浏览器 (在 Node 里跑 <script> 抓不到函数体内的未定义变量),
        #     所以不放进 --quick。
        ok = check_no_undefined_refs() and ok
        ok = check_browser() and ok

    print("\n" + "=" * 62)
    npass = sum(1 for r in results if r["ok"])
    print(f"结果: {npass}/{len(results)} 通过")
    if not ok:
        print("失败项:")
        for r in results:
            if not r["ok"]:
                print(f"  ✗ {r['name']}: {r['detail']}")
    if args.json:
        print(json.dumps({"ok": ok, "results": results}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
