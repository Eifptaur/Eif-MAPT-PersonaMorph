# -*- coding: utf-8 -*-
"""知识图谱看板（**只读**）：把 `~/.dsh/graph-memory/graph-memory.db` 读成控制台能画的东西。

为什么要有它（用户 2026-09-14）：「知识图谱……它没有 UI 呀，所以重绘它的呀」——
图谱插件只提供工具（`gm_*`）和一个 43 MB 的 SQLite，**没有任何界面**；
这份模块把它读出来给E 控制台画（不写库、不锁库：一律 `mode=ro` URI 打开）。

表（实测 2026-09-15）：
  gm_nodes(id,type,name,description,content,status,validated_count,source_sessions,community_id,pagerank,…)
  gm_edges(id,from_id,to_id,type,instruction,condition,session_id,…)
  gm_nodes_fts(name,description,content)   ← 全文检索用
  gm_node_sources   ← 每个节点是由哪些会话消息抽出来的（溯源）
"""
import os
import sqlite3
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def db_path() -> str:
    """定位图谱库：环境变量优先（**显式指定的路径即使不存在也照实返回**，不静默换库），
    其次 `$DSH_HOME/graph-memory/graph-memory.db`，最后 `~/.dsh/...`。"""
    env = os.environ.get("DSH_GRAPH_DB")
    if env:
        return env
    home = os.environ.get("DSH_HOME") or os.path.join(os.path.expanduser("~"), ".dsh")
    cands = [os.path.join(home, "graph-memory", "graph-memory.db"),
             os.path.join(os.path.expanduser("~"), ".dsh", "graph-memory", "graph-memory.db")]
    for c in cands:
        if os.path.exists(c):
            return c
    return cands[-1]


def _ro_conn(path: str = None):
    """只读连接（`mode=ro`）：绝不写、绝不建库、不会把插件正在用的库锁住。"""
    p = path or db_path()
    if not os.path.exists(p):
        raise FileNotFoundError(p)
    uri = "file:%s?mode=ro" % p.replace("\\", "/")
    c = sqlite3.connect(uri, uri=True, timeout=3.0)
    c.row_factory = sqlite3.Row
    return c


def available(path: str = None) -> tuple:
    """(ok, 说明)：库不在 / 表不在 / 都好在。"""
    p = path or db_path()
    if not os.path.exists(p):
        return False, "找不到图谱库：%s" % p
    try:
        with _ro_conn(p) as c:
            names = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
        if "gm_nodes" not in names:
            return False, "库里没有 gm_nodes 表（不是图谱库？）：%s" % p
        return True, p
    except Exception as e:
        return False, "打开图谱库失败：%s" % str(e)[:120]


def _rows(c, sql, args=()):
    try:
        return [dict(r) for r in c.execute(sql, args)]
    except Exception:
        return []


def stats(path: str = None) -> dict:
    """总览：节点/边总量、按类型分布、最近 7 天新增、社区数、库文件大小。"""
    p = path or db_path()
    ok, why = available(p)
    if not ok:
        return {"ok": False, "reason": why, "path": p}
    try:
        with _ro_conn(p) as c:
            n_nodes = c.execute("select count(*) from gm_nodes").fetchone()[0]
            n_edges = c.execute("select count(*) from gm_edges").fetchone()[0]
            types = _rows(c, "select type, count(*) n from gm_nodes group by type order by n desc")
            etypes = _rows(c, "select type, count(*) n from gm_edges group by type order by n desc")
            last = c.execute("select max(created_at) from gm_nodes").fetchone()[0] or 0
            # 时间戳单位要判断（秒 / 毫秒都见过）：按最大值量级选单位，否则"最近 7 天"会算成全库
            unit = 1000.0 if float(last or 0) > 1e11 else 1.0
            week = int(time.time() * unit) - int(7 * 86400 * unit)
            recent = c.execute("select count(*) from gm_nodes where created_at > ?", (week,)).fetchone()[0]
            new_edges = c.execute("select count(*) from gm_edges where created_at > ?", (week,)).fetchone()[0]
            try:
                n_comm = c.execute("select count(*) from gm_communities").fetchone()[0]
            except Exception:
                n_comm = 0
            last = c.execute("select max(created_at) from gm_nodes").fetchone()[0] or 0
        return {"ok": True, "path": p, "size_mb": round(os.path.getsize(p) / 1e6, 1),
                "nodes": n_nodes, "edges": n_edges, "types": types, "edge_types": etypes,
                "recent_nodes": recent, "recent_edges": new_edges, "communities": n_comm,
                "last_ts": _ts(last)}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:160], "path": p}


def _ts(ms) -> str:
    try:
        ms = int(ms or 0)
        if ms > 1e12:
            ms = ms / 1000.0
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(ms)) if ms else ""
    except Exception:
        return ""


def _node_row(r, degree=None) -> dict:
    # ⚠️ sqlite3.Row 没有 .get()：这里先统一成 dict（2026-09-15 踩过：不转就整段抛异常被吞掉，
    # 表现是"最近节点/邻域全空"，而 search 因为先转过了所以正常——很难一眼看出的半死状态）
    d = dict(r) if not isinstance(r, dict) else r
    return {"id": d.get("id"), "type": d.get("type"), "name": d.get("name"),
            "description": (d.get("description") or "")[:220],
            "status": d.get("status") or "", "pagerank": round(float(d.get("pagerank") or 0), 5),
            "degree": degree, "created": _ts(d.get("created_at")), "updated": _ts(d.get("updated_at"))}


def recent(limit: int = 30, ntype: str = "") -> list:
    p = db_path()
    ok, _why = available(p)
    if not ok:
        return []
    try:
        with _ro_conn(p) as c:
            if ntype:
                return [_node_row(r) for r in c.execute(
                    "select * from gm_nodes where type=? order by created_at desc limit ?", (ntype, int(limit)))]
            return [_node_row(r) for r in c.execute(
                "select * from gm_nodes order by created_at desc limit ?", (int(limit),))]
    except Exception:
        return []


def search(q: str, limit: int = 20) -> list:
    """先走 FTS，失败退回 LIKE（FTS 表可能被重建过）。

    ⚠️ 踩过的坑（2026-09-15）：FTS 表的**第一列是 `name`**（不是 id）——
    拿 `n.id = f.name` 去 join 会一条都匹配不上（真库上表现为"搜什么都搜不到"）。
    正确做法：用 FTS 捞出**名字**，再按名字回表取节点（并去掉重名）。
    """
    p = db_path()
    ok, _why = available(p)
    if not ok or not (q or "").strip():
        return []
    q = q.strip()
    lim = max(1, int(limit))
    try:
        with _ro_conn(p) as c:
            rows = []
            try:
                names = [r[0] for r in c.execute(
                    "select name from gm_nodes_fts where gm_nodes_fts match ? limit ?", (q, lim))]
                out = []
                for nm in names:
                    for r in c.execute("select * from gm_nodes where name=? limit 3", (nm,)):
                        out.append(dict(r))
                rows = out[:lim]
            except Exception:
                rows = []
            if not rows:
                like = "%%%s%%" % q
                rows = [dict(r) for r in c.execute(
                    "select * from gm_nodes where name like ? or description like ? or content like ? "
                    "order by updated_at desc limit ?", (like, like, like, lim))]
        seen = set()
        out = []
        for r in rows:
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            out.append(_node_row(r))
        return out
    except Exception:
        return []


def node(nid: str, depth: int = 1) -> dict:
    """一个节点的详情 + 邻域（depth 层，最多 60 个邻居，防止画爆）。"""
    p = db_path()
    ok, why = available(p)
    if not ok:
        return {"ok": False, "reason": why}
    try:
        with _ro_conn(p) as c:
            r = c.execute("select * from gm_nodes where id=?", (nid,)).fetchone()
            if r is None:
                return {"ok": False, "reason": "没有这个节点：%s" % nid}
            seen = {nid}
            frontier = [nid]
            edges = []
            for _ in range(max(1, int(depth))):
                nxt = []
                for cur in frontier:
                    for e in c.execute(
                            "select * from gm_edges where from_id=? or to_id=? limit 120", (cur, cur)):
                        edges.append({"id": e["id"], "from": e["from_id"], "to": e["to_id"],
                                      "type": e["type"], "instruction": (e["instruction"] or "")[:160]})
                        other = e["to_id"] if e["from_id"] == cur else e["from_id"]
                        if other not in seen and len(seen) < 60:
                            seen.add(other)
                            nxt.append(other)
                frontier = nxt
                if not frontier:
                    break
            qs = ",".join("?" * len(seen))
            nodes = [_node_row(x) for x in c.execute(
                "select * from gm_nodes where id in (%s)" % qs, tuple(seen))]
            srcs = _rows(c, "select session_id, count(*) n from gm_node_sources where node_id=? "
                            "group by session_id order by n desc limit 5", (nid,))
        return {"ok": True, "node": _node_row(r), "content": (r["content"] or "")[:2000],
                "nodes": nodes, "edges": edges, "sources": srcs}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:160]}


def graph(limit: int = 120, ntype: str = "") -> dict:
    """鸟瞰图数据：**按类型轮流取**（类型内按连线数排），再要它们之间的边。

    为什么按类型轮流取：全局按度数取前 N 会把"边少但占多数"的类型整类挤掉——
    实测（2026-09-15）：EVENT 占 61.8%，却因为边少一个都画不出来，图上看只剩两列，误导性强。
    """
    p = db_path()
    ok, why = available(p)
    if not ok:
        return {"ok": False, "reason": why, "nodes": [], "edges": []}
    lim = max(1, int(limit))
    try:
        with _ro_conn(p) as c:
            if ntype:
                tlist = [ntype]
            else:
                tlist = [r[0] for r in c.execute(
                    "select type from gm_nodes group by type order by count(*) desc")]
            byt = {}
            for t in tlist:
                # **每种类型各查各的**（各自按连线数排序）：一次性全局排序会把"边少但数量多"的类型整类挤掉
                rows = [dict(r) for r in c.execute(
                    "select * from gm_nodes where type=? order by "
                    "(select count(*) from gm_edges e where e.from_id=gm_nodes.id or e.to_id=gm_nodes.id) desc "
                    "limit ?", (t, lim))]
                lst = []
                for r in rows:
                    dn = c.execute("select count(*) from gm_edges e where e.from_id=? or e.to_id=?",
                                   (r["id"], r["id"])).fetchone()[0]
                    lst.append((r["id"], dn, r))
                byt[t] = lst
            picked, i = [], 0
            while len(picked) < lim:
                added = False
                for t in tlist:
                    lst = byt.get(t) or []
                    if i < len(lst):
                        picked.append(lst[i])
                        added = True
                        if len(picked) >= lim:
                            break
                if not added:
                    break
                i += 1
            nodes = []
            for nid, dn, r in picked:
                nd = _node_row(r, degree=dn)
                nd["n_edges"] = dn
                nodes.append(nd)
            keep = {n["id"] for n in nodes}
            edges = []
            if keep:
                qs = ",".join("?" * len(keep))
                for e in c.execute("select * from gm_edges where from_id in (%s) and to_id in (%s) "
                                   "limit 400" % (qs, qs), tuple(keep) + tuple(keep)):
                    edges.append({"from": e["from_id"], "to": e["to_id"], "type": e["type"]})
            return {"ok": True, "nodes": nodes, "edges": edges, "path": p}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:160], "nodes": [], "edges": []}


def view() -> dict:
    """控制台一次拿全：总览 + 最近节点 + 鸟瞰图（都只读）。"""
    st = stats()
    if not st.get("ok"):
        return {"ok": False, "reason": st.get("reason"), "path": st.get("path")}
    return {"ok": True, "stats": st, "recent": recent(24), "graph": graph(90)}
