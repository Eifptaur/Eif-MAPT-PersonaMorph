#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「微信连不上」诊断与自动重试的判据（离线；不碰真微信、不动鼠标、不弹窗）。

背景（2026-09-16 用户反馈「又有说微信连接不上的」）：产品以前对"连不上"只有一个是/否，
用户只能看到"微信未连接"，我们只能来回猜。本次两件事：
  ① `agent/wechat.py::attach_diagnosis()` —— 把接入拆成**逐步只读检查**（进程 / 装没装 /
     消息库 / 密钥 / 认出你自己的账号），每一步给证据与下一步动作；
  ② `scripts/persona_morph.py` —— 启动时用 `_attach_wechat()` 接入（失败**不抛**、把诊断记下来），
     并在监听循环里**每 10 秒重试**（老代码注释里承诺过、实际从未实现），接上后自动补目标群；
     控制台侧栏显示一行短原因（悬停看全部 steps），每条反馈的环境里自动带上这段。

本判据守住的：五步各卡一次都要报对 step/action；诊断**只读**（不许出现任何输入/窗口 API）；
三处接线（启动接入 / 循环重试 / 状态下发 / 反馈 env）不许被以后改回去。
"""
import os
import re
import sys
import types

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agent import wechat as W                    # noqa: E402

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


def _src(rel):
    return open(os.path.join(ROOT, rel), encoding="utf-8").read()


class _DB(object):
    """假消息库：只实现诊断用到的三样。"""

    def __init__(self, keys_ok=1, master=False, self_info=None,
                 account_dir="C:\\fake\\xwechat_files\\wxid_abc\\db_storage"):
        self._keys = {"message_0.db": "x", "message_1.db": "y"}
        self.master_key = "MK" if master else None
        self.account_dir = account_dir
        self._keys_ok = keys_ok
        self._self = self_info

    def _key_works(self, rel):
        return self._keys_ok > 0

    def get_self_info(self):
        return self._self


_VI_RUN = {"found": True, "path": "C:\\Program Files\\Tencent\\Weixin\\Weixin.exe",
           "version": "4.1.15.8", "supported": True, "install": {"installed": True}}
_VI_NOPROC = {"found": False, "path": "", "version": "", "supported": True,
              "install": {"installed": True, "detail": "微信已安装但没在运行（登录后本工具才能读到消息；不用重装）"}}
_VI_MISSING = {"found": False, "path": "", "version": "", "supported": True,
               "install": {"installed": False, "detail": "本机没检测到微信。"}}


def _diag(vi, db=None, err="", adapter=None):
    _saved = (W.wechat_version_info, W.wechat_install_state)
    W.wechat_version_info = lambda: dict(vi)
    W.wechat_install_state = lambda proc_found=False, proc_path="": dict(vi.get("install") or {})
    try:
        return W.attach_diagnosis(adapter=adapter, err=err, db=db)
    finally:
        (W.wechat_version_info, W.wechat_install_state) = _saved


print("── A. 全过：微信在跑 / 库打得开 / 密钥可用 / 认得出你自己 ──")
_d = _diag(_VI_RUN, db=_DB(self_info={"username": "wxid_me", "nick_name": "我"}))
ok("ok=True 且没有卡点 step", _d["ok"] and _d["step"] == "", "ok=%s step=%r" % (_d["ok"], _d["step"]))
ok("四步都给出来了（process/db_open/key/self）",
   {s["key"] for s in _d["steps"]} == {"process", "db_open", "key", "self"},
   str([s["key"] for s in _d["steps"]]))
ok("每步都带证据文字（不是光一个勾）", all(str(s.get("detail") or "").strip() for s in _d["steps"]))
ok("认出了自己的账号 ⇒ 说明里带昵称/wxid", "我" in str(_d["steps"][-1]["detail"]), _d["steps"][-1]["detail"])
ok("action=none（没什么要他做的）", _d["action"] == "none", _d["action"])

print("── B. 微信没在跑（已装）⇒ 卡在 process，叫他开微信 ──")
_b = _diag(_VI_NOPROC, db=_DB(self_info={"username": "wxid_me"}))
ok("step=process", _b["step"] == "process", _b["step"])
ok("action=start_wechat", _b["action"] == "start_wechat", _b["action"])
ok("额外给出「装没装」这一步（装了 ⇒ 那一步是过的）",
   any(s["key"] == "install" and s["ok"] for s in _b["steps"]), str([s["key"] for s in _b["steps"]]))
ok("原因里点明是进程没找到", "没找到" in _b["reason"], _b["reason"])

print("── C. 微信根本没装 ⇒ 还是 process 卡点，但 action 必须落到 install ──")
_c = _diag(_VI_MISSING, db=_DB())
ok("action=install（不许让他去开一个不存在的微信）", _c["action"] == "install", _c["action"])
ok("「装没装」那一步是不过的", any(s["key"] == "install" and not s["ok"] for s in _c["steps"]))

print("── D. 拿不到数据库密钥 ⇒ 卡在 key，action=relogin ──")
_e = _diag(_VI_RUN, db=_DB(keys_ok=0, master=False, self_info={"username": "wxid_me"}))
ok("step=key", _e["step"] == "key", _e["step"])
ok("action=relogin（密钥要从微信进程里取 ⇒ 让他重登/重启微信）", _e["action"] == "relogin", _e["action"])
ok("原因里点明是密钥", "密钥" in _e["reason"], _e["reason"])
ok("self 那一步仍在列表里（诊断走到底，不提前掐）", any(s["key"] == "self" for s in _e["steps"]))

print("── E. 只有缓存密钥、主密钥为空 ⇒ **照样算可用**（本机常态，别误报）──")
_f = _diag(_VI_RUN, db=_DB(keys_ok=2, master=False, self_info={"username": "wxid_me"}))
ok("ok=True（主密钥为空不算卡点）", _f["ok"] is True, _f["reason"])
ok("说明里交代了「主密钥为空但有 N 把缓存密钥可用」这回事",
   "缓存密钥" in str(_f["steps"][2]["detail"]), _f["steps"][2]["detail"])

print("── F. 消息库打不开 ⇒ 卡在 db_open，action=retry，且不再假装往下走 ──")
_saved_mod = sys.modules.get("wechatauto")
_boom = types.ModuleType("wechatauto")


def _raise(*a, **k):
    raise RuntimeError("cannot locate account dir")


_boom.WeChatDB = _raise
sys.modules["wechatauto"] = _boom
try:
    _g = _diag(_VI_RUN)          # 不传 db ⇒ 会去构造 WeChatDB（这里让它抛）
finally:
    if _saved_mod is not None:
        sys.modules["wechatauto"] = _saved_mod
    else:
        sys.modules.pop("wechatauto", None)
ok("step=db_open", _g["step"] == "db_open", _g["step"])
ok("action=retry", _g["action"] == "retry", _g["action"])
ok("原因里带上了异常名/信息", "RuntimeError" in _g["reason"], _g["reason"][:90])
ok("库都打不开时不再报 key/self（不许拿空气当证据）",
   not any(s["key"] in ("key", "self") for s in _g["steps"]), str([s["key"] for s in _g["steps"]]))

print("── G. 库能读、但读不出你自己的账号 ⇒ 卡在 self，action=retry，并说明它不挡读群消息 ──")
_h = _diag(_VI_RUN, db=_DB(keys_ok=1, self_info=None))
ok("step=self", _h["step"] == "self", _h["step"])
ok("action=retry", _h["action"] == "retry", _h["action"])
ok("说明里写明「不影响读群消息、但回声判据会退化」",
   ("不影响读群消息" in _h["reason"]) and ("退化" in _h["reason"]), _h["reason"])

print("── H. 接入时抛的错要拼进原因（用户回报时一条就够）──")
_i = _diag(_VI_RUN, db=_DB(self_info={"username": "wxid_me"}), err="KeyError: 'username'【KeyError】")
ok("err 进 reason", "KeyError" in _i["reason"], _i["reason"])
ok("err 单独留字段（诊断包按字段读，不靠解析句子）", _i["err"] == "KeyError: 'username'【KeyError】", _i["err"])

print("── I. 侧栏短原因：五个卡点各有短标签，未知情况有兜底 ──")
short = {k: W.attach_short_reason({"step": k}) for k in ("process", "install", "db_open", "key", "self")}
ok("五个卡点都有一行短标签且互不相同", len(set(short.values())) == 5, str(short))
ok("未知/空诊断 ⇒ 兜底「原因未知」", W.attach_short_reason(None) == "原因未知"
   and W.attach_short_reason({"step": ""}) == "原因未知", W.attach_short_reason(None))

print("── J. 诊断必须**只读**：函数体里不许出现输入/窗口 API ──")
_w = _src(os.path.join("agent", "wechat.py"))
_seg = _w[_w.index("def attach_diagnosis("):]
_seg = _seg[:_seg.index("# 卡点的**短标签**")]
_banned = [n for n in ("mouse_event", "SetCursorPos", "SendInput", "MoveWindow",
                       "SetForegroundWindow", "PostMessage", "SendMessage",
                       "ShowWindow", "keybd_event") if n in _seg]
ok("attach_diagnosis 里没有任何输入/改动窗口的调用", not _banned, str(_banned))
ok("诊断不写盘（没有 open(...'w')/json.dump 之类）",
   ("json.dump" not in _seg) and ("os.remove" not in _seg))

print("── K. 接线：启动接入 / 10 秒重试 / 状态下发 / 反馈 env（源码级，防以后改回去）──")
_pm = _src(os.path.join("scripts", "persona_morph.py"))
ok("启动接入走 _attach_wechat（失败不抛）",
   "wechat_box[0] = _attach_wechat(cfg)" in _pm and "def _attach_wechat(cfg)" in _pm)
ok("监听循环里有**重试**（老代码只有注释没有实现）",
   "wechat_box[0] is None and (time.time() - float(_ATTACH.get(\"at\") or 0)) >= 10" in _pm)
ok("接上以后把发送队列与 orchestrator 的句柄都补上",
   "sender.wechat = _wc_new" in _pm and "orch.wechat = _wc_new" in _pm)
ok("接上以后重算监听目标（否则永远监听 0 个群）",
   "groups, targets = _collect_targets(_wc_new)" in _pm and "def _collect_targets(wc)" in _pm)
ok("看门狗与定时巡检不再被传死 None（每跳现取句柄）",
   "args=(lambda: wechat_box[0],)" in _pm and "_start_timer_holiday_loop(orch, lambda: wechat_box[0])" in _pm)
ok("状态里下发 wechat_attach（控制台才看得到原因）",
   '"wechat_attach": wechat_attach_status(),' in _pm and "def wechat_attach_status()" in _pm)
_ch = _src(os.path.join("agent", "console_html.py"))
ok("控制台侧栏显示短原因 + 悬停看逐步诊断",
   "_wa.short" in _ch and "_wa.steps.map(" in _ch)
_ui = _src(os.path.join("agent", "webui.py"))
ok("提交反馈时服务端现取 attach 诊断（不信前端）",
   'self.status_provider() or {}).get("wechat_attach")' in _ui and '"attach":' in _ui)

print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
