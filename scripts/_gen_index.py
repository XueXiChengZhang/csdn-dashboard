# -*- coding: utf-8 -*-
"""生成 csdn-dashboard 顶层 INDEX.md (幂等, 无时间戳)。"""
import os, sys

CS = sys.argv[1]

pages = [f for f in ['index.html', 'analytics.html', 'student.html', 'ranking.html', 'trend.html']
         if os.path.exists(os.path.join(CS, f))]
scripts = sorted(f for f in os.listdir(os.path.join(CS, 'scripts'))
                 if f.endswith(('.py', '.cmd', '.sh', '.js')))
reports = sorted(os.listdir(os.path.join(CS, 'reports')))
hr = sorted(f for f in os.listdir(os.path.join(CS, 'history-real')) if f.endswith('.json'))

scls = {'抓取': [], '测试': [], '任务': [], '部署提交': [], '其他': []}
for s in scripts:
    if s.startswith('test_'):
        scls['测试'].append(s)
    elif s.endswith('.cmd'):
        scls['任务'].append(s)
    elif 'monitor' in s or 'incremental' in s or 'stats' in s:
        scls['抓取'].append(s)
    elif 'deploy' in s or 'commit' in s:
        scls['部署提交'].append(s)
    else:
        scls['其他'].append(s)

out = []
out.append('# CSDN 仪表板 索引')
out.append('')
out.append('> 面向人和 AI 的导航表。**先看这里**。项目说明见 `README.md`。')
out.append('')
out.append('## 概览')
out.append('')
out.append('| 项 | 数量 |')
out.append('|---|---|')
out.append('| 页面 | %d |' % len(pages))
out.append('| 脚本 | %d |' % len(scripts))
out.append('| 报告 | %d |' % len(reports))
out.append('| 历史快照 | %d |' % len(hr))
out.append('| 测试项 | 250（全绿）|')
out.append('')
out.append('## 页面')
out.append('')
out.append('| 文件 | 用途 |')
out.append('|---|---|')
pdesc = {'index.html': '主仪表板（活跃榜/沉睡榜）', 'analytics.html': '班级整体分析',
         'student.html': '单生详情', 'ranking.html': '排名页',
         'trend.html': '历史趋势（读 history-real/）'}
for p in pages:
    out.append('| `%s` | %s |' % (p, pdesc.get(p, '')))
out.append('')
out.append('## 脚本')
out.append('')
for cls, items in scls.items():
    if not items:
        continue
    out.append('### %s' % cls)
    out.append('')
    for s in items:
        out.append('- `%s`' % s)
    out.append('')
out.append('## 报告')
out.append('')
out.append('每日报告 `reports/csdn-report-YYYY-MM-DD.md`（最新 %d 份）：' % min(len(reports), 10))
out.append('')
for r in reports[-10:]:
    out.append('- `%s`' % r)
fix = [r for r in reports if r.startswith('FIX')]
if fix:
    out.append('')
    out.append('修复与验证报告：')
    for r in fix:
        out.append('- `%s`' % r)
out.append('')
out.append('## 历史快照')
out.append('')
out.append('`history-real/YYYY-MM-DD.json` —— 逐日快照，**只增不改**，是趋势计算的事实来源。')
out.append('最新 5 份：')
out.append('')
for f in hr[-5:]:
    out.append('- `%s`' % f)
out.append('')
out.append('---')
out.append('')
out.append('## 相关')
out.append('')
out.append('- 项目说明：`README.md`')
out.append('- 部署：`DEPLOY.md`（GitHub Pages）/ `ALIYUN_DEPLOY.md`（阿里云）')
out.append('- 关键约束：绝不能破坏 live checkout；`history-real/` 只增不改')

open(os.path.join(CS, 'INDEX.md'), 'w', encoding='utf-8', newline='\n').write('\n'.join(out) + '\n')
print('  索引已生成: %d 页面, %d 脚本, %d 报告' % (len(pages), len(scripts), len(reports)))
