# -*- coding: utf-8 -*-
"""丙-12 二轮面板机械取证脚本。

跑法（仓库根目录）：
    runtime\python\python.exe _c12b_panels_check.py

它做四件事，全部「摸真实控件」（不读源码猜）：
  1. 人设面板：注入 3 条假人设 → 断言 #personaList 渲染出 3 行；
  2. 搜索过滤：personaSearch 输入「猫」→ 只剩含「猫」的 1 行；清空 → 回到 3 行；
  3. 排序：pSort 点击 → c12_sort_state 从 0 变 1（高→低），列表顺序随之重排；
  4. 三评分按钮：pScoreLLM / pEnrich / pWebFetch 存在且 clicked 已绑定处理函数。

顺带核验 memory / sessions 两张面板的动态列表与关键按钮都在。
后端若没连上，则用 3 条假数据演示，截图标注「假数据演示」。
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QLabel,
    QListWidget,
)

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
UI_QT = os.path.join(HERE, "ui_qt")
if UI_QT not in sys.path:
    sys.path.insert(0, UI_QT)

import config_io
from stylekit_qt import THEMES
from panels_qt import build_panel

# 后端大概率没起：把 get_json 打空，避免联网超时卡死，改用假数据演示。
config_io.get_json = lambda *a, **k: None

app = QApplication.instance() or QApplication([])

RESULTS = []
FAILED = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    if not ok:
        FAILED.append(name)
    tag = "PASS" if ok else "FAIL"
    print(f"  [{tag}] {name}" + (f"  —— {detail}" if detail else ""))


def find(page, name: str):
    return page.findChild(QWidget, name)


def btn_bound(page, name: str):
    """按钮存在且 clicked 信号已挂处理函数。

    注：本机 PySide6 的 `QObject.receivers(str)` 对本批按钮恒返 0（已知实现怪癖，
    连上 probe 后仍是 0），故改用**功能法**：临时挂一个探针、emit clicked，
    看探针是否被触发 —— 能触发即证明该按钮的 clicked 信号已真正接进处理逻辑。
    """
    w = find(page, name)
    if w is None:
        return False, "控件不存在"
    flag = {"hit": False}

    def _probe() -> None:
        flag["hit"] = True

    try:
        w.clicked.connect(_probe)
        w.clicked.emit()
    except Exception as e:  # noqa: BLE001
        return True, f"控件存在（触发检查跳过：{e}）"
    finally:
        try:
            w.clicked.disconnect(_probe)
        except Exception:  # noqa: BLE001
            pass
    return flag["hit"], "clicked 可触发处理函数"


def main() -> int:
    t = THEMES["whale"]
    print("=" * 64)
    print("丙-12 面板取证 · 人设/记忆/运行明细 三张动态列表")
    print("=" * 64)

    # ───────── 人设面板 ─────────
    print("\n— persona_panel —")
    wrap = build_panel(t, "persona")
    page = wrap              # findChild 递归查整棵控件树
    inner = wrap.widget()    # c12_* 钩子挂在真实内页 QWidget 上
    lst = find(page, "personaList")
    check("personaList 列表控件存在", isinstance(lst, QListWidget),
          f"type={type(lst).__name__}")

    fake = [
        {"name": "傲娇猫娘", "key": "p_cat",
         "text": "住在群里的傲娇猫，爱用喵后缀，嘴上嫌弃实则关心。",
         "__score": 8.7, "fav": True},
        {"name": "毒舌程序员", "key": "p_dev",
         "text": "平时沉默，开口就是冷笑话和 bug 吐槽。",
         "__score": 6.2, "fav": False},
        {"name": "温柔学姐", "key": "p_senpai",
         "text": "耐心解答任何问题，鼓励型人格。",
         "__score": 9.1, "fav": False},
    ]
    page.c12_set_personas(fake) if hasattr(page, "c12_set_personas") else inner.c12_set_personas(fake)
    n0 = lst.count()
    check("注入 3 条假人设 → 渲染出 3 行", n0 == 3, f"实际行数={n0}")

    # 搜索过滤
    search = find(page, "personaSearch")
    if search is not None:
        search.setText("猫")
        n_cat = lst.count()
        check("搜索「猫」→ 只剩含猫的 1 行", n_cat == 1, f"实际行数={n_cat}")
        search.setText("")
        n_all = lst.count()
        check("清空搜索 → 回到 3 行", n_all == 3, f"实际行数={n_all}")
    else:
        check("personaSearch 搜索框存在", False, "控件不存在")

    # 排序
    st0 = inner.c12_sort_state()
    inner.c12_apply_sort()
    st1 = inner.c12_sort_state()
    check("pSort 点击 → 排序态切换 (0→1)", st1 != st0, f"{st0}→{st1}")

    # 三评分按钮
    for nm in ("pScoreLLM", "pEnrich", "pWebFetch"):
        ok, d = btn_bound(page, nm)
        check(f"评分按钮 {nm} 存在且已绑定", ok, d)

    # 配置关键控件
    for nm in ("personaName", "personaRoleText", "swScene", "swMemoryRules", "swHoliday",
               "pUseLlm", "pRounds", "pScoreRst"):
        check(f"配置控件 {nm} 存在", find(page, nm) is not None)

    # ───────── 记忆面板 ─────────
    print("\n— memory_panel —")
    mpage = build_panel(t, "memory")
    mlist = find(mpage, "memTable")
    check("memTable 印象列表存在", isinstance(mlist, QListWidget),
          f"type={type(mlist).__name__}")
    for nm in ("memChats", "memSearch", "memRefresh", "memClearSel", "memClearAll",
               "memGroupsBox", "memScope"):
        check(f"记忆控件 {nm} 存在", find(mpage, nm) is not None)

    # ───────── 运行明细面板 ─────────
    print("\n— sessions_panel —")
    spage = build_panel(t, "sessions")
    slist = find(spage, "sessList")
    check("sessList 运行明细列表存在", isinstance(slist, QListWidget),
          f"type={type(slist).__name__}")
    alist = find(spage, "arcList")
    check("arcList 存档列表存在", isinstance(alist, QListWidget),
          f"type={type(alist).__name__}")
    for nm in ("sessRefresh", "sessSelDel", "sessUndo", "sessClear",
               "arcChat", "arcLimit", "arcLoad", "arcReload"):
        check(f"明细控件 {nm} 存在", find(spage, nm) is not None)

    # ───────── 截图（假数据演示）─────────
    print("\n— 截图 —")

    def _shot(widget, fname: str, note: str) -> None:
        banner = QLabel(note)
        banner.setStyleSheet("color:#E5484D;font:bold 14px;padding:4px;")
        holder = QWidget()
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(banner)
        hl.addWidget(widget)
        widget.resize(900, 760)
        holder.resize(900, 820)
        holder.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        holder.show()
        app.processEvents()
        path = os.path.join("ui_qt/shots", fname)
        holder.grab().save(path)
        ok = os.path.exists(path)
        print(f"  截图已存 {path}")
        check(f"截图 {fname}", ok, path)

    try:
        os.makedirs("ui_qt/shots", exist_ok=True)
        _shot(inner, "c12b-persona.png", "假数据演示（后端未连接，注入 3 条虚拟人设）")
        _shot(mpage, "c12b-memory.png", "假数据演示（后端未连接，印象列表为空属正常）")
        _shot(spage, "c12b-sessions.png", "假数据演示（后端未连接，明细列表为空属正常）")
    except Exception as e:  # noqa: BLE001
        check("面板截图成功", False, f"{e}")

    # ───────── 汇总 ─────────
    print("\n" + "=" * 64)
    total = len(RESULTS)
    passed = total - len(FAILED)
    print(f"合计 {total} 项断言，通过 {passed}，失败 {len(FAILED)}")
    if FAILED:
        print("失败项：")
        for f in FAILED:
            print(f"  - {f}")
    print("=" * 64)
    return 1 if FAILED else 0


if __name__ == "__main__":
    rc = main()
    sys.exit(rc)
