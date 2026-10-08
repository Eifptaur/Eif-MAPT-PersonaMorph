# -*- coding: utf-8 -*-
"""collect_size_samples.py —— 多尺寸样本采集器（喂方向A「归一化剖面」与方向B「标题OCR」的 POC）

⛔ 为什么要它（两份文档共同要求的数据）
  · 我方《判据根治-调研任务书》方向 A 的「所需最小数据」：
    同一会话 ≥3 组不同窗口尺寸的整幅截图（至少 1 组深色主题），附 DPI/主题/版本元数据。
  · 调研报告《判据根治-深度调研报告-20260930》第 1 章同样要求"同一会话 ≥3 种窗口尺寸截图"，
    第 2 章要求"深色/浅色各 ≥5 张会话头截图（标注真值会话名与 DPI）"。
  · 而我实测发现：**语料里那些整屏截图不能用**（坐标系是"整个屏幕"，而判据跑在**微信窗口渲染区**上
    ⇒ 相对定位对不上）。⇒ 必须专门采集"渲染区"级别的样本。

★它做什么
  每 `--every` 秒抓一帧**微信窗口渲染区**，并落盘：
    frame-XXX.png        渲染区原图
    samples.jsonl        每帧一行的元数据（见下）
  元数据字段（一次采集把 A/B 两个方向需要的都带上）：
    t / w / h            时间、渲染区物理像素尺寸
    dpi                  GetDpiForWindow（判断是否 125%/150% 缩放）
    theme                detect_polarity 的输出（light/dark）
    wxver                微信 exe 文件版本（版本分档用）
    pane_left            pane_left_for(img)
    title_box / title    ⭐ 标题带的**相对定位** + 它的 OCR 读数（B 方向：检验"标题能否读出会话名"）
    hl_name              highlight() 的绿底行名字（对照用）

★怎么用（关键：**边采集边拖窗口大小**）
  # 终端 1（微信窗口保持可见、不最小化）
  ./runtime/python/python.exe -u scripts/collect_size_samples.py --every 2 --n 60 --tag sizes
  # 期间你把微信窗口拖成 3~5 种不同大小、并在目标会话与别的会话之间切几次
  ⇒ 事后就能量：同会话跨尺寸的归一化剖面相似度、以及标题 OCR 在各尺寸下的可读率

  ⚠️ 本脚本**只读**：不点鼠标、不切会话、不发送、不动窗口。
  ⚠️ 想采"深色/浅色两套"，就采两轮（中间自己切一次微信主题，脚本不做也不改你的设置）。
"""
import argparse
import ctypes
import io
import json
import os
import sys
import time
import logging

log = logging.getLogger("persona-morph")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_HR = chr(0x2500)


def _wx_version():
    """微信 exe 的文件版本（版本分档要用它）。取不到给空串。"""
    try:
        import win32api # type: ignore
        # ⭐ **三来源**：原来只扫两个硬编码目录 ⇒ 用户装在别处就**取不到版本号**。
        #    ① 注册表（微信自己的项里带 InstallPath）② 常见默认路径 ③ **从正在运行的进程拿 exe 路径**。
        _cands = []
        try:
            import winreg as _wr
            for _root, _key in ((_wr.HKEY_CURRENT_USER, r"Software\Tencent\Weixin"),
                               (_wr.HKEY_LOCAL_MACHINE, r"Software\Tencent\Weixin"),
                               (_wr.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Tencent\Weixin")):
                try:
                    with _wr.OpenKey(_root, _key) as _k:
                        _v, _ = _wr.QueryValueEx(_k, "InstallPath")
                        if _v:
                            _cands.append(str(_v))
                except Exception:
                    continue
        except Exception:
            pass
        _cands += [r"M:\WX\Weixin", r"C:\Program Files\Tencent\Weixin",
                   os.path.join(os.environ.get("ProgramFiles(x86)", ""), r"Tencent\Weixin")]
        try:
            import psutil
            for _p in psutil.process_iter(["name", "exe"]):
                _nm = (_p.info.get("name") or "").lower()
                if _nm.startswith(("weixin", "wechat")):
                    _e = _p.info.get("exe")
                    if _e:
                        _cands.append(os.path.dirname(_e))
        except Exception as _e:
            log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
            pass
        for p in _cands:
            if not p or not os.path.isdir(p):
                continue
            for d in sorted(os.listdir(p), reverse=True):
                exe = os.path.join(p, d, "Weixin.exe")
                if os.path.isfile(exe):
                    info = win32api.GetFileVersionInfo(exe, "\\")
                    ms, ls = info["FileVersionMS"], info["FileVersionLS"]
                    return "%d.%d.%d.%d" % (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
        # ⛔ 取不到时**说清试过哪些路径**（不然"版本号空着"根本没法排障 —— 这条是讲人话的底线）
        print("  [版本号] 三个来源都没命中，试过：%s"
              % "；".join((p or "(空)") for p in _cands[:6]))
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        pass
    return ""


def _dpi(hwnd):
    try:
        u = ctypes.windll.user32
        try:
            return int(u.GetDpiForWindow(int(hwnd)))
        except Exception:
            return 0
    except Exception:
        return 0


def main():
    ap = argparse.ArgumentParser(description="多尺寸样本采集（只读）")
    ap.add_argument("--out", default="", help="输出目录（默认 报告/ocr瓶颈-<今天>/多尺寸样本/<tag>-<时间戳>/）")
    ap.add_argument("--every", type=float, default=2.0)
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--tag", default="sizes")
    a = ap.parse_args()

    from agent.wechat import WeChatAdapter
    from agent import chat_ocr as CO
    from agent import chat_header as CH

    _stamp = time.strftime("%Y%m%d-%H%M%S")
    _out = a.out or os.path.join(_ROOT, "报告", "ocr瓶颈-%s" % time.strftime("%Y%m%d"),
                                 "多尺寸样本", "%s-%s" % (a.tag, _stamp))
    os.makedirs(_out, exist_ok=True)
    _ver = _wx_version()

    print(_HR * 74)
    print("多尺寸样本采集（只读）→ %s" % _out)
    print("  微信版本 = %r" % (_ver or "<取不到>"))
    print("  ⚠️ 采集期间请**把微信窗口拖成 3~5 种不同大小**，并在目标会话间切换几次")
    print("  ⚠️ 别最小化（抓不到帧）；本脚本不动鼠标、不切会话、不发送")
    print(_HR * 74)

    try:
        _ad = WeChatAdapter()
        _gui = _ad._get_gui()
    except Exception as _e:
        print("⛔ 拿不到微信窗口：%s: %s" % (type(_e).__name__, str(_e)[:120]))
        return 2
    _main = int(getattr(_gui, "main_hwnd", 0) or 0)
    _rr = getattr(_gui, "render_rect", None)
    try:
        if int(_rr[0]) < -10000:
            print("  ⛔ 窗口是最小化/屏幕外（render_rect=%s）⇒ 先点开微信窗口再跑" % (_rr,))
            return 2
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        pass
    print("  ✓ GUI 就绪（main_hwnd=%s, dpi=%d）" % (_main, _dpi(_main)))
    print("  微信版本 = %s" % (_ver or "?"))

    _jl = os.path.join(_out, "samples.jsonl")
    _n_ok, _seen_sizes, _seen_ocr = 0, set(), 0
    with io.open(_jl, "w", encoding="utf-8") as _f:
        for _i in range(max(1, int(a.n))):
            _t0 = time.time()
            _rec = {"i": _i, "t": time.strftime("%Y-%m-%d %H:%M:%S"), "wxver": _ver}
            _img = None
            try:
                _img = CO.capture_best(gui=_gui, frames=1)
            except Exception as _e:
                _rec["err"] = "%s: %s" % (type(_e).__name__, str(_e)[:80])
            if _img is None:
                print("  [%02d] 抓帧失败（窗口最小化/被遮挡？）" % _i)
            else:
                _w, _h = _img.size
                _rec.update({"w": _w, "h": _h, "dpi": _dpi(_main)})
                try:
                    _rec["theme"] = CH.detect_polarity(_img)
                except Exception as _e:
                    log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
                    _rec["theme"] = ""
                _pl = 0
                try:
                    _pl = int(CH.pane_left_for(_img) or 0)
                except Exception:
                    _pl = 0
                if not _pl:
                    _pl = int(_w * CH.PANE_LEFT_REL)
                _rec["pane_left"] = _pl
                # ⭐ 标题带：**相对定位**（面板左沿 -12px 起、宽按图高比例、y 按图高比例）
                _tb = (max(0, _pl - 12), int(_h * 0.045),
                       min(_w, _pl + int(_h * 0.42)), int(_h * 0.098))
                _rec["title_box"] = list(_tb)
                _ttl = ""
                try:
                    if _tb[2] - _tb[0] >= 30 and _tb[3] - _tb[1] >= 12:
                        _c = _img.crop(_tb)
                        _c = _c.resize((_c.width * 2, _c.height * 2))
                        _ttl = "".join(str(x[0]) for x in CO.recognize_dual(_c)).strip()
                except Exception as _e:
                    log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
                    _ttl = ""
                _rec["title"] = _ttl[:40]
                if _ttl:
                    _seen_ocr += 1
                try:
                    _hl = CO.highlight(_img)
                    _rec["hl_name"] = str((_hl or {}).get("name") or "")[:32]
                except Exception as _e:
                    log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
                    _rec["hl_name"] = ""
                try:
                    _img.save(os.path.join(_out, "frame-%03d.png" % _i))
                    _rec["png"] = "frame-%03d.png" % _i
                    _n_ok += 1
                    _seen_sizes.add("%dx%d" % (_w, _h))
                except Exception as _e:
                    _rec["save_err"] = str(_e)[:60]
                print("  [%02d] %-11s dpi=%-4s %-5s 标题=%-18r 绿底名=%-14r"
                      % (_i, "%dx%d" % (_w, _h), _rec.get("dpi", ""), _rec.get("theme", ""),
                         _ttl[:18], _rec.get("hl_name", "")[:14]))
            _f.write(json.dumps(_rec, ensure_ascii=False) + "\n")
            _f.flush()
            if _i < int(a.n) - 1:
                _sp = a.every - (time.time() - _t0)
                if _sp > 0:
                    time.sleep(_sp)

    print(_HR * 74)
    print("== 汇总：落盘 %d 帧 · 出现过的尺寸 %d 种 %s · 标题 OCR 非空 %d 帧 =="
          % (_n_ok, len(_seen_sizes), sorted(_seen_sizes), _seen_ocr))
    print("   目录：%s" % _out)
    if len(_seen_sizes) < 2:
        print("   ⚠️ 只有 %d 种尺寸 ⇒ **喂不了方向A的 POC**（需要 ≥3 种）⇒ 请重采并多拖几次窗口"
              % len(_seen_sizes))
    if _seen_ocr == 0:
        print("   ⚠️ 标题 OCR 全空 ⇒ 先别下结论：可能是标题带定位仍不对（见报告 §24 的实测）"
              "⇒ 把当时的 frame-*.png 发我看")
    print(_HR * 74)
    return 0 if _n_ok else 1


if __name__ == "__main__":
    sys.exit(main())
