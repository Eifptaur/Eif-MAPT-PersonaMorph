# -*- coding: utf-8 -*-
"""一键安装界面组件（PySide6-Essentials）：运行 py -3 -X utf8 scripts\setup_ui.py
（一键启动在依赖阶段自动调用本脚本）。

为什么要单独一个脚本（而不是在 onestart 进程里直接调 qt_bootstrap.ensure_pyside6）：
  · ensure_pyside6 的下载/安装进度经日志回调往 stdout 打文本；
  · 一键启动的 GUI 模式由安装器窗口按 @@ 事件行驱动，普通文本必须由父进程决定
    怎么显示 —— 放子进程里跑，父进程用 run_stream 收输出、按行数进度
    （与 scripts\setup_deps.py 同一条路）；子进程崩溃也不至于把启动流程一起带走。

行为：
  · 已装且版本一致 → 打印"已就绪，跳过安装"，exit 0（幂等快路径，不碰网络）；
  · 没装/版本不符 → 走 qt_bootstrap.ensure_pyside6()（国内镜像并行竞速 +
    分块下载 + 本地安装，详见 qt_bootstrap.py 模块头），进度实时打印；
  · 失败不致命：exit 1 并打印人话原因。真正要用界面时 qt_bootstrap 还会再试，
    仍失败则产品照旧回落网页控制台。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# 与 setup_deps.py 同口径：父进程用 -X utf8 起，这里再钉一次，输出永不乱码
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)


class _StdoutLog:
    """把 qt_bootstrap 的日志回调转成 stdout 文本行（显示与进度计数都由父进程做）。

    回调形态是 ``log.info(格式串, 参数...)``（qt_bootstrap 固定传「界面组件：%s」），
    这里先做 % 填充再整行输出；stdout 失效时退到 stderr 把这行说完
    （进度行丢了顶多界面少一格百分比，原因行丢了排障就没线索了）。
    """

    def _send(self, level, fmt, *args):
        try:
            msg = fmt % args if args else fmt
        except Exception:
            msg = fmt
        line = "[%s] %s" % (level, msg)
        try:
            print(line, flush=True)
        except OSError:
            sys.stderr.write(line + "\n")

    def info(self, fmt, *args):
        self._send("信息", fmt, *args)

    def warning(self, fmt, *args):
        self._send("提醒", fmt, *args)

    def error(self, fmt, *args):
        self._send("失败", fmt, *args)


def main():
    print("=" * 52)
    print(" Persona Morph 界面组件检查 / 安装")
    print("=" * 52)
    import qt_bootstrap
    ok, why = qt_bootstrap.ensure_pyside6(log=_StdoutLog())
    print("-" * 52)
    if ok:
        print("界面组件已就绪，跳过安装。")
        return 0
    print("界面组件这次没装上：%s" % why)
    print("不影响本次启动（先用网页控制台）；下次一键启动会再试一次。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
