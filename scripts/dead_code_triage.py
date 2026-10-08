# -*- coding: utf-8 -*-
"""`dead_code_triage.py` —— PLAN 第 189 项的三档判定（可复跑）。

## 它判什么
`_scratch/scan_dead_code.py` 扫出的 90 个"agent 里只有定义、无引用"的候选，
本脚本给它们**逐个贴上三档结论之一**，并在报告里为每一档附**依据**：

  · `keep`  —— **有意保留**（公开读接口/判据与排障用/库函数/备胎/等前置项）；
  · `wire`  —— **该接的接上**（有明确下游，但正式链漏了调用点）；
  · `drop`  —— **该删的删掉**（已被替代、或被证伪的旧实现，留着只会误导）。

## 为什么要有它
第 189 项的验收是「**90 个逐个有结论，且每项结论都能指到具体依据**」。
靠人逐条写在 PLAN 里，既长又会随代码漂移（函数一改名，结论就对不上了）。
⇒ 改成**规则 + 显式名单**：规则从代码本身读（docstring 自述/是否被判据引用），
   显式名单只放"规则判不了、需要人拍板"的那几条，并写明理由。
⚠️ 本脚本**不改代码**，只出结论表；真正的增删由人看过结论后动手。

## 用法
    py -3 scripts/dead_code_triage.py            # 打印结论表 + 各档计数
    py -3 scripts/dead_code_triage.py --strict   # 有 `wire`/`drop` 未处理 ⇒ 非零退出
"""
from __future__ import annotations

import io
import os
import re
import sys
import logging

log = logging.getLogger("persona-morph")

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AG = os.path.join(ROOT, "agent")
SC = os.path.join(ROOT, "scripts")

# ── `wire`：有明确下游、但正式链漏了调用点（接上才算完） ──────────────────────
# 依据写在本表里：左边是函数，右边是"它该被谁调、为什么现在没接上"。
WIRE = {
    # 已在 接上（保留在表里作为"已接线"的留痕，再犯即回退）：
    ("wechat.py", "_click_visible_session"): "已接：布局快判（`layout_state`）—— 见第 189/23 项",
}
# 已接完的不再列进"未处理" ⇒ 单独一张已办表
DONE_WIRE = {
    ("chat_header.py", "layout_state"):
        "2026-10-05 已接进 `wechat._click_visible_session`（列表被藏时提前让位给搜索路线）；判据 M+1..M+5",
}

# ── `drop`：已被替代 / 已被证伪的旧实现（删掉连它的判据一起清） ────────────────
DROP = {
    ("code_check.py", "_run_py"):
        "已被 `_compile_all()`（内联编译、明确『不用 subprocess』）替代 ⇒ 2026-10-05 已删",
    ("memory.py", "_unname_dir"):
        "逻辑已被 `chat_keys_on_disk()` **内联**取代（它自己拼 `_meta.json` 路径、自己还原）⇒ 2026-10-05 已删",
    ("memory.py", "_chat_dir_meta_path"):
        "同上：被 `chat_keys_on_disk()` 内联取代 ⇒ 2026-10-05 已删",
    ("tools.py", "_ok_text"):
        "硬编码返回『（截图见下方图片）』、零引用（连 `_exec_moments_publish` 也没调它）⇒ 2026-10-05 已删",
    ("voice_strip.py", "_POSTED_HOLD"):
        "『按住 + 松手即发』那套的残留：发送早已改成**点绿↑**（`_posted_send`，用户 2026-10-05 澄清的"
        "第三个动作『声音输完之后点那个绿色的发送键』）⇒ 该件与 `_posted_release()` 成对、双双零引用"
        "⇒ **2026-10-05 已删**（见第 193 项同批）",
    ("voice_strip.py", "_posted_release"):
        "同上：`_POSTED_HOLD` 的读口，随『按住/松手』作废 ⇒ 2026-10-05 已删",
}

# ── `keep`：规则判不了、需要人拍板的保留项（写明理由） ────────────────────────
KEEP_MANUAL = {
    ("history_prune.py", "run_history_windows"):
        "被证伪的时间窗法，**作反例存证**（判据用它证明老法会把留下那轮的回复一起删）⇒ 不许接回主链",
    ("wechat_windows.py", "send_file_to_popup"):
        "自述『已停用 / 重复实现』；判据 `minimized_file_send_selftest.py` 用它做**反向锚**"
        "（断言主链不得调它）⇒ 保留",
    ("tts_text.py", "split_same_char"):
        "自述『默认不再使用，留着只为用户显式要求逐字念的场景』⇒ 备胎，保留",
    # ── 等前置项（PLAN 里已登记，做了前置就该接上） ──
    ("emoji_index.py", "pick_by_usage"): "等第 130/131 项（表情默认路线 + 收藏批量登记）接进表情链",
    ("emoji_index.py", "bump_usage"):
        "等第 130 项：**读口已在用**（`emoji_codes.prompt_lines` 按 `top_used()` 排序情绪组候选），"
        "**写口还没接**（谁真发了哪个方括号码就 +1）。⛔ 不许为了消掉这个候选就把它硬塞进 "
        "`wechat.send_text` 热路径 —— 那要在一笔发送链里多解析一次整条文本，属"
        "『接上了但代价没算』；正解是第 130 项统一在『表情默认路线』里记账。",
    ("emoji_index.py", "split_codes"): "等第 133 项（方括号表情码通道）接进发送前切分",
    ("emoji_index.py", "apply_captions"): "等第 130/136 项（表情语义回写索引）",
    ("emoji_index.py", "need_search"): "等第 130/136 项（低置信表情的定向检索队列）",
    ("emoji_curiosity.py", "parse_verdict"): "等第 130 项（表情好奇心：解析模型判定）",
    ("emoji_codes.py", "kaomoji_index"): "颜文字索引（面板/提示词接口）；W5 已接提示词，本函数供面板用 ⇒ 保留",
    ("memory.py", "extract_facts"): "等第 118 项（M1 模型深抽取层）",
    # ── 等前置项（启动链） ──
    ("display_metrics.py", "warmup"):
        "开机预热（现测一次缩放 + 边框写进档案）。**已接线**（2026-10-05）："
        "接点在 `scripts/persona_morph.py::_attach_wechat()`（`WeChatAdapter(cfg)` 之后、"
        "`_ATTACH` 置成功之前），句柄走 `input_backend.find_main_window()`（只读枚举，不建界面），"
        "整段 try 罩住（失败不影响启动）。"
        "⛔ 不许为了消候选就把它塞进发送热路径（每次发送都量一遍缩放，白花一次采样）；"
        "⛔ 也不许写成「无条件写盘」（`system`/`fallback` 来源不许覆盖档案里的窗口实测值，见 `warmup` docstring）。"
        "本函数现在只由启动链与判据引用 ⇒ **不再是死代码候选**（这也是候选数由 89 降 88 的那一个）。",
    ("replica_adapter.py", "load_group_members"):
        "等第 148 项（群名片名录）。⛔ **已确认的真缺口**：`agent/prompt.py` 曾叫模型去用 "
        "`get_group_members` 查全群名录，而**工具层根本没有这个工具**（只有 `get_active_members`）"
        "⇒ 模型会去调一个不存在的名字。**已修**：把那句提示改成只提 `get_active_members`"
        "（说清它只给最近发过言的人）。本函数（真正的全群名录实现）等第 148 项做成工具再接。",
    ("memory.py", "add_event"): "等第 113/119 项（事件簿写入）",
    ("memory.py", "list_events"): "等第 113/119 项（事件簿读取）",
    ("memory.py", "hot_words"): "第 136 项（热词）已用其数据；本函数是面板读口 ⇒ 保留",
    ("meme_search.py", "note_meme"): "等第 137 项（群梗词典：见过就记下来）",
    ("video_read.py", "media_kind"): "等第 132 项（辟谣解析·媒体分流）；抽帧自研已砍，但分流仍要它",
    ("video_read.py", "probe_subtitle_band"): "同上（先探帧定位字幕带）",
    ("video_read.py", "extract_scene_frames"): "同上（按场景采帧）；⚠️ 判据 `ui_qt/selftest.py` 真跑过它",
    ("row_geometry.py", "merge_rows_with_projection"): "等第 18 项（行归并改结构性判据）—— 预研件",
    ("row_geometry.py", "first_row_y_from_projection"): "等第 17 项（行高锚／首行 y）—— 预研件",
    ("bilibili.py", "download_external"): "等第 132 项（任意平台外链统一下载入口）；判据已拨号验证其平台表",
    ("bilibili.py", "media_download_plan"): "同上（面板『这链接能不能下』预览）",
    ("input_backend.py", "find_panel_window"): "等第 130/131 项（表情面板窗定位）",
    ("input_backend.py", "with_reconnect_once"): "句柄自愈公共件；判据已覆盖，正式链未接（等一次真机复现『句柄过期』再定）",
    ("feedback.py", "pending_text"): "等第 156/157 项（免服务器反馈页：界面『复制反馈』按钮）",
    ("tools.py", "log_swallowed"):
        "⚠️ **本项目采用『就地记账』风格**（如 `wechat._note_ledger_write_fail`）⇒ 本通用件未被采用；"
        "接它会动多处热路径的 except（引新 bug 面）⇒ 按『少就是多』保留不接",
    ("tools.py", "is_code_defect"): "同上（与 `log_swallowed` 同族，随它一起保留）",
    ("chat_header.py", "seed_from_main"):
        "等第 23/150 项：把当前打开会话的会话头存成 chat_id 参照（按窗口尺寸建档）—— 环境收敛后用",
    ("config_events.py", "subscribe"): "配置变更订阅公共件；判据覆盖，等第 145/147 项前端接线时统一接",
    ("config_events.py", "unsubscribe"): "同上",
    ("audio_devices.py", "render_devices"): "音频设备枚举（等第 141 项拟人节奏层/语音链需要时接）",
    ("truth_candidates.py", "from_targets"): "候选生成第二路（命名目标）；与 `from_image` 同族，等真值核验流水线",
    ("wechat.py", "_LEDGER_add"):
        "消息判定台账的追加件；实际写入走 `LEDGER` 另一路 ⇒ 待核是否重复实现（低危，留下轮核）",
    ("wechat_windows.py", "unstick_window_to_screen"):
        "第 36 项（不许裸 PostMessage 关窗/自写 SetWindowPos）的**解法件**；等真机遇到离屏僵死态时接",
    ("wechat_windows.py", "get_foreground"): "『借完前台立刻还』的读件；随第 36/53 项一起定",
    ("wechat_windows.py", "_screen_img_diff"): "两帧变化率（观测件）；等第 96 项（`_note_click_effect` 只观测）用",
    ("wechat_windows.py", "find_row_by_name"): "屏幕上按相似度找行（旧路）；现行走 `chat_ocr.find_row_info` ⇒ 待判重复",
    ("wechat_windows.py", "_row_y_by_db"): "由 DB 序号算行 y；等第 17/18 项行高锚落地后判是否复用它",
    ("wechat_windows.py", "process_is_dpi_aware"): "第 21/22 项（DPI 感知）的读件；接线时接",
    ("wechat_windows.py", "rel_to_px"): "比例→像素换算小件；随第 21/22 项一起用",
    ("wechat_windows.py", "popup_controls"): "独立弹窗控件逻辑坐标表；随第 31 项（重启后重开）用",
    ("safe_name.py", "is_same_session_name"): "P0-7 指纹比对件；判据已覆盖 ⇒ 保留（防重名互相覆盖）",
    ("util.py", "truncate"): "通用文本截断工具（库函数）；等需要时用，零成本 ⇒ 保留",
    ("util.py", "today_key"): "通用日期键工具（库函数）；同上",
    ("image_sources.py", "_baidu"): "图源适配器（内部实现，由 `source_health`/图源表间接用）⇒ 待核",
    ("image_sources.py", "_sogou"): "同上",
}
# 低危删除候选（自述已废弃且已无调用点）—— 单列，等用户/后续一并处理
DROP_CANDIDATE = {
    ("wechat_windows.py", "_give_back_foreground"), ("wechat_windows.py", "_borrow_foreground"),
}

# 规则：docstring 自述这些词 ⇒ 归 `keep`（公开读接口 / 判据与排障用）
_KEEP_WORDS = ["控制台", "判据", "自检", "排障", "只读", "读回", "人工", "诊断", "探针", "观测", "读数"]


def _scan() -> list:
    """跑 `_scratch/scan_dead_code.py` 拿候选（不依赖它的打印格式，直接执行取其输出）。"""
    import subprocess
    env = dict(os.environ, DEAD_LIMIT="500")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "_scratch", "scan_dead_code.py")],
                       cwd=ROOT, capture_output=True, env=env,
                       creationflags=(0x08000000 if os.name == "nt" else 0))
    out = (r.stdout or b"").decode("utf-8", "replace")
    cands = []
    for ln in out.splitlines():
        m = re.match(r"\s+(★判据在用|无人用)\s+(\S+\.py)\s+(\S+)\s+L(\d+)", ln)
        if m:
            cands.append((m.group(2), m.group(3), m.group(1) == "★判据在用"))
    return cands


def _doc(fn: str, nm: str) -> str:
    try:
        s = io.open(os.path.join(AG, fn), encoding="utf-8", errors="replace").read()
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return ""
    m = re.search(r"^(?:def|class)\s+%s\b.*?:\s*\n(\s*)(\"\"\"|''')" % re.escape(nm), s, re.M | re.S)
    if not m:
        return ""
    q = m.group(2)
    i = m.end()
    j = s.find(q, i)
    return s[i:j] if j != -1 else ""


def classify(fn: str, nm: str, in_judge: bool) -> tuple:
    """给一个候选定档 ⇒ `(档, 依据)`。"""
    key = (fn, nm)
    if key in DONE_WIRE:
        return "done", DONE_WIRE[key]
    if key in DROP:
        return "drop", DROP[key]
    if key in KEEP_MANUAL:
        return "keep", KEEP_MANUAL[key]
    d = _doc(fn, nm)
    for w in _KEEP_WORDS:
        if w in d:
            return "keep", "docstring 自述「%s」⇒ 给控制台/判据/排障用的公开读接口" % w
    if in_judge:
        return "keep", "判据脚本在引用它（属测试/对照件，不接正式链）"
    return "wire", "无引用、无判据、docstring 也未自述保留 ⇒ 需逐项定「接上/删掉」"


def main() -> int:
    strict = "--strict" in sys.argv
    cands = _scan()
    buckets = {"keep": [], "wire": [], "drop": [], "done": []}
    for fn, nm, inj in cands:
        k, why = classify(fn, nm, inj)
        buckets[k].append((fn, nm, why))
    print("== PLAN 第 189 项：'写了没接线' 三档判定（共 %d 个候选）==\n" % len(cands))
    for k, title in (("done", "✅ 已接上"), ("drop", "🗑 该删（已有替代/被证伪）"),
                     ("wire", "🔌 待定（接上 or 删掉）"), ("keep", "📌 有意保留")):
        rows = buckets[k]
        print("── %s：%d 个 ──" % (title, len(rows)))
        for fn, nm, why in rows[:12]:
            print("   %-22s %-30s %s" % (fn, nm, why[:60]))
        if len(rows) > 12:
            print("   …… 其余 %d 个（规则同上）" % (len(rows) - 12))
        print()
    total_ok = len(buckets["keep"]) + len(buckets["drop"]) + len(buckets["done"])
    print("结论：%d/%d 已定档（keep/drop/done）；剩 %d 个 'wire' 待逐项拍板"
          % (total_ok, len(cands), len(buckets["wire"])))
    if strict and buckets["wire"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
