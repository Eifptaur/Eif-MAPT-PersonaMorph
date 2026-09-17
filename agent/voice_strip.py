# -*- coding: utf-8 -*-
"""真语音条：**合成 → 播进虚拟声卡 → 让微信自己录 → 点发送**（发出去是微信语音条，不是文件）。

为什么单开一个模块（用户 2026-09-17 原话：「我让他发语音条，**不是发音频文件**」）：
`tools.send_voice_reply` 走的是"发文件"那条路（微信里显示成文件）；真语音条要靠**成对的虚拟声卡**
（VB-CABLE 之类）+ 微信自己在录音态里录。这条链 2026-09-15 在实验脚本里跑通过，但**产品里一直没接**
（实验脚本后来清掉了）。这个模块就是那次实验的产品化。

三道闸（缺哪道就如实说，绝不假装发过）：
  ① **虚拟麦克风**（成对虚拟声卡）——`agent/audio_devices.virtual_mics()`
  ② **sounddevice**（把 PCM 播到虚拟声卡"扬声器"那一端）
  ③ **合成引擎**——`agent/voice_models.status()`
还有一道"位置标定"：微信那个"进录音态"的圆圈位置随版本/尺寸变，所以位置存在配置里
（`voice_strip.record_btn` / `send_btn`，渲染区比例），没标定时**拒发并让你先跑 `calibrate`**。

产物与判据：人看的是"语音条"，机器只认 **DB 回读 `type=语音`**（不信 GUI 返回值）。
"""
import os
import time
import wave

from .config import ROOT, get_config

#: 进录音态后"发送"那个绿簇：默认取渲染区右下（老实测 (0.927, 0.924) 量级）
DEFAULT_SEND = (0.932, 0.945)
#: 试标定时沿这一行扫的候选 x 比例（y 取 DEFAULT_SEND 同一带）
CALIB_XS = (0.86, 0.89, 0.92, 0.95)


def _cfg(cfg=None) -> dict:
    try:
        return dict((cfg or get_config()).get("voice_strip") or {})
    except Exception:
        return {}


def _virtual_mic() -> tuple:
    """有没有可用的虚拟麦克风（成对虚拟声卡）⇒ (名字, 说明)。"""
    try:
        from . import audio_devices as ad
        mics = ad.virtual_mics(only_active=True) or []
        clean = [m for m in mics if not ad._is_mixing(m)] if hasattr(ad, "_is_mixing") else mics
        if mics:
            pick = (clean or mics)[0]
            return str(pick.get("name") or ""), ""
        return "", "没检测到虚拟麦克风（要成对的虚拟声卡，如 VB-CABLE；装完在控制台点「重检」）"
    except Exception as e:
        return "", "虚拟声卡检测失败：%s" % e


def _out_device() -> tuple:
    """把声音播到哪块设备（虚拟声卡的"播放"端）⇒ (索引, 名字, 说明)。"""
    try:
        import sounddevice as sd
    except Exception as e:
        return -1, "", "没装 sounddevice（%s）⇒ 播不进虚拟声卡" % type(e).__name__
    want = str(_cfg().get("out_device") or "").strip().lower()
    try:
        devs = sd.query_devices()
    except Exception as e:
        return -1, "", "sounddevice 查不到设备：%s" % e
    for i, d in enumerate(devs):
        n = str(d.get("name") or "")
        if int(d.get("max_output_channels") or 0) <= 0:
            continue
        if want:
            if want in n.lower():
                return i, n, ""
            continue
        if "cable" in n.lower() or "scream" in n.lower():
            return i, n, ""
    if want:
        return -1, "", "配置里指定的输出设备没找到：%s" % want
    return -1, "", "没找到虚拟声卡的播放端（名字里含 CABLE / Scream 的那块）"


def status(cfg=None) -> dict:
    """能不能发真语音条 ⇒ {ok, why, mic, out_dev, out_idx, engine, calibrated}。"""
    c = _cfg(cfg)
    mic, why_mic = _virtual_mic()
    idx, name, why_out = _out_device()
    eng = {}
    try:
        from . import voice_models as vm
        eng = vm.status() or {}
    except Exception as e:
        eng = {"ok": False, "why": "合成引擎查不了：%s" % e}
    calibrated = bool(c.get("record_btn"))
    why = ""
    if not mic:
        why = why_mic
    elif idx < 0:
        why = why_out
    elif not eng.get("ok"):
        why = "没有可用的合成引擎：%s" % (eng.get("why") or "未知")
    elif not calibrated:
        why = "还没标定微信那个「进录音态」的圆圈位置（跑一次 voice_strip.calibrate 即可；标定按钮下版补到控制台）"
    return {"ok": not why, "why": why, "mic": mic, "out_dev": name, "out_idx": idx,
            "engine": (eng.get("engine") or eng.get("backend") or ""), "calibrated": calibrated,
            "switch": bool(c.get("enabled"))}


def _read_pcm(path: str):
    """读成 (采样率, 声道, 样本宽度, 裸 PCM 字节)。"""
    with wave.open(path, "rb") as w:
        return w.getframerate(), w.getnchannels(), w.getsampwidth(), w.readframes(w.getnframes())


def _ensure_wav(path: str) -> tuple:
    """不是 RIFF 就用 ffmpeg 转一份 wav（合成端可能是 mp3）。⇒ (wav 路径, 说明)"""
    try:
        with open(path, "rb") as f:
            head = f.read(4)
    except Exception as e:
        return "", "音频读不了：%s" % e
    if head == b"RIFF":
        return path, ""
    try:
        from . import ffmpeg_bin as fb
        ff = fb.path()
    except Exception:
        ff = ""
    if not ff:
        return "", "产物不是 wav，而这台机器没有 ffmpeg 可转"
    out = os.path.splitext(path)[0] + "_pcm.wav"
    import subprocess
    try:
        subprocess.run([ff, "-y", "-loglevel", "error", "-i", path, "-ar", "22050", "-ac", "1", out],
                       capture_output=True, timeout=120,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:
        return "", "ffmpeg 转 wav 失败：%s" % e
    return (out, "") if os.path.exists(out) else ("", "ffmpeg 没产出 wav")


def play_to_cable(wav_path: str, out_name: str = "") -> tuple:
    """把 wav 播进虚拟声卡（阻塞到播完）。⇒ (ok, 说明)"""
    try:
        import sounddevice as sd
    except Exception as e:
        return False, "没装 sounddevice：%s" % e
    idx, name, why = _out_device()
    if idx < 0:
        return False, why
    try:
        rate, ch, width, pcm = _read_pcm(wav_path)
    except Exception as e:
        return False, "wav 读不了（%s）：%s" % (type(e).__name__, e)
    dtype = {1: "int8", 2: "int16", 4: "int32"}.get(width, "int16")
    try:
        with sd.RawOutputStream(samplerate=rate, channels=ch, dtype=dtype, device=idx) as st:
            st.write(pcm)
        return True, "已播到 %s（%.2f 秒）" % (name, len(pcm) / float(max(1, rate * ch * width)))
    except Exception as e:
        return False, "播到 %s 失败：%s" % (name, e)


def _green_cluster(gui) -> bool:
    """输入区右半边有没有**绿色簇**（进录音态后那个绿色↑发送）⇒ 用它当"进没进录音态"的判据。"""
    try:
        from . import chat_header as ch
        img = ch.grab_render(gui)
        if img is None:
            return False
        w, h = img.size
        band = img.crop((int(w * 0.55), int(h * 0.86), w, h)).convert("RGB")
        px = band.load()
        bw, bh = band.size
        hits = 0
        for y in range(0, bh, 2):
            for x in range(0, bw, 2):
                r, g, b = px[x, y]
                if g > r + 25 and g > b + 25 and g > 90:
                    hits += 1
        return hits >= 12
    except Exception:
        return False


def calibrate(wechat, save: bool = True, log=None) -> dict:
    """**标定**那个"进录音态"的圆圈：沿输入区右下扫几个候选点，谁点了出现绿簇就是它。

    进录音态后**立刻点 ✕（取消）退出**，不留残余；标定结果写回配置 `voice_strip.record_btn`。
    """
    c = _cfg()
    send = tuple(c.get("send_btn") or DEFAULT_SEND)
    gui = wechat._get_gui()
    gui._update_render_rect()
    r = gui.render_rect or (0, 0, 0, 0)
    rw, rh = int(r[2] - r[0]), int(r[3] - r[1])
    tried = []
    for rx in CALIB_XS:
        pt = (int(rw * rx), int(rh * send[1]))
        try:
            ok, why = wechat._click(gui, pt[0], pt[1])
        except Exception as e:
            tried.append((rx, "点击异常：%s" % e))
            continue
        time.sleep(0.7)
        hit = _green_cluster(gui)
        tried.append((rx, "绿簇=%s" % hit))
        if hit:
            # 退出录音态（✕ 取消在最左）
            try:
                wechat._click(gui, int(rw * 0.30), int(rh * send[1]))
            except Exception:
                pass
            time.sleep(0.4)
            if save:
                cfg = get_config()
                seg = cfg.setdefault("voice_strip", {})
                seg["record_btn"] = [rx, send[1]]
                seg.setdefault("send_btn", list(send))
                try:
                    from .config import save_config
                    save_config(cfg)
                except Exception as e:
                    if log:
                        log("warn", "标定结果写配置失败：%s", e)
            return {"ok": True, "record_btn": [rx, send[1]], "tried": tried}
    return {"ok": False, "why": "几个候选点都没进录音态（微信没在前台/没打开聊天/位置完全不同？）",
            "tried": tried}


def send(wechat, chat_id: str, text: str, cfg=None, timeout: float = 60.0) -> tuple:
    """发一条**真语音条**。⇒ (ok, 说明, info)

    流程：查三闸 → 合成 → 播进虚拟声卡（同时微信在录音）→ 点发送 → **只认 DB 回读 `type=语音`**。
    """
    c = _cfg(cfg)
    if not c.get("enabled"):
        return False, "真语音条默认关（控制台「语音回复」里打开「发真语音条」）——关了就不发，不假装。", {}
    st = status(cfg)
    if not st["ok"]:
        return False, st["why"], st
    if not str(text or "").strip():
        return False, "要念的内容是空的", {}
    try:
        from . import voice_models as vm
        path, err, info = vm.make(str(text).strip(), cfg=cfg, timeout=int(timeout))
    except Exception as e:
        return False, "合成失败：%s" % e, {}
    if not path:
        return False, "合成没产出音频：%s" % (err or "未知"), info or {}
    wav, why2 = _ensure_wav(path)
    if not wav:
        return False, why2, info or {}
    gui = wechat._get_gui()
    gui._update_render_rect()
    r = gui.render_rect or (0, 0, 0, 0)
    rw, rh = int(r[2] - r[0]), int(r[3] - r[1])
    rb = c.get("record_btn") or [0.92, 0.95]
    sb = c.get("send_btn") or list(DEFAULT_SEND)
    before = _latest_voice_seq(wechat, chat_id)
    try:
        wechat._click(gui, int(rw * rb[0]), int(rh * rb[1]))     # ① 进录音态
        time.sleep(0.8)
        okp, whyp = play_to_cable(wav)                            # ② 边录边播
        if not okp:
            try:
                wechat._click(gui, int(rw * 0.30), int(rh * rb[1]))   # 失败：取消，别留残余
            except Exception:
                pass
            return False, whyp, info or {}
        time.sleep(0.4)
        wechat._click(gui, int(rw * sb[0]), int(rh * sb[1]))      # ③ 点发送（绿簇）
    except Exception as e:
        return False, "微信侧操作失败：%s" % e, info or {}
    ok, msg = _wait_voice(wechat, chat_id, before, timeout=12.0)
    return ok, (msg if ok else ("语音条没发出去：%s" % msg)), info or {}


def _latest_voice_seq(wechat, chat_id: str):
    """当前会话里最后一条**语音**的 id（回读判据的基线）。"""
    try:
        rows = wechat._db.get_messages(chat_id, limit=20) or []
        for row in rows:
            if str(row.get("type") or "") == "语音":
                return row.get("local_id") or row.get("id")
        return 0
    except Exception:
        return 0


def _wait_voice(wechat, chat_id: str, before, timeout: float = 12.0) -> tuple:
    """等 DB 里出现**新的**语音行（只认回读，不信 GUI）。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            rows = wechat._db.get_messages(chat_id, limit=20) or []
            for row in rows:
                if str(row.get("type") or "") == "语音":
                    cur = row.get("local_id") or row.get("id")
                    if before in (None, 0) or (cur and cur != before):
                        return True, "DB 回读确认：新语音条 local_id=%s" % cur
                    return False, "DB 里还是那条旧语音（local_id=%s）——这一次没发出去" % cur
        except Exception:
            pass
        time.sleep(1.0)
    return False, "等了 %.0f 秒，DB 里没出现新语音条" % timeout
