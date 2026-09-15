# -*- coding: utf-8 -*-
"""第 20 条判据：图/文/视频分流选模型 + 视频读取（不需要微信、不联网）。

跑法： py -3 scripts\\model_video_selftest.py      退出码 0=全过 / 1=有失败
A 分流判定与候选链（带图自动识别、留空回主模型、与备选链共存）· B 视频读取（**用真 ffmpeg 合成一段视频再真读**，
含"缺 ffmpeg / 文件不存在 / 不是视频 / 没声音"四条诚实路径）· C 接线与文案 · D 阴性对照。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from agent import model_routes as mr          # noqa: E402
from agent import video_read as vr            # noqa: E402
from agent import llm                         # noqa: E402

PASS, FAIL = [], []
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


def mk_video(path, seconds=2, size="64x48", with_audio=True):
    """用 ffmpeg 合成一段最小视频（testsrc 画面 + 正弦音）——判据要跑真链路，不能只打桩。"""
    ff = vr.ffmpeg_path()
    if not ff:
        return False
    args = [ff, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=%d:size=%s:rate=5" % (seconds, size)]
    if with_audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=440:duration=%d" % seconds]
    args += ["-shortest", "-pix_fmt", "yuv420p", path]
    r = subprocess.run(args, capture_output=True, text=True, timeout=60,
                       creationflags=NO_WINDOW, encoding="utf-8", errors="ignore")
    return r.returncode == 0 and os.path.isfile(path) and os.path.getsize(path) > 0


def main():
    tmp = tempfile.mkdtemp(prefix="mv-judge-")
    mr.stats_path = lambda: os.path.join(tmp, "routes_stats.json")
    real_mr_route = mr.route
    real_mr_routes = mr.routes

    print("== A. 输入类型判定 ==")
    ok("纯文字 ⇒ text", mr.kind_of([{"role": "user", "content": "你好"}]) == "text")
    ok("消息里带 image_url ⇒ image（自动判，不用调用方操心）",
       mr.kind_of([{"role": "user", "content": [{"type": "text", "text": "x"},
                                                {"type": "image_url", "image_url": {"url": "u"}}]}]) == "image")
    ok("工具结果是纯字符串 ⇒ text", mr.kind_of([{"role": "tool", "content": "ok"}]) == "text")
    ok("空/异常输入不炸", mr.kind_of(None) == "text" and mr.kind_of([{}]) == "text")

    print("== B. 分流选模型 + 与备选链共存 ==")
    base = {"model": "cheap-text", "fallback_models": ["fb1"]}
    ok("没配分流 ⇒ 候选就是主模型+备选（默认行为不变）",
       llm.candidates(dict(base), kind="text") == ["cheap-text", "fb1"])
    routed = dict(base, model_routes={"image": "vlm-1"})
    ok("配了带图分流 ⇒ 带图请求用分流模型，备选照旧跟在后面",
       llm.candidates(routed, kind="image") == ["vlm-1", "fb1"])
    ok("同一次配置下，纯文字仍走主模型",
       llm.candidates(routed, kind="text") == ["cheap-text", "fb1"])
    ok("视频没单独配 ⇒ 跟「带图」同一个",
       mr.route(dict(model_routes={"image": "vlm-1"}), "video") == "vlm-1")
    ok("视频单独配了就用自己的",
       mr.route({"model_routes": {"image": "vlm-1", "video": "vlm-v"}}, "video") == "vlm-v")
    ok("分流留空 ⇒ 回主模型（空串）", mr.route({"model_routes": {"image": ""}}, "image") == "")

    print("== C. mark/note 记账 ==")
    mr.note("image", "vlm-1", reason="分流命中")
    mr.note("image", "vlm-1")
    mr.note("text", "", reason="未配分流，用主模型")
    snap = mr.snapshot()
    ok("按「类型→模型」分别计数", snap["counts"].get("image→vlm-1") == 2, snap["counts"])
    ok("主模型那条记成「主模型」而不是空",
       snap["counts"].get("text→主模型") == 1, snap["counts"])
    ok("last 记下最近一次的类型与原因", snap["last"]["kind"] == "text" and snap["last"]["reason"])

    print("== D. 视频读取（真 ffmpeg：合成→真读）==")
    p = vr.probe()
    ok("探测结果齐全（ffmpeg 路径/识别可用性/就绪）",
       set(p.keys()) >= {"ffmpeg", "asr", "ready", "why"})
    if not p["ready"]:
        ok("⚠️ 本机没有 ffmpeg ⇒ 只验诚实路径（跳过真读）", True, p["why"])
    else:
        vid = os.path.join(tmp, "clip.mp4")
        ok("合成一段 2 秒测试视频成功", mk_video(vid), os.path.getsize(vid) if os.path.isfile(vid) else 0)
        ok("读出时长（从 ffmpeg 输出解析，不依赖 ffprobe）",
           (vr.duration_seconds(vid) or 0) > 1.0, vr.duration_seconds(vid))
        res = vr.read(vid, max_frames=3, max_seconds=10)
        ok("真抽帧成功且帧文件都在", res["ok"] and len(res["frames"]) == 3
           and all(os.path.isfile(f) and os.path.getsize(f) > 0 for f in res["frames"]), res.get("note"))
        ok("备注里写清抽了几帧/时长", "抽了 3 帧" in (res.get("note") or ""), res.get("note"))
        ok("音频那条也如实交代（能识别就写识别结果，不能就写原因）",
           bool(res.get("audio_text")) or bool(res.get("audio_why")), res.get("audio_text") or res.get("audio_why"))
        noisy = os.path.join(tmp, "silent.mp4")
        mk_video(noisy, seconds=1, with_audio=False)
        res2 = vr.read(noisy, max_frames=2, max_seconds=5)
        ok("没有音轨的视频：帧照样给、音频如实说（不假装听到了）",
           res2["ok"] and len(res2["frames"]) == 2 and (not res2["audio_ok"])
           and bool(res2["audio_why"]), res2.get("audio_why"))
        vr.cleanup(res["dir"])
        ok("临时目录被清掉（不留垃圾）", not os.path.isdir(res["dir"]))
        ok("抽帧数被夹到上限内（要 99 帧也最多 8）",
           (lambda r: r["ok"] and len(r["frames"]) <= vr.MAX_FRAMES)(
               vr.read(vid, max_frames=99, max_seconds=5)))

    print("== E. 诚实路径（做不到就说做不到）==")
    bad = vr.read(os.path.join(tmp, "根本没有这个文件.mp4"))
    ok("文件不存在 ⇒ ok=False 且给出原因（不是空帧假成功）",
       (not bad["ok"]) and bad["error"] and not bad["frames"], bad["error"])
    junk = os.path.join(tmp, "notvideo.mp4")
    with open(junk, "wb") as f:
        f.write(b"this is definitely not a video")
    bad2 = vr.read(junk)
    ok("不是视频 ⇒ ok=False 且说明读不出时长",
       (not bad2["ok"]) and bad2["error"] and not bad2["frames"], bad2["error"])

    print("== F. 接线与文案 ==")
    defs = {d["name"]: d for d in __import__("agent.tools", fromlist=["x"])._builtin_tool_defs()}
    ok("read_video 工具注册了", "read_video" in defs)
    ok("工具描述写明「读不了就说读不了、别假装看过」",
       "绝不假装看过" in defs["read_video"]["description"] and "照实说读不了" in defs["read_video"]["description"])
    html = open(os.path.join(ROOT, "agent", "console_html.py"), encoding="utf-8").read()
    wui = open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
    cfg_py = open(os.path.join(ROOT, "agent", "config.py"), encoding="utf-8").read()
    cex = open(os.path.join(ROOT, "config.example.json"), encoding="utf-8").read()
    prc = open(os.path.join(ROOT, "agent", "prompt.py"), encoding="utf-8").read()
    ok("控制台有分流三行 + 读数", all(k in html for k in ('data-cfg="api.model_routes.text"',
                                                        'data-cfg="api.model_routes.image"',
                                                        'data-cfg="api.model_routes.video"', 'id="routeStat"')))
    ok("控制台有视频读取三键 + 读数", all(k in html for k in ('data-cfg="video_read.enabled"',
                                                             'data-cfg="video_read.max_frames"',
                                                             'data-cfg="video_read.max_seconds"', 'id="videoStat"')))
    ok("读数来自 /api/status", 'st["model_routes"] = _mrt.snapshot()' in wui
       and 'st["video_read"] = _vrd.snapshot()' in wui)
    ok("提示词里写了视频可以用 read_video", "read_video 读" in prc)
    ok("配置默认段 + 示例同步",
       '"model_routes"' in cfg_py and '"video_read"' in cfg_py
       and '"model_routes"' in cex and '"video_read"' in cex)
    ok("文案写明「留空＝用主模型」与「抽帧是采样」", "留空＝用上面的主模型" in html and "抽帧是采样" in html)

    print("== G. 阴性对照 ==")
    ok("阴性对照：把 kind 显式写成 text 时，带图请求也走主模型（证明分流是按判定走，不是恒走分流）",
       llm.candidates(dict(routed), kind="text") == ["cheap-text", "fb1"])
    ok("阴性对照：没配分流时 image 也不会冒出分流模型",
       llm.candidates(dict(base), kind="image") == ["cheap-text", "fb1"])

    # ── H. 音频识别的返回约定（2026-09-15 抓出的真 bug 的回归守卫）──────────────
    # `voice.recognize_wav()` 的约定是 **(文本, 错误说明)**；`video_read.read()` 一度写成
    # `ok_flag, text = ...`（顺序反了）⇒ 识别到的文本被当成功标志、错误说明被当文本，
    # 结果 **微信视频的音频识别结果永远传不出来**（audio_text 恒为空）。这里用替身把它钉住。
    print("== H. 音频识别返回约定（真 bug 的回归守卫）==")
    from agent import voice as _voice
    _real_rec = _voice.recognize_wav
    _real_ex = vr.extract_audio
    _real_fr = vr.extract_frames
    try:
        vr.extract_frames = lambda p, d, count=4: {"ok": True, "frames": ["f.jpg"], "duration": 3.0}
        vr.extract_audio = lambda p, w, max_seconds=60: True
        _voice.recognize_wav = lambda w, max_seconds=60: ("这是一条测试语音", "")
        r = vr.read("fake.mp4", max_frames=1, max_seconds=5)
        ok("替身：识别到文本时 audio_text 要真的带出来（顺序写反就带不出来）",
           r.get("audio_text") == "这是一条测试语音", repr(r.get("audio_text"))[:40])
        ok("替身：audio_ok 为真", r.get("audio_ok") is True)
        ok("替身：有文本时不该写 audio_why", not (r.get("audio_why") or ""),
           repr(r.get("audio_why"))[:40])
        _voice.recognize_wav = lambda w, max_seconds=60: ("", "本机没装识别引擎")
        r2 = vr.read("fake.mp4", max_frames=1, max_seconds=5)
        ok("替身：引擎报错时 audio_ok 为假、原因带出来",
           r2.get("audio_ok") is False and "识别引擎" in (r2.get("audio_why") or ""),
           repr(r2.get("audio_why"))[:40])
        _voice.recognize_wav = lambda w, max_seconds=60: ("   ", "")
        r3 = vr.read("fake.mp4", max_frames=1, max_seconds=5)
        ok("替身：跑通但没听出内容 ⇒ 如实说「没听出可辨认的说话内容」",
           r3.get("audio_ok") is False and "没听出可辨认" in (r3.get("audio_why") or ""),
           repr(r3.get("audio_why"))[:40])
    finally:
        _voice.recognize_wav = _real_rec
        vr.extract_audio = _real_ex
        vr.extract_frames = _real_fr

    mr.route = real_mr_route
    mr.routes = real_mr_routes
    shutil.rmtree(tmp, ignore_errors=True)
    print("\n== 汇总：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  - " + f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
