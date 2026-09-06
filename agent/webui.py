# -*- coding: utf-8 -*-
"""Web 控制台：在浏览器里改设置、看状态、看日志、测试 API（移植自 qq-agent 的控制台思路）。

零第三方依赖，纯标准库 http.server，单文件 HTML（内联 CSS/JS，无框架）。
只监听本机回环地址，含可选访问口令。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .config import get_config, save_config, set_config
from .console_html import HTML  # 界面模板（蓝白设计，设置项全量，独立文件便于改版）

# 给挂件脚本（whale-widget/client/widget.js）注入访问口令：把脚本里的 /dsh-whale/*
# 绝对路径都补上 ?token=xxx，保证前端轮询/音频请求都带上口令
_WHALE_URL_RE = re.compile(r"(/dsh-whale/[^'\"\s?]+)(\?[^'\"\s]*)?")

# ── 旧版界面模板（无操作字符串，仅保留防外部引用；实际界面见 agent/console_html.py）──
r"""
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>wx-agent 控制台</title>
<style>
:root{
  --bg:#0f1115; --bg2:#161a22; --bg3:#1d232e; --bd:#2a3242;
  --tx:#e6e9ef; --tx2:#9aa4b2; --acc:#4c8dff; --ok:#34d399; --warn:#fbbf24; --err:#f87171;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--tx);font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;padding:16px}
a{color:var(--acc)}
header{display:flex;align-items:center;gap:12px;padding:4px 0 16px;flex-wrap:wrap}
header h1{font-size:20px}
.dot{width:10px;height:10px;border-radius:50%;background:var(--err)}
.dot.on{background:var(--ok)}
.badge{padding:2px 10px;border:1px solid var(--bd);border-radius:20px;color:var(--tx2);font-size:12px}
.wrap{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:960px){.wrap{grid-template-columns:1fr}}
.card{background:var(--bg2);border:1px solid var(--bd);border-radius:10px;padding:16px;margin-bottom:16px}
.card h2{font-size:15px;margin-bottom:12px;color:var(--tx)}
.row{display:flex;gap:10px;margin-bottom:10px;align-items:center;flex-wrap:wrap}
.row label{width:130px;color:var(--tx2);flex-shrink:0}
.row input[type=text],.row input[type=password],.row select,.row textarea{
  flex:1;min-width:180px;background:var(--bg3);border:1px solid var(--bd);color:var(--tx);
  border-radius:6px;padding:7px 10px;font:inherit}
.row textarea{width:100%;min-height:90px;font-family:ui-monospace,Consolas,monospace;font-size:12px}
.row input[type=range]{flex:1}
.row .val{width:34px;text-align:right;color:var(--acc)}
.row input[type=checkbox]{width:18px;height:18px}
.btns{display:flex;gap:10px;margin-top:14px;flex-wrap:wrap}
button{background:var(--acc);border:0;color:#fff;border-radius:6px;padding:8px 16px;cursor:pointer;font:inherit}
button.ghost{background:var(--bg3);border:1px solid var(--bd);color:var(--tx)}
button.danger{background:var(--err)}
button:disabled{opacity:.5;cursor:not-allowed}
.hint{color:var(--tx2);font-size:12px;margin-top:6px}
#log{background:var(--bg3);border:1px solid var(--bd);border-radius:6px;padding:10px;
  font-family:ui-monospace,Consolas,monospace;font-size:12px;height:360px;overflow:auto;white-space:pre-wrap;word-break:break-all}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--bd)}
th{color:var(--tx2);font-weight:500}
.pill{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11px;background:var(--bg3)}
.pill.ok{background:rgba(52,211,153,.15);color:var(--ok)}
.pill.off{background:rgba(248,113,113,.15);color:var(--err)}
#toast{position:fixed;right:20px;bottom:20px;background:var(--bg3);border:1px solid var(--bd);
  padding:10px 16px;border-radius:8px;display:none;z-index:99}
.stat{display:flex;gap:18px;flex-wrap:wrap;margin-bottom:8px}
.stat b{font-size:20px;display:block}
.stat span{color:var(--tx2);font-size:12px}
</style>
<link rel="icon" href="/assets/icon.png" type="image/png">
</head>
<body>
<header>
  <span class="dot" id="dot"></span>
  <h1>wx-agent 控制台</h1>
  <span class="badge" id="model-badge">模型未知</span>
  <span class="badge" id="balance-badge" title="点击刷新余额">余额：查询中…</span>
  <span class="badge" title="右下角为 DeepSeek 小鲸鱼余额挂件（可拖拽/缩放/调音效，菜单里可关）">鲸鱼挂件已启用</span>
  <span class="badge" id="addr-badge"></span>
  <span style="flex:1"></span>
  <button id="pauseBtn" class="ghost">暂停</button>
  <button id="stopBtn" class="danger">停止机器人</button>
  <button id="rawJsonBtn" class="ghost">查看原始 JSON</button>
</header>

<div class="wrap">
  <div>
    <div class="card">
      <h2>状态</h2>
      <div class="stat">
        <div><b id="st-sessions">0</b><span>会话数</span></div>
        <div><b id="st-tokens">0</b><span>总 token</span></div>
        <div><b id="st-sent">0</b><span>已发消息</span></div>
        <div><b id="st-cost">¥0</b><span>估算成本</span></div>
        <div><b id="st-groups">0</b><span>目标群</span></div>
      </div>
      <table id="group-table"><thead><tr><th>群名</th><th>目标</th></tr></thead><tbody></tbody></table>
    </div>

    <div class="card">
      <h2>模型 API</h2>
      <div class="row"><label>Base URL</label><input type="text" id="api.base_url"></div>
      <div class="row"><label>API Key</label><input type="password" id="api.api_key"></div>
      <div class="row"><label>模型</label><input type="text" id="api.model"></div>
      <div class="row"><label>视觉(看图)</label><input type="checkbox" id="api.vision"></div>
      <div class="row"><label>温度</label><input type="range" id="api.temperature" min="0" max="1" step="0.05"><span class="val" id="api.temperature-v">0.8</span></div>
      <div class="btns">
        <button id="testApi">测试 API 连通</button>
        <span class="hint" id="testResult"></span>
      </div>
    </div>

    <div class="card">
      <h2>微信</h2>
      <div class="row"><label>机器人昵称</label><input type="text" id="wechat.bot_nickname"></div>
      <div class="row"><label>轮询间隔(秒)</label><input type="text" id="wechat.poll_interval"></div>
      <div class="row"><label>群白名单</label><input type="text" id="wechat.group_name_white_list" placeholder="逗号分隔，留空=所有群"></div>
    </div>

    <div class="card">
      <h2>人设与响应档位</h2>
      <div class="row"><label>人设名</label><input type="text" id="persona.bot_name"></div>
      <div class="row"><label>参与度</label>
        <select id="persona.participation">
          <option value="low">安静型</option><option value="medium">普通群友</option><option value="high">活跃型</option>
        </select></div>
      <div class="row"><label>响应档位</label>
        <select id="store.context_tier">
          <option value="1">1 档：仅艾特</option><option value="2">2 档：+关键词</option>
          <option value="3">3 档：+随机</option><option value="4">4 档：全响应</option>
        </select></div>
      <div class="row"><label>关键词(逗号)</label><input type="text" id="store.keywords" placeholder="命中即响应（2档起）"></div>
    </div>

    <div class="card">
      <h2>联网搜索</h2>
      <div class="row"><label>启用</label><input type="checkbox" id="web_search.enabled"></div>
      <div class="row"><label>引擎</label>
        <select id="web_search.provider">
          <option value="bing">Bing（免key）</option><option value="deepseek">DeepSeek</option>
          <option value="zhipu">智谱</option><option value="bocha">博查</option>
          <option value="baidu">百度千帆</option><option value="metaso">秘塔</option><option value="custom">自定义</option>
        </select></div>
    </div>

    <div class="card">
      <h2>界面适配（DPI / 遮挡）</h2>
      <div class="row"><label>显示缩放</label>
        <select id="ui.coord_scale">
          <option value="auto">按系统自动检测</option>
          <option value="1.0">100%（无缩放）</option>
          <option value="1.25">125%</option>
          <option value="1.5">150%</option>
          <option value="1.75">175%</option>
          <option value="2.0">200%</option>
        </select></div>
      <div class="row"><label>点击前清遮挡</label>
        <input type="checkbox" id="ui.clean_overlays" title="自动关闭手写输入画布等系统叠加层、最小化遮挡窗口（推荐开启）">
      </div>
      <div class="btns">
        <button id="selfCheck" class="ghost">一键体检</button>
        <button id="pokeTest" class="ghost">拍一拍诊断</button>
        <span class="hint" id="uiTestResult"></span>
      </div>
      <pre id="selfCheckResult" class="hint" style="white-space:pre-wrap;margin-top:8px;display:none"></pre>
      <div class="hint" id="uiTestDetail">一键体检：检查配置/微信窗口/消息库/目标群/缩放叠加层/点击命中测试，输出每项通过/注意/失败与建议。拍一拍诊断：完整执行 定位头像→右键→点拍一拍→验证（目标不在可见区会自动向上翻页），并输出每一步结果（点击前请勿动鼠标）。</div>
    </div>

    <div class="card">
      <h2>完整配置 JSON（高级）</h2>
      <textarea id="rawjson" spellcheck="false"></textarea>
      <div class="btns">
        <button id="save">保存设置</button>
        <button id="reload" class="ghost">重新加载</button>
      </div>
      <div class="hint">保存后需要重启机器人（或至少重启核心循环）才完全生效；部分设置即时生效。</div>
    </div>
  </div>

  <div>
    <div class="card">
      <h2>运行日志</h2>
      <div class="btns" style="margin-top:0;margin-bottom:10px">
        <button id="refreshLog" class="ghost">刷新</button>
        <label style="color:var(--tx2)"><input type="checkbox" id="autolog" checked> 自动刷新</label>
      </div>
      <div id="log">加载中…</div>
    </div>
  </div>
</div>

<div id="toast"></div>

<!-- 小鲸鱼余额挂件（DeepSeek-Balance-Whale-Widget 迁移版，原项目客户端脚本原样引入） -->
<script defer src="/dsh-whale/widget.js?token=__TKN__"></script>

<script>
const $ = id => document.getElementById(id);
let cfg = null;

function toast(msg){const t=$('toast');t.textContent=msg;t.style.display='block';clearTimeout(t._h);t._h=setTimeout(()=>t.style.display='none',2500)}

// 从网址里取访问口令（形如 ?token=xxxx），后续所有请求都带上，避免 401
const URL_TOKEN = new URLSearchParams(location.search).get('token') || '';

async function getJSON(url, opts){
  opts = opts || {};
  opts.headers = opts.headers || {};
  if(URL_TOKEN) opts.headers['Authorization'] = 'Bearer ' + URL_TOKEN;
  const r = await fetch(url, opts);
  if(!r.ok) throw new Error((await r.text())||r.status);
  return r.json();
}

function syncToForm(){
  if(!cfg) return;
  const map = {
    'api.base_url': cfg.api.base_url, 'api.api_key': cfg.api.api_key, 'api.model': cfg.api.model,
    'api.vision': cfg.api.vision, 'api.temperature': cfg.api.temperature,
    'wechat.bot_nickname': cfg.wechat.bot_nickname, 'wechat.poll_interval': cfg.wechat.poll_interval,
    'wechat.group_name_white_list': (cfg.wechat.group_name_white_list||[]).join(','),
    'persona.bot_name': cfg.persona.bot_name, 'persona.participation': cfg.persona.participation,
    'store.context_tier': String(cfg.store.context_tier||4),
    'store.keywords': (cfg.store.keywords||[]).join(','),
    'web_search.enabled': cfg.web_search.enabled, 'web_search.provider': cfg.web_search.provider,
  };
  for(const k in map){ const el=$(k); if(el) el.value = map[k]; if(el && el.type==='checkbox') el.checked = !!map[k]; }
  const ui = cfg.ui || {};
  $('ui.coord_scale').value = String(ui.coord_scale !== undefined ? ui.coord_scale : 'auto');
  $('ui.clean_overlays').checked = ui.clean_overlays !== false;
  $('api.temperature-v').textContent = cfg.api.temperature;
  $('rawjson').value = JSON.stringify(cfg, null, 2);
  $('model-badge').textContent = '模型：' + (cfg.api.model||'未设置');
}

function syncFromForm(){
  cfg.api.base_url = $('api.base_url').value.trim();
  cfg.api.api_key = $('api.api_key').value.trim();
  cfg.api.model = $('api.model').value.trim();
  cfg.api.vision = $('api.vision').checked;
  cfg.api.temperature = parseFloat($('api.temperature').value);
  cfg.wechat.bot_nickname = $('wechat.bot_nickname').value.trim();
  cfg.wechat.poll_interval = parseFloat($('wechat.poll_interval').value)||3;
  cfg.wechat.group_name_white_list = $('wechat.group_name_white_list').value.split(',').map(s=>s.trim()).filter(Boolean);
  cfg.persona.bot_name = $('persona.bot_name').value.trim();
  cfg.persona.participation = $('persona.participation').value;
  cfg.store.context_tier = parseInt($('store.context_tier').value)||4;
  cfg.store.keywords = $('store.keywords').value.split(',').map(s=>s.trim()).filter(Boolean);
  cfg.web_search.enabled = $('web_search.enabled').checked;
  cfg.web_search.provider = $('web_search.provider').value;
  if(!cfg.ui) cfg.ui = {};
  const uiScale = $('ui.coord_scale').value;
  cfg.ui.coord_scale = uiScale === 'auto' ? 'auto' : parseFloat(uiScale) || 'auto';
  cfg.ui.clean_overlays = $('ui.clean_overlays').checked;
}

async function load(){
  try{ cfg = await getJSON('/api/config'); syncToForm(); }catch(e){ toast('加载配置失败：'+e.message) }
  loadStatus(); loadLog(); loadBalance();
}

async function loadBalance(){
  const el = $('balance-badge');
  try{
    const b = await getJSON('/api/balance');
    if(b.ok === false){
      el.textContent = '余额：' + b.error;
      return;
    }
    const cur = b.currency === 'USD' ? '$' : '¥';
    const txt = '余额 ' + cur + b.total_balance + '（充值 ' + b.topped_up_balance + ' / 赠送 ' + b.granted_balance + '）';
    el.textContent = txt; el.title = '点击刷新余额';
  }catch(e){
    el.textContent = '余额：查询失败';
  }
}

async function loadStatus(){
  try{
    const s = await getJSON('/api/status');
    $('dot').className = 'dot ' + (s.wechat_connected ? 'on':'');
    $('st-sessions').textContent = s.stats.sessions;
    $('st-tokens').textContent = s.stats.tokens;
    $('st-sent').textContent = s.stats.sent;
    $('st-cost').textContent = '¥' + (s.stats.cost||0).toFixed(4);
    $('st-groups').textContent = s.groups.filter(g=>g.target).length;
    $('pauseBtn').textContent = s.paused ? '恢复' : '暂停';
    const tb = $('group-table').querySelector('tbody'); tb.innerHTML='';
    for(const g of s.groups){
      const tr=document.createElement('tr');
      tr.innerHTML = '<td>'+esc(g.name)+'</td><td><span class="pill '+(g.target?'ok':'off')+'">'+(g.target?'监听':'忽略')+'</span></td>';
      tb.appendChild(tr);
    }
  }catch(e){}
}

async function loadLog(){
  try{ const l = await getJSON('/api/logs'); $('log').textContent = l.lines.join('\n'); $('log').scrollTop = $('log').scrollHeight; }catch(e){}
}

function esc(s){return String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}

$('api.temperature').addEventListener('input', ()=>$('api.temperature-v').textContent=$('api.temperature').value);
$('save').onclick = async ()=>{
  try{
    // 若用户直接改过 rawjson 则优先用 rawjson，否则用表单
    let raw = null;
    try{ raw = JSON.parse($('rawjson').value); }catch(e){}
    if(raw){ cfg = raw; } else { syncFromForm(); }
    const saved = await getJSON('/api/config', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(cfg)});
    toast('已保存 ' + new Date().toLocaleTimeString());
    syncToForm();
  }catch(e){ toast('保存失败：'+e.message) }
};
$('reload').onclick = load;
$('refreshLog').onclick = loadLog;
$('balance-badge').onclick = loadBalance;
$('rawJsonBtn').onclick = ()=>{
  window.open('/api/config' + (URL_TOKEN ? ('?token=' + URL_TOKEN) : ''), '_blank');
};
$('pauseBtn').onclick = async ()=>{
  try{ await getJSON($('pauseBtn').textContent==='暂停'?'/api/pause':'/api/resume', {method:'POST'}); loadStatus(); }catch(e){toast(e.message)}
};
$('stopBtn').onclick = async ()=>{
  if(!confirm('确定停止机器人？停止后需重新双击「启动机器人.bat」才能再跑。')) return;
  try{
    await getJSON('/api/shutdown', {method:'POST'});
    toast('已停止机器人');
    $('dot').className = 'dot';
  }catch(e){ toast('停止失败：'+e.message); }
};
$('testApi').onclick = async ()=>{
  const btn=$('testApi'); btn.disabled=true; $('testResult').textContent='测试中…';
  try{
    const r = await getJSON('/api/test-api', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({})});
    if(r.ok) $('testResult').textContent = '✅ 延迟 '+r.latency_ms+'ms，模型 '+r.model+'：'+r.reply;
    else $('testResult').textContent = '❌ '+(r.error||'失败');
  }catch(e){ $('testResult').textContent='❌ '+e.message; }
  finally{ btn.disabled=false; }
};
$('selfCheck').onclick = async ()=>{
  const btn=$('selfCheck'); btn.disabled=true;
  const pre=$('selfCheckResult'); pre.style.display='block';
  pre.textContent='体检中（约 10~20 秒，会移动光标做命中测试）…';
  try{
    const r = await getJSON('/api/selfcheck', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({})});
    let lines = ['===== 一键体检 =====', r.summary || '', ''];
    for(const c of (r.checks||[])){
      const mark = c.status==='ok' ? '✅' : (c.status==='warn' ? '⚠️' : (c.status==='fail' ? '❌' : 'ℹ️'));
      lines.push(mark+' '+c.name+'：'+c.detail);
      if(c.hint) lines.push('     建议：'+c.hint);
    }
    pre.textContent = lines.join('\n');
  }catch(e){ pre.textContent='体检失败：'+e.message; }
  finally{ btn.disabled=false; }
};
$('pokeTest').onclick = async ()=>{
  const btn=$('pokeTest'); btn.disabled=true; $('uiTestResult').textContent='诊断中（约 15~30 秒，请勿动鼠标）…';
  try{
    const r = await getJSON('/api/poke-test', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({})});
    const steps = (r.steps||[]).join('\n');
    $('uiTestResult').textContent = (r.ok?'✅ ':'❌ ')+(r.message || r.error || '(无结果)');
    $('uiTestDetail').textContent = '目标：'+(r.target?(r.target.name+' / '+r.target.id+' 在群「'+(r.group||'?')+'」'):'未解析')+'\n步骤：\n'+(steps || (r.error||''));
  }catch(e){ $('uiTestResult').textContent='❌ '+e.message; }
  finally{ btn.disabled=false; }
};

load();
setInterval(loadStatus, 8000);
setInterval(loadBalance, 30000);
setInterval(()=>{ if($('autolog').checked) loadLog(); }, 4000);

// 断线检测：机器人停止后显示全屏「已停止」提示，并尝试自动关闭本页面
let offlineShown = false;
async function checkAlive(){
  if(offlineShown) return;
  try{
    const r = await fetch('/api/status', {headers: URL_TOKEN?{Authorization:'Bearer '+URL_TOKEN}:{}});
    if(!r.ok) throw new Error(r.status);
  }catch(e){
    offlineShown = true;
    const ov=document.createElement('div');
    ov.id='stoppedOverlay';
    ov.style.cssText='position:fixed;inset:0;background:rgba(15,17,21,.97);z-index:99999;display:flex;flex-direction:column;align-items:center;justify-content:center;color:#e6e9ef;font:15px/2 sans-serif;text-align:center;padding:20px';
    ov.innerHTML='<div style="font-size:30px;font-weight:700;margin-bottom:10px">机器人已停止</div>'+
      '<div>后台进程已退出。请双击「启动机器人.bat」重新启动。</div>'+
      '<div style="margin-top:14px;color:#9aa4b2">本页面将在几秒后尝试自动关闭…</div>';
    document.body.appendChild(ov);
    setTimeout(()=>{ try{window.close();}catch(_e){} }, 4000);
  }
}
setInterval(checkAlive, 6000);
</script>
</body>
</html>
"""


class WebUI:
    """启动一个仅监听本机的 HTTP 服务，提供设置/状态/日志/测试 API 接口。"""

    def __init__(self, status_provider, log_buffer, test_api_fn=None, on_save=None,
                 pause_fn=None, resume_fn=None, balance_fn=None, shutdown_fn=None,
                 whale=None, poke_test_fn=None, selfcheck_fn=None, restart_fn=None,
                 groups_fn=None):
        self.status_provider = status_provider      # () -> dict
        self.log_buffer = log_buffer                # collections.deque[str]
        self.test_api_fn = test_api_fn              # () -> dict
        self.on_save = on_save                      # (new_cfg) -> None（可选，用于通知运行中组件）
        self.pause_fn = pause_fn or (lambda: None)  # () -> None
        self.resume_fn = resume_fn or (lambda: None)  # () -> None
        self.balance_fn = balance_fn or (lambda: {"error": "未提供 balance_fn"})  # () -> dict
        self.shutdown_fn = shutdown_fn or (lambda: None)  # () -> None
        self.restart_fn = restart_fn or (lambda: None)    # () -> None（后台无窗口重启）
        self.whale = whale                          # agent.whale.WhaleWidget（小鲸鱼挂件，可选）
        self.poke_test_fn = poke_test_fn or (lambda: {"error": "未提供 poke_test_fn"})  # () -> dict
        self.selfcheck_fn = selfcheck_fn or (lambda: {"ok": False, "error": "未提供 selfcheck_fn"})  # () -> dict
        self.groups_fn = groups_fn or (lambda: {"ok": True, "groups": []})  # () -> dict（群列表）
        self._server = None
        self._thread = None
        self.port = 0
        self._whale_js_cache = {}  # token -> bytes（注入口令后的挂件脚本缓存）
        # 加载图标（assets/icon.png），用于 favicon
        self._icon_bytes = b""
        try:
            icon_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "icon.png")
            with open(icon_path, "rb") as f:
                self._icon_bytes = f.read()
        except Exception:
            self._icon_bytes = b""

    # ── 小鲸鱼挂件路由（/dsh-whale/*，实现与原版插件一致的接口）───────────

    def _whale_get(self, handler, path: str, query: str):
        """GET /dsh-whale/* 分发。handler 是当前 HTTP Handler（带 _json/_bytes）。"""
        whale = self.whale
        if whale is None:
            return handler._json({"error": "not found"}, 404)
        if path == "/dsh-whale/balance.json":
            try:
                handler._json(whale.balance_payload())
            except Exception as e:
                handler._json({"ok": False, "error": str(e)[:200]})
        elif path == "/dsh-whale/size.json":
            handler._json(whale.size_payload())
        elif path == "/dsh-whale/last-turn.json":
            handler._json(whale.last_turn_payload())
        elif path == "/dsh-whale/image.png":
            handler._bytes(whale.asset_bytes("DSniang1.png") or b"", "image/png")
        elif path == "/dsh-whale/rua.gif":
            handler._bytes(whale.asset_bytes("rua.gif") or b"", "image/gif")
        elif path in ("/dsh-whale/sound/press.mp3", "/dsh-whale/sound/release.mp3"):
            kind = "press" if path.endswith("press.mp3") else "release"
            sound_set = (parse_qs(query).get("set") or [""])[0]
            data = whale.sound_bytes(kind, sound_set)
            handler._bytes(data or b"", "audio/mpeg")
        elif path == "/dsh-whale/widget.js":
            handler._bytes(self._whale_js_injected(), "application/javascript; charset=utf-8")
        else:
            handler._json({"error": "not found"}, 404)

    def _whale_js_injected(self) -> bytes:
        """返回注入口令后的挂件脚本字节（带缓存）。"""
        token = str(get_config().get("server", {}).get("token") or "").strip()
        if token in self._whale_js_cache:
            return self._whale_js_cache[token]
        try:
            js_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   "whale-widget", "client", "widget.js")
            with open(js_path, "r", encoding="utf-8") as f:
                js = f.read()
        except Exception:
            return b""
        if token:
            def _inj(m):
                base, q = m.group(1), (m.group(2) or "")[1:]
                return base + "?token=" + token + ("&" + q if q else "")
            js = _WHALE_URL_RE.sub(_inj, js)
        body = js.encode("utf-8")
        if len(self._whale_js_cache) > 4:
            self._whale_js_cache.clear()
        self._whale_js_cache[token] = body
        return body

    def start(self) -> int:
        cfg = get_config().get("server", {})
        if cfg.get("enabled") is False:
            return 0
        host = str(cfg.get("host") or "127.0.0.1")
        port = int(cfg.get("port") or 3210)

        parent = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "wx-agent/1.0"

            def log_message(self, fmt, *args):
                pass  # 静默，避免刷屏

            def _json(self, obj, code=200):
                body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _bytes(self, body, ctype="application/octet-stream", code=200):
                if not body:
                    body = b""
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _auth_ok(self):
                token = str(get_config().get("server", {}).get("token") or "").strip()
                if not token:
                    return True
                # 支持 ?token= 或 Authorization: Bearer
                q = urlparse(self.path).query
                from urllib.parse import parse_qs
                if token in parse_qs(q).get("token", []):
                    return True
                auth = self.headers.get("Authorization", "")
                return auth == "Bearer " + token

            def do_GET(self):
                if not self._auth_ok():
                    return self._json({"error": "unauthorized"}, 401)
                parsed = urlparse(self.path)
                path = parsed.path
                if path in ("/", "/index.html"):
                    token = str(get_config().get("server", {}).get("token") or "").strip()
                    body = HTML.replace("__TKN__", token).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif path.startswith("/dsh-whale/"):
                    parent._whale_get(self, path, parsed.query)
                elif path == "/assets/icon.png":
                    body = parent._icon_bytes
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif path == "/api/config":
                    self._json(get_config())
                elif path == "/api/status":
                    self._json(parent.status_provider())
                elif path == "/api/balance":
                    try:
                        self._json(parent.balance_fn())
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/logs":
                    self._json({"lines": list(parent.log_buffer)})
                elif path == "/api/wechat-groups":
                    # 检测到的群聊列表（白名单勾选用，GET）
                    try:
                        self._json(parent.groups_fn())
                    except Exception as e:
                        self._json({"ok": False, "error": str(e), "groups": []})
                else:
                    self._json({"error": "not found"}, 404)

            def do_POST(self):
                if not self._auth_ok():
                    return self._json({"error": "unauthorized"}, 401)
                self._handle_body_request()

            def do_PUT(self):
                # 小鲸鱼挂件前端用 PUT 保存配置（fetch SIZE_URL, {method:'PUT'}）
                if not self._auth_ok():
                    return self._json({"error": "unauthorized"}, 401)
                self._handle_body_request()

            def _handle_body_request(self):
                path = urlparse(self.path).path
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    data = json.loads(raw.decode("utf-8")) if raw else {}
                except Exception:
                    data = {}
                if path == "/api/config":
                    try:
                        new_cfg = data if isinstance(data, dict) and data else get_config()
                        set_config(new_cfg)
                        save_config(new_cfg)
                        if parent.on_save:
                            parent.on_save(new_cfg)
                        self._json({"ok": True})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)}, 500)
                elif path == "/dsh-whale/size.json":
                    # 小鲸鱼挂件配置保存（前端 PUT）
                    if parent.whale is None:
                        self._json({"error": "not found"}, 404)
                    else:
                        try:
                            self._json(parent.whale.save_size(data))
                        except Exception as e:
                            self._json({"ok": False, "error": str(e)}, 500)
                elif path == "/api/test-api":
                    try:
                        if parent.test_api_fn:
                            self._json(parent.test_api_fn())
                        else:
                            self._json({"ok": False, "error": "未提供 test_api_fn"})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/poke-test":
                    # 拍一拍诊断：完整跑一遍并返回分步结果
                    try:
                        self._json(parent.poke_test_fn())
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/selfcheck":
                    # 一键体检：配置/微信/数据/界面适配/命中测试 全套
                    try:
                        self._json(parent.selfcheck_fn())
                    except Exception as e:
                        self._json({"ok": False, "checks": [], "summary": str(e)})
                elif path == "/api/wechat-groups":
                    # 检测到的群聊列表（白名单勾选用）
                    try:
                        self._json(parent.groups_fn())
                    except Exception as e:
                        self._json({"ok": False, "error": str(e), "groups": []})
                elif path == "/api/pause":
                    parent.pause_fn()
                    self._json({"ok": True})
                elif path == "/api/resume":
                    parent.resume_fn()
                    self._json({"ok": True})
                elif path == "/api/shutdown":
                    self._json({"ok": True, "note": "正在停止机器人…"})
                    # 稍等响应返回后再触发停止，避免连接被切断
                    threading.Timer(0.5, parent.shutdown_fn).start()
                elif path == "/api/restart":
                    # 重启：后台无窗口拉起新实例（释放端口后接替），当前实例退出
                    self._json({"ok": True, "note": "正在后台重启机器人…"})
                    threading.Timer(0.5, parent.restart_fn).start()
                else:
                    self._json({"error": "not found"}, 404)

        # 端口自适应：被占用则顺延
        for offset in range(20):
            try:
                self._server = ThreadingHTTPServer((host, port + offset), Handler)
                self.port = port + offset
                break
            except OSError:
                continue
        if self._server is None:
            raise RuntimeError("无法启动 Web 控制台：端口 %d-%d 均被占用" % (port, port + 19))

        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.port

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
