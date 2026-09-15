# -*- coding: utf-8 -*-
"""本机音频端点枚举 + 「有没有能当麦克风用的虚拟设备」判定（**只读注册表，不动任何设置**）。

为什么要它（用户 2026-09-15）：「**虚拟声卡可以装啊，大小不大就行。最主要是要兼容那些用户本地的，
比方说 GPT-SoVITS 的、RVC 的**」。
"真语音条"的唯一干净路线＝**把音频送进一个"麦克风"，让微信自己录**
（直接把 mp3/silk 当文件发出去，微信只会显示成文件，不是语音条）。
那个"麦克风"有三个来源，代价差一个数量级：
  ① **立体声混音 / Stereo Mix**（多数声卡驱动自带）——零安装。但**它录的是扬声器正在放的一切**
     ⇒ 用户在看视频/听歌时会把那些声音一起录进语音条（本机实测：这个设备存在，但处于**停用**态）。
  ② **成对的虚拟声卡**（Scream / VB-CABLE）——播放进它的"扬声器"端，它的"麦克风"端出声，
     **不串音**；代价＝装一个内核音频驱动 = 本项目「四不」第 ④ 条（不装驱动）。
  ③ 把默认录音设备临时切到 ①②——系统设置、可逆。
⇒ 我们的口径（守红线）：**只检测 + 引导 + 用户自己点装**，绝不静默装驱动、绝不改系统设置。
"""
from __future__ import annotations

import sys

try:                                     # 控制台/判据在管道里跑时别被 GBK 编码崩掉
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

MMDEV = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio"
NAME_KEY = "{a45c254e-df1c-4efd-8020-67d146a850e0},2"      # PKEY_Device_FriendlyName
DESC_KEY = "{b3f8fa53-0004-438e-9003-51a46e139bfc},6"      # PKEY_Device_DeviceDesc
STATE_TEXT = {1: "已启用", 2: "已停用", 4: "未插入", 8: "已拔出"}

#: 能被当"麦克风"的虚拟/混音设备名特征（大小写无关，取子串）
VIRTUAL_HINTS = ("立体声混音", "stereo mix", "what u hear", "wave out mix", "cable output",
                 "voicemeeter", "scream", "virtual audio", "虚拟音频", "loopback", "混音")
#: 其中"会串音"的那些：它们录的是扬声器正在放的一切，不是一条干净通道
MIX_HINTS = ("立体声混音", "stereo mix", "what u hear", "wave out mix", "混音")

#: 用户可见的口径（判据断言它必须含"不装驱动""你自己装"这类词）
GUIDE_NOTE = ("真语音条要一个「虚拟麦克风」让微信自己录。我们**不替你装驱动、也不改系统设置**："
              "检测到没有时给你怎么装的指引，装完点重检即可。推荐成对的虚拟声卡（如 Scream），"
              "比「立体声混音」干净——后者会把扬声器里正在放的声音一起录进去。")


def _walk(kind: str):
    """枚举 MMDevices 下的一类端点（Capture/Render）。返回 [{guid,name,state}]。"""
    out = []
    try:
        import winreg
    except Exception:
        return out
    root = r"%s\%s" % (MMDEV, kind)
    try:
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root)
    except OSError:
        return out
    i = 0
    while True:
        try:
            guid = winreg.EnumKey(k, i)
        except OSError:
            break
        i += 1
        name = desc = ""
        state = 0
        try:
            dev = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"%s\%s" % (root, guid))
        except OSError:
            continue
        try:
            props = winreg.OpenKey(dev, "Properties")
            for key_name, slot in ((NAME_KEY, "name"), (DESC_KEY, "desc")):
                try:
                    v, _t = winreg.QueryValueEx(props, key_name)
                    if slot == "name":
                        name = str(v)
                    else:
                        desc = str(v)
                except OSError:
                    pass
        except OSError:
            pass
        try:
            state, _t = winreg.QueryValueEx(dev, "DeviceState")
        except OSError:
            pass
        out.append({"guid": guid, "name": name or desc or "?", "state": int(state or 0)})
    return out


def capture_devices():
    return _walk("Capture")


def render_devices():
    return _walk("Render")


def is_active(dev) -> bool:
    """注册表里"活跃"的标准值就是 1；本机实测"立体声混音"给的是 0x10000001（存在但没启用）⇒
    必须按**精确等于 1** 判，不能只看低位（只看低位会把停用设备当成可用，那正是"以为能录却没有"的坑）。"""
    try:
        return int((dev or {}).get("state") or 0) == 1
    except Exception:
        return False


def virtual_mics(only_active: bool = True):
    """可当"麦克风"用的虚拟/混音设备。返回 [{name,state,active,mix,clean}]。"""
    out = []
    for d in capture_devices():
        low = str(d.get("name") or "").lower()
        if not any(h in low for h in VIRTUAL_HINTS):
            continue
        a = is_active(d)
        if only_active and not a:
            continue
        mix = any(h in low for h in MIX_HINTS)
        out.append({"name": d.get("name"), "state": d.get("state"), "active": a,
                    "mix": mix, "clean": not mix})
    return out


def status() -> dict:
    """给控制台/状态接口用：本机有没有可用的虚拟麦克风、干净不干净、该怎么引导。"""
    caps = capture_devices()
    vms = virtual_mics(only_active=True)
    latent = [v for v in virtual_mics(only_active=False) if not v["active"]]
    out = {"ok": bool(vms), "virtual_mics": vms, "inactive_virtual_mics": latent,
           "capture": [{"name": c["name"], "state": c["state"], "active": is_active(c)} for c in caps],
           "guide": GUIDE_NOTE, "why": ""}
    if vms:
        clean = [v["name"] for v in vms if v["clean"]]
        out["why"] = ("检测到可用的虚拟麦克风：%s" % "、".join(v["name"] for v in vms)
                      + ("；其中不串音的：%s" % "、".join(clean) if clean
                         else "；**都是混音类设备，会把扬声器里正在放的声音一起录进去**"))
    elif latent:
        out["why"] = ("有虚拟/混音设备但**没启用**（%s）——先在系统声音设置里把它启用（无需装驱动）"
                      % "、".join(v["name"] for v in latent))
    else:
        out["why"] = "没有可用的虚拟麦克风：装一个成对的虚拟声卡（如 Scream）后点重检"
    return out
