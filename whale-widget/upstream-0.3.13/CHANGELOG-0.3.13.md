## 更新内容

- **修好：官方桌面端里，关闭本插件后再刷新页面会导致「应用无法启动」**（报 `desktop web: failed to load /dsh-whale/widget.js`）—— 这是我 0.3.12 引入的回归，抱歉（#154）。
- **修好：桌面端「挂件有时完全不出现」的时序隐患** —— 注入改为插件一加载就登记，不再等其它服务就绪。

升级：`dsh plugin --profile web update dsh-whale-widget`（或在插件市场里更新），然后**重启 `dsh web`**（桌面端请重开客户端）；浏览器按 **Ctrl+F5** 硬刷新。

<details>
<summary>详细说明（点击展开）</summary>

### 1. #154：关闭插件后刷新 → 桌面端致命启动错误（0.3.12 引入的回归）

**现象**：在插件面板里关掉 `dsh-whale-widget`，然后刷新页面 → 应用弹致命错误、起不来。

**根因（在桌面端 asar 里逐行核实）**：

- 0.3.12 往结构化注入表里推的是 `{ kind:'script-src', src:'/dsh-whale/widget.js' }`；
- 桌面端页面侧解释器对两种 script 行**处理不对称**：
  ```js
  case "script":     createElement + textContent + append   // 没有 await，不可能"加载失败"
  case "script-src": await loadScript(src)                  // 失败即 reject ⇒ reject 掉 __DSH_BOOT_READY__ ⇒ 应用起不来
  ```
- 而外壳把这张注入表在**宿主启动时收集一次**后就缓存、**没有任何刷新路径** ⇒ 插件在运行期被关掉时，表里仍留着这一行，而 `/dsh-whale/*` 路由已注销 ⇒ 加载 404 ⇒ 致命。

**改法**：注入行换成**内联 `script` 行**，由插件自己去建 `<script src="/dsh-whale/widget.js">` 并**吞掉** `onerror`：

```js
{ kind: 'script', placement: 'body',
  text: '(function(){try{var d=document.body||document.head||document.documentElement;if(!d)return;' +
        'var s=document.createElement("script");s.src="/dsh-whale/widget.js";' +
        's.onerror=function(){};d.appendChild(s)}catch(e){}})()' }
```

- 内联行只会被 `createElement + textContent + append` 执行，**不存在"加载失败"** ⇒ 不再可能 reject 掉宿主的启动流程；
- 路由不在时，404 由**我们自己的** `onerror` 吞掉 ⇒ 宿主照常启动；
- Web 形态不受影响：`renderIndexInjections` 把它渲染成内联 `<script>`，`tapIndex` 仍凭文本里的 `/dsh-whale/widget.js` 去重，全链路只注入一次。

> 顺带排除了 `kind:'html'` 方案：桌面端对 `html` 行用的是 `insertAdjacentHTML`，这样插入的 `<script>` **不会执行**。

**取舍（先说清）**：关掉插件后，页面里那行仍会尝试请求一次 `/dsh-whale/widget.js`（得到 404、在控制台留一条网络错误），但**应用能正常启动**。彻底消除需要 DSH 桌面端提供注入表刷新路径，或本插件改造成 `dsh.client` 双面包（见下）。

### 2. 时序加固：注入行不再被"等其它服务"推迟

`#152 / #153` 的两位报告人都验证过"补一行结构化注入"能修好桌面端；但其中一位还记录了一个**无法稳定复现**的隐患：桌面端那张注入表是**宿主启动时一次性收集**的，而本插件的注入行原先注册在 `apply()` 里，`apply()` 又被对象级 `inject: ['webServer','credentials','connection']` 整体推迟 —— 订阅一旦晚于那次收集，这一行就永远进不了表，表现为「桌面端完全不出现」。

现在：**去掉对象级 `inject`**，`apply()` 一进来就注册注入行（它不需要任何服务），其余逻辑原样放进 `ctx.inject([...], cb)` 局部等待 —— 语义与原来等价，但行的登记不再被推迟。

### 3. 验证

| 项 | 结果 |
|---|---|
| 新探针 `_v758-check.mjs` | **35/35** — 含**回归守卫**：用真实的桌面端解释器语义同时跑「旧的 script-src 行 → boot 被 reject（复现 #154）」与「新的内联行 → boot 正常 resolve」；另含内联脚本真实执行（桩 DOM）、`body/head/documentElement` 全空也不抛、行注册早于 `ctx.inject`、`tapIndex` 仍幂等、以及经 DSH 真实 `renderIndexInjections` 渲染后全链路只注入一次 |
| 既有探针 | `_v757` 改成对行的 kind 中立（45/45）；`_v756` 35/35 |
| 全部探针 | **35 个全绿**（对发布副本运行） |
| 第三方参考套件 | **36/36** |
| 其他 | `node --check` ×3；`_verify-package.mjs` 双重通过；发布副本开发机路径 **0** 残留 |

### 4. 已知限制（未在本版处理）

- **开关切换不实时生效**（#154 的第 2 条）：本插件的前端是自建 DOM 的独立 IIFE，不在 DSH 的客户端模块体系（`@deepseek-ai/dsh-client-modules`）里，所以关闭组合包时页面里那份 DOM 无人回收，只能靠刷新清掉。要彻底解决需要把前端改成 `dsh.client` 双面包（`lib/client.js` + `package.json` 的 `dsh.client`），是一次较大的重构，需要单独排期。
- 桌面端的注入表仍无刷新路径 —— 上半部分已使它在"关闭插件"时**无害**，但"重新开启后立即生效"依然需要刷新或重启。

</details>
