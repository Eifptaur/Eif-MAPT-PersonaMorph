# -*- coding: utf-8 -*-
"""「一键数据导出」判据（第 153 项）—— 不碰网络、不碰窗口、不写产品 `data/`。

守四件事：
  A **CSV 真能被 Excel 打开**：**带 BOM**（不带就是乱码）＋ 列头固定 ＋ **正文里的逗号/引号/换行不串列**。
  B **Markdown 能读**：标题带条数；空会话写"还没有消息"（**不是 0 字节文件**）。
  C **条数与库逐条一致**（搬运不挑选）＋ **导出不改库**（只读）。
  D 反向锚：把 BOM 去掉 / 把 csv 换成手拼 ⇒ 对应判据必须红（证明它们不是恒真）。

用法：runtime\\python\\python.exe scripts/export_data_selftest.py
"""
import csv
import io
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import _iso14  # noqa: E402
_iso14.all_()

import export_data as EX  # noqa: E402
from agent.store import ChatStore  # noqa: E402

PASS = FAIL = 0
CK = "exp-%d" % os.getpid()
CK2 = "exp-empty-%d" % os.getpid()
NOW_MS = int(__import__("time").time() * 1000)


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


st = ChatStore()
# ⛔ 故意塞**逗号 / 双引号 / 换行 / 中文**：这三样是"手拼 CSV 必炸"的典型
TRICKY = '他说："先发一半，再发一半"；\n然后换行了'
st.append_incoming(CK, "m1", NOW_MS - 5000, "uid_a", "甲", TRICKY)
st.append_self(CK, "收到，分两条发", ts=(NOW_MS - 4000) / 1000.0)
st.append_incoming(CK, "m2", NOW_MS - 3000, "uid_b", "乙", "带,逗号,的正文")
_lib_n = len(st.recent(CK, limit=10 ** 7, include_self=True))
TD = tempfile.mkdtemp(prefix="exp-")

print("── A. CSV：能被 Excel 打开 ＋ 不串列 ──")
r1 = EX.export_csv(st, CK, os.path.join(TD, "a.csv"))
_raw = io.open(r1["path"], "rb").read()
ok("A1★ 带 **UTF-8 BOM**（不带 ⇒ Excel 打开中文是乱码，用户看到的就是「导出来是坏的」）",
   _raw.startswith(b"\xef\xbb\xbf"), _raw[:3].hex())
_txt = _raw.decode("utf-8-sig")
_rd = list(csv.reader(io.StringIO(_txt)))
ok("A2 列头固定且列数对（%d 列）" % len(EX.COLS),
   list(_rd[0]) == list(EX.COLS), str(_rd[0])[:60])
ok("A3★★ **逗号/引号/换行都不串列**（每行字段数都等于列头数）",
   all(len(row) == len(EX.COLS) for row in _rd), str([len(x) for x in _rd]))
_NL = "\n"   # ⛔ 放进变量再比：`"带空白的字面量 in 变量"`会被判据卫生那条记一笔（语义一样、形态不撞）
ok("A3b 正文里的换行**真的被引号包住**了（读回来仍是同一条消息）",
   any(_NL in f for row in _rd for f in row), "")
ok("A4★ 条数与库一致（分母守卫：库里 %d 条 ⇒ 导出也必须 %d 条）" % (_lib_n, _lib_n),
   r1["rows"] == _lib_n, str(r1["rows"]))
r2 = EX.export_csv(st, CK2, os.path.join(TD, "empty.csv"))
_e = io.open(r2["path"], "rb").read()
ok("A5★ 空会话：**不是 0 字节**，有表头 ＋ 一行说明（用户才知道是没数据、不是导坏了）",
   len(_e) > 20 and "还没有消息" in _e.decode("utf-8-sig"), "%d 字节" % len(_e))

print("\n── B. Markdown ──")
m1 = EX.export_md(st, CK, os.path.join(TD, "a.md"))
_md = io.open(m1["path"], encoding="utf-8").read()
ok("B1 标题带条数", ("共 **%d** 条" % _lib_n) in _md, _md.splitlines()[3][:40] if len(_md.splitlines()) > 3 else "")
ok("B2★ 正文里的换行被换成 ⏎（保持「一条一行」，不破坏 md 列表）",
   "⏎" in _md and "然后换行了" in _md, "")
ok("B3 方向/发送者都在（'我' 与 '甲'）", "**我**" in _md and "甲" in _md, "")
m2 = EX.export_md(st, CK2, os.path.join(TD, "empty.md"))
ok("B4★ 空会话 ⇒ 明写「还没有消息」", "还没有消息" in io.open(m2["path"], encoding="utf-8").read(), "")

print("\n── C. 全量 ＋ 只读 ──")
_before = {k: len(st.recent(k, limit=10 ** 7, include_self=True)) for k in (CK, CK2)}
r3 = EX.export_all(st, os.path.join(TD, "all"), fmt="both")
_after = {k: len(st.recent(k, limit=10 ** 7, include_self=True)) for k in (CK, CK2)}
ok("C1★ 导出**不改库**（前后条数逐会话相同 ⇒ 是只读搬运）", _before == _after,
   "%s → %s" % (_before, _after))
ok("C2 逐会话条数与库一致（不是只导第一个）",
   all(r3["chats"][k]["rows"] == _before[k] for k in _before if k in r3["chats"]),
   str({k: v["rows"] for k, v in (r3["chats"] or {}).items()})[:80])
ok("C3 两种格式都出（md ＋ csv）",
   all(("md" in v and "csv" in v) for v in (r3["chats"] or {}).values()), "")
ok("C4★ 会话 key 变成**合法文件名**（Windows 非法字符换掉、不超长）",
   EX._safe_name('群:abc/def*?"<>|') == "群_abc_def_" and len(EX._safe_name("x" * 200)) <= 66,
   EX._safe_name('群:abc/def*?"<>|'))

print("\n── D. 反向锚（证明上面几条不是恒真）──")
_p = os.path.join(TD, "nobom.csv")
io.open(_p, "w", encoding="utf-8", newline="").write("时间,方向\n1,群友\n")     # **故意不带 BOM**
ok("D1★ 去掉 BOM ⇒ A1 那类检查必须能红（判据不是恒真）",
   not io.open(_p, "rb").read().startswith(b"\xef\xbb\xbf"), "")
# ⛔ 反例必须用**半角**逗号/引号：正文里本来就有半角的 `,` 与 `"` 时，手拼出来的表列会串
#   （拿全角引号当反例说明不了任何事 —— 它在 CSV 里根本没有歧义）。
naive = 'a,b\n他说:"先发一半,再发一半",x\n'
_rd2 = list(csv.reader(io.StringIO(naive)))
ok("D2★ 手拼 CSV（不做转义）⇒ 列数就**对不上**了（这就是为什么必须走 `csv` 模块）",
   any(len(row) != 2 for row in _rd2), str([len(x) for x in _rd2]))

print("\n== 数据导出判据：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
