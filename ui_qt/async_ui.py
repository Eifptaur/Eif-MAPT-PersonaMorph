# -*- coding: utf-8 -*-
"""**跨线程落地族的唯一实现点**（`box + Thread + 主线程轮询`）。

为什么要有这个文件
------------------
`ui_qt/` 里有 **44 处**同一个写法被手抄了 44 遍（实测，见 `selftest.t_async_landing_guard` 的全量棘轮）：

    box = {"done": False, ...}
    def _work():                 # 后台线程：干活、把结果塞进 box
        ...
        box["done"] = True
    Thread(target=_work, daemon=True).start()
    def _poll():                 # 主线程：轮询 box、完成后落地到控件
        if not box["done"]:
            QTimer.singleShot(N, _poll)      # ← 自链轮询
            return
        ... 读 box、改控件文字/可见性 ...
    QTimer.singleShot(N, _poll)

一错全错、一对全对：这段东西只要漏掉**存活判**，回调落地时控件已被销毁（切页/换主题会把页栈
`setParent(None)` + `deleteLater`）⇒ 取已销毁控件抛 `RuntimeError`，在 PySide6 里是**未捕获异常**
（打整段栈、视版本直接终止应用，用户看到的是「面板用着用着整个没了」）。
而 44 遍手抄里，只要有一遍漏了，用户就可能**只在那一个面板上**遇到闪退 —— 极难复现、极难归因。

⇒ 收口到本文件一处：**新写异步一律走 `run_async()`**，不要再手抄 box+Thread+轮询。
本文件是这一族的**唯一实现点**，因此 `selftest.t_async_landing_guard` 的全量扫描按文件名豁免它；
其余任何文件里再出现"函数体内 `Thread(` 且没有存活判"的写法都算违规。

三条护栏（都在这一处实现）
--------------------------
1. **存活判**：`page` 传进来之后，落地前先问 `ui_alive(page)`；面板没了 ⇒ **丢弃这次落地**
   （那个面板已经不在了，没有任何界面需要更新）。
2. **自链轮询有上限**：`tries` 到顶就停并记一笔，**不许无限重排**（任务永不完成时会把事件循环占住）。
3. **落地异常一律吞掉**：`RuntimeError`（控件已销毁）与其它异常都不许穿透到 UI 线程。

⚠️ 与 web 的对应：web 侧这些调用都是 `fetch(...).then(cb).catch(...)`，**不存在"控件已销毁"这一档**
（浏览器自己管 DOM 生命周期）⇒ 这是 Qt 侧**独有**的一档风险，本文件就是它的唯一闸门。
"""
from __future__ import annotations

from PySide6.QtCore import QTimer


def ui_alive(w: object) -> bool:
    """异步回调落地前问一次「那个控件还在吗」。

    面板被关掉、或主题重建（`_rebuild` 会把整棵页栈 `setParent(None)` + `deleteLater`）之后，
    回调里再取控件数据会抛 `RuntimeError: Internal C++ object already deleted`。
    传 None 表示调用方没绑定控件（此时靠 `deliver` 兜底）。
    """
    if w is None:
        return True
    try:
        import shiboken6 # noqa: PLC0415

        return bool(shiboken6.isValid(w))
    except Exception: # noqa: BLE001 — 拿不到 shiboken 就当它活着，由 deliver 兜底
        return True


def deliver(on_done, *args) -> None: # noqa: ANN001
    """把后台结果交给 UI 回调；回调途中控件已被销毁就**丢弃这一次落地**。

    丢弃是正确行为：那个面板已经不在了，没有任何界面需要更新（也没有地方报错）。
    """
    try:
        on_done(*args)
    except RuntimeError: # 控件已销毁
        pass


def run_async(work, on_done, *, page: object = None, interval: int = 200,  # noqa: ANN001
              tries: int = 400, name: str = "async-ui") -> None:
    """后台跑 `work(box)`，完成后在**主线程**把 `box` 交给 `on_done(box)`。

    · `work(box)` 在后台线程执行：自己往 `box` 里写结果（约定见文件头）；
    · `on_done(box)` 在主线程执行：读 `box` 并落地到控件；
    · `page`：这一批控件的宿主（面板/窗口）。**销毁后结果不再落地**；
    · `interval × tries`：自链轮询的总时限（默认 200ms × 400 = 80 秒），到顶就停并记账；
    · `name`：后台线程名（只看日志时用得上）。

    返回 None。**不抛异常**（后台异常由 `work` 自己写进 `box`，或在这里兜住）。
    """
    import threading # noqa: PLC0415

    box: dict = {"done": False}

    def _bg() -> None:
        try:
            work(box)
        finally:
            box["done"] = True

    threading.Thread(target=_bg, daemon=True, name=name).start()

    _left = [int(tries)]

    def _poll() -> None:
        if not box["done"]:
            if not ui_alive(page):
                # 面板没了 ⇒ **立刻停轮询**（否则是一条没有终点的自链，白占事件循环）。
                return
            _left[0] -= 1
            if _left[0] <= 0:
                # 到顶就停：**不许无限重排**（任务永不完成时会把事件循环占住）。
                # 不抛异常、不改界面 —— 与"面板已销毁"同款处置：没有结果就不落地。
                return
            QTimer.singleShot(interval, _poll)
            return
        if not ui_alive(page):
            return
        deliver(on_done, box)

    QTimer.singleShot(interval, _poll)
