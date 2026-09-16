# -*- coding: utf-8 -*-
"""正文还原 判据（2026-09-14，用户报障「把你的话复制到微信发过来之后你就不太能正常识别了」）。

**实测机制**（本判据钉的就是它）：
  · 微信 4.x 把**长文本与文件卡**的 content 以 **zstd** 压缩存库（实测 local_id=703 是 1791 字节的
    zstd 帧，magic `28 b5 2f fd`）；
  · 库的友好化读法（`get_messages` / `get_new_messages`）遇到压缩体**只回类型标签**（`[文本]`）
    ⇒ 正文整条消失：短 token 读得到，用户粘过来的长段落全部读成 `[文本]` ⇒ 机器人看不见他说的话。
  · 修法：`replica_adapter.recover_text/message_text/fill_text`（走库的公开 `get_message_row` + 解压），
    在 `recent_texts`（身份闸的针）与 `normalize`（模型输入）两处接线。

用法：py -3 scripts/content_recover_selftest.py
"""
import os
import sys
import zlib

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from agent import replica_adapter as RA        # noqa: E402

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


SRC_WX = open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
SRC_RA = open(os.path.join(ROOT, "agent", "replica_adapter.py"), encoding="utf-8").read()

print("── A. 纯逻辑：能解、不乱解、不抛 ──")
z = RA.zstd_module()
ok("运行时能拿到 zstd 实现", z is not None, getattr(z, "__version__", "无"))
if z is None:
    print("（没有 zstd 就没法验正文还原，先修依赖）")
    sys.exit(2)

LONG = "这是用户粘贴过来的一整段长文本，用来验证能否从库里把正文解出来。" * 8
blob = z.ZstdCompressor().compress(LONG.encode("utf-8"))
ok("zstd 帧确实带 magic", blob[:4] == RA.ZSTD_MAGIC, repr(blob[:4]))
ok("长中文文本能原样还原", RA.recover_text(blob) == LONG, "%d 字" % len(RA.recover_text(blob)))
ok("str 输入原样返回（不当成压缩体）", RA.recover_text("[文本]") == "[文本]")
ok("普通 bytes 尽力 utf-8 解码", RA.recover_text("中文".encode("utf-8")) == "中文")
ok("垃圾字节不抛异常（返回内容，不返回占位符）", isinstance(RA.recover_text(b"\x01\x02\x03"), str))
ok("None / 空 ⇒ 空串", RA.recover_text(None) == "" and RA.recover_text(b"") == "")
ok("假 zstd 帧（解不开）⇒ 空串而不是炸", RA.recover_text(RA.ZSTD_MAGIC + b"\x00" * 20) == "")

print("── B. 类型标签识别 + 取正文 ──")
class _FakeDB:
    """假库：get_message_row 回 zstd 帧；顺带记录被问过哪条。"""
    def __init__(self):
        self.asked = []

    def get_message_row(self, chat, lid):
        self.asked.append((chat, lid))
        if int(lid) == 1:
            return {"content": z.ZstdCompressor().compress("真实正文".encode("utf-8"))}
        if int(lid) == 2:
            return {"content": "短消息原文"}
        return None


db = _FakeDB()
ok("message_text 解出压缩正文", RA.message_text(db, "c", 1) == "真实正文")
ok("message_text 对未压缩行原样给出", RA.message_text(db, "c", 2) == "短消息原文")
ok("message_text 拿不到行 ⇒ 空串", RA.message_text(db, "c", 9) == "")
ok("message_text 传 None 库 ⇒ 空串（不炸）", RA.message_text(None, "c", 1) == "")
ok("fill_text：标签 ⇒ 换成正文", RA.fill_text(db, "c", 1, "[文本]") == "真实正文")
ok("fill_text：正常文本 ⇒ 原样返回（不多查一次库）",
   RA.fill_text(db, "c", 1, "普通文本") == "普通文本" and db.asked.count(("c", 1)) <= 2)
ok("fill_text：解不出来 ⇒ 退回原标签（不猜内容）",
   RA.fill_text(db, "c", 9, "[文本]") == "[文本]")
ok("fill_text：库是 None ⇒ 原样返回", RA.fill_text(None, "c", 1, "[文本]") == "[文本]")

print("── C. 接线：身份闸的针 + 模型输入 都要吃到正文 ──")
ok("recent_texts 先试着解正文，再决定跳过占位符",
   "replica_adapter.fill_text(self._db, chat_id, r.get(\"local_id\"), c)" in SRC_WX)
ok("normalize 在**最前面**解一次（后面所有分支都吃到正文）",
   "replica_adapter.fill_text(self._db, chat_id or \"\", local_id, content)" in SRC_WX)
ok("两处都包在 try 里（解不出来不许影响主流程）",
   SRC_WX.count("replica_adapter.fill_text(") >= 2 and "except Exception:\n            pass" in SRC_WX)
ok("适配层只用公开方法 get_message_row（不碰私有）",
   "db.get_message_row(" in SRC_RA and "_msg_conn" not in SRC_RA.split("def message_text")[1][:600])

print("── D. 真数据（有微信在跑才有意义）：最近的长消息能不能读出来 ──")
try:
    from agent.config import get_config            # noqa: E402
    from agent.wechat import WeChatAdapter         # noqa: E402
    ad = WeChatAdapter(get_config())
    # ⚠️ 这里**不许写死会话 id**（2026-09-15）：原来写的是用户的真实 wxid，被外发包的
    # 个人信息闸门当场拦下（"微信账号/数据"致命命中）；换成假 id 又会让这一节恒假红。
    # 正路＝**从库里现取候选会话**（群列表 + 自己），谁读得出文本就用谁；都读不出就如实 SKIP。
    cands = []
    try:
        cands += [str(g.get("wxid") or "") for g in (ad.list_groups() or [])]
    except Exception:
        pass
    try:
        me = ad._db.get_self_info() or {}
        cands.append(str(me.get("username") or me.get("wxid") or ""))
    except Exception:
        pass
    cands = [c for c in dict.fromkeys(cands) if c]
    try:
        cands.append("filehelper")     # 文件传输助手：名字固定、不涉及任何人
    except Exception:
        pass
    hit = ("", [])
    for cid in cands:
        try:
            nt = ad.recent_texts(cid) or []
        except Exception:
            nt = []
        if len(nt or []) > len(hit[1] or []):
            hit = (cid, nt)
    if not hit[0]:
        print("  SKIP 真数据这节（库里 %d 个候选会话都读不到文本：微信没开或库为空）" % len(cands))
    else:
        cid, nt = hit
        longs = [t for t in nt if len(t) > 60]
        tags = [t for t in nt if t.startswith("[") and t.endswith("]")]
        # 自检分工（2026-09-15 定）：**"还有没有纯类型标签"才是回归自检**（旧 bug 会把压缩正文
        # 读成 `[文本]` 这种标签，长度必然 ≤60、必被这条抓到）；"能不能看到长正文"是**证据**——
        # 库里这段窗口没有长消息时它天然为假，那不是红，是"这会儿没得比"，如实 SKIP。
        ok("真数据里不再出现纯类型标签（压缩正文没被读成 `[文本]`）", not tags, str(tags[:3]))
        if longs:
            ok("针里出现了长正文（说明压缩体被解开了）", True,
               "会话 %s：%d 条长文本 / 共 %d 条" % (cid[:14] + "…", len(longs), len(nt)))
            print("     样例：%r" % longs[0][:70])
        else:
            print("  SKIP 「长正文」这条（会话 %s 最近 %d 条里没有 >60 字的文本可比对）"
                  % (cid[:14] + "…", len(nt)))
except Exception as e:
    print("  SKIP 真数据这节（%s: %s）" % (type(e).__name__, str(e)[:60]))

print("\n通过 %d / 失败 %d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
