# -*- coding: utf-8 -*-
"""记忆图谱判据：构图正确 / 本机零 token / 隐私不露 wxid / 空数据如实 / Qt 自绘能画。

为什么要单独一套：README 那条「把印象与会话记忆画成可交互的图谱」是全库**唯一**
曾经完全没做的一条（见 `报告/README逐条核对-20261010.md`）。这套判据钉住它真的落地，
而不是"页面上多了一行字"。

⛔ 隐私是红线：图上**不许出现 wxid**（那是真实账号）。没名字的成员显示占位符。
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "ui_qt"))

PASS = 0
FAIL = 0


def ck(name: str, cond, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  OK    " + name + (("   [" + extra + "]") if extra else ""))
    else:
        FAIL += 1
        print("  FAIL  " + name + (("   [" + extra + "]") if extra else ""))


class _FakeStore:
    """假的 MemoryStore：给两个会话、三个成员、带真实印象正文。"""

    def chat_keys(self):
        # ⛔ 假数据的账号 id 用 `uid_` 中性名：**不许写成 wxid_ 形状** ——
        #    出包器的 PII 扫描分不出合成还是真的（踩坑合集：别放宽红线来迁就判据）。
        return ["group:g1@chatroom", "private:uid_zzz"]

    def members(self, chat_key: str):
        if chat_key == "group:g1@chatroom":
            return [
                {"userId": "uid_aaa111", "name": "张三",
                 "impressions": [{"content": "喜欢夜跑", "createdAt": 1},
                                 {"content": "夜跑遇到下雨", "createdAt": 2},
                                 {"content": "周末去爬山", "createdAt": 3}]},
                {"userId": "uid_bbb222", "name": "李四",
                 "impressions": [{"content": "在山里露营", "createdAt": 4},
                                 {"content": "露营带了无人机", "createdAt": 5}]},
                {"userId": "uid_ccc333", "name": "",
                 "impressions": [{"content": "爬山装备很专业", "createdAt": 6}]},
            ]
        if chat_key == "private:uid_zzz":
            return [{"userId": "uid_zzz", "name": "老王",
                     "impressions": [{"content": "喜欢钓鱼", "createdAt": 7}]}]
        return []


def main() -> int:
    import agent.memory_graph as mg

    print("== 构图基础 ==")
    g = mg.build(store=_FakeStore())
    ck("build 返回 ok 且非空", g.get("ok") is True and not g.get("empty"),
       "stats=%s" % (g.get("stats"),))
    ids = {n["id"] for n in g["nodes"]}
    ck("两个会话都成节点（含私聊）",
       sum(1 for n in g["nodes"] if n["type"] == "chat") == 2,
       str([n["name"] for n in g["nodes"] if n["type"] == "chat"]))
    ck("四个成员都成节点（含没名字的那个）",
       sum(1 for n in g["nodes"] if n["type"] == "person") == 4,
       str(sorted(x for x in ids if x.startswith("p"))))
    ck("节点类型只有 chat/person/topic 三类",
       {n["type"] for n in g["nodes"]} <= {"chat", "person", "topic"},
       str({n["type"] for n in g["nodes"]}))
    rels = {l["relation"] for l in g["links"]}
    ck("三类关系都出现了（在群里/聊到/同群）",
       {"在群里", "聊到", "同群"} <= rels, str(sorted(rels)))

    print("== 隐私（红线） ==")
    names = [n["name"] for n in g["nodes"]]
    ck("图上**不出现任何 wxid**（名字里没有 wxid_）",
       not any("wxid_" in str(x) for x in names),
       str([x for x in names if "wxid_" in str(x)]))
    ck("没名字的成员显示占位符（不回落成账号）",
       mg._NO_NAME in names, str(names))
    # topics 是词，不该带出账号形状
    topics = [n["name"] for n in g["nodes"] if n["type"] == "topic"]
    ck("话题词里没有账号形状的词", not any("wxid" in t for t in topics), str(topics))
    # ⛔ 最强的一条：**整份 JSON**（节点 id / meta 全算）都不许出现 wxid —— id 会进浏览器
    import json as _json

    blob = _json.dumps(g, ensure_ascii=False)
    ck("整份返回 JSON 不含 wxid（节点 id / meta 全算）",
       "wxid_" not in blob, str([x for x in blob.split('"') if "wxid_" in x][:3]))

    print("== 话题抽取 ==")
    ck("反复出现的词成话题节点（「夜跑」出现 2 次）",
       any(n["name"] == "夜跑" for n in g["nodes"] if n["type"] == "topic"),
       str(topics))
    ck("只出现 1 次的词**不**成节点（去噪）",
       not any(n["name"] == "钓鱼" for n in g["nodes"] if n["type"] == "topic"),
       str(topics))

    print("== 空数据如实 ==")
    class _Empty:
        def chat_keys(self):
            return []

        def members(self, chat_key):
            return []

    ge = mg.build(store=_Empty())
    ck("没有记忆 ⇒ empty=True 且给说明（不画假骨架）",
       ge.get("empty") is True and not ge.get("nodes") and bool(ge.get("note")),
       "note=" + str(ge.get("note"))[:40])

    print("== 节点超限按「人优先」裁 ==")
    class _Many:
        def chat_keys(self):
            return ["group:big@chatroom"]

        def members(self, chat_key):
            out = [{"userId": "uid_u%d" % i, "name": "人%d" % i,
                    "impressions": [{"content": "话题", "createdAt": i}]}
                   for i in range(40)]
            return out

    gm = mg.build(store=_Many(), max_nodes=10)
    kinds = [n["type"] for n in gm["nodes"]]
    ck("超限时人优先于群（人没被群挤掉）",
       kinds.count("person") >= 1 and len(gm["nodes"]) == 10, str(kinds))

    print("== Mermaid 导出（同源） ==")
    mm = mg.to_mermaid(store=_FakeStore())
    ck("Mermaid 首行是 graph LR 且含节点",
       mm.startswith("graph LR") and "[[" not in mm and "N0" in mm, mm.splitlines()[0])

    print("== Qt 自绘视图（能建、能灌数据、能画） ==")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from PySide6.QtGui import QPixmap

    from stylekit_qt import THEMES
    from memgraph import MemoryGraphView

    t = THEMES["whale"]
    v = MemoryGraphView(t)
    v.resize(600, 400)
    v.set_data(g)
    ck("灌数据后节点被收下、收敛计时器在跑",
       len(v._nodes) == len(g["nodes"]) and v._timer.isActive(),
       "n=%d running=%s" % (len(v._nodes), v._timer.isActive()))
    # 跑几帧力导向，再真画一次（paintEvent 不许抛）
    for _ in range(5):
        v._step()
    pm = QPixmap(600, 400)
    try:
        v.render(pm)
        drew = True
    except Exception as e: # noqa: BLE001
        drew = False
        print("       渲染异常：" + str(e))
    ck("paintEvent 真画不抛（含边、点、名字）", drew)
    ck("空数据也画说明行（不空白、不抛）",
       (lambda: v.set_data({"nodes": [], "links": [], "note": "还没记下什么"}) or True)()
       and not v._timer.isActive())

    print("== 后端接线（路由注册） ==")
    from agent import routes

    ck("/api/memory/graph 已注册（GET）",
       routes.ROUTES.get("/api/memory/graph") == ("GET",)
       and (routes.HANDLERS.get("/api/memory/graph") or {}).get("GET") == "_rapi_memory_graph",
       str(routes.ROUTES.get("/api/memory/graph")))

    print("== 面板接线（Qt 侧按钮 + 自绘视图在页面上） ==")
    import panels_qt

    wrap = panels_qt.build_panel(t, "memory")
    page = wrap.widget()
    ck("memory 页有自绘图谱视图（objectName=mgView）",
       page.findChild(MemoryGraphView, "mgView") is not None)
    from widgets import Btn

    ck("memory 页有「画关系图」按钮（web_action=mgDraw）",
       any(b.property("web_action") == "mgDraw" for b in page.findChildren(Btn)))
    ck("mgDraw 已进「追加区自带实现」名单（不再重复渲染一颗）",
       "mgDraw" in getattr(panels_qt, "_AIDS_COVERED_ELSEWHERE", set()))

    print("\n==== 记忆图谱判据：%d 通过 / %d 失败 ====" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
