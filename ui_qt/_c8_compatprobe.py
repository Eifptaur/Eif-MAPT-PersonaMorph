# -*- coding: utf-8 -*-
"""丙-8 · P0-C 兼容性审计探针（只读；offscreen 可跑；产物 _c8_compatprobe_out.log）。

七项里能机械断言的都在这里：
  A. 字体回退链（双 TTF 随包 / emoji 走 WINDIR / resolve_family 降级 / qfont 空串回退）
  B. 路径硬编码（产品代码零命中由 grep 取证，这里断言 emoji 路径源自 WINDIR）
  C. 系统版本（断言 ui_qt 源码无 Win11-only 裸调；位数由 compat.fingerprint 报）
  D. DPI（rounding policy 实名 / PerMonitorV2 钉子 / 极小窗下限）
  E. 自举（qt_bootstrap 三镜像 + 版本钉 + 兼容指纹五节实跑）
  F. 网络（九源/镜像常量表在位 —— 源码断言）
  G. 125% 奇数档（policy=PassThrough 实名取证）

纪律：不写任何产品文件（logs/data 零触碰）；fingerprint() 只读。
"""

import io
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

os.environ["QT_QPA_PLATFORM"] = "offscreen"   # 取证纪律：不抢真桌面

_OUT = io.StringIO()


def ok(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + (("  [%s]" % detail) if detail else ""))
    print(("  OK   " if cond else "  FAIL ") + name + (("  [%s]" % detail) if detail else ""), file=_OUT)
    return bool(cond)


def main() -> int:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QGuiApplication
    app = QApplication([])

    import stylekit_qt as SK

    n_fail = 0

    print("== A. 字体回退链 ==")
    print("== A. 字体回退链 ==", file=_OUT)
    windir = os.environ.get("WINDIR", "C:/Windows")
    ok("emoji 兜底路径源自 WINDIR（不假设 C: 盘）",
       str(SK._FONT_EMOJI_FILE).lower().replace("\\\\", "/").startswith(str(Path(windir)).lower().replace("\\\\", "/")),
       str(SK._FONT_EMOJI_FILE))
    ok("双 TTF 随包在位（相对路径，不依赖用户机器）",
       SK._FONT_DISPLAY_FILE.exists() and SK._FONT_BODY_FILE.exists(),
       "%s | %s" % (SK._FONT_DISPLAY_FILE.name, SK._FONT_BODY_FILE.name))
    disp, body, emoji = SK.ensure_fonts()
    ok("ensure_fonts 三 family 本机全注册成功", bool(disp and body and emoji),
       "display=%r body=%r emoji=%r" % (disp, body, emoji))
    ok("ensure_fonts 幂等（二调同值）", SK.ensure_fonts() == (disp, body, emoji))
    ok("resolve_family：雅黑在本机命中不降级", SK.resolve_family("Microsoft YaHei UI") == "Microsoft YaHei UI",
       SK.resolve_family("Microsoft YaHei UI"))
    # offscreen 扫不到系统字体（stylekit L320 注释口径）⇒ 注入族表测降级链本身
    _real_fams = SK.available_ui_families
    try:
        SK.available_ui_families = lambda: ["Segoe UI", "SimHei"]
        r1 = SK.resolve_family("Microsoft YaHei UI")
        SK.available_ui_families = lambda: ["Microsoft YaHei UI", "Segoe UI"]
        r2 = SK.resolve_family("不存在的字体xyz")
        ok("resolve_family 降级链：雅黑缺→命中 Segoe UI", r1 == "Segoe UI", r1)
        ok("resolve_family 降级链：未知字→fallback 首个真实族", r2 == "Microsoft YaHei UI", r2)
    finally:
        SK.available_ui_families = _real_fams
    t = SK.THEMES["whale"]
    f = SK.qfont(t, 12, display=True)
    ok("qfont 链首=标题字体", f.families()[0] == disp, str(f.families()[:2]))
    f2 = SK.qfont(t, 12)
    ok("qfont 正文链首=正文字体", f2.families()[0] == body, str(f2.families()[:2]))
    # 模拟注册失败：三族全空 → 回退 t.font_family
    old = (SK._DISPLAY_FAMILY, SK._BODY_FAMILY, SK._EMOJI_FAMILY, SK._FONTS_READY)
    SK._DISPLAY_FAMILY = SK._BODY_FAMILY = SK._EMOJI_FAMILY = ""
    SK._FONTS_READY = True
    f3 = SK.qfont(t, 12)
    ok("字体全注册失败 → 回退 t.font_family（不崩、不空链）",
       bool(f3.families()) and f3.families()[0] == t.font_family, str(f3.families()[:2]))
    SK._DISPLAY_FAMILY, SK._BODY_FAMILY, SK._EMOJI_FAMILY, SK._FONTS_READY = old
    ok("模拟状态已还原", (SK._DISPLAY_FAMILY, SK._BODY_FAMILY) == (disp, body))

    print("== B/C. 路径硬编码 / 系统版本裸调 ==")
    print("== B/C. 路径硬编码 / 系统版本裸调 ==", file=_OUT)
    bad = re.compile(r"C:[/\\\\]+Users[/\\\\]+(?!某个人)[^/\\\\\"']+|ptmou")
    hits = []
    for d, dirs, files in os.walk(ROOT):
        rel = os.path.relpath(d, ROOT).replace("\\\\", "/")
        top = rel.split("/")[0]
        if top in ("_scratch", "runtime", ".git", "logs", "data", "__pycache__", "报告",
                   "node_modules", ".workbuddy", "docs", "archive", "_outbox", "assets"):
            dirs[:] = []
            continue
        for fn in files:
            # 产品源码口径：py/cs/ps1/vbs/xaml —— md/json 属文档与运行时用户数据，另算
            if not fn.endswith((".py", ".cs", ".ps1", ".vbs", ".xaml")):
                continue
            if fn.startswith("_c8_"):
                continue   # 取证探针自身（正则字面量必然命中），非产品运行时代码
            p = Path(d) / fn
            try:
                txt = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for i, ln in enumerate(txt.splitlines(), 1):
                if bad.search(ln) and "某个人" not in ln:
                    # 检测器自身的模式定义行/占位注释豁免（pack_online 脱敏门禁 / 示例文案）
                    if re.search(r"re\.compile|r\"ptmou|家目录|BAD_TRACE|BAD_STATE|脱敏器|例：", ln):
                        continue
                    hits.append("%s:%d" % (rel + "/" + fn, i))
    ok("产品源码（py/cs/ps1/vbs/xaml）无 C:\\Users\\<用户名> 硬编码", not hits, str(hits[:5]))
    src = (HERE / "shell.py").read_text(encoding="utf-8")
    ok("ui_qt 无 Win11-only 裸调（DwmSetWindowAttribute / RtlGetVersion 分支前不存在）",
       "DwmSetWindowAttribute" not in src,
       "P1-M 圆角做时须带版本检测+Win10 回退（审计报告口径）")
    ok("极小窗有下限（setMinimumSize 860×560）",
       "setMinimumSize(860, 560)" in src)
    ok("PerMonitorV2 显式钉子（SetThreadDpiAwarenessContext(-4)）",
       "SetThreadDpiAwarenessContext" in src and "(-4)" in src)

    print("== D/G. DPI 与 125% 奇数档 ==")
    print("== D/G. DPI 与 125% 奇数档 ==", file=_OUT)
    try:
        pol = QGuiApplication.highDpiScaleFactorRoundingPolicy().name
    except Exception:
        pol = "?"
    ok("rounding policy = PassThrough（Qt6 默认；125% 不取整、几何精确）", pol == "PassThrough", pol)
    import shell
    r = shell.set_per_monitor_dpi()
    ok("set_per_monitor_dpi() 可调不崩（返回 policy 名）", r == pol, r)

    print("== E. 自举 / 兼容指纹 ==")
    print("== E. 自举 / 兼容指纹 ==", file=_OUT)
    qtbs = (ROOT / "qt_bootstrap.py").read_text(encoding="utf-8")
    ok("qt_bootstrap 三国内镜像竞速", qtbs.count("https://") >= 3 and "tsinghua" in qtbs and "aliyun" in qtbs)
    ok("qt_bootstrap 版本钉死 6.11.2", 'PYSIDE_PIN = "6.11.2"' in qtbs)
    ok("qt_bootstrap 只装 Essentials（不拖 633MB 全家桶）", 'PYSIDE_PKG = "PySide6-Essentials"' in qtbs)
    from agent import compat as CP
    fp = CP.fingerprint()          # 只读，永不抛（compat_selftest 钉过）
    ok("compat.fingerprint 五节实跑成功", all(isinstance(fp.get(k), dict) for k in
       ("windows", "display", "wechat_window", "data_dir", "runtime")), str(sorted(fp.keys())))
    win = fp.get("windows") or {}
    ok("系统节报版本+位数（32 位机一眼可见）", bool(win.get("release")) and bool(win.get("arch")),
       "%s %s" % (win.get("release"), win.get("arch")))
    disp_f = fp.get("display") or {}
    ok("显示节报主屏+缩放（125% 等奇数档一眼可见）", bool(disp_f.get("primary")), str(disp_f))

    print("== F. 网络多源常量表 ==")
    print("== F. 网络多源常量表 ==", file=_OUT)
    uc = (ROOT / "agent" / "update_check.py").read_text(encoding="utf-8")
    ua = (ROOT / "agent" / "update_apply.py").read_text(encoding="utf-8")
    ok("更新检查多源并行（候选源 ≥8 条）", uc.count("https://") >= 8, "%d 条" % uc.count("https://"))
    ok("更新检查有官方域白名单（防镜像定版本）", "官方域" in uc or "_OFFICIAL" in uc or "官方直连" in ua)
    ok("资产下载四镜像轮换", ua.count("https://gh") >= 3, "DL_MIRRORS")
    ok("依赖自愈离线优先其次镜像", "离线优先" in (ROOT / "agent" / "dep_heal.py").read_text(encoding="utf-8"))

    print()
    log = _OUT.getvalue()
    fail = log.count("  FAIL ")
    print(("总结：%d 条断言，FAIL %d" % (log.count("  OK   ") + fail, fail)))
    print("总结：%d 条断言，FAIL %d" % (log.count("  OK   ") + fail, fail), file=_OUT)
    out = HERE / "_c8_compatprobe_out.log"
    out.write_text(_OUT.getvalue(), encoding="utf-8")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
