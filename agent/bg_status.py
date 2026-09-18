# -*- coding: utf-8 -*-
"""后台能力矩阵 —— **单一事实源**：每条微信操作路径当前到底走的是「投递档」还是「真鼠标档」。

为什么要它（用户 2026-09-13 的最高目标 + 2026-09-14 的全后台审计）：
  最高目标原文：「我们微信这个项目的最高目标就是全程后台，不抢鼠标，在 Windows 系统下兼容一切情况」
  ⇒ 任何一条路径要么**全程投递**（不动光标、不要求窗口可见；**可能短暂置前（2026-09-18 本机实测：发文字约 1 秒｜切会话 3~7 秒｜切会话失败重试可达约 15 秒），随后自动还回**——
    这是伪激活的代价，跨机实测数字，2026-09-16 按对面 r15 现场改口径，不再写"不打扰你"这种过头话），
    要么**如实标注**它现在只能真鼠标（会动光标、可能短暂置前）并按需跳过。
  「不许悄悄降级」是硬要求，所以这份表既是给用户看的账，也是判据脚本的比对基准
  （`scripts/background_selftest.py` 会拿它去核对代码事实，防止"表上写投递、代码里真鼠标"）。

状态取值：
  posted          全程投递（不动光标 / 不要求可见 / 可能短暂置前（2026-09-18 本机实测：发文字约 1 秒｜切会话 3~7 秒｜切会话失败重试可达约 15 秒），随后自动还回）
  posted_fallback 默认投递，判据不过时**默认不退回真鼠标**（`input.allow_real_fallback` 默认关）——**返回消息里必须写明档位与原因**
  real            目前只能真鼠标（会动光标、可能短暂置前），如实标注
  skipped         本产品不提供（如实说"跳过"，不做）
"""

# 一定非得走前台的路径（会动光标 / 会真占前台）——后台档对这些一律「跳过并说明原因」，不偷偷用真鼠标。
# 2026-09-18 逐条实测（_scratch/fg_ops_probe.py，40ms 采样，明细 _scratch/fg_ops_*.json）：
#   必须前台：朋友圈点赞 / 评论 / 发朋友圈 · 转发视频/文件（系统「选择文件」对话框那一下）· UI 标定 / 真鼠标兜底档（默认关）
#   投递档的前台代价：取 GUI / 朋友圈投递滚动 / 表情面板投递打开 = 0 秒；发文字 ≈1 秒；
#                   切会话 3~7 秒；切会话失败重试可达 ~15 秒；投递 ESC ≈1 秒——全部随后自动还回。
MUST_FOREGROUND = [
    "朋友圈点赞 / 评论 / 发朋友圈",
    "转发视频/文件（系统「选择文件」对话框那一下）",
    "UI 标定 / 真鼠标兜底档（默认关）",
]

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
        "detail": "剪贴板 + 投递 Ctrl+V / 文件投递；不动光标（可能短暂置前（2026-09-18 本机实测：发文字约 1 秒｜切会话 3~7 秒｜切会话失败重试可达约 15 秒），随后自动还回）",
        "evidence": "scripts/send_file_posted_selftest.py",
    },
    {
        "key": "sticker", "label": "发送收藏表情", "status": "posted",
        "detail": "投递点笑脸 → 收藏标签 → 收藏格（点完以数据库回读为准）",
        "evidence": "_scratch/sticker_h_send.py（DB 回读命中、光标未动）",
    },
    {
        "key": "switch_chat", "label": "切换会话", "status": "posted",
        "detail": "**顺序：按键走格 → 点列表 → 搜索兜底**（2026-09-18 深夜按实测重排）："
                  "①已经在目标会话 ⇒ **什么都不做**（不搜索、不置前）；"
                  "②**按键走格**＝往会话列表投方向键走过去（零坐标、不开窗；方向由各会话最后消息时间定，"
                  "每按一格读会话头确认、走过头自动回头；读不到会话头就不按键）——**短距实测约 1 秒**，"
                  "走得远时按格数线性变长，**已限长（单向 ≤4 格）**，超了就换下一条路；"
                  "③列表里看得见那一行就直接点它（一次点击，实测不占主窗前台）；"
                  "④搜索兜底（投递点搜索入口 → 输名字 → 点结果，两套 UI 都认、不依赖滚动）；"
                  "搜索也没成时**默认停手**（不退回滚列表——滚轮虽不动光标，但会话列表会在你眼前滚；"
                  "要回退得打开 `wechat.scroll_list_fallback`）；"
                  "⚠️ 这几条都会发伪激活 ⇒ **可能短暂把微信置前再自动还回你原来的窗口**"
                  "（2026-09-18 本机实测：按键走格 ↑2 格 ≈1 秒｜搜索路线 ≈3.7 秒；随后都还回）。"
                  "**2026-09-19 起更进一步：它会一边干活一边把你原来的窗口摁在最前、把微信压回 Z 序底层**"
                  "（实测：切会话 + 发消息 + 切回三次动作，微信占前台各 **0.00 秒**，消息照样发出、DB 回读命中）。"
                  "**全程不动光标**",
        "evidence": "agent/wechat.py::_switch_by_keys / _click_visible_session / open_chat_by_search"
                    "（都含还前台；实测脚本 _scratch/_probe_fg_by_window.py）· "
                    "**你在全屏玩游戏/放演示时它不动窗**（判据同 Windows 通知系统；会等你，等不到就跳过并记日志）",
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
        "detail": "走的是**蓝点路线**（真实滚轮回顶 → 点蓝点 → 菜单出现 → 点菜单项），"
                  "其中的滚回顶与点蓝点都是全局真实输入 ⇒ 仍是真鼠标档；开「只走后台」时跳过并如实说明。"
                  "（2026-09-16 说明：右键菜单那条链本身已可投递，但**点赞/评论没有走右键**，"
                  "所以不能跟着一起放开——要放开得先实测「投递滚轮 + 投递点蓝点」能不能让菜单出现。）",
        "evidence": "agent/wechat.py::_moments_hover_menu（real_guard + mouse_event 滚轮 + 点蓝点）",
    },
    {
        "key": "poke", "label": "拍一拍", "status": "posted_fallback",
        "detail": "右键**头像** → 菜单「拍一拍」：右键与点菜单项**都走投递**（投主窗弹菜单、"
                  "再投递点菜单项；2026-09-16 实测，全程不动光标（微信可能被短暂置前，实测约 1~2 秒，随后自动还回））。"
                  "投递不成时才按 `input.allow_real_fallback` 决定是否回真鼠标（**默认关**＝不回落）。",
        "evidence": "agent/wechat.py::_send_poke_inner → _right_click_menu（投递优先）· "
                    "_scratch/rclick_avatar.py + rck_menu.py（含真实右键阳性对照）",
    },
    {
        "key": "quote", "label": "引用消息", "status": "posted_fallback",
        "detail": "右键**气泡** → 菜单「引用」：同样**投递优先**（不动光标（可能短暂置前（2026-09-18 本机实测：发文字约 1 秒｜切会话 3~7 秒｜切会话失败重试可达约 15 秒），随后自动还回）），"
                  "投递不成按 `input.allow_real_fallback` 决定是否回真鼠标（默认关）。"
                  "⚠️ 定位那一步仍要窗口**能抓画面**（最小化时先无激活还原；投递档不再要求前台）。",
        "evidence": "agent/wechat.py::_reply_quote_inner → _right_click_menu · "
                    "_scratch/rck_menu.py（投递点菜单项命中，剪贴板为证）",
    },
    {
        "key": "calibrate", "label": "UI 标定", "status": "real",
        "detail": "标定本身要量用户在真实界面上的操作（点击落点/布局）⇒ 真鼠标档，且只在用户主动点标定时跑",
        "evidence": "agent/ui_adapt.py",
    },
]

STATUS_LABEL = {
    "posted": "全程后台",
    "posted_fallback": "后台优先（默认投递；不成立时默认**不**回落真鼠标）",
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
