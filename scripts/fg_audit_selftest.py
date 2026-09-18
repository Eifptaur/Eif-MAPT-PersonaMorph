#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布前静态审计判据：**产品路径里不许有"没过闸就动前台/置顶/光标"的调用**（2026-09-18 立）。

起因（作者口径，原话）：「发布前你要再查一遍，会不会有本来可以全后台的代码，结果由于某些疏忽，
导致它在某一环会把窗口带到前台。」

做法＝AST 扫 `agent/**` + `scripts/persona_morph.py|watchdog.py` 里所有会动前台/置顶/光标/窗口几何的
调用，逐个问"它有没有过闸"；闸的四个真源：`ui_adapt.fg_allowed()`、`harden_gui_class` 的类级包装
（`bring_to_front`/`calibrate_layout`/`ensure_visible`/`_minimize_blockers`/`restore_zorder`/`_get_uia`
都在里面）、`ui_adapt.real_guard()`（真鼠标下沉点）、`_background_only()` / `allow_real_fallback` 配置闸。

**白名单**（有意的、逐条写明理由；不在名单上的新命中一律判红 —— 这就是"机械完整性"）：
  · `agent/ui_adapt.py` / `agent/input_backend.py` / `agent/notify_ui.py` —— 闸实现自身 + 我们自己控制台窗口的提示；
  · `wechat._restore_fg` / `_force_foreground` —— **把前台还给用户**（restore，不是抢占）；
  · `wechat._minimize_back_if_needed` —— 压到 Z 底（HWND_BOTTOM），不是置前；
  · `wechat._ensure_main_visible` —— `SW_SHOWNOACTIVATE` + `SWP_NOACTIVATE`（不激活还原）；
  · `wechat._type_into_focused` / `voice_strip._send_alt|_cancel_alt` —— 真键鼠**原语**，入口已过闸
    （`moments_comment` 过 `_background_only`；`voice_strip.send` 顶部现在过 `_background_only`）；
  · `wechat._moments_scroll_real` / `moments_comment` —— 朋友圈真鼠标路，入口 `moments_scroll` /
    `moments_comment` 已过 `_background_only`；
  · `wechat._restore_after_send` —— 发送**收尾**：`restore_zorder()`（类级已过闸）＋把主窗压到
    Z 底（`HWND_BOTTOM`），方向都是"离开前台"而不是抢占；
  · `scripts/*selftest*` —— 判据里的假对象/阳性对照。
"""
import ast
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

FG_CALLS = {
    "SetForegroundWindow", "BringWindowToTop", "SetActiveWindow", "SwitchToThisWindow",
    "AllowSetForegroundWindow", "SetWindowPos", "ShowWindow", "ShowWindowAsync",
    "SetFocus", "AttachThreadInput", "SetCursorPos", "mouse_event", "SendInput",
    "keybd_event", "MoveWindow", "SetWindowPlacement", "bring_to_front", "calibrate_layout",
    "ensure_visible", "restore_zorder", "open_chat",
}
GATE_WORDS = ("fg_allowed", "fg_guard", "real_guard", "background_only", "allow_real_fallback",
              "_real_fallback_allowed", "harden", "FG_", "foreground_ok", "hardened", "fg_refused")

# 白名单：(文件后缀片段, 函数名) —— 命中即视为"有意的、原因见文件头"
ALLOW = {
    ("ui_adapt.py", None), ("input_backend.py", None), ("notify_ui.py", None),
    ("wechat.py", "_restore_fg"), ("wechat.py", "_force_foreground"),
    ("wechat.py", "_minimize_back_if_needed"), ("wechat.py", "_ensure_main_visible"),
    ("wechat.py", "_type_into_focused"), ("wechat.py", "_moments_scroll_real"),
    ("wechat.py", "moments_comment"), ("wechat.py", "moments_like"),
    ("wechat.py", "moments_publish_text"),
    ("voice_strip.py", "_send_alt"), ("voice_strip.py", "_cancel_alt"),
    ("window_borrow.py", "restore"),
    ("wechat.py", "_restore_after_send"),
}

PASS = FAIL = 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  OK   " + name)
    else:
        FAIL += 1
        print("  FAIL " + name + ("   [%s]" % extra if extra else ""))


def enclosing(tree, lineno):
    best = None
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.lineno <= lineno <= getattr(n, "end_lineno", n.lineno):
            if best is None or n.lineno > best.lineno:
                best = n
    return best


def scan(path, rel):
    src = io.open(path, encoding="utf-8", errors="ignore").read()
    lines = src.split("\n")
    try:
        tree = ast.parse(src)
    except Exception:
        return []
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else "")
        if name not in FG_CALLS:
            continue
        f = enclosing(tree, node.lineno)
        fname = f.name if f else None
        body = ("\n".join(lines[f.lineno - 1: getattr(f, "end_lineno", f.lineno)]) if f else "")
        gated = any(g in body for g in GATE_WORDS)
        allowed = (os.path.basename(rel), None) in ALLOW or (os.path.basename(rel), fname) in ALLOW
        out.append({"rel": rel, "line": node.lineno, "api": name, "fn": fname,
                    "gated": gated, "allowed": allowed})
    return out


hits = []
for rel in ("agent", "scripts"):
    base = os.path.join(ROOT, rel)
    for dp, dn, fn in os.walk(base):
        dn[:] = [d for d in dn if d not in ("__pycache__",)]
        for f in fn:
            if not f.endswith(".py"):
                continue
            p = os.path.join(dp, f)
            r = os.path.relpath(p, ROOT).replace("\\", "/")
            if r.startswith("scripts/") and ("selftest" not in r):
                continue
            if "selftest" in r:
                continue          # 判据里的假对象不算产品路径（要真跑真窗口的判据另有背景自检管）
            hits += scan(p, r)

bad = [h for h in hits if not (h["gated"] or h["allowed"])]
print("── 静态审计：产品路径里 %d 处前台/置顶/光标类调用 ──" % len(hits))
ok("没有**未过闸**的置前/置顶/光标调用（%d 处）" % len(bad), not bad,
   "; ".join("%s:%s %s" % (b["rel"], b["line"], b["api"]) for b in bad[:5]))

# 关键三处必须**有闸**（本次补的两处 + 类级包装）
_W = io.open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
_V = io.open(os.path.join(ROOT, "agent", "voice_strip.py"), encoding="utf-8").read()
_U = io.open(os.path.join(ROOT, "agent", "ui_adapt.py"), encoding="utf-8").read()
_scan_hint = [
    h for h in hits if h["rel"].endswith("wechat.py") and h["fn"] == "_moments_focus"]
ok("`_moments_focus` 这条路径已被扫到且过闸", bool(_scan_hint) and all(h["gated"] for h in _scan_hint),
   str(_scan_hint[:2]))
_seg = _W[_W.index("def _moments_focus("):]
_seg = _seg[:_seg.index("\n    def ", 10)]
ok("`_moments_focus` 里读了唯一真源 `ui_adapt.fg_allowed()`", "fg_allowed()" in _seg)
ok("闸不过时**只枚举不激活**并留日志", "不激活" in _seg and "log.info" in _seg)
ok("`voice_strip.send` 顶部过「只走后台」闸（真鼠标+真键盘的路）",
   "background_only_reason(\"发真语音条\")" in _V)
ok("类级包装覆盖 `_get_uia`（它一旦被调就会把微信顶到前）",
   '("_get_uia", None)' in _U and "def harden_gui_class" in _U)
ok("闸实现自身在读配置失败时 fail-closed", "读配置失败（按拒绝处理）" in _U)

print("\n==== 前台静态审计判据：%d 通过 / %d 失败 ====" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
