# -*- coding: utf-8 -*-
"""**切会话 / 发文件的自检探针**（跨机测试用；只读默认，不动鼠标、不抢前台）。

为什么有这个脚本（2026-09-16 跨机 r11 报告）：上一轮要求对面跑的 `_scratch\\send_to_e.py` **不在包里**
（`_scratch` 整个目录都不进包）⇒ 对面没法按原样复现。这个脚本就是补上的**随包入口**：它只依赖 `agent/`
与 `scripts/`，任何一台机器解包后都能直接跑。

三种模式（默认＝只读，什么都不点）：
  · 只读（默认）：把"当前会话是不是目标"的**每一档证据**逐条打出来（身份闸口径）
  · `--switch`  ：投递切一次会话（先会话行、失败退搜索浮层），**失败自动留现场**
  · `--send <文件>`：切完再投递发这个文件（**只发到 `--chat-id` 指定的会话**，只认 DB 回读）

用法：
  runtime\\python\\python.exe scripts\\switch_chat_probe.py --name E --chat-id wxid_xxx
  runtime\\python\\python.exe scripts\\switch_chat_probe.py --name E --chat-id wxid_xxx --switch
  runtime\\python\\python.exe scripts\\switch_chat_probe.py --name E --chat-id wxid_xxx --send 报告\\x.zip

产出：失败现场落 `wechatauto_logs\\fail\\<时间戳>_<tag>\\{shot.png,probe.json}`（照 AGENTS.md §2.2，
别人拿到目录不看日志就能判断"是没找到还是找错了"）。
"""
import argparse
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from agent import version_gate as vg                     # noqa: E402
from agent.config import get_config                      # noqa: E402
from agent.wechat import WeChatAdapter                   # noqa: E402
from agent import chat_ocr as co                         # noqa: E402
from agent import chat_header as ch                      # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="切会话/发文件自检探针（默认只读）")
    ap.add_argument("--name", required=True, help="目标会话显示名（如 E / 文件传输助手）")
    ap.add_argument("--chat-id", required=True, help="目标会话 chat_id")
    ap.add_argument("--switch", action="store_true", help="真切一次（投递档，一次最多一枪）")
    ap.add_argument("--send", default="", help="切完再发这个文件（只发到 --chat-id）")
    ap.add_argument("--timeout", type=float, default=90.0, help="发文件的 DB 回读等待秒数")
    a = ap.parse_args()

    vg.allow_session("跨机自检探针：切会话/发文件（%s）" % a.name)
    ad = WeChatAdapter(get_config())
    gui = ad._get_gui()
    try:
        gui._update_render_rect()
    except Exception:
        pass
    r = getattr(gui, "render_rect", None) or (0, 0, 0, 0)
    print("① 窗口：main=%s 渲染区=%s %sx%s"
          % (getattr(gui, "main_hwnd", None), r, int(r[2] - r[0]), int(r[3] - r[1])))
    _bk = "?"
    try:
        from agent import input_backend as _ib
        _bk = _ib.select_backend(gui=gui).name
    except Exception:
        pass
    print("   目标：%s（%s）· display_name=%r · 输入后端=%s"
          % (a.name, a.chat_id, ad.display_name(a.chat_id), _bk))

    print("② 身份闸逐档（只读）")
    try:
        im = ch.capture_image(gui=gui)
        print("   绿底高亮行 = %s" % (co.highlight(im),))
        print("   高亮行时间 = %s" % (co.highlight_time(im),))
        print("   current_chat_name = %s" % (co.current_chat_name(im),))
        print("   会话行 OCR：")
        for row in co.session_rows(im):
            print("      y=%3s green=%.2f name=%-18r full=%r"
                  % (row["y_abs"], co.green_row_ratio(im, row["y_abs"]),
                     str(row.get("name"))[:18], str(row.get("full"))[:34]))
    except Exception as e:
        print("   读屏失败：%s" % e)
    print("   _last_time_hhmm = %r" % ad._last_time_hhmm(a.chat_id))
    print("   _active_row_time_ok = %s" % (ad._active_row_time_ok(a.chat_id, gui=gui),))
    print("   chat_is_open = %s" % (ad.chat_is_open(a.chat_id, gui=gui, name=a.name),))
    try:
        print("   chat_identity_ok = %s" % (ad.chat_identity_ok(a.chat_id, gui=gui),))
    except Exception as e:
        print("   chat_identity_ok 异常：%s" % e)

    if not (a.switch or a.send):
        print("③ 只读模式：没有 --switch / --send ⇒ 一枪都没开。")
        print("   要真切：加 --switch；要真发：加 --send <文件>（只发到上面的 chat_id）。")
        return 0

    print("③ 切会话（投递档；一次最多一枪）")
    ok1, why1 = ad.switch_chat_posted(a.chat_id, name=a.name)
    print("   switch_chat_posted -> %s ｜ %s" % (ok1, str(why1)[:300]))
    if not ok1:
        ok2, why2 = ad.open_chat_by_search(a.chat_id, name=a.name)
        print("   open_chat_by_search -> %s ｜ %s" % (ok2, str(why2)[:300]))
        if not ok2:
            print("   ⛔ 两条路都没切过去 ⇒ 不发（防误发）。失败现场见 wechatauto_logs\\fail\\")
            return 2
    print("   切完复核：%s" % (ad.chat_is_open(a.chat_id, gui=gui, name=a.name),))

    if not a.send:
        return 0
    if not os.path.isfile(a.send):
        print("⛔ 文件不存在：%s" % a.send)
        return 2
    print("④ 发文件（只认 DB 回读）")
    ok, msg = ad.send_file_posted(a.chat_id, os.path.abspath(a.send),
                                  wait_s=float(a.timeout), allow_repeat=True, confirm_open=False)
    print("   %s -> %s ｜ %s" % (os.path.basename(a.send), ok, str(msg)[:300]))
    return 0 if ok == "ok" else 2


if __name__ == "__main__":
    sys.exit(main())
