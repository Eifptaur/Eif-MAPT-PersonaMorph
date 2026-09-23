# -*- coding: utf-8 -*-
"""Qt 正式壳入口 —— 主进程内的子线程拉起（persona_morph.py 专用）。

=====================================================================
为什么是子线程（而不是主线程跑 Qt）
=====================================================================
persona_morph.py 的主线程要跑机器人主循环（监听/发送/水位/计时），
不能让给 Qt 事件循环 ⇒ Qt 壳整体（QApplication + Shell + exec）放进
**专用子线程**。Qt 的约束是「所有 GUI 对象都在创建 QApplication 的那个
线程里使用」——本模块保证 Shell 及其全部子控件、QTimer 都在 qt-shell
线程里创建与操作；机器人主循环继续在 Python 主线程，互不抢消息泵。

探活接线（照抄原型口径，不改语义）：
  Shell 内置的 _probe_timer（4 秒一跳）→ agent_bridge.current_url()
  （权威顺序：agent/notify_ui.console_url() → logs/console.url → 配置端口）
  → heal.probe_backend()（绕代理、六态判别）。addr/agent_bridge 落位时
  只改了目录层级（parents[2]→parents[1]），口径一行未动。

线程纪律：
  · 本线程 daemon=True —— 进程退出时随进程收尾（网页控制台同期死亡，对等）；
  · 线程内任何异常都写日志、绝不弹窗（0 系统 MessageBox 纪律）、
    绝不往主线程抛（Qt 壳崩了不能拖死机器人本体）。
"""

from __future__ import annotations

import os
import sys
import threading
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent


def start_qt_shell(log=None, theme: str = "whale") -> bool:
    """在子线程里拉起 Qt 壳。返回是否成功**派发**（线程起没起来，不含 Qt 内部结果）。

    调用方（persona_morph.py）在 PySide6 自举通过之后调用本函数；
    Qt 壳的后续生死都走日志，不影响机器人主循环。
    """
    def _log(level: str, msg: str) -> None:
        if log is not None:
            try:
                getattr(log, level)("Qt 控制台：%s", msg)
            except Exception:  # noqa: BLE001
                pass

    def _run() -> None:
        try:
            if HERE not in [Path(p) for p in sys.path]:
                sys.path.insert(0, str(HERE))
            # 离屏兜底仅在取证环境需要；真桌面跑 windows 平台（不设 = Qt 自选）
            from PySide6.QtWidgets import QApplication  # noqa: PLC0415

            from shell import Shell, set_per_monitor_dpi  # noqa: PLC0417
            from stylekit_qt import THEMES, apply_font_to_app, ensure_fonts, resolve_family  # noqa: PLC0415

            set_per_monitor_dpi()
            # 空参数表：Qt 不该解析机器人自己的命令行（--foreground 等）
            app = QApplication.instance() or QApplication([])
            app.setApplicationName("Persona Morph 控制台")
            # 丙-5 #6：任务栏/窗口图标用透明底完整鲸鱼（真机问题⑨）
            _icon_path = HERE.parent / "assets" / "icon-whale.png"
            if _icon_path.exists():
                from PySide6.QtGui import QIcon  # noqa: PLC0415

                app.setWindowIcon(QIcon(str(_icon_path)))

            t = THEMES.get(theme) or THEMES["whale"]
            # 字体：双字体注册（朝華標題A 标题 / 屏显臻宋 正文）+ 需要时降级 UI 字体
            ensure_fonts()
            fam = resolve_family(t.font_family)
            if fam != t.font_family:
                for tk in THEMES.values():
                    tk.font_family = fam
                _log("info", f"本机没有 {t.font_family}，界面字体降级用 {fam}")
            apply_font_to_app(app, t)

            w = Shell(t)
            w.setWindowTitle("群相 控制台")
            w.show()
            _log("info", "原生界面已打开（与网页控制台并存）")
            app.exec()
            _log("info", "原生界面已退出")
        except Exception:  # noqa: BLE001
            _log("warning", "界面线程异常退出：\n" + traceback.format_exc(limit=6))

    th = threading.Thread(target=_run, daemon=True, name="qt-shell")
    th.start()
    return th.is_alive()


if __name__ == "__main__":
    # 单独调试：python ui_qt/app.py  （脱离机器人，直接起界面看效果）
    import logging

    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    os.environ.setdefault("QT_QPA_PLATFORM", "windows")
    start_qt_shell(log=logging.getLogger("ui_qt"))
    input("按回车退出…")
