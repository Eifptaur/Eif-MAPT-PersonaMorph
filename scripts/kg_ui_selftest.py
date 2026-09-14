# -*- coding: utf-8 -*-
"""知识图谱看板判据（⑫a）——不需要 DSH 插件在跑，用**合成库**验逻辑，用真库验兼容。

用户口径（2026-09-14）：「知识图谱……它没有 UI 呀，所以重绘它的呀」⇒ 这张看板必须：
  ① 只读（绝不写图谱库、绝不建库、不锁插件正在用的库）；
  ② 真读得懂那张库（gm_nodes / gm_edges / gm_nodes_fts 的实际列）；
  ③ 在控制台里有位置、有接口，且**图是我们自己画的**（SVG，不用任何图表库）。

四组：
  A 只读铁律：连接是 mode=ro；写操作必须失败；库文件 mtime 不变。
  B 逻辑（合成库）：统计/类型分布/最近节点/搜索（FTS + 退回 LIKE）/邻域/鸟瞰图都算得对。
  C 兼容与诚实：库不存在/表缺失/空库 ⇒ ok=False 且说明原因，不抛异常、不假装有数据。
  D 接线：控制台有这一节 + SVG + 接口；数据只在 kg_view 一份。
"""
import os
import sqlite3
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OK, BAD = [], []


def ck(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("  %s %s%s" % ("PASS" if cond else "FAIL", name, (" · " + extra) if extra else ""))


from agent import kg_view as K      # noqa: E402

# ── 造一个合成图谱库（列名照真库 2026-09-15 实测）────────────────────────
tmpdir = tempfile.mkdtemp(prefix="kg_")
DB = os.path.join(tmpdir, "graph-memory.db")


def build(path, nodes=(), edges=(), with_fts=True):
    c = sqlite3.connect(path)
    c.executescript("""
      create table gm_nodes(id text primary key, type text, name text, description text, content text,
        status text, validated_count int, source_sessions text, community_id text, pagerank real,
        created_at integer, updated_at integer);
      create table gm_edges(id text primary key, from_id text, to_id text, type text, instruction text,
        condition text, session_id text, created_at integer);
      create table gm_node_sources(node_id text, session_id text, message_id text, turn_index int);
      create table gm_communities(id text, summary text, node_count int);
    """)
    now = int(time.time() * 1000)
    for i, (nid, t, nm, desc, pr) in enumerate(nodes):
        c.execute("insert into gm_nodes values(?,?,?,?,?,?,?,?,?,?,?,?)",
                  (nid, t, nm, desc, "正文 " + nm, "active", 1, "s1", None, pr,
                   now - i * 1000, now - i * 1000))
    for eid, a, b, t in edges:
        c.execute("insert into gm_edges values(?,?,?,?,?,?,?,?)", (eid, a, b, t, "怎么用的", None, "s1", now))
    c.execute("insert into gm_node_sources values('n1','dsh:session-abc','dsh:session-abc:3',3)")
    if with_fts:
        c.executescript("create virtual table gm_nodes_fts using fts5(name, description, content);")
        c.execute("insert into gm_nodes_fts(name, description, content) "
                  "select name, description, content from gm_nodes")
    c.commit()
    c.close()


NODES = [("n1", "TASK", "rebuild-console-window-webview2", "让控制台改用自有 WebView2 窗口", 0.9),
         ("n2", "SKILL", "webview2-embed", "用 WebView2 内嵌承载 web 控制台", 0.5),
         ("n3", "EVENT", "wechat-version-bump", "微信被自动升级到 4.1.15.8", 0.2)]
EDGES = [("e1", "n1", "n2", "USED_SKILL"), ("e2", "n1", "n3", "SOLVED_BY"), ("e3", "n2", "n3", "REQUIRES")]
build(DB, NODES, EDGES)
os.environ["DSH_GRAPH_DB"] = DB

# ── A 只读铁律 ───────────────────────────────────────────────────────────
print("[A] 只读铁律")
ck("A1 db_path 认环境变量", K.db_path() == DB, K.db_path())
try:
    c = K._ro_conn(DB)
    c.execute("insert into gm_nodes(id,type) values('x','X')")
    c.commit()
    ck("A2 只读连接拒绝写入", False, "居然写进去了")
except sqlite3.OperationalError as e:
    ck("A2 只读连接拒绝写入", "readonly" in str(e).lower(), str(e)[:60])
except Exception as e:
    ck("A2 只读连接拒绝写入", False, str(e)[:60])
m0 = os.path.getmtime(DB)
K.stats(); K.recent(5); K.search("WebView2"); K.node("n1"); K.graph(10)
time.sleep(0.05)
ck("A3 读一圈之后库文件没被改动（mtime 不变）", os.path.getmtime(DB) == m0)

# ── B 逻辑 ───────────────────────────────────────────────────────────────
print("[B] 逻辑（合成库）")
st = K.stats()
ck("B1 总览：节点/边计数对", st.get("nodes") == 3 and st.get("edges") == 3, str({k: st.get(k) for k in ("nodes", "edges")}))
ck("B2 类型分布按数量排序", [t["type"] for t in st["types"]] == ["TASK", "SKILL", "EVENT"]
   or sorted(t["n"] for t in st["types"]) == [1, 1, 1], str(st["types"]))
ck("B3 边类型也在总览里", {e["type"] for e in st["edge_types"]} == {"USED_SKILL", "SOLVED_BY", "REQUIRES"})
ck("B4 最近节点按时间倒序", [n["id"] for n in K.recent(5)] == ["n1", "n2", "n3"],
   str([n["id"] for n in K.recent(5)]))
ck("B5 节点行字段齐全（描述/时间都出来了）",
   K.recent(1)[0]["description"] and K.recent(1)[0]["created"], str(K.recent(1)[0])[:120])
s1 = K.search("WebView2")
ck("B6 搜索命中 n1（FTS 按名字回表；命中多个也算对）",
   len(s1) >= 1 and "n1" in [x["id"] for x in s1], str([x["id"] for x in s1]))
s2 = K.search("没有这个词")
ck("B7 搜不到就返回空（不报错、不返回全库）", s2 == [], str(s2)[:60])
nd = K.node("n1")
ck("B8 邻域：1 跳邻居 2 个、边 2 条", len(nd["nodes"]) == 3 and len(nd["edges"]) == 2,
   "nodes=%d edges=%d" % (len(nd["nodes"]), len(nd["edges"])))
ck("B9 节点详情带出处（gm_node_sources）",
   any(x["session_id"] == "dsh:session-abc" for x in nd["sources"]), str(nd["sources"]))
ck("B10 节点正文截断但保留", nd["content"].startswith("正文"), str(nd["content"])[:30])
g = K.graph(10)
ck("B11 鸟瞰：含全部 3 个节点、且只留两端都在图里的边", len(g["nodes"]) == 3 and len(g["edges"]) == 3,
   "n=%d e=%d" % (len(g["nodes"]), len(g["edges"])))
ck("B12 鸟瞰按连线数排序（hub 在前）", g["nodes"][0]["id"] == "n1", str([(x["id"], x["n_edges"]) for x in g["nodes"]]))
# LIKE 退回：没有 FTS 的库也要能搜
DB2 = os.path.join(tmpdir, "nofts.db")
build(DB2, NODES, EDGES, with_fts=False)
os.environ["DSH_GRAPH_DB"] = DB2
ck("B13 没有 FTS 表时退回 LIKE 也能搜到", "n1" in [x["id"] for x in K.search("WebView2")], str([x["id"] for x in K.search("WebView2")]))
os.environ["DSH_GRAPH_DB"] = DB

# ── C 兼容与诚实 ─────────────────────────────────────────────────────────
print("[C] 兼容与诚实（读不到就说读不到）")
os.environ["DSH_GRAPH_DB"] = os.path.join(tmpdir, "not-there.db")
r = K.view()
ck("C1 库不存在 ⇒ ok=False 且给出原因", r.get("ok") is False and "找不到" in str(r.get("reason")),
   str(r.get("reason"))[:70])
ck("C2 库不存在时各接口都不抛异常", K.stats().get("ok") is False and K.recent(3) == []
   and K.search("x") == [] and K.node("n1").get("ok") is False and K.graph(5).get("nodes") == [])
bad = os.path.join(tmpdir, "bad.db")
sqlite3.connect(bad).execute("create table something_else(x)").connection.commit()
os.environ["DSH_GRAPH_DB"] = bad
ck("C3 库里没有 gm_nodes ⇒ 明确说「不是图谱库」",
   "gm_nodes" in str(K.available()[1]), str(K.available()[1])[:70])
empty = os.path.join(tmpdir, "empty.db")
build(empty)
os.environ["DSH_GRAPH_DB"] = empty
st0 = K.stats()
ck("C4 空图谱 ⇒ 计数为 0 而不是报错", st0.get("ok") is True and st0["nodes"] == 0 and st0["edges"] == 0)
ck("C5 空图谱的鸟瞰图是空节点表（控制台画「还没有节点」）", K.graph(10)["nodes"] == [])
os.environ.pop("DSH_GRAPH_DB", None)

# ── D 接线 ───────────────────────────────────────────────────────────────
print("[D] 接线：控制台里有位置、有接口、图自己画")
SRC_C = open(os.path.join(ROOT, "agent", "console_html.py"), encoding="utf-8").read()
SRC_W = open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
SRC_K = open(os.path.join(ROOT, "agent", "kg_view.py"), encoding="utf-8").read()
ck("D1 导航里有「图谱」这一栏", 'href="#sec-kg"' in SRC_C and '<span class="lb">图谱</span>' in SRC_C)
ck("D2 有独立小节 + 标题", 'id="sec-kg"' in SRC_C and "<h2>知识图谱</h2>" in SRC_C)
ck("D3 图是自己画的 SVG（不引任何图表库）",
   'id="kgSvg"' in SRC_C and "<svg" in SRC_C and "chart.js" not in SRC_C.lower() and "d3." not in SRC_C)
ck("D4 三个接口都在（总览/搜索/节点）",
   all(x in SRC_W for x in ('"/api/kg/view"', '"/api/kg/search"', '"/api/kg/node"')))
ck("D5 接口都走 kg_view 这一份实现（控制台不自己读库）",
   "from . import kg_view" in SRC_W and SRC_C.count("sqlite") == 0)
ck("D6 只读打开写在实现里（mode=ro）", "mode=ro" in SRC_K)
ck("D7 画图逻辑是确定性的（不抖：按类型分列 + 度数排序）",
   "byType" in SRC_C and "n_edges" in SRC_C)
ck("D8 点节点看邻域（交互接上了）", "data-kg=" in SRC_C and "kgOpen(" in SRC_C and "/api/kg/node?id=" in SRC_C)
ck("D13 类型分布与边类型的拼接写法正确（不许 `+` 抢先算成恒真）",
   "kgTypes').innerHTML = bars + (er.length" in SRC_C)

# D9 活体：真起一次控制台，走 HTTP 拉三个接口（"接口真的通"只有这一条能证明）
print("[D9] 活体：起一次控制台，走 HTTP 拉图谱接口")
try:
    import json
    import socket
    import urllib.request

    from agent import webui as W
    _orig = W.get_config
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    base = dict(_orig() or {})
    base["server"] = {"enabled": True, "host": "127.0.0.1", "port": port, "token": "kg-judge",
                      "auto_open_browser": False}
    W.get_config = lambda: base
    w3 = None
    try:
        w3 = W.WebUI(lambda: {}, [])
        port = w3.start()

        def _get(p):
            with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, p), timeout=10) as r:
                return json.loads(r.read().decode("utf-8", "replace"))

        v = _get("/api/kg/view?token=kg-judge")
        ck("D9 /api/kg/view 通（HTTP + 真库）", v.get("ok") is True and (v.get("stats") or {}).get("nodes", 0) > 0,
           "节点=%s 边=%s" % ((v.get("stats") or {}).get("nodes"), (v.get("stats") or {}).get("edges")))
        gn = ((v.get("graph") or {}).get("nodes") or [])
        ck("D10 /api/kg/view 带回可画的鸟瞰数据", len(gn) > 0, "鸟瞰节点=%d" % len(gn))
        nid = (gn[0] or {}).get("id") if gn else ""
        nd = _get("/api/kg/node?id=%s&token=kg-judge" % nid)
        ck("D11 /api/kg/node 通（能取详情）", nd.get("ok") is True and (nd.get("node") or {}).get("id") == nid,
           str((nd.get("node") or {}).get("name"))[:40])
        se = _get("/api/kg/search?q=%E4%BA%BA%E8%AE%BE&token=kg-judge")   # 「人设」
        ck("D12 /api/kg/search 通（返回结构对）", se.get("ok") is True and isinstance(se.get("nodes"), list),
           "命中 %d 条" % len(se.get("nodes") or []))
    finally:
        W.get_config = _orig
        try:
            if w3:
                w3.stop()
        except Exception:
            pass
except Exception as e:
    ck("D9 活体 HTTP 三接口", False, "起控制台失败：%s" % str(e)[:90])

print("\n[结论] %d 通过 / %d 失败" % (len(OK), len(BAD)))
if BAD:
    print("失败项：%s" % BAD)
sys.exit(1 if BAD else 0)
