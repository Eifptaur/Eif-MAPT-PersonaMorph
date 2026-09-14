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
    def __init__(self, rows=None, err=None, master_key="k"):
        self.master_key = master_key
        self._rows = rows or []
        self._err = err

    def get_messages(self, chat_id, limit=1):
        if self._err:
            raise self._err
        return self._rows[:limit]


def _mk(rows=None, err=None, master_key="k", wal=None):
    ad = WeChatAdapter.__new__(WeChatAdapter)      # 不跑 __init__（那会连微信）
    ad._db = _FakeDB(rows=rows, err=err, master_key=master_key)
    ad._newest_wal_mtime = (lambda: wal) if wal is not None else (lambda: 0.0)
    return ad


print("── B. db_alive：判据可用性自检 ──")
a = _mk(rows=[{"create_time": int(time.time())}], master_key=None)
alive, why = a.db_alive("filehelper")
ok("master_key=None ⇒ 判不可用（这就是测机那台的状态）", alive is False and "master_key" in why, why[:60])

a = _mk(rows=[{"create_time": int(time.time())}], master_key="k", wal=time.time())
alive, why = a.db_alive("filehelper")
ok("主密钥在 + 回读不落后 ⇒ 判可用", alive is True, why[:60])

a = _mk(rows=[{"create_time": int(time.time()) - 3600}], master_key="k", wal=time.time())
alive, why = a.db_alive("filehelper")
ok("回读落后活库 -wal 超过 300 秒 ⇒ 判不可用", alive is False and "落后" in why, why[:70])

a = _mk(err=RuntimeError("db boom"))
alive, why = a.db_alive("filehelper")
ok("读取出异常 ⇒ fail-closed 判不可用（不是「可用」）", alive is False and "异常" in why, why[:60])

a = _mk(rows=[], master_key="k")
alive, why = a.db_alive("filehelper")
ok("能读但当前无消息 ⇒ 判可用（不误报）", alive is True, why[:40])

print("── C. 源码层：三个发送入口都必须先问 db_alive ──")
SRC = open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
ok("存在 V_UNVERIFIED 分支（文本）", SRC.count("return V_UNVERIFIED") >= 3, "共 %d 处" % SRC.count("return V_UNVERIFIED"))
ok("文本链路：回读失败时先 db_alive，再决定 未证实/失败",
   "已投递，但**判据不可用**" in SRC and "return V_NOT_SENT, \"已投递但 %ds 内 DB 没等到新行" in SRC)
ok("发图链路同上", "已投递粘贴并点了发送，但**判据不可用**" in SRC)
ok("发文件链路同上", "已走完对话框与发送，但**判据不可用**" in SRC)
ok("成功分支已改回 V_OK（机器可判读）", "return V_OK, \"投递发送成功" in SRC and "return V_OK, \"投递发文件成功" in SRC)

print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
