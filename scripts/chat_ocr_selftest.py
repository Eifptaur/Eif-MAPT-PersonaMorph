# -*- coding: utf-8 -*-
"""会话头 OCR 判据自测（不需要微信：合成会话列表图 + 纯函数）：
①名字归一化/清洗 ②互相包含式匹配 ③绿底高亮行能被挑出来（合成图：一行绿底、其余灰底）
用法：py -3 scripts/chat_ocr_selftest.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _srcmatch as _sm

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

print("\n── F. 绿底白字多档择优（`_name_score`；真机残字不得压过完整读数）──")
# 真机锚点（2026-09-29，`dev-workspace/persona-morph/ocr-samples/wechat/7bcf1579`，真值 `aaa偷啃使`）：
#   `pane_left` 相对偏移三档扫描实测读数 = ①`''` ②`aaa亻俞`（名字后半被裁掉）③`aaa偷啃使“．`（完整+时间戳残留）。
#   老逻辑「第一档 non-empty 且 plausible 就 return」会把 ② 的**残字**当结果收下（`_name_plausible`
#   只挡"像时间的"、不挡"被截断的"）⇒ 命中率 0/2。修法：三档全读、按 `_name_score` 择优。
_truth = "aaa偷啃使"
ck("F1 残字 vs 完整读数：完整的那一份分更高（否则择优无用）",
   ocr._name_score("aaa偷啃使“．", [_truth]) > ocr._name_score("aaa亻俞", [_truth]),
   "完整=%.3f 残字=%.3f" % (ocr._name_score("aaa偷啃使“．", [_truth]),
                            ocr._name_score("aaa亻俞", [_truth])))
ck("F2 反向锚：残字确实更低分（不是恒真）",
   ocr._name_score("aaa亻俞", [_truth]) < ocr._name_score("aaa偷啃使“．", [_truth]), "")
ck("F3 **认不出候选时不许判 0**（`微信团队胙天` 纠不到候选集，但仍是有效读数）",
   ocr._name_score("微信团队胙天", ["文件传输助手", "演示(3)"]) > 0.0,
   "=%.3f" % ocr._name_score("微信团队胙天", ["文件传输助手", "演示(3)"]))
ck("F4 认不出的读数 < 认得出的读数（候选集加分确实是加分项，不是替代项）",
   ocr._name_score("微信团队胙天", ["文件传输助手"]) < ocr._name_score("微信团队", ["微信团队"]), "")
ck("F5 时间戳形状一律 -1（不许被择优捞回来）",
   ocr._name_score("16:32") < 0 and ocr._name_score("1609") < 0, "")
ck("F6 空串 -1", ocr._name_score("") < 0 and ocr._name_score("   ") < 0, "")
ck("F7 正常名字得正分（无候选集路径不许整体失效）", ocr._name_score("微信团队") > 0.0, "")
# 源码锚：多档扫描必须**读完三档再择优**（老逻辑是"第一档 plausible 就 return"）
_ocr_src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "agent", "chat_ocr.py"), encoding="utf-8").read()
ck("F8 源码锚：`_band_name` 三档扫描改择优（不再第一档返回）",
   _sm.has(_ocr_src, "_best_w, _best_s = \"\", -1.0")
   and not _sm.has(_ocr_src, "if _w and _name_plausible(_w):\n                return _w"), "")
ck("F9 源码锚：`_band_name` 接 `cands`（线上路径靠它择优）",
   "def _band_name(img, y_abs: int, cands=None)" in _ocr_src, "")
# ⭐ 第一候选档＝"绿带上半行 × 绿像素全宽裸读"（2026-09-29 真机实测两例读全）
ck("F10 源码锚：`_band_name` 有「绿带上半行整宽裸读」这一档",
   _sm.has(_ocr_src, "_green_span(img, int(y0), int(y1))"), "")
ck("F11 `_green_span` 与 `_green_x` 是**两个东西**（前者是绿块横界、后者是不含头像的取样窗；混用会砍掉名字开头）",
   "def _green_span(" in _ocr_src and "不含头像" in _ocr_src, "")
# ⚠️ **诚实标注已知局限**（无候选集时纯串面择优做不到可靠——残字与"完整+时间戳残留"无法区分）：
#    这条断言不是在夸功能，是**把局限钉住**，防止后人误以为"无候选集也稳"。
#    事实：无候选集时 `aaa亻俞`（残字，5 字）得分 **不低于** `aaa偷啃使“．`（完整+残留，6 字）
#    ⇒ 无候选集那条路（`highlight()` 纯像素）的择优**不可靠**，可靠的是候选集那条。
ck("F12 已知局限如实记录：无候选集时残字分**不低于**「完整+残留」（⇒ 只有候选集那条路可靠）",
   ocr._name_score("aaa亻俞") >= ocr._name_score("aaa偷啃使“．"),
   "残字=%.3f 完整+残留=%.3f" % (ocr._name_score("aaa亻俞"), ocr._name_score("aaa偷啃使“．")))
ck("F13 反向对照：**有**候选集时完整的那份一定更高（证明候选集才是解药）",
   ocr._name_score("aaa偷啃使“．", ["aaa偷啃使"]) > ocr._name_score("aaa亻俞", ["aaa偷啃使"]), "")
# ⛔ 回归锚（本轮踩过）：整宽档写进 `_best_*` 后，三档循环**不许再重置** `_best_*`
#   （重置会把整宽档白读掉 —— 我第一版就是这么写的，`_best_w, _best_s = "", -1.0` 出现了两次）。
ck("F14 源码锚：三档循环**不再重置** `_best_*`（只允许声明一次）",
   _ocr_src.count('_best_w, _best_s = "", -1.0') == 1,
   "出现 %d 次" % _ocr_src.count('_best_w, _best_s = "", -1.0'))

# ── 1.13 相对日词**形近误读**容错（用户 2026-09-30 当面指正：`胙`是月字旁，应是日字旁的`昨`）──
# 起因：实测报告里读出 `微信团队胙天`。`昨`=U+6628（日旁）、`胙`=U+80D9（月旁）——形近误读。
# 与注释里记的「星期六」事故**同类**：只剥正确的 `昨天` ⇒ 读成 `胙天` 的行残字进 norm ⇒
# `matches()` 判不过 ⇒ **那一行永远认不出**（切会话第③条路失效）。
print("\n── F′. 1.13 相对日词形近误读容错（只修定位；授权档指纹守恒**不许**被放宽）──")
_TYPO_ROWS = ["微信团队胙天", "微信团队咋天", "微信团队昨夭", "微信团队作天",
              "微信团队前天", "微信团队煎天"]
ck("F15 形近误读一律剥净（`胙天/咋天/昨夭/作天/煎天` 都当相对日剥掉）",
   all(ocr.clean(_s) == "微信团队" for _s in _TYPO_ROWS),
   "；".join("%s→%r" % (_s, ocr.clean(_s)) for _s in _TYPO_ROWS))
ck("F15b 「名字 + 形近误读的相对日」这一行恢复可判（matches 判过）",
   all(ocr.matches(_s, "微信团队") for _s in _TYPO_ROWS), "")
# ⭐ 反向锚（与既有「剥空退回原文」纪律同款）：**真名绝不许被洗没**
_REAL_NAMES = ["星期六", "星期天", "昨天", "前天", "下午茶", "昨天播报"]
ck("F16 反向锚：真名（星期六/昨天/前天/下午茶…）一个都不许被洗没（空剥退回原文）",
   all(ocr.clean(_s) == _s for _s in ["星期六", "昨天", "前天", "下午茶"])
   and ocr.clean("昨天播报") == "播报",  # 这条**是**名字+时间词，剥掉才是对的
   "；".join("%s→%r" % (_s, ocr.clean(_s)) for _s in _REAL_NAMES))
# ⛔ 最关键的一条：形近误读容错**只走 clean/定位**，`_rel_re`（授权档指纹源）**不许被扩宽**。
#    否则 `指纹('昨天播报') == 指纹('胙天播报')` ⇒ 守恒失效 ⇒ 真有这两个群名时可能发错人。
ck("F17 授权档仍严格：`胙天播报` 与 `昨天播报` 指纹**不守恒** ⇒ matches_strict 判不过（读不准就不发）",
   ocr.matches_strict("胙天播报", "昨天播报") is False
   and ocr.matches_strict("昨天播报", "胙天播报") is False
   and ocr.matches_strict("昨天播报", "昨天播报") is True, "")
ck("F18 源码锚：`_rel_re` **只收正确的字**（形近误读在 `_rel_typo_re` 里，两条不许合并）",
   '(?:昨天|前天)' in _ocr_src and "_rel_typo_re" in _ocr_src
   and "_rel_re.sub" in _ocr_src and "_rel_typo_re.sub" in _ocr_src, "")

# ── F″. 1.14 授权档模糊兜底的**容忍度**提升（用户 2026-09-30 追问）──
# 用户原话："模型肯定是会读错的，就这水平吗？宁可不发也不冒险，发错人不就等于发不出去吗"
# ⇒ 复核确认：`matches_fuzzy` 原①号约束只有「固定距离阈值」与「包含关系」两条，
#    短名（<3 字）**永不参与**、复合误读（错字+多字）够不着 ⇒ 真实漏网。
#    改法：新增第三条路 `_uniq_nearest`——**不卡固定距离，卡"竞争者明确更远"**。
print("\n── F″. 1.14 授权档模糊兜底：容忍度提升 + 歧义弃权（反向锚）──")
_OTH = ["微信团队", "演示群", "产品讨论组", "先期调研小组讨论群", "aaa偷啃使", "市场部通知群"]
_TOL = [("演示祥", "演示群", "1 字误读"),
        ("演示祥子", "演示群", "错字 + 多 1 字（复合）"),
        ("产品讨论祖", "产品讨论组", "长名 1 字误读"),
        ("产品讨论组x", "产品讨论组", "长名多 1 字"),
        ("先期调研小组", "先期调研小组讨论群", "截断/漏整段")]
_miss = ["%s→%s(%s)" % (a, b, w) for a, b, w in _TOL if not ocr.matches_fuzzy(a, b, _OTH, _OTH)]
ck("F19 读错 1~2 字仍能认对（含复合形态，%d 例）" % len(_TOL), not _miss, _miss)
# ⛔ **超短名继续不参与**——我第一版想"救回"短名，打破了 `ui_qt/selftest.py:7122` 既有判据
#    「两字名不参与（章三≠张三 距离1 也拒）」。那条判据是对的：两字名差一字＝两个人。
ck("F19b ⛔ 超短名（<3 字）**继续不参与**模糊档（`章三≠张三`；误认代价远高于漏发）",
   not ocr.matches_fuzzy("章三", "张三", _OTH, _OTH)
   and not ocr.matches_fuzzy("老扳", "老板", _OTH, _OTH), "")
# ⛔ 反向锚（最容易做坏的一条）：**两个候选都接近时必须弃权**——放宽形态的底气全在这里。
#    这几条若变 True，就是把"猜"当成了"认"，等于允许发错人。
_AMB = [("产品讨论祖", "产品讨论组", ["产品讨论组", "产品讨论祖"], "竞争者同距离"),
        ("产品讨论祖", "产品讨论组", ["产品讨论组", "产品讨论祖", "产品讨论阻"], "三个形近名都在"),
        ("演示祥", "演示群", ["演示群", "演示祥群"], "竞争者同样近"),
        ("测试", "测试群", ["测试", "测试群"], "互为子串"),
        ("某会话测试", "某会话", ["某会话", "某会话测试"], "嵌套名")]
_bad = ["%s|%s(%s)" % (a, b, w) for a, b, oth, w in _AMB
        if ocr.matches_fuzzy(a, b, oth, oth)]
ck("F20 反向锚：候选同样接近 ⇒ **一律弃权**（%d 组，放宽不许变成猜）" % len(_AMB), not _bad, _bad)
ck("F20b 无候选集时**不许放宽**（无参照=猜；`_uniq_nearest` 必须直接 False）",
   ocr.matches_fuzzy("演示祥子", "演示群", None, None) is False
   and ocr.matches_fuzzy("演示祥子", "演示群", [], []) is False, "")
ck("F20c 边际有实证：竞争者**远一档**时该认（否则上面那条只是恒 False）",
   ocr.matches_fuzzy("产品讨论祖", "产品讨论组", ["产品讨论组", "市场部通知群"],
                     ["产品讨论组", "市场部通知群"]) is True, "")
ck("F21 源码锚：`_uniq_nearest` 存在、`_MARGIN` 是**相对边际**、**超短名护栏在**",
   "def _uniq_nearest(" in _ocr_src and "_MARGIN" in _ocr_src
   and "_edit_dist(a, no) < d0 + _MARGIN" in _ocr_src
   and 'if min(len(a), len(b)) < 3:  # ⛔ 超短名不参与' in _ocr_src, "")



print("\n── G. 真机绿底白字端到端（`_band_name` 走生产入口；素材不在则**跳过**，绝不假绿）──")
# 素材：用户 2026-09-29 专供的真机微信截图（150% DPI），`dev-workspace/` 是**非版本库路径** ⇒
# 换台机器/清过 workspace 就跑不到 ⇒ 跳过（打印「跳过」并计数，不冒充通过）。
_WXDIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                      "dev-workspace", "persona-morph", "ocr-samples", "wechat")
_REAL = [("0af83564-b60d-4812-aa4f-3c330c6399c0.png", "微信团队"),
         ("7bcf1579-61e9-4c80-a54a-47eb45341b53.png", "aaa偷啃使")]
SKIP = 0
if not (os.path.isdir(_WXDIR) and all(os.path.exists(os.path.join(_WXDIR, f)) for f, _ in _REAL)):
    SKIP = 3
    print("  ↷ 跳过（找不到真机素材 %s）" % _WXDIR)
else:
    _hit = 0
    for _fn, _t in _REAL:
        _im = Image.open(os.path.join(_WXDIR, _fn)).convert("RGB")
        _bs = ocr.green_bands(_im, min_ratio=0.30, min_h=28)
        if not _bs:
            ck("G·%s 找到绿底高亮行" % _fn[:8], False, "green_bands 空")
            continue
        _g = ocr._band_name(_im, _bs[0]["y_abs"], [_t, "文件传输助手", "演示(3)"])
        _a, _tt = ocr.norm(_g), ocr.norm(_t)
        _ok = bool(_tt) and (_tt in _a or (len(_tt) >= 3 and ocr._edit_dist_le(_a, _tt, 1)))
        # ⛔ 这里**不要求**读到的就是真名本身——真实场景允许"名字+时间戳残留"（`微信团队胙天`），
        #    所以判据只问"真名有没有被读到"（这正是线上拿去比对候选集的东西）。
        _hit += 1 if _ok else 0
        ck("G·%s 生产入口读到真名（真值 %s）" % (_fn[:8], _t), _ok, "读到 %r" % _g)
    ck("G·真机绿底白字命中 ≥ 1/2（裸像素那版是 0/2；这是「我方增强版行不行」的下限）",
       _hit >= 1, "%d/2" % _hit)

print("\n── E. 第二引擎档位（PP-OCRv6 优先 / v4 回退）──")
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_src = open(os.path.join(_root, "agent", "chat_ocr.py"), encoding="utf-8").read()
ck("E1 引擎构造 v6 优先（`from rapidocr import` 排在 v4 之前）",
   _sm.has(_src, "from rapidocr import RapidOCR as _RapidOCR6")
   and _src.find("from rapidocr import RapidOCR as _RapidOCR6") < _src.find("from rapidocr_onnxruntime import RapidOCR"))
ck("E2 v4 仍作回退（v6 不可用时不至于第二引擎整体不可用）",
   _sm.has(_src, "回退 PP-OCRv4") and _sm.has(_src, "rapidocr_onnxruntime import RapidOCR"))
ck("E3 自举整栈离线装（v6 包不自带 omegaconf/antlr4；只装它＝假成功）",
   _sm.has(_src, "_RAPID_PKGS") and _sm.has(_src, "整栈"))
ck("E4 离线安装双 find-links（opencv/numpy/Pillow 在 offline/wheels）",
   _sm.has(_src, "_extra = os.path.join") and _sm.has(_src, '"offline", "wheels"'))
ck("E5 v6 输出与 WinRT 同构（归一成 5 元组，否则 list_rows 分组全错）",
   _sm.has(_src, "getattr(_r, \"boxes\", None)") and _sm.has(_src, "out6.append((str(_txt)"))
try:
    import rapidocr as _r6pkg # noqa: F401
    _has6 = True
except Exception: # noqa: BLE001
    _has6 = False
if _has6:
    from PIL import Image as _PImg
    _items = ocr._rapid_recognize(_PImg.new("RGB", (240, 60), (250, 250, 250)))
    ck("E6 行为锚：v6 档位下 `_rapid_recognize` 返回 5 元组",
       all(len(x) == 5 for x in _items), "%d 条 · tier=%s" % (len(_items), ocr.rapidocr_status().get("tier")))
ck("E7 随包离线轮子里带齐 v6 那一套（rapidocr + omegaconf + antlr4）",
   all(any(fn.startswith(_p) for fn in os.listdir(os.path.join(_root, "vendor", "ocr")))
       for _p in ("rapidocr-", "omegaconf-", "antlr4")), "")

print("\n== 结论：%d 通过 / %d 失败%s ==" % (PASS, FAIL, ("（跳过 %d）" % SKIP) if SKIP else ""))
sys.exit(1 if FAIL else 0)
