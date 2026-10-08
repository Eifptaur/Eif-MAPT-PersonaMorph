# 小鲸鱼挂件移植记录（PORT-NOTES）

上游：`MeteorNOX/DeepSeek-Balance-Whale-Widget`（npm 包名 `dsh-whale-widget`）
本目录（`whale-widget/`）是**下游移植副本**，挂到群相控制台上（宿主侧用 Python 重写，见 `agent/whale.py`）。

| 项 | 值 |
|---|---|
| 已 vendor 的上游版本 | **0.3.18**（2026-10-06 升；之前：0.3.13 = 2026-09-26、0.3.9 = 2026-09-21、0.3.5 = 2026-09-18）|
| 移植日期 | 2026-09-19（0.3.5）；历次 re-vendor：2026-09-21 → 0.3.9、2026-09-26 → 0.3.13、**2026-10-06 → 0.3.18** |
| 前端脚本 | `client/widget.js` ← 上游 `assets/whale-widget.js`（0.3.5 起前端脚本挪到 assets/ 下） |
| 宿主侧 | `agent/whale.py`（Python 重写，非 Node） |
| 原文留档 | `upstream-0.3.18/`（README / PROVENANCE / package.json / LICENSE）—— 旧版留档随升级删除。⚠️ npm 包里**没有 CHANGELOG**（旧留档那份来自 GitHub；本机到 api.github.com 不通 ⇒ 本次没取） |
| 许可 | 代码 MIT；**`assets/**` 不在 MIT 范围内**（上游 `PROVENANCE.md`：图片/动图/音效 "as-is 随插件分发，不授予再许可"）。⚠️ 本项目移植并随包分发该挂件**已获原作者同意**，这是继续 vendor 素材的前提。 |
| 价目表 | **唯一来源** `agent/model_prices.py`。✅ **0.3.18 已重新逐行对数：与 0.3.9/0.3.5 的表和公式一致**（Flash 命中 0.02/0.04 · 未命中 1/2 · 输出 4/8；Pro 0.15/0.3 · 4.5/9 · 13.5/27；峰谷规则未变）⇒ 本次无需改数。⛔ 别在别处再写一份（上游 0.3.5 改过表：Flash 2026-09-10 降价；也改过公式：`reasoning ⊆ output` ⇒ **输出只算一次**）。 |
| 素材 | ✅ **0.3.18 的 12 个媒体文件与 0.3.13 逐字节相同**（sha256 全等）⇒ 本次只换脚本、不重 vendor 素材 |

## 一、相对上游的改动（**唯一一处，重新 vendor 时必须重打**）

`client/widget.js` 里 `dshwIsChatRoot()` 开头一行：

```js
if (!r) r = document.body        // ← 群相移植补丁 v1（唯一改动）
```

为什么必须打：上游只在「DSH 主聊天界面」挂载 —— `dshwIsChatRoot(document.getElementById('root'))`
要求 `#root` 里存在 composer（`textarea` / `[contenteditable]`）。**群相控制台是普通页面、没有 `#root`**
⇒ 原样搬过来**完全不挂载**（2026-09-19 隔离实测：脚本已加载、`[class^=dshwv]` 节点 0 个、页面无报错）。
补丁只放宽"页面根"，其余自检与约束（检测到 composer 前不碰 DOM、不注册全局监听）**原样保留**。

机械判据：`scripts/whale_widget_selftest.py` G 段 —— 该行必须**恰好出现一次**，且
`client/widget.js` 必须带 `dshwv-` 类名与 0.3.x 的菜单文案（证明换的是新版而不是旧版）。

## 二、接口覆盖（**以代码为准，本文不再手抄清单**）

⛔ **别再在这儿手抄一份端点清单** —— 手抄必然过时：本文原来写着"已实现 7 个、不支持 13 个"，
而本轮（升 0.3.18）对账时实测宿主**已实现 18 条 GET + 3 条 POST**，不支持的只剩 2 条。
唯一权威来源是这两处：

| 看什么 | 去哪看 |
|---|---|
| **注册了哪些路径/方法** | `agent/routes.py` 的 `/dsh-whale/*` 登记表（网关/口令也按这一族前缀判） |
| **GET 实际怎么答** | `agent/webui.py::WebUI._whale_get`（`do_GET` 按 `path.startswith("/dsh-whale/")` 整族转交） |
| **哪些"如实回不支持"** | `agent/whale.py::_UNSUPPORTED`（目前：`sound/` 目录式入口、**`wait.json`**） |

**0.3.13 → 0.3.18 的接口增量（本次唯一需要动的）**：
- **新增 `wait.json`**（上游 0.3.14 起）：客户端**每秒轮询**它，用来知道"有没有挂起的交互
  （提问 / 授权）、是哪一类、对话名"，据此弹**常驻泡泡** + 响提示音，并给文案里的 `{session}` 取值。
  ⚠️ 我们**暂无"挂起交互"这个状态** ⇒ 已登记进 `_UNSUPPORTED`（**如实回不支持**，客户端走默认值：
  不弹挂起泡泡、`{session}` 显示"当前对话"，**不会显示假数据**）。
  ⭐ **将来可接**：`agent/pending_decisions.py` 里就有"等用户拍板"的队列 ⇒ 接上它，这条就能亮起来。
- 其余端点与 0.3.13 **完全一致**（本轮用正则从新脚本里抠出来逐个对过）。

**为什么"不支持"要如实回、不许回假 `ok:true`**：客户端对 `ok:false` 一律走默认值
（实测代码 `if (d && d.ok && d.config)` / `if (!d || !d.ok …) return`）⇒ 界面干净降级
（菜单里看得到入口，点下去得到"本移植版暂无此能力"）；回假成功则会让界面**显示假数据**。

## 三、重新 vendor 的步骤（上游再发版时照这个做）

1. 取新版：`https://registry.npmjs.org/dsh-whale-widget/-/dsh-whale-widget-<版本>.tgz`（或 GitHub tag）；
2. 覆盖 `client/widget.js` ← `<包>/assets/whale-widget.js`，**重打第一节那一行补丁**；
3. 更新 `upstream-<版本>/`（README / PROVENANCE / package.json）；
4. 对账 `agent/model_prices.py`：上游 `lib/index.js` 顶端的价目表与 `isPeakTime`，有变就改（**只改这一处**）；
5. 跑 `py -3 scripts\whale_widget_selftest.py`（G 段会拦住"忘了打补丁/换错版本"）；
6. 想实测：起控制台后跑 `一键启动.exe`，挂件窗应当自己出现 —— 看右下角挂件在不在、点一下菜单出不出来。
   挂件**只跟控制台通信**（不连微信、不发消息），所以随手开关它不会打扰任何会话。
