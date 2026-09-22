"""预设信息（`agent/briefs.py` + 控制台面板 + `/api/briefs`）的判据。

来源＝B站网友原话：「提前设定信息，当遇到和设定信息有关的内容就好已经提前注入的信息思考，
**设置截止日期**，截止后**自动舍弃**注入的信息，信息**针对每个单独群聊不外泄**」。
⇒ 四条要求各配判据，另加"绝不把这一轮搞崩"与"接线在两条链上"：
  ① 按会话隔离（**结构上**只读本会话；拿别的会话的 id 删不动）
  ② 到期待遇（不再注入 + 真被舍弃 + 不相关的过期条目不影响别的会话）
  ③ 相关才注入（不相关 ⇒ 返回空串，一个字都不给模型）
  ④ 坏文件/异常一律不抛（注入失败绝不能把这一轮弄崩）
"""

import io
import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _srcmatch as _sm          # noqa: E402

from agent import briefs as BR   # noqa: E402

PASS, FAIL = [0], [0]


def ok(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + ("  [%s]" % detail if detail else ""))
    if cond:
        PASS[0] += 1
    else:
        FAIL[0] += 1
    return bool(cond)


def main():
    _tmp = tempfile.mkdtemp(prefix="pm-briefs-")
    _real = BR.PATH
    BR.PATH = os.path.join(_tmp, "briefs.json")
    try:
        now = 1_700_000_000.0
        print("== A. 按会话隔离（结构性，不靠自觉）==")
        BR.add("group:A", "这个群周六下午社庆，地点在 3 号工作室", now=now)
        BR.add("group:B", "这个群每周一早上开例会", now=now)
        a = BR.list_for("group:A", now=now)
        b = BR.list_for("group:B", now=now)
        ok("A 群只看到自己的 1 条", len(a["active"]) == 1 and "社庆" in a["active"][0]["text"],
           str([x["text"][:10] for x in a["active"]]))
        ok("B 群看不到 A 群的条目（读的就是另一条列表）",
           all("社庆" not in x["text"] for x in b["active"]), str([x["text"][:10] for x in b["active"]]))
        _blk_a = BR.block("group:A", "社庆几点开始", now=now)
        _blk_b = BR.block("group:B", "社庆几点开始", now=now)
        ok("注入块里不含别的会话的内容", "例会" not in _blk_a and "社庆" not in _blk_b,
           (_blk_a or "(空)")[:40])
        ok("注入块写明「只属于本会话、不许推断别的会话」",
           "只属于本会话" in _blk_a and "不许" in _blk_a)
        _aid = a["active"][0]["id"]
        _rm = BR.remove("group:B", _aid)
        ok("拿 A 群的 id 去 B 群里删：删不动（只在本会话里找）", _rm["ok"] is False, str(_rm.get("why"))[:40])
        ok("A 群那条还在", len(BR.list_for("group:A", now=now)["active"]) == 1)

        print("== B. 截止日期：到期不再注入 + 自动舍弃 ==")
        BR.add("group:A", "本周三团建", until="2026-09-23", now=now)
        _u = BR.parse_until("2026-09-23")
        _lt = time.localtime(_u)
        ok("按日期填的截止＝当天 23:59:59", (_lt.tm_hour, _lt.tm_min, _lt.tm_sec) == (23, 59, 59),
           time.strftime("%Y-%m-%d %H:%M:%S", _lt))
        ok("填错/留空一律当永久（宁可留着，也不因解析失败丢内容）",
           BR.parse_until("随便写") == 0.0 and BR.parse_until("") == 0.0)
        _before = len(BR.list_for("group:A", now=now)["active"])
        _after = BR.list_for("group:A", now=_u + 10)          # 过了截止日
        ok("到期后不再算「在用」", len(_after["active"]) == _before - 1,
           "before=%d after=%d" % (_before, len(_after["active"])))
        ok("到期的那条进 expired 列表（界面上看得见「已到期」，不是凭空消失）",
           any("团建" in x["text"] for x in _after["expired"]), str(_after["expired"])[:60])
        _n = BR.prune(now=_u + 20)
        ok("prune 把它真舍弃（返回删了几条）", _n == 1, "n=%s" % _n)
        ok("舍弃后 expired 也空了（文件里真的没有它了）",
           BR.list_for("group:A", now=_u + 20)["expired"] == [])

        print("== C. 相关才注入 ==")
        BR.add("group:A", "路由器管理密码在抽屉里的便签上", now=now)
        ok("不相关 ⇒ 返回空串（一个字都不给模型）",
           BR.block("group:A", "今天天气不错", now=now) == "", "非空＝会乱注入")
        _hit = BR.block("group:A", "路由器又掉线了，密码是啥", now=now)
        ok("相关 ⇒ 注入（含那条的内容）", "路由器" in _hit and "抽屉" in _hit, (_hit or "")[:50])
        ok("没设过任何条目的会话 ⇒ 空串（不会凭空造一段）",
           BR.block("group:C", "路由器密码", now=now) == "")
        _e = BR.relevant("路由器掉线了", [{"text": "路由器管理密码在抽屉里", "always": False}])
        ok("relevant() 对相关条目命中", len(_e) == 1)
        _e2 = BR.relevant("今天天气不错", [{"text": "路由器管理密码在抽屉里", "always": False}])
        ok("relevant() 对不相关条目不命中（字面匹配，命不中就不注入）", _e2 == [])
        _e3 = BR.relevant("随便说点啥", [{"text": "这条是常驻背景", "always": True}])
        ok("标了「每次都用」的条目无条件命中", len(_e3) == 1)

        print("== D. 绝不把这一轮搞崩 ==")
        _bad = os.path.join(_tmp, "bad.json")
        with io.open(_bad, "w", encoding="utf-8") as fh:
            fh.write("{ 这不是 json")
        _keep = BR.PATH
        BR.PATH = _bad
        ok("坏文件：load 不抛、按空起", isinstance(BR._load(), dict))
        ok("坏文件：block 给空串（注入失败不影响这一轮）", BR.block("group:A", "社庆") == "")
        ok("坏文件：已被改名留证（.bad.<时间戳>）",
           any(".bad." in n for n in os.listdir(_tmp)), str(os.listdir(_tmp))[:80])
        BR.PATH = _keep
        ok("entries 为 None / chat_key 为空也不抛", BR.block("", "x") == "" and BR.block(None, None) == "")

        print("== E. 接线（两条链 + 面板 + 提示词 + 配置）==")
        _wb = io.open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
        ok("GET 链注册了 /api/briefs", _sm.has(_wb, 'elif path == "/api/briefs"') and
           _sm.has(_wb, 'self._briefs_api(data, "GET")'))
        ok("POST 链注册了同一个处理器（不是只挂一条链）",
           _sm.has(_wb, 'self._briefs_api(data, "POST")'))
        _ch = io.open(os.path.join(ROOT, "agent", "console_html.py"), encoding="utf-8").read()
        ok("控制台有面板（选择会话 + 内容 + 截止日期 + 添加 + 列表）",
           all(k in _ch for k in ("bfChat", "bfText", "bfUntil", "bfAdd", "bfList")))
        ok("面板上把代价写清楚（会随该会话请求发给模型；字面匹配）",
           _sm.has(_ch, "会随<b>该会话</b>的请求一起发给模型") and _sm.has(_ch, "字面"))
        _pr = io.open(os.path.join(ROOT, "agent", "prompt.py"), encoding="utf-8").read()
        ok("提示词里真的注入了（不是写了没人用）", _sm.has(_pr, "from . import briefs as _bf"))
        _cfg = io.open(os.path.join(ROOT, "agent", "config.py"), encoding="utf-8").read()
        _cex = json.load(io.open(os.path.join(ROOT, "config.example.json"), encoding="utf-8"))
        _cst = (_cex.get("store") or {}) if isinstance(_cex, dict) else {}
        ok("配置有 briefs 段（enabled / inject_max）",
           _sm.has(_cfg, '"briefs": {') and isinstance(_cst.get("briefs"), dict),
           str(list((_cst.get("briefs") or {}).keys()))[:40])
        ok("示例配置也带上了（不然用户看不到这个开关）",
           isinstance(_cst.get("briefs"), dict) and "enabled" in (_cst.get("briefs") or {}))
    finally:
        BR.PATH = _real

    print("== 预设信息判据：%d 通过 / %d 失败 ==" % (PASS[0], FAIL[0]))
    return 0 if FAIL[0] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
