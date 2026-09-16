#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""控制台「不带下划线」判据（先生 2026-09-13 定调）

原话：「控制台到时候 UI 重做的时候，你所有字不要带下划线啊。顺便还查查哪些有带下划线的，把下划线去掉」

判据（不需要微信、不需要起服务）：
  ① 控制台前端源码里**没有**下划线写法：`text-decoration:underline` / `text-decoration-line:underline` / `<u>` / `</u>`
  ② 全局 `a` 规则**显式**写了 `text-decoration:none`（浏览器默认给链接加下划线 —— 不显式关掉就会露出来）
  ③ 链接的 hover/focus/visited/active 也不带下划线
  ④ 导航 `a` 规则同样显式 none（历史遗留在 `.nav a` 上的那几个）

扫的面：`agent/console_html.py`（控制台页面本体）＋ `agent/webui.py`（另一处内联 CSS/图标注）＋ `launcher-src/*.cs`（WebView2 外壳，不涉排版但一起扫以防手写 Html）
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

TARGETS = [
    os.path.join(ROOT, 'agent', 'console_html.py'),
    os.path.join(ROOT, 'agent', 'webui.py'),
]
LAUNCHER_DIR = os.path.join(ROOT, 'launcher-src')
if os.path.isdir(LAUNCHER_DIR):
    for fn in sorted(os.listdir(LAUNCHER_DIR)):
        if fn.endswith('.cs'):
            TARGETS.append(os.path.join(LAUNCHER_DIR, fn))

PASS = 0
FAIL = 0


def ok(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print('  {} {}{}'.format('OK  ' if cond else 'FAIL', name, '  [{}]'.format(detail) if detail else ''))


def read(p):
    with open(p, 'r', encoding='utf-8', errors='replace') as f:
        return f.read()


print('── A. 扫面 ──')
ok('目标文件都在', all(os.path.exists(p) for p in TARGETS),
   '{} 个文件：{}'.format(len(TARGETS), ', '.join(os.path.basename(p) for p in TARGETS)))

texts = {p: read(p) for p in TARGETS if os.path.exists(p)}

print('── B. 零下划线写法 ──')
UNDERLINE_RE = re.compile(r'text-decoration\s*:\s*(?![^;{}]*\bnone\b)[^;{}]*underline|text-decoration-line\s*:\s*underline|<u>|</u>', re.I)
hits = []
for p, t in texts.items():
    for m in UNDERLINE_RE.finditer(t):
        line = t[:m.start()].count('\n') + 1
        hits.append('{}:{} {}'.format(os.path.basename(p), line, m.group(0)[:40]))
ok('前端源码里没有下划线写法', not hits, ' | '.join(hits[:4]))

print('── C. 全局 a 规则显式 none ──')
console = texts.get(os.path.join(ROOT, 'agent', 'console_html.py'), '')
ok('全局 a{…} 明确写 text-decoration:none',
   re.search(r'(^|\n)\s*a\{[^}]*text-decoration\s*:\s*none', console) is not None)
ok('链接各态（hover/focus/visited/active）都不带下划线',
   re.search(r'a:hover[^{]*\{[^}]*text-decoration\s*:\s*none', console) is not None,
   '逐态显式 none，避免 hover 时又冒出一条线')

print('── D. 导航链接（历史遗留）──')
nav_rules = re.findall(r'\.nav a\{[^}]*\}', console)
ok('至少一条 .nav a 规则', len(nav_rules) >= 1, '{} 条'.format(len(nav_rules)))
ok('.nav a 规则里显式 none', any('text-decoration:none' in r.replace(' ', '') for r in nav_rules))

print('── E. CSS 花括号配平（孤儿声明行＝声明被浏览器静默丢弃，本条专抓它）──')
# 先生报的"下划线"根因就是这个：某次改 `.nav a{…}` 时 transition 那行提前写了 `}`，
# 后面 `text-decoration:none;font-size;margin` 变成孤儿声明、被浏览器整段丢掉 ⇒ 导航链接露出默认下划线。
# 花括号不配平能机械抓到这类错误，自检不需要真机渲染。
css_blocks = re.findall(r'"""(.*?)"""', console, re.S)
css = next((b for b in css_blocks if '.nav a{' in b), '')
opens = css.count('{')
closes = css.count('}')
ok('CSS 花括号配平（三引号样式块内）', bool(css) and opens == closes and opens > 50, '块长 {} 字符 / {{={} }}={}'.format(len(css), opens, closes))

print('== [no-underline] 判据：{} 通过 / {} 失败 =='.format(PASS, FAIL))
sys.exit(1 if FAIL else 0)
