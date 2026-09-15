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
import re
import ctypes
import json
import os
import platform
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OUT_DIR = os.path.join(ROOT, "报告")
SECTIONS = []       # [(标题, 行列表, 原始dict)]


def redact(s):
    """打码：家目录 → %USERPROFILE% · 主机名 → %COMPUTERNAME%。
    用户口径（2026-09-13）：发回来的报告里不许带他的个人信息。"""
    try:
        home = os.path.expanduser("~")
        if home:
            s = s.replace(home, "%USERPROFILE%")
            s = s.replace(home.replace("\\", "/"), "%USERPROFILE%")
        s = re.sub(r"[A-Za-z]:\\+Users\\+[^\\\s\"']+", "%USERPROFILE%", s)
        s = re.sub(r"[A-Za-z]:/Users/[^/\s\"']+", "%USERPROFILE%", s)
        node = platform.node()
        if node:
            s = s.replace(node, "%COMPUTERNAME%")
    except Exception:
        pass
    return s


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
        # 自包含的 DPI 自查（不依赖任何开发期模块）
        u32 = ctypes.windll.user32
        try:
            u32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
            u32.SetProcessDpiAwarenessContext.restype = ctypes.c_bool
            aware = bool(u32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)))
        except Exception:
            aware = False
        gdi = ctypes.windll.gdi32
        gdi.GetDeviceCaps.restype = ctypes.c_int
        hdc0 = u32.GetDC(0)
        phys_w, phys_h = gdi.GetDeviceCaps(hdc0, 118), gdi.GetDeviceCaps(hdc0, 117)   # DESKTOPHORZRES/VERTRES
        seen_w, seen_h = gdi.GetDeviceCaps(hdc0, 8), gdi.GetDeviceCaps(hdc0, 10)       # HORZRES/VERTRES
        u32.ReleaseDC(0, hdc0)
        scale = round(phys_w / seen_w, 2) if seen_w else None
        raw["dpi"] = {"permonitorv2": aware, "physical": (phys_w, phys_h), "seen": (seen_w, seen_h), "scale": scale}
        lines.append("  DPI 上下文: %s（SetProcessDpiAwarenessContext(-4) 的返回值）" % ("PerMonitorV2" if aware else "未锁上/已是别的档"))
        lines.append("  物理分辨率: %s×%s · 进程所见: %s×%s ⇒ 缩放 %s×" % (phys_w, phys_h, seen_w, seen_h, scale))
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
        from agent.chat_header import check, reference, ref_sizes
        from agent.wechat import WeChatAdapter
        wx = WeChatAdapter()
        lines.append("  文件传输助手参照: %s · 已学尺寸 %s" % (bool(reference("filehelper")), ref_sizes("filehelper")))
        st = check("filehelper")
        lines.append("  发送前会话头三态: %s · %s" % (st.get("status"), st.get("note")))
        # ⚠️ 一律走**生产路径** `send_text`：会话头判 ok 才投递，否则真实路径（按名字打开会话 +
        #    顺手学该尺寸的参照，下次即可投递）。**不允许"没参照就直接投递"**——
        #    2026-09-13 自测事故：no_ref 照发 ⇒ 消息被打进当时打开的另一个会话（发给了联系人 E）。
        t0 = time.time()
        # ⚠️ 发送必须带**硬超时**：真实路径兜底可能长时间不返回（实测 >120s 未回），
        #    没有这道闸会让整份报告卡死 ⇒ 违反本工具"永不卡住"的设计原则。
        _box = {}

        def _do_send():
            try:
                _box["r"] = wx.send_text("filehelper", token)
            except Exception as _e:      # noqa: BLE001
                _box["e"] = "%s: %s" % (type(_e).__name__, str(_e)[:150])

        _th = threading.Thread(target=_do_send, daemon=True)
        _th.start()
        _th.join(90)
        if _th.is_alive():
            ok, msg = False, "发送实测超时（90 秒未返回）⇒ 放弃等待；报告继续（这本身是有用的现象）"
        elif "e" in _box:
            ok, msg = False, "发送抛异常：%s" % _box["e"]
        else:
            ok, msg = _box.get("r", (False, "无返回"))
        lines.append("  发送结果: %s · %s · %.1fs · token=%s" % (ok, msg, time.time() - t0, token))
        raw.update({"token": token, "ok": bool(ok), "msg": str(msg), "gate": st.get("status")})
        if "投递档" not in str(msg):
            lines.append("  说明: 本次没走投递（前置未满足）⇒ 用了真实路径兜底（会短暂动光标/切前台，属库的既有兜底）")
        if not ok:
            lines.append("  ⇒ 请把本报告发回；若希望用真鼠标兜底，可在控制台把 input.backend 设为 real 再试")
    except Exception as e:
        lines.append("  投递发送实测失败: %s: %s" % (type(e).__name__, str(e)[:200]))
    return lines, raw


def sec_delivery():
    """2026-09-15 新增：**交付面**自检——新加的那些功能在这台机器上到底能不能用。

    为什么单独一节：原来的检验报告只覆盖"环境 + 微信窗口 + 依赖/UIA + 会话头 + 投递发送"，
    而这一年新加的音源（edge 三档）、B 站解析、模型端点、更新链、磁盘占用**一条都没进**，
    另一台电脑跑完也不知道这些能不能用。这一节全部只读、不打字不发消息。
    """
    lines, raw = [], {}

    # ① 版本与更新链
    try:
        from agent.version import VERSION
        lines.append("  本机版本: %s" % VERSION)
        raw["version"] = VERSION
    except Exception as e:
        lines.append("  本机版本: 读不到（%s）" % type(e).__name__)
    try:
        from agent import update_check as UC
        st = UC.state()
        lines.append("  更新检查: status=%s · 本机=%s · 源上=%s · %s"
                     % (st.get("status"), st.get("mine") or "未记录", st.get("theirs") or "-",
                        str(st.get("why") or "")[:60]))
        raw["update"] = {k: st.get(k) for k in ("status", "mine", "theirs", "why")}
    except Exception as e:
        lines.append("  更新检查: 跑不了（%s）" % type(e).__name__)

    # ② 语音音源三档（edge 要联网、sapi 离线、http 是你自己的服务）
    try:
        from agent import voice_models as VM
        s = VM.status()
        lines.append("  语音音源: 档位=%s · 引擎=%s · 可用=%s · %s"
                     % (s.get("backend"), s.get("engine"), s.get("ok"), str(s.get("why") or "")[:70]))
        if s.get("backend") == "edge":
            vs = [v.get("name") for v in (s.get("voices") or []) if isinstance(v, dict)]
            lines.append("  可用音色: %d 个 %s" % (len(vs), ("（%s…）" % vs[0]) if vs else ""))
        raw["voice_models"] = {"backend": s.get("backend"), "engine": s.get("engine"), "ok": s.get("ok")}
    except Exception as e:
        lines.append("  语音音源: 读不到（%s）" % type(e).__name__)
    try:
        import shutil as _sh
        lines.append("  ffmpeg: %s" % (_sh.which("ffmpeg") or "**没找到**（合成要转 wav，必需）"))
    except Exception:
        pass

    # ③ 模型端点：能不能拉这个地址上的模型列表（＝"自己读模型清单"的第一步）
    try:
        from agent.config import get_config
        from agent.llm import join_url, _auth_headers
        import urllib.request as _ur
        api = (get_config() or {}).get("api") or {}
        base = str(api.get("base_url") or "")
        if not base:
            lines.append("  模型端点: 没填地址")
        else:
            u = join_url(base, "/models")      # ⚠️ 必须带前导斜杠：join_url 只 rstrip("/") 再拼，不补斜杠
            req = _ur.Request(u, headers=_auth_headers(str(api.get("api_key") or "")))
            with _ur.urlopen(req, timeout=10) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
            ids = [str((x or {}).get("id") or "") for x in (d.get("data") or [])]
            lines.append("  模型端点: 通 · 这个 key 能用 %d 个模型 %s"
                         % (len(ids), ("（%s…）" % ", ".join(ids[:4])) if ids else ""))
            cur = str(api.get("model") or "")
            lines.append("  当前模型 %s：%s" % (cur or "(未填)",
                                              "在列表里" if cur in ids else "**不在这个端点返回的列表里**"))
            raw["models"] = {"base": base, "count": len(ids), "has_current": cur in ids}
    except Exception as e:
        lines.append("  模型端点: 拉不到列表（%s）——不影响聊天，但无法核对模型名" % str(e)[:60])

    # ④ B 站（新功能：群友丢链接能不能看懂）
    try:
        from agent import bilibili as BL
        d, why = BL._get_json(BL.API_VIEW % "BV1LLjH6hEio", timeout=8)
        if d and d.get("code") == 0:
            dd = d.get("data") or {}
            lines.append("  B站连通: 通（示例稿件「%s」）" % str(dd.get("title") or "")[:26])
            raw["bilibili"] = {"ok": True}
        else:
            lines.append("  B站连通: 不通（%s）" % (why or ("code=%s" % (d or {}).get("code"))))
            raw["bilibili"] = {"ok": False, "why": why}
    except Exception as e:
        lines.append("  B站连通: 探不了（%s）" % type(e).__name__)

    # ⑤ 磁盘占用与清理
    try:
        from agent import housekeeping as HK
        f = HK.footprint()
        lines.append("  磁盘: 临时残留 %d 项 / %.2f MB · 语音产物 %d 个 / %.2f MB"
                     % (f["temp"]["entries"], f["temp"]["mb"],
                        f["dirs"].get("media/tts", {}).get("files", 0),
                        f["dirs"].get("media/tts", {}).get("mb", 0)))
        raw["disk"] = {"temp": f["temp"], "tts": f["dirs"].get("media/tts")}
    except Exception as e:
        lines.append("  磁盘: 查不了（%s）" % type(e).__name__)
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
                      ("四、会话头可读性", sec_visual),
                      ("五、交付面：音源 / 模型端点 / B站 / 更新 / 磁盘", sec_delivery)):
        lines, raw = safe(fn, title) or ([], {})
        add(title, lines, raw)
    if args.send_test:
        lines, raw = safe(sec_send_test, "六、投递发送实测") or ([], {})
        add("六、投递发送实测", lines, raw)
    else:
        add("六、投递发送实测", ["  未执行（加 --send-test 才会真发一条测试消息）"], {})

    ts = time.strftime("%Y%m%d-%H%M%S")
    os.makedirs(OUT_DIR, exist_ok=True)
    base = os.path.join(OUT_DIR, "检验报告-%s" % ts)
    raw_all = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "host": platform.node(),
               "elapsed_s": round(time.time() - t0, 1), "send_test": bool(args.send_test),
               "sections": [{"title": t, "lines": ln, "raw": r} for t, ln, r in SECTIONS]}
    with open(base + ".json", "w", encoding="utf-8") as f:
        f.write(redact(json.dumps(raw_all, ensure_ascii=False, indent=1)))
    txt = ["群相 · 环境检验报告",
           "生成时间: %s · 主机: %s · 耗时 %.1fs · 发送实测: %s" % (
               raw_all["when"], raw_all["host"], raw_all["elapsed_s"], "是" if args.send_test else "否"),
           "=" * 72]
    for t, ln, _r in SECTIONS:
        txt.append("\n【%s】" % t)
        txt.extend(ln)
    txt.append("\n" + "=" * 72)
    txt.append("把本文件（或同名 .json）发回即可定位问题。")
    with open(base + ".txt", "w", encoding="utf-8") as f:
        f.write(redact("\n".join(txt) + "\n"))
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
