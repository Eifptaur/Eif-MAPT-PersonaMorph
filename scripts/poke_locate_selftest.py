# -*- coding: utf-8 -*-
"""第 22 条判据：拍一拍**落点**必须来自"运行时检测到的头像方块"（不需要微信在跑）。

跑法： py -3 scripts\\poke_locate_selftest.py      退出码 0=全过 / 1=有失败

为什么要这条判据（2026-09-18 现场）：
  机器人右键落在渲染 (434,423)，而真头像方块是 x 360..413 ⇒ 落进**气泡** ⇒ 弹的是消息菜单
  （实测读到 撤销/放大阅读/翻译/转发/收藏，**没有「拍一拍」**）⇒ 回拍一直失败。
  434 的来源＝公式 `right_pane_left(262) + 0.185×931`——即"彩色饱和度"头像判据认不出深色头像后
  的**猜点**。⇒ 这条判据同时钉三件事：①方块要能量到；②老的那个点必须被判**不合格**；
  ③代码里不许再出现"认不出就猜点"的路（气泡兜底 / 0.185 公式落点）。

夹具： scripts/fixtures/poke_render_1193x891.png（本机真窗口实拍一帧，1193×891，渲染区相对）。
      ⛔ 作者口径（记忆 0mu60w7k）：夹具只当**回归夹具**，**不许把里面的坐标写死进产品代码**。
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from PIL import Image                          # noqa: E402

from agent.wechat import WeChatAdapter         # noqa: E402

FIX = os.path.join(ROOT, "scripts", "fixtures", "poke_render_1193x891.png")
WECHAT_PY = os.path.join(ROOT, "agent", "wechat.py")

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


def centers(blocks):
    return sorted(((b[0] + b[2]) // 2, (b[1] + b[3]) // 2) for b in blocks)


def near(a, b, tol=3):
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def func_body(src, name):
    """切出某个方法体（**按起止锚点**，不用"后面 N 字符"——那种扫法会误吃隔壁代码）。"""
    i = src.find("    def %s(" % name)
    if i < 0:
        return None
    ends = [x for x in (src.find("\n    def ", i + 12), src.find("\n    # ──", i + 12)) if x > 0]
    return src[i:min(ends)] if ends else src[i:]


def code_of(src, name):
    """方法体里**去掉整行注释**后的代码 —— 静态断言必须只看代码。

    ⚠️ 这条是被自己绊出来的：解释性注释里写了 `gui.ocr(...)`/`0.185`，判据就把注释当代码判红。
    同类教训在项目里已有过一次（A11 取样窗口越界扫进隔壁合法代码 ⇒ 假红）。
    """
    body = func_body(src, name) or ""
    return "\n".join(ln for ln in body.splitlines() if not ln.strip().startswith("#"))


def main():
    if not os.path.exists(FIX):
        print("✘ 夹具不在：%s" % FIX)
        print("  这条判据**没有通过**（缺夹具＝没验证，不是跳过）")
        return 1
    img = Image.open(FIX).convert("RGB")
    print("夹具 = %s  尺寸 %s" % (os.path.basename(FIX), img.size))

    # ① 方块要能量到（本机实拍真值：4 个 54×54；左侧＝对方、右侧＝自己）
    t0 = time.time()
    blocks = WeChatAdapter._avatar_blocks(img, 331)
    dt = time.time() - t0
    print("  检出 %d 个方块，耗时 %.2fs" % (len(blocks), dt))
    for b in blocks:
        print("    bbox x %d..%d y %d..%d  (%dx%d)" % (b[0], b[2], b[1], b[3], b[2] - b[0] + 1, b[3] - b[1] + 1))
    ok("① 数量=4（本例群里可见 2 人 ×2 条消息）", len(blocks) == 4, len(blocks))
    ok("① 全部是 46~62px 的方块",
       len(blocks) == 4 and all(46 <= (b[2] - b[0] + 1) <= 62 and 46 <= (b[3] - b[1] + 1) <= 62
                                for b in blocks))
    cs = centers(blocks)
    ok("① 对方（左）头像 = (386,220) / (386,419)",
       any(near(c, (386, 220)) for c in cs) and any(near(c, (386, 419)) for c in cs), cs)
    ok("① 自己（右）头像 = (1126,335) / (1126,590)",
       any(near(c, (1126, 335)) for c in cs) and any(near(c, (1126, 590)) for c in cs), cs)
    ok("① 耗时 < 5s（这条链是低频路径，但要留个量）", dt < 5.0, "%.2fs" % dt)

    # ② 阈值稳定性：diff 30/45/60/80 都必须**找到那 4 个头像**（实测 diff≤30 会多出一个
    #    右下角的淡色块，宽 71px —— 它在右侧、且不在头像列，不影响拍人；默认取 diff=45 最干净）
    TRUE4 = [(386, 220), (386, 419), (1126, 335), (1126, 590)]
    for d in (30, 45, 60, 80):
        cs_d = centers(WeChatAdapter._avatar_blocks(img, 331, diff=d))
        ok("② diff=%d 时 4 个头像全部找到" % d,
           all(any(near(c, t) for c in cs_d) for t in TRUE4), cs_d)
    ok("② 默认 diff=45 结果恰好是这 4 个（最干净的一档）",
       centers(WeChatAdapter._avatar_blocks(img, 331)) == sorted(TRUE4))

    # ③ 库给的 right_pane_left 是**过期值**（本机实测 262 vs 真值 331）⇒ 用错值也得能量到左侧头像
    bad = centers(WeChatAdapter._avatar_blocks(img, 262))
    ok("③ pane_left 用过期值 262 仍能量到左侧两个头像",
       any(near(c, (386, 220)) for c in bad) and any(near(c, (386, 419)) for c in bad), bad)

    # ④ ⭐ 回归判据：老的那个落点必须被判**不合格**（它落在气泡上）
    OLD = (434, 423)
    inside = [b for b in blocks if b[0] + 4 <= OLD[0] <= b[2] - 4 and b[1] + 4 <= OLD[1] <= b[3] - 4]
    ok("④ 旧落点 (434,423) 不在任何头像方块内（⇒ 新的归属校验会拦住它）", not inside,
       "命中了 %s" % (inside[:1] or "无"))
    # 顺带钉死它对的是哪一行：旧点的 y=423 与左侧第二块 y 393..446 同高 ⇒ 确实是"行对了、x 错了"
    ok("④ 旧落点 x 比真头像右缘 413 还右（证实是「行对、列错」）", OLD[0] > 413,
       "434 > 413")

    # ⑤ 认不出的输入必须**什么都不给**（不许猜、不许崩）
    blank = Image.new("RGB", (1193, 891), (250, 250, 250))
    ok("⑤ 纯色图 ⇒ 0 个方块（不猜）", WeChatAdapter._avatar_blocks(blank, 331) == [])
    ok("⑤ 8×8 小图不崩、返回空",
       WeChatAdapter._avatar_blocks(Image.new("RGB", (8, 8), (10, 20, 30)), 0) == [])
    ok("⑤ pane_left 越界不崩（传等于宽度）",
       WeChatAdapter._avatar_blocks(img, 99999) == [])

    # ⑥ 静态判据：代码里不许再留"认不出就猜点"的路（按方法体切，别扫隔壁）
    src = open(WECHAT_PY, encoding="utf-8").read()
    b_send = code_of(src, "_send_poke_inner")
    b_menu = code_of(src, "_verify_poke_menu_inner")
    b_loc = code_of(src, "_send_poke_locate")
    ok("⑥ 切到了三个方法体", all(x for x in (b_send, b_menu, b_loc)))
    ok("⑥ `_send_poke_inner` 里不再有气泡兜底 `_bubble_point`", "_bubble_point" not in (b_send or ""))
    ok("⑥ `_verify_poke_menu_inner` 里不再有气泡兜底", "_bubble_point" not in (b_menu or ""))
    ok("⑥ 拍一拍链里不再有 0.185 公式落点（按代码用法查，注释提名字不算）",
       "* 0.185" not in (b_send or "") and "* 0.185" not in (b_loc or ""))
    ok("⑥ 老的饱和中位数判据已移除", "def _find_avatar_center" not in src)
    ok("⑥ 定位函数里在用时序检测的头像方块", "_avatar_blocks" in (b_loc or ""))
    ok("⑥ 定位函数里不再用 `gui.ocr`（抓屏 OCR：微信被遮挡时读到的别人的像素）",
       "gui.ocr(" not in (b_loc or ""))
    ok("⑥ 定位函数改用项目加固层 `chat_ocr`（硬超时/预算/健康）",
       "chat_ocr" in (b_loc or "") and "recognize(" in (b_loc or ""))
    b_ver = code_of(src, "_verify_poke")
    ok("⑥ 拍后验证也不再用抓屏 OCR（否则会「拍上了却报没拍上」）",
       bool(b_ver) and "gui.ocr_zoomed" not in b_ver and "gui.ocr(" not in b_ver)
    ok("⑥ 定位函数里有归属校验（落点必须在方块内）", "不在任何头像方块内" in (b_loc or ""))
    ok("⑥ 定位函数里有失败原因（不许含糊返回 None）", "_poke_locate_why" in (b_loc or ""))
    ok("⑥ 没有按 x 排序 OCR 行的老 bug（`key=lambda b: b[1]`）",
       "key=lambda b: b[1]" not in (b_loc or ""))
    ok("⑥ 认人只认「文本真的对上」，对不上就如实失败（不猜是谁）",
       "不敢猜是谁" in (b_loc or ""))
    ok("⑥ 右键重试候选点被夹在方块内（旧公式候选已删）",
       "bbox[0] + 4 <= x <= bbox[2] - 4" in (b_send or ""))
    ok("⑥ 会话区左沿是**帧内现量**（库的 right_pane_left 会过期：本机 262 vs 真值 331）",
       "detect_pane_left(img)" in (b_loc or ""))
    ok("⑥ 左右分界＝**会话区中点**（用整幅中点会把别人的行判成自己的 ⇒ 定位失败）",
       "(int(pane_left) + int(rw)) // 2" in (b_loc or ""))

    # ⑦ 端到端离线回归：桩掉"抓帧"与"OCR"，跑**真的** `_send_poke_locate`
    #    桩数据＝本机真帧上实测到的 OCR 结果；坐标按**代码实际用的 crop** 反算（crop 一变也不会假红）
    from agent import chat_header as _ch_mod
    from agent import chat_ocr as _co_mod
    from agent import wechat as _wx_mod

    # 代码里的 crop 现在是 `top = max(80, (render_h-6) - 720)`（**不再调 get_input_box**）
    _IH = img.size[1]
    _TOP = max(80, (_IH - 6) - 720)
    _PL = 262
    STUB = [("@#deepseek说讠舌！", 455 - _PL, 243 - _TOP, 137, 22),   # E 的消息（真帧实测）
            ("「E」拍拍*deepseek」", 656 - _PL, 517 - _TOP, 6, 17),   # 居中拍拍提示（无头像）
            ("05：05", 274 - _PL, 149 - _TOP, 16, 11)]                # 会话列表时间（该被 x 过滤掉）
    _old_grab, _old_rec, _old_blk = _ch_mod.grab_render, _co_mod.recognize, _co_mod.blocked
    _ch_mod.grab_render = lambda gui=None, render=None, tries=12: img
    _co_mod.recognize = lambda image, timeout=None: list(STUB)
    _co_mod.blocked = lambda: ""

    class _FakeGUI:
        render_w, render_h, origin_y = 1193, 891, 0
        right_pane_left = 262

        def get_input_box(self):
            return (262, 701, 1193, 818)

    class _FakeAd:
        _norm_ocr = staticmethod(WeChatAdapter._norm_ocr)
        _avatar_blocks = staticmethod(WeChatAdapter._avatar_blocks)
        _send_poke_locate = WeChatAdapter._send_poke_locate

        def _uia_target_row_rect(self, *a, **k):
            return None

    try:
        fake = _FakeAd()
        # 正例：锚点用 E 的真消息文本 ⇒ 必须落到 E 的头像方块内
        loc = fake._send_poke_locate(_FakeGUI(), "E", ["。。。", "@群deepseek 说话！"], scroll=False)
        ok("⑦ 正例：锚点命中 ⇒ 返回头像方块中心", loc is not None and loc[:2] == (386, 220), loc)
        blk = getattr(fake, "_poke_block", None)
        ok("⑦ 正例：落点在命中的头像方块内",
           bool(loc) and bool(blk)
           and blk[0] + 4 <= loc[0] <= blk[2] - 4 and blk[1] + 4 <= loc[1] <= blk[3] - 4, blk)
        # 负例：锚点一条都对不上 ⇒ 必须如实失败（**不许**取"最下面那条"猜人）
        loc2 = fake._send_poke_locate(_FakeGUI(), "E", ["绝不存在zzz", "也不存在yyy"], scroll=False)
        why2 = getattr(fake, "_poke_locate_why", "")
        ok("⑦ 负例：锚点对不上 ⇒ 不返回任何点（不猜是谁）", loc2 is None, loc2)
        ok("⑦ 负例：失败原因写明「不敢猜是谁」", "不敢猜是谁" in (why2 or ""), why2[:60])
        # 负例：没有任何锚点（库里取不到 TA 的文本）⇒ 同样不许猜
        loc3 = fake._send_poke_locate(_FakeGUI(), "E", [], scroll=False)
        ok("⑦ 负例：没有锚点 ⇒ 不返回任何点", loc3 is None, loc3)
    finally:
        _ch_mod.grab_render, _co_mod.recognize, _co_mod.blocked = _old_grab, _old_rec, _old_blk
    _ = _wx_mod

    print("\n== 汇总：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  - " + f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
