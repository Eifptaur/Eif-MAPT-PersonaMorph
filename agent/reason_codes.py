# -*- coding: utf-8 -*-
"""失败原因码：把"为什么没做/没发出去"变成**机器可读的稳定码**。

⛔ 为什么要有它（2026-09-22，作者「那开始做吧」后落地的第一件；依据 `WX-chatbot\research\
  兼容性-业界做法调研.md` 附 C）：微软自家的 `winapp ui`（给 agent 用的 Windows UI 自动化 CLI）
   对失败给的是**稳定码**（`target_moved` / `foreground_not_target` / `no_interactive_desktop` /
   `no_target`），而我们全仓的拒发/拒点原因是**中文散文**（"会话没对上"、"抓不到微信画面"…）——
   人能读，但**判据钉不住、统计不了、跨版本对不上**。
   ⇒ 本模块只做两件事：①给一整套**固定的码**；②把散文**分类**到其中的一个码。
   ⚠️ 纪律：**分类结果绝不进群里、也绝不替换原文**（原文照旧给模型/日志；码只做附加字段），
      更不许拿它做"能不能发"的判断——判据仍然是现场证据（DB 回读 / 绿底带 / 会话头）。

码的取向与官方那套一致：**宁可如实说"没做"，也不许把失败说成"做了但对方没收到"**。
"""

from __future__ import annotations

#: 码 → 一句人话（控制台/报告里可以显示；**不往微信侧发**）
CODES = {
    "self_detected": "判为自己发出去的（回声/自家行号/账号命中）——只落库、不喂模型、不回拍",
    "kept": "保留（当别人的消息处理）",
    "filtered": "过滤掉了（系统提示/空内容/内部话术/自测痕迹之类）",
    "no_capture": "抓不到窗口画面（最小化/收进托盘/被遮挡且兜底也失败）",
    "target_moved": "动作前目标窗或落点变了（现量对不上，宁可不做）",
    "identity_unconfirmed": "认不准就是目标会话（宁可漏发，绝不发错）",
    "foreground_not_target": "拿不到前台且它是真鼠标档（闸门拒发）",
    "no_interactive_desktop": "锁屏/安全桌面/无交互桌面（注入类动作做不了）",
    "access_denied": "权限或完整性级别不一致（UIPI 会静默拦掉注入）",
    "db_unreadable": "读不到微信库（解密模式/密钥/陈旧分片/被占用）",
    "key_missing": "模型或服务的凭据没就绪",
    "halted": "机器人处于暂停/停止状态（按你的设定不动手）",
    "busy": "用户正在忙（全屏/正在输入）⇒ 等空档，不硬插",
    "rate_limited": "频率或本轮预算触顶（进重试队列，晚点补）",
    "not_found": "窗口/元素/会话行没找到",
    "timeout": "等待超时（对方没在给结论）",
    "bad_args": "参数不合法或工具名不对",
    "unknown": "其它原因（原文见回执）",
}

#: 分类顺序＝从"最具体"到"最泛"。**顺序有意义**：先命中先算（例：同时含"遮挡"和"超时"⇒ no_capture）
_RULES = (
    # ⚠️ 前三条是**账本（message_ledger）语义**：它记的是"这条为什么被留/被丢"，
    #   若把它们并进"身份/过滤"那几类会得到误导性结论（"判为自己"其实**已经确认了身份**）。
    #   所以这三条必须排在最前、单独成码。
    ("self_detected", ("判为自己", "self_wxid 命中", "自家行号")),
    ("kept", ("保留（当别人的消息处理）", "保留（当别人")),
    ("filtered", ("跳过（系统", "跳过（空", "过滤", "内部故障话术")),
    ("no_interactive_desktop", ("安全桌面", "锁屏", "no_interactive_desktop", "无交互桌面")),
    ("access_denied", ("uipi", "权限", "拒绝访问", "access is denied", "5 拒绝", "管理员")),
    ("no_capture", ("抓不到", "抓不到画面", "看不到画面", "窗口不可见", "已最小化", "收进托盘",
                    "遮挡", "no_capture", "帧全", "假帧", "全黑", "整幅")),
    ("target_moved", ("落点", "现量", "目标窗", "换过尺寸", "矩形", "target_moved", "点偏")),
    ("identity_unconfirmed", ("没认准", "认不准", "会话没对上", "不是目标会话", "身份", "串群",
                              "会话头", "指纹", "目标行", "按名字")),
    ("halted", ("暂停", "已停止", "机器人已", "halted", "stopped")),
    ("busy", ("全屏", "正在输入", "用户忙", "忙闲", "user busy")),
    ("rate_limited", ("重试队列", "退避", "上限", "预算", "频率", "限流")),
    ("db_unreadable", ("数据库", "合并失败", "解密", "密钥缓存", "message_", "读库失败", "sqlite",
                       "contact.db", ".db 失败", "locked")),
    ("key_missing", ("api key", "apikey", "没填", "未配置", "凭据", "余额")),
    ("timeout", ("超时", "timeout", "没等到")),
    ("not_found", ("没找到", "找不到", "不存在", "没有这条", "空结果")),
    ("bad_args", ("参数", "不是合法 json", "未知工具", "缺少")),
)


def classify(text) -> str:
    """把一段中文/英文的失败原文分到一个码；分不出来就给 `unknown`（**绝不猜**）。"""
    s = str(text or "").lower()
    if not s.strip():
        return "unknown"
    for code, keys in _RULES:
        for k in keys:
            if k in s:
                return code
    return "unknown"


def label(code) -> str:
    """码 → 一句人话（给控制台/报告用；未知码原样返回，不编）。"""
    c = str(code or "")
    return CODES.get(c, c or "unknown")


def tag(text) -> str:
    """给原文本**追加**一个码后缀（原文一字不动，方便日志里人机两读）。"""
    s = str(text or "")
    return "%s〔%s〕" % (s, classify(s)) if s else s
