# -*- coding: utf-8 -*-
"""控制台「弹窗/关窗」判据（2026-09-15 用户报「屏幕上一直在闪弹窗」+「点停止弹窗不自动关」）

背景（真事故）：控制台页在"机器人已停止"这条路径上**自己关自己的窗口**——
`window.open('', '_self')` → `window.close()` → `location.replace('about:blank')`。
在 WebView2 里 `window.open` 会**真的开一个新窗口**再被关掉 ⇒ 用户看到窗口一闪一闪，
只有把整个应用关掉才停；而"点停止后弹窗不自动关"是因为停止分支又补了第二个模态、
却把自动关闭定在了 4 秒后（是关窗口，不是关弹窗）。

判据（静态 + 真起页面两条）：
  A. 页面里**不许再出现**这三样自动关窗写法：`window.open('', '_self')`、`location.replace('about:blank')`、
     停止分支里的 `window.close()`。（别的 `window.open(url)` 打开新页面属正常，不算。）
  B. 「机器人已停止」必须走**可关横幅**（`.offline-bar` + 「知道了」按钮），不是全屏模态。
  C. 停止按钮的处理里**不许**再补第二个 `confirmBox('机器人已停止'…)`，也不许定时关窗口。
  D. 真起控制台抓页面复核：横幅样式与脚本都在页面里。

用法：py -3 scripts/console_popup_selftest.py
"""
import io
import os
import re
import socket
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

PASS = 0
FAIL = 0
SKIP_N = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


def skip(name, why=""):
    global SKIP_N
    SKIP_N += 1
    print("  SKIP {}  [{}]".format(name, why))


src = io.open(os.path.join(ROOT, "agent", "console_html.py"), encoding="utf-8").read()


def strip_js_comments(s):
    """判据只看**代码**，不看注释——修复说明里会引用旧的坏写法（"旧实现是 window.open…"），
    不剥注释就会把注释当违规。"""
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.S)
    s = re.sub(r"//[^\n]*", " ", s)
    return s


code = strip_js_comments(src)

print("== 控制台弹窗/关窗判据 ==")

print("\n── A. 自动关窗写法必须绝迹 ──")
ok("没有 window.open('', '_self')（会真开一个新窗口）", "window.open('', '_self')" not in code)
ok("没有「拿空窗口名去抢同名窗口」的写法（WebView2 里会真开窗）", "window.open('', 'Persona" not in code)
ok("没有 location.replace('about:blank')", "about:blank" not in code)
# 允许 window.open(某网址) 打开新页面；只禁"用来关自己"的那种。
# ⚠️ 2026-09-16 口径更新（已知现象：「我点停止机器人窗口怎么不会自己关掉」）：
#   **用户主动点「停止」之后**把控制台窗口一起关掉是**允许的**（那是用户自己的意图，
#   而且宿主侧已接 `WindowCloseRequested`）。所以这里不再"全文禁定时关窗"，
#   只禁**停止分支之外**的自动/定时关窗——后端意外断线那条路径仍然只挂横幅、不许关窗。
_stop_at = code.index("$('stopBtn').onclick") if "$('stopBtn').onclick" in code else -1
_outside = code[:_stop_at] if _stop_at > 0 else code
bad_close = re.findall(r"setTimeout\([^\n]{0,80}window\.close\(\)", _outside)
ok("停止分支**之外**没有「定时自己关窗口」的写法", not bad_close, bad_close[:2])

print("\n── B. 停止状态走可关横幅，不是全屏模态 ──")
ok("有 .offline-bar 样式", ".offline-bar{" in src)
ok("横幅里有「知道了」按钮（用户可关）", 'id="offlineBarX"' in src)
ok("挂横幅时不再 maskOpen（不弹模态）",
   "document.body.appendChild(bar)" in code and "maskOpen(ov)" not in code)
ok("状态灯会置灰（dot off）", ".dot.off{" in src and "d.className='dot off'" in code)

print("\n── C. 停止分支：不补第二个模态、可由用户意图关窗 ──")
blk = code[code.index("$('stopBtn').onclick"):]
blk = blk[:blk.index("$('restartBtn').onclick")] if "$('restartBtn').onclick" in blk else blk
ok("停止分支里没有第二个 confirmBox('机器人已停止'…)", "confirmBox('机器人已停止'" not in blk)
# 2026-09-16 口径再更新（用户实测「点停止关不掉窗口」，根因就在这里）：
#   原来要求"关窗必须挂在成功分支（catch 之前）"—— 但 `/api/shutdown` 一执行**后端自己就关了**，
#   响应很可能没读完连接就断 ⇒ `getJSON` 抛错 ⇒ 流程走 catch ⇒ 放在 `try` 里的关窗**永远执行不到**。
#   新口径＝**用户点了「确认停止」就无条件关窗**（成功失败都关）⇒ 关窗必须在 `finally` 里，且只允许一处。
ok("停止分支里关窗有且只有一次",
   blk.count("window.close") == 1, str(blk.count("window.close")))
ok("关窗挂在 finally 里（成功失败都关，不再受 shutdown 抛错影响）",
   ("window.close" in blk and "}finally{" in blk and blk.index("window.close") > blk.index("}finally{")))
ok("停止后会顺手清掉残留遮罩（别把用户堵在弹窗里）", "querySelectorAll('.mask')" in blk)
ok("停止后提示文案说清了「想再跑怎么办」", "启动机器人.vbs" in blk and "重启" in blk)

print("\n── D. 真起控制台抓页面复核 ──")
_page = None
try:
    from agent import webui as W

    _orig = W.get_config
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    free = s.getsockname()[1]
    s.close()
    base = dict(W.get_config() or {})
    base["server"] = {"enabled": True, "host": "127.0.0.1", "port": free,
                      "token": "popup-judge", "auto_open_browser": False}
    W.get_config = lambda: base
    w = W.WebUI(lambda: {}, [])
    port = w.start()
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/?token=popup-judge" % port, timeout=8) as r:
            _page = r.read().decode("utf-8", "replace")
    finally:
        w.stop()
except Exception as e:
    skip("D. 真起控制台抓页面", "起不了：%s" % e)
finally:
    try:
        W.get_config = _orig
    except Exception:
        pass

if _page is None:
    skip("页面复核", "没抓到页面")
else:
    _pcode = strip_js_comments(_page)
    ok("页面里有 .offline-bar 样式", ".offline-bar{" in _page)
    ok("页面里没有 about:blank（注释不算）", "about:blank" not in _pcode)
    ok("页面里没有「拿空窗口名抢同名窗」的写法", "window.open('', 'Persona" not in _pcode)
    ok("页面里有横幅关闭按钮", 'id="offlineBarX"' in _page)

print("")
print("控制台弹窗判据：%d 通过 / %d 失败 / %d 跳过" % (PASS, FAIL, SKIP_N))
sys.exit(1 if FAIL else 0)
