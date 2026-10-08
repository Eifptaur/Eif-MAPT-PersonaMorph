# -*- coding: utf-8 -*-
"""OCR 成功率基准 · **30+ 条真机素材**（先测出成功率，目标 ≥90% 乃至 ≥95%）。

素材与真值来源（全部**真实屏幕像素**，无合成图）：
  · 的 3 张真机微信截图（1854×1281，150% DPI）⇒ `ocr-samples/wechat/`；
  · 真值由我**逐张读图**给定（列表 7 行 × 3 张 / 会话头 × 3 / 消息区文字若干）。
  · 行位置用**实测行距**推：绿底带在 sample1 落在第 7 行（705..802）、sample3 落在第 3 行（315..412）
    ⇒ 行距 ≈ 97px、首行顶 ≈ 121px（两张互验一致）。名字列横向取 185..470（头像右侧、时间戳左侧）。

度量口径（**与我们线上用途一致**，不是"整串完全相等"）：
  · 列表行：读到的名字里**包含**该行真值（归一化后）即算成功；`_band_name`（生产路径）与
    **直读名字列**（"还能优化多少"的上界）分别统计；
  · 会话头：`_header_read` 的读数包含真值即算成功；
  · 消息区：`recognize_dual` 读数里包含真值即算成功。

用法：
    runtime\\python\\python.exe scripts\\ocr_bench_30.py             # 基线
    runtime\\python\\python.exe scripts\\ocr_bench_30.py --optimized # 走"优化档"（ROI 名字列 + 闭运算 + CLAHE）
"""
import argparse
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from PIL import Image, ImageFilter, ImageOps # noqa: E402

from agent import chat_ocr as O # noqa: E402

_WX = os.path.join(os.path.dirname(ROOT), "dev-workspace", "persona-morph", "ocr-samples", "wechat")
S1 = os.path.join(_WX, "7bcf1579-61e9-4c80-a54a-47eb45341b53.png")
S2 = os.path.join(_WX, "0af83564-b60d-4812-aa4f-3c330c6399c0.png")
S3 = os.path.join(_WX, "6c29e9f4-2c81-4b4d-9a71-a826c521f403.png")

#: 实测行距与首行顶（两张图互验：705 = 121 + 6×97；315 = 121 + 2×97）
ROW_TOP, ROW_PITCH = 121, 97
#: 名字列（头像右侧 → 时间戳左侧）
NAME_X0, NAME_X1 = 185, 470

#: 每个会话的 7 行真值（我读图给定）；`hl`＝绿底高亮行序号（0 起）
CHATS = {
    S1: {"rows": ["E", "服务号", "文件传输", "微信团队", "腾讯新闻", "海绵宝", "某会话"], "hl": 6,
         "header": "某会话"},
    S2: {"rows": ["微信团队", "某会话", "E", "服务号", "文件传输", "腾讯新闻", "海绵宝"], "hl": 0,
         "header": "微信团队"},
    S3: {"rows": ["微信团队", "某会话", "E", "服务号", "文件传输", "腾讯新闻", "海绵宝"], "hl": 2,
         "header": "E"},
}

#: 消息区真值（`(图, 框, 真值...)`；框按 1854×1281 比例给）
#: 聊天区真值：`(图, 聊天区框, 真值清单...)` —— 用**整块区域的召回**度量（不再依赖我手调的细框；
#: 第一版细框两次都偏，量出来的数字不可信）。框 = 聊天区（会话头下方到输入框上方）。
MSGS = [
    (S2, (0.42, 0.03, 1.00, 0.72),
     "登录网站", "乐享知识库", "登录网址", "lexiangla", "登录时间", "2026", "登录操作通知", "进行网站登录操作"),
    (S3, (0.42, 0.03, 1.00, 0.60),
     "Samuel", "Parallelomania", "Journal of Biblical Literature", "jstor.org"),
]

#: ② **不同 UI**：我们自己控制台的真实截图（亮色/暗色两套主题；`_r18_live.png` 与我逐字读过的那些项）
_DEV2 = os.path.join(os.path.dirname(ROOT), "dev-workspace", "persona-morph", "root-probes")
SCREENS = [
    # ⛔ 第一版我把这块裁到了聊天区（侧栏项在更左/更下）⇒ 2/8 是**测量伪影**，不是 OCR 差。
    #    现在用**整图**（真值仍是我逐字读过的侧栏项），并用"跨列"的宽区域。
    (os.path.join(_DEV2, "_r18_live.png"), (0.0, 0.08, 0.55, 0.55),
     "新建任务", "助理", "项目", "专家", "连接器", "定时任务", "资料库", "更多"),
    (os.path.join(_DEV2, "_r18_full.png"), (0.10, 0.05, 0.75, 0.65),
     "修复启动崩溃发送器错误", "深度思考", "正在执行命令", "资料库", "完整访问"),
    (os.path.join(_DEV2, "_r19_pm_real_final_v.png"), (0.0, 0.0, 1.0, 1.0),
     "运行状态", "微信已连接", "新建", "图库", "桌面", "下载"),
]

#: ③ **不同缩放**：把真机图重采样成 100% / 125%（真实的"别家机器 DPI 不同"形态）
SCALES = [(0.667, "100%"), (0.833, "125%")]


def _name_crop(im, idx):
    y0 = ROW_TOP + idx * ROW_PITCH
    return im.crop((NAME_X0, y0 + 6, NAME_X1, y0 + 50))


def _msg_crop(im, frac):
    return im.crop((int(im.width * frac[0]), int(im.height * frac[1]),
                    int(im.width * frac[2]), int(im.height * frac[3])))


def _closed(crop):
    """形态学**闭运算**（先膨胀后腐蚀＝先 MaxFilter 后 MinFilter）：断笔/抗锯齿缺口修复。

    研究口径：断笔文字 +18%、低对比 +25%（CLAHE）⇒ 这里把两者串成"优化档"。
    只用 PIL（项目依赖纪律：不引 cv2）。
    """
    g = ImageOps.autocontrast(crop.convert("L"))
    try:
        g = g.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))
    except Exception as e: # noqa: BLE001
        print("（闭运算跳过：%s）" % str(e)[:40])
    return g.convert("RGB")


def _clahe(crop):
    """CLAHE 局部对比均衡（numpy 版：分块直方图均衡的简化实现）；不可用则原样返回。"""
    try:
        import numpy as np
        g = np.asarray(ImageOps.autocontrast(crop.convert("L")), dtype=np.float32)
        # 4×4 分块 → 每块做对比拉伸（近似 CLAHE 的效果，避免引入 cv2）
        h, w = g.shape
        out = g.copy()
        for by in range(4):
            for bx in range(4):
                ys, ye = int(h * by / 4), int(h * (by + 1) / 4)
                xs, xe = int(w * bx / 4), int(w * (bx + 1) / 4)
                blk = g[ys:ye, xs:xe]
                if blk.size == 0:
                    continue
                lo, hi = np.percentile(blk, 2), np.percentile(blk, 98)
                if hi - lo < 8:
                    continue
                out[ys:ye, xs:xe] = np.clip((blk - lo) * (255.0 / (hi - lo)), 0, 255)
        return Image.fromarray(out.astype("uint8")).convert("RGB")
    except Exception as e: # noqa: BLE001
        print("（CLAHE 跳过：%s）" % str(e)[:40])
        return crop


def _opt_read(crop):
    """**优化档**：ROI（调用方已裁名字列）→ 闭运算 → CLAHE → 两种极性各读一次 → 取最长读数。"""
    best = ""
    for img in (_closed(crop), _clahe(crop), _closed(_clahe(crop))):
        for z in (1, 2, 3):
            c = img if z == 1 else img.resize((img.width * z, img.height * z), Image.LANCZOS)
            got = "".join(str(x[0]) for x in O.recognize_dual(c)).strip()
            if len(O.norm(got)) > len(O.norm(best)):
                best = got
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--optimized", action="store_true", help="走优化档（ROI+闭运算+CLAHE）")
    a = ap.parse_args()
    if not O.available():
        print("⚠️ 内置 OCR 不可用")
        return 2
    rows_ok = rows_n = hl_ok = hl_n = hdr_ok = hdr_n = msg_ok = msg_n = 0
    print("模式：%s" % ("优化档（ROI + 闭运算 + CLAHE）" if a.optimized else "基线（生产路径）"))
    for path, info in CHATS.items():
        im = Image.open(path).convert("RGB")
        name = os.path.basename(path)[:8]
        # ⛔ 生产口径（第一版我搞错了）：**普通行**由 `session_rows()`（列表区 OCR）读；
        #    **绿底高亮行**是白字、列表 OCR 天生读不出 ⇒ 由 `_band_name()` 单读。
        #    所以"某一行认出来了吗"＝该行真值出现在 **两者任一** 的读数里。
        _sr = [str(r.get("name") or "") for r in O.session_rows(im)]
        _hl_y = ROW_TOP + info["hl"] * ROW_PITCH + 24
        _hl_txt = _opt_read(_name_crop(im, info["hl"])) if a.optimized else (O._band_name(im, _hl_y) or "")
        got_rows, got_hl, got_hl_raw = [], "", _hl_txt
        for i, truth in enumerate(info["rows"]):
            # ⛔ **两种模式必须同一把尺子**：都以"任一路径认出"为准；
            #    `--optimized` 只是**多加**一条优化档读数（否则就是拿苹果比橘子 —— 第一版 30.6% 那个数就是这么来的）。
            _pool = list(_sr) + ([_hl_txt] if i == info["hl"] else [])
            if a.optimized:
                _pool.append(_opt_read(_name_crop(im, i)))
            ok = bool(O.norm(truth)) and any(O.norm(truth) in O.norm(x) for x in _pool if x)
            txt = next((x for x in _pool if x and O.norm(truth) in O.norm(x)), "")
            rows_ok += 1 if ok else 0
            rows_n += 1
            if i == info["hl"]:
                hl_ok += 1 if ok else 0
                hl_n += 1
                got_hl = txt
            got_rows.append("%s%s" % (truth, "✓" if ok else "✗"))
        hdr = O._header_read(im, 2)[0] if not a.optimized else _opt_read(im.crop(O.header_box(im)))
        h_ok = O.norm(info["header"]) in O.norm(hdr)
        hdr_ok += 1 if h_ok else 0
        hdr_n += 1
        print("── %s 列表 %s" % (name, " ".join(got_rows)))
        print("   会话头 真值=%r → %r %s | 高亮行(%d) = %r" % (
            info["header"], hdr or "空", "✓" if h_ok else "✗", info["hl"] + 1, got_hl or "空"))
    for path, frac, *truths in MSGS:
        im = Image.open(path).convert("RGB")
        crop = _msg_crop(im, frac)
        if a.optimized:
            got = _opt_read(crop)
            lines = [got]
        else:
            lines = [str(x[0]) for x in O.recognize_dual(crop)]
        hits = [t for t in truths if any(O.norm(t) in O.norm(ln) for ln in lines)]
        miss = [t for t in truths if t not in hits]
        msg_ok += len(hits)
        msg_n += len(truths)
        print("   聊天区 %s：%d/%d 命中%s（读出 %d 行）" % (
            os.path.basename(path)[:8], len(hits), len(truths),
            "，漏：" + "、".join(miss) if miss else "", len(lines)))
    # ② 不同 UI（控制台真机截图，区域召回）
    scr_ok = scr_n = 0
    for path, frac, *truths in SCREENS:
        if not os.path.exists(path):
            print("（跳过：找不到 %s）" % os.path.basename(path))
            continue
        im = Image.open(path).convert("RGB")
        crop = _msg_crop(im, frac)
        lines = [_opt_read(crop)] if a.optimized else [str(x[0]) for x in O.recognize_dual(crop)]
        hits = [x for x in truths if any(O.norm(x) in O.norm(ln) for ln in lines)]
        scr_ok += len(hits)
        scr_n += len(truths)
        print("   控制台 %s：%d/%d 命中%s" % (os.path.basename(path)[:14], len(hits), len(truths),
              "，漏：" + "、".join([x for x in truths if x not in hits]) if len(hits) < len(truths) else ""))
    # ③ 不同缩放（真机图重采样后，列表行是否仍读得出）
    scl_ok = scl_n = 0
    for ratio, label in SCALES:
        for path, info in CHATS.items():
            im = Image.open(path).convert("RGB")
            im2 = im.resize((max(1, int(im.width * ratio)), max(1, int(im.height * ratio))), Image.LANCZOS)
            _top = int(ROW_TOP * ratio)
            _pitch = int(ROW_PITCH * ratio)
            _sr = [str(r.get("name") or "") for r in O.session_rows(im2)]
            _best = list(_sr)
            _hl = info["hl"]
            _b = O._band_name(im2, _top + _hl * _pitch + int(24 * ratio)) or ""
            if _b:
                _best.append(_b)
            for truth in info["rows"]:
                okk = bool(O.norm(truth)) and any(O.norm(truth) in O.norm(x) for x in _best if x)
                scl_ok += 1 if okk else 0
                scl_n += 1
        print("   缩放 %s：累计 %d/%d" % (label, scl_ok, scl_n))

    tot_ok = rows_ok + hdr_ok + msg_ok + scr_ok + scl_ok
    tot_n = rows_n + hdr_n + msg_n + scr_n + scl_n
    print("")
    print("==== 成功率（%d 条真值）====" % tot_n)
    print("   列表行    %2d/%2d" % (rows_ok, rows_n))
    print("     └高亮行(白字绿底) %d/%d" % (hl_ok, hl_n))
    print("   会话头    %2d/%2d" % (hdr_ok, hdr_n))
    print("   消息区    %2d/%2d" % (msg_ok, msg_n))
    print("   控制台UI  %2d/%2d" % (scr_ok, scr_n))
    print("   缩放变体  %2d/%2d" % (scl_ok, scl_n))
    print("   ── 合计  %2d/%2d = %5.1f%%" % (tot_ok, tot_n, 100.0 * tot_ok / max(1, tot_n)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
