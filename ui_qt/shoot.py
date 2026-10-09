# -*- coding: utf-8 -*-
"""Qt 原型的离屏取证 —— 三套主题 × 三类镜头。

照 `launcher-src/README.md` 硬规矩第 6 条办：
  **出图不许用 `CreateControl()+SWP_SHOWWINDOW`**（那会把窗口真闪到用户屏幕上）。
  C# 侧用的是 `CaptureOffscreen`（`WS_EX_NOACTIVATE` + `Show()` + `DrawToBitmap`）。
  Qt 侧对应做法：`QWidget.grab()` —— 它会**离屏渲染整棵控件树**，
  窗口可以完全不出现在屏幕上，也不抢焦点。

⇒ 这样拍出来的就是**真实控件渲染结果**（不是设计稿、不是手画的 mock）。

用法：
  python shoot.py                # 三主题 × 三类镜头，全部落盘
  python shoot.py --only whale   # 只拍一套（调试用）
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# ⚠️ 必须把**项目根**也放进 sys.path：本脚本在 `ui_qt/` 下，而它要 import `agent.*`
#   （真配置、真字体、真面板渲染）。只加 `ui_qt/` 的话会 `ModuleNotFoundError: No module named 'agent'`
#   —— 表现是"三主题 × 五镜头全部失败"，看着像渲染坏了，其实只是路径没加。
sys.path.insert(0, str(HERE.parent))

# 离屏渲染。
# ⚠️ 实测坑：sandbox 里 `QT_QPA_PLATFORM=windows` 会让 Python
#    进程在启动 Qt 前就被回收（rc=127、stdout/stderr 双空 —— 连报错都写不出来）。
#    改 `offscreen` 平台后一切正常，而且更合"不许闪窗"的初衷：
#    根本不建原生窗口，`grab()` 直接对控件树做离屏栅格化。
#    ⇒ 这条和 C# 侧 ` CaptureOffscreen`（README 硬规矩 6）是同一个目的：
#      **出图绝不许把窗口闪到用户屏幕上。**
os.environ["QT_QPA_PLATFORM"] = "offscreen"
# 取证专用隔离配置：机器人主面板镜头会真写盘（保存回执取证），
# 不能把测试值写进项目根的 config.json —— 指到系统临时目录去。
os.environ.setdefault(
    "WX_AGENT_CONFIG",
    str(Path(os.environ.get("TEMP", os.environ.get("TMP", "/tmp"))) / "qt-shoot-config.json"),
)

from PySide6.QtCore import Qt # noqa: E402
from PySide6.QtWidgets import QApplication # noqa: E402

OUT = HERE / "shots"


_FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc", # Microsoft YaHei / YaHei UI（Regular）
    "C:/Windows/Fonts/msyhbd.ttc", # 同上 Bold
    "C:/Windows/Fonts/simhei.ttf", # SimHei（兜底）
    "C:/Windows/Fonts/simsun.ttc", # SimSun（再兜底）
)


def register_fonts() -> int:
    """把中文字体**显式注册**进 Qt。

    ⚠️ 为什么要这一步：
       本 sandbox 里 `offscreen` 平台下 `QFontDatabase.families()` 返回 **0 个字体族** ——
       Qt 没有扫到系统字体目录。后果是**每个汉字都渲染成豆腐块（□）**，
       看起来像"排版全错"，其实是**环境问题不是代码问题**。
       （`QFontDatabase.addApplicationFont()` 能读这些文件 ⇒ 说明字体文件本身没问题，
         只是 Qt 的扫描路径没拿到。）
       ⇒ 生产环境（用户的 Windows 桌面）不会有这个毛病；但**取证脚本必须自己兜住**，
         否则拍出来的图没法用于判断，也没法给用户看。
    返回成功注册的字体数。
    """
    from PySide6.QtGui import QFontDatabase # noqa: PLC0415

    n = 0
    for p in _FONT_CANDIDATES:
        if not os.path.exists(p):
            continue
        try:
            if QFontDatabase.addApplicationFont(p) >= 0:
                n += 1
        except Exception:
            pass
    return n


def _mk(key: str):
    """建一次窗口。**顺序有讲究**：QApplication 必须最先建，字体探测才敢调。"""
    from stylekit_qt import THEMES, apply_font_to_app, ensure_fonts, resolve_family # noqa: PLC0415

    # ① 先有 app —— 见 `available_ui_families()` 里记的那个硬崩坑
    app = QApplication.instance() or QApplication(sys.argv)

    # ② 注册中文字体（sandbox 里 Qt 扫不到系统字体，不注册就是满屏豆腐块）
    nf = register_fonts()
    print(f"[字体] 注册 {nf} 个字体文件；可见字体族 {len(__import__('PySide6.QtGui', fromlist=['QFontDatabase']).QFontDatabase.families())} 个")

    dfam, bfam, efam = ensure_fonts()
    print(f"[字体] 项目字体 display={dfam!r} body={bfam!r} emoji={efam!r}")

    # ③ 探字体、必要时降级
    t = THEMES[key]
    fam = resolve_family(t.font_family)
    if fam != t.font_family:
        for tk in THEMES.values():
            tk.font_family = fam
        print(f"[字体] 本机没有 {t.font_family}，全主题降级用 {fam}")
    apply_font_to_app(app, t)

    # ④ 最后建窗口
    from shell import Shell # noqa: PLC0415
    w = Shell(t)
    # 关键：不进屏幕。
    # ⚠️ 用 offscreen 平台时本来就无原生窗口；这里再钉一层 WA_DontShowOnScreen，
    #    是为了**万一有人把 QT_QPA_PLATFORM 改成 windows 也仍然不会闪窗**。
    w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    w.resize(1180, 760)
    w.show()
    app.processEvents()
    return w, t


def _save(w, name: str) -> str:
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"{name}.png"
    pm = w.grab()
    pm.save(str(p), "PNG")
    return str(p) # 大小由调用方读


def _teardown(*ws) -> None:
    """关掉并**销毁**这批窗口，再把事件队列排空。

    ⛔ 必须销毁，不能只 `close()`：每个镜头都 `_mk` 一棵完整 `Shell` 控件树，
       而这些树在被 grab 之后若只 close，就**留在内存里不释放**。
       实测（whale 主题）：单跑任意一个镜头都正常，连跑「整窗 → 导航 →
       二次确认」时第三个镜头会**卡死在 `_mk` 里**（前面几个镜头留下的树把
       资源吃光/因果链留在半途）。加 `deleteLater()` + 排空事件后连跑正常。
    """
    for w in ws:
        try:
            w.close()
            w.deleteLater()
        except Exception: # noqa: BLE001 —— 出图收尾失败不该盖住真正的镜头结果
            pass
    QApplication.processEvents()
    QApplication.sendPostedEvents(None, 0) # 让 deleteLater 立刻兑现


def shot_overview(key: str) -> str:
    """镜头 1：整窗 —— 看整体气质与信息密度。"""
    w, _t = _mk(key)
    p = _save(w, f"{key}-1-整窗")
    _teardown(w)
    return p


def shot_nav(key: str) -> str:
    """镜头 2：导航 + 搜索（可用性主战场）。"""
    w, t = _mk(key)
    w.find.setText("发消息") # 触发三路匹配，让候选状态可见
    w._on_find("发消息")
    from PySide6.QtWidgets import QApplication as A # noqa: PLC0415
    A.processEvents()
    p = _save(w, f"{key}-2-导航搜索")
    _teardown(w)
    return p


def shot_confirm(key: str) -> str:
    """镜头 3：二次确认弹窗（原生真模态 —— web 侧做不到的那块）。"""
    w, t = _mk(key)
    from confirm import ConfirmDialog # noqa: PLC0415
    from PySide6.QtWidgets import QApplication as A # noqa: PLC0415

    d = ConfirmDialog(
        t, w,
        "清空全部记忆？",
        "它会忘掉所有群友的印象、聊过的话、以及自己攒下的经验。",
        [
            "所有群友的印象和标签会被删掉，重新开始认识人",
            "它攒下的说话经验、金句都会没有",
            "已经发出去的消息不受影响（那些在微信里）",
            "这个动作**不能撤销**",
        ],
        confirm_label="我真的要清空", cancel_label="算了",
        dangerous=True, typed_word="清空",
    )
    d.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    d.show()
    A.processEvents()
    p = _save(d, f"{key}-3-二次确认")
    _teardown(d, w)
    return p


def _envguide_hint() -> dict:
    """造一份**真的会让人想去调**的环境校准快照（演示用）。

    直接调后端 `agent/env_hint.check()` 拿真结构，再把演示值设成"确实没达标"——
    本机若已全达标就会拍出一屏 ✓，看不出这一屏长什么样（等于没取证）。

    ⚠️ 只有**两项**（微信浅色、微信标准字号）—— 都是"用户点两下就能改"的微信层级。
    ⛔ 不许再传 `scale=`/`ocr_ready=`：显示缩放会把用户**所有软件**的图标显示搞坏；
       OCR 是**程序自己的活**，不该让用户管（2026-10-10 用户当面纠正）。
    """
    from agent import env_hint # noqa: PLC0415
    return env_hint.check(theme="dark", font="大", probe=False)


def _missing_demo() -> dict:
    """造一条"确实缺、且要下载"的缺件（锁**虚拟声卡**：最典型样本）。

    ⛔ 这里**绝不调 `env_guide.status()`**：那会做一次网络探测（读 `logs/console.url`、
       探端口存活）。在离屏取证进程里，一个"显示过的弹窗 + 网络探测 + 再建 `Shell`"的
       组合会把第二次 `_mk` 卡死（实测复现：`shot_envguide` → `status()` → `_mk` 停住，
       而同样三步去掉 `status()` 就正常）。出图只取**静态清单结构**，探测交给真程序跑。

    ⇒ 数据直接读后端 `_mk` 的口径（它内部已把 `download_of(key)` 挂到 `dl`）。
    """
    from agent import env_guide # noqa: PLC0415
    return env_guide._mk( # noqa: SLF001 —— 出图只用它的字段口径，不跑探测
        "vmic", "虚拟声卡（发真语音条）", "发微信语音条要它", False,
        "这台电脑上没有可用的虚拟麦克风 —— 装上它，机器人才能发微信语音条。",
        "装完点重检；它会多出一个「CABLE Input（播放）」和一个「CABLE Output（录音）」",
        kind="driver", action="vmicInstall")


def shot_envguide(key: str) -> str:
    """镜头 6：环境引导（**一个**弹窗）—— 引导用户把微信调成浅色 + 标准字号。

    ⛔ 这里只**提醒**，不替用户改微信（`env_hint` 三条口径）⇒ 图上不会有"帮你改好"。
    """
    w, t = _mk(key)
    from envguide import EnvGuideDialog # noqa: PLC0415
    from PySide6.QtWidgets import QApplication as A # noqa: PLC0415

    d = EnvGuideDialog(t, w, _envguide_hint())
    d.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    d.show()
    A.processEvents()
    p = _save(d, f"{key}-6-环境引导")
    _teardown(d, w)
    return p


def shot_missing(key: str) -> str:
    """镜头 7：缺件引导（**每个缺件一个**弹窗，三段式）—— 拍**初始段**。

    初始段是该交互的门面：一颗「忽略」一颗「下载」＋**写清要下载多大**（用户点名要求）。
    """
    w, t = _mk(key)
    from envguide import MissingGuideDialog # noqa: PLC0415
    from PySide6.QtWidgets import QApplication as A # noqa: PLC0415

    d = MissingGuideDialog(t, w, _missing_demo())
    d._stage = 1
    d._apply_stage()
    d.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    d.show()
    A.processEvents()
    p = _save(d, f"{key}-7-缺件引导")
    _teardown(d, w)
    return p


def shot_missing_progress(key: str) -> str:
    """镜头 8：缺件引导**第 3 段**（等安装时的双进度条）。

    这一段是"帮用户把事办好"的落点 ⇒ 必须留证：下载条 + 安装条**都在**，且写明关掉也不影响。
    """
    w, t = _mk(key)
    from envguide import MissingGuideDialog # noqa: PLC0415
    from PySide6.QtWidgets import QApplication as A # noqa: PLC0415

    d = MissingGuideDialog(t, w, _missing_demo())
    d._stage = 3
    d._apply_stage()
    d.bar_dl.setValue(62) # 下载条走到一半，安装条还没动（真实现是 backend 推进度）
    d.bar_in.setValue(0)
    d.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    d.show()
    A.processEvents()
    p = _save(d, f"{key}-8-缺件引导-进度")
    _teardown(d, w)
    return p


def shot_heal(key: str) -> str:
    """镜头 4：把"服务死掉"现场设出来 —— 直击的那个症状。"""
    w, t = _mk(key)
    from heal import Health, Probe # noqa: PLC0415
    from PySide6.QtWidgets import QApplication as A # noqa: PLC0415

    w._show_probe(Probe(
        Health.DEAD,
        "演示：后台进程已经退了，但窗口还开着",
        fix_hint="点『现在就拉起来』", can_self_heal=True,
    ))
    A.processEvents()
    p = _save(w, f"{key}-4-后台死掉")
    _teardown(w)
    return p


def shot_botpanel(key: str) -> str:
    """镜头 5：机器人主面板真配置—— 新四行 + 保存回执 + 鲸语立即生效。

    填三行 → 点保存（真写盘，隔离配置路径）→ 截「已保存」回执。
    鲸语开关保存在 `_save_bot_panel` 里走顶栏同一条 `_pick` 路径 ⇒
    截图里整窗文案已换鲸语 —— 一张图同时证明「保存→写盘→立即应用」。
    """
    w, _t = _mk(key)
    from PySide6.QtWidgets import QApplication as A # noqa: PLC0415

    w.nick.setText("群小鲸")
    w.self_nick.setText("朕")
    w.tier.cb.setCurrentIndex(3)
    w.sw_emoji.setChecked(True)
    w._save_bot_panel()
    A.processEvents()
    p = _save(w, f"{key}-5-机器人主面板")
    _teardown(w)
    return p


SHOTS = [
    ("整窗（看气质）", shot_overview),
    ("导航搜索（看可用性）", shot_nav),
    ("二次确认（原生真模态）", shot_confirm),
    ("后台死掉（那个场景）", shot_heal),
    ("机器人主面板（真配置+保存回执）", shot_botpanel),
    ("环境引导（一个弹窗·调微信）", shot_envguide),
    ("缺件引导（每件一个·初始段）", shot_missing),
    ("缺件引导（每件一个·进度段）", shot_missing_progress),
]


def main() -> int:
    from shell import set_per_monitor_dpi # noqa: PLC0415
    from stylekit_qt import THEMES # noqa: PLC0415

    set_per_monitor_dpi()
    keys = list(THEMES.values()) and list(THEMES)
    if "--only" in sys.argv:
        keys = [sys.argv[sys.argv.index("--only") + 1]]

    made: list[tuple[str, str]] = []
    for k in keys:
        for label, fn in SHOTS:
            try:
                p = fn(k)
                # 每次都要重新 mk 一个 app 上下文；这里不复用是为了隔离（原型够用）
                sz = os.path.getsize(p) if os.path.exists(p) else 0
                made.append((f"{k} · {label}", f"{p}  ({sz // 1024} KB)"))
            except Exception as e: # noqa: BLE001
                made.append((f"{k} · {label}", f"失败 {type(e).__name__}: {e}"))

    for n, info in made:
        print(f"  {n:34s} {info}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
