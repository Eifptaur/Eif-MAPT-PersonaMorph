# -*- coding: utf-8 -*-
"""「我的其他账号（大号）」判据（离线，不需要微信在跑）。

起因（用户 2026-09-16 原话）：「这个是用的我的小号 他无法识别我的大号 之前版本也有这个问题」
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

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
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

from agent.wechat import WeChatAdapter       # noqa: E402


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

print("\n── F. 界面能看到（用户口径：机制要映射到 UI 上）──")
page = io.open(os.path.join(ROOT, "agent", "console_html.py"), encoding="utf-8").read()
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

print("")
print("主人识别判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
