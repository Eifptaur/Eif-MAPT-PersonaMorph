# -*- coding: utf-8 -*-
"""会话头指纹自检：不需要微信（用真字体合成"同会话/异会话"两种情形）。

判据：
  H 基本：区域框在窗口内且避开会话列表、维数固定、同图相似度=1.0、异图低于阈值、空指纹不匹配
  S 存储：没有参照时必须拒绝（不许默认放行）、覆盖式写入、原子写不留 .tmp、参照≠当前必须不匹配
  L 实机：当前会话头能否抓出来 + 抓两次是否稳定（抓不到不算失败，只报 INFO）

用法：py -3 scripts\\chat_header_selftest.py   （非零退出＝有失败）
"""
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from agent import chat_header as ch  # noqa: E402
from PIL import Image, ImageDraw, ImageFont   # noqa: E402

OK, BAD = [], []


def ck(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("  %s %s%s" % ("PASS" if cond else "FAIL", name, (" · " + extra) if extra else ""))


def fake_window(text, size=(1139, 890), offset=0):
    """造一张像微信主窗的图：**左侧会话列表浅灰 + 右侧聊天区纯白**（好让 detect_pane_left 有边界可找），
    文字带里用真字体写会话名（贴合实机 28px 字号）。"""
    img = Image.new("RGB", size, (245, 245, 245))
    d = ImageDraw.Draw(img)
    pl = int(size[0] * ch.PANE_LEFT_REL)
    d.rectangle((pl, 0, size[0], size[1]), fill=(255, 255, 255))
    box = ch.crop_box(size, pane_left_px=pl)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 28)
    except Exception:
        font = ImageFont.load_default()
    d.text((box[0] + 6 + offset, box[1] + 8), text, fill=(20, 20, 20), font=font)
    return img


print("[H] 基本判据")
size = (1139, 890)
box = ch.crop_box(size)
ck("H1 区域框在窗口内且非空",
   0 <= box[0] < box[2] <= size[0] and 0 <= box[1] < box[3] <= size[1], str(box))
ck("H2 区域避开左侧会话列表（left >= 0.26w）", box[0] >= int(size[0] * 0.26), "left=%d" % box[0])
pl_det = ch.detect_pane_left(fake_window("文件传输助手"))
ck("H2b detect_pane_left 能测出面板左沿（≈0.26w ± 24px）",
   abs(pl_det - int(size[0] * ch.PANE_LEFT_REL)) <= 24, "测得 %d（期望 %d）"
   % (pl_det, int(size[0] * ch.PANE_LEFT_REL)))
ck("H2c 面板左沿在不同窗口宽度下也能测（会话列表是固定像素宽的实机场景见 §三实测）",
   ch.detect_pane_left(fake_window("文件传输助手", size=(900, 680))) > 0,
   "测得 %d" % ch.detect_pane_left(fake_window("文件传输助手", size=(900, 680))))

fa = ch.fingerprint(fake_window("文件传输助手"))
fb = ch.fingerprint(fake_window("群deepseek"))
ck("H3 指纹维数固定＝BINS", len(fa) == ch.BINS == len(fb), "%d 维" % len(fa))
ck("H4 同一会话（同图）相似度＝1.0",
   abs(ch.similarity(fa, ch.fingerprint(fake_window("文件传输助手"))) - 1.0) < 1e-9)
sim_ab = ch.similarity(fa, fb)
ck("H5 不同会话相似度低于阈值", sim_ab < ch.DEFAULT_THRESHOLD,
   "相似度 %.3f < 阈值 %.2f" % (sim_ab, ch.DEFAULT_THRESHOLD))
ck("H6 match() 与阈值一致", ch.match(fa, fb) is False and ch.match(fa, fa) is True)
ck("H7 空指纹不许被当成匹配", ch.match([], []) is False and ch.similarity([], []) == 0.0)
ck("H8 窗口尺寸不同也能算出同维指纹",
   len(ch.fingerprint(fake_window("文件传输助手", size=(800, 600)))) == ch.BINS)

print("[S] 存储与「没参照不放行」")
with tempfile.TemporaryDirectory() as td:
    p = os.path.join(td, "hdr.json")
    ok0, why0 = ch.verify("filehelper", render=(0, 0, 10, 10), path=p)
    ck("S1 没有参照时 verify ⇒ False（默认拒绝）", ok0 is False, why0)
    ch.remember("filehelper", fa, note="seed", path=p)
    ck("S2 落盘可回读且维数一致", ch.reference("filehelper", p) == fa)
    ck("S3 原子写不留 .tmp", not os.path.exists(p + ".tmp"))
    ch.remember("filehelper", fb, note="覆盖测试", path=p)
    ck("S4 同会话重复 remember 是覆盖（不累积）",
       ch.reference("filehelper", p) == fb and len(ch.load(p)) == 1)
    img = fake_window("群deepseek")
    ch.remember("filehelper", ch.fingerprint(img), path=p)
    ck("S5 参照＝当前 ⇒ 匹配", ch.match(ch.reference("filehelper", p), ch.fingerprint(img)) is True)
    ch.remember("filehelper", fa, path=p)   # 参照换成"文件传输助手"，当前图是"群deepseek"
    ck("S6 参照≠当前 ⇒ 不匹配（这就是防发错会话的那道闸）",
       ch.match(ch.reference("filehelper", p), ch.fingerprint(img)) is False)
    # —— 分尺寸记忆（2026-09-13 实测：指纹**不可跨窗口尺寸复用**，微信会按尺寸重排表头）——
    ck("S7 size_key 形如 宽x高", ch.size_key((1139, 890)) == "1139x890")
    ch.remember("filehelper", fa, path=p, size="1139x890")
    ch.remember("filehelper", fb, path=p, size="900x680")
    ck("S8 同一会话可存多个尺寸，且按尺寸取回",
       ch.reference("filehelper", p, size="900x680") == fb
       and ch.reference("filehelper", p, size="1139x890") == fa,
       "已有尺寸：%s" % ch.ref_sizes("filehelper", p))
    ck("S9 严格取参照时，该尺寸没有 ⇒ 返回空（check() 会判 no_ref ⇒ 不拦只留痕，而不是拿别的尺寸误拦）",
       ch.reference("filehelper", p, size="777x555", strict=True) == []
       and ch.reference("filehelper", p, size="1139x890", strict=True) == fa)

print("[L] 实机（抓不到不算失败）")
fp1 = ch.capture()
if fp1:
    time.sleep(1.5)
    fp2 = ch.capture()
    s = ch.similarity(fp1, fp2) if fp2 else 0.0
    print("  INFO 当前会话头指纹 %d 维；两次抓取相似度 %.3f（稳定性）" % (len(fp1), s))
    other = ch.fingerprint(fake_window("群deepseek"))
    print("  INFO 与合成「群deepseek」相似度 %.3f（应明显低于阈值 %.2f）"
          % (ch.similarity(fp1, other), ch.DEFAULT_THRESHOLD))
else:
    print("  INFO 这次没抓到（微信窗口不可见/未开）——不算失败")

print("\n[结论] %d 通过 / %d 失败" % (len(OK), len(BAD)))
if BAD:
    print("失败项：%s" % BAD)
sys.exit(1 if BAD else 0)
