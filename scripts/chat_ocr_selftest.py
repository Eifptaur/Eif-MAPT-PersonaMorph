# -*- coding: utf-8 -*-
"""会话头 OCR 判据自测（不需要微信：合成会话列表图 + 纯函数）：
①名字归一化/清洗 ②互相包含式匹配 ③绿底高亮行能被挑出来（合成图：一行绿底、其余灰底）
用法：py -3 scripts/chat_ocr_selftest.py
"""
import os
import sys

try: # 控制台默认 GBK：自检里的 ✔/✘ 一旦被重定向就 UnicodeEncodeError 崩掉整条自检
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageDraw, ImageFont # noqa: E402

from agent import chat_ocr as ocr # noqa: E402

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
ck("norm 去掉群成员数", ocr.norm("示例群（8）") == "示例群", repr(ocr.norm("示例群（8）")))

print("② 名字匹配（容忍 OCR 截断/多字）")
ck("完全一致", ocr.matches("文件传输助手", "文件传输助手"))
ck("OCR 截断成前缀", ocr.matches("文件传，17：01", "文件传输助手"))
ck("带群成员数", ocr.matches("示例群（8）", "示例群"))
ck("不同名字不匹配", not ocr.matches("腾讯新闻", "文件传输助手"))
ck("空串不匹配", not ocr.matches("", "文件传输助手"))
ck("单字不误配", not ocr.matches("巷", "文件传输助手"))
# 原来单字只剩上面那条否定式 ⇒ 把 chat_ocr 的「单字名字」分支删掉它仍为真（恒绿）。
# 补三条正例（反向锚：撤掉单字分支 ⇒ 前两条必红）。
ck("单字名字：同名配上", ocr.matches("E", "E"))
ck("单字名字：会话名里的单字配上", ocr.matches("[草稿]EE", "E"))
ck("单字名字反向锚：对不上时仍为 False", not ocr.matches("群里的人", "E"))

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
# ⛔ 这里**按生产口径**传候选集（`wechat.current_chat_name` 就是这么传的）：
#    OCR 在小字上会把「手」读成「孚」这类同形字（本夹具实测到过），候选集纠错正是治它的。
#    不传候选集＝只测了半条链（而线上那条链是带纠错的）。
name, why = ocr.current_chat_name(img, cands=list(names))
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

print("④ 高亮行取名的几何（名字行 | 预览行 | 时间戳 三者必须分开）")
# 合成一条"真机那样"的高亮行，坐标沿用 ③ 的惯例（面板左沿 331）：
#   列表左槽（整带高） | 头像块 | 名字行+右侧时间戳 | 预览行。
# 判据只看几何（不看 OCR 结果）—— OCR 会随字体/缩放漂，而几何不该漂。
_gimg = Image.new("RGB", (1139, 890), (237, 237, 239))
_gd = ImageDraw.Draw(_gimg)
_gd.rectangle([91, 300, 319, 356], fill=ocr.GREEN)        # 绿底高亮行（高 56）
_gd.rectangle([0, 300, 58, 356], fill=(200, 200, 205))    # 列表左槽（整带高 ⇒ 背景块）
_gd.rectangle([101, 305, 131, 345], fill=(90, 130, 200))  # 头像（方块，与文字列隔 20px）
_gd.rectangle([151, 305, 241, 325], fill=(20, 20, 20))    # 名字行（左）
_gd.rectangle([291, 305, 311, 325], fill=(20, 20, 20))    # 时间戳（名字行最右，隔 50px 空白）
_gd.rectangle([151, 330, 269, 350], fill=(120, 120, 120)) # 预览行
_gd.rectangle([331, 0, 1139, 890], fill=(255, 255, 255))  # 右侧聊天区（供面板左沿检测）
_gleft = ocr.ch.pane_left_for(_gimg)
ck("分母守卫：面板左沿在绿底行右侧（窗口宽的 26% 兜底也算）", 250 <= _gleft <= 360, "left=%s" % _gleft)
_gy0, _gy1, _gtx0, _gtx1, _gbg = ocr._band_geo(_gimg, 328)
ck("带上下沿测得（300..356）", abs(_gy0 - 300) <= 3 and abs(_gy1 - 356) <= 3, "y0=%s y1=%s" % (_gy0, _gy1))
_blocks = ocr._band_col_blocks(_gimg, _gy0, _gy1, _gleft, _gbg)
ck("原始列块里**确实**存在「整带高」的背景块（左槽，分母守卫）",
   any(b[2] <= _gy0 + 2 and b[3] >= _gy1 - 3 for b in _blocks), "%s" % (_blocks,))
ck("文字列排除左槽与时间戳（取最宽那段：名字行+预览行并起来最宽）",
   146 <= _gtx0 <= 156 and 264 <= _gtx1 <= 274, "文字列=(%s,%s)" % (_gtx0, _gtx1))
_box = ocr._band_name_box(_gimg, _gy0, _gy1, _gtx0, _gtx1, _gbg)
ck("名字框落在**上半行**（框心在上半、没跑进预览行）",
   bool(_box) and (_box[1] + _box[3]) / 2.0 < 328 and _box[1] <= 308, "%s" % (_box,))
ck("名字框**不含时间戳**（x 上限在时间戳左侧）", bool(_box) and _box[2] < 291, "%s" % (_box,))
ck("时间戳形状不许当名字（否则走格永远认不出目标）",
   (not ocr._name_plausible("16:32")) and (not ocr._name_plausible("1609"))
   and ocr._name_plausible("aaa偷啃使…"), "")
# 走格那道宽容判据：修复前后的读数各验一次（反向锚）
ck("宽容判据：修复后读到的 'aaa偷啃使' 能认过", ocr.loose_matches("aaa偷啃使用者", "aaa偷啃使"))
ck("宽容判据：修复前那两种残读认不过（反向锚）",
   (not ocr.loose_matches("aaa偷啃使用者", "L"))
   and (not ocr.loose_matches("aaa偷啃使用者", "aal日月1551")), "")

print("\n== 结论：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
