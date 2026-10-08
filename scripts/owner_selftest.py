# -*- coding: utf-8 -*-
"""「我的其他账号（大号）」判据（离线，不需要微信在跑）。

起因
—— 机器人跑在**小号**上，而主人的**大号**在群里说话时，程序原先把大号当**普通群友**（会回你自己的话）。

本判据盯四件事：
  ① 认得出：wxid 精确优先、昵称兜底；关掉或没登记都不认（fail-closed）
  ② 登记表能被三种写法解析（数组 / 英文逗号 / 中文逗号 + 换行）
  ③ 认出来之后：上下文里显示「主人」；mode=skip 时 `normalize` 直接跳过
  ④ 界面能看到：控制台有登记框 + 反应档位 + **匹配结果**（认错人能一眼发现）；`/api/status` 有 owner 段
"""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  OK   %s%s" % (name, ("  [" + str(detail) + "]") if detail else ""))
    else:
        FAIL += 1
        print("  FAIL %s%s" % (name, ("  [" + str(detail) + "]") if detail else ""))


print("「我的其他账号（大号）」判据")
print("")

from agent.wechat import WeChatAdapter # noqa: E402


def fake(cfg_wechat, nicks=None):
    """造一个空壳适配器：只放判据需要的那几个属性，不连微信、不读库。"""
    o = object.__new__(WeChatAdapter)
    o.cfg = {"wechat": cfg_wechat}
    o._nick_map = nicks or {}
    o._self_wxid = ""
    o._self_nickname = ""
    o._load_owner_accounts()
    return o


print("── A. 认得出（wxid 精确优先、昵称兜底）──")
a = fake({"owner_accounts": ["wxid_aaa", "张大号"], "owner_mode": "know"},
         {"wxid_aaa": "张大号", "wxid_bbb": "路人"})
ok("wxid 命中", a.is_owner("wxid_aaa", "随便") is True)
ok("昵称命中（兜底）", a.is_owner("wxid_zzz", "张大号") is True)
ok("路人两条都不中", a.is_owner("wxid_bbb", "路人") is False)
ok("大小写不敏感", a.is_owner("WXID_AAA", "") is True)

print("\n── B. fail-closed：关掉 / 没登记 ⇒ 谁都不认 ──")
ok("mode=off ⇒ 全不认",
   fake({"owner_accounts": ["wxid_aaa"], "owner_mode": "off"}).is_owner("wxid_aaa", "x") is False)
ok("没登记 ⇒ 全不认", fake({"owner_mode": "know"}).is_owner("wxid_aaa", "x") is False)
ok("空串登记 ⇒ 全不认", fake({"owner_accounts": "", "owner_mode": "know"}).is_owner("", "") is False)

print("\n── C. 登记表的三种写法都要能解析 ──")
ok("数组", fake({"owner_accounts": ["a", "b"]})._owner_ids == {"a", "b"})
ok("英文逗号字符串", fake({"owner_accounts": "a, b"})._owner_ids == {"a", "b"})
ok("中文逗号 + 换行", fake({"owner_accounts": "a，b\nc"})._owner_ids == {"a", "b", "c"})
ok("非法档位回落 know", fake({"owner_accounts": ["a"], "owner_mode": "??"})._owner_mode == "know")

print("\n── D. 匹配结果要能核对（认错人一眼发现）──")
st = a.owner_status()
ok("统计项齐", all(k in st for k in ("mode", "count", "byId", "byName", "nameMatched", "nameUnmatched")))
ok("wxid 1 项 / 昵称 1 项", st["byId"] == 1 and st["byName"] == 1, st)
ok("昵称确实匹配上了", st["nameMatched"] == ["张大号"], st)
st2 = fake({"owner_accounts": ["不存在的人"]}, {"wxid_x": "别人"}).owner_status()
ok("写错的昵称进 unmatched（界面据此警示）", st2["nameUnmatched"] == ["不存在的人"], st2)

print("\n── E. 认出来之后：上下文显示「主人」、skip 直接跳过 ──")
try:
    from agent import prompt as P
    s1 = P._format_entry({"sender_id": "wxid_aaa", "sender_name": "张大号", "owner": True, "text": "在吗"})
    s0 = P._format_entry({"sender_id": "wxid_bbb", "sender_name": "路人", "text": "在吗"})
    ok("owner=True ⇒ 显示「主人」", "主人" in s1, s1[:40])
    ok("普通群友不显示「主人」", "主人" not in s0, s0[:40])
except Exception as e:
    ok("prompt 显示「主人」", False, e)

src = io.open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
ok("normalize 里打了 owner 标记", '"owner": _is_owner' in src)
ok("mode=skip 时直接跳过", '"skip"' in src)
ok("与「识别自己账号」是两件事（self_identity 仍在）", "self_identity" in src and "is_owner" in src)

print("\n── F. 界面能看到──")
page = io.open(os.path.join(ROOT, "assets", "console", "index.html"), encoding="utf-8").read()
ok("有登记框", 'data-cfg="wechat.owner_accounts"' in page)
ok("有反应档位下拉", 'data-cfg="wechat.owner_mode"' in page)
ok("三个档位都在", all(('value="%s"' % m) in page for m in ("know", "skip", "off")))
ok("有匹配结果显示位", 'id="ownerHit"' in page and "nameUnmatched" in page)
w = io.open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
ok("/api/status 带 owner 段", 'st["owner"]' in w)
cfg = io.open(os.path.join(ROOT, "agent", "config.py"), encoding="utf-8").read()
ok("配置默认值在", '"owner_accounts"' in cfg and '"owner_mode"' in cfg)
ex = io.open(os.path.join(ROOT, "config.example.json"), encoding="utf-8").read()
ok("config.example.json 也带这两项（否则 fullcheck 的「同键」判据会红）",
   '"owner_accounts"' in ex and '"owner_mode"' in ex)

print("\n── G. 私聊（大号跟小号对谈）──")
p = fake({"owner_accounts": ["wxid_big", "大号"], "owner_mode": "know", "private_chat": "owner_only"})
p._privates = [{"name": "大号", "wxid": "wxid_big"}, {"name": "路人", "wxid": "wxid_other"}]
ok("owner_only ⇒ 只给主人的私聊",
   [c["wxid"] for c in p.list_private_targets()] == ["wxid_big"],
   [c["wxid"] for c in p.list_private_targets()])
p2 = fake({"owner_accounts": ["wxid_big"], "owner_mode": "know", "private_chat": "all"})
p2._privates = p._privates
ok("all ⇒ 全部私聊都给", len(p2.list_private_targets()) == 2)
p3 = fake({"owner_accounts": ["wxid_big"], "owner_mode": "know", "private_chat": "off"})
p3._privates = p._privates
ok("off ⇒ 空", p3.list_private_targets() == [])
p4 = fake({"owner_mode": "know", "private_chat": "owner_only"})
p4._privates = p._privates
ok("owner_only 但没登记 ⇒ 空（不静默变成谁都理）", p4.list_private_targets() == [])
p5 = fake({"owner_accounts": ["wxid_big"], "owner_mode": "know", "private_chat": "??"})
p5._privates = p._privates
ok("非法档位回落 owner_only", [c["wxid"] for c in p5.list_private_targets()] == ["wxid_big"])

ra = io.open(os.path.join(ROOT, "agent", "replica_adapter.py"), encoding="utf-8").read()
ok("有 load_privates（只读 contact）", "def load_privates" in ra)
ok("排掉系统号与群", "_SYS_CONTACTS" in ra and "@chatroom" in ra)
pm = io.open(os.path.join(ROOT, "scripts", "persona_morph.py"), encoding="utf-8").read()
ok("监听目标里**追加**私聊（群那份逻辑没改）",
   "list_private_targets()" in pm and "targets += [{" in pm)
ok("UI 有私聊档位且三档齐", 'data-cfg="wechat.private_chat"' in page
   and all(('value="%s"' % m) in page for m in ("owner_only", "off", "all")))
ok("config.example.json 有该项", '"private_chat"' in ex)

print("\n── H. 长清单折叠：群白名单那排也要折叠──")
ok("#wlChips 在 FOLD_TARGETS 里", '["#wlChips"' in page)
ok("折叠框架在（__foldAll + fold-bar）", "__foldAll" in page and "fold-bar" in page)
wt = io.open(os.path.join(ROOT, "agent", "whale_text.py"), encoding="utf-8").read()
ok("新文案进了鲸语字典（否则 whale 判据会红）", '"私聊"' in wt)

print("\n── I. 第四档「只在群里 @ 我 / 引用我时才回」（2026-09-16 新增，做成可选档位）──")
ok("第四档合法、不被回落成 know",
   fake({"owner_accounts": ["wxid_big"], "owner_mode": "owner_at_only"})._owner_mode == "owner_at_only")
ok("UI 有第四档", ('value="owner_at_only"') in page)
ok("config.py 注释里写了这一档", "owner_at_only" in cfg)
try:
    from agent import prompt as _P

    _real_gc = _P.get_config

    def _cfg_with(mode):
        def _f():
            return {"wechat": {"owner_mode": mode},
                    "store": {"context_tier": 4, "all_count": 60, "at_count": 20,
                              "keyword_count": 30, "random_count": 40, "random_percent": 0,
                              "group_blocklist": {}, "unified_tier": True}}
        return _f

    def _ent(text, owner=True, sender="wxid_big"):
        return {"id": 1, "mid": 1, "ts": 9999999999999, "sender_id": sender,
                "sender_name": "张大号", "text": text, "self": False, "media": [],
                "reply": None, "owner": owner}

    _P.get_config = _cfg_with("owner_at_only")
    _g = _P.resolve_context_tier([_ent("在吗")], "示例群", "", "", roll=0,
                                 chat_key="group:gA", group_name="甲群")
    ok("群里主人的话、没 @ 我 ⇒ 不回", _g.get("should_respond") is False, _g.get("reason"))
    _g2 = _P.resolve_context_tier([_ent("@示例群 在吗")], "示例群", "", "", roll=0,
                                  chat_key="group:gA", group_name="甲群")
    ok("群里 @ 了我 ⇒ 回", _g2.get("should_respond") is True, _g2.get("reason"))
    _g3 = _P.resolve_context_tier([_ent("在吗"), _ent("我也在", owner=False, sender="wxid_other")],
                                  "示例群", "", "", roll=0, chat_key="group:gA", group_name="甲群")
    ok("批里混进别人的话 ⇒ 不按本档拦（照常按档位走）",
       _g3.get("should_respond") is True, _g3.get("reason"))
    _g4 = _P.resolve_context_tier([_ent("在吗")], "示例群", "", "", roll=0,
                                  chat_key="private:wxid_big", group_name="")
    ok("私聊不受这一档影响（「借个智能体跟自己聊」那条用法）",
       _g4.get("should_respond") is True, _g4.get("reason"))
    _P.get_config = _cfg_with("know")
    _g5 = _P.resolve_context_tier([_ent("在吗")], "示例群", "", "", roll=0,
                                  chat_key="group:gA", group_name="甲群")
    ok("know 档下照常回（没把老行为改坏）", _g5.get("should_respond") is True, _g5.get("reason"))
    _P.get_config = _real_gc
except Exception as e: # noqa: BLE001
    ok("第四档运行时行为", False, e)

print("\n── J. `skip` 档的**行为**锚（原来只有 `'\"skip\"' in src` 这种文案锚 ⇒ 改坏了照样绿）──")
try:
    _s_owner = fake({"owner_accounts": ["wxid_big", "张大号"], "owner_mode": "skip"})
    _s_other = fake({"owner_accounts": ["wxid_big", "张大号"], "owner_mode": "skip"})
    # ① 认人这一步不能因为档位是 skip 就失灵（skip 是在"认出来之后"才跳的）
    ok("J1 skip 档下 is_owner 仍认得出主人（wxid 命中）",
       _s_owner.is_owner("wxid_big", "") is True)
    ok("J2 skip 档下 is_owner 仍认得出主人（昵称兜底）",
       _s_owner.is_owner("", "张大号") is True)
    ok("J3 skip 档下**不**认别人（防「整批都当主人」的过宽）",
       _s_other.is_owner("wxid_other", "李四") is False)
    # ② 拿真源码验"跳过分支真的挂在那儿、且**必须靠 is_owner + skip 两个条件同时成立**"
    #    ⛔ 不锚字面（注释里也出现过 skip 字样）—— 取 `normalize` 那段源码里的真语句。
    _WX_SRC = io.open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
    _line = [ln.strip() for ln in _WX_SRC.split("\n")
             if ln.strip().startswith("if _is_owner and") and "skip" in ln]
    ok("J4 真有一条「既认成主人、又是 skip 档」才跳过的语句（不是只认一条）",
       len(_line) == 1 and "_owner_mode" in _line[0].split("==")[0], _line[0] if _line else "没找到")
    _back = [ln.strip() for ln in _WX_SRC.split("\n")
             if ln.strip() == "return None"]
    # ③ 反向锚：同一段里必须**同时**存在"不跳过"的路径（否则把所有人全 skip 了也能绿）
    _ret_after = _WX_SRC.split("if _is_owner and", 1)[-1][:400] if "if _is_owner and" in _WX_SRC else ""
    ok("J5 反向锚：跳过分支之后还有正常返回那条路（不是整段 return None）",
       _ret_after.strip().startswith("str(getattr") or '"_owner_mode"' in _ret_after
       or "return {" in _ret_after, "")
    # ④ 四档都要合法（落回落表里，不能被悄悄改成 know）
    for _m in ("off", "skip", "owner_at_only", "know"):
        ok("J6 档位 %s 是合法档（不被回落）" % _m,
           fake({"owner_accounts": ["wxid_big"], "owner_mode": _m})._owner_mode == _m)
    ok("J7 非法档位回落 know（既有口径不变）",
       fake({"owner_accounts": ["wxid_big"], "owner_mode": "乱填"})._owner_mode == "know")
    # ⑤ off 档：登记了但装没认出来（fail-closed，不误伤）
    _off = fake({"owner_accounts": ["wxid_big"], "owner_mode": "off"})
    ok("J8 off 档 ⇒ 登记了也不认（fail-closed）", _off.is_owner("wxid_big", "张大号") is False)
    # ⑥ 没登记 ⇒ 任何档位都不认（防"空登记表把全员当主人"）
    for _m in ("skip", "owner_at_only", "know"):
        ok("J9 空登记表 + %s 档 ⇒ 一个都不认" % _m,
           fake({"owner_accounts": [], "owner_mode": _m}).is_owner("wxid_big", "张大号") is False)
except Exception as e: # noqa: BLE001
    ok("J. `skip` 档行为锚", False, e)

print("\n── K. 「主人说话 / 别人说话 ⇒ 行为不同」这条验收的**两条真链路**──")
try:
    from agent import prompt as _P2
    # ① `know` 档：同一条文本，只有 owner 标记不同 ⇒ 进模型的显示名必须不同
    _s_master = _P2._format_entry({"sender_id": "wxid_big", "sender_name": "张大号",
                                   "owner": True, "text": "在吗", "ts": 1700000000000})
    _s_stranger = _P2._format_entry({"sender_id": "wxid_other", "sender_name": "李四",
                                     "owner": False, "text": "在吗", "ts": 1700000000000})
    ok("K1 owner 标记 ⇒ 显示成「主人」", "主人" in _s_master, _s_master)
    ok("K2 非 owner ⇒ 显示昵称（不被当成主人）",
       "主人" not in _s_stranger and "李四" in _s_stranger, _s_stranger)
    ok("K3 反向锚：两条显示**确实不同**（不是同一条文案）", _s_master != _s_stranger)
    # ② `owner_at_only` 档：同一批消息，只有 owner 标记不同 ⇒ should_respond 必须不同
    _real_gc2 = _P2.get_config

    def _cfg_oa():
        return {"wechat": {"owner_mode": "owner_at_only"},
                "store": {"context_tier": 4, "all_count": 60, "at_count": 20,
                          "keyword_count": 30, "random_count": 40, "random_percent": 0,
                          "group_blocklist": {}, "unified_tier": True}}

    def _e(text, owner):
        return {"id": 1, "mid": 1, "ts": 9999999999999, "sender_id": "wxid_big" if owner else "wxid_x",
                "sender_name": "张大号" if owner else "李四", "text": text, "self": False,
                "media": [], "reply": None, "owner": owner}

    _P2.get_config = _cfg_oa
    _r_own = _P2.resolve_context_tier([_e("在吗", True)], "示例群", "", "", roll=0,
                                     chat_key="group:gA", group_name="甲群")
    _r_oth = _P2.resolve_context_tier([_e("在吗", False)], "示例群", "", "", roll=0,
                                      chat_key="group:gA", group_name="甲群")
    ok("K4 同一句话：主人发的 ⇒ 不回", _r_own.get("should_respond") is False, _r_own.get("reason"))
    ok("K5 同一句话：别人发的 ⇒ 回", _r_oth.get("should_respond") is True, _r_oth.get("reason"))
    ok("K6 反向锚：两者结论**确实不同**（不是都回或都不回）",
       _r_own.get("should_respond") != _r_oth.get("should_respond"))
    _P2.get_config = _real_gc2
except Exception as e: # noqa: BLE001
    ok("K. 两条真链路", False, str(e)[:200])

print("")
print("主人识别判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
