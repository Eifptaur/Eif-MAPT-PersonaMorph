# -*- coding: utf-8 -*-
"""会话头 OCR 判据自测（不需要微信：合成会话列表图 + 纯函数）：
①名字归一化/清洗 ②互相包含式匹配 ③绿底高亮行能被挑出来（合成图：一行绿底、其余灰底）
用法：py -3 scripts/chat_ocr_selftest.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageDraw, ImageFont      # noqa: E402

from agent import chat_ocr as ocr                # noqa: E402

PASS = FAIL = 0


def ck(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✔ %s" % name)
    else:
        FAIL += 1
        print("  ✘ %s  %s" % (name, detail))


print("① 文本清洗（会话行＝名字+预览+时间挤在一起）")
ck("去掉时间", ocr.clean("文件传，17：01") == "文件传，", repr(ocr.clean("文件传，17：01")))
ck("去掉日期", ocr.clean("微信团队09/06") == "微信团队", repr(ocr.clean("微信团队09/06")))
ck("去掉省略号", ocr.clean("日本爆发梅毒．．．") == "日本爆发梅毒", repr(ocr.clean("日本爆发梅毒．．．")))
ck("norm 去掉标点", ocr.norm("文件传，“17：01") == "文件传", repr(ocr.norm("文件传，“17：01")))
ck("norm 去掉群成员数", ocr.norm("群deepseek（8）") == "群deepseek", repr(ocr.norm("群deepseek（8）")))

print("② 名字匹配（容忍 OCR 截断/多字）")
ck("完全一致", ocr.matches("文件传输助手", "文件传输助手"))
ck("OCR 截断成前缀", ocr.matches("文件传，17：01", "文件传输助手"))
ck("带群成员数", ocr.matches("群deepseek（8）", "群deepseek"))
ck("不同名字不匹配", not ocr.matches("腾讯新闻", "文件传输助手"))
ck("空串不匹配", not ocr.matches("", "文件传输助手"))
ck("单字不误配", not ocr.matches("巷", "文件传输助手"))

print("③ 绿底高亮行检测（合成图：第 2 行绿底）")
try:
    font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 22)
except Exception:
    font = ImageFont.load_default()
W, H = 1139, 890
img = Image.new("RGB", (W, H), (237, 237, 239))
d = ImageDraw.Draw(img)
names = ["腾讯新闻", "文件传输助手", "服务通知"]
pane_left = 331
for i, nm in enumerate(names):
    y = 60 + i * ocr.ROW_PITCH
    if i == 1:
        d.rectangle([pane_left - 240, y - 28, pane_left - 12, y + 28], fill=ocr.GREEN)
    d.text((pane_left - 230, y - 16), nm, font=font, fill=(20, 20, 20))
    d.text((pane_left - 230, y + 6), "最后一条消息预览", font=font, fill=(150, 150, 150))
# 让 detect_pane_left 能认出面板左沿：右侧一大片纯白
d.rectangle([pane_left, 0, W, H], fill=(255, 255, 255))
got_left = ocr.ch.detect_pane_left(img)
ck("合成图面板左沿可测", 300 <= got_left <= 360, "got=%s" % got_left)
scores = [ocr._green_at(img, 60 + i * ocr.ROW_PITCH) for i in range(3)]
ck("绿底行占比最高且 >0.5", scores[1] > 0.5 and scores[1] > scores[0] and scores[1] > scores[2],
   "scores=%s" % [round(s, 2) for s in scores])
rows = ocr.session_rows(img)
ck("会话行聚合成 3 行", len(rows) == 3, "rows=%d" % len(rows))
name, why = ocr.current_chat_name(img)
ck("判定为第 2 行（文件传输助手）", ocr.matches(name, "文件传输助手"), "name=%r why=%s" % (name, why))
ck("依据串里带绿底占比", "占比" in why, why)

# 没有绿底行时不许瞎认
plain = Image.new("RGB", (W, H), (237, 237, 239))
d2 = ImageDraw.Draw(plain)
for i, nm in enumerate(names):
    d2.text((pane_left - 230, 60 + i * ocr.ROW_PITCH - 16), nm, font=font, fill=(20, 20, 20))
d2.rectangle([pane_left, 0, W, H], fill=(255, 255, 255))
name2, why2 = ocr.current_chat_name(plain)
ck("无绿底行 ⇒ 返回空（fail-closed）", name2 == "", "name=%r why=%s" % (name2, why2))

print("\n== 结论：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
