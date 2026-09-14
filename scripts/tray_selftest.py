# -*- coding: utf-8 -*-
"""托盘气泡兜底 判据（2026-09-14，测机报告 ⑦d）。

口径：控制台**开不出来**时（WebView2 起不来 / 没有桌面会话 / 被策略挡住），至少让任务栏气泡说一句
「有件事等你拍板」，点气泡才去开控制台 —— 不抢前台、不弹窗。

⚠️ 屏幕纪律（第 7 条教训）：托盘图标与气泡**都是用户可见的**，所以本判据**默认只在 WX_NO_UI_POP=1
下跑**（验开关与降级路径，一个像素都不上屏）；真建档/真出气泡那一段必须显式加 `--real`，
而且只在你想亲眼看一眼时跑一次。

用法：py -3 scripts/tray_selftest.py          # 默认：静态 + 开关 + 降级（不出屏）
      py -3 scripts/tray_selftest.py --real   # 额外：真加图标 + 真气泡 + 真删除（屏幕上会出现气泡）
"""
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

REAL = "--real" in sys.argv
os.environ["WX_NO_UI_POP"] = "1"          # 默认全程不让它上屏

from agent import tray as T               # noqa: E402

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


SRC = open(os.path.join(ROOT, "agent", "tray.py"), encoding="utf-8").read()
GSRC = open(os.path.join(ROOT, "agent", "version_gate.py"), encoding="utf-8").read()

print("── A. 静态：自研图标 + 结构体 + 不打扰 ──")
ok("图标用我们自己的素材 assets/app.ico（LoadImageW）",
   'os.path.join(ROOT, "assets", "app.ico")' in SRC and "LoadImageW" in SRC)
ok("NOTIFYICONDATAW 结构体按原样声明（cbSize 对不上会静默失败）",
   "class NOTIFYICONDATAW" in SRC and "cbSize = ctypes.sizeof(NOTIFYICONDATAW)" in SRC)
ok("窗口过程留了引用（防 GC 掉回调）", "_wndproc_ref.append" in SRC)
ok("消息窗用 HWND_MESSAGE（不可见、不进任务栏）", "HWND_MESSAGE" in SRC)
ok("点了气泡才去开控制台", "NIN_BALLOONUSERCLICK" in SRC and "open_console" in SRC)
ok("不弹系统 MessageBox、不动鼠标键盘",
   all(k not in SRC for k in ("MessageBox", "mouse_event", "SetCursorPos", "SendInput")))
ok("窗口 API 都声明了 argtypes", SRC.count(".argtypes =") >= 10, "%d 处" % SRC.count(".argtypes ="))
ok("遵守 WX_NO_UI_POP（判据/无人值守一律关）", 'os.environ.get("WX_NO_UI_POP") == "1"' in SRC)

print("── B. 开关与降级（默认不出屏）──")
r0 = T.notify("标题", "内容")
ok("设了 WX_NO_UI_POP ⇒ 直接跳过、不建档", r0.get("skipped") and r0.get("icon_added") is False, str(r0.get("skipped")))
ok("跳过时 ok=True（不是错误，是「按要求没做」）", r0.get("ok") is True)
ok("全程没有把 ready 拉起来（没建消息窗）", T.status().get("ready") is False)

print("── C. 接线：开不出控制台才走托盘 ──")
seg = GSRC.split("def _run():")[1].split("def pending(")[0]
ok("弹窗失败 ⇒ 托盘兜底", "if not rep.get(\"ok\")" in seg and "tray.notify" in seg)
ok("托盘失败不影响开单（包在 try 里只记日志）", "except Exception as _te" in seg)
ok("带着单子的标题与原因去出气泡", 'item.get("title")' in seg and 'item.get("reason")' in seg)

print("── D. 默认路径不会上屏的机械证据 ──")
ok("默认路径下 status() 里 icon=False", T.status().get("icon") is False)
ok("shutdown() 在没建档时也不抛异常", isinstance(T.shutdown(), dict))

if REAL:
    print("\n── E. 真跑（--real）：屏幕上会出现一个托盘气泡，几秒后自动消失 ──")
    os.environ.pop("WX_NO_UI_POP", None)
    t0 = time.time()
    ok("消息窗能建起来", T.available() is True, "%.1fs" % (time.time() - t0))
    r = T.notify("群相灵 · 自检", "这条是托盘判据自己发的，几秒后自动消失")
    ok("建档成功（图标进了任务栏）", r.get("icon_added") is True, str(r.get("why")))
    ok("气泡调用成功", r.get("balloon_shown") is True, str(r.get("why")))
    st = T.status()
    ok("状态里记下了这条通知", st.get("count", 0) >= 1 and "自检" in str(st.get("last")), str(st.get("last"))[:48])
    ok("删档成功", T.shutdown().get("removed") is True)
    ok("删完状态归位", T.status().get("icon") is False)
else:
    print("\n（真建档/真气泡那一段要 --real 才跑：托盘图标与气泡都是用户可见的，不许每轮扫判据都上屏）")

print("\n通过 %d / 失败 %d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
