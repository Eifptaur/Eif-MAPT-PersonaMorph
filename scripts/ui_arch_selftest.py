# -*- coding: utf-8 -*-
"""控制台「信息架构」判据（2026-09-14，用户口径「功能映射在 UI 上区分做得够不够、分门别类好了吗」）。

它守的是**归类**，不是"有没有"（有没有由 `fullcheck.py` 的 A 段管）：
  ① 每块面板（`<section id="sec-*">`）都要出现在左侧导航里（否则用户找不到）；
  ② 导航里不许有指向不存在面板的死链；
  ③ 每块都要有**标题**（`<h2>`）与**说明**（`class="desc"`）——"分门别类"的最低标准；
  ④ 每个可调项（`data-cfg="..."`）必须落在**某一块面板内**（不许有"孤儿配置"散在页面其它地方）；
  ⑤ 面板数 / 导航数 / 可调项数给出来（口径数字，便于对账）。

⚠️ 两个口径细节（不写清就会误判）：
  · **`<script>` 段要排除**：里面的 `data-cfg` 是 JS 模板拼出来的（`"'+path+'"`），不是真实绑定；
  · 判"在不在面板里"要用**顺序扫描 + 深度计数**，不能用 `rfind("<section")` 那种近似（注释/嵌套会骗它）。

用法：py -3 scripts/ui_arch_selftest.py
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# ⚠️ 扫**渲染出来的页面**（`console_html.HTML`），不是扫源码：源码开头那段说明里就写着
#   `data-cfg="点.path"` 当例子（2026-09-14 实测被自己误判成"孤儿配置"）⇒ 扫产物最忠实。
from agent import console_html as CH            # noqa: E402
SRC = CH.HTML

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


def scan(src: str) -> dict:
    """把一份 console HTML 的信息架构量出来（判据与阴性对照共用同一套口径）。"""
    body = re.sub(r"<script[\s\S]*?</script>", "", src)          # 脚本段不是真实绑定
    secs = re.findall(r'<section id="(sec-[a-z0-9\-]+)"', body)
    navs = set(re.findall(r'href="#(sec-[a-z0-9\-]+)"', body))
    keys = re.findall(r'data-cfg="([^"]+)"', body)
    miss_h2, miss_desc, sizes = [], [], []
    for sid in secs:
        i = body.index('id="%s"' % sid)
        j = body.find("</section>", i)
        blk = body[i:j if j > 0 else i + 20000]
        sizes.append(len(blk))
        if "<h2>" not in blk:
            miss_h2.append(sid)
        if 'class="desc"' not in blk:
            miss_desc.append(sid)
    depth, outside = 0, []
    for t in re.finditer(r'<section\b|</section>|data-cfg="([^"]+)"', body):
        tok = t.group(0)
        if tok.startswith("<section"):
            depth += 1
        elif tok == "</section>":
            depth = max(0, depth - 1)
        elif depth <= 0:
            outside.append(t.group(1))
    return {"secs": secs, "navs": navs, "keys": keys, "miss_h2": miss_h2, "miss_desc": miss_desc,
            "sizes": sizes, "outside": outside,
            "no_nav": [s for s in secs if s not in navs],
            "dead_nav": sorted(n for n in navs if n not in secs)}


m = scan(SRC)
print("── 口径数字 ──")
print("   面板 %d 块 · 导航 %d 条 · 可调项绑定 %d 个（去重 %d）" % (
    len(m["secs"]), len(m["navs"]), len(m["keys"]), len(set(m["keys"]))))
if m["sizes"]:
    print("   面板块大小：最小 %d / 中位 %d / 最大 %d 字符" % (
        min(m["sizes"]), sorted(m["sizes"])[len(m["sizes"]) // 2], max(m["sizes"])))

print("\n── ① 每块都要能在导航里找到 ──")
ok("面板全部进了导航", not m["no_nav"], "缺 %s" % m["no_nav"] if m["no_nav"] else "%d 块" % len(m["secs"]))

print("── ② 导航不许有死链 ──")
ok("导航无死链", not m["dead_nav"], "死链 %s" % m["dead_nav"] if m["dead_nav"] else "%d 条" % len(m["navs"]))

print("── ③ 每块都有标题与说明 ──")
ok("每块都有 <h2> 标题", not m["miss_h2"], "缺 %s" % m["miss_h2"] if m["miss_h2"] else "%d 块" % len(m["secs"]))
ok("每块都有 desc 说明", not m["miss_desc"], "缺 %s" % m["miss_desc"] if m["miss_desc"] else "%d 块" % len(m["secs"]))

print("── ④ 可调项不许有「孤儿」（必须落在某块面板里）──")
ok("data-cfg 全在面板内", not m["outside"],
   "孤儿 %s" % m["outside"][:6] if m["outside"] else "%d 个键" % len(set(m["keys"])))

print("── ⑤ 阴性对照：判据不是恒真 ──")
syn = ('<section id="sec-a"><h2>甲</h2><div class="desc">x</div>'
       '<div data-cfg="a.b"></div></section>'
       '<section id="sec-b"><h2>乙</h2></section>'      # 缺 desc（且没进导航）
       '<div data-cfg="c.d"></div>'                      # 孤儿
       '<a href="#sec-gone">死链</a>'
       '<script>var x = \'data-cfg="\'+p+\'"\';</script>')
sm = scan(syn)
ok("人造孤儿被抓出", sm["outside"] == ["c.d"], str(sm["outside"]))
ok("人造缺导航被抓出", sm["no_nav"] == ["sec-a", "sec-b"], str(sm["no_nav"]))
ok("人造死链被抓出", sm["dead_nav"] == ["sec-gone"], str(sm["dead_nav"]))
ok("人造缺 desc 被抓出", sm["miss_desc"] == ["sec-b"], str(sm["miss_desc"]))
ok("脚本段里的假 data-cfg 被忽略（不误报）", "c.d" not in sm["outside"][1:], str(sm["outside"]))

print("\n通过 %d / 失败 %d（面板 %d · 导航 %d · 可调项 %d）"
      % (PASS, FAIL, len(m["secs"]), len(m["navs"]), len(set(m["keys"]))))
sys.exit(1 if FAIL else 0)
