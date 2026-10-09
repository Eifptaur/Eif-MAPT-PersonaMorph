# -*- coding: utf-8 -*-
"""环境引导：把"**要用户自己动手装的东西**"汇成一张傻瓜清单（只读检测 + 现场给指引）。

为什么要它
功能不是装完就能用的：真语音条要一块虚拟声卡、音视频读入要 ffmpeg、离线识别要 Windows
语音识别包、依赖缺了要补轮子…… 这些**装不了自动版**（有的要装内核驱动、有的要开系统功能），
以前分散在各面板的提示里，新用户配好 Key 就走、跑到那一步才发现缺东西，还查不出缺哪一环。
本模块把它们**汇成一份带"要什么 / 有没有 / 怎么装 / 装完点重检"的清单**，供新手引导
（Qt `ui_qt/onboarding.py` 与 web 控制台 `onboarding()`）与状态接口共用。

口径（照项目红线，**与 `audio_devices.py` 同一条**）
  · **只检测 + 给指引 + 用户自己点装**：绝不静默装驱动、绝不改系统设置、绝不自动开系统功能；
  · 每项都如实写"现在能不能用"，缺就写清缺哪一环、他自己能做的下一步是什么；
  · 读不出（探测本身失败）⇒ 如实说"读不出"，**不混进"不缺"**（三态必须分开）。

对外只有 `status()`：返回 `{"ok", "items": [...], "n_ok", "n_total", "why"}`，
每项形如 `{"key","title","need","ok","state","why","how","install_kind","action"}`：
  · `state` ∈ {"ok","warn","fail","unknown"}（未知＝探测本身没读出来，不算失败）；
  · `install_kind` ∈ {"driver","system","pip","manual","none"} —— 决定前端给什么按钮/文案；
  · `how` ＝ 用户照做就能补上的**一步到位的说明**（不是内部术语）。
"""
from __future__ import annotations

import logging
import sys

try:  # 控制台/自检在管道里跑时别被 GBK 编码崩掉
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

log = logging.getLogger("persona-morph")

#: 每项"要用户自己装"的东西分几类（前端据此决定按钮/文案；**不是**内部术语）
#   driver  ＝ 装一个驱动程序（虚拟声卡这类）——我们**不替他装**，只给指引
#   system  ＝ 开系统功能 / 装语言包（Windows 设置里点）——同样不替他改
#   pip     ＝ 装一个 Python 包（pilk / yt-dlp 这类）——可以走自带运行时的 pip
#   manual  ＝ 放一个文件 / 填一段配置（ffmpeg 二进制、自建服务地址）
#   none    ＝ 不需要装（已就绪）
KIND_LABEL = {
    "driver": "要装驱动（我们不会替你装）",
    "system": "要开系统功能 / 装语言包",
    "pip": "要装一个 Python 包",
    "manual": "要放文件 / 填地址",
    "none": "不用装",
}

#: 各项「要下载的东西」的元数据：**下载地址 + 体积 + 能不能替用户静默装**。
#:
#: 为什么要这张表（2026-10-09 用户口径）：缺件引导的弹窗上**必须写明"要下多大的东西"**，
#: 用户才好在"现在装 / 以后再说"之间做判断；而"能不能静默装"决定了弹窗给不给进度条。
#:
#: ⛔ 体积一律**实测填数**，不许估：
#:   · VB-CABLE：`curl -sI` 打官方直链，`Content-Length: 1318877`（实测 2026-10-09）；
#:   · ffmpeg：`imageio-ffmpeg` 轮子里自带（`offline/wheels/imageio_ffmpeg-0.6.0-*.whl`
#:     实测 31,246,824 字节，解开后二进制 87,638,016 字节）⇒ **随包分发，用户零下载**；
#:   · 微信 / Windows 语音识别包：官方**没有**稳定直链（官网下载页是按需生成的），
#:     体积也随版本变 ⇒ `url` 只给官方页面、`size` 记 0（＝"大小以官网为准"，弹窗照实显示）。
#:
#: ⚠️ `silent` 三态：
#:   "yes"  ＝ 我们能完全替用户静默装（不弹任何窗、不问任何问题）；
#:   "admin"＝ 能替用户装，但**必须提权**（装内核驱动这类 —— 会弹一次系统 UAC，
#:            躲不掉，如实告诉用户"会弹一次确认"）；
#:   "no"   ＝ 装不了（要用户自己点/自己开系统功能），我们只给官方地址。
_DOWNLOAD = {
    "vmic": {
        "url": "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip",
        "size": 1318877,
        "size_text": "1.3 MB",
        "silent": "admin",
        "note": "装完会多出一个「CABLE Input（播放）」和一个「CABLE Output（录音）」，"
                "微信从录音端就能录到声音；装完要重启一次电脑才生效。",
    },
    "ffmpeg": {
        "url": "https://ffmpeg.org/download.html",
        "size": 0,
        "size_text": "不用下载（已随程序自带）",
        "silent": "no",
        "note": "这一份已经跟程序一起装好了，你什么都不用做。",
    },
    "wechat": {
        "url": "https://pc.weixin.qq.com/",
        "size": 0,
        "size_text": "以官网为准",
        "silent": "no",
        "note": "只支持官方 PC 版微信；装完登录你自己的号再回来点重检。",
    },
    "asr": {
        "url": "https://github.com/SYSTRAN/faster-whisper",
        "size": 0,
        "size_text": "以官网为准（可选，不装也能用）",
        "silent": "no",
        "note": "**可选**：装了识别更准，不装机器人的内置识别照样能用。"
                "它体积较大、第一次用还要联网下模型 —— 你下不下自己定，我们不代装。",
    },
    "silk": {
        "url": "",
        "size": 0,
        "size_text": "不用下载（已随程序自带）",
        "silent": "yes",
        "note": "随程序的依赖一起装，不需要联网。",
    },
    "deps": {
        "url": "",
        "size": 0,
        "size_text": "不用下载（已随程序自带）",
        "silent": "yes",
        "note": "包内自带全部轮子装，不需要联网。",
    },
    "tts": {
        "url": "",
        "size": 0,
        "size_text": "不用下载（用系统自带）",
        "silent": "no",
        "note": "程序会自己切到系统自带那一档，零安装、不出网；你不用做任何事。",
    },
}


def download_of(key: str) -> dict:
    """取某项的下载元数据（没有登记过 ⇒ 返回一份"如实说不知道"的空壳，**不许猜数**）。"""
    d = _DOWNLOAD.get(str(key) or "") or {}
    return {
        "url": str(d.get("url") or ""),
        "size": int(d.get("size") or 0),
        "size_text": str(d.get("size_text") or "大小以官网为准"),
        "silent": str(d.get("silent") or "no"),
        "note": str(d.get("note") or ""),
    }


def _mk(key, title, need, ok, why, how="", kind="none", action=""):
    """造一条清单项。`ok` 三态：True＝已就绪 / False＝缺 / None＝读不出（**不许当缺**）。

    ⚠️ 额外的 `dl` 字段挂上"要下载什么、多大、能不能静默装"（见 `_DOWNLOAD`）——
    缺件引导的弹窗**照它显示体积**，不许前端自己编数字。
    """
    state = "ok" if ok is True else ("unknown" if ok is None else "fail")
    if ok is True:
        kind = "none"
    return {"key": str(key), "title": str(title), "need": str(need), "ok": ok,
            "state": state, "why": str(why or ""), "how": str(how or ""),
            "install_kind": str(kind), "action": str(action or ""),
            "dl": download_of(key)}


# ── 各项探测（每项独立兜底：一处读不出不影响其余）────────────────────────
def _item_wechat() -> dict:
    """微信本体：装了没、版本、能不能接上（这是"跑起来"的第一前提）。"""
    try:
        from .wechat import wechat_version_info
        info = wechat_version_info() or {}
        # ⚠️ `wechat_version_info()` 的 `version` 是**字符串**（如 "4.1.15.13"），不是 dict；
        #    安装信息在 `install` 段。旧写法按 dict 取 ⇒ `'str' object has no attribute 'get'`。
        ver = str(info.get("version") or "").strip()
        inst = info.get("install") if isinstance(info.get("install"), dict) else {}
        installed = bool(info.get("installed") if info.get("installed") is not None
                         else inst.get("installed"))
        if ver and installed:
            running = str(info.get("state") or inst.get("state") or "")
            tail = "（正在运行）" if running == "running" else ""
            return _mk("wechat", "微信本体", "跑机器人必须先有微信（官方版）", True,
                       "已检测到微信 %s%s" % (ver, tail))
        if ver or installed:
            return _mk("wechat", "微信本体", "跑机器人必须先有微信（官方版）", True,
                       "已检测到微信安装（版本没读到：%s）" % (ver or "未知"))
        return _mk("wechat", "微信本体", "跑机器人必须先有微信（官方版）", False,
                   "没检测到微信安装",
                   "到微信官网下载安装（**只支持官方 PC 版微信**），装完登录你自己的号，再点重检",
                   kind="manual", action="openUrl")
    except Exception as e:  # noqa: BLE001
        return _mk("wechat", "微信本体", "跑机器人必须先有微信（官方版）", None,
                   "读不出微信安装信息：%s" % str(e)[:60])


def _item_deps() -> dict:
    """依赖：产品自带的运行时里，`requirements.txt` 那些包装齐了没（缺就补轮子）。"""
    try:
        from .wechat import dep_check
        rows, all_ok = dep_check("all")
        miss = [str(r[0]) for r in rows if not r[3]]
        if all_ok:
            return _mk("deps", "运行依赖", "程序自己的运行时里要装齐依赖包", True,
                       "全部 %d 项依赖均已就绪" % len(rows))
        return _mk("deps", "运行依赖", "程序自己的运行时里要装齐依赖包", False,
                   "还缺 %d 个：%s" % (len(miss), "、".join(miss[:6])),
                   "点「一键补齐」用包内自带的依赖装（不需要联网）；装过的不会重装",
                   kind="pip", action="depHeal")
    except Exception as e:  # noqa: BLE001
        return _mk("deps", "运行依赖", "程序自己的运行时里要装齐依赖包", None,
                   "依赖清单读不出：%s" % str(e)[:60])


def _item_virtual_mic() -> dict:
    """虚拟声卡：**真语音条**（微信语音气泡）的唯一前提——微信必须自己录到一段音频。

    三种来源，代价差一个数量级（照 `audio_devices.py` 的口径，**不夸大**）：
      ① 成对的虚拟声卡（VB-CABLE / Scream）——干净、不串音；代价＝装一个内核音频驱动；
      ② 立体声混音 / Stereo Mix——零安装，但会把扬声器里正在放的声音一起录进去；
      ③ 把默认录音设备临时切到 ①②——系统设置、可逆。
    """
    try:
        from . import audio_devices as ad
        st = ad.status() or {}
        if st.get("ok"):
            names = "、".join(str(v.get("name")) for v in (st.get("virtual_mics") or []))
            clean = [v for v in (st.get("virtual_mics") or []) if v.get("clean")]
            if clean:
                return _mk("vmic", "虚拟声卡（发真语音条）",
                           "想让机器人发**微信语音条**，得有一块虚拟麦克风让微信自己录",
                           True, "已检测到干净的虚拟麦克风：%s" % names)
            return _mk("vmic", "虚拟声卡（发真语音条）",
                       "想让机器人发**微信语音条**，得有一块虚拟麦克风让微信自己录",
                       True,
                       "检测到虚拟麦克风：%s（**属混音类**：会把扬声器里正在放的声音一起录进去）"
                       % names,
                       "想更干净可换一块成对的虚拟声卡（如 Scream）；现在这样也能发，"
                       "但发语音条时别在放音乐/视频",
                       kind="driver")
        latent = st.get("inactive_virtual_mics") or []
        if latent:
            nm = "、".join(str(v.get("name")) for v in latent)
            return _mk("vmic", "虚拟声卡（发真语音条）",
                       "想让机器人发**微信语音条**，得有一块虚拟麦克风让微信自己录",
                       False,
                       "有虚拟/混音设备但**没启用**：%s" % nm,
                       "在「设置 → 系统 → 声音 → 输入」里把它**启用**（不用装驱动）；启用后回这点重检",
                       kind="system", action="recheck")
        return _mk("vmic", "虚拟声卡（发真语音条）",
                   "想让机器人发**微信语音条**，得有一块虚拟麦克风让微信自己录",
                   False, "没有可用的虚拟麦克风",
                   "装一块成对的虚拟声卡（如 **VB-CABLE** 或 **Scream**，免费）："
                   "装完它会多出一个「播放」端和一个「录音」端，微信从录音端就能录到；"
                   "**我们不替你装驱动、也不改系统设置**——这是你自己一步能做的"
                   "（不想装也行：语音会如实退成音频文件发出去）",
                   kind="driver", action="recheck")
    except Exception as e:  # noqa: BLE001
        return _mk("vmic", "虚拟声卡（发真语音条）",
                   "想让机器人发**微信语音条**，得有一块虚拟麦克风让微信自己录", None,
                   "音频设备检测失败：%s" % str(e)[:60])


def _item_tts_engine() -> dict:
    """合成引擎：把文字念成音频。系统档零安装（Windows 内置 SAPI），edge 档要联网。

    ⛔ 这里**不教用户进系统设置**（用户最多只到微信这一层）：程序自己能切到系统档，
      所以缺了只说"点重检"（程序去切内置档），或让用户决定要不要连网用 edge。
    """
    try:
        from . import voice_models as vm
        st = vm.status() or {}
        if st.get("ok"):
            return _mk("tts", "语音合成引擎", "让机器人能「说话」（合成一段语音）", True,
                       str(st.get("why") or "可用"))
        return _mk("tts", "语音合成引擎", "让机器人能「说话」（合成一段语音）", False,
                   str(st.get("why") or "没有可用的合成引擎"),
                   "点重检就行 —— 程序会自己切到系统自带那一档（零安装、不出网）。"
                   "想要更自然的音色，也可以在面板里换成在线的 edge 神经语音（那一档要连网，"
                   "换不换由你定）。",
                   kind="system", action="recheck")
    except Exception as e:  # noqa: BLE001
        return _mk("tts", "语音合成引擎", "让机器人能「说话」（合成一段语音）", None,
                   "合成引擎状态读不出：%s" % str(e)[:60])


def _item_ffmpeg() -> dict:
    """ffmpeg：抽帧 / 抽音频 / 转格式都要它。**自动版装不了**，得放一个二进制。"""
    try:
        from . import video_read as vr
        ff = vr.ffmpeg_path()
        if ff:
            return _mk("ffmpeg", "ffmpeg（音视频处理）",
                       "读视频、抽音频、把语音转成 mp3 都要它", True,
                       "已找到 ffmpeg（%s）" % ff)
        return _mk("ffmpeg", "ffmpeg（音视频处理）",
                   "读视频、抽音频、把语音转成 mp3 都要它", False,
                   "本机没有 ffmpeg",
                   "到 ffmpeg 官网下一份 Windows 版（或用一个绿色包），把 `ffmpeg.exe` 那个目录"
                   "加进系统 PATH（或放进项目 `tools.d/`），装完点重检——这个**装不了自动版**，"
                   "只能你自己放一份",
                   kind="manual", action="recheck")
    except Exception as e:  # noqa: BLE001
        return _mk("ffmpeg", "ffmpeg（音视频处理）",
                   "读视频、抽音频、把语音转成 mp3 都要它", None,
                   "ffmpeg 状态读不出：%s" % str(e)[:60])


def _item_asr() -> dict:
    """语音识别：让机器人「听懂」群友发的语音条。

    ⛔ **不再让用户进系统设置**（用户最多只到微信这一层）：这里给的是**可选下载**——
      · 随包/内置那一档（Windows 内置识别）能用 ⇒ 直接判就绪；
      · 想更准 ⇒ 列一档 faster-whisper（**用户自己选下不下，不塞安装包**）。
    装不装由用户决定，我们不催、不代装。
    """
    try:
        from . import video_read as vr
        st = vr.asr_status() or {}
        if st.get("ok"):
            bk = vr.asr_backend() or {}
            note = str(bk.get("detail") or "可用")
            return _mk("asr", "语音识别（听懂语音条）", "让机器人能「听懂」群友发的语音", True, note)
        return _mk("asr", "语音识别（听懂语音条）", "让机器人能「听懂」群友发的语音", False,
                   str(st.get("why") or "没有可用的识别引擎"),
                   "想要识别更准，可以自己装一份 faster-whisper（**可选，不在包内 —— 你下不下都行，"
                   "不装只是识别效果一般**）。注意它体积较大、第一次用要联网下模型，"
                   "装不装由你决定，不装照样能用。",
                   kind="manual", action="openUrl")
    except Exception as e:  # noqa: BLE001
        return _mk("asr", "语音识别（听懂语音条）", "让机器人能「听懂」群友发的语音", None,
                   "识别引擎状态读不出：%s" % str(e)[:60])


def _item_silk() -> dict:
    """解码器：微信语音条是 .silk 格式，转文字前要先解码（pilk 这个包）。"""
    try:
        from . import media_status
        snap = (media_status.snapshot() or {}).get("voice") or {}
        dec = [d for d in (snap.get("decode") or []) if isinstance(d, dict)]
        if not dec:
            return _mk("silk", "语音解码器（.silk）", "微信语音条是 .silk 格式，转文字前要先解码",
                       None, "解码器清单读不到")
        ok = any(d.get("ok") for d in dec)
        if ok:
            names = "、".join(str(d.get("name")) for d in dec if d.get("ok"))
            return _mk("silk", "语音解码器（.silk）", "微信语音条是 .silk 格式，转文字前要先解码",
                       True, "可用：%s" % names)
        miss = "、".join(str(d.get("name")) for d in dec if not d.get("ok"))
        return _mk("silk", "语音解码器（.silk）", "微信语音条是 .silk 格式，转文字前要先解码",
                   False, "缺：%s" % miss,
                   "点「一键补齐」装 pilk（可选第三方依赖，GPL-3.0，不随本项目分发）",
                   kind="pip", action="depHeal")
    except Exception as e:  # noqa: BLE001
        return _mk("silk", "语音解码器（.silk）", "微信语音条是 .silk 格式，转文字前要先解码",
                   None, "解码器状态读不出：%s" % str(e)[:60])


#: 清单顺序＝用户按这个顺序补最省事（先能跑，再补"锦上添花"的能力）
_ORDER = ("wechat", "deps", "vmic", "tts", "ffmpeg", "asr", "silk")


def status() -> dict:
    """汇总全部"要用户自己装的东西"⇒ {ok, items, n_ok, n_total, n_missing, why}。

    `ok` ＝ **没有 fail 项**（unknown 不算失败，但会在 `why` 里点出来——不许把"读不出"混成"不缺"）。
    """
    items = []
    for fn in (_item_wechat, _item_deps, _item_virtual_mic, _item_tts_engine,
               _item_ffmpeg, _item_asr, _item_silk):
        try:
            items.append(fn())
        except Exception as e:  # noqa: BLE001
            log.debug("宽捕处出错（留栈，便于归因）：%s", e, exc_info=True)
    want = {k: i for i, k in enumerate(_ORDER)}
    items.sort(key=lambda x: want.get(x.get("key"), 99))
    n_ok = sum(1 for x in items if x["ok"] is True)
    n_fail = sum(1 for x in items if x["ok"] is False)
    n_unk = sum(1 for x in items if x["ok"] is None)
    why = ""
    if n_fail:
        why = "%d 项还没就绪（补齐后点重检）：%s" % (
            n_fail, "、".join(x["title"] for x in items if x["ok"] is False))
    elif n_unk:
        why = "%d 项读不出（探测本身失败，**不等于缺**）：%s" % (
            n_unk, "、".join(x["title"] for x in items if x["ok"] is None))
    else:
        why = "全部 %d 项已就绪" % len(items)
    return {"ok": n_fail == 0, "items": items, "n_ok": n_ok, "n_fail": n_fail,
            "n_unknown": n_unk, "n_total": len(items), "why": why,
            "note": "只检测 + 给指引：**不替你装驱动、不改系统设置**；装完点重检即可"}
