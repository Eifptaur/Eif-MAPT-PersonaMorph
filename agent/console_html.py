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
.pick .opt{display:grid;grid-template-columns:18px minmax(0,1fr) auto;align-items:center;column-gap:8px;
  padding:8px 12px;border:1px solid var(--bd);border-radius:10px;margin-bottom:6px;cursor:pointer}
.pick .opt:hover{border-color:var(--blue);background:#F8FAFF}
.pick .opt b{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pick .opt .hint{margin:0;white-space:nowrap;font-size:11px;color:var(--tx2)}
.pick .opt input{width:16px;height:16px;accent-color:var(--blue)}
.dlist{background:#fff;border:1px solid var(--bd);border-radius:8px;padding:4px;font-size:13px}
.main{min-width:0}

.card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:18px 20px;margin-bottom:16px;box-shadow:var(--shadow)}
.card h2{font-size:15px;margin-bottom:4px;color:var(--blue);display:flex;align-items:center;gap:6px}
.card .desc{font-size:12.5px;color:var(--tx2);margin-bottom:12px}
.row{display:flex;gap:12px;margin-bottom:12px;align-items:center;flex-wrap:wrap}
.row label{width:150px;color:var(--tx2);flex-shrink:0;font-size:13px}
.row .grow{flex:1;min-width:220px}
.row input[type=text],.row input[type=password],.row input[type=number],.row select,.row textarea{
  width:100%;background:#F8FAFE;border:1px solid #DCE4FF;color:var(--tx);
  border-radius:10px;padding:8px 12px;font:inherit;outline:none;transition:border .15s,box-shadow .15s}
.row input:focus,.row select:focus,.row textarea:focus{border-color:var(--blue);box-shadow:0 0 0 3px rgba(77,107,254,.12)}
.row select{appearance:none;-webkit-appearance:none;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8'%3E%3Cpath d='M1 1l5 5 5-5' stroke='%23 4D6BFE' stroke-width='2' fill='none' stroke-linecap='round'/%3E%3C/svg%3E");
  background-repeat:no-repeat;background-position:right 12px center;padding-right:30px;border-radius:10px}
.row select option{border-radius:10px;background:#fff;color:var(--tx);padding:6px}
/* 自绘下拉（原生弹层无法样式化，全部替换为这个） */
.dsel{position:relative;width:100%}
.dsel-btn{width:100%;display:flex;align-items:center;justify-content:space-between;gap:8px;
  background:#F8FAFE;border:1px solid #DCE4FF;border-radius:10px;padding:8px 12px;color:var(--tx);
  font:inherit;font-weight:500;text-align:left;cursor:pointer}
.dsel-btn:hover{border-color:var(--blue)}
.dsel-btn .arr{color:var(--blue);font-size:11px;transform:translateY(-1px)}
.dsel-menu{position:absolute;left:0;right:0;top:calc(100% + 4px);z-index:60;background:#fff;
  border:1px solid #DCE4FF;border-radius:10px;box-shadow:0 10px 30px rgba(77,107,254,.14);
  max-height:260px;overflow:auto;padding:5px}
.dsel-menu li{list-style:none;padding:8px 12px;border-radius:8px;cursor:pointer;font-size:13.5px;color:var(--tx)}
.dsel-menu li:hover{background:var(--blue-soft);color:var(--blue)}
.dsel-menu li.on{background:var(--blue);color:#fff;font-weight:600}
body.locked{overflow:hidden}
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
      <a href="#sec-memory">记忆</a>
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
      <div class="row"><label>API Key</label>
        <div class="grow">
          <input type="password" id="apiKeyInput" data-cfg="api.api_key" placeholder="sk-...">
          <div class="btns" style="margin-top:6px"><button id="keyReset" class="ghost">重置 Key（重新填写）</button></div>
          <div class="hint">默认只显示打码值（sk-***…尾4位），真实密钥只保存在服务器 config.json。</div>
        </div></div>
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

    <section id="sec-memory" class="card" data-sec>
      <h2>记忆（群友印象）</h2>
      <div class="desc">每个群友的长期印象，机器人回复时会参考。点「保存设置」不影响此处；删除即从记忆中移除。</div>
      <div class="row"><label>选择群聊</label>
        <div class="grow">
          <select id="memChats"><option value="">（加载中…）</option></select>
          <button id="memRefresh" class="ghost" style="margin-top:6px">刷新</button>
        </div>
      </div>
      <table id="memTable"><thead><tr><th>成员</th><th>印象数</th><th>更新时间</th><th></th></tr></thead><tbody></tbody></table>
      <div class="hint" id="memEmpty">（无记忆数据）</div>
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
/* 内嵌原版 DeepSeek 蓝鲸 Logo（base64，服务挂了也能显示；渲染与粒子效果都在用） */
const LOGO_URL = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAADoAAAA2CAYAAACWeYpTAAAAAXNSR0IArs4c6QAAAARnQU1BAACxjwv8YQUAAAAJcEhZcwAADsMAAA7DAcdvqGQAAATOSURBVGhD7ZhPUxpnHMe/xKXCgCPP2EedNIdCb8GT+wbc4itoFfoGTOw0uXXS9hAzxhwar3oweuilFwjTnjsD4gso9oI59EB6KDPClu466gRk0R5g191nd4FdcGoIn5mdWX6/h2W/z+/P8zx4ms3LK3wA3GENw8pI6LDhUUY1OlyMhA4bI6HDxkjobaBWv0StfsmaXfG/rKMVSUEuf9a+b0LgA5iL+Nhh2HpdReVfBRsPZ1iXY25EaEVSUCjWDLZpwuGoWEMuf46K1NDs0bDfVsgX3/8FAHj+YNZyIpwwMKGFYg1HxRqSGZl1dWTnu3uYJhxrBnRCp4kXAh9AYjHEDtGoSArWdsvY/vYuuDEP6x5MjT59Vcba7rFjkWhPkBUVSdHdN5DMyFh9WWLsrcxp+f42ZApLXxGtSAq2UlUcvX3HunqmU7Seviqbni3wExD4ANZ2jw12lV9+/JQ1Af1EdBAioYuWVTbMfTbOmnBUrNmKFPgJ1qThWuggROpJZmRsva4abAIfNHxGe2LsEPgAa9K4c3UFOL32fz8bqEiVXP4UD1+WtN+hIQ6Plj5mh1mSiIUQDftM76periKazJ6wpoEhSg2sbpa0zwIf7CiWEi/WV2YRt6hxPZ6G4rwZfflDq+3fJJR4sbEyA9peelY3SxB1aZuIhXA/4ut5fXUs9I8/3+HFT2VA92MAIEoKCm/rOMifMt9wDyVefD4fACUcttP/aPZo2I/nD6w3GXY4FvrrwQl+/k0CJV7sPPmEdUNsr3O5/Bn2D88NUbAjEQuBEg65/HlPtb/AT+Dx0hRr7oirGkW7llRReijhQAmH+GIIGyszWOjQ8vVQwuHx8hQSsc61BgBzYfOy0w3HQqWz69NE2UKoHko4fBWb7PryZbmJ7XRVm6Bu43utSz2OhZLg9Vfe2Gzf9Kgvv/PkHijxsm6gvQnQZ4jABxEN+9lhGmqDcoJjoaGJMe2+UKwbfJ2ghMPGinUDUetYzRBKONvFv9dSYHEsNHz3I+2+IpuPY52ghMP6yixr1thOV5HKyCi0j3NWCPPWE9ANx0Ing9cRFaUGcofWL2THXMRnW4Oi1EAyK+PZ3rFl942G/a7qE26EkokxQ/0cOYioSrcatCO+OMmaesaxUDCbZ1FqYCtt3Ix3Q11KnNBPNOFW6FzEZ+igB/lTpCyOWZ2gpPcNOwDHE8PiSiglHBIxYxrtH57bNqatdBWrmyWTX+CDtvWqR9059cOdKwBurgU+aGj1otTAdrqKZEY2jVX9z/Zaf7ewz+lUr9GwH8uLIdMznV6uIqqSiE0aUliUGkhlZVMa66Ofysr4erOkbQ4o4fBoecpWbDQybsoEN3guHG7qWURJwdpe2bR5p8QLYT6A+xEf3hRrSGWN4lW/eo4UJQXJ7Int6YcSL+KxSct/HXqhb6Fov2Quf2YS0wuUeBGN+BBtb9RT2RPTpKlEw36sOzyeqQxEqEoqI7sSq0KJF98sTWGGcFq66lPcbTQBwHPRaA5MKACIsoJc/tyx4HgshDjTyQfJwIWqiLKCQrHePpkoqEhNiHIrJWmo1cCikXFME+5GBapcC/W0+7Ad77nfc6HcTERvGzeWureNvjYM7xMjocPGSOiw8cEI9dRHy8twMRI6bPwHTfhdUJub1u0AAAAASUVORK5CYII=';
const ICON = '<img src="'+LOGO_URL+'" style="width:64px;height:60px;margin-bottom:12px" alt="whale">';

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
    // 按厂商存 Key（真实值才存；打码值不动）
    const pkv = ($('apiKeyInput') || {}).value || '';
    if(pkv && !pkv.includes('••••') && !pkv.startsWith('sk-***')){
      if(!cfg.api.provider_keys) cfg.api.provider_keys = {};
      cfg.api.provider_keys[prov] = pkv;
    }
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
$('keyReset').onclick = ()=>{ const k=$('apiKeyInput'); k.value=''; k.focus(); };
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
    $('gpOk').onclick=()=>{ wlList=[...pick]; renderChips(); maskClose(m); m.remove(); };
    $('gpCancel').onclick=()=>{ maskClose(m); m.remove(); };
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

/* ── 自绘下拉组件：替换所有原生 select（弹层样式可控，DeepSeek 风）── */
function enhanceSelects(){
  document.querySelectorAll('select').forEach(sel=>{
    if(sel._enhanced) return;
    sel._enhanced = true;
    const wrap = document.createElement('div'); wrap.className='dsel';
    const btn = document.createElement('button'); btn.type='button'; btn.className='dsel-btn';
    btn.innerHTML = '<span class="txt"></span><span class="arr">▾</span>';
    const menu = document.createElement('div'); menu.className='dsel-menu dn';
    const sel2 = sel; // 原 select 隐藏但保留值
    sel2.style.display = 'none';
    function refreshText(){
      const o = sel2.options[sel2.selectedIndex];
      btn.querySelector('.txt').textContent = (o && o.textContent) || sel2.value || '—';
    }
    function buildMenu(){
      menu.innerHTML='';
      Array.from(sel2.options).forEach((o,i)=>{
        const li=document.createElement('li');
        li.dataset.i=i; li.textContent=o.textContent;
        if(i===sel2.selectedIndex) li.classList.add('on');
        li.addEventListener('click',()=>{
          sel2.selectedIndex=i;
          sel2.dispatchEvent(new Event('change'));
          buildMenu(); refreshText(); menu.classList.add('dn');
        });
        menu.appendChild(li);
      });
    }
    btn.addEventListener('click', e=>{
      e.stopPropagation();
      const open = !menu.classList.contains('dn');
      document.querySelectorAll('.dsel-menu').forEach(m=>m.classList.add('dn'));
      if(!open){ buildMenu(); refreshText(); menu.classList.remove('dn'); }
    });
    document.addEventListener('click', ()=>menu.classList.add('dn'));
    sel2.addEventListener('change', ()=>{ buildMenu(); refreshText(); });
    sel2.insertAdjacentElement('afterend', wrap);
    wrap.appendChild(btn); wrap.appendChild(menu);
    refreshText();
  });
}
/* 遮罩锁滚动：显示弹层时锁定 body，关闭恢复（修复停止页下层还能滚） */
const _maskStack = [];
function lockBody(on){ document.body.classList.toggle('locked', on); }
function maskOpen(el){
  _maskStack.push(el); lockBody(true);
}
function maskClose(el){
  const i=_maskStack.indexOf(el);
  if(i>=0) _maskStack.splice(i,1);
  if(!_maskStack.length) lockBody(false);
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
function providerSavedKey(provider){
  // 该厂商是否存过 Key（打码也算存过）
  try{
    if(cfg && cfg.api && cfg.api.provider_keys && cfg.api.provider_keys[provider]) return true;
  }catch(e){}
  return false;
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
  // 已存过该厂商 Key → 自动回填（打码值则不回填，防误存）
  const pk = document.querySelector('[data-cfg="api.api_key"]');
  try{
    const saved = cfg && cfg.api && cfg.api.provider_keys && cfg.api.provider_keys[provider];
    if(saved && pk && !String(saved).includes('••••') && !String(saved).startsWith('sk-***')) pk.value = saved;
  }catch(e){}
  if(askKey && provider !== 'deepseek'){
    // 换厂商必弹：让用户确认该公司的 API Key（预填当前值，可覆盖/跳过）
    const have = (pk && pk.value || '').trim();
    const m=document.createElement('div'); m.className='mask';
    m.innerHTML='<div class="box"><h1>'+p.label+' API Key</h1><p>已切换到 '+p.label+'（Base URL：'+p.base+'）。请填写该公司的 API Key（'+(p.keyHint||'见官网')+' 开头）。</p><input type="password" id="pkCmd" placeholder="'+(p.keyHint||'')+'..." value="'+have.replace(/"/g,'')+'"><div class="btns" style="justify-content:center"><button class="pri" id="pkOk">保存 Key</button><button class="ghost" id="pkSame">沿用现有 Key</button><button class="ghost" id="pkNo">暂不填</button></div></div>';
    document.body.appendChild(m); maskOpen(m);
    $('pkOk').onclick=()=>{ const v=$('pkCmd').value.trim(); if(v&&pk) pk.value=v; maskClose(m); m.remove(); toast('已填入 '+p.label+' Key，记得点「保存设置」'); };
    $('pkSame').onclick=()=>{ maskClose(m); m.remove(); };
    $('pkNo').onclick=()=>{ maskClose(m); m.remove(); };
  }
}
$('providerSel').addEventListener('change', ()=>applyProvider($('providerSel').value, true));
/* ── 左上角 Logo：官方 DeepSeek 蓝鲸原图 + 像素级「溶解游动/重组」粒子效果 ── */
(function(){
  const lc = $('logoFx'), ctx = lc.getContext('2d');
  const W = 152, H = 88;
  lc.width = W; lc.height = H;
  lc.style.width = '76px'; lc.style.height = '44px';
  const img = new Image();
  let dots = [], ready = false, hov = false, running = false, t0 = null;
  img.onload = function(){
    // 把 logo 画进离屏画布，按不透明像素采样粒子
    const scale = Math.min(W / img.width, H / img.height) * 0.92;
    const dw = img.width * scale, dh = img.height * scale;
    const ox = (W - dw) / 2, oy = (H - dh) / 2;
    const off = document.createElement('canvas'); off.width = W; off.height = H;
    const octx = off.getContext('2d');
    octx.drawImage(img, ox, oy, dw, dh);
    let data = null;
    try { data = octx.getImageData(0, 0, W, H).data; } catch(e){}
    if (data){
      const step = 3;
      for (let y=0; y<H; y+=step) for (let x=0; x<W; x+=step){
        const a = data[(y*W+x)*4+3];
        if (a > 128 && dots.length < 260) dots.push({hx:x, hy:y, x:x, y:y, ph:Math.random()*6.28, r:1.2+Math.random()*0.9});
      }
    }
    ready = true; drawStatic();
  };
  img.src = LOGO_URL;
  function drawStatic(){
    ctx.clearRect(0,0,W,H);
    ctx.drawImage(img, (W-img.width*1.4)/2, (H-img.height*1.4)/2, img.width*1.4, img.height*1.4);
  }
  function frame(ts){
    if(t0 === null) t0 = ts;
    const t = (ts - t0) / 1000;
    ctx.clearRect(0,0,W,H);
    for (const d of dots){
      let tx = d.hx, ty = d.hy;
      if (hov){ tx = d.hx + Math.sin(t*4 + d.ph) * 4.0; ty = d.hy + Math.cos(t*3 + d.ph) * 2.0; }
      d.x += (tx - d.x) * 0.14; d.y += (ty - d.y) * 0.14;
      ctx.globalAlpha = 0.95; ctx.fillStyle = '#3D5BF0';
      ctx.beginPath(); ctx.arc(d.x, d.y, d.r, 0, Math.PI*2); ctx.fill();
    }
    ctx.globalAlpha = 1;
    if (hov || dots.some(d => Math.abs(d.x-d.hx) > 0.4 || Math.abs(d.y-d.hy) > 0.4)){
      requestAnimationFrame(frame);
    } else { running = false; drawStatic(); }
  }
  lc.addEventListener('mouseenter', ()=>{ if(!ready) return; hov = true; if(!running){ running = true; t0 = null; requestAnimationFrame(frame); } });
  lc.addEventListener('mouseleave', ()=>{ hov = false; if(!running){ running = true; t0 = null; requestAnimationFrame(frame); } });
})();

/* ── 首次运行向导：Key → 检测微信+勾选群 → 一键体检 → 完成 ── */
async function onboarding(){
  if(!cfg) return;
  const key = getPath(cfg,'api.api_key') || '';
  if(key && !key.includes('在这里填') && key!=='******' && !key.includes('••••')) return;
  const m = document.createElement('div'); m.className='mask'; m.id='onboard';
  m.innerHTML='<div class="box">'+ICON+'<h1>欢迎使用 wx-agent · 三步上手</h1>'+
    '<p id="obDesc">第 1 步/共 3 步：填入你的 API Key（默认 DeepSeek，sk- 开头）。保存后无需再改文件。</p>'+
    '<input type="password" id="obKey" placeholder="sk-...">'+
    '<div id="obBody"></div>'+
    '<div class="btns" style="justify-content:center;margin-top:10px"><button class="pri" id="obNext">下一步</button><button class="ghost" id="obLater">跳过向导</button></div></div>';
  document.body.appendChild(m); maskOpen(m);
  let step = 1, picked = [];
  $('obNext').onclick = async ()=>{
    try{
      if(step===1){
        const k=$('obKey').value.trim();
        if(k){ setPath(cfg,'api.api_key',k); await getJSON('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(cfg)}); }
        const r = await getJSON('/api/wechat-groups');
        const groups = r.groups||[];
        $('obDesc').textContent = '第 2 步/共 3 步：勾选需要机器人监听的群（全不勾=监听所有群）。检测到 '+groups.length+' 个群聊。';
        $('obKey').style.display='none';
        const body=$('obBody'); body.innerHTML='';
        if(!groups.length){ body.innerHTML='<div class="hint">未检测到群聊——请确认微信已登录，重启机器人后再试。</div>'; }
        groups.forEach(g=>{
          const lab=document.createElement('label'); lab.className='opt';
          const inp=document.createElement('input'); inp.type='checkbox'; inp.checked = (wlList||[]).includes(g.name);
          inp.onchange=()=>{ if(inp.checked) picked.push(g.name); else picked=picked.filter(x=>x!==g.name); };
          lab.appendChild(inp);
          const b=document.createElement('b'); b.textContent=g.name; lab.appendChild(b);
          const h=document.createElement('span'); h.className='hint'; h.style.marginLeft='8px'; h.textContent=g.wxid; lab.appendChild(h);
          body.appendChild(lab);
        });
        $('obNext').textContent='下一步'; step=2; return;
      }
      if(step===2){
        if(picked.length){ wlList = picked.slice(); setPath(cfg,'wechat.group_name_white_list', wlList.slice()); await getJSON('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(cfg)}); renderChips(); }
        $('obDesc').textContent = '第 3 步/共 3 步：一键体检（约 10~20 秒，会移动光标+真实右键测试，请勿动鼠标）。';
        $('obBody').innerHTML='<pre class="out" id="obCheck" style="height:190px">体检中…</pre>';
        $('obNext').textContent='完成'; step=3;
        const r = await getJSON('/api/selfcheck',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
        let lines=[r.summary||'',''];
        for(const c of (r.checks||[])){
          lines.push((c.status==='ok'?'✅':(c.status==='warn'?'⚠️':'❌'))+' '+c.name+'：'+c.detail);
          if(c.hint) lines.push('   建议：'+c.hint);
        }
        $('obCheck').textContent = lines.join('\n');
        return;
      }
      if(step===3){ maskClose(m); m.remove(); load(); loadMemory(''); toast('🎉 部署完成！'); }
    }catch(e){ toast('出错：'+e.message); }
  };
  $('obLater').onclick = ()=>{ maskClose(m); m.remove(); };
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
  if(!confirm('确定停止机器人？停止后可用「重启」按钮或双击启动机器人.vbs 恢复。')) return;
  try{
    await getJSON('/api/shutdown',{method:'POST'});
    toast('已发出停止指令，机器人即将退出…');
    $('dot').className='dot';
  }catch(e){
    toast('停止指令未送达（机器人可能已经不在运行）——页面稍后会显示「机器人已停止」');
  }
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
/* ── 记忆页面 ── */
function memTime(ts){
  if(!ts) return '—';
  const d=new Date(ts>1e12?ts:ts*1000);
  const p=n=>String(n).padStart(2,'0');
  return d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate())+' '+p(d.getHours())+':'+p(d.getMinutes());
}
async function loadMemory(chat_key){
  try{
    const r = await getJSON('/api/memory'+(chat_key?('?chat_key='+encodeURIComponent(chat_key)):''));
    const chats = r.chats||[];
    const sel = $('memChats');
    const prev = sel.value;
    sel.innerHTML = '<option value="">— 选择群聊 —</option>';
    chats.forEach(c=>{ const o=document.createElement('option'); o.value=c.chat_key; o.textContent=c.name+'（'+c.count+' 人）'; sel.appendChild(o); });
    if(prev && chats.some(c=>c.chat_key===prev)) sel.value=prev; else sel.value = r.chat_key || '';
    memMembers = r.members||[];
    const tb=$('memTable').querySelector('tbody'); tb.innerHTML='';
    $('memEmpty').style.display = memMembers.length?'none':'block';
    for(const m of memMembers){
      const tr=document.createElement('tr');
      const name = m.name || m.userId || '某人';
      const n = (Array.isArray(m.impressions)?m.impressions.length:0);
      const del=document.createElement('button'); del.className='ghost'; del.textContent='删除';
      del.onclick=async ()=>{
        if(!confirm('删除「'+name+'」的全部印象？')) return;
        try{
          await getJSON('/api/memory',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chat_key:sel.value,user_id:m.userId})});
          toast('已删除'); loadMemory(sel.value);
        }catch(e){ toast('删除失败：'+e.message); }
      };
      tr.innerHTML='<td>'+esc(name)+'</td><td>'+n+'</td><td>'+memTime(m.updatedAt)+'</td>';
      tr.appendChild(del);
      tb.appendChild(tr);
    }
  }catch(e){ $('memEmpty').style.display='block'; $('memEmpty').textContent='加载失败：'+e.message; }
}
let memMembers = [];
$('memChats').addEventListener('change', ()=>loadMemory($('memChats').value));
$('memRefresh').onclick = ()=>loadMemory($('memChats').value);

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
    ov.innerHTML='<div class="box">'+ICON+'<h1>机器人已停止</h1>'+
      '<p>后台进程已退出。可双击「启动机器人.vbs」（完全无窗口）或在有运行实例时点「重启」恢复。</p>'+
      '<div class="hint">浏览器可能拦截自动关闭——请手动关闭本标签页（页面不会自己关掉属正常现象）。</div></div>';
    document.body.appendChild(ov); maskOpen(ov);
    setTimeout(()=>{ try{window.close();}catch(_e){} }, 5000);
  }
}

load();
onboarding();
loadMemory('');
enhanceSelects();
setInterval(loadStatus, 8000);
setInterval(loadBalance, 30000);
setInterval(()=>{ if($('autolog').checked) loadLog(); }, 4000);
setInterval(checkAlive, 6000);
</script>
</body>
</html>
"""
