# -*- coding: utf-8 -*-
"""发送三态 + 判据可用性自检 判据（2026-09-14，由测机报告推动）。

背景（新机器 微信 4.1.13.65 × 适配层 1.2.2.2 实测）：投递**真的发出去了**（用户截图 + 4.x 活库 -wal 写入为证），
但 `WeChatDB.master_key is None` ⇒ `get_messages()` **不报错、静默返回旧数据**（filehelper 反复给同一条
09-13 的老消息，轮询 63 秒不变）⇒ "投递成功"被判成"发送失败"（假失败），进而会污染版本能力矩阵。

本判据钉住三件事：
  ① 三态 `Verdict`：`ok` 真、`sent_unverified`/`not_sent` **假**（未证实绝不当成功，fail-closed 照旧），
     而且它是 str 子类 ⇒ 老调用点 `ok, why = f(...)` 不解包失败、`ok == "sent_unverified"` 可机器判读；
  ② `db_alive()`：master_key=None / 回读落后活库 -wal / 读了抛异常 ⇒ 一律判"判据不可用"；
  ③ 三个发送入口在"回读没等到新行"时**必须先问 db_alive**：判据不可用 ⇒ `V_UNVERIFIED`，不许写"发送失败"。
用法：py -3 scripts/send_verdict_selftest.py
"""
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from agent.wechat import WeChatAdapter, Verdict, V_OK, V_UNVERIFIED, V_NOT_SENT  # noqa: E402

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


print("── A. 三态语义（未证实绝不当成功）──")
ok("V_OK 为真", bool(V_OK) is True)
ok("V_UNVERIFIED **为假**（关键：别把「判据不可用」当成功）", bool(V_UNVERIFIED) is False, repr(str(V_UNVERIFIED)))
ok("V_NOT_SENT 为假", bool(V_NOT_SENT) is False)
ok("是 str 子类 ⇒ 老调用点 `ok, why = f(...)` 仍可解包", issubclass(Verdict, str))
ok("可机器判读（字符串比较）", V_UNVERIFIED == "sent_unverified" and str(V_OK) == "ok")
ok("三个常量互不相等", len({str(V_OK), str(V_UNVERIFIED), str(V_NOT_SENT)}) == 3)


class _FakeDB:
    def __init__(self, rows=None, err=None, master_key="k", keys=None, bad_keys=()):
        self.master_key = master_key
        self._rows = rows or []
        self._err = err
        # 缓存密钥（per-file）：库靠它们解密，**能过页1校验就说明密钥是对的**
        self._keys = dict.fromkeys(keys if keys is not None else ["message_0.db"], 1)
        self._bad = set(bad_keys)

    def _key_works(self, rel):
        return rel not in self._bad

    def get_messages(self, chat_id, limit=1):
        if self._err:
            raise self._err
        return self._rows[:limit]


def _mk(rows=None, err=None, master_key="k", wal=None, keys=None, bad_keys=()):
    ad = WeChatAdapter.__new__(WeChatAdapter)      # 不跑 __init__（那会连微信）
    ad._db = _FakeDB(rows=rows, err=err, master_key=master_key, keys=keys, bad_keys=bad_keys)
    ad._newest_wal_mtime = (lambda: wal) if wal is not None else (lambda: 0.0)
    return ad


print("── B. db_alive：判据可用性自检 ──")
# 2026-09-16 改口径：`master_key=None` **不再是**判不可用的理由——它是**常态**（库只在"内存扫描"
# 那层成功时才给 master_key 赋值，走缓存密钥时一直是 None），只要缓存密钥能过页1 HMAC 校验，
# 回读就是可信的（本机实测：None + 20 把密钥全过 + 真读到最新消息）。对面 r22 核心②的"未证实"
# 正是老口径（`if mk is None: return False`）造成的。
a = _mk(rows=[{"create_time": int(time.time())}], master_key=None)
alive, why = a.db_alive("filehelper")
ok("master_key=None 但有可用缓存密钥 ⇒ 判可用（本机常态，不再误报）", alive is True, why[:70])

a = _mk(rows=[{"create_time": int(time.time())}], master_key=None, keys=[], wal=None)
alive, why = a.db_alive("filehelper")
ok("既没主密钥、又没有可用缓存密钥 ⇒ 判不可用", alive is False and "缓存密钥" in why, why[:70])

a = _mk(rows=[{"create_time": int(time.time())}], master_key=None, keys=["message_0.db"],
        bad_keys=("message_0.db",))
alive, why = a.db_alive("filehelper")
ok("缓存密钥过不了页1校验 ⇒ 同样算不可信", alive is False, why[:70])

a = _mk(rows=[{"create_time": int(time.time())}], master_key="k", wal=time.time() - 600)
alive, why = a.db_alive("filehelper")
ok("活库最近没在写 + 能读 ⇒ 判可用", alive is True, why[:70])

a = _mk(rows=[{"create_time": int(time.time())}], master_key="k", wal=time.time())
alive, why = a.db_alive("filehelper")
ok("活库刚刚还在写、而该会话读不到新行 ⇒ 判不可信（未证实，不判真失败）",
   alive is False and "刚刚还在写" in why, why[:80])

a = _mk(err=RuntimeError("db boom"))
alive, why = a.db_alive("filehelper")
ok("读取出异常 ⇒ fail-closed 判不可用（不是「可用」）", alive is False and "异常" in why, why[:60])

a = _mk(rows=[], master_key="k")
alive, why = a.db_alive("filehelper")
ok("能读但当前无消息 ⇒ 判可用（不误报）", alive is True, why[:40])

print("── C. 源码层：三个发送入口都必须先问 db_alive ──")
SRC = open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
ok("存在 V_UNVERIFIED 分支（文本）", SRC.count("return V_UNVERIFIED") >= 3, "共 %d 处" % SRC.count("return V_UNVERIFIED"))
_SV = SRC[SRC.index("def send_text_posted("):]
_SV = _SV[:_SV.index("def send_image_posted(")]
_ALIVE = _SV.find("_alive, _why_alive = self.db_alive(chat_id)")
_u = _SV.find("return V_UNVERIFIED")
_n = _SV.find("return V_NOT_SENT")
ok("文本链路：回读失败时先 db_alive，再决定 未证实/失败",
   0 <= _ALIVE < _u and 0 <= _ALIVE < _n,
   "db_alive@%d 未证实@%d 失败@%d" % (_ALIVE, _u, _n))
ok("发图链路同上", "已投递粘贴并点了发送，但**判据不可用**" in SRC)
ok("发文件链路同上", "已走完对话框与发送，但**判据不可用**" in SRC)
ok("成功分支已改回 V_OK（机器可判读）", "return V_OK, \"投递发送成功" in SRC and "return V_OK, \"投递发文件成功" in SRC)

print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
