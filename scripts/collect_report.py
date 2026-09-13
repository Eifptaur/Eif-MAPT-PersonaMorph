# -*- coding: utf-8 -*-
"""随包检验工具：在**任意一台电脑**上跑一次，生成一份"能发回来直接定位问题"的报告。

用途（用户口径）：他把包拷到另一台电脑，双击 `一键检验.cmd` 跑一遍；
遇到任何问题，**把生成的报告文件发回来**，我据此改进。

设计原则：
  · **永不崩**：每个小节各自 try/except，缺什么就在报告里写明"这一节失败/不适用"，绝不让整份报告出不来；
  · **只读为主**：默认不发送任何消息、不点击、不发表；只有加 `--send-test` 才做一次"投递给文件传输助手"的实测；
  · **自包含**：报告里带环境指纹（系统/DPI/分辨率/显示器/微信版本/UI 类名/窗口几何/依赖/输入档位/会话头可读性），
    拿到它不看日志就能判断"是没找到还是找错了"；
  · **落两份**：人读的 `.txt` 与可机器解析的 `.json`。

用法：
    runtime\\python\\python.exe scripts\\collect_report.py            # 只读体检
    runtime\\python\\python.exe scripts\\collect_report.py --send-test # 额外做一次投递发送实测（会真发一条测试消息）
"""
import argparse
import ctypes
import json
import os
import platform
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "_scratch"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OUT_DIR = os.path.join(ROOT, "报告")
SECTIONS = []       # [(标题, 行列表, 原始dict)]


def add(title, lines, raw=None):
    SECTIONS.append((title, list(lines), raw or {}))


def safe(fn, title, fallback="这一节失败（不影响其它节）"):
    """跑一小节，失败也只在报告里留痕。"""
    try:
        return fn()
    except Exception as e:
        add(title, ["  ✘ %s: %s" % (type(e).__name__, str(e)[:200])])
        return None


# ── 1 系统 / 显示 ──────────────────────────────────────────────────────
def sec_system():
    lines, raw = [], {}
    lines.append("  操作系统: %s %s (build %s) · %s" % (
        platform.system(), platform.release(), platform.version(), platform.machine()))
    lines.append("  Python: %s (%s)" % (platform.python_version(), sys.executable))
    lines.append("  是否 64 位解释器: %s" % (sys.maxsize > 2 ** 32))
    try:
        import dpi as _dpi
        lines.append("  DPI 上下文: %s" % _dpi.fix())
        wh = _dpi.where()
        raw["dpi"] = wh
        lines.append("  物理分辨率: %s×%s · 进程所见: %s×%s ⇒ 缩放 %s×" % (
            wh.get("physical", ("?", "?"))[0], wh.get("physical", ("?", "?"))[1],
            wh.get("seen", ("?", "?"))[0], wh.get("seen", ("?", "?"))[1], wh.get("scale")))
    except Exception as e:
        lines.append("  DPI 自查失败: %s" % e)
    try:
        u32 = ctypes.windll.user32
        monitors = []

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
        MONITORENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                                             ctypes.POINTER(RECT), ctypes.c_double)

        def cb(h, hdc, r, data):
            monitors.append((r.contents.left, r.contents.top, r.contents.right, r.contents.bottom))
            return 1
        u32.EnumDisplayMonitors(0, 0, MONITORENUMPROC(cb), 0)
        raw["monitors"] = monitors
        lines.append("  显示器数量: %d ⇒ %s" % (len(monitors), monitors))
    except Exception as e:
        lines.append("  显示器枚举失败: %s" % e)
    return lines, raw


# ── 2 微信版本 / UI 类名 / 窗口几何 ─────────────────────────────────────
def sec_wechat():
    lines, raw = [], {}
    try:
        from agent import wechat_version_info  # type: ignore
    except Exception:
        try:
            from agent.wechat import wechat_version_info   # type: ignore
        except Exception:
            wechat_version_info = None
    if wechat_version_info:
        try:
            info = wechat_version_info() or {}
            raw["version_info"] = info
            for k in ("version", "path", "supported", "message"):
                if k in info:
                    lines.append("  %s: %s" % (k, str(info.get(k))[:160]))
        except Exception as e:
            lines.append("  版本检测失败: %s" % e)
    try:
        import glob
        exes = []
        for pat in ("C:/Program Files/Tencent/WeChat/Weixin.exe", "C:/Program Files (x86)/Tencent/WeChat/Weixin.exe",
                    "D:/WX/Weixin/Weixin.exe", "M:/WX/Weixin/Weixin.exe"):
            if os.path.exists(pat):
                exes.append(pat)
        lines.append("  常见安装路径命中: %s" % (exes or "无（可能在其它盘，看上面的 path）"))
    except Exception:
        pass
    # 窗口枚举（不依赖库）
    try:
        from agent import input_backend as ib
        raw["input"] = ib.status()
        main = ib.find_main_window()
        lines.append("  输入后端档位: %s（level=%s, touches_cursor=%s, DPI=%s）" % (
            raw["input"].get("backend"), raw["input"].get("level"),
            raw["input"].get("touches_cursor"), raw["input"].get("dpi_mode")))
        lines.append("  主窗 hwnd=%s class=%s rect=%s visible=%s" % (
            main, ib._class_of(main) if main else "-",
            ib.window_rect(main) if main else "-",
            bool(ctypes.windll.user32.IsWindowVisible(main)) if main else "-"))
        lines.append("  候选同类名窗口（应含主窗；朋友圈编辑窗也算，注意区分）:")
        CB = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        found = []

        def cb2(h, _l):
            if ib._class_of(h) == ib.MAIN_CLASS:
                found.append((int(h), ib.window_rect(h), ib.RENDER_CHILD in ib._child_classes(h)))
            return True
        ctypes.windll.user32.EnumWindows(CB(cb2), 0)
        for h, r, child in found:
            lines.append("    hwnd=%-9s rect=%s 带渲染子窗=%s" % (h, r, child))
        raw["windows"] = [{"hwnd": h, "rect": r, "has_render_child": c} for h, r, c in found]
    except Exception as e:
        lines.append("  窗口枚举失败: %s" % e)
    return lines, raw


# ── 3 依赖 / 版本能力矩阵 / UIA ────────────────────────────────────────
def sec_deps_and_caps():
    lines, raw = [], {}
    try:
        from agent import dep_heal as dh
        diag = dh.diagnose()
        raw["deps"] = diag
        lines.append("  体检来源: %s" % dh.probe_source())
        lines.append("  依赖合计 %d 项：%s" % (len(diag), dh.summary_line()))
        bad = [d for d in diag if d["status"] != "ok"]
        for d in bad[:12]:
            lines.append("    ! %s 需 %s，实装 %s（%s）" % (d["name"], d["required"], d["installed"] or "无", d["status"]))
        lines.append("  离线 wheel 可用: %s" % dh.offline_available())
    except Exception as e:
        lines.append("  依赖体检失败: %s" % e)
    try:
        from agent import version_matrix as vm
        cur = vm.current()
        raw["caps"] = {"wechat": cur["wechat"], "adapter": cur["adapter"],
                       "summary": cur["summary"], "gate": cur["gate"]}
        lines.append("  版本能力矩阵: 微信 %s × 适配层 %s ⇒ %s" % (
            cur["wechat"], cur["adapter"], cur["summary"]))
        lines.append("  版本门: measured=%s %s" % (cur["gate"]["measured"], cur["gate"]["advice"][:120]))
        for cid, v in sorted(cur["caps"].items()):
            lines.append("    %-18s %-10s %s" % (cid, v["status"], v["evidence"][:80]))
    except Exception as e:
        lines.append("  能力矩阵失败: %s" % e)
    try:
        from agent import uia_probe
        res = uia_probe.probe(save=False)
        raw["uia"] = {k: res.get(k) for k in ("nodes", "materialized", "fingerprint", "error")}
        lines.append("  UIA 探针: 节点 %s · 物化=%s · 指纹 %s %s" % (
            res.get("nodes"), res.get("materialized"), res.get("fingerprint"),
            ("· " + str(res.get("error"))[:100]) if res.get("error") else ""))
    except Exception as e:
        lines.append("  UIA 探针失败: %s" % e)
    return lines, raw


# ── 4 会话头可读性 / 面板探测 ──────────────────────────────────────────
def sec_visual():
    lines, raw = [], {}
    try:
        from agent import chat_header as ch
        fp = ch.capture()
        raw["header_len"] = len(fp)
        if fp:
            lines.append("  会话头指纹: 可抓，%d 维，样例 %s" % (len(fp), fp[:6]))
            pane = None
            try:
                from PIL import ImageGrab
                from agent.wechat import WeChatAdapter
                gui = WeChatAdapter()._get_gui()
                gui._update_render_rect()
                img = ImageGrab.grab(tuple(gui.render_rect))
                pane = ch.detect_pane_left(img)
                lines.append("  探测到的聊天面板左沿: %s px（渲染宽 %d）" % (
                    pane or "未探测到（将用比例兜底）", gui.render_rect[2] - gui.render_rect[0]))
                raw["pane_left"] = pane
            except Exception as e:
                lines.append("  面板左沿探测失败: %s" % e)
        else:
            lines.append("  会话头指纹: **抓不到**（微信窗口不可见/被最小化/权限不足？）")
    except Exception as e:
        lines.append("  会话头检查失败: %s" % e)
    return lines, raw


# ── 5 可选：投递发送实测 ───────────────────────────────────────────────
def sec_send_test():
    lines, raw = [], {}
    token = "检验%05d" % (int(time.time()) % 100000)
    try:
        from agent.chat_header import verify, reference, seed_from_main
        from agent.wechat import WeChatAdapter
        wx = WeChatAdapter()
        ok_ref = bool(reference("filehelper"))
        lines.append("  文件传输助手参照存在: %s" % ok_ref)
        if not ok_ref:
            ok, msg = seed_from_main("filehelper", note="检验包首次运行")
            lines.append("  首次取参照: %s %s" % (ok, msg))
        else:
            okv, whyv = verify("filehelper")
            lines.append("  会话头校验: %s（%s）" % (okv, whyv))
        t0 = time.time()
        ok, msg = wx.send_text_posted(token, "filehelper")
        lines.append("  投递发送: %s · %s · %.1fs · token=%s" % (ok, msg, time.time() - t0, token))
        raw.update({"token": token, "ok": bool(ok), "msg": str(msg)})
        if not ok:
            lines.append("  ⇒ 请把本报告发回；若希望用真鼠标兜底，可在控制台把 input.backend 设为 real 再试")
    except Exception as e:
        lines.append("  投递发送实测失败: %s: %s" % (type(e).__name__, str(e)[:200]))
    return lines, raw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--send-test", action="store_true", help="额外做一次投递发送实测（会真发一条测试消息）")
    ap.add_argument("--open", action="store_true", help="跑完打开报告目录")
    args = ap.parse_args()

    t0 = time.time()
    for title, fn in (("一、系统与显示", sec_system),
                      ("二、微信与窗口", sec_wechat),
                      ("三、依赖 / 版本能力矩阵 / UIA", sec_deps_and_caps),
                      ("四、会话头可读性", sec_visual)):
        lines, raw = safe(fn, title) or ([], {})
        add(title, lines, raw)
    if args.send_test:
        lines, raw = safe(sec_send_test, "五、投递发送实测") or ([], {})
        add("五、投递发送实测", lines, raw)
    else:
        add("五、投递发送实测", ["  未执行（加 --send-test 才会真发一条测试消息）"], {})

    ts = time.strftime("%Y%m%d-%H%M%S")
    os.makedirs(OUT_DIR, exist_ok=True)
    base = os.path.join(OUT_DIR, "检验报告-%s" % ts)
    raw_all = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "host": platform.node(),
               "elapsed_s": round(time.time() - t0, 1), "send_test": bool(args.send_test),
               "sections": [{"title": t, "lines": ln, "raw": r} for t, ln, r in SECTIONS]}
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(raw_all, f, ensure_ascii=False, indent=1)
    with open(base + ".txt", "w", encoding="utf-8") as f:
        f.write("群相灵 · 环境检验报告\n")
        f.write("生成时间: %s · 主机: %s · 耗时 %.1fs · 发送实测: %s\n" % (
            raw_all["when"], raw_all["host"], raw_all["elapsed_s"], "是" if args.send_test else "否"))
        f.write("=" * 72 + "\n")
        for t, ln, _r in SECTIONS:
            f.write("\n【%s】\n" % t)
            for x in ln:
                f.write(x + "\n")
        f.write("\n" + "=" * 72 + "\n")
        f.write("把本文件（或同名 .json）发回即可定位问题。\n")
    print("[完成] 报告已生成：")
    print("  " + base + ".txt")
    print("  " + base + ".json")
    if args.open:
        try:
            os.startfile(OUT_DIR)      # noqa: S606  打开报告目录
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
