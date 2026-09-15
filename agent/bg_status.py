# -*- coding: utf-8 -*-
"""后台能力矩阵 —— **单一事实源**：每条微信操作路径当前到底走的是「投递档」还是「真鼠标档」。

为什么要它（用户 2026-09-13 的最高目标 + 2026-09-14 的全后台审计）：
  最高目标原文：「我们微信这个项目的最高目标就是全程后台，不抢鼠标，在 Windows 系统下兼容一切情况」
  ⇒ 任何一条路径要么**全程投递**（不动光标、不要求窗口可见；**可能短暂置前约 1~3 秒后自动还回**——
    这是伪激活的代价，跨机实测数字，2026-09-16 按对面 r15 现场改口径，不再写"不打扰你"这种过头话），
    要么**如实标注**它现在只能真鼠标（会动光标、可能短暂置前）并按需跳过。
  「不许悄悄降级」是硬要求，所以这份表既是给用户看的账，也是判据脚本的比对基准
  （`scripts/background_selftest.py` 会拿它去核对代码事实，防止"表上写投递、代码里真鼠标"）。

状态取值：
  posted          全程投递（不动光标 / 不要求可见 / 可能短暂置前约 1~3 秒后自动还回）
  posted_fallback 默认投递，判据不过时**默认不退回真鼠标**（`input.allow_real_fallback` 默认关）——**返回消息里必须写明档位与原因**
  real            目前只能真鼠标（会动光标、可能短暂置前），如实标注
  skipped         本产品不提供（如实说"跳过"，不做）
"""

# key 用英文（代码里做比对），label 给用户看
PATHS = [
    {
        "key": "send_text", "label": "发送文字", "status": "posted_fallback",
        "detail": "会话头三态闸判 ok ⇒ 投递（WM_CHAR 打字 + 投递点「发送」按钮）；"
                  "判 mismatch / 没参照 ⇒ 退回真鼠标档，返回值里写明档位",
        "evidence": "scripts/background_selftest.py · _scratch/send_postclick.py（3/3 落库、光标未动）",
    },
    {
        "key": "send_media", "label": "发送图片 / 文件", "status": "posted",
        "detail": "剪贴板 + 投递 Ctrl+V / 文件投递；不动光标（可能短暂置前约 1~3 秒后自动还回）",
        "evidence": "scripts/send_file_posted_selftest.py",
    },
    {
        "key": "sticker", "label": "发送收藏表情", "status": "posted",
        "detail": "投递点笑脸 → 收藏标签 → 收藏格（点完以数据库回读为准）",
        "evidence": "_scratch/sticker_h_send.py（DB 回读命中、光标未动）",
    },
    {
        "key": "switch_chat", "label": "切换会话", "status": "posted",
        "detail": "投递点会话行（慢节奏）+ OCR 确认会话头；不负责改前台",
        "evidence": "agent/wechat.py::switch_chat_posted",
    },
    {
        "key": "moments_open", "label": "打开朋友圈", "status": "posted_fallback",
        "detail": "投递点「发现」→ 投递点「朋友圈」，判据＝发现页 OCR / 主窗灰度差 / 子窗枚举；"
                  "投递失败才退回真鼠标档（会动光标、可能短暂置前），消息里写明。"
                  "⚠️ 判据要抓画面 ⇒ **窗口须留在屏幕上**（被别的窗口盖住也行：优先 PrintWindow）；"
                  "微信被最小化时如实拒绝（判不了就不动手）",
        "evidence": "_scratch/moments_*（发现页与信息流都能纯后台打开，光标与前台未变）· "
                    "_scratch/后台能力-真机记录.md（最小化时如实拒绝的真机读数）",
    },
    {
        "key": "moments_scroll", "label": "刷朋友圈", "status": "posted_fallback",
        "detail": "投递 WM_MOUSEWHEEL（多格 + 间隔）；滚后以主窗灰度差判「内容真动了」；"
                  "同样要求窗口留在屏幕上（最小化 ⇒ 如实拒绝）",
        "evidence": "_scratch/moments_*（下滚 6 格差值 0.389、反向精确回位 0.000）",
    },
    {
        "key": "moments_publish", "label": "发朋友圈（纯文字）", "status": "real",
        "detail": "草稿投递可写进编辑窗，但**「发表」那一下尚未取得投递取证** ⇒ 不冒充后台："
                  "默认不点发表；开启「只走后台」时直接跳过",
        "evidence": "_scratch/moments_k_draft.py（实验明确记「发表、取消一个都没按」）",
    },
    {
        "key": "moments_like_comment", "label": "朋友圈点赞 / 评论", "status": "real",
        "detail": "右键菜单类：投递右键尚未实测（MessageBackend 对 right=True 直接拒绝）⇒ 真鼠标档",
        "evidence": "agent/input_backend.py::click（right=True 直接返回「尚未实测」）",
    },
    {
        "key": "poke", "label": "拍一拍", "status": "real",
        "detail": "要右键头像/气泡再点菜单项 ⇒ 真鼠标档；开「只走后台」时跳过并如实说明",
        "evidence": "agent/wechat.py::_send_poke_inner",
    },
    {
        "key": "quote", "label": "引用消息", "status": "real",
        "detail": "右键气泡 → 菜单「引用」⇒ 真鼠标档；开「只走后台」时跳过并如实说明",
        "evidence": "agent/wechat.py::_reply_quote_inner",
    },
    {
        "key": "calibrate", "label": "UI 标定", "status": "real",
        "detail": "标定本身要量用户在真实界面上的操作（点击落点/布局）⇒ 真鼠标档，且只在用户主动点标定时跑",
        "evidence": "agent/ui_adapt.py",
    },
]

STATUS_LABEL = {
    "posted": "全程后台",
    "posted_fallback": "后台优先（兜底会动鼠标）",
    "real": "真鼠标",
    "skipped": "已跳过",
}

_STATUS_ORDER = {"posted": 0, "posted_fallback": 1, "real": 2, "skipped": 3}


def paths() -> list:
    """按"后台程度"排序返回矩阵（好读的那份，给控制台/报告用）。"""
    return sorted([dict(p) for p in PATHS], key=lambda p: (_STATUS_ORDER.get(p["status"], 9), p["key"]))


def get(key: str) -> dict:
    for p in PATHS:
        if p["key"] == key:
            return dict(p)
    return {}


def status() -> dict:
    """给 /api/status 用的机器可读快照。"""
    ps = paths()
    counts = {}
    for p in ps:
        counts[p["status"]] = counts.get(p["status"], 0) + 1
    return {"paths": ps, "counts": counts, "labels": STATUS_LABEL,
            "summary": "全程后台 %d 条 · 后台优先 %d 条 · 真鼠标 %d 条"
                       % (counts.get("posted", 0), counts.get("posted_fallback", 0),
                          counts.get("real", 0) + counts.get("skipped", 0))}


def background_only_reason(label: str) -> str:
    """统一话术：开了「只走后台」时，真鼠标路径怎么拒绝。"""
    return ("「只走后台」已开启：%s 目前只能用真鼠标（会动光标、可能短暂置前），"
            "本次**跳过**、不动你的鼠标" % label)
