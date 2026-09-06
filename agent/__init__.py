# -*- coding: utf-8 -*-
"""wx-agent 整合包：QQ Agent 的智能大脑 + wechatauto 的微信接入层。

模块划分：
  config       统一配置加载 / 默认值
  util         通用工具（时间格式化 / 文本清洗 / 档位滑条换算 / md 转纯文本）
  llm          OpenAI 兼容 Chat Completions 客户端（重试 / usage / 成本估算）
  web_search   联网搜索（Bing / DeepSeek / 智谱 / 博查 / 百度 / 秘塔 / 自定义）
  safe_fetch   SSRF 全防护抓取（DNS 校验 + IP 固定 + 手动重定向 + 限量读取）
  store        每会话 JSON 消息存档（已读 / 未读语义）
  memory       群友长期印象（每群友一个 JSON 文件）
  sender       出站发送队列（限频 / 真人化间隔 / md 转纯文本 / 切分）
  persona      人设模板库
  prompt       系统提示 + 用户消息组装 + 响应档位判定
  tools        原生工具集（OpenAI function calling 格式）
  wechat       wechatauto 适配层（读消息 / 发消息 / 下载图片）
"""

__version__ = "1.0.0"
