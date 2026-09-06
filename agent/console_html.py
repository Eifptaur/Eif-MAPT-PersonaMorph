# -*- coding: utf-8 -*-
"""Web 控制台界面（独立模板，便于大改样式而不动逻辑）。

设计（参考 DeepSeek 品牌蓝 + 现代蓝白后台方案）：
  · 主色 DeepSeek 蓝 #4D6BFE，浅灰蓝底 #F4F6FC，白卡片圆角 12px + 轻阴影
  · 顶部：Logo（鲸鱼娘头像）+ 名称；右侧状态胶囊（运行/模型/余额/今日用）+ 启停重启
  · 左侧导航（分区设置）+ 右侧内容卡片，每区「保存设置」
  · 首次运行引导：API Key 为空时全屏引导（粘贴密钥→保存→测试→完成）
  · 所有配置项均带 data-cfg="点.path"，前后端通用映射，改配置不用碰文件
"""
HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>wx-agent 控制台</title>
<link rel="icon" href="/assets/icon.png" type="image/png">
<style>
:root{
  --blue:#4D6BFE; --blue2:#3D5BF0; --blue-soft:#EEF2FF; --blue-line:#DCE4FF;
  --bg:#F4F6FC; --card:#FFFFFF; --bd:#E6EAF5; --tx:#1F2937; --tx2:#6B7280;
  --ok:#10B981; --warn:#F59E0B; --err:#EF4444; --shadow:0 1px 3px rgba(31,41,55,.06),0 8px 24px rgba(77,107,254,.06);
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--tx);font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;min-height:100vh}
a{color:var(--blue)}
.icon{width:18px;height:18px;vertical-align:-3px;margin-right:6px}

/* ── 顶栏 ── */
.topbar{position:sticky;top:0;z-index:50;display:flex;align-items:center;gap:12px;padding:10px 20px;
  background:rgba(255,255,255,.92);backdrop-filter:blur(8px);border-bottom:1px solid var(--bd)}
.topbar .logo{display:flex;align-items:center;gap:10px;font-size:17px;font-weight:700}
.topbar .logo canvas{width:38px;height:30px;display:block;cursor:pointer}
.topbar .sp{flex:1}
.chip{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:16px;background:var(--bg);
  border:1px solid var(--bd);color:var(--tx2);font-size:12px;white-space:nowrap}
.chip b{color:var(--tx)}
.chip .dot{width:8px;height:8px;border-radius:50%;background:var(--err)}
.chip .dot.on{background:var(--ok)}
.chip .dot.p{background:var(--warn)}

/* ── 布局 ── */
.shell{display:grid;grid-template-columns:216px 1fr;gap:16px;max-width:1280px;margin:16px auto;padding:0 16px}
@media(max-width:900px){.shell{grid-template-columns:1fr}}
.side{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:10px;height:fit-content;
  position:sticky;top:70px;box-shadow:var(--shadow)}
.side .status{background:var(--blue-soft);border:1px solid var(--blue-line);border-radius:10px;padding:10px 12px;margin-bottom:8px}
.side .status b{font-size:13px;color:var(--blue)}
.side .status p{font-size:12px;color:var(--tx2)}
.nav a{display:flex;align-items:center;gap:8px;padding:9px 12px;border-radius:9px;color:var(--tx2);
  text-decoration:none;font-size:13.5px;margin:2px 0}
.nav a:hover{background:var(--bg)}
.nav a.on{background:var(--blue-soft);color:var(--blue);font-weight:600;position:relative}
.nav a.on::before{content:"";position:absolute;left:0;top:9px;bottom:9px;width:3px;border-radius:2px;background:var(--blue)}
.card{transition:box-shadow .2s ease,transform .2s ease}
.card:hover{box-shadow:0 2px 6px rgba(31,41,55,.07),0 16px 40px rgba(77,107,254,.10)}
button:active{transform:scale(.97)}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}
.chips .c{display:inline-flex;align-items:center;gap:6px;background:var(--blue-soft);border:1px solid var(--blue-line);
  color:var(--blue);border-radius:14px;padding:3px 10px;font-size:12.5px}
.chips .c b{cursor:pointer;font-weight:700;color:var(--blue)}
.chips .c b:hover{color:var(--err)}
.pick{margin-top:4px}
.pick .opt{display:flex;align-items:center;gap:8px;padding:7px 10px;border:1px solid var(--bd);border-radius:8px;margin-bottom:6px;cursor:pointer}
.pick .opt:hover{border-color:var(--blue)}
.pick .opt input{accent-color:var(--blue)}
.dlist{background:#fff;border:1px solid var(--bd);border-radius:8px;padding:4px;font-size:13px}
.main{min-width:0}

.card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:18px 20px;margin-bottom:16px;box-shadow:var(--shadow)}
.card h2{font-size:15px;margin-bottom:4px;color:var(--blue);display:flex;align-items:center;gap:6px}
.card .desc{font-size:12.5px;color:var(--tx2);margin-bottom:12px}
.row{display:flex;gap:12px;margin-bottom:12px;align-items:center;flex-wrap:wrap}
.row label{width:150px;color:var(--tx2);flex-shrink:0;font-size:13px}
.row .grow{flex:1;min-width:220px}
.row input[type=text],.row input[type=password],.row input[type=number],.row select,.row textarea{
  width:100%;background:#FBFCFE;border:1px solid var(--bd);color:var(--tx);
  border-radius:8px;padding:8px 10px;font:inherit;outline:none;transition:border .15s}
.row input:focus,.row select:focus,.row textarea:focus{border-color:var(--blue)}
.row textarea{min-height:84px;font-family:ui-monospace,Consolas,monospace;font-size:12.5px}
.row input[type=range]{flex:1}
.row .val{width:44px;text-align:right;color:var(--blue);font-weight:600}
.row input[type=checkbox]{width:16px;height:16px;accent-color:var(--blue)}
.mid{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:12px}
.mid > *{flex:1;min-width:240px}
.btns{display:flex;gap:10px;margin-top:8px;flex-wrap:wrap}
button{border:0;border-radius:8px;padding:8px 18px;cursor:pointer;font:inherit;font-weight:600;transition:.15s}
button.pri{background:var(--blue);color:#fff;box-shadow:0 4px 12px rgba(77,107,254,.3)}
button.pri:hover{background:var(--blue2)}
button.ghost{background:var(--card);border:1px solid var(--bd);color:var(--tx)}
button.ghost:hover{border-color:var(--blue);color:var(--blue)}
button.danger{background:#FEE2E2;color:#B91C1C}
button.danger:hover{background:#FECACA}
button:disabled{opacity:.5;cursor:not-allowed}
.hint{color:var(--tx2);font-size:12px;margin-top:6px}
.hint a{color:var(--blue)}
pre.out{background:#0F172A;color:#D8E0F0;border-radius:10px;padding:12px 14px;font:12px/1.55 ui-monospace,Consolas,monospace;
  overflow:auto;margin-top:8px;white-space:pre-wrap;word-break:break-all}

/* ── 概览 ── */
.stat{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:14px}
.stat .s{background:var(--bg);border:1px solid var(--bd);border-radius:10px;padding:12px 14px}
.stat .s b{font-size:20px;display:block;color:var(--blue)}
.stat .s span{color:var(--tx2);font-size:12px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--bd)}
th{color:var(--tx2);font-weight:500}
.pill{display:inline-block;padding:1px 10px;border-radius:10px;font-size:11px;background:var(--bg)}
.pill.ok{background:#D1FAE5;color:#047857}
.pill.off{background:#FEE2E2;color:#B91C1C}

/* ── 弹层 ── */
#toast{position:fixed;right:20px;bottom:20px;background:#0F172A;color:#fff;padding:11px 18px;border-radius:10px;
  display:none;z-index:9999;font-size:13px;box-shadow:0 8px 24px rgba(0,0,0,.25)}
.mask{position:fixed;inset:0;background:rgba(244,246,252,.96);z-index:9998;display:flex;align-items:center;justify-content:center;padding:20px}
.mask .box{max-width:560px;width:100%;background:var(--card);border:1px solid var(--blue-line);border-radius:16px;
  padding:28px 30px;box-shadow:0 20px 60px rgba(77,107,254,.18);text-align:center}
.mask .box img{width:72px;height:72px;border-radius:18px;margin-bottom:12px;box-shadow:0 6px 20px rgba(77,107,254,.3)}
.mask .box h1{font-size:19px;margin-bottom:8px}
.mask .box p{color:var(--tx2);font-size:13px;margin-bottom:14px}
.mask .box input{width:100%;padding:10px 12px;border:1px solid var(--bd);border-radius:8px;font:inherit;margin-bottom:10px}
.dn{display:none}
</style>
</head>
<body>

<div class="topbar">
  <div class="logo"><canvas id="logoFx" width="152" height="60" title="小鲸鱼"></canvas><span>wx-agent 控制台</span></div>
  <div class="sp"></div>
  <span class="chip"><span class="dot" id="dot"></span><b id="runText">连接中…</b></span>
  <span class="chip">模型 <b id="model-badge">? </b></span>
  <span class="chip" id="balance-badge" title="点击刷新余额">余额：查询中…</span>
  <button id="pauseBtn" class="ghost">暂停</button>
  <button id="stopBtn" class="danger">停止</button>
  <button id="restartBtn" class="pri">重启</button>
</div>

<div class="shell">
  <aside class="side">
    <div class="status"><b>运行状态</b><p id="sideStatus">未连接</p></div>
    <nav class="nav" id="nav">
      <a href="#sec-overview" class="on">概览</a>
      <a href="#sec-model">模型 API</a>
      <a href="#sec-wechat">微信</a>
      <a href="#sec-persona">人设与响应</a>
      <a href="#sec-send">发送限制</a>
      <a href="#sec-memory">记忆</a>
      <a href="#sec-search">联网搜索</a>
      <a href="#sec-server">服务器</a>
      <a href="#sec-ui">界面适配</a>
      <a href="#sec-diag">体检与诊断</a>
      <a href="#sec-log">运行日志</a>
      <a href="#sec-json">原始 JSON</a>
    </nav>
  </aside>

  <main class="main">

    <section id="sec-overview" class="card" data-sec>
      <h2>概览</h2>
      <div class="desc">机器人运作状态与账户信息（数据每 8 秒自动刷新）。</div>
      <div class="stat">
        <div class="s"><b id="st-sessions">0</b><span>会话数</span></div>
        <div class="s"><b id="st-tokens">0</b><span>总 token</span></div>
        <div class="s"><b id="st-sent">0</b><span>已发消息</span></div>
        <div class="s"><b id="st-cost">¥0</b><span>估算成本</span></div>
        <div class="s"><b id="st-groups">0</b><span>目标群</span></div>
      </div>
      <table id="group-table"><thead><tr><th>群名</th><th>目标</th></tr></thead><tbody></tbody></table>
      <div class="btns">
        <button id="testApi" class="pri">测试 API 连通</button>
        <span class="hint" id="testResult" style="align-self:center"></span>
      </div>
    </section>

    <section id="sec-model" class="card" data-sec>
      <h2>模型 API</h2>
      <div class="desc">密钥在控制台首次引导填入后自动保存，无需再改 config.json。</div>
      <div class="row"><label>Base URL</label><div class="grow"><input type="text" data-cfg="api.base_url"></div></div>
      <div class="row"><label>API Key</label><div class="grow"><input type="password" data-cfg="api.api_key" title="保存后即生效，无需改文件"></div></div>
      <div class="row"><label>模型厂商</label>
        <div class="grow"><select id="providerSel">
          <option value="deepseek">DeepSeek（默认，见下方模型列表）</option>
          <option value="moonshot">Moonshot Kimi</option>
          <option value="zhipu">智谱 GLM</option>
          <option value="qwen">通义千问（阿里）</option>
          <option value="minimax">MiniMax</option>
          <option value="doubao">豆包（火山方舟）</option>
          <option value="custom">自定义（手动填 URL/Key/模型）</option>
        </select>
        <div class="hint">切换厂商会自动替换 Base URL，并弹窗让您填入该厂商的 API Key；模型列表现场切换。</div>
      </div></div>
      <div class="row"><label>模型</label>
        <div class="grow">
          <select id="modelSel" style="margin-bottom:6px"></select>
          <input type="text" id="modelCustom" class="dn" placeholder="自定义模型名（如 glm-4-plus）">
          <div class="hint">所选厂商的常用模型都在下拉里；不够用就选「自定义」手填，或改原始 JSON。</div>
        </div></div>
      <div class="row"><label>视觉(看图)</label><input type="checkbox" data-cfg="api.vision"><span class="hint">模型支持图片则勾选</span></div>
      <div class="row"><label>温度</label><input type="range" id="api.temperature" min="0" max="1" step="0.05" data-cfg="api.temperature"><span class="val" id="api.temperature-v">0.8</span></div>
      <div class="row"><label>单次工具轮数</label><div class="grow"><input type="number" data-cfg="api.max_rounds" min="1" max="50"></div></div>
      <div class="row"><label>请求超时(ms)</label><div class="grow"><input type="number" data-cfg="api.timeout_ms" min="5000" step="1000"></div></div>
      <div class="mid">
        <div class="row"><label>输入单价/百万</label><input type="number" step="0.01" data-cfg="api.price_input_per_m"><span class="val">元</span></div>
        <div class="row"><label>输出单价/百万</label><input type="number" step="0.01" data-cfg="api.price_output_per_m"><span class="val">元</span></div>
        <div class="row"><label>缓存单价/百万</label><input type="number" step="0.01" data-cfg="api.price_cached_per_m"><span class="val">元</span></div>
      </div>
      <div class="row"><label>内置官方价</label><input type="checkbox" data-cfg="api.use_official_price"><span class="hint">上面填 0 时用内置官方单价表</span></div>
      <div class="btns"><button class="pri" data-save>保存设置（模型 API）</button></div>
    </section>

    <section id="sec-wechat" class="card" data-sec>
      <h2>微信</h2>
      <div class="desc">机器人微信身份与轮询 / 白名单。改完保存后需要重启才能完全生效。</div>
      <div class="row"><label>机器人昵称</label><div class="grow"><input type="text" data-cfg="wechat.bot_nickname"></div></div>
      <div class="row"><label>自我称呼</label><div class="grow"><input type="text" data-cfg="persona.self_nickname" placeholder="留空=机器人昵称，用于识别「我」"></div></div>
      <div class="row"><label>轮询间隔(秒)</label><div class="grow"><input type="number" step="0.5" min="0.5" data-cfg="wechat.poll_interval"></div></div>
      <div class="row"><label>每分钟限发</label><div class="grow"><input type="number" min="1" data-cfg="wechat.rate_limit_per_minute"></div></div>
      <div class="row"><label>群白名单</label>
        <div class="grow">
          <div class="chips" id="wlChips"></div>
          <div class="btns" style="margin-top:0">
            <button id="pickGroups" class="ghost">检测群聊并勾选</button>
            <input id="customGroup" type="text" placeholder="自定义群名，回车添加" style="flex:1;background:#FBFCFE;border:1px solid var(--bd);border-radius:8px;padding:7px 10px">
          </div>
          <div class="hint">留空=所有群都监听；勾选的群才响应（也可配合「暂停」）。</div>
        </div>
      </div>
      <div class="row"><label>媒体目录</label><div class="grow"><input type="text" data-cfg="wechat.media_dir"></div></div>
      <div class="row"><label>数据库目录</label><div class="grow"><input type="text" data-cfg="wechat.db_dir" placeholder="留空=自动探测微信数据目录"></div></div>
      <div class="btns"><button class="pri" data-save>保存设置（微信）</button></div>
    </section>

    <section id="sec-persona" class="card" data-sec>
      <h2>人设与响应</h2>
      <div class="row"><label>人设名</label><div class="grow"><input type="text" data-cfg="persona.bot_name"></div></div>
      <div class="row"><label>参与度</label><div class="grow"><select data-cfg="persona.participation">
        <option value="low">安静型</option><option value="medium">普通群友</option><option value="high">活跃型</option></select></div></div>
      <div class="row"><label>自定义角色文本</label><div class="grow"><textarea data-cfg="persona.role_text" placeholder="留空=内置小鲸鱼角色卡；填了=完全替换。可参考 agent/persona.py"></textarea></div></div>
      <div class="row"><label>额外规则</label><div class="grow"><textarea data-cfg="persona.custom_rules" placeholder="如：回复永远不超过 5 个字"></textarea></div></div>
      <hr style="border:none;border-top:1px solid var(--bd);margin:12px 0">
      <div class="row"><label>响应档位</label><div class="grow"><select data-cfg="store.context_tier">
        <option value="1">1 档：仅艾特</option><option value="2">2 档：+关键词</option>
        <option value="3">3 档：+随机</option><option value="4">4 档：全响应</option></select></div></div>
      <div class="row"><label>关键词(逗号)</label><div class="grow"><input type="text" data-cfg="store.keywords" placeholder="2档起命中即响应"></div></div>
      <div class="row"><label>随机概率%</label><div class="grow"><input type="number" min="0" max="100" data-cfg="store.random_percent"></div></div>
      <div class="mid">
        <div class="row"><label>艾特上下文条数</label><input type="number" min="1" data-cfg="store.at_count"></div>
        <div class="row"><label>关键词上下文</label><input type="number" min="1" data-cfg="store.keyword_count"></div>
        <div class="row"><label>随机上下文</label><input type="number" min="1" data-cfg="store.random_count"></div>
      </div>
      <div class="row"><label>单档上下文上限</label><div class="grow"><input type="number" min="1" data-cfg="store.all_count"></div></div>
      <div class="row"><label>每群消息上限</label><div class="grow"><input type="number" min="0" data-cfg="store.max_messages_per_chat" title="0=不限制"></div></div>
      <div class="btns"><button class="pri" data-save>保存设置（人设与响应）</button></div>
    </section>

    <section id="sec-send" class="card" data-sec>
      <h2>发送限制</h2>
      <div class="desc">真人化间隔与限频，防止刷屏/封号风险。</div>
      <div class="mid">
        <div class="row"><label>最小间隔(ms)</label><input type="number" min="200" step="100" data-cfg="send.min_gap_ms"></div>
        <div class="row"><label>最大间隔(ms)</label><input type="number" min="200" step="100" data-cfg="send.max_gap_ms"></div>
        <div class="row"><label>每字附加(ms)</label><input type="number" min="0" step="5" data-cfg="send.by_length_ms"></div>
      </div>
      <div class="mid">
        <div class="row"><label>每分钟上限</label><input type="number" min="1" data-cfg="send.max_per_minute"></div>
        <div class="row"><label>每小时上限</label><input type="number" min="1" data-cfg="send.max_per_hour"></div>
        <div class="row"><label>超长切分(字)</label><input type="number" min="0" step="100" data-cfg="send.hard_split_at"></div>
      </div>
      <div class="btns"><button class="pri" data-save>保存设置（发送限制）</button></div>
    </section>

    <section id="sec-memory" class="card" data-sec>
      <h2>记忆</h2>
      <div class="row"><label>自动整理</label><input type="checkbox" data-cfg="memory.consolidate_enabled"></div>
      <div class="row"><label>整理间隔(小时)</label><div class="grow"><input type="number" min="1" data-cfg="memory.consolidate_min_interval_ms"></div></div>
      <div class="mid">
        <div class="row"><label>最少印象数</label><input type="number" min="1" data-cfg="memory.consolidate_min_impressions"></div>
        <div class="row"><label>每成员印象上限</label><input type="number" min="1" data-cfg="memory.max_impressions_per_member"></div>
        <div class="row"><label>发现最少消息</label><input type="number" min="1" data-cfg="memory.discover_min_messages"></div>
      </div>
      <div class="btns"><button class="pri" data-save>保存设置（记忆）</button></div>
    </section>

    <section id="sec-search" class="card" data-sec>
      <h2>联网搜索</h2>
      <div class="row"><label>启用</label><input type="checkbox" data-cfg="web_search.enabled"></div>
      <div class="row"><label>引擎</label><div class="grow"><select data-cfg="web_search.provider">
        <option value="bing">Bing（免key）</option><option value="deepseek">DeepSeek</option>
        <option value="zhipu">智谱</option><option value="bocha">博查</option>
        <option value="baidu">百度千帆</option><option value="metaso">秘塔</option><option value="custom">自定义</option></select></div></div>
      <div class="row"><label>结果数</label><div class="grow"><input type="number" min="1" max="20" data-cfg="web_search.max_results"></div></div>
      <div class="hint">自定义引擎的 Key/地址：切到「自定义」后，在右下方“原始 JSON”里改 web_search.* 节点，或直接改文件 web_search.provider 对应小节的 api_key/base_url。</div>
      <div class="btns"><button class="pri" data-save>保存设置（联网搜索）</button></div>
    </section>

    <section id="sec-server" class="card" data-sec>
      <h2>服务器</h2>
      <div class="row"><label>监听地址</label><div class="grow"><input type="text" data-cfg="server.host" title="默认只允许本机访问"></div></div>
      <div class="row"><label>端口</label><div class="grow"><input type="number" min="1" max="65535" data-cfg="server.port"></div></div>
      <div class="row"><label>自动开浏览器</label><input type="checkbox" data-cfg="server.auto_open_browser"></div>
      <div class="row"><label>访问口令</label><div class="grow"><input type="text" data-cfg="server.token" placeholder="留空=启动时自动生成"></div></div>
      <div class="btns"><button class="pri" data-save>保存设置（服务器）</button></div>
    </section>

    <section id="sec-ui" class="card" data-sec>
      <h2>界面适配（DPI / 遮挡）</h2>
      <div class="row"><label>显示缩放</label><div class="grow"><select data-cfg="ui.coord_scale">
        <option value="auto">按系统自动检测</option><option value="1.0">100%</option>
        <option value="1.25">125%</option><option value="1.5">150%</option>
        <option value="1.75">175%</option><option value="2.0">200%</option></select></div></div>
      <div class="row"><label>点击前清遮挡</label><input type="checkbox" data-cfg="ui.clean_overlays"></div>
      <div class="btns"><button class="pri" data-save>保存设置（界面适配）</button></div>
    </section>

    <section id="sec-diag" class="card" data-sec>
      <h2>体检与诊断</h2>
      <div class="desc">一键体检检查配置/微信/消息库/目标群/缩放叠加层/点击实测；拍一拍诊断完整跑一遍定位→右键→点菜单→验证（目标不在可见区会自动翻页）。点击期间请勿动鼠标。</div>
      <div class="btns">
        <button id="selfCheck" class="pri">一键体检</button>
        <button id="pokeTest" class="ghost">拍一拍诊断</button>
        <span class="hint" id="uiTestResult" style="align-self:center"></span>
      </div>
      <pre class="out dn" id="selfCheckResult"></pre>
      <div class="hint" id="uiTestDetail"></div>
    </section>

    <section id="sec-log" class="card" data-sec>
      <h2>运行日志</h2>
      <div class="btns" style="margin-bottom:10px">
        <button id="refreshLog" class="ghost">刷新</button>
        <label class="hint" style="align-self:center"><input type="checkbox" id="autolog" checked> 自动刷新</label>
      </div>
      <pre class="out" id="log" style="height:380px">加载中…</pre>
    </section>

    <section id="sec-json" class="card" data-sec>
      <h2>完整配置 JSON（高级）</h2>
      <textarea id="rawjson" spellcheck="false" style="width:100%;min-height:260px;font-family:ui-monospace,Consolas,monospace;font-size:12.5px;background:#FBFCFE;border:1px solid var(--bd);border-radius:8px;padding:10px"></textarea>
      <div class="btns">
        <button id="saveAll" class="pri">保存全部设置</button>
        <button id="rawJsonBtn" class="ghost">新窗口查看原始 JSON</button>
      </div>
      <div class="hint">保存后需重启才能完全生效的部分：模型/人设/白名单等；暂停恢复、测试 API 即时生效。可改可不改：一般用上面各分区即可。</div>
    </section>

  </main>
</div>

<div id="toast"></div>

<!-- 小鲸鱼余额挂件（DeepSeek-Balance-Whale-Widget 迁移版，原版客户端脚本原样引入） -->
<script defer src="/dsh-whale/widget.js?token=__TKN__"></script>

<script>
const $ = id => document.getElementById(id);
let cfg = null;
function toast(msg){const t=$('toast');t.textContent=msg;t.style.display='block';clearTimeout(t._h);t._h=setTimeout(()=>t.style.display='none',2800)}
function esc(s){return String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}

const URL_TOKEN = new URLSearchParams(location.search).get('token') || '';

async function getJSON(url, opts){
  opts = opts || {};
  opts.headers = opts.headers || {};
  if(URL_TOKEN) opts.headers['Authorization'] = 'Bearer ' + URL_TOKEN;
  const r = await fetch(url, opts);
  if(!r.ok) throw new Error((await r.text())||r.status);
  return r.json();
}

function getPath(obj, path){ let o=obj; for(const k of String(path).split('.')){ if(o==null) return undefined; o=o[k]; } return o; }
function setPath(obj, path, v){ const ks=String(path).split('.'); let o=obj; for(let i=0;i<ks.length-1;i++){ if(o[ks[i]]==null) o[ks[i]]={}; o=o[ks[i]]; } o[ks[ks.length-1]]=v; }

function syncToForm(){
  if(!cfg) return;
  document.querySelectorAll('[data-cfg]').forEach(el=>{
    const path = el.dataset.cfg;
    const isCheck = el.type==='checkbox';
    if(path === 'wechat.group_name_white_list'){
      wlList = Array.isArray(getPath(cfg,path)) ? getPath(cfg,path).slice() : [];
      renderChips();
      return;
    }
    let v = getPath(cfg, path);
    if(isCheck){ el.checked = !!v; return; }
    if(v==null) v = '';
    if(Array.isArray(v)) v = v.join(',');
    el.value = v;
  });
  $('api.temperature-v').textContent = getPath(cfg,'api.temperature') ?? '0.8';
  $('rawjson').value = JSON.stringify(cfg, null, 2);
  $('model-badge').textContent = getPath(cfg,'api.model') || '未设置';
  /* 模型厂商/模型（下拉选择，换厂商自动带出 Base URL 与模型列表） */
  {
    const base = getPath(cfg,'api.base_url') || '';
    const prov = detectProvider(base);
    $('providerSel').value = prov;
    renderModelSel(prov);
    const model = getPath(cfg,'api.model') || '';
    const p = PROVIDERS[prov];
    if(p.models.includes(model)){
      $('modelSel').value = model;
      $('modelCustom').classList.add('dn');
    } else {
      $('modelSel').value = '';
      $('modelCustom').classList.remove('dn');
      $('modelCustom').value = model;
    }
  }
}

function syncFromForm(){
  document.querySelectorAll('[data-cfg]').forEach(el=>{
    const path = el.dataset.cfg;
    if(path === 'wechat.group_name_white_list'){ setPath(cfg, path, wlList.slice()); return; }
    let v;
    if(el.type==='checkbox') v = el.checked;
    else if(el.type==='number') v = parseFloat(el.value);
    else {
      v = el.value;
      if(path === 'store.keywords') v = v.split(',').map(s=>s.trim()).filter(Boolean);
    }
    setPath(cfg, path, v);
  });
  /* 模型厂商/模型：按当前下拉写入模型与 Base URL */
  {
    const prov = $('providerSel').value;
    const p = PROVIDERS[prov];
    const model = (p.models.length ? $('modelSel').value : '').trim() || $('modelCustom').value.trim();
    if(model) setPath(cfg, 'api.model', model);
    if(p.base) setPath(cfg, 'api.base_url', p.base);
  }
}

/* ── 左上角小鲸鱼（Canvas 绘制 + 悬停粒子动效，参考 DSH 官网颗粒感）── */
let wlList = [];
function renderChips(){
  const box=$('wlChips'); if(!box) return;
  box.innerHTML='';
  if(!wlList.length){ box.innerHTML='<span class="hint">（未勾选=监听所有群）</span>'; return; }
  wlList.forEach(g=>{
    const s=document.createElement('span'); s.className='c'; s.textContent=g;
    const x=document.createElement('b'); x.textContent='×'; x.title='移除';
    x.onclick=()=>{ wlList=wlList.filter(v=>v!==g); renderChips(); };
    s.appendChild(x); box.appendChild(s);
  });
}
$('customGroup').addEventListener('keydown',e=>{
  if(e.key==='Enter'){
    const v=$('customGroup').value.trim();
    if(v && !wlList.includes(v)){ wlList.push(v); renderChips(); }
    $('customGroup').value=''; e.preventDefault();
  }
});
$('pickGroups').onclick = async ()=>{
  try{
    const r = await getJSON('/api/wechat-groups');
    const groups = r.groups||[];
    const m=document.createElement('div'); m.className='mask';
    m.innerHTML='<div class="box" style="text-align:left"><h1>选择监听的群</h1><p>检测到 '+groups.length+' 个群聊，勾选机器人需要监听的群（全不勾=监听所有群）。</p><div class="pick" id="groupPick" style="max-height:340px;overflow:auto"></div><div class="btns" style="justify-content:flex-end;margin-top:10px"><button class="pri" id="gpOk">确定</button><button class="ghost" id="gpCancel">取消</button></div></div>';
    document.body.appendChild(m);
    const box=$('groupPick');
    const pick = new Set(wlList);
    if(!groups.length){ box.innerHTML='<div class="hint">没有检测到群聊——请确认微信已登录，重启机器人后再试。</div>'; }
    groups.forEach(g=>{
      const lab=document.createElement('label'); lab.className='opt';
      const inp=document.createElement('input'); inp.type='checkbox'; inp.checked = pick.has(g.name);
      lab.appendChild(inp);
      lab.appendChild(document.createTextNode(' '));
      const b=document.createElement('b'); b.textContent=g.name; lab.appendChild(b);
      const h=document.createElement('span'); h.className='hint'; h.style.marginLeft='8px'; h.textContent=g.wxid; lab.appendChild(h);
      inp.onchange=()=>{ if(inp.checked) pick.add(g.name); else pick.delete(g.name); };
      box.appendChild(lab);
    });
    $('gpOk').onclick=()=>{ wlList=[...pick]; renderChips(); m.remove(); };
    $('gpCancel').onclick=()=>m.remove();
  }catch(e){ toast('检测失败：'+e.message); }
};

async function load(){
  try{ cfg = await getJSON('/api/config'); syncToForm(); }catch(e){ toast('加载配置失败：'+e.message) }
  loadStatus(); loadLog(); loadBalance();
}

async function loadBalance(){
  const el = $('balance-badge');
  try{
    const b = await getJSON('/api/balance');
    if(b.ok===false){ el.textContent='余额：'+b.error; return; }
    const cur = b.currency==='USD'?'$':'¥';
    el.textContent = '余额 '+cur+b.total_balance+'（充值 '+b.topped_up_balance+'）';
  }catch(e){ el.textContent='余额：查询失败'; }
}

async function loadStatus(){
  try{
    const s = await getJSON('/api/status');
    $('dot').className = 'dot ' + (s.wechat_connected ? 'on':'');
    $('runText').textContent = s.paused ? '已暂停' : '运行中';
    $('sideStatus').textContent = (s.wechat_connected?'微信已连接':'微信未连接') + ' · 启动于 '+s.started_at;
    $('st-sessions').textContent = s.stats.sessions;
    $('st-tokens').textContent = s.stats.tokens;
    $('st-sent').textContent = s.stats.sent;
    $('st-cost').textContent = '¥' + (s.stats.cost||0).toFixed(4);
    $('st-groups').textContent = s.groups.filter(g=>g.target).length;
    $('pauseBtn').textContent = s.paused ? '恢复' : '暂停';
    const tb = $('group-table').querySelector('tbody'); tb.innerHTML='';
    for(const g of s.groups){
      const tr=document.createElement('tr');
      tr.innerHTML='<td>'+esc(g.name)+'</td><td><span class="pill '+(g.target?'ok':'off')+'">'+(g.target?'监听':'忽略')+'</span></td>';
      tb.appendChild(tr);
    }
  }catch(e){}
}

async function loadLog(){
  try{ const l = await getJSON('/api/logs'); $('log').textContent = l.lines.join('\n'); $('log').scrollTop = $('log').scrollHeight; }catch(e){}
}

async function saveAllBtn(btn){
  try{
    let raw = null;
    try{ raw = JSON.parse($('rawjson').value); }catch(e){}
    if(raw){ cfg = raw; } else { syncFromForm(); }
    await getJSON('/api/config', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(cfg)});
    toast('已保存 ' + new Date().toLocaleTimeString());
    syncToForm();
  }catch(e){ toast('保存失败：'+e.message); }
}

/* ── 模型厂商预设：切换即换 BaseURL/模型，弹窗要 Key ── */
const PROVIDERS = {
  deepseek:{label:'DeepSeek', base:'https://api.deepseek.com/v1', keyHint:'sk-',
    models:['deepseek-v4-flash-vision-exp','deepseek-v4-flash','deepseek-v4-pro','deepseek-chat','deepseek-reasoner']},
  moonshot:{label:'Moonshot Kimi', base:'https://api.moonshot.cn/v1', keyHint:'sk-',
    models:['kimi-k2-0711-preview','kimi-k2-0905-preview','moonshot-v1-128k','moonshot-v1-32k','moonshot-v1-8k']},
  zhipu:{label:'智谱 GLM', base:'https://open.bigmodel.cn/api/paas/v4', keyHint:'',
    models:['glm-4.5','glm-4.5-air','glm-4-plus','glm-4-flash','glm-4v-plus']},
  qwen:{label:'通义千问（阿里）', base:'https://dashscope.aliyuncs.com/compatible-mode/v1', keyHint:'sk-',
    models:['qwen-max','qwen-plus','qwen-turbo','qwen-vl-max','qwen2.5-72b-instruct']},
  minimax:{label:'MiniMax', base:'https://api.minimax.chat/v1', keyHint:'',
    models:['MiniMax-M1-80k','abab6.5s-chat']},
  doubao:{label:'豆包（火山方舟）', base:'https://ark.cn-beijing.volces.com/api/v3', keyHint:'',
    models:['doubao-seed-1.6-250615','doubao-1.5-pro-32k','doubao-vision-pro-32k']},
  custom:{label:'自定义', base:'', keyHint:'', models:[]}
};
function renderModelSel(provider){
  const sel=$('modelSel'); sel.innerHTML='';
  const p = PROVIDERS[provider] || PROVIDERS.deepseek;
  p.models.forEach(m=>{ const o=document.createElement('option'); o.value=m; o.textContent=m; sel.appendChild(o); });
  if(!p.models.length){
    const o=document.createElement('option'); o.value=''; o.textContent='（无预设，请在下方手填）'; sel.appendChild(o);
  }
  $('modelCustom').classList.toggle('dn', p.models.length>0);
}
function detectProvider(base){
  const b = String(base||'').trim();
  for(const k of Object.keys(PROVIDERS)){
    if(k!=='custom' && b && b.startsWith(PROVIDERS[k].base)) return k;
  }
  return b ? 'custom' : 'deepseek';
}
function applyProvider(provider, askKey){
  const p = PROVIDERS[provider] || PROVIDERS.deepseek;
  if(p.base){
    const be = document.querySelector('[data-cfg="api.base_url"]');
    if(be) be.value = p.base;
  }
  renderModelSel(provider);
  const pk = document.querySelector('[data-cfg="api.api_key"]');
  if(askKey && provider!=='deepseek'){
    const have = (pk&&pk.value||'').trim();
    if(!have || provider!==detectProvider(document.querySelector('[data-cfg="api.base_url"]').value)){
      const m=document.createElement('div'); m.className='mask';
      m.innerHTML='<div class="box"><h1>'+p.label+' API Key</h1><p>已为你切换到 '+p.label+'（Base URL：'+p.base+'）。请粘贴该公司的 API Key（'+p.keyHint+'开头）。</p><input type="password" id="pkCmd" placeholder="'+p.keyHint+'..."><div class="btns" style="justify-content:center"><button class="pri" id="pkOk">保存 Key</button><button class="ghost" id="pkNo">稍后再说</button></div></div>';
      document.body.appendChild(m);
      $('pkOk').onclick=()=>{ const v=$('pkCmd').value.trim(); if(v&&pk) pk.value=v; m.remove(); toast('已填入 '+p.label+' Key，记得点「保存设置」'); };
      $('pkNo').onclick=()=>m.remove();
    }
  }
}
$('providerSel').addEventListener('change', ()=>applyProvider($('providerSel').value, true));
/* ── 左上角小鲸鱼 Logo：完整造型 + 悬停「溶解成粒子游动 / 离开重组」（参考官网粒子 Logo 思路）── */
(function(){
  const lc = $('logoFx'), ctx = lc.getContext('2d');
  const W = 112, H = 52;
  lc.width = W; lc.height = H;
  lc.style.width = '96px'; lc.style.height = '44px';
  // 鲸鱼形状：身体椭圆 + 尾巴多边形
  const E = {cx:34, cy:31, rx:20, ry:12.5};
  const TAIL = [[50,26],[66,12],[61,27],[70,39],[50,33]];
  function inWhale(x, y){
    const ex = (x - E.cx) / E.rx, ey = (y - E.cy) / E.ry;
    if (ex*ex + ey*ey <= 1) return true;
    let inside = false;
    for (let i=0, j=TAIL.length-1; i<TAIL.length; j=i++){
      const [xi,yi] = TAIL[i], [xj,yj] = TAIL[j];
      if (((yi > y) !== (yj > y)) && (x < (xj-xi)*(y-yi)/(yj-yi)+xi)) inside = !inside;
    }
    return inside;
  }
  // 采样鲸鱼内部的粒子家坐标
  const home = [];
  for (let y=6; y<H; y+=3) for (let x=4; x<W; x+=3){
    if (inWhale(x,y) && home.length < 150) home.push({x, y});
  }
  const dots = home.map((p,i)=>({hx:p.x, hy:p.y, x:p.x, y:p.y, ph:Math.random()*6.28, r:1.1+Math.random()*0.9}));
  function drawSolid(){
    ctx.clearRect(0,0,W,H);
    ctx.fillStyle = '#4D6BFE';
    ctx.beginPath(); ctx.ellipse(E.cx,E.cy,E.rx,E.ry,0,0,Math.PI*2); ctx.fill();
    ctx.beginPath(); ctx.moveTo(TAIL[0][0],TAIL[0][1]);
    for(let i=1;i<TAIL.length;i++) ctx.lineTo(TAIL[i][0],TAIL[i][1]);
    ctx.closePath(); ctx.fill();
    ctx.fillStyle = 'rgba(255,255,255,.75)';
    ctx.beginPath(); ctx.arc(26,28,2.6,0,Math.PI*2); ctx.fill();
    ctx.fillStyle = 'rgba(77,107,254,.16)';
    ctx.beginPath(); ctx.ellipse(36,36,9,3.4,0,0,Math.PI*2); ctx.fill();
  }
  drawSolid();
  let hov = false, running = false, t0 = null;
  function frame(ts){
    if(t0 === null) t0 = ts;
    const t = (ts - t0) / 1000;
    ctx.clearRect(0,0,W,H);
    for (const d of dots){
      let tx = d.hx, ty = d.hy;
      if (hov){ // 悬停：鲸鱼“游动”——粒子围绕原位做波浪游走
        tx = d.hx + Math.sin(t*4 + d.ph) * 3.2;
        ty = d.hy + Math.cos(t*3 + d.ph) * 1.6;
      }
      d.x += (tx - d.x) * 0.14;
      d.y += (ty - d.y) * 0.14;
      ctx.globalAlpha = hov ? 0.92 : 1;
      ctx.fillStyle = '#4D6BFE';
      ctx.beginPath(); ctx.arc(d.x, d.y, d.r, 0, Math.PI*2); ctx.fill();
    }
    ctx.globalAlpha = 1;
    if (hov || dots.some(d => Math.abs(d.x-d.hx) > 0.4 || Math.abs(d.y-d.hy) > 0.4)){
      requestAnimationFrame(frame);
    } else {
      running = false; drawSolid();
    }
  }
  lc.addEventListener('mouseenter', ()=>{ hov = true; if(!running){ running = true; t0 = null; requestAnimationFrame(frame); } });
  lc.addEventListener('mouseleave', ()=>{ hov = false; if(!running){ running = true; t0 = null; requestAnimationFrame(frame); } });
})();

/* ── 首次运行引导 ── */
async function onboarding(){
  if(!cfg) return;
  const key = getPath(cfg,'api.api_key') || '';
  if(key && !key.includes('在这里填') && key!=='******') return;
  const m = document.createElement('div');
  m.className='mask'; m.id='onboard';
  m.innerHTML='<div class="box"><img src="/assets/icon.png"><h1>欢迎使用 wx-agent</h1>'+
    '<p>还差最后一步：填入你的 DeepSeek API Key（sk- 开头）。保存后自动生效，无需再改任何文件。</p>'+
    '<input type="password" id="obKey" placeholder="sk-...">'+
    '<div class="btns" style="justify-content:center"><button class="pri" id="obSave">保存并测试</button><button class="ghost" id="obLater">稍后再说</button></div></div>';
  document.body.appendChild(m);
  $('obSave').onclick = async ()=>{
    const k = $('obKey').value.trim();
    if(!k){ toast('请先粘贴 API Key'); return; }
    setPath(cfg,'api.api_key',k);
    try{
      await getJSON('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(cfg)});
      toast('已保存，测试连通中…');
      const r = await getJSON('/api/test-api',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
      if(r.ok){ m.remove(); toast('✅ 连通成功（'+r.latency_ms+'ms，模型 '+r.model+'）'); load(); }
      else { toast('Key 已保存但测试失败：'+(r.error||'')); }
    }catch(e){ toast('保存失败：'+e.message); }
  };
  $('obLater').onclick = ()=>m.remove();
}

/* 事件绑定 */
$('api.temperature').addEventListener('input',()=>$('api.temperature-v').textContent=$('api.temperature').value);
document.querySelectorAll('[data-save]').forEach(b=> b.addEventListener('click', ()=>saveAllBtn(b)));
$('saveAll').onclick = ()=>saveAllBtn();
$('refreshLog').onclick = loadLog;
$('balance-badge').onclick = loadBalance;
$('rawJsonBtn').onclick = ()=>{ window.open('/api/config'+(URL_TOKEN?('?token='+URL_TOKEN):''),'_blank'); };
$('pauseBtn').onclick = async ()=>{
  try{ await getJSON($('pauseBtn').textContent==='暂停'?'/api/pause':'/api/resume',{method:'POST'}); loadStatus(); }catch(e){toast(e.message)}
};
$('stopBtn').onclick = async ()=>{
  if(!confirm('确定停止机器人？停止后可用「重启」按钮或双击启动机器人.bat 恢复。')) return;
  try{
    await getJSON('/api/shutdown',{method:'POST'});
    toast('已停止机器人，页面稍后自动显示停止提示');
    $('dot').className='dot';
  }catch(e){ toast('停止失败：'+e.message); }
};
$('restartBtn').onclick = async ()=>{
  if(!confirm('重启机器人？会在后台无窗口方式重新启动（约 2 秒）。')) return;
  try{
    await getJSON('/api/restart',{method:'POST'});
    toast('已发出重启指令，等待新实例接管…');
  }catch(e){ toast('重启失败：'+e.message); }
};
$('testApi').onclick = async ()=>{
  const btn=$('testApi'); btn.disabled=true; $('testResult').textContent='测试中…';
  try{
    const r = await getJSON('/api/test-api',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    if(r.ok) $('testResult').textContent = '✅ 延迟 '+r.latency_ms+'ms，模型 '+r.model+'：'+r.reply;
    else $('testResult').textContent = '❌ '+(r.error||'失败');
  }catch(e){ $('testResult').textContent='❌ '+e.message; }
  finally{ btn.disabled=false; }
};
$('selfCheck').onclick = async ()=>{
  const btn=$('selfCheck'); btn.disabled=true;
  const pre=$('selfCheckResult'); pre.classList.remove('dn');
  pre.textContent='体检中（约 10~20 秒，会移动光标+真实右键测试，请勿动鼠标）…';
  try{
    const r = await getJSON('/api/selfcheck',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    let lines=['===== 一键体检 =====', r.summary||'', ''];
    for(const c of (r.checks||[])){
      const mark = c.status==='ok'?'✅':(c.status==='warn'?'⚠️':(c.status==='fail'?'❌':'ℹ️'));
      lines.push(mark+' '+c.name+'：'+c.detail);
      if(c.hint) lines.push('    建议：'+c.hint);
    }
    pre.textContent = lines.join('\n');
  }catch(e){ pre.textContent='体检失败：'+e.message; }
  finally{ btn.disabled=false; }
};
$('pokeTest').onclick = async ()=>{
  const btn=$('pokeTest'); btn.disabled=true;
  $('uiTestResult').textContent='诊断中（约 15~30 秒，请勿动鼠标）…'; $('uiTestDetail').textContent='';
  try{
    const r = await getJSON('/api/poke-test',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    $('uiTestResult').textContent = (r.ok?'✅ ':'❌ ')+(r.message||r.error||'(无结果)');
    $('uiTestDetail').textContent = '目标：'+(r.target?(r.target.name+' / '+r.target.id+' 在群「'+(r.group||'?')+'」'):'未解析')+'\n步骤：\n'+((r.steps||[]).join('\n')||(r.error||''));
  }catch(e){ $('uiTestResult').textContent='❌ '+e.message; }
  finally{ btn.disabled=false; }
};
/* 导航高亮 */
document.querySelectorAll('#nav a').forEach(a=>{
  a.addEventListener('click',()=>{
    document.querySelectorAll('#nav a').forEach(x=>x.classList.remove('on'));
    a.classList.add('on');
  });
});

/* 断线检测：机器人停止后显示全屏提示，并尝试自动关闭 */
let offlineShown=false;
async function checkAlive(){
  if(offlineShown) return;
  try{
    const r = await fetch('/api/status',{headers:URL_TOKEN?{Authorization:'Bearer '+URL_TOKEN}:{}});
    if(!r.ok) throw new Error(r.status);
  }catch(e){
    offlineShown=true;
    const ov=document.createElement('div'); ov.className='mask';
    ov.innerHTML='<div class="box"><img src="/assets/icon.png"><h1>机器人已停止</h1>'+
      '<p>后台进程已退出。可双击「启动机器人.bat」或在有运行实例时点「重启」恢复。</p>'+
      '<div class="hint">本页面稍后尝试自动关闭…</div></div>';
    document.body.appendChild(ov);
    setTimeout(()=>{ try{window.close();}catch(_e){} }, 5000);
  }
}

load();
onboarding();
setInterval(loadStatus, 8000);
setInterval(loadBalance, 30000);
setInterval(()=>{ if($('autolog').checked) loadLog(); }, 4000);
setInterval(checkAlive, 6000);
</script>
</body>
</html>
"""
