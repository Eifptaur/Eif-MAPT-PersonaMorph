# 自定义工具：怎么写、怎么加进来

> **English version is below (see [English](#english)).**
> 本目录（`tools.d/`）就是"给机器人加工具"的地方：**一个工具一个 `.json` 文件**，写好放进来、勾选启用，模型就能用了。

---

## 中文

### 一、先说清楚它是什么（边界）

| | |
|---|---|
| **能做什么** | 调**任意 HTTP 接口**（GET/POST），把返回内容交给模型当参考资料 |
| **不能做什么** | **不执行任何本地代码/命令**（不是插件、不是脚本）；不读你的文件；不碰微信数据 |
| **联网范围** | 只允许请求 `allow_hosts` 里列出的域名；**内网/本机地址永远拒绝**（`localhost`、`127.*`、`10.*`、`192.168.*`、`.local`——即使你写进白名单也一样） |
| **默认状态** | 总开关默认**关**，每个工具默认**不启用**；关着时清单一个都不会加载 |
| **谁的责任** | 对方接口返回什么、记录什么，由**对方服务**决定。**只加你信得过的接口** |

### 二、三步就能跑起来

1. **抄一个例子**：把这个目录里的 `example.json` 复制成 `我的工具.json`，改里面的字段（最少改 `name` / `description` / `url` / `allow_hosts`）；
2. **确认域名**：`url` 的域名必须和 `allow_hosts` 里的一项对上，否则工具会被拒（这是防 SSRF 的硬闸）；
3. **启用**：控制台 →「工具与插件」→ 打开总开关 → 在列表里**勾选**你的工具（或者直接把清单里的 `"enabled"` 改成 `true`）。**下一轮对话**就生效。

> 改完清单不用重启：控制台「重新加载清单」会重扫目录；正在跑的会话在**下一次构建工具清单**时用上新的。

### 三、字段表（全部）

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | ✅ | 工具名，`[a-z_][a-z0-9_]{2,30}`。**不许与内置工具重名**，也不许与别的清单重名（一个能力一个名字） |
| `description` | ✅ | 给模型看的说明：**什么时候该用它**、能拿到什么。写清楚，模型才不会乱调 |
| `url` | ✅ | `http://` 或 `https://`；主机必须在 `allow_hosts` 里 |
| `allow_hosts` | ✅ | 域名白名单（数组）。子域名也算（写 `example.com` 覆盖 `api.example.com`） |
| `method` | | `GET`（默认）或 `POST` |
| `enabled` | | 是否启用，默认 `true`（**建议显式写 `false`**，等你确认没问题再勾） |
| `params` | | 给模型看的参数（**合法 JSON Schema 对象**）。不传参数时写 `{"type":"object","properties":{},"required":[]}` |
| `query` | | GET 查询参数，`{"city": "{city}"}` 里的 `{city}` 会被模型传的参数替换 |
| `body` | | POST 请求体（JSON），同样支持 `{参数名}` 模板 |
| `headers` | | 自定义请求头，如 `{"Authorization": "Bearer …"}` |
| `timeout_ms` | | 单次请求超时（默认取配置里的 `user_tools.timeout_ms`） |
| `response_path` | | 只把返回 JSON 里的这一段给模型，如 `data.answer`（不写就整段给） |
| `max_chars` | | 结果截断长度（默认 `user_tools.max_chars`） |

**模板变量只做字符串替换**，不会求值——`{city}` 会被替换成模型传的城市名，仅此而已。

### 四、三个例子

**① 最简单的 GET（不传参数）**——见 `example.json`：

```json
{
  "name": "get_time",
  "description": "查 UTC 当前时间；有人问「现在几点/今天几号」时用",
  "enabled": false,
  "method": "GET",
  "url": "https://worldtimeapi.org/api/timezone/Etc/UTC",
  "allow_hosts": ["worldtimeapi.org"],
  "params": {"type": "object", "properties": {}, "required": []},
  "response_path": "datetime",
  "max_chars": 200
}
```

**② POST + 请求头带 Key + 只取一段结果**——见 `example-post.json`：

```json
{
  "name": "ask_kb",
  "description": "到自己的知识库接口问一句话，返回答案；问「我们知识库里有没有…」时用",
  "enabled": false,
  "method": "POST",
  "url": "https://kb.example.com/api/query",
  "allow_hosts": ["kb.example.com"],
  "headers": {"Authorization": "Bearer 把这里换成你的Key"},
  "body": {"question": "{question}", "top_k": 3},
  "params": {"type": "object",
             "properties": {"question": {"description": "要问的问题"}},
             "required": ["question"]},
  "response_path": "data.answer",
  "timeout_ms": 10000,
  "max_chars": 2000
}
```

**③ 带参数拼进 URL**：

```json
{
  "name": "get_stock",
  "description": "查 A 股某只股票的最新价；有人问题某只股票多少钱时用",
  "enabled": false,
  "method": "GET",
  "url": "https://api.example.com/quote",
  "allow_hosts": ["api.example.com"],
  "query": {"code": "{code}"},
  "params": {"type": "object",
             "properties": {"code": {"description": "6 位股票代码，如 600519"}},
             "required": ["code"]},
  "response_path": "data.price"
}
```

### 五、启用 / 停用 / 删掉

- **启用**：控制台勾选，或清单里 `"enabled": true`；
- **停用**：取消勾选（等价于把 `enabled` 改成 `false`）——清单文件会原样留着，随时能再开；
- **删掉**：直接删除那个 `.json` 文件即可（本功能不写任何别的状态，删了就是没了）。

### 六、出问题怎么查（面板上会逐条写出来）

打开控制台 →「工具与插件」，**坏清单会逐条列在"清单现状"下面**，常见的几种：

| 面板上会写 | 意思 | 怎么改 |
|---|---|---|
| `JSON 读不出来` | 文件不是合法 JSON（常见：多了逗号、用了注释） | 用编辑器校验一下 JSON（本文件里的 ```jsonc 只在本说明里，清单文件**不能写注释**） |
| `name 不合规` | 名字格式不对 | 改成小写字母/数字/下划线，3~31 个字符 |
| `与内置工具重名` / `与另一份清单重复` | 撞名了 | 换个名字；同一能力别起两个名 |
| `description 太短` | 模型看不出什么时候用它 | 写清"什么时候用 + 拿到什么" |
| `url 必须是 http/https` / `主机不在 allow_hosts 白名单里` | 域名没对上 | 把域名加进 `allow_hosts` |
| `params 必须是 JSON Schema 的对象` | `params` 写成了数组/字符串 | 改成 `{"type":"object","properties":{…}}` |
| `params 里出现了与工具同名的字段` | 把参数名写成了工具名 | 改参数名 |

运行时的错误会**直接返回给模型**（模型会照实告诉你），例如：`请求地址的主机不在白名单里` / `地址指向内网/本机，被拒` / `请求失败（URLError: …）`。

### 七、安全提醒

- 清单里**别写私钥**：一旦把这个目录打进共享包，Key 就跟着走了。要用密钥就走"服务端中转"或本机环境变量方案；
- 只加**你信得过**的接口：模型会把用户的话发到那个接口；
- 白名单尽量写**具体主机**，不要图省事写宽泛域名。

---

## English

### What this is

`tools.d/` is where you add your own tools to the bot: **one tool per `.json` file**. Write the file, tick it
in the console, and the model can use it.

- **It makes HTTP requests (GET/POST) only.** It **never executes local code or commands**, never reads your
  files, and never touches WeChat data.
- **Host whitelist is mandatory** (`allow_hosts`). **Private/loopback addresses are always refused**
  (`localhost`, `127.*`, `10.*`, `192.168.*`, `.local`) — even if you list them in the whitelist.
- **Off by default**: the global switch is off and every tool starts disabled.
- **Whichever service you call is responsible for what it returns.** Only add endpoints you trust.

### Three steps

1. Copy `example.json` to `my-tool.json` and edit at least `name` / `description` / `url` / `allow_hosts`.
2. Make sure the host of `url` appears in `allow_hosts` (otherwise the tool is rejected — that is the SSRF gate).
3. Enable it: console → "工具与插件" → turn the global switch on → tick your tool. It takes effect on the
   **next turn** (no restart needed; use "重新加载清单" to rescan immediately).

### Fields

`name` (required, `[a-z_][a-z0-9_]{2,30}`, must not collide with built-ins or other manifests) ·
`description` (required, tells the model when to use it) · `url` (required, http/https) ·
`allow_hosts` (required) · `method` (GET/POST) · `enabled` · `params` (a valid JSON Schema object) ·
`query` / `body` (support `{param}` string substitution, **no evaluation**) · `headers` · `timeout_ms` ·
`response_path` (pick one field out of the JSON response) · `max_chars`.

### Enabling / disabling / removing

Tick to enable; untick to disable (the file stays); delete the `.json` to remove it — nothing else is stored.

### Troubleshooting

Open the console panel: **bad manifests are listed one by one** (bad JSON, illegal name, duplicate name,
short description, host not whitelisted, `params` not an object schema, a `params` field named like the tool).
Runtime errors are returned to the model verbatim, e.g. `host not in whitelist` / `private address refused` /
`request failed`.

### Security

Do not put API keys in these manifests if you plan to share the folder. Only add endpoints you trust —
whatever the user says may be sent to that endpoint.
