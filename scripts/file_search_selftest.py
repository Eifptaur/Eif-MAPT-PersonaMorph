#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地文件"找 + 发"判据（含 UI 映射）：**能力必须有 UI 面，没有就把那个面做出来**。

用户 2026-09-13 原话：「帮用户找文件的触发方法呢，找完文件发群里的那些呢，**所有功能以及可自定义的东西
都要映射到 UI 上。如果没有可以映射的对象，就要把那个对象做出来**」

判据：
  ① 搜索：只在**配置的目录**里找；没开/没配目录 ⇒ 明确文案；噪声目录与隐藏目录跳过；结果按时间新→旧
  ② 越界闸：`is_inside()` 对 `..` 与目录外路径判否；`send_local_file` 对允许目录外的路径**拒绝**
  ③ 发送闸：总开关关 / 发文件 opt-in 关 / 超大文件 ⇒ 都不发（记账适配器验"一次都没碰发送"）
  ④ 唯一性：同名多个 ⇒ 让调用方去挑（不瞎猜）；唯一命中才发
  ⑤ UI 映射：控制台里有「本地文件」栏（开关/可搜目录增删/触发条件/上限/引导按钮）+ 三处端点接线
  ⑥ 引导与触发条件：GUIDES 里有 file 那条且有入口按钮；`file_search.trigger_mode` 真的改变提示词
"""
import json
import os
import shutil
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


from agent import file_search as FS  # noqa: E402
from agent import tools as TL  # noqa: E402
from agent import console_html as CH  # noqa: E402

tmp = tempfile.mkdtemp(prefix="pm_fs_")
d1 = os.path.join(tmp, "下载")
d2 = os.path.join(tmp, "报告")
os.makedirs(os.path.join(d1, "sub"), exist_ok=True)
os.makedirs(d2, exist_ok=True)
os.makedirs(os.path.join(d1, ".git"), exist_ok=True)
now = time.time()
for p, age in ((os.path.join(d1, "周报-A.docx"), 10), (os.path.join(d1, "sub", "周报-B.docx"), 900),
               (os.path.join(d1, "照片.jpg"), 500), (os.path.join(d1, ".git", "周报-隐藏.docx"), 1),
               (os.path.join(d2, "周报-C.docx"), 3000), (os.path.join(d2, "别的.pdf"), 100)):
    open(p, "wb").write(b"x" * 64)
    os.utime(p, (now - age, now - age))

_real = FS._cfg
FS._cfg = lambda: {"enabled": True, "dirs": [d1, d2], "max_results": 20, "max_mb": 1,
                   "trigger_mode": "on_request"}

print("── A. 搜索 ──")
hits, why = FS.search("周报")
names = [h["name"] for h in hits]
ok("找到候选", len(hits) == 3, "%s ｜ %s" % (names, why))
ok("跳过 .git 里的同名文件", "周报-隐藏.docx" not in names)
ok("按修改时间新→旧", names[0] == "周报-A.docx" and names[-1] == "周报-C.docx", str(names))
h2, why2 = FS.search("不存在的名字")
ok("找不到 ⇒ 明确说明", h2 == [] and "没有文件名含" in why2, why2[:40])
FS._cfg = lambda: {"enabled": False, "dirs": [d1]}
ok("总开关关 ⇒ 明确文案", FS.search("周报")[0] == [] and "没开" in FS.search("周报")[1])
FS._cfg = lambda: {"enabled": True, "dirs": []}
ok("没配目录 ⇒ 明确文案", "还没配可搜目录" in FS.search("周报")[1])
FS._cfg = lambda: {"enabled": True, "dirs": [d1, d2], "max_results": 20, "max_mb": 1}

print("── B. 越界与唯一性 ──")
outside = os.path.join(tmp, "外面.txt")
open(outside, "wb").write(b"y")
ok("目录外的路径 ⇒ is_inside 判否", FS.is_inside(outside) is False)
ok("目录内 ⇒ 判是", FS.is_inside(os.path.join(d1, "周报-A.docx")) is True)
ok("用 .. 绕出去也判否", FS.is_inside(os.path.join(d1, "..", "外面.txt")) is False)
p_ok, why_ok = FS.resolve("周报-A.docx")
ok("唯一命中 ⇒ 解析出路径", bool(p_ok) and p_ok.endswith("周报-A.docx"), str(why_ok)[:30])
p_bad, why_bad = FS.resolve(outside)
ok("给目录外路径 ⇒ 拒绝", p_bad is None and "不在允许目录内" in why_bad, why_bad[:40])
open(os.path.join(d1, "同名.docx"), "wb").write(b"z")
open(os.path.join(d2, "同名.docx"), "wb").write(b"z")
p_dup, why_dup = FS.resolve("同名.docx")
ok("同名多个 ⇒ 不瞎猜，让人挑", p_dup is None and "命中多个" in why_dup, why_dup[:50])


class FakeWeChat:
    def __init__(self):
        self.calls = []

    def send_file_posted(self, chat_id, path, **kw):
        self.calls.append((chat_id, path))
        return True, "投递发文件成功（DB 回读 local_id=1 type=文件/链接/卡片）"


def mkctx(w):
    return {"store": None, "chat_key": "group:x", "chat_id": "x", "wechat": w,
            "sender": None, "session": {"sent": []}}


print("── C. 发送闸 ──")
_real_cfg = TL.get_config
_real_note = FS.note_sent
FS.note_sent = lambda path, chat_id="": None        # 测试不写真实台账（data/sent_local_files.json）
try:
    TL.get_config = lambda: {"file_search": {"enabled": False}, "send": {"file_forward_optin": True}}
    fw = FakeWeChat()
    r = TL._exec_send_local_file(mkctx(fw), {"name_or_path": "周报-A.docx"})
    ok("找文件没开 ⇒ 不发", fw.calls == [] and "默认关闭" in (r.get("content") or ""), (r.get("content") or "")[:36])
    TL.get_config = lambda: {"file_search": {"enabled": True, "max_mb": 1}, "send": {"file_forward_optin": False}}
    fw2 = FakeWeChat()
    r2 = TL._exec_send_local_file(mkctx(fw2), {"name_or_path": "周报-A.docx"})
    ok("发文件 opt-in 没开 ⇒ 不发且说明会抢前台", fw2.calls == [] and "抢一次前台" in (r2.get("content") or ""))
    TL.get_config = lambda: {"file_search": {"enabled": True, "max_mb": 1}, "send": {"file_forward_optin": True}}
    fw3 = FakeWeChat()
    r3 = TL._exec_send_local_file(mkctx(fw3), {"name_or_path": outside})
    ok("目录外路径 ⇒ 拒绝且不发", fw3.calls == [] and r3.get("is_error") is True, (r3.get("content") or "")[:40])
    big = os.path.join(d1, "大文件.bin")
    open(big, "wb").write(b"0" * (2 * 1024 * 1024))
    fw4 = FakeWeChat()
    r4 = TL._exec_send_local_file(mkctx(fw4), {"name_or_path": "大文件.bin"})
    ok("超过大小上限 ⇒ 不发", fw4.calls == [] and "太大" in (r4.get("content") or ""), (r4.get("content") or "")[:30])
    fw5 = FakeWeChat()
    r5 = TL._exec_send_local_file(mkctx(fw5), {"name_or_path": "周报-A.docx"})
    ok("唯一命中 ⇒ 真的走发送", len(fw5.calls) == 1 and r5.get("is_error") is not True, (r5.get("content") or "")[:40])
finally:
    TL.get_config = _real_cfg
    FS.note_sent = _real_note

print("── D. 工具注册与提示词触发条件 ──")
d = {x["name"]: x for x in TL.build_tool_defs()}
for nm in ("find_local_file", "send_local_file"):
    ok("注册了 %s" % nm, nm in d and callable(d[nm].get("execute")))
    ok("  %s 描述里写了触发场景" % nm, len(str(d[nm].get("description") or "")) > 30)
from agent import prompt as PR  # noqa: E402
from agent import config as CFG  # noqa: E402
_rc = CFG.get_config
try:
    CFG.get_config = lambda: {"file_search": {"trigger_mode": "on_request"},
                              "image_reply": {"trigger_mode": "on_request"},
                              "voice_reply": {"trigger_mode": "on_request"},
                              "api": {"vision": True}, "web_search": {"enabled": True}}
    import importlib
    importlib.reload(PR)
    t = PR._scene_rules()
    ok("提示词里有找文件的触发条件", "find_local_file" in t and "send_local_file" in t)
    CFG.get_config = lambda: {"file_search": {"trigger_mode": "off"},
                              "image_reply": {"trigger_mode": "on_request"},
                              "voice_reply": {"trigger_mode": "on_request"},
                              "api": {"vision": True}, "web_search": {"enabled": True}}
    importlib.reload(PR)
    ok("trigger_mode=off ⇒ 不再给找文件指令", "find_local_file" not in PR._scene_rules())
finally:
    CFG.get_config = _rc
    import importlib
    importlib.reload(PR)

print("── E. UI 映射（能力必须有 UI 面）──")
H = CH.HTML
for key in ("file_search.enabled", "file_search.trigger_mode", "file_search.max_results", "file_search.max_mb"):
    ok("设置项 %s 在控制台" % key, ('data-cfg="%s"' % key) in H)
ok("有「可搜目录」输入与加入按钮", 'id="fsNewDir"' in H and 'id="fsAdd"' in H)
ok("有目录列表与最近台账的渲染点", 'id="fsList"' in H and 'id="fsRecent"' in H)
ok("有引导按钮 #fsGuide 且接到 openGuide", 'id="fsGuide"' in H and "['fsGuide','file']" in H.replace(" ", ""))
ok("GUIDES 里有 file 那条", "\n  file: {" in H)
ok("两条端点接线（add / del）", "/api/file_search/add" in H and "/api/file_search/del" in H)
_wu = open(os.path.join("agent", "webui.py"), encoding="utf-8").read()
ok("后端有 /api/file_search/add 与 del", '"/api/file_search/add", "/api/file_search/del"' in _wu)
ok("/api/status 带 file_search 段", 'st["file_search"] = _fsx.snapshot()' in _wu)
ok("触发条件也映射到了图/语音面板", 'data-cfg="image_reply.trigger_mode"' in H and 'data-cfg="voice_reply.trigger_mode"' in H)

FS._cfg = _real
shutil.rmtree(tmp, ignore_errors=True)
print("\n%d/%d 通过" % (PASS, PASS + FAIL))
sys.exit(1 if FAIL else 0)
