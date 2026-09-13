# 自定义工具清单目录（tools.d/）

把 **一个工具一个 `.json` 文件**放进这个目录，然后在控制台「工具与插件」里**勾选**，模型才能用它。
默认总开关是**关**的（`user_tools.enabled`），关着时这些清单一个都不会加载。

## 这是什么形态（先把边界说清楚）

- **只发 HTTP**（GET/POST）：本功能**不会在你机器上执行任何代码**，所以它是"加接口"，不是"加脚本"。
- **域名白名单必填**（`allow_hosts`）：名单外的主机一律不请求；内网/本机地址（localhost、私有 IP、`.local`）
  **永远拒绝**——即使你把它写进白名单也一样。
- **参数必须是合法的 JSON Schema 对象**（`params`）：它直接进模型的提示词。
- **坏清单不会静默**：JSON 坏了、名字不合规、与内置工具重名、`params` 不是对象……每一条都会在控制台面板里列出来。
- **第三方工具＝别人写的接口**：对方接口拿到什么、返回什么由对方决定；请只加你信得过的服务。

## 字段

```jsonc
{
  "name": "get_time",                       // 必填：[a-z_][a-z0-9_]{2,30}，不许与内置工具重名
  "description": "查某个时区的当前时间",      // 必填：会进提示词，写清"什么时候该用它"
  "enabled": false,                          // 逐项开关（控制台也能勾）
  "method": "GET",                           // GET | POST
  "url": "https://worldtimeapi.org/api/timezone/Etc/UTC",   // 必填：http/https
  "allow_hosts": ["worldtimeapi.org"],       // 必填：域名白名单
  "headers": {"X-Api-Key": "…"},             // 可选
  "query": {"city": "{city}"},               // 可选：{名} 会被模型传的参数替换
  "body": {"q": "{city}"},                   // 可选（POST）：同样支持 {名} 模板
  "params": {"type": "object", "properties": {"city": {"description": "城市名"}}, "required": ["city"]},
  "timeout_ms": 8000,                        // 可选
  "response_path": "data.answer",            // 可选：只把 JSON 里这一段给模型
  "max_chars": 2000                          // 可选：结果截断长度
}
```

`example.json` 就是一份可直接抄的模板（默认 `enabled: false`，不会真的被调用）。

## 命名与去重的三条规矩（防止工具集失序）

1. **一个能力一个名字**：同类工具不许起两个名（`search_x` / `find_x` / `x_search` 这种会让人和模型都挑错）；
2. **名字不许与内置工具重名**，也不许跨清单重名——加载时直接拒绝并说明；
3. **参数名不许与工具名相同**（那通常是把参数误当成了工具名）。
