"""「完整档 / 省 token 档」两套的判据。

档位开关 = 配置 `token_budget.saver`（默认 false = 完整档）。
省 token 档按三降调研报告的 R2–R5 逐项收紧；R1（两段恒定文案挪进 system）**无损，两档都生效**。

每条都配**反向锚**：只证"开着时变了"会漏掉"关着时也变了"（那等于档位不存在）。
⛔ 判据不许改用户真实配置 —— 全程用内存里的假配置（patch 掉 `get_config` / `token_saver`）。
"""

import os
import re
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OK = 0
BAD = []


def ck(name, cond, extra=""):
    global OK
    if cond:
        OK += 1
        print("  OK   %s  %s" % (name, ("[%s]" % extra) if extra else ""))
    else:
        BAD.append(name)
        print("  FAIL %s  %s" % (name, extra))


print("── A. 档位存在，且默认是「完整档」──")
from agent import config as C  # noqa: E402
D = C.DEFAULT_CONFIG
ck("配置里有 token_budget 段", isinstance(D.get("token_budget"), dict))
ck("默认 saver=False（默认完整档，效果优先）",
   D.get("token_budget", {}).get("saver") is False)
ck("token_saver() 是唯一读取点（其它模块不许自己读这个键）",
   sum(1 for p in ("agent/prompt.py", "agent/tools.py", "scripts/persona_morph.py")
       if 'token_budget' in open(os.path.join(ROOT, p), encoding="utf-8").read()
       and 'get("token_budget"' in open(os.path.join(ROOT, p), encoding="utf-8").read()) == 0,
   "除了 config.py，别处不许直接读该键")
_cex = open(os.path.join(ROOT, "config.example.json"), encoding="utf-8").read()
ck("config.example.json 同步了这个键（fullcheck 的对账项）", '"token_budget"' in _cex)
_web = open(os.path.join(ROOT, "assets", "console", "index.html"), encoding="utf-8").read()
ck("控制台有勾选框（用户选得着）", 'data-cfg="token_budget.saver"' in _web)

print()
print("── B. R2：表情码清单 ──")


def _patch(saver: bool):
    """把 config.get_config / token_saver 换成内存假配置（不碰真实配置）。"""
    from agent import config as _c
    _c.get_config = lambda: {"emoji": {"prompt_codes": True}, "token_budget": {"saver": saver}}
    _c.token_saver = lambda: saver
    from agent import prompt as _p
    _p.get_config = _c.get_config
    return _c, _p


_c, _p = _patch(False)
_full = _p._emoji_codes_rule()
_c, _p = _patch(True)
_save = _p._emoji_codes_rule()
ck("完整档：表情码清单摆出来（非空）", len(_full) > 0, "%d 字" % len(_full))
ck("省 token 档：不摆（空）", _save == "", "%d 字" % len(_save))
ck("★ 反向锚：两档结果确实不同（否则开关没用）", _full != _save)

print()
print("── C. R4：工具表说明收成第一句 ──")
from agent import tools as T  # noqa: E402
_demo = [{"name": "x", "description": "第一句说明。第二句更长更细的补充说明，属可省的那部分。",
          "parameters": {"type": "object", "properties": {}}}]
_slim = T.slim_defs(_demo)
ck("收短了", len(_slim[0]["description"]) < len(_demo[0]["description"]),
   "%d → %d" % (len(_demo[0]["description"]), len(_slim[0]["description"])))
ck("只留到第一个句末标点", _slim[0]["description"] == "第一句说明。", repr(_slim[0]["description"]))
ck("★ 不就地改原对象（浅拷贝，原 list 不受影响）", _demo[0]["description"].startswith("第一句说明。第二句"))
ck("参数说明**不动**（填参数靠它，动了就真掉效果）", "parameters" in _slim[0])
ck("★ 反向锚：slim_defs 不在 visible_defs 里被调用（两者性质不同，不许合并）",
   "slim_defs" not in open(os.path.join(ROOT, "agent", "tools.py"), encoding="utf-8")
   .read().split("def visible_defs")[1].split("def ")[0])

print()
print("── D. R5：工具协议「少走一轮」 ──")
_c, _p = _patch(False)
_proto_full = _p._tool_protocol()
_c, _p = _patch(True)
_proto_save = _p._tool_protocol()
ck("完整档：**没有**这条引导", "少走一轮" not in _proto_full)
ck("省 token 档：有这条引导", "少走一轮" in _proto_save)
ck("★ 反向锚：完整档那 7 条**一条不少**（加档位不许删原有规则）",
   all(x in _proto_full for x in ("【工作方式", "【分条】", "【绝不报告内部故障】")))

print()
print("── E. R3：翻记录上限 与 网页回执头尾裁剪 ──")
_ts = open(os.path.join(ROOT, "agent", "tools.py"), encoding="utf-8").read()
ck("get_recent_messages 的 100→50 有开关分支",
   "_cap = 100" in _ts and "_cap = 50" in _ts)
ck("web_fetch 有头尾裁剪分支（头 6000 + 尾 3000）",
   "_head, _tail = 6000, 3000" in _ts)
ck("★ 头尾**都留**（只留头会漏末尾结论 ⇒ 更掉效果）",
   "body[-_tail:]" in _ts and "body[:_head]" in _ts)
ck("★ 两处都只在 token_saver() 为真时生效", _ts.count("token_saver") >= 2)

print()
print("── F. 不碰发送链（项目红线）──")
_w = open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
ck("wechat.py 里没有 token_budget / token_saver", "token_budget" not in _w and "token_saver" not in _w)
_srcs = open(os.path.join(ROOT, "agent", "prompt.py"), encoding="utf-8").read()
ck("prompt.py 里没有把 send/发送 逻辑挂到档位上（档位只管提示词与工具表）",
   "token_saver" not in _srcs.split("def _tool_protocol")[1].split("def ")[0]
   or "send_message" not in _srcs.split("if _token_saver():")[-1][:200])

print()
print("== 两套档位判据：%d 通过 / %d 失败 ==" % (OK, len(BAD)))
if BAD:
    print("失败项：")
    for b in BAD:
        print("  -", b)
sys.exit(1 if BAD else 0)
