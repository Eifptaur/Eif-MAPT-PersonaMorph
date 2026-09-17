# -*- coding: utf-8 -*-
"""**内容级身份闸**的只读诊断探针（2026-09-16，为跨机需求②「内容级闸的 OCR 口径」做）。

用途：在对面的复现机上跑一次，把"闸为什么判否"摊开——
  · 目标会话最近几条**文本针**是什么（`recent_texts`）；
  · 聊天区**读到了多少字**、前 40 字长什么样（`pane_text`）；
  · 每条针的**最好匹配**（最长命中片段长度 + difflib 相似度）⇒ 一眼分清
    「**根本没信号**」（命中 0 字、相似度极低）与「**信号被阈值判掉**」（命中 ≥8 字但整条不匹配）；
  · 闸门最终结论（`chat_identity_ok`）；
  · 把抓到的画面存到 `报告\identity_probe-<时间戳>.png`（**定位失败时这张图就是最重要的证据**）。

**全程只读**：不点、不发、不动鼠标、不抢前台；只抓图 + 读库 + OCR。

用法（在待查的那台机器上）：
    runtime\\python\\python.exe scripts\\identity_probe.py [chat_id]
chat_id 省略时默认用环境变量 `PM_CHAT_ID`，再不行就用文件传输助手 `filehelper`。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from agent import chat_ocr as co                     # noqa: E402


def main():
    chat_id = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("PM_CHAT_ID") or "filehelper")
    print("要诊断的会话 id =", chat_id)
    from agent.wechat import WeChatAdapter
    ad = WeChatAdapter()
    try:
        needles = ad.recent_texts(chat_id) or []
    except Exception as e:
        needles = []
        print("recent_texts 异常：", type(e).__name__, str(e)[:120])
    print("\n[针] 目标会话最近几条可作文本的比对内容：共 %d 条" % len(needles))
    for i, nd in enumerate(needles[:5]):
        print("   %d) %r" % (i + 1, str(nd)[:60]))

    gui = ad._get_gui()
    img = co.capture_best(gui=gui, frames=3)
    print("\n[抓图] 渲染区画面 =", (img.size if img is not None else None))
    if img is None:
        print("   ⇒ 抓不到画面：最小化/被遮挡时 PrintWindow 会失效（这一步就得换 WGC，见跨机建议④）")
        return
    outdir = os.path.join(ROOT, "报告")
    os.makedirs(outdir, exist_ok=True)
    png = os.path.join(outdir, "identity_probe-%s.png" % time.strftime("%Y%m%d-%H%M%S"))
    img.save(png)
    print("   画面已存：%s（定位失败时把这张图发回来）" % png)

    pane = co.pane_text(img, limit=400)
    print("\n[聊天区 OCR] 读到 %d 字；前 40 字 = %r" % (len(pane), pane[:40]))
    print("   归一化后 %d 字" % len(co.norm_alnum(pane)))

    print("\n[逐针对账]（命中片段长度 / difflib 相似度 / 命中片段）")
    for i, nd in enumerate(needles[:5]):
        n_len, ratio, frag = co.best_partial(pane, str(nd))
        print("   %d) 最长命中 %2d 字 · 相似度 %.3f · 片段 %r   ← %r"
              % (i + 1, n_len, ratio, frag[:16], str(nd)[:24]))
    if needles:
        print("\n怎么读这几行：")
        print("   · 全部『最长命中 0~3 字、相似度极低』 ⇒ **根本没有信号**（聊天区不是这个会话，或抓错区域）")
        print("   · 有『最长命中 ≥8 字、相似度 0.4~0.7』⇒ **信号在、被阈值判掉了**（归一化/窗口要调）")

    try:
        ok, why = ad.chat_identity_ok(chat_id, gui=gui)
        print("\n[闸门结论] chat_identity_ok =", ok)
        print("   说明：%s" % str(why)[:300])
    except Exception as e:
        print("\n[闸门结论] 异常：", type(e).__name__, str(e)[:120])

    # ── 「现在开着的到底是谁」三条只读证据（2026-09-18 加）────────────────────────────
    # 为什么加：拍摄现场的真实报障都落在这里 ——「图片他拿到了，但是又没有发给我」的日志原文是
    # 「当前会话 OCR=''（目标 '演示'）· **绿底带在 y=120~217（占比 0.99）但那一行名字 OCR 读不出**
    #  （白字绿底）」⇒ 名字档 ① 假阴性；而**清空过聊天记录**的会话连内容档也一起瞎（库里没行）。
    # ⇒ 一次跑完把三条证据的**原始读数**摊开：绿底行名字 / 会话头标题带 / 纯屏幕兜底档。
    print("\n[现在开着的到底是谁] 三条只读证据（要判「为什么拒发」就看这里）")
    want = ad.display_name(chat_id) or chat_id
    print("   目标显示名 want = %r" % want)
    try:
        got, why_got = ad.current_chat_name(gui=gui)
        print("   ① 绿底高亮行名字 OCR = %r   · 依据：%s" % (got, str(why_got)[:120]))
    except Exception as e:
        print("   ① 绿底高亮行名字 OCR 异常：%s" % str(e)[:100])
    try:
        _tt = co.header_text(gui=gui)
        print("   ② 会话头标题带 OCR   = %r   · 与目标匹配 = %s"
              % (_tt, (co.matches(_tt, want) if _tt else False)))
        try:
            _hb = co.header_box(co.capture_best(gui=gui, frames=1) or img)
            print("      标题带像素矩形 = %s（左沿靠 detect_pane_left 现算）" % (_hb,))
        except Exception:
            pass
    except Exception as e:
        print("   ② 会话头标题带 OCR 异常：%s" % str(e)[:100])
    try:
        _so, _so_why = ad._screen_only_identity(chat_id, gui=gui, name=want)
        print("   ③ 纯屏幕兜底档（本机新增）= %s · %s" % (_so, str(_so_why)[:140]))
    except Exception as e:
        print("   ③ 纯屏幕兜底档异常：%s" % str(e)[:100])
    try:
        _ok_open, _why_open = ad.chat_is_open(chat_id, gui=gui)
        print("   [名字档合成结论] chat_is_open = %s · %s" % (_ok_open, str(_why_open)[:140]))
    except Exception as e:
        print("   [名字档合成结论] 异常：%s" % str(e)[:100])
    # 消息表在不在（**清空聊天记录会把整张 `Msg_<md5>` 表删掉** ⇒ 回读/回声/内容档全瞎）
    try:
        _conns = ad._msg_conns(chat_id)
        print("\n[消息表] `Msg_<md5>` 命中 %d 个分片连接 ⇒ %s"
              % (len(_conns), "在（有库可比对）" if _conns else
                 "**不在**（用户清空过聊天记录 ⇒ 内容级证据拿不到，只能靠纯屏幕证据）"))
    except Exception as e:
        print("\n[消息表] 查询异常：%s" % str(e)[:100])
    try:
        _alive, _why_alive = ad.db_alive(chat_id)
        print("[DB 探活] db_alive = %s · %s" % (_alive, str(_why_alive)[:120]))
    except Exception as e:
        print("[DB 探活] 异常：%s" % str(e)[:100])
    try:
        from agent import window_borrow as wb
        wb.restore("探针结束")
    except Exception:
        pass


if __name__ == "__main__":
    main()
