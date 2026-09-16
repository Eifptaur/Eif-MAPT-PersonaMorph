# -*- coding: utf-8 -*-
"""UIA 探针自测（不需要微信、不需要 uiautomation 真的能用）。

跑法：runtime\\python\\python.exe scripts\\uia_probe_selftest.py
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from agent import uia_probe as U

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS  %s" % name)
    else:
        FAIL += 1
        print("FAIL  %s  %s" % (name, extra))


# ── 假控件树 ────────────────────────────────────────────────────────────
MATERIALIZED = [
    {"type": "WindowControl", "class": "mmui::MainWindow", "name": "微信", "aid": ""},
    {"type": "PaneControl", "class": "mmui::ChatMasterView", "name": "", "aid": ""},
    {"type": "ListControl", "class": "mmui::ChatSessionList", "name": "", "aid": ""},
    {"type": "GroupControl", "class": "mmui::ChatMessagePage", "name": "", "aid": ""},
    {"type": "ListControl", "class": "mmui::MessageView", "name": "", "aid": ""},
    {"type": "EditControl", "class": "mmui::ChatInputField", "name": "", "aid": ""},
    {"type": "ButtonControl", "class": "", "name": "发送(S)", "aid": ""},
]
BARE = [  # 本机 4.1.15.8 实测形态：只有两层原生窗口
    {"type": "PaneControl", "class": "Qt51514QWindowIcon", "name": "微信", "aid": ""},
    {"type": "PaneControl", "class": "MMUIRenderSubWindowHW", "name": "", "aid": ""},
]
BARE_PLUS_SESSION = BARE + [
    {"type": "ListControl", "class": "mmui::ChatSessionList", "name": "", "aid": ""},
]

# ── 1 物化判定 ──────────────────────────────────────────────────────────
a = U.analyze_nodes(MATERIALIZED)
check("物化树被判定为 materialized", a["materialized"] is True, str(a))
check("命中关键类名", "mmui::ChatInputField" in a["key_hits"], str(a["key_hits"]))
b = U.analyze_nodes(BARE)
check("只有原生窗口 ⇒ 未物化", b["materialized"] is False, str(b))
check("未物化时 mmui_hits 为空", b["mmui_hits"] == [], str(b))
c = U.analyze_nodes([])
check("空树不报错且未物化", c["materialized"] is False and c["nodes"] == 0, str(c))
d = U.analyze_nodes(BARE_PLUS_SESSION)
check("部分物化：命中会话列表即算物化", d["materialized"] is True and d["mmui_hits"] == ["mmui::ChatSessionList"], str(d))

# ── 2 通讯录完整性（将来 UiaBackend 直接用这份数据）─────────────────────
need = {"main_window", "sub_window", "session_list", "search_box", "message_list",
        "input_box", "send_button", "chat_info_path"}
check("控件通讯录字段齐全", need <= set(U.WECHAT_UI_MAP.keys()),
      str(need - set(U.WECHAT_UI_MAP.keys())))
check("输入框类名正确", U.WECHAT_UI_MAP["input_box"]["uia_class"] == "mmui::ChatInputField")
check("发送按钮按 Name 定位", U.WECHAT_UI_MAP["send_button"]["name"] == "发送(S)")
check("独立子窗类名正确", U.WECHAT_UI_MAP["sub_window"]["uia_class"] == "mmui::FramelessMainWindow")
check("聊天信息 AutomationId 是点分路径",
      "." in U.WECHAT_UI_MAP["chat_info_path"]["automation_id"])

# ── 3 指纹：稳定、够敏感 ────────────────────────────────────────────────
f1 = U.fingerprint(MATERIALIZED, {"hwnd": 123, "class": "Qt51514QWindowIcon"})
f2 = U.fingerprint(MATERIALIZED, {"hwnd": 123, "class": "Qt51514QWindowIcon"})
check("同输入指纹稳定", f1 == f2, "%s vs %s" % (f1, f2))
f3 = U.fingerprint(MATERIALIZED + [{"type": "ToolBarControl", "class": "mmui::XToolBar", "name": "", "aid": ""}],
                   {"hwnd": 123, "class": "Qt51514QWindowIcon"})
check("多了控件类 ⇒ 指纹变化", f3 != f1, "%s vs %s" % (f1, f3))
f4 = U.fingerprint(MATERIALIZED, {"hwnd": 999, "class": "Qt51514QWindowIcon"})
check("窗口句柄变化不改指纹（句柄每次都可能变）", f4 == f1, "%s vs %s" % (f1, f4))
f5 = U.fingerprint(BARE, {"hwnd": 123, "class": "Qt51514QWindowIcon"})
check("未物化与物化指纹不同", f5 != f1, "%s vs %s" % (f1, f5))

# ── 4 版本门比对 ────────────────────────────────────────────────────────
old = {"fingerprint": f1, "mmui_hits": U.analyze_nodes(MATERIALIZED)["mmui_hits"]}
new_same = {"fingerprint": f1, "mmui_hits": old["mmui_hits"]}
check("指纹一致 ⇒ ok", U.compare_fingerprint(old, new_same)["status"] == "ok")
new_diff = {"fingerprint": f3, "mmui_hits": old["mmui_hits"] + ["mmui::XToolBar"]}
r = U.compare_fingerprint(old, new_diff)
check("指纹变了 ⇒ changed", r["status"] == "changed", str(r))
check("changed 里给出差异类名", "mmui::XToolBar" in r["changed"], str(r["changed"]))
check("没有历史 ⇒ new", U.compare_fingerprint({}, new_same)["status"] == "new")
check("这次没指纹 ⇒ unknown", U.compare_fingerprint(old, {})["status"] == "unknown")

# ── 5 落盘与摘要（真的写一次文件，再读回）──────────────────────────────
tmp = tempfile.mkdtemp(prefix="uiaprobe_")
U.PROBE_DIR = tmp
res = {"when": "t", "wechat_version": "9.9.9-test", "ok": True, "nodes": 3,
       "mmui_hits": [], "materialized": False, "class_count": 2, "window": {},
       "fingerprint": "abc123", "error": ""}
path = os.path.join(tmp, "9.9.9-test.json")
with open(path, "w", encoding="utf-8") as f:
    json.dump(res, f, ensure_ascii=False)
back = json.load(open(path, encoding="utf-8"))
check("探针结果 json 可读回", back["wechat_version"] == "9.9.9-test" and back["fingerprint"] == "abc123")

# ── 6 版本门必须**先读旧记录再比**（否则永远 ok —— 这个 bug 是自测抓出来的）──
fake_old = dict(res, fingerprint="old0000000000000", materialized=True,
                mmui_hits=["mmui::ChatInputField"])
fake_new = dict(res, fingerprint="new1111111111111", materialized=True,
                mmui_hits=["mmui::ChatInputField", "mmui::XToolBar"])
with open(path, "w", encoding="utf-8") as f:
    json.dump(fake_old, f, ensure_ascii=False)
g_changed = U.version_gate(save=False, cur=fake_new)
check("旧记录不同指纹 ⇒ changed（而不是恒 ok）", g_changed["status"] == "changed", str(g_changed["status"]))
check("changed 时给出建议动作", "整轮重连" in g_changed["advice"], g_changed["advice"])
check("changed 时暴露新旧指纹", g_changed["old_fingerprint"] == "old0000000000000", str(g_changed))
with open(path, "w", encoding="utf-8") as f:
    json.dump(fake_old, f, ensure_ascii=False)
g_ok = U.version_gate(save=False, cur=fake_old)
check("旧记录同指纹 ⇒ ok", g_ok["status"] == "ok", str(g_ok["status"]))
check("brief() 在无微信环境不抛异常", isinstance(U.brief(), str))

print("\n=== UIA 探针自测：%d PASS / %d FAIL ===" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
