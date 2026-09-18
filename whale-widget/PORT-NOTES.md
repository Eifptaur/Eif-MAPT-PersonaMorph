# 小鲸鱼挂件移植记录（PORT-NOTES）

上游：`MeteorNOX/DeepSeek-Balance-Whale-Widget`（npm 包名 `dsh-whale-widget`）
本目录（`whale-widget/`）是**下游移植副本**，挂到群相控制台上（宿主侧用 Python 重写，见 `agent/whale.py`）。

| 项 | 值 |
|---|---|
| 已 vendor 的上游版本 | **0.3.5**（2026-09-18 发布；上一版 0.2.10） |
| 移植日期 | 2026-09-19 |
| 前端脚本 | `client/widget.js` ← 上游 `assets/whale-widget.js`（0.3.5 起前端脚本挪到 assets/ 下） |
| 宿主侧 | `agent/whale.py`（Python 重写，非 Node） |
| 原文留档 | `upstream-0.3.5/`（README / PROVENANCE / package.json） |
| 许可 | 代码 MIT；**`assets/**` 不在 MIT 范围内**（上游 `PROVENANCE.md`：图片/动图/音效 "as-is 随插件分发，不授予再许可"）。⚠️ 本项目移植并随包分发该挂件**已获原作者同意**（用户 2026-09-19 告知），这是继续 vendor 素材的前提。 |
| 价目表 | **唯一来源** `agent/model_prices.py`（照抄上游 0.3.5 lib/index.js L436-495）。别在别处再写一份 —— 上游 0.3.5 改了表（Flash 2026-09-10 降价）与公式（reasoning ⊆ output，输出只算一次）。 |

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

## 二、接口覆盖（上游 0.3.5 客户端会调 22 个端点）

**已实现（7 个老端点，与 0.2.x 完全一致）**：
`balance.json` · `size.json`（GET/PUT）· `last-turn.json` · `image.png` · `rua.gif` ·
`sound/press.mp3` · `sound/release.mp3`

**本次新增落地（2 个）**：`bubble.json` · `audio.json`（纯配置 ⇒ 真存真读，存进 `data/whale-state.json`）

**如实回"不支持"（13 个，客户端会保留默认值 ⇒ 干净降级）**：
`api-models.json` · `usage-records.json` · `usage-settings.json` · `balance-adjustments.json` ·
`roles.json` · `role-image.png` · `role-pin.json` · `role-delete.json` · `bubble-imgs.json` ·
`bubble-img.png` · `bubble-img-upload.json` · `audio-fragment.wav` · `sound/`

这批都依赖上游的 **DSH 宿主侧能力**（多厂商余额、账本与余额校正、角色/泡泡图片库、音频包管理、
Codex 本地会话统计）。移植版**不回假 `ok:true`**（那会让界面显示假数据），而是
`{"ok": false, "why": "...", "unsupported": true}` —— 客户端对 `ok:false` 一律走默认值
（实测代码：`if (d && d.ok && d.config)` / `if (!d || !d.ok …) return`），所以界面是干净降级：
菜单里能看到入口，点下去会得到"本移植版暂无此能力"。

## 三、重新 vendor 的步骤（上游再发版时照这个做）

1. 取新版：`https://registry.npmjs.org/dsh-whale-widget/-/dsh-whale-widget-<版本>.tgz`（或 GitHub tag）；
2. 覆盖 `client/widget.js` ← `<包>/assets/whale-widget.js`，**重打第一节那一行补丁**；
3. 更新 `upstream-<版本>/`（README / PROVENANCE / package.json）；
4. 对账 `agent/model_prices.py`：上游 `lib/index.js` 顶端的价目表与 `isPeakTime`，有变就改（**只改这一处**）；
5. 跑 `py -3 scripts\whale_widget_selftest.py`（G 段会拦住"忘了打补丁/换错版本"）；
6. 想实测：`runtime\python\python.exe _scratch\_live_whale_widget.py` 起隔离台（**不连微信、不发消息**），
   浏览器打开它打印的 URL，看右下角挂件在不在、点一下菜单出不出来。
