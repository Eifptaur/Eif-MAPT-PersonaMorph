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
  /* ── 默认主题：「鲸落」深蓝海（whale）——海浪底图 + 深蓝 tint + 慢速动效 ──
     背景=实拍海浪（assets/wallpaper/ocean1.jpg，本机文件），深蓝 tint 与慢速缩放；
     卡片=浅蓝半透毛玻璃、导航栏=更深蓝实体——色差分三层凸显透明 */
  --blue:#6FCFFF; --blue2:#4FB3F2; --blue-soft:rgba(63,168,240,.15); --blue-line:rgba(120,190,255,.32);
  --bg:linear-gradient(160deg,rgba(8,30,58,.62),rgba(12,44,84,.45) 45%,rgba(18,48,96,.55) 100%);
  --bg-solid:rgba(12,34,62,.86);
  --card:rgba(150,206,255,.10);
  --bd:rgba(170,215,255,.26); --tx:#EAF6FF; --tx2:#A9D1EC;
  --ok:#35F0C0; --warn:#FFD166; --err:#FF8A8A;
  --shadow:inset 0 1px 0 rgba(255,255,255,.26),0 10px 34px rgba(10,40,80,.5),0 2px 8px rgba(0,20,40,.4);
  --input-bg:rgba(255,255,255,.09); --hover-bg:rgba(255,255,255,.16); --input-bd:rgba(160,210,255,.35); --topbar:rgba(8,24,46,.7);
  --code-bg:rgba(4,16,32,.7); --code-tx:#BFE9FF; --ok-soft:rgba(53,240,192,.14); --ok-tx:#7AF9E2;
  --err-soft:rgba(255,138,138,.16); --err-tx:#FFB0B0; --menu-bg:#0C2440;
}
/* 浅色主题（手动）——明亮蓝白 */
:root[data-theme=light]{
  --blue:#5B78F7; --blue2:#4A67F0; --blue-soft:#EEF2FF; --blue-line:#DCE4FF;
  --bg:#F5F7FD; --bg-solid:#F5F7FD; --card:#FFFFFF; --bd:#EBEFF8; --tx:#1F2937; --tx2:#6B7280;
  --ok:#10B981; --warn:#F59E0B; --err:#EF4444; --shadow:0 1px 3px rgba(31,41,55,.06),0 8px 24px rgba(77,107,254,.06);
  --input-bg:#F8FAFE; --hover-bg:#F8FAFF; --input-bd:#DCE4FF; --topbar:rgba(255,255,255,.92);
  --code-bg:#0F172A; --code-tx:#D8E0F0; --ok-soft:#D1FAE5; --ok-tx:#047857;
  --err-soft:#FEE2E2; --err-tx:#B91C1C; --menu-bg:#FFFFFF;
}
/* 深色主题（手动） */
:root[data-theme=dark]{
  --blue:#7C96FF; --blue2:#5F7BFF; --blue-soft:#1E2A4A; --blue-line:#2A3A66;
  --bg:#0E1420; --bg-solid:#131A27; --card:#151D2E; --bd:#263348; --tx:#E6EAF3; --tx2:#98A6C0;
  --ok:#34D399; --warn:#FBBF24; --err:#F87171;
  --shadow:0 1px 3px rgba(0,0,0,.4),0 8px 24px rgba(0,0,0,.35);
  --input-bg:#0F1626; --hover-bg:#1B2438; --input-bd:#2A3A66; --topbar:rgba(21,29,46,.92);
  --code-bg:#0A0E16; --code-tx:#A9B8D0; --ok-soft:#10352A; --ok-tx:#5EEAD4;
  --err-soft:#3A1A1A; --err-tx:#FCA5A5; --menu-bg:#1B2438;
}
/* 系统跟随（仅未手动设置主题（无 data-theme=跟随系统）时生效；whale/light/dark 都不跟随） */
@media (prefers-color-scheme: dark){
  :root:not([data-theme]){
    --blue:#7C96FF; --blue2:#5F7BFF; --blue-soft:#1E2A4A; --blue-line:#2A3A66;
    --bg:#0E1420; --bg-solid:#131A27; --card:#151D2E; --bd:#263348; --tx:#E6EAF3; --tx2:#98A6C0;
    --ok:#34D399; --warn:#FBBF24; --err:#F87171;
    --shadow:0 1px 3px rgba(0,0,0,.4),0 8px 24px rgba(0,0,0,.35);
    --input-bg:#0F1626; --hover-bg:#1B2438; --input-bd:#2A3A66; --topbar:rgba(21,29,46,.92);
    --code-bg:#0A0E16; --code-tx:#A9B8D0; --ok-soft:#10352A; --ok-tx:#5EEAD4;
    --err-soft:#3A1A1A; --err-tx:#FCA5A5; --menu-bg:#1B2438;
  }
}
body.whale-anim{background:var(--bg) fixed}
body.whale-anim::before{content:"";position:fixed;inset:-60px;z-index:-1;pointer-events:none;
  background-image:url(/assets/ocean1.jpg);
  background-size:cover;background-position:center;
  filter:saturate(1.1) brightness(.55) hue-rotate(-8deg) contrast(1.05);
  animation:oceanDrift 46s ease-in-out infinite alternate}
@keyframes oceanDrift{from{transform:scale(1) translateY(0)}to{transform:scale(1.08) translateY(-18px)}}
body.whale-anim::after{content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;
  background:
    radial-gradient(560px 560px at 12% 8%,rgba(120,210,255,.20),transparent 62%),
    radial-gradient(420px 420px at 88% 20%,rgba(150,180,255,.15),transparent 64%),
    radial-gradient(300px 300px at 78% 68%,rgba(120,220,200,.12),transparent 65%),
    radial-gradient(240px 240px at 36% 26%,rgba(255,220,170,.10),transparent 65%),
    var(--bg)}
/* 导航栏：更深蓝实体（与卡片/背景拉开色差；用不透明色避免透出海洋渐变导致滚动后上下色差——030117） */
.side{background:rgba(11,30,56,1);border:1px solid rgba(120,180,240,.28);box-shadow:var(--shadow);backdrop-filter:none}
.side::after{background:repeating-linear-gradient(115deg,rgba(255,255,255,.10) 0 1px,transparent 1px 22px);opacity:.5}
.side .nav a{background:transparent}
.side .nav a.on{background:rgba(63,168,240,.22);color:#fff;font-weight:600}
/* ── 海洋动态背景：三层大波浪 + 浪尖高光线（SVG 平移；无外部素材依赖）── */
.ocean-wave{position:fixed;left:0;right:0;bottom:0;height:40vh;z-index:-1;pointer-events:none;opacity:.95}
.ocean-wave svg{position:absolute;bottom:0;left:-50%;width:200%;height:100%;display:block}
.ocean-wave .w1{animation:waveMove 9s linear infinite}
.ocean-wave .w2{animation:waveMove 14s linear infinite reverse;opacity:.7}
.ocean-wave .w3{animation:waveMove 20s linear infinite;opacity:.45}
@keyframes waveMove{from{transform:translateX(0)}to{transform:translateX(25%)}}
/* 鲸鱼光斑背景：浮动加强（幅度 -22px/1.04 → -30px/1.06；流沙更快） */
body.whale-anim::before{content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;
  background:
    repeating-radial-gradient(160% 90% at 18% 12%,rgba(255,255,255,.22) 0 2px,transparent 2px 26px),
    repeating-radial-gradient(150% 80% at 82% 30%,rgba(120,210,255,.16) 0 2px,transparent 2px 40px),
    radial-gradient(560px 560px at 12% 8%,rgba(255,255,255,.50),transparent 62%),
    radial-gradient(420px 420px at 88% 20%,rgba(190,240,255,.45),transparent 64%),
    radial-gradient(300px 300px at 78% 68%,rgba(186,240,190,.38),transparent 65%),
    radial-gradient(300px 300px at 20% 78%,rgba(210,205,255,.38),transparent 65%),
    radial-gradient(240px 240px at 58% 40%,rgba(255,220,240,.30),transparent 65%),
    radial-gradient(200px 200px at 36% 26%,rgba(255,232,170,.28),transparent 65%);
  animation:whaleDrift 22s ease-in-out infinite alternate, seaFlow 7s linear infinite}
@keyframes whaleDrift{
  from{transform:translateY(0) scale(1) hue-rotate(0deg)}
  to{transform:translateY(-30px) scale(1.06) hue-rotate(12deg)}
}
@keyframes seaFlow{
  from{background-position:0 0,0 0,0 0,0 0,0 0,0 0,0 0,0 0}
  to{background-position:-90px 45px,90px -45px,0 0,0 0,0 0,0 0,0 0,0 0}
}
/* ── 自定义背景（JS 设 CSS 变量，绕开一切选择器冲突；无变量=默认海浪图 assets/wallpaper/ocean1.jpg）── */
body.whale-anim::before,body.custom-bg::before{
  background-image:var(--bgimg, url(/wallpaper/ocean1.jpg))!important}
body.custom-bg::after{background:linear-gradient(160deg,rgba(15,35,65,.10),rgba(20,45,80,.05) 50%,rgba(25,50,90,.08))}
body.wall-video #wallVideo{display:block}
body.wall-video #wallTint{display:block}
body.wall-video:not(.custom-bg)::before{opacity:0}   /* 视频模式隐藏静态背景；自定义背景优先 */
body.custom-bg::before{opacity:1!important}
/* ── 毛玻璃卡片（正常玻璃+雾蒙蒙；浅蓝为主 + 七彩折射；无划痕）── */
.card,.side{backdrop-filter:blur(22px) saturate(1.6)}
.card,.side{border:1px solid rgba(190,228,255,.45);box-shadow:var(--shadow)}
.card,.side{background:transparent}
.card::before,.side::before{content:"";position:absolute;inset:0;border-radius:inherit;pointer-events:none;
  background:radial-gradient(120% 120% at 50% 50%,rgba(180,222,255,.20),rgba(214,242,255,.055) 92%)}
.card::after,.side::after{content:"";position:absolute;inset:0;border-radius:inherit;pointer-events:none;
  background:
    radial-gradient(300px 220px at 14% 0%,rgba(255,255,255,.17),transparent 62%),
    radial-gradient(280px 200px at 88% 10%,rgba(190,240,255,.15),transparent 62%),
    radial-gradient(260px 200px at 80% 90%,rgba(220,205,255,.13),transparent 62%),
    radial-gradient(240px 180px at 18% 88%,rgba(255,220,240,.11),transparent 62%),
    radial-gradient(200px 160px at 45% 20%,rgba(255,232,170,.09),transparent 62%);
  opacity:.9}
:root[data-theme=dark] .card::after,:root[data-theme=dark] .side::after{opacity:.25}
/* ── 水光波纹 v10.-（修复：hover transform 创建 stacking context 导致输入框挡住下拉选单）──
   卡片浮起效果改为 filter+shadow（均不创建 stacking context 不锁层级），
   transform 保留在 .wave-char 等内部元素上（不涉及整卡分层）。 */
.card{transition:filter .5s ease,box-shadow .5s ease}
.card:hover{filter:drop-shadow(0 10px 22px rgba(63,168,240,.16));box-shadow:0 12px 30px rgba(63,168,240,.14),0 2px 8px rgba(31,41,55,.08)}
.card:hover::after{animation:cardShimmer 2.6s ease-in-out infinite}
@keyframes cardShimmer{0%,100%{opacity:.5}50%{opacity:1}}
.card:hover h2,.card:hover .row label{transform:translateY(-1.5px)}
.card h2,.card .row label{transition:transform .55s ease}
/* 逐字浮动：每个字一个 span，波浪相位递增（浮动增强：幅度 -2px → -3.5px） */
.wave-char{display:inline-block;animation:charFloat 3.0s ease-in-out infinite}
@keyframes charFloat{
  0%,100%{transform:translateY(0)}
  45%{transform:translateY(-3.5px)}
}

/* 卡片轻柔浮沉：改用 box-shadow 呼吸（transform 会锁内部层级/遮下拉——修复 1018） */
.card.float-a{animation:waveFloat 4.6s ease-in-out infinite}
@keyframes waveFloat{
  0%,100%{box-shadow:var(--shadow)}
  50%{box-shadow:0 10px 24px rgba(63,168,240,.12),0 2px 8px rgba(31,41,55,.08)}
}
/* 下拉菜单永远在最上层：即便卡片形成局部 stacking context，菜单自身 z 拉满 */
.dsel{z-index:70}
.dsel-menu{z-index:220;position:absolute}
/* 卡片内下拉菜单展开时允许溢出（默认 overflow:hidden 会裁剪菜单） */
.card:has(.dsel .dsel-menu:not(.dn)),.card:has(.box .dsel-menu:not(.dn)){overflow:visible}
.card.fx-overflow,.box.fx-overflow,.bill-dlg.fx-overflow{overflow:visible!important}
/* 水光波纹 v13「模块内投石入水」：透镜=光标所在整个模块（顶栏/导航栏/功能卡），
   单一窄环带从鼠标处一波波向外扩散（有肉眼可见时间差），到模块边缘极强衰减，绝不越过模块边界。
   z-index 40 < 顶栏50：功能栏滚到顶栏下方时，波纹只作用于下层内容，顶栏始终置顶不受扭曲。 */
#waveLens{position:fixed;left:0;top:0;pointer-events:none;z-index:40;opacity:0;
  -webkit-backdrop-filter:url(#cardWave2) saturate(1.02);
  backdrop-filter:url(#cardWave2) saturate(1.02);
  transform:translate3d(-9999px,-9999px,0)}
input,select,textarea{backdrop-filter:blur(8px)}
.pri{background:linear-gradient(135deg,#39B6F0,#1E9BE8 55%,#6C8CFF);box-shadow:0 4px 16px rgba(30,155,232,.38),inset 0 1px 0 rgba(255,255,255,.55);border:1px solid rgba(255,255,255,.72);color:#fff}
.pri:hover{filter:brightness(1.06)}
.danger{color:#fff}
.topbar{backdrop-filter:blur(14px) saturate(1.4)}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--tx);font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;min-height:100vh}
a{color:var(--blue)}
.icon{width:18px;height:18px;vertical-align:-3px;margin-right:6px}

/* ── 顶栏 ── */
.topbar{position:sticky;top:0;z-index:50;display:flex;align-items:center;gap:12px;padding:10px 20px;
  background:var(--topbar);backdrop-filter:blur(8px);border-bottom:1px solid var(--bd)}
.topbar button{white-space:nowrap}
.topbar .logo{display:flex;align-items:center;gap:12px;font-size:17px;font-weight:700;min-width:0}
.topbar .logo span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
/* 鲸鱼徽章：绿底 + 白鲸主体（可拖拽：按住鲸鱼拖出，松开随机三态返回）；flex 不收缩，尺寸恒定 */
.whale-badge{position:relative;flex:0 0 52px;min-width:52px;width:52px;height:52px;border-radius:13px;overflow:hidden;cursor:grab;
  background:url(/assets/logo-bg.png) center/cover;box-shadow:0 2px 8px rgba(31,41,55,.15)}
.whale-badge.whale-open{overflow:visible}      /* 拖拽中取消裁切 */
.whale-badge img{position:absolute;left:8px;bottom:6px;width:36px;height:36px;
  transform-origin:bottom center;will-change:transform}
.whale-badge img.whale-grabbing{filter:brightness(1.15) drop-shadow(0 6px 12px rgba(0,0,0,.45))}
.whale-badge:active{cursor:grabbing}
/* 游离鲸鱼（拖出后 & 返回动画载体；与帧独立，保证拖出框外可见） */
.whale-fly{position:fixed;z-index:99998;pointer-events:none;width:44px;height:44px;
  filter:drop-shadow(0 4px 8px rgba(0,0,0,.35))}
.whale-fly img{width:44px;height:44px;position:absolute;left:0;top:0;transition:opacity .05s}
.whale-fly .paper-plane{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);opacity:0;
  filter:drop-shadow(0 3px 6px rgba(0,0,0,.3))}
.whale-bubble{position:fixed;z-index:99997;font-style:normal;font-size:13px;color:var(--blue);
  pointer-events:none;opacity:.9;transition:opacity .55s, transform .55s ease-out}
.topbar .sp{flex:1}
.chip{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:16px;background:var(--bg-solid);
  border:1px solid var(--bd);color:var(--tx2);font-size:12px;white-space:nowrap}
.chip b{color:var(--tx)}
.chip .dot{width:8px;height:8px;border-radius:50%;background:var(--err)}
.chip .dot.on{background:var(--ok)}
.chip .dot.p{background:var(--warn)}

/* ── 布局 ── */
.shell{display:grid;grid-template-columns:216px 1fr;gap:16px;max-width:1280px;margin:16px auto;padding:0 16px}
@media(max-width:900px){.shell{grid-template-columns:1fr}}
/* 侧栏：完全不透明实色（滚动到底也无色差）+ sticky 让开顶栏 */
.side{background:rgba(12,32,58,1);border:1px solid var(--bd);border-radius:12px;padding:10px;
  position:sticky;top:88px;max-height:calc(100vh - 112px);overflow-y:auto;overscroll-behavior:contain;
  z-index:20;box-shadow:var(--shadow);backdrop-filter:none!important}
/* 导航栏单独做成「不透明单层」：去掉毛玻璃光斑/质感层（避免透出海洋渐变、叠光斑造成滚动色差——030117） */
.side::before,.side::after{content:none!important;display:none!important}
.side::-webkit-scrollbar{width:6px}
.side::-webkit-scrollbar-track{background:transparent}
.side::-webkit-scrollbar-thumb{background:rgba(148,196,255,.18);border-radius:3px}
.side::-webkit-scrollbar-thumb:hover{background:rgba(148,196,255,.32)}
.side .nav a{color:var(--tx2);border-radius:9px;margin:1px 0;background:transparent}
.side .nav a.on{background:rgba(63,168,240,.20);color:#fff;font-weight:600}
.side::-webkit-scrollbar{width:8px}
.side::-webkit-scrollbar-thumb{background:var(--input-bd);border-radius:4px}
.side::-webkit-scrollbar-thumb:hover{background:var(--blue)}
.side .status{background:var(--blue-soft);border:1px solid var(--blue-line);border-radius:10px;padding:10px 12px;margin-bottom:8px}
.side .status b{font-size:13px;color:var(--blue)}
.side .status p{font-size:12px;color:var(--tx2)}
.nav{position:relative}
.nav-ind{position:absolute;left:0;width:3px;border-radius:2px;background:var(--blue);
  top:0;height:3px;opacity:0;transition:top .28s cubic-bezier(.34,1.4,.64,1),opacity .2s}
.nav a{position:relative;z-index:1}
.nav a{display:flex;align-items:center;gap:8px;padding:9px 12px;border-radius:11px;color:var(--tx2);
  transition:background .18s ease,color .18s ease}
  text-decoration:none;font-size:13.5px;margin:2px 0}
.nav a:hover{background:var(--bg-solid)}
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
.pick .opt:hover{border-color:var(--blue);background:var(--hover-bg)}
.pick .opt b{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pick .opt .hint{margin:0;white-space:nowrap;font-size:11px;color:var(--tx2)}
.pick .opt input{width:16px;height:16px;accent-color:var(--blue)}
/* 群列表容器：固定高度滚动槽 + 顶部搜索框 */
.group-box{max-height:340px;overflow-y:auto;border:1px solid var(--bd);border-radius:10px;padding:6px;margin-top:6px}
.group-box .opt{margin-bottom:4px}
.group-search{width:100%;padding:9px 12px;border:1px solid var(--bd);border-radius:10px;font:inherit;margin-top:6px;box-sizing:border-box;
  background:var(--input-bg);color:var(--tx)}
.group-search:focus{border-color:var(--blue);outline:none}
.dlist{background:var(--menu-bg);border:1px solid var(--bd);border-radius:8px;padding:4px;font-size:13px}
.main{min-width:0}

.card{background:var(--card);border:1px solid var(--bd);border-radius:18px;padding:18px 20px;margin-bottom:16px;box-shadow:var(--shadow);position:relative;overflow:hidden}
.card h2{font-size:15px;margin-bottom:4px;color:var(--blue);display:flex;align-items:center;gap:6px}
/* 柔和过渡：卡片/按钮/输入/导航淡入与浮起 */
.card,button.pri,button.ghost,.nav a,.chips .c,.row input,.row select,.row textarea,.dsel-btn,.pick .opt{transition:transform .18s ease,box-shadow .18s ease,border-color .18s ease,background .18s ease,color .18s ease}
@keyframes wxpage{from{opacity:.4;transform:translateY(5px)}to{opacity:1;transform:none}}
.card{animation:wxpage .3s ease}
.mask .box{animation:wxpage .22s ease}
/* 首页右上角工具按钮（计费删除）——绝对定位到卡片右上角，与标题分离，保证可点层级 */
.ov-tools{position:absolute;top:14px;right:16px;display:flex;gap:6px;font-weight:400;z-index:80;pointer-events:auto}
#sec-overview h2{padding-right:240px}
button.tiny{padding:3px 10px;font-size:12px;border-radius:7px}
/* ── 计费日志勾选删除弹窗（更不透明设计，按天勾选，可一键勾一天/一月，删除后概览自动刷新）── */
.bill-dlg{background:linear-gradient(180deg,#101828,#0c1220)!important;border:1px solid #33415C!important;box-shadow:0 18px 60px rgba(0,0,0,.65),0 0 0 1px rgba(63,168,240,.10)!important;border-radius:14px}
.bill-dlg .bd-head{display:flex;align-items:center;gap:10px;margin-bottom:10px}
.bill-dlg .bd-head b{font-size:14px}
.bill-dlg .bd-head b.whale-tag{font-weight:700;color:var(--blue)}
.bill-dlg .bd-sel{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}
.bill-dlg .bd-sel button{font-size:12px;padding:3px 10px}
.bill-list{max-height:340px;overflow:auto;border:1px solid rgba(148,196,255,.18);border-radius:10px;padding:6px;background:rgba(10,16,28,.6)}
.bill-list .row{display:flex;align-items:center;gap:8px;padding:7px 10px;border-bottom:1px solid rgba(148,196,255,.08);margin:0;font-size:13px;border-radius:8px}
.bill-list .row:hover{background:rgba(63,168,240,.08)}
.bill-list .row.hi{background:rgba(63,168,240,.28)!important;outline:1px solid rgba(63,168,240,.7)}
.bill-list .row:last-child{border-bottom:none}
.bill-list .row label{display:flex;gap:6px;align-items:center;flex:1;margin:0}
.bill-list .row b{min-width:112px;font-weight:600}
.bill-list .row .hint{flex:1}
.bill-list b{color:var(--tx)}
.card .desc{font-size:12.5px;color:var(--tx2);margin-bottom:12px}
.row{display:flex;gap:12px;margin-bottom:12px;align-items:center;flex-wrap:wrap}
.row label{width:150px;color:var(--tx2);flex-shrink:0;font-size:13px}
.row .grow{flex:1;min-width:220px}
.row input[type=text],.row input[type=password],.row input[type=number],.row select,.row textarea{
  width:100%;background:var(--input-bg);border:1px solid var(--input-bd);color:var(--tx);
  border-radius:10px;padding:8px 12px;font:inherit;outline:none;transition:border .15s,box-shadow .15s}
.row input:focus,.row select:focus,.row textarea:focus{border-color:var(--blue);box-shadow:0 0 0 3px rgba(77,107,254,.12)}
.row select{appearance:none;-webkit-appearance:none;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8'%3E%3Cpath d='M1 1l5 5 5-5' stroke='%23 4D6BFE' stroke-width='2' fill='none' stroke-linecap='round'/%3E%3C/svg%3E");
  background-repeat:no-repeat;background-position:right 12px center;padding-right:30px;border-radius:10px}
.row select option{border-radius:10px;background:var(--menu-bg);color:var(--tx);padding:6px}
/* 自绘下拉（原生弹层无法样式化，全部替换为这个） */
.dsel{position:relative;width:100%}
.dsel-btn{width:100%;display:flex;align-items:center;justify-content:space-between;gap:8px;white-space:nowrap;
  background:var(--input-bg);border:1px solid var(--input-bd);border-radius:10px;padding:8px 12px;color:var(--tx);
  font:inherit;font-weight:500;text-align:left;cursor:pointer}
.dsel-btn .txt{overflow:hidden;text-overflow:ellipsis}
.dsel-btn:hover{border-color:var(--blue)}
.dsel-btn .arr{color:var(--blue);font-size:11px;transform:translateY(-1px)}
.dsel-menu{position:absolute;left:0;right:0;top:calc(100% + 4px);z-index:220;background:var(--menu-bg);
  border:1px solid var(--input-bd);border-radius:10px;box-shadow:0 10px 30px rgba(77,107,254,.14);
  max-height:260px;overflow:auto;padding:5px}
.dsel-menu li{list-style:none;padding:8px 12px;border-radius:8px;cursor:pointer;font-size:13.5px;color:var(--tx)}
.dsel-menu li:hover{background:var(--blue-soft);color:var(--blue)}
.dsel-menu li.on{background:var(--blue);color:#fff;font-weight:600}
body.locked{overflow:hidden}
.row textarea{min-height:84px;font-family:ui-monospace,Consolas,monospace;font-size:12.5px}
.row input[type=range]{flex:1}
.row .val{width:44px;text-align:right;color:var(--blue);font-weight:600}
.row input[type=checkbox]{width:16px;height:16px;accent-color:var(--blue)}
/* 省 token 开关：醒目的卡片式勾选 */
.think-card{display:flex;gap:12px;align-items:flex-start;background:linear-gradient(135deg,var(--blue-soft),var(--hover-bg));
  border:1.5px solid var(--blue-line);border-radius:12px;padding:12px 14px;margin-bottom:12px;cursor:pointer}
.think-card input[type=checkbox]{width:20px;height:20px;accent-color:var(--blue);margin-top:2px;flex:none}
.think-card.on{background:linear-gradient(135deg,var(--ok-soft),var(--hover-bg));border-color:var(--ok)}
.think-card .tc-title{font-weight:700;font-size:13.5px;color:var(--tx)}
.think-card .tc-sub{font-size:12px;color:var(--tx2);margin-top:3px;line-height:1.6}
.think-card .tc-badge{display:inline-block;background:var(--ok);color:#fff;font-size:11px;border-radius:8px;
  padding:1px 8px;margin-left:6px;vertical-align:1px}
.mid{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:12px}
.mid > *{flex:1;min-width:240px}
.btns{display:flex;gap:10px;margin-top:8px;flex-wrap:wrap}
button{border:0;border-radius:8px;padding:8px 18px;cursor:pointer;font:inherit;font-weight:600;transition:.15s}
button.pri{background:var(--blue);color:#fff;box-shadow:0 4px 12px rgba(77,107,254,.3)}
button.pri:hover{background:var(--blue2)}
button.ghost{background:var(--card);border:1px solid var(--bd);color:var(--tx)}
button.ghost:hover{border-color:var(--blue);color:var(--blue)}
button.danger{background:var(--err-soft);color:var(--err-tx)}
button.danger:hover{filter:brightness(1.12)}
button:disabled{opacity:.5;cursor:not-allowed}
.hint{color:var(--tx2);font-size:12px;margin-top:6px}
.hint a{color:var(--blue)}
pre.out{background:var(--code-bg);color:var(--code-tx);border-radius:10px;padding:12px 14px;font:12px/1.55 ui-monospace,Consolas,monospace;
  overflow:auto;margin-top:8px;white-space:pre-wrap;word-break:break-all}

/* ── 概览 ── */
#calGrid button{transition:transform .12s ease,box-shadow .12s ease,background .12s ease,border-color .12s ease}
#calGrid button:hover{transform:scale(1.06)}
#calGrid button.sel{background:var(--blue)!important;color:#fff!important;font-weight:700;transform:scale(1.12);box-shadow:0 0 0 2px rgba(63,168,240,.55),0 4px 12px rgba(63,168,240,.35)}
.stat{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:14px}
.stat .s{background:var(--bg-solid);border:1px solid var(--bd);border-radius:10px;padding:12px 14px}
.stat .s b{font-size:20px;display:block;color:var(--blue)}
.stat .s span{color:var(--tx2);font-size:12px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--bd)}
th{color:var(--tx2);font-weight:500}
.pill{display:inline-block;padding:1px 10px;border-radius:10px;font-size:11px;background:var(--bg-solid)}
.pill.ok{background:var(--ok-soft);color:var(--ok-tx)}
.pill.off{background:var(--err-soft);color:var(--err-tx)}

/* ── 弹层 ── */
#toast{position:fixed;right:20px;bottom:20px;background:#0F172A;color:#fff;padding:11px 18px;border-radius:10px;
  display:none;z-index:9999;font-size:13px;box-shadow:0 8px 24px rgba(0,0,0,.25)}
.mask{position:fixed;inset:0;background:var(--bg-solid);z-index:9998;display:flex;align-items:center;justify-content:center;padding:20px}
.mask .box{max-width:560px;width:100%;background:var(--card);border:1px solid var(--blue-line);border-radius:16px;
  padding:28px 30px;box-shadow:0 20px 60px rgba(77,107,254,.18);text-align:center}
.mask .box img{width:72px;height:72px;border-radius:18px;margin-bottom:12px;box-shadow:0 6px 20px rgba(77,107,254,.3)}
.mask .box h1{font-size:19px;margin-bottom:8px}
.mask .box p{color:var(--tx2);font-size:13px;margin-bottom:14px}
.mask .box input{width:100%;padding:10px 12px;border:1px solid var(--bd);border-radius:8px;font:inherit;margin-bottom:10px;
  background:var(--input-bg);color:var(--tx)}
.dn{display:none}
</style>
</head>
<body>

<!-- 水光透镜滤镜（feTurbulence+feDisplacementMap；JS 正弦动画持续调 scale/baseFrequency，波纹永远在流动） -->
<svg width="0" height="0" style="position:absolute"><defs>
  <filter id="cardWave2" x="-15%" y="-15%" width="130%" height="130%">
    <feTurbulence type="fractalNoise" baseFrequency="0.012 0.016" numOctaves="2" seed="5" result="n"/>
    <feDisplacementMap in="SourceGraphic" in2="n" scale="17" xChannelSelector="R" yChannelSelector="G"/>
  </filter>
</defs></svg>
<!-- 海洋动态波浪（三层 SVG 曲线平移；无外部素材依赖） -->
<div class="ocean-wave">
  <svg class="w3" viewBox="0 0 1440 320" preserveAspectRatio="none"><path d="M0,230 C240,150 480,290 720,230 C960,150 1200,290 1440,230 L1440,320 L0,320 Z" fill="rgba(120,200,255,.40)"/></svg>
  <svg class="w2" viewBox="0 0 1440 320" preserveAspectRatio="none"><path d="M0,200 C240,120 480,280 720,200 C960,120 1200,280 1440,200 L1440,320 L0,320 Z" fill="rgba(160,222,255,.55)"/></svg>
  <svg class="w1" viewBox="0 0 1440 320" preserveAspectRatio="none"><path d="M0,160 C240,80 480,240 720,160 C960,80 1200,240 1440,160 L1440,320 L0,320 Z" fill="rgba(235,250,255,.80)"/><path d="M0,160 C240,80 480,240 720,160 C960,80 1200,240 1440,160" fill="none" stroke="rgba(255,255,255,.9)" stroke-width="5"/></svg>
</div>
<!-- 视频壁纸（Wallpaper Engine「海的眼睛.mp4」本地文件；加载失败自动回退 CSS 海浪） -->
<video id="wallVideo" autoplay muted loop playsinline
  src="/wallpaper/海的眼睛.mp4"
  style="position:fixed;inset:0;width:100%;height:100%;object-fit:cover;z-index:-2;pointer-events:none;display:none"></video>
<div id="wallTint" style="position:fixed;inset:0;z-index:-1;pointer-events:none;display:none;background:linear-gradient(160deg,rgba(150,200,235,.30),rgba(210,232,248,.22) 60%,rgba(225,215,245,.28))"></div>
<!-- 隐藏彩蛋（左下角，正常大小但低调；连点三下再点一下出"档案"，属于留待用户自己发现的彩蛋，不进任何说明） -->
<button id="easterEgg" class="ghost" type="button"
  style="position:fixed;left:12px;bottom:12px;z-index:9997;padding:5px 12px;font-size:12px;color:#ff5252;opacity:.5"
  title="……？">不要点！</button>

<!-- 水光透镜：跟随鼠标的扭曲圆环（backdrop-filter 只影响圈内；初始藏于屏外） -->
<div id="waveLens"></div>

<div class="topbar">
  <div class="logo"><div class="whale-badge" id="whaleBadge" title="小鲸鱼"><img src="/assets/icon-whale.png" alt=""></div><span>wx-agent 控制台 <small style="font-weight:400;color:var(--tx2);font-size:12px" title="构建号（换新包后如果这里不变，说明连的是旧实例——先停止再启动）">vβ·Ⅱ（__VER__）</small></span></div>  <div class="sp"></div>
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
      <a href="#sec-check">体检与功能自检</a>
      <a href="#sec-advanced">调试·高级功能</a>
      <a href="#sec-sessions">运行明细</a>
      <a href="#sec-model">模型 API</a>
      <a href="#sec-wechat">微信</a>
      <a href="#sec-poke">拍一拍</a>
      <a href="#sec-memory">记忆</a>
      <a href="#sec-memory-set">记忆共享</a>
      <a href="#sec-persona">人设与响应</a>
      <a href="#sec-community">社区与学习</a>
      <a href="#sec-send">发送限制</a>
      <a href="#sec-search">联网搜索</a>
      <a href="#sec-server">服务器</a>
      <a href="#sec-ui">界面适配</a>
      <a href="#sec-cursor">光标设置</a>
      <a href="#sec-wavefx">🌊 水光波纹</a>
      <a href="#sec-log">运行日志</a>
      <a href="#sec-json">原始 JSON</a>
    </nav>
  </aside>

  <main class="main">

    <section id="sec-overview" class="card" data-sec>
      <span class="ov-tools">
        <button class="ghost tiny" id="costClearAll" type="button" title="一键删除全部计费历史记录">🗑 一键删</button>
        <button class="ghost tiny" id="costClearSel" type="button" title="打开计费日志弹窗，勾选删除">☑ 勾选删</button>
        <button class="ghost tiny" id="dataExport" type="button" title="导出全部计费与对话记录为一个迁移包"><img src="/assets/icon-whale.png" style="width:14px;height:14px;vertical-align:-2px;margin-right:4px">导出记录</button>
        <button class="ghost tiny" id="dataImport" type="button" title="从迁移包导入（合并到当前数据，按内容去重）"><img src="/assets/icon-whale.png" style="width:14px;height:14px;vertical-align:-2px;margin-right:4px">迁移数据</button>
        <input type="file" id="dataImportFile" accept=".zip" style="display:none">
      </span>
      <h2>概览</h2>
      <div class="desc">机器人运作状态与账户信息（数据每 8 秒自动刷新）。</div>
      <div class="ov-checkbar" id="codeCheckTip" style="font-weight:700;font-size:12.5px;padding:8px 12px;border-radius:10px;border:1px solid var(--blue-line);background:rgba(63,168,240,.07);color:var(--blue);margin-bottom:12px">代码检测：尚未运行（点「检测中心」页的代码检测/代码检测＋依赖核对）</div>
      <div class="stat">
        <div class="s"><b id="st-sessions">0</b><span>累计会话数</span></div>
        <div class="s"><b id="st-tokens">0</b><span>累计 token</span></div>
        <div class="s"><b id="st-sent">0</b><span>已发消息</span></div>
        <div class="s"><b id="st-cost">¥0</b><span>累计成本</span></div>
        <div class="s" style="grid-column:span 2"><b id="st-dcost">—</b><span id="st-dlabel">今日用量</span></div>
        <div class="s"><b id="st-pcost">—</b><span id="st-plabel">本周期</span></div>
        <div class="s"><b id="st-groups">0</b><span>目标群</span></div>
        <div class="s"><b id="st-r5c">—</b><span>最近5条成本</span></div>
        <div class="s"><b id="st-ac">—</b><span>平均每条成本</span></div>
        <div class="s"><b id="st-extra">—</b><span>今日其他工具成本</span></div>
        <div class="s"><b id="st-extra2">—</b><span>累计其他工具成本</span></div>
      </div>
      <div class="card" id="feeCalc" style="margin-bottom:14px">
        <h2>🧮 费用计算器（官方峰谷价）</h2>
        <div class="desc">选模型与每日用量估算成本；高峰=工作日 9:00-12:00 / 14:00-18:00，其余为空闲价（周末全天空闲价）</div>
        <div class="row" style="margin:4px 0"><label>模型</label>
          <select id="fcModel">
            <option value="flash">deepseek-v4-flash</option>
            <option value="vision">deepseek-v4-flash-vision-exp</option>
          </select>
        </div>
        <div class="row" style="margin:4px 0"><label>每日消息数</label><input id="fcMsgs" type="number" value="200" min="0" style="width:130px"></div>
        <div class="row" style="margin:4px 0"><label>每消息输入 Token</label><input id="fcIn" type="number" value="800" min="0" style="width:130px"></div>
        <div class="row" style="margin:4px 0"><label>每消息输出 Token</label><input id="fcOut" type="number" value="800" min="0" style="width:130px"></div>
        <div class="row" style="margin:4px 0"><label>时段</label>
          <select id="fcPeak">
            <option value="0">空闲（夜间/周末）</option>
            <option value="1">高峰（工作日 9-12 / 14-18）</option>
          </select>
        </div>
        <div class="row" style="margin:6px 0"><label></label>
          <button class="pri" id="fcCalc" type="button">计算</button>
          <span class="hint" style="margin-left:8px">输入缓存命中按 0.02/0.04 元计价</span>
        </div>
        <div class="row" style="margin:6px 0"><label></label><b id="fcResult" style="color:var(--blue)">—</b></div>
      </div>
      <div style="margin:10px 0 2px;display:flex;align-items:center;gap:8px;flex-wrap:wrap">
        <b style="color:var(--blue)">📅 每日明细</b>
        <span class="hint" style="flex:1">点日期查看当天会话/词数/成本；点「年月」任意地方跳转年份</span>
        <button class="ghost" id="calPrev">‹</button>
        <button class="ghost" id="calYM" title="点击跳转年份（有特效）" style="min-width:104px;text-align:center;background:linear-gradient(135deg,var(--blue-soft),var(--hover-bg));border:1px solid var(--blue-line)"></button>
        <button class="ghost" id="calNext">›</button>
      </div>
      <div id="calGrid" style="display:grid;grid-template-columns:repeat(7,1fr);gap:3px;font-size:12px;margin-bottom:6px"></div>
      <div id="calDetail" class="hint" style="margin-bottom:4px">点日期查看当天明细</div>
      <div class="group-box" style="max-height:240px">
        <table id="group-table" style="margin:0"><thead><tr><th>群名</th><th>目标</th></tr></thead><tbody></tbody></table>
      </div>
      <div class="btns">
        <button id="testApi" class="pri">测试 API 连通</button>
        <span class="hint" id="testResult" style="align-self:center"></span>
      </div>
    </section>

    <section id="sec-check" class="card" data-sec>
      <h2>检测中心（代码检测 / 鼠标操作检测）</h2>
      <div class="desc">「代码检测」= 纯代码层检查（编译/依赖/角色卡评估/种子库/UI 标定/提示词静态/保护机制——不动鼠标、零风险，实测约 0.5~3 秒）；「鼠标操作检测」= 环境/配置/点击 + 程序鼠标操作检验（约 40~70 秒，期间接管鼠标请勿动；评论/收藏为真实操作）。想单独测某项用下方「🖱️ 程序鼠标检验」的独立按钮。</div>
      <div class="btns">
        <button id="codeCheck" class="pri">代码检测</button>
        <button id="codeCheckDeps" class="ghost" title="额外跑依赖版本详细核对（55 项，稍慢）">代码检测＋依赖核对</button>
        <button class="ghost" id="codeCheckTip2" title="点击切换到概览查看常驻状态条" onclick="document.getElementById('sec-overview').scrollIntoView({behavior:'smooth'})">查看进度条</button>
      </div>
      <div class="btns">
        <button id="selfCheck" class="pri">鼠标操作检测</button>
        <button id="selfCheckStop" class="ghost" disabled>停止检测</button>
        <span class="hint" id="selfCheckTip" style="align-self:center">进行中约 40~70 秒（含程序鼠标操作；可随时「停止检测」）</span>
      </div>
      <pre class="out dn" id="selfCheckResult"></pre>
      <hr style="border:none;border-top:1px solid var(--bd);margin:14px 0">
      <div class="row"><label>拍一拍目标群</label>
        <div class="grow"><select id="pokeGroup">
          <option value="">自动（最近有人发言的群）</option>
        </select></div>
      </div>
      <div class="row"><label>简易检测</label><input type="checkbox" id="pokeVerifyOnly" checked title="只验证右键头像能弹出「拍一拍」菜单，不点击、不拍任何人">
        <span class="hint">勾选=只验证菜单可弹（绝不到任何群友）；取消勾选=完整执行拍一拍（会真正拍一下）</span>
      </div>
      <div class="btns">
        <button id="pokeTest" class="pri">拍一拍检测</button>
        <span class="hint" id="uiTestResult" style="align-self:center"></span>
      </div>
      <div class="hint" style="color:var(--err-tx)">⚠️ 拍一拍是右键「对方头像」触发：头像由程序识别，若群内同名/头像辨识不清，理论上有拍到其他群友的风险——所以默认用「简易检测」，确认无误后再完整执行。</div>
      <div class="hint" id="uiTestDetail"></div>
      <hr style="border:none;border-top:1px solid var(--bd);margin:14px 0">
      <h2>🖱️ 程序鼠标检验（点按钮 → 程序直接操控微信鼠标执行；不耗 token、不靠模型）</h2>
      <div class="desc">检验的是「程序能否正确执行鼠标操作」——每项点一下，程序自动开窗/定位/点击/关窗并回显结果；请确保微信窗口在前台。</div>
      <input type="text" id="uiTestSearch" class="group-search" placeholder="🔍 搜索检验项…">
      <div id="uiTestBox" style="max-height:300px;overflow-y:auto;border:1px solid var(--bd);border-radius:10px;padding:8px 10px;background:var(--input-bg)">
        <!-- 检验项由 JS 渲染 -->
      </div>
      <hr style="border:none;border-top:1px solid var(--bd);margin:14px 0">
      <h2>功能自检清单（按重要性排序）</h2>
      <table id="checkList">
        <thead><tr><th style="width:26px">✓</th><th>项目</th><th>怎么测</th><th>预期</th></tr></thead>
        <tbody>
          <tr><td><input type="checkbox" class="ck"></td><td>1. 环境体检</td><td>点上方「鼠标操作检测」</td><td>无 ❌ 项（允许 ⚠️ 提示）</td></tr>
          <tr><td><input type="checkbox" class="ck"></td><td>2. 发消息</td><td>群里 @机器人 说句话</td><td>机器人正常回复，且不重复</td></tr>
          <tr><td><input type="checkbox" class="ck"></td><td>3. 拍一拍</td><td>先「简易检测」，再完整检测</td><td>简易=菜单可弹；完整=群里出现拍一拍提示</td></tr>
          <tr><td><input type="checkbox" class="ck"></td><td>4. 引用回复</td><td>让机器人 引用某条消息回复</td><td>出现引用样式（灰底卡片）且内容正确</td></tr>
          <tr><td><input type="checkbox" class="ck"></td><td>5. 发图</td><td>发一张带图消息，让机器人「发一张图」</td><td>群里出现机器人转发的图片</td></tr>
          <tr><td><input type="checkbox" class="ck"></td><td>6. 识图</td><td>引用图片 + @机器人 分析这张</td><td>机器人正确描述图片内容</td></tr>
          <tr><td><input type="checkbox" class="ck"></td><td>7. 联网搜索</td><td>@机器人 今天的天气/新闻</td><td>给出实时信息（联网层开启）</td></tr>
          <tr><td><input type="checkbox" class="ck"></td><td>8. 记忆</td><td>聊天里让机器人记住一件事 → 控制台「记忆」页看</td><td>印象出现、可删除</td></tr>
          <tr><td><input type="checkbox" class="ck"></td><td>9. 挂件</td><td>看右下角鲸鱼挂件（余额/今日已用/每轮消耗）</td><td>数据变化、点击刷新、可拖拽</td></tr>
          <tr><td><input type="checkbox" class="ck"></td><td>10. 启停重启</td><td>顶部 停止/重启（无窗口）→ 双击 启动机器人.vbs</td><td>页面变「已停止」、重启后台接管</td></tr>
          <tr><td><input type="checkbox" class="ck"></td><td>11. 多厂商切换</td><td>模型 API 切到 Kimi/智谱/ChatGPT/Claude/Gemini 等 → 保存 → 测试连通</td><td>默认弹 Key 输入，测试通过</td></tr>
        </tbody>
      </table>
      <div class="btns" style="margin-top:8px"><button id="ckReset" class="ghost">重置勾选</button><span class="hint" id="ckCount" style="align-self:center"></span></div>
    </section>

    <section id="sec-advanced" class="card" data-sec>
      <h2>调试 · 高级功能</h2>
      <div class="desc">一般用户不用、其他分区没覆盖的可调项（行为引擎完整参数 / UI 图标库 / 学习机制）。</div>

      <hr style="border:none;border-top:1px solid var(--bd);margin:14px 0">
      <div class="desc">🐋 人性化行为完整参数（一般用户不用；微信卡只有概率，这里调冷却/每日上限/开关）：</div>
      <div class="mid">
        <div class="row"><label>收藏表情-冷却(秒)</label><input type="number" min="0" data-cfg="behavior.collect_emoji.cooldown_s"></div>
        <div class="row"><label>收藏表情-每日上限</label><input type="number" min="0" data-cfg="behavior.collect_emoji.daily_limit"></div>
      </div>
      <div class="mid">
        <div class="row"><label>回发表情-冷却(秒)</label><input type="number" min="0" data-cfg="behavior.send_emoji.cooldown_s"></div>
        <div class="row"><label>回发表情-每日上限</label><input type="number" min="0" data-cfg="behavior.send_emoji.daily_limit"></div>
      </div>
      <div class="mid">
        <div class="row"><label>@群友-冷却(秒)</label><input type="number" min="0" data-cfg="behavior.at_member.cooldown_s"></div>
        <div class="row"><label>@群友-每日上限</label><input type="number" min="0" data-cfg="behavior.at_member.daily_limit"></div>
      </div>
      <div class="mid">
        <div class="row"><label>点赞-每日上限</label><input type="number" min="0" data-cfg="behavior.like_moments.daily_limit"></div>
        <div class="row"><label>点赞-冷却(秒)</label><input type="number" min="0" data-cfg="behavior.like_moments.cooldown_s"></div>
      </div>
      <div class="desc">📷 朋友圈（刷/点赞/评论/发布；默认全关=机器人不主动碰朋友圈，打开后按概率低频触发）：</div>
      <div class="mid">
        <div class="row"><label>刷朋友圈</label><input type="checkbox" data-cfg="behavior.moments_surf.enabled"><span class="hint">开启后按概率自动刷（截图给模型看更耗 token，频率请保守）</span></div>
        <div class="row"><label>刷-概率</label><input type="number" min="0" max="1" step="0.05" data-cfg="behavior.moments_surf.probability"></div>
      </div>
      <div class="mid">
        <div class="row"><label>刷-每日上限</label><input type="number" min="0" data-cfg="behavior.moments_surf.daily_limit"></div>
        <div class="row"><label>刷-冷却(秒)</label><input type="number" min="0" data-cfg="behavior.moments_surf.cooldown_s"></div>
      </div>
      <div class="mid">
        <div class="row"><label>点赞</label><input type="checkbox" data-cfg="behavior.like_moments.enabled"></div>
        <div class="row"><label>评论朋友圈</label><input type="checkbox" data-cfg="behavior.moments_comment.enabled"><span class="hint">默认关（评论是有感而发不该高频）</span></div>
      </div>
      <div class="mid">
        <div class="row"><label>评-概率</label><input type="number" min="0" max="1" step="0.05" data-cfg="behavior.moments_comment.probability"></div>
        <div class="row"><label>评-每日上限</label><input type="number" min="0" data-cfg="behavior.moments_comment.daily_limit"></div>
      </div>
      <div class="mid">
        <div class="row"><label>发朋友圈</label><input type="checkbox" data-cfg="behavior.moments_publish.enabled"><span class="hint">默认关（公开发布，慎重）</span></div>
        <div class="row"><label>发-概率</label><input type="number" min="0" max="1" step="0.05" data-cfg="behavior.moments_publish.probability"></div>
      </div>
      <div class="mid">
        <div class="row"><label>发-每日上限</label><input type="number" min="0" data-cfg="behavior.moments_publish.daily_limit"></div>
        <div class="row"><label>发-冷却(秒)</label><input type="number" min="0" data-cfg="behavior.moments_publish.cooldown_s"></div>
      </div>
      <div class="desc">🐋 微信 UI 图标库（一次性标定；自动检测侧栏图标序列，坐标按窗口尺寸换算）：</div>
      <div class="row"><label>当前布局</label><div class="grow">
        <span class="hint" id="uiLayoutStat">加载中…</span>
        <button id="uiLayoutReload" class="ghost" style="margin-left:8px">刷新</button>
        <button id="uiRecalibrate" class="pri" style="margin-left:8px">重新标定（接管鼠标）</button>
        <div class="hint">自动检测微信侧栏图标序列写入 data/ui_layout.json；请确保微信窗口在前台再点（会瞬间点击左栏）</div>
      </div></div>
      <div class="desc">🧠 语言风格训练（机器学习）</div>
      <div class="hint" style="margin-top:0">
        机制：机器人每次发言后，若群友在 24h 内热烈回应（@ 它 / 接话 / 追问）→ 该条话术加分；冷场 → 降权。热度半衰期 7 天，老梗自动衰减，防饱和。<br>
        <b>不变人原则</b>：学习只调整语言风格（机灵/更有人情味/更机敏），<b>绝不改变角色卡人设</b>——角色设定是绝对基准权重最高，参考素材只能"换衣服不能换魂"，你的角色卡是什么样，学得越久就越像那个人的语气。用户自定义角色卡同样适用。
      </div>
      <div style="display:flex;gap:12px;align-items:center;margin:14px 0 18px;padding:14px;border-radius:14px;background:rgba(63,168,240,.08);border:1px solid var(--blue-line)">
        <button id="learnApply" class="pri" title="点击开启机器学习，机制会真的开始工作（有群友回应时学习）" style="font-weight:700">🧠 确定学习</button>
        <button id="learnEval" class="ghost" title="模型按评分细则评估：学习前后对话质量变化，打分并说明提升多少" style="font-weight:700">📊 学习评估</button>
        <span id="learnRst" class="hint" style="flex:1"></span>
      </div>
      <div class="row"><label>种子库状态</label><div class="grow">
        <span class="hint" id="seedStats" style="display:inline-block">加载中…</span>
        <button id="seedReload" class="ghost" style="margin-left:8px">刷新</button>
        <span class="hint">趣味种子库（内置官方 212 条 + 你导入的金句，合计可在下方状态看到）；「社区与学习」页可导入金句墙种子。</span>
      </div></div>
    </section>

    <section id="sec-sessions" class="card" data-sec>
      <h2>运行明细</h2>
      <div class="desc">简明日志：发了什么、多少 token、耗时（服务端按天落盘，最近 30 轮）。每个日期记录可勾选删除（按日期删，不可恢复）。</div>
      <div class="btns">
        <button id="sessRefresh" class="pri">刷新</button>
        <label class="hint" style="align-self:center;cursor:pointer"><input type="checkbox" id="sessExpand"> 展开详情（推理/工具/触发）</label>
        <span class="hint" style="align-self:center">推理文本按输出价计费，控制台「省 token 开关」默认已关闭思考。</span>
      </div>
      <div class="btns">
        <button id="sessSelDel" class="danger" disabled>删除选中（勾选日期删除）</button>
        <button id="sessClear" class="danger" title="清空全部运行明细（会话日志/对话历史）——模型将不再记得这些对话">一键清全部</button>
        <span class="hint" style="align-self:center">勾选每条记录左侧「删」→「删除选中」；或直接「一键清全部」。</span>
      </div>
      <div id="sessBox" style="max-height:360px;overflow-y:auto;border:1px solid var(--bd);border-radius:10px;padding:10px 12px;margin-top:10px;background:var(--input-bg)">
        <div id="sessList" style="display:flex;flex-direction:column;gap:8px">
          <div class="hint" style="padding:14px;text-align:center;color:var(--tx2)">加载中…</div>
        </div>
      </div>
    </section>

    <section id="sec-model" class="card" data-sec>
      <h2>模型 API</h2>
      <div class="desc">密钥在控制台首次引导填入后自动保存，无需再改 config.json。</div>
      <div class="row"><label>Base URL</label><div class="grow"><input type="text" data-cfg="api.base_url"><span class="hint" style="margin-top:4px;display:block">可填官方地址，也可填「API 中转站」地址（如 https://api.中轉站.com/v1——常更便宜、能降低 token 花费；填中转站地址+对应 Token 即可，无需改其它设置）。</span></div></div>
      <div class="row"><label>API Key</label>
        <div class="grow">
          <input type="password" id="apiKeyInput" data-cfg="api.api_key" placeholder="sk-...">
          <div class="btns" style="margin-top:6px">
            <button id="keySave" class="pri">保存 Key</button>
            <button id="keyReset" class="ghost">重置 Key（重新填写）</button>
          </div>
          <div class="hint">点「保存 Key」立即生效（无需滚到底）；空 Key 会保留当前值。打码值只显示在页面上，真实密钥仅存服务器 config.json。</div>
        </div></div>
      <div class="row"><label>模型厂商</label>
        <div class="grow"><select id="providerSel">
          <option value="deepseek">DeepSeek（默认，见下方模型列表）</option>
          <option value="moonshot">Moonshot Kimi</option>
          <option value="zhipu">智谱 GLM</option>
          <option value="qwen">通义千问（阿里）</option>
          <option value="minimax">MiniMax</option>
          <option value="doubao">豆包（火山方舟）</option>
          <option value="openai">ChatGPT（OpenAI）</option>
          <option value="claude">Claude（Anthropic，OpenAI 兼容端点）</option>
          <option value="gemini">Gemini（Google）</option>
          <option value="grok">Grok（xAI）</option>
          <option value="nvidia">NVIDIA（Nemotron）</option>
          <option value="openrouter">OpenRouter（聚合）</option>
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
      <label class="think-card" id="thinkCard" title="模型返回的「推理文本」是生成的思考链式输出，并非真实内部思维；它按输出价计费，通常占一个会话 token 的 50~90%。">
        <input type="checkbox" data-cfg="api.thinking" id="thinkOffChk" checked>
        <div>
          <div class="tc-title">省 token：关闭模型思考<span class="tc-badge" id="thinkBadge">已开启省 token</span></div>
          <div class="tc-sub">勾选 = 关闭推理文本（api.thinking=off），单会话可省 50~90% token；群里闲聊/问答建议保持勾选。
          取消勾选 = 跟随模型默认（auto）或强制思考（on），回答更「深思熟虑」但费 token、更慢。</div>
        </div>
      </label>
      <div class="row"><label>温度</label><input type="range" id="api.temperature" min="0" max="1" step="0.05" data-cfg="api.temperature"><span class="val" id="api.temperature-v">0.8</span></div>
      <div class="row"><label>单次工具轮数</label><div class="grow"><input type="number" data-cfg="api.max_rounds" min="1" max="50"></div></div>
      <div class="row"><label>请求超时(ms)</label><div class="grow"><input type="number" data-cfg="api.timeout_ms" min="5000" step="1000"></div></div>
      <div class="mid">
        <div class="row"><label>输入单价/百万</label><input type="number" step="0.01" data-cfg="api.price_input_per_m"><span class="val">元</span></div>
        <div class="row"><label>输出单价/百万</label><input type="number" step="0.01" data-cfg="api.price_output_per_m"><span class="val">元</span></div>
        <div class="row"><label>缓存单价/百万</label><input type="number" step="0.01" data-cfg="api.price_cached_per_m"><span class="val">元</span></div>
      </div>
      <div class="row"><label>内置官方价</label><input type="checkbox" data-cfg="api.use_official_price"><span class="hint">上面填 0 时用内置官方单价表</span></div>
      <div class="row"><label>按型号单价(JSON)</label><div class="grow">
        <textarea data-cfg="api.model_prices" rows="3" spellcheck="false" placeholder='{"deepseek-v4-flash": {"in": 1.5, "out": 4.5, "cached": 0.05}}'></textarea>
        <div class="hint">优先级最高：按模型 id 覆盖内置价（元/百万 token）。示例见左。</div>
      </div></div>
      <div class="btns"><button class="pri" data-save>保存设置（模型 API）</button></div>
    </section>

    <section id="sec-wechat" class="card" data-sec>      <div class="row"><label>微信版本</label><div class="grow"><b id="wxver">检测中…</b></div></div>
      <h2>微信</h2>
      <div class="desc">机器人微信身份与轮询 / 白名单。改完保存后需要重启才能完全生效。</div>
      <div class="row"><label>机器人昵称</label><div class="grow"><input type="text" data-cfg="wechat.bot_nickname"></div></div>
      <div class="row"><label>自我称呼</label><div class="grow"><input type="text" data-cfg="persona.self_nickname" placeholder="留空=机器人昵称，用于识别「我」"></div></div>
      <div class="row"><label>启动后暂停</label><input type="checkbox" data-cfg="wechat.start_paused"><span class="hint">勾选：机器人启动后不自动监听，需点「恢复」才工作（防开机刷群/回应积压旧消息）</span></div>
      <div class="row"><label>轮询间隔(秒)</label><div class="grow"><input type="number" step="0.5" min="0.5" data-cfg="wechat.poll_interval"></div></div>
      <div class="row"><label>每分钟限发</label><div class="grow"><input type="number" min="1" data-cfg="wechat.rate_limit_per_minute"></div></div>
      <div class="row"><label>最小化提醒</label><input type="checkbox" data-cfg="wechat.minimize_warning"><span class="hint">勾选=提示别最小化微信窗口（发送依赖模拟键鼠）</span></div>
      <div class="row"><label>群白名单</label>
        <div class="grow">
          <div class="chips" id="wlChips"></div>
          <div class="btns" style="margin-top:0">
            <button id="pickGroups" class="ghost">检测群聊并勾选</button>
            <input id="customGroup" type="text" placeholder="自定义群名，回车添加" style="flex:1;background:var(--input-bg);border:1px solid var(--bd);border-radius:8px;padding:7px 10px;color:var(--tx)">
          </div>
          <div class="hint">留空=所有群都监听；勾选的群才响应（也可配合「暂停」）。</div>
        </div>
      </div>
      <div class="row"><label>媒体目录</label><div class="grow"><input type="text" data-cfg="wechat.media_dir"></div></div>
      <div class="row"><label>数据库目录</label><div class="grow"><input type="text" data-cfg="wechat.db_dir" placeholder="留空=自动探测微信数据目录"></div></div>
      <div class="btns"><button class="pri" data-save>保存设置（微信）</button></div>
      <hr style="border:none;border-top:1px solid var(--bd);margin:14px 0">
      <div class="desc">😊 表情包（模型-程序协作）：群里收到有趣的表情，机器人用 <code>collect_emoji</code> 收藏（生成极简概述入库，模型-程序协作），需要时 <code>send_emoji</code> 按概述/语境选一个再发出；也可在下面手动管理。</div>
      <div class="row"><label>收藏夹表情</label><div class="grow">
        <input type="text" id="emojiSearch" placeholder="🔍 搜索表情（按文件名）" style="margin-bottom:8px">
        <div id="emojiBox" style="display:flex;flex-wrap:wrap;gap:8px;align-items:flex-start;min-height:60px;max-height:240px;overflow-y:auto;border:1px dashed var(--bd);border-radius:10px;padding:10px">
          <span class="hint">加载中…</span>
        </div>
        <button id="emojiRefresh" class="ghost" style="margin-top:6px">刷新</button>
        <span class="hint" id="emojiCount"></span>
        <!-- 数据源：搜索只过滤显示，收藏夹太大时自动分页预览（最多显示 60 个 + 滚动） -->
      </div></div>
      <div class="row"><label>说明</label><div class="grow">
        <span class="hint">合并转发消息在聊天记录里显示为「[合并转发] …」，机器人可用 <code>view_merge_forward</code> 查看具体内容。</span>
      </div></div>
      <hr style="border:none;border-top:1px solid var(--bd);margin:14px 0">
      <div class="desc">🤖 人性化自主行为（省 token 规则引擎：纯本地概率+冷却+每日上限，不调模型；人设参与度/表情包等级会调节频率系数，自定义角色卡不影响——角色卡管"怎么说"，引擎管"做不做"）：</div>
      <div class="mid">
        <div class="row"><label>收藏表情概率</label><input type="number" min="0" max="1" step="0.05" data-cfg="behavior.collect_emoji.probability"></div>
        <div class="row"><label>回发表情概率</label><input type="number" min="0" max="1" step="0.05" data-cfg="behavior.send_emoji.probability"></div>
        <div class="row"><label>@群友概率</label><input type="number" min="0" max="1" step="0.05" data-cfg="behavior.at_member.probability"></div>
      </div>
      <div class="mid">
        <div class="row"><label>点赞朋友圈</label><input type="checkbox" data-cfg="behavior.like_moments.enabled">
          <span class="hint">实验性（需入口坐标校准 + 朋友圈窗口可见）</span></div>
        <div class="row"><label>点赞概率</label><input type="number" min="0" max="1" step="0.05" data-cfg="behavior.like_moments.probability"></div>
      </div>
    </section>

    <section id="sec-poke" class="card" data-sec>
      <h2>拍一拍（行为）</h2>
      <div class="desc">自动回拍 / 主动皮一下的频率与冷却。注意：拍一拍有误拍风险（同名/头像辨识不清），建议保持「简易检测」优先。</div>
      <div class="mid">
        <div class="row"><label>回拍概率%</label><input type="number" min="0" max="100" data-cfg="poke.reply_probability" title="别人拍你，回拍的概率，默认 90"></div>
        <div class="row"><label>回拍冷却(秒)</label><input type="number" min="0" data-cfg="poke.cooldown_seconds" title="同一人再次被拍后不再回拍的冷却，默认 1800"></div>
      </div>
      <div class="mid">
        <div class="row"><label>主动皮一下概率%</label><input type="number" min="0" max="100" data-cfg="poke.active_probability" title="空闲时主动拍群友的概率，默认 10"></div>
        <div class="row"><label>主动每日上限</label><input type="number" min="0" data-cfg="poke.active_daily_limit" title="每天最多主动拍几次，默认 3"></div>
      </div>
      <div class="btns"><button class="pri" data-save>保存设置（拍一拍）</button></div>
    </section>

    <section id="sec-memory" class="card" data-sec>
      <h2>记忆（群友印象）</h2>
      <div class="desc">每个群友的长期印象，机器人回复时会参考。点「保存设置」不影响此处；删除即从记忆中移除。</div>
      <div class="row"><label>选择群聊</label>
        <div class="grow">
          <input type="text" id="memSearch" class="group-search" placeholder="搜索群名，回车选中第一个匹配…">
          <select id="memChats"><option value="">（加载中…）</option></select>
          <button id="memRefresh" class="ghost" style="margin-top:6px">刷新</button>
        </div>
      </div>
      <div class="row"><label>关机总结印象</label><div class="grow">
        <input type="checkbox" data-cfg="memory.summarize_on_exit" checked title="每次关闭机器人时把本次对话总结成群友印象（只在那时调一次模型，平时绝不计费）">
        <span class="hint">每次关闭机器人时自动把本对话总结为群友印象（仅关机时调一次模型；平时不调，不耗 token）。</span>
      </div></div>
      <div class="row"><label>清除记忆</label><div class="grow">
        <div class="btns" style="justify-content:flex-start;gap:8px">
          <button id="memClearSel" class="danger" disabled>清除勾选的印象</button>
          <button id="memClearAll" class="danger">清除全部</button>
          <span class="hint" id="memClearRst"></span>
        </div>
        <div class="hint">① 成员印象=记忆页勾选清除/本按钮清除全部；②「清除全部」=印象+共享记忆全清；③ 会话日志/运行明细的删除在「运行明细」页。</div>
      </div></div>
      <div style="max-height:340px;overflow-y:auto;border:1px solid var(--bd);border-radius:10px">
        <table id="memTable" style="width:100%"><thead><tr><th style="width:26px"><input type="checkbox" id="memCheckAll" title="全选"></th><th>成员</th><th>印象数</th><th>更新时间</th><th></th></tr></thead><tbody></tbody></table>
      </div>
      <div class="hint" id="memEmpty">（无记忆数据）</div>
    </section>

    <section id="sec-persona" class="card" data-sec>
      <h2>人设与响应</h2>
      <div class="row"><label>人设名</label><div class="grow"><input type="text" data-cfg="persona.bot_name"></div></div>
      <div class="row"><label>人设选单</label><div class="grow">
        <div id="personaCats" style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:6px;align-items:center">
          <button id="pCatAdd" class="ghost" style="padding:2px 10px" title="新建分区或添加角色">＋ 新建/添加</button>
        </div>
        <div class="btns" style="justify-content:flex-start;gap:8px">
          <button id="pSort" class="ghost" title="点击：分数高→低；再点：低→高；再点回到高→低（WPS 式切换）" style="font-weight:700">↓ 按评估分数排序</button>
          <span class="hint" id="pSortHint">（点一下正序，再点一下倒序）</span>
          <button id="pSortOff" class="ghost">恢复默认顺序</button>
          <button id="pRestorePrev" class="ghost" title="撤销最近一次应用的人设（真实有效：恢复上一个人设名+文本）" style="color:var(--warn);border-color:var(--warn)">↩ 恢复上个人设</button>
        </div>
        <input type="text" id="personaSearch" class="group-search" placeholder="🔍 搜索人设（如 傲娇/毒舌/猫/程序员）…">
        <div id="personaList" style="max-height:320px;overflow-y:auto;border:1px solid var(--bd);border-radius:10px;padding:6px;background:var(--input-bg)">
          <div class="hint">加载中…</div>
        </div>
        <div class="hint">⭐ 星标=收藏置顶（始终显示在最上）；每张卡右下角 ⋯ =更多操作（为模型打星/编辑/移动/删除）。排序按模型评估分高→低（当前视图=全部或当前分区）。</div>
      </div></div>
      <div class="row"><label>参与度</label><div class="grow"><select data-cfg="persona.participation">
        <option value="low">安静型</option><option value="medium">普通群友</option><option value="high">活跃型</option></select></div></div>
      <div class="row"><label>自定义角色文本</label><div class="grow"><textarea data-cfg="persona.role_text" placeholder="留空=内置小鲸鱼角色卡；填了=完全替换。可参考 agent/persona.py"></textarea></div></div>
      <div class="row"><label>评分补足</label><div class="grow">
        <div class="btns" style="justify-content:flex-start;gap:8px">
          <button id="pScoreLLM" class="ghost">模型评分</button>
          <button id="pEnrich" class="ghost">模型补足</button>
          <label style="display:flex;align-items:center;gap:6px">补足轮数
            <select id="pRounds" style="width:64px"><option value="1">1 轮</option><option value="2">2 轮</option><option value="3">3 轮</option></select>
          </label>
          <label style="display:flex;align-items:center;gap:6px"><input type="checkbox" id="pUseLlm" checked>允许模型处理</label>
        </div>
        <span class="hint" id="pScoreRst"></span>
        <div class="hint">【评分细则】风格辨识25%/角色贴合30%/内在一致20%/表达自然15%/完整可用10%，每维 0~100.00 精确百分位；无口头禅→风格≤45；通用词口头禅→≤70；AI套话→表达≤65；客服口吻→贴合≤60；换角色都能用→≤50；示例占位→完整≤75；沉默类无扩展→≤70；缺说话规则→≤70；满分唯一条件=仅凭提示词+一次提醒即逐句贴合本人（否则一律<95，优秀 88~94.99）。</div>
        <div class="hint">「模型补足」按人设驱动（让说话更贴近本人，不是为分数调整）；每轮补足后自动重评：分数上升才继续下一轮，不升/降即停止；轮数可选（1~3 轮，每轮约 10~30 秒耗少量 token）；完成后点「保存」落盘。</div>
      </div></div>
      <div class="row"><label>角色卡行为推荐</label><div class="grow">
        <button id="roleHintBtn" class="ghost" type="button">根据角色卡推荐行为档</button>
        <span class="hint" id="roleHintRst"></span>
        <div class="hint" id="roleHintDetail" style="display:none">
          <label style="display:inline-flex;align-items:center;gap:4px;margin-right:10px">参与度
            <select id="roleHintPart"><option value="low">安静</option><option value="medium">普通</option><option value="high">活跃</option></select></label>
          <label style="display:inline-flex;align-items:center;gap:4px">表情包
            <select id="roleHintSticker"><option value="0">少</option><option value="1">偶尔</option><option value="2">较多</option><option value="3">爱好者</option></select></label>
          <button id="roleHintApply" class="pri" type="button">应用</button>
        </div>
        <div class="hint">建议来自角色卡文本关键词（本地零 token）；应用后保存即生效。</div>
      </div></div>
      <div class="row"><label>额外规则</label><div class="grow"><textarea data-cfg="persona.custom_rules" placeholder="如：回复永远不超过 5 个字"></textarea></div></div>
      <hr style="border:none;border-top:1px solid var(--bd);margin:12px 0">
      <div class="row"><label>响应档位</label><div class="grow"><select data-cfg="store.context_tier" id="ctxTier">
        <option value="1">1 档：仅艾特</option><option value="2">2 档：+关键词</option>
        <option value="3">3 档：+随机</option><option value="4">4 档：全响应</option></select>
        <div class="hint">1 档只回艾特；2 档加关键词；3 档再加随机；4 档全回。关键词在 2/3 档生效，随机只在 3 档生效。</div>
      </div></div>
      <div class="row" data-tier="2,3"><label>关键词(逗号)</label><div class="grow"><input type="text" data-cfg="store.keywords" placeholder="2/3档命中即响应"></div></div>
      <div class="row" data-tier="3"><label>随机概率%</label><div class="grow"><input type="number" min="0" max="100" data-cfg="store.random_percent"></div></div>
      <div class="mid">
        <div class="row"><label>艾特上下文条数</label><input type="number" min="1" data-cfg="store.at_count"></div>
        <div class="row" data-tier="2,3"><label>关键词上下文</label><input type="number" min="1" data-cfg="store.keyword_count"></div>
        <div class="row" data-tier="3"><label>随机上下文</label><input type="number" min="1" data-cfg="store.random_count"></div>
      </div>
      <div class="row"><label>单档上下文上限</label><div class="grow"><input type="number" min="1" data-cfg="store.all_count"></div></div>
      <div class="row"><label>历史窗口(分钟)</label><div class="grow"><input type="number" min="0" data-cfg="store.past_window_min" title="0=不限"> <span class="hint">只把最近 N 分钟内的消息给模型当历史，防它回应很久之前的艾特/旧话题</span></div></div>
      <div class="row"><label>每群消息上限</label><div class="grow"><input type="number" min="0" data-cfg="store.max_messages_per_chat" title="0=不限制"></div></div>
      <hr style="border:none;border-top:1px solid var(--bd);margin:12px 0">
      <div class="row"><label>每群独立档位</label><input type="checkbox" data-cfg="store.unified_tier" id="unifiedTierChk" checked><span class="hint">取消勾选后，可在下方按群单独设置响应档位（未设置的群跟随全局）</span></div>
      <div id="groupTierBox"><div class="hint">勾选"每群独立档位"后，这里按群显示档位下拉并保存到 store.group_tier。</div></div>
      <div class="row"><label>屏蔽名单(按群)</label><div class="grow">
        <textarea id="blocklistBox" data-cfg="store.group_blocklist" rows="3" placeholder='{"群名": ["昵称或wxid", ...]}'></textarea>
        <div class="hint">JSON 格式：{群名: [要屏蔽的昵称/wxid…]}。被屏蔽者消息不存档、不触发、不进提示词。</div>
      </div></div>
      <div class="row"><label>表情包积极度</label><div class="grow"><select data-cfg="store.sticker_level">
        <option value="0">0：不鼓励</option><option value="1">1：偶尔</option>
        <option value="2">2：较积极</option><option value="3">3：表情包爱好者</option></select>
        <div class="hint">提示词层面引导，不强制。</div>
      </div></div>
      <hr style="border:none;border-top:1px solid var(--bd);margin:12px 0">
      <div class="row"><label>主动开话题</label><input type="checkbox" data-cfg="proactive.enabled">
        <span class="hint">群冷场超过阈值后，按概率主动抛一个话题（默认关；费少量 token）</span></div>
      <div class="mid" id="proactiveRows">
        <div class="row"><label>冷场阈值(毫秒)</label><input type="number" min="1" data-cfg="proactive.idle_threshold_ms" title="毫秒；默认 1800000（30 分钟）"></div>
        <div class="row"><label>检查间隔(毫秒)</label><input type="number" min="1" data-cfg="proactive.check_interval_min_ms" title="毫秒；默认 1800000（30 分钟）"></div>
        <div class="row"><label>检查上限(毫秒)</label><input type="number" min="1" data-cfg="proactive.check_interval_max_ms" title="毫秒；默认 5400000（90 分钟）"></div>
        <div class="row"><label>触发概率(小数)</label><input type="number" min="0" max="1" step="0.05" data-cfg="proactive.probability" title="0~1；默认 0.25（25%）"></div>
      </div>
      <div class="btns"><button class="pri" data-save>保存设置（人设与响应）</button></div>
    </section>

    <section id="sec-community" class="card" data-sec>
      <h2>社区与学习</h2>
      <div class="desc">金句/意见/聊天记录本地导出；可选上传到自配服务器；反应评分引擎让机器人越聊越有趣（防饱和）。</div>
      <div class="row"><label>评分引擎</label><input type="checkbox" data-cfg="scoring.enabled" checked><span class="hint">本地正反馈评分（零 token）；群友回应热烈→高效反应进入提示词参考</span></div>
      <div class="row"><label>种子库</label><input type="checkbox" data-cfg="scoring.seed_library" checked><span class="hint">内置有趣开场/接梗 small-sample 参考</span></div>
      <div class="row"><label>在线评分</label><input type="checkbox" data-cfg="scoring.online_scoring"><span class="hint">每次 reaction 后调 LLM 打分（费 token，默认关）</span></div>
      <div class="row"><label>热度衰减</label><input type="checkbox" data-cfg="scoring.heat_decay" checked><span class="hint">老梗降权，防饱和</span></div>
      <div class="row"><label>导入金句种子</label><div class="grow"><textarea id="seedImport" rows="2" placeholder="粘贴金句墙导出的文本，每行一条…"></textarea>
        <div class="row"><label>自定义金句(选单)</label><div class="grow"><input type="text" id="seedCustomTxt" placeholder="输入一句你的自定义金句，点「添加」进库（学习/接梗参考）" style="flex:1"><button id="seedCustomAdd" class="ghost">添加</button><span id="seedCustomRst" class="hint"></span></div></div>
        <div class="btns"><button id="seedImportBtn" class="ghost">导入种子库</button><button id="seedImportFile" class="ghost">选择文件导入</button><input type="file" id="seedFile" accept=".txt,.json,text/plain,application/json" style="display:none"><span class="hint" id="seedImportRst"></span></div>
        <div class="hint">粘贴导入（每行一条）；或「选择文件导入」读 txt/json 文件——导入自动查重（精确+72% 相似度）后写入并立即生效。</div>
      </div></div>
      <div class="row"><label>导出目录</label><div class="grow"><input type="text" data-cfg="community.export_dir" placeholder="exports">
        <div class="hint">金句/意见/聊天记录导出到项目根下该目录（相对路径）。</div></div></div>
      <div class="row"><label>导出</label><div class="grow">
        <div class="btns">
          <button id="exportHolyshits" class="ghost">导出金句</button>
          <button id="exportFeedback" class="ghost">导出意见反馈</button>
          <button id="exportMessages" class="ghost">导出聊天记录</button>
          <button id="openExportDir" class="ghost">打开导出文件夹</button>
        </div>
        <div class="hint" id="exportRst">导出为本地文件（community.export_dir）；「打开导出文件夹」直接用资源管理器定位。</div>
      </div></div>
      <div class="row"><label>社区上传</label><input type="checkbox" data-cfg="community.upload_enabled"><span class="hint">开启后金句/意见可 POST 到下方 URL（需自配服务器）</span></div>
      <div class="row"><label>金句上传 URL</label><div class="grow"><input type="text" data-cfg="community.holyshits_upload_url" placeholder="留空=仅本地导出"></div></div>
      <div class="row"><label>意见反馈上传 URL</label><div class="grow"><input type="text" data-cfg="community.feedback_upload_url" placeholder="留空=仅本地导出"></div></div>
      <div class="row"><label>上传动作</label><div class="grow">
        <div class="btns" style="justify-content:flex-start;gap:8px">
          <button id="openSeedBtn" class="ghost">打开种子库</button>
          <button id="uploadSeeds" class="ghost" disabled>确认上传金句</button>
          <button id="uploadFeedback" class="ghost" disabled>确认上传意见</button>
          <span class="hint" id="uploadRst">默认关闭（需勾选「社区上传」+填对应 URL）；确认后上传到你的服务器。</span>
        </div>
      </div></div>
      <div class="row"><label>意见上传 URL</label><div class="grow"><input type="text" data-cfg="community.feedback_upload_url" placeholder="留空=仅本地导出"></div></div>
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
      <div class="mid" style="margin-top:10px">
        <div class="row"><label>新对话自动引用</label><input type="checkbox" data-cfg="send.quote_on_new_talk" title="新一轮对话开始时自动引用对方最近一句话"></div>
        <div class="row"><label>引用概率(0~1)</label><input type="number" min="0" max="1" step="0.05" data-cfg="send.quote_reply_probability"></div>
        <div class="row"><label>对话冷却(秒)</label><input type="number" min="0" step="30" data-cfg="send.quote_new_talk_gap_s" title="机器人上条消息超过该秒数才算「新一轮对话」"></div>
      </div>
      <div class="hint" style="margin-top:8px">引用规则：机器人上一条消息超过「对话冷却」秒（对话已冷场）时，以「引用概率」（默认 70%）自动引用对方最近的一句话，让"新开头"更像真人接话；模型显式指定引用时以模型为准。</div>
      <div class="row"><label>UIA 直写输入</label><input type="checkbox" data-cfg="send.uia_setvalue" checked>
        <span class="hint">勾选=用 UIA SetValue 后台直写输入框（不点输入框/不粘贴）；不勾=点输入框+粘贴（兼容部分微信版本）</span></div>
      <div class="btns"><button class="pri" data-save>保存设置（发送限制）</button></div>
    </section>

    <section id="sec-memory-set" class="card" data-sec>
      <h2>记忆（共享设置）</h2>
      <div class="row"><label>自动整理</label><input type="checkbox" data-cfg="memory.consolidate_enabled"></div>
      <div class="row"><label>共享记忆池</label><input type="checkbox" data-cfg="memory.share_across_groups" checked id="memShareChk">
        <span class="hint">勾选=所有群共享一个记忆池（群间互通）；不勾=每群独立（默认，群间互不串味）</span></div>
      <div class="row" id="memGroupsRow"><label>共享群（可选）</label><div class="grow">
        <div id="memGroupsBox" style="display:flex;flex-wrap:wrap;gap:6px"><span class="hint">加载中…</span></div>
        <div class="hint">勾选几个群 → 只有这些群间共享记忆（比全共享更精准；不勾=用上方总开关）</div>
      </div></div>
      <div class="row"><label>整理间隔(小时)</label><div class="grow"><input type="number" min="1" data-cfg="memory.consolidate_min_interval_ms"></div></div>
      <div class="mid">
        <div class="row"><label>最少印象数</label><input type="number" min="1" data-cfg="memory.consolidate_min_impressions"></div>
        <div class="row"><label>每成员印象上限</label><input type="number" min="1" data-cfg="memory.max_impressions_per_member"></div>
        <div class="row"><label>发现最少消息</label><input type="number" min="1" data-cfg="memory.discover_min_messages"></div>
        <div class="row"><label>发现最多成员</label><input type="number" min="1" data-cfg="memory.discover_max_members"></div>
      </div>
      <div class="btns"><button class="pri" data-save>保存设置（记忆共享）</button></div>
    </section>

    <section id="sec-search" class="card" data-sec>
      <h2>联网搜索</h2>
      <div class="row"><label>启用</label><input type="checkbox" data-cfg="web_search.enabled"></div>
      <div class="row"><label>引擎</label><div class="grow"><select data-cfg="web_search.provider" id="wsProvider">
        <option value="bing">Bing（免key）</option><option value="deepseek">DeepSeek</option>
        <option value="zhipu">智谱</option><option value="bocha">博查</option>
        <option value="baidu">百度千帆</option><option value="metaso">秘塔</option><option value="custom">自定义</option></select>
        <div class="hint" id="wsHint">Bing 免 Key；其余引擎填「引擎 Key」与「Base URL」（留空=官方默认；DeepSeek 另有模型、智谱另有 engine）。</div></div></div>
      <div class="row"><label>结果数</label><div class="grow"><input type="number" min="1" max="20" data-cfg="web_search.max_results"></div></div>
      <hr style="border:none;border-top:1px solid var(--bd);margin:12px 0">
      <div class="desc">当前引擎参数（切换引擎自动带出对应小节，保存真实落盘 web_search.&lt;provider&gt;）：</div>
      <div class="row"><label>引擎 Key</label><div class="grow"><input type="password" id="wsKey" placeholder="贴该引擎的 API Key" autocomplete="off"></div></div>
      <div class="row"><label>Base URL</label><div class="grow"><input type="text" id="wsUrl" placeholder="留空=官方默认"></div></div>
      <div class="row" data-ws="deepseek"><label>模型</label><div class="grow"><input type="text" id="wsModel" placeholder="deepseek-chat"></div></div>
      <div class="row" data-ws="zhipu"><label>engine</label><div class="grow"><input type="text" id="wsEngine" placeholder="search_std"></div></div>
      <div class="row"><label>请求数</label><div class="grow"><input type="number" id="wsCount" min="1" max="50"></div></div>
      <div class="btns"><button class="pri" data-save>保存设置（联网搜索）</button></div>
    </section>

    <section id="sec-server" class="card" data-sec>
      <h2>服务器</h2>
      <div class="row"><label>监听地址</label><div class="grow"><input type="text" data-cfg="server.host" title="默认只允许本机访问"></div></div>
      <div class="row"><label>端口</label><div class="grow"><input type="number" min="1" max="65535" data-cfg="server.port"></div></div>
      <div class="row"><label>自动开浏览器</label><input type="checkbox" data-cfg="server.auto_open_browser"></div>
      <div class="row"><label>访问口令</label><div class="grow"><input type="text" data-cfg="server.token" placeholder="留空=启动时自动生成"></div></div>
      <div class="row"><label>统计周期</label><div class="grow"><select data-cfg="stats.period">
        <option value="daily">每日（每天 0 点重置）</option>
        <option value="weekly">每周（默认，周一重置）</option>
        <option value="monthly">每月（1 号重置）</option></select>
        <div class="hint">概览卡的「今日/本周/本月」用量卡按此周期归零重计（历史保留 24 期）。</div></div></div>
      <div class="btns"><button class="pri" data-save>保存设置（服务器）</button></div>
    </section>

    <section id="sec-ui" class="card" data-sec>
      <h2>界面适配（DPI / 遮挡 / 主题）</h2>
      <div class="row"><label>显示缩放</label><div class="grow"><select data-cfg="ui.coord_scale">
        <option value="auto">按系统自动检测</option><option value="1.0">100%</option>
        <option value="1.25">125%</option><option value="1.5">150%</option>
        <option value="1.75">175%</option><option value="2.0">200%</option></select></div></div>
      <div class="row"><label>自定义背景</label><div class="grow">
        <div class="btns" style="justify-content:flex-start;gap:8px">
          <button id="bgUpload" class="ghost">上传背景图</button>
          <button id="bgClear" class="ghost">恢复默认</button>
          <span class="hint" id="bgRst"></span>
        </div>
        <input type="file" id="bgFile" accept="image/*" style="display:none">
        <div class="hint">选一张图片（≤1920px，自动压缩）：深蓝海/星河等任意图；点「恢复默认」回到内置背景。</div>
      </div></div>
      <div class="row"><label>控制台主题</label><div class="grow"><select data-cfg="ui.theme">
        <option value="whale">🐋 鲸落（默认：深海蓝渐变）</option>
        <option value="light">浅色</option>
        <option value="dark">深色</option>
        <option value="system">跟随系统自动</option></select></div></div>
      <div class="row"><label>界面文案风格</label><div class="grow"><select data-cfg="ui.text_style">
        <option value="normal">正常</option>
        <option value="whale">🐋 鲸语</option>
      </select><span class="hint">切换后保存设置（自动刷新）即生效；功能完全一致。</span></div></div>
      <div class="row"><label>点击前清遮挡</label><input type="checkbox" data-cfg="ui.clean_overlays"></div>
      <div class="row"><label>地址栏乱码化</label><input type="checkbox" data-cfg="ui.obscure_url">
        <span class="hint">开启后：进入页面把地址栏路径替换成随机乱码（保护访问地址不被他人复制直接登入；刷新靠会话 Cookie）。端口号无法乱码（浏览器必须用真实端口连接）。默认关。</span></div>
      <div class="btns"><button class="pri" data-save>保存设置（界面适配）</button></div>
    </section>

    <section id="sec-cursor" class="card" data-sec>
      <h2>🐋 光标设置</h2>
      <div class="desc">把鼠标指针换成鲸鱼（或你自己的图片），点击时向下点头；默认鲸鱼小蓝鲸（22）。</div>
      <div class="row"><label>启用鲸鱼光标</label><input type="checkbox" data-cfg="ui.whale_cursor"></div>
      <div class="row"><label>光标图片</label><div class="grow">
        <input type="file" id="cursorFile" accept="image/png,image/jpeg" style="padding:6px">
        <span class="hint">选一张 PNG/JPEG（建议透明底、方形，≤8MB），上传后立即预览效果</span>
      </div></div>
      <div class="row"><label>当前预览</label><div class="grow">
        <canvas id="cursorPreview" width="120" height="120" style="border:1px solid var(--bd);border-radius:12px;background:linear-gradient(135deg,var(--blue-soft),var(--hover-bg));cursor:pointer" title="你的光标（点击可试点头）"></canvas>
        <span class="hint"><button id="cursorReset" class="ghost" style="margin-left:8px">重置为默认鲸鱼</button></span>
      </div></div>
      <hr style="border:none;border-top:1px solid var(--bd);margin:14px 0">
      <div class="desc">🐋 拖拽返回动画速度（拖动顶栏鲸鱼后，松手返回的速度倍率；1=标准，越少越快，0.3~3 可调）。</div>
      <div class="mid">
        <div class="row"><label>蠕动速度系数</label><input type="number" min="0.3" max="3" step="0.1" data-cfg="ui.whale_anim.worm">
          <span class="hint">最慢的方式（蠕动/摆尾）</span></div>
        <div class="row"><label>纸飞机速度系数</label><input type="number" min="0.3" max="3" step="0.1" data-cfg="ui.whale_anim.plane">
          <span class="hint">较快（变形滑翔）</span></div>
        <div class="row"><label>扎入速度系数</label><input type="number" min="0.3" max="3" step="0.1" data-cfg="ui.whale_anim.zap">
          <span class="hint">距离自适应（含 0.5s 消失）</span></div>
      </div>
      <div class="hint">时长公式（dist=拖拽距离 px）：蠕动 260×dist/100×系数④（600~2400ms）；纸飞机 170×dist/100×系数+0.58s（变形/翻回）；扎入 90×dist/100×系数+0.78s（含 0.5s 消失+冒出）。可在控制台 Console 看每次返回的日志（如 [whale-return]）。</div>
      <div class="btns"><button class="pri" id="cursorSaveBtn">保存光标设置</button></div>
    </section>

    <section id="sec-wavefx" class="card" data-sec>
      <h2>🌊 水光波纹（鼠标投石入水）</h2>
      <div class="desc">鼠标像石子投入湖面：一道波纹从鼠标处肉眼可见地一波波荡开，扩散范围=光标所在的整个模块（顶栏/导航栏/功能卡），到模块边缘极强衰减、绝不越界；拖动越快荡得越快。所有参数即时生效。</div>
      <div class="row"><label>启用水光波纹</label><input type="checkbox" data-cfg="ui.wave_fx.enabled"><span class="hint">关闭后完全无扭曲</span></div>
      <div class="row"><label>扭曲强度</label><input type="number" min="0" max="40" step="1" data-cfg="ui.wave_fx.scale"><span class="hint">核心位移量（0=无扭曲；建议 8~24）</span></div>
      <div class="row"><label>基础波速</label><input type="number" min="1" max="20" step="0.2" data-cfg="ui.wave_fx.speed"><span class="hint">波前内部的频闪速度（越大越急促）</span></div>
      <div class="row"><label>鼠标提速</label><input type="number" min="0" max="0.1" step="0.005" data-cfg="ui.wave_fx.mouse_gain"><span class="hint">拖动越快波光越快的增益（0=不联动）</span></div>
      <div class="row"><label>提速上限</label><input type="number" min="0" max="20" step="0.5" data-cfg="ui.wave_fx.max_gain"><span class="hint">鼠标带动额外速度上限 rad/s</span></div>
      <div class="row"><label>扩散圈大小</label><input type="number" min="100" max="600" step="10" data-cfg="ui.wave_fx.radius"><span class="hint">空白处回退用（模块内仍以模块为界）</span></div>
      <div class="row"><label>衰减强度</label><input type="number" min="1" max="8" step="0.5" data-cfg="ui.wave_fx.falloff"><span class="hint">扩散衰减指数，越大越强（边缘几乎无影响；推荐 4+）</span></div>
      <div class="row"><label>可见波纹环数</label><input type="number" min="1" max="3" step="1" data-cfg="ui.wave_fx.rings"><span class="hint">1=单环一波接一波（时间差最清晰）</span></div>
      <div class="row"><label>波纹荡开速度</label><input type="number" min="0.1" max="1.5" step="0.05" data-cfg="ui.wave_fx.ring_speed"><span class="hint">一圈≈1/速度 秒（0.4≈2.5s 一波，肉眼可见）</span></div>
      <div class="btns"><button class="pri" id="wavefxApply" style="background:linear-gradient(135deg,#30B0C8,#0E8FB0)">应用水光波纹设置</button></div>
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
      <textarea id="rawjson" spellcheck="false" style="width:100%;min-height:260px;font-family:ui-monospace,Consolas,monospace;font-size:12.5px;background:var(--input-bg);border:1px solid var(--bd);border-radius:8px;padding:10px;color:var(--tx)"></textarea>
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
/* 弹窗/向导用鲸鱼徽章（绿底 + 蓝鲸线稿）；自动「果冻弹跳」动画（大幅压扁回弹） */
const ICON = '<div class="whale-badge big" style="margin:0 auto 12px"><img src="/assets/icon-whale.png" alt="whale"></div>';
const _iconCss = '.whale-badge.big{width:72px;height:72px;border-radius:18px}' +
  '.whale-badge.big img{left:10px;bottom:8px;width:52px;height:52px}' +
  '@keyframes whaleJelly{0%{transform:scale(1)}18%{transform:scaleX(1.25) scaleY(.8)}38%{transform:scaleX(.82) scaleY(1.16)}' +
  '58%{transform:scaleX(1.12) scaleY(.88)}76%{transform:scaleX(.94) scaleY(1.06)}92%{transform:scaleX(1.02) scaleY(.99)}100%{transform:scale(1)}}' +
  '.whale-badge.big{animation:whaleJelly 1.15s cubic-bezier(.34,1.4,.64,1) .15s 2 both}' +
  '.whale-badge.big:active{transform:scale(.9) rotate(4deg)}';
document.addEventListener('DOMContentLoaded', ()=>{
  const st = document.createElement('style'); st.textContent = _iconCss; document.head.appendChild(st);
});

async function getJSON(url, opts){
  opts = opts || {};
  opts.headers = opts.headers || {};
  if(URL_TOKEN) opts.headers['Authorization'] = 'Bearer ' + URL_TOKEN;
  // 超时保护：服务端卡死/旧进程无路由时 30s 内必须返回（避免"点了没反应"）
  const ctrl = new AbortController();
  const tmr = setTimeout(()=>ctrl.abort(), opts.timeoutMs || 30000);
  opts.signal = ctrl.signal;
  try{
    const r = await fetch(url, opts);
    if(!r.ok) throw new Error((await r.text())||r.status);
    return r.json();
  }finally{
    clearTimeout(tmr);
  }
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
    if(isCheck){
      if(path==='api.thinking'){ el.checked = (String(v||'').toLowerCase()==='off'); }
      else { el.checked = !!v; }
      return;
    }
    if(v==null) v = '';
    if(Array.isArray(v)) v = v.join('，'); // 关键词等多值用全角逗号回显（与输入一致）
    if(typeof v === 'object' && !Array.isArray(v)) v = JSON.stringify(v, null, 1); // model_prices 等返回 JSON 文本
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
  /* 省 token 卡片视觉联动 */
  const tb = $('thinkOffChk');
  if(tb){
    tb.addEventListener('change', ()=>{
      const on = tb.checked;
      $('thinkCard').classList.toggle('on', on);
      $('thinkBadge').textContent = on ? '已开启省 token' : '已关闭（模型自由思考）';
    });
    const on0 = tb.checked;
    $('thinkCard').classList.toggle('on', on0);
    $('thinkBadge').textContent = on0 ? '已开启省 token' : '已关闭（模型自由思考）';
  }
  /* 自绘下拉（厂商/模型/etc）：程序赋值后同步按钮文字（不触发业务 change） */
  document.querySelectorAll('select').forEach(s=>{ if(s._refresh) s._refresh(); });
  updateTierRows();
  if(typeof renderGroupTierBox === 'function') renderGroupTierBox();
  if(typeof wsSyncToForm === 'function') wsSyncToForm();
  if(typeof loadMemGroups === 'function') loadMemGroups();
}

/* 档位联动：关键词(2/3档)与随机(3档)只在对应档位选中时显示 */
function updateTierRows(){
  const sel = document.querySelector('[data-cfg="store.context_tier"]');
  if(!sel) return;
  const tier = parseInt(sel.value || '1', 10);
  document.querySelectorAll('[data-tier]').forEach(el=>{
    const show = String(el.dataset.tier).split(',').map(Number).includes(tier);
    el.style.display = show ? '' : 'none';
  });
}

function syncFromForm(){
  document.querySelectorAll('[data-cfg]').forEach(el=>{
    const path = el.dataset.cfg;
    if(path === 'wechat.group_name_white_list'){ setPath(cfg, path, wlList.slice()); return; }
    let v;
    if(el.type==='checkbox'){
      if(path==='api.thinking') v = el.checked ? 'off' : 'auto';  // 勾选=off，取消=auto（跟随模型默认）
      else v = el.checked;
    }
    else if(el.type==='number') v = parseFloat(el.value);
    else {
      v = el.value;
      if(path === 'store.keywords') v = v.split(/[,，]/).map(s=>s.trim()).filter(Boolean);
      else if(path === 'api.model_prices'){        // JSON 文本 → dict（非法 JSON 时给空对象，前台提示）
        try{ v = v.trim() ? JSON.parse(v) : {}; }
        catch(e){ v = {}; toast('按型号单价 JSON 格式有误，已忽略；示例：{"模型id": {"in":1.5,"out":4.5}}'); }
      }
      else if(path === 'store.group_blocklist'){   // JSON 文本 → dict
        try{ v = v.trim() ? JSON.parse(v) : {}; }
        catch(e){ v = {}; toast('屏蔽名单 JSON 格式有误，已忽略；示例：{"群名":["昵称"]}'); }
      }
      // 空字符串不覆盖已有值（防"保存全部设置"把用户没填的文本框冲成空）
      else if(v !== undefined && String(v).trim() === '' && getPath(cfg,path) !== undefined
              && getPath(cfg,path) !== null && getPath(cfg,path) !== ''){
        // 仍保留表单描述字段等非关键文本的可清空性：仅当原有值非空时跳过覆盖
        return;
      }
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
$('keySave').onclick = async ()=>{
  try{
    // 只保存 Key（不覆盖其它字段），并与当前厂商关联
    const k = ($('apiKeyInput')||{}).value || '';
    if(!k || k.includes('••••') || k.startsWith('sk-***')){
      toast('Key 为空或仍是打码值，未保存'); return;
    }
    const prov = $('providerSel').value;
    if(!cfg) return;
    setPath(cfg,'api.api_key', k);
    if(prov && prov!=='custom'){
      if(!cfg.api.provider_keys) cfg.api.provider_keys = {};
      cfg.api.provider_keys[prov] = k;
    }
    await getJSON('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(cfg)});
    toast('✅ API Key 已保存（'+prov+'）');
    cfg = await getJSON('/api/config'); syncToForm();
    // 保存后立即测试连通（可选，让用户看到能不能跑）
    try{
      const t = await getJSON('/api/test-api',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
      toast(t.ok ? ('✅ Key 已保存，测试连通成功（'+t.latency_ms+'ms）') : ('Key 已保存，但测试失败：'+(t.error||'')));
    }catch(e){ /* 测试失败不打断保存成功提示 */ }
  }catch(e){ toast('保存失败：'+e.message); }
};
/* 通用群列表渲染：搜索框 + 滚动槽。groups=[{name,wxid}]；pick=Set(选中名)；
   onPick(selectedSet) 每次勾选变化回调；返回时容器已含搜索框。 */
function renderGroupList(box, groups, pick, onPick){
  box.innerHTML='';
  box.className='group-box';
  const search=document.createElement('input'); search.type='text';
  search.className='group-search'; search.placeholder='搜索群名…';
  box.appendChild(search);
  const list=document.createElement('div'); list.className='pick';
  box.appendChild(list);
  function draw(filter){
    list.innerHTML='';
    const kw=(filter||'').trim().toLowerCase();
    let shown=0;
    groups.forEach(g=>{
      if(kw && !String(g.name||'').toLowerCase().includes(kw)) return;
      shown++;
      const lab=document.createElement('label'); lab.className='opt';
      const inp=document.createElement('input'); inp.type='checkbox'; inp.checked=pick.has(g.name);
      lab.appendChild(inp);
      const b=document.createElement('b'); b.textContent=g.name; lab.appendChild(b);
      const h=document.createElement('span'); h.className='hint'; h.style.marginLeft='8px'; h.textContent=g.wxid; lab.appendChild(h);
      inp.onchange=()=>{ if(inp.checked) pick.add(g.name); else pick.delete(g.name); onPick(pick); };
      list.appendChild(lab);
    });
    if(!shown){
      const d=document.createElement('div'); d.className='hint'; d.style.padding='12px';
      d.textContent=kw?('没有名字包含「'+kw+'」的群'):'没有检测到群聊——请确认微信已登录，重启机器人后再试。';
      list.appendChild(d);
    }
  }
  search.addEventListener('input', ()=>draw(search.value));
  draw('');
  return {search, list, draw};
}
$('pickGroups').onclick = async ()=>{
  try{
    const r = await getJSON('/api/wechat-groups');
    const groups = r.groups||[];
    const m=document.createElement('div'); m.className='mask';
    m.innerHTML='<div class="box" style="text-align:left"><h1>选择监听的群</h1><p>检测到 '+groups.length+' 个群聊，勾选机器人需要监听的群（全不勾=监听所有群）。</p><div id="groupPick"></div><div class="btns" style="justify-content:flex-end;margin-top:10px"><button class="pri" id="gpOk">确定</button><button class="ghost" id="gpCancel">取消</button></div></div>';
    document.body.appendChild(m);
    const box=$('groupPick');
    const pick = new Set(wlList);
    renderGroupList(box, groups, pick, ()=>{});
    $('gpOk').onclick=()=>{ wlList=[...pick]; renderChips(); maskClose(m); m.remove(); };
    $('gpCancel').onclick=()=>{ maskClose(m); m.remove(); };
  }catch(e){ toast('检测失败：'+e.message); }
};

/* ── 主题：whale（默认鲸落）/ light / dark / system ── */
function applyTheme(t){
  // 默认 whale：出厂视觉；显式选 system 才跟随系统
  const theme = (['light','dark','whale','system'].includes(t) ? t : 'whale');
  document.documentElement.setAttribute('data-theme', theme==='system' ? '' : theme);
  document.body.classList.toggle('whale-anim', theme==='whale');
}
function bindThemeSelect(){
  const sel = document.querySelector('[data-cfg="ui.theme"]');
  if(!sel || sel._bound) return;
  sel._bound = true;
  sel.addEventListener('change', ()=>{ applyTheme(sel.value); toast('主题已切换（保存设置后重启仍生效）'); });
}
function syncThemeFromCfg(){
  bindThemeSelect();
  // URL ?theme=light|dark|whale 可临时覆盖（用于预览/固定主题，URL 不带时用配置）
  const qTheme = new URLSearchParams(location.search).get('theme');
  try{ applyTheme(qTheme || getPath(cfg,'ui.theme')); }catch(e){}
}

/* ── 鲸鱼光标（默认 22 蓝鲸，用户可自定义图片）——借鉴"自定义背景"成功经验：
   背景靠 body.custom-bg class + CSS 生效；光标同理改为"html.whale-cursor class + cursor:url() 原生光标"，
   不依赖 JS 跟随动画（更可靠、能真正渲染）。图片在服务端已 resize ≤128（CSS 原生光标尺寸上限）。 ── */
const CURSOR_DEFAULT_URL = '/assets/cursor.png';
const CURSOR_DEFAULT_NOD_URL = '/assets/cursor-nod.png';
const CURSOR_CUSTOM_URL = '/assets/custom-cursor.png';
const CURSOR_CUSTOM_NOD_URL = '/assets/custom-cursor-nod.png';
const WHALE_CURSOR = (function(){
  const DEFAULT_URL = CURSOR_DEFAULT_URL;
  const DEFAULT_NOD = CURSOR_DEFAULT_NOD_URL;
  const CUSTOM_URL = CURSOR_CUSTOM_URL;
  const CUSTOM_NOD = CURSOR_CUSTOM_NOD_URL;
  let url = DEFAULT_URL, nodUrl = DEFAULT_NOD, enabled = false, nodTimer = null;
  function setStyle(u){
    let st = document.getElementById('whaleCursorStyle');
    if(!st){ st = document.createElement('style'); st.id = 'whaleCursorStyle'; document.head.appendChild(st); }
    // 注意：cursor:url() 需同时覆盖 html 与所有元素；图片加载失败用 auto（系统默认）兜底
    st.textContent = 'html.whale-cursor,html.whale-cursor *{cursor:url("'+u+'") 8 8, auto!important}';
  }
  // 挂件 iframe（widget.js 动态创建）CSS 无法穿透：向同源 iframe 文档注入光标样式（每 2 秒扫描，已注入跳过）
  function injectFrames(){
    if(!enabled) return;
    try{
      document.querySelectorAll('iframe').forEach(f=>{
        if(f.dataset.whaleCursorDone === url) return;
        try{
          const d = f.contentDocument;
          if(!d || !d.documentElement || !d.head) return;
          let st = d.getElementById('whaleCursorStyle');
          if(!st){ st = d.createElement('style'); st.id = 'whaleCursorStyle'; d.head.appendChild(st); }
          st.textContent = 'html,body,html *{cursor:url("'+url+'") 8 8, auto!important}';
          f.dataset.whaleCursorDone = url;
        }catch(e){}
      });
    }catch(e){}
  }
  function apply(){ setStyle(url + '?v=' + Date.now()); injectFrames(); }
  function applyNod(){ setStyle(nodUrl + '?v=' + Date.now()); }
  // 点击时点头：mousedown 换成歪头帧，180ms 后换回
  document.addEventListener('mousedown', ()=>{
    if(!enabled) return;
    applyNod();
    clearTimeout(nodTimer);
    nodTimer = setTimeout(apply, 180);
  });
  function setCustom(u){
    url = u || DEFAULT_URL;
    nodUrl = DEFAULT_NOD;
    if(u){
      const probe = new Image();
      probe.onload = ()=>{ nodUrl = CUSTOM_NOD + '?v=' + Date.now(); if(enabled) apply(); };
      probe.src = CUSTOM_NOD + '?v=' + Date.now();
    }
    if(enabled) apply();
  }
  function set(on){
    enabled = !!on;
    if(on){ document.documentElement.classList.add('whale-cursor'); apply(); }
    else {
      document.documentElement.classList.remove('whale-cursor');
      const st = document.getElementById('whaleCursorStyle');
      if(st) st.textContent = '';
    }
  }
  apply();
  setInterval(injectFrames, 2000);   // 挂件 iframe 动态出现后自动注入光标
  return { set, setCustom, url: ()=>url };
})();
function syncCursorFromCfg(){
  try{
    const on = getPath(cfg,'ui.whale_cursor') !== false;
    const custom = getPath(cfg,'ui.cursor_image');
    // 默认先确认自定义图是否存在（custom-cursor.png 只有上传后才存在）；加 ?v= 防浏览器缓存旧 404
    if(custom){
      const tus = CURSOR_CUSTOM_URL + '?v=' + Date.now();
      const probe = new Image();
      probe.onload = ()=> WHALE_CURSOR.setCustom(tus);
      probe.onerror = ()=> WHALE_CURSOR.setCustom('');
      probe.src = tus;
    } else {
      WHALE_CURSOR.setCustom('');
    }
    WHALE_CURSOR.set(on);
  }catch(e){}
}

/* ── 光标设置分节：上传/预览/重置 ── */
(function(){
  const cv = $('cursorPreview'), cctx = cv ? cv.getContext('2d') : null;
  let preImg = new Image(), preUrl = '/assets/cursor.png';
  function drawPreview(){
    if(!cctx) return;
    cctx.clearRect(0,0,120,120);
    if(preImg.complete){
      cctx.save();
      const s = Math.min(90/preImg.width, 90/preImg.height);
      const w = preImg.width*s, h = preImg.height*s;
      cctx.drawImage(preImg, (120-w)/2, (120-h)/2 + 6, w, h);
      cctx.restore();
    }
  }
  (function initPreview(){
    preImg.onload = drawPreview;      // 换源后必重绘（重置立即恢复默认图）
    const probe = new Image();
    probe.onload = ()=>{ preUrl = '/assets/custom-cursor.png'; preImg.src = preUrl; };
    probe.onerror = ()=>{ preUrl = '/assets/cursor.png'; preImg.src = preUrl; };
    probe.src = '/assets/custom-cursor.png';
  })();
  if(cv){
    preImg.onload = drawPreview;
    cv.addEventListener('pointerdown', ()=>{   // 点击预览也点头
      cctx.save();
      cctx.translate(60,70); cctx.rotate(0.24); cctx.scale(0.8,0.86); cctx.translate(-60,-70);
      drawPreview(); cctx.restore();
    });
    const cf = $('cursorFile');
    if(cf){
      cf.addEventListener('change', ()=>{
        const f = cf.files && cf.files[0];
        if(!f) return;
        if(!/^image\/(png|jpeg)$/.test(f.type)){ toast('仅支持 PNG/JPEG 图片'); return; }
        const rd = new FileReader();
        rd.onload = function(){
          preImg.src = rd.result;   // 本地预览
          const img2 = rd.result;
          $('cursorSaveBtn').disabled = false;
          $('cursorSaveBtn').dataset.preview = img2;
          toast('已载入预览图片，点「保存光标设置」生效');
        };
        rd.readAsDataURL(f);
      });
    }
    const reset = $('cursorReset');
    if(reset){
      reset.onclick = async ()=>{
        try{
          // 先删自定义光标残留文件（否则刷新后预览探测到旧文件仍显示——030538），再清配置
          await getJSON('/api/cursor/reset',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
          await getJSON('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({ui:{whale_cursor:true, cursor_image:''}})});
          cfg = await getJSON('/api/config'); syncToForm(); syncCursorFromCfg();
          preImg.src = '/assets/cursor.png'; drawPreview();
          toast('已重置为默认鲸鱼光标');
          setTimeout(()=> location.reload(), 800);
        }catch(e){ toast('重置失败：'+e.message); }
      };
    }
    const save = $('cursorSaveBtn');
    if(save){
      save.onclick = async ()=>{
        const dataUrl = save.dataset.preview;
        if(!dataUrl){ toast('先选一张图片'); return; }
        try{
          await getJSON('/api/cursor/upload',{method:'POST',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({image:dataUrl})});
          await getJSON('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({ui:{whale_cursor:true, cursor_image:'custom'}})});
          cfg = await getJSON('/api/config'); syncToForm(); syncCursorFromCfg();
          preImg.src = '/assets/custom-cursor.png';
          save.disabled = true; delete save.dataset.preview;
          toast('✅ 自定义光标已保存并生效');
          setTimeout(()=> location.reload(), 800);   // 强制刷新确保光标生效
        }catch(e){ toast('保存失败：'+e.message); }
      };
    }
  }
})();

async function load(){
  try{ cfg = await getJSON('/api/config'); syncToForm(); onboarding(); applyWhale(); applyCustomBg(); }catch(e){ toast('加载配置失败：'+e.message) }

/* ── 自定义背景：上传 / 恢复默认 / 应用 ── */
function applyCustomBg(){
  try{
    const has = cfg && (getPath(cfg,'ui.background')||'') === 'custom';
    document.body.classList.toggle('custom-bg', !!has);
    // 默认背景=海浪（assets/wallpaper/ocean1.jpg）；自定义后=ui-bg.jpg
    document.body.style.setProperty('--bgimg', has ? 'url(/assets/ui-bg.jpg)' : 'url(/wallpaper/ocean1.jpg)');
    if(has && !document.body.classList.contains('wall-video')) document.body.style.setProperty('--bgimg', 'url(/assets/ui-bg.jpg)');
    if(has) document.body.classList.remove('wall-video');
  }catch(e){}
}
(function(){
  const up = document.getElementById('bgUpload'), cl = document.getElementById('bgClear'),
        f = document.getElementById('bgFile'), rst = document.getElementById('bgRst');
  if(!up) return;
  up.onclick = ()=> f && f.click();
  if(f) f.onchange = async ()=>{
    const file = f.files && f.files[0];
    if(!file) return;
    const ok = await new Promise(res=>{ const rd = new FileReader(); rd.onload = ()=>res(rd.result); rd.onerror = ()=>res(null); rd.readAsDataURL(file); });
    if(!ok){ rst.textContent = '读取文件失败'; return; }
    rst.textContent = '上传中…';
    try{
      const r = await getJSON('/api/ui/background',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({data:ok})});
      if(r.ok){ cfg = await getJSON('/api/config'); applyCustomBg(); rst.textContent = '✅ ' + (r.note||'背景已应用'); }
      else rst.textContent = '上传失败：'+(r.error||'');
    }catch(e){ rst.textContent = '上传失败：'+e.message; }
  };
  cl.onclick = async ()=>{
    try{
      const r = await getJSON('/api/ui/background',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({clear:true})});
      if(r.ok){ cfg = await getJSON('/api/config'); applyCustomBg(); rst.textContent = '✅ 已恢复默认背景'; }
      else rst.textContent = '操作失败：'+(r.error||'');
    }catch(e){ rst.textContent = '操作失败：'+e.message; }
  };
})();
  syncThemeFromCfg(); syncCursorFromCfg(); applyWhale(); applyCustomBg();   // 关键：主加载后也套用文案/背景
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
    try{
      const wv = s.wechat_version || {};
      const el = $('wxver');
      if(el){
        el.textContent = (wv.version ? ('微信 ' + wv.version + ' · 适配层 ' + (wv.adapter||'-')) : '未检测到')
          + (wv.supported===false ? '（⚠️ 低于 4.0，请升级微信）' : '');
        el.style.color = wv.supported===false ? 'var(--err-tx)' : '';
      }
      const dh = $('depHint');
      if(dh){
        dh.textContent = s.dep_ok ? '✅ 版本体检：匹配（微信/适配层/依赖均符合要求）' : '⚠️ 版本体检：存在不匹配（重启时自动弹窗询问修正，或运行 检查微信版本.bat --update）';
        dh.style.color = s.dep_ok ? 'var(--ok-tx)' : 'var(--err-tx)';
      }
    }catch(e){}
    if(window.__calSel && window.applyDay){ window.applyDay(window.__calDay, window.__calSel); return; }   // 选中日期：概览保持"选中日"数据，不覆盖回今日
    $('st-sessions').textContent = s.stats.sessions;
    $('st-tokens').textContent = s.stats.tokens;
    $('st-sent').textContent = s.stats.sent;
    $('st-cost').textContent = '¥' + (s.stats.cost||0).toFixed(4);
    const u = s.usage || {};
    const fmt = (o) => ('¥' + (o?parseFloat(o.cost||0):0).toFixed(4) + ' · ' + ((o?parseInt(o.tokens||0):0)) + ' tok · ' + ((o?parseInt(o.sessions||0):0)) + ' 会话');
    const lbl = {daily:'今日', weekly:'本周', monthly:'本月'};
    $('st-dcost').textContent = fmt(u.day);
    $('st-dlabel').textContent = '今日用量（' + (u.day?u.day.sent:0||0) + ' 条）';
    $('st-pcost').textContent = fmt(u.period);
    $('st-plabel').textContent = (lbl[u.period_type]||'本周期') + '用量（' + (u.period?u.period.sent:0||0) + ' 条）';
    $('st-groups').textContent = s.groups.filter(g=>g.target).length;
    // 成本明细（最近5条/平均/本次其他工具/累计其他工具——恒显示数值）
    $('st-r5c').textContent = '¥' + (s.stats.recent5_cost||0).toFixed(4);
    (function(){
      if(window.__calInit) return; window.__calInit=true;   // 日历只初始化一次（避免随每分钟刷新重置到当前月）
      const grid=$('calGrid')||null, det=$('calDetail')||null, ymEl=$('calYM')||null;
      if(!grid||!det||!ymEl) return;
      const now=new Date(); let ym=now.getFullYear()*100+(now.getMonth()+1);
      const pad=n=>String(n).padStart(2,'0');
      let sel=null;
      function markSel(){
        grid.querySelectorAll('button.sel').forEach(b=>b.classList.remove('sel'));
        if(sel){
          const d=parseInt(sel.slice(-2),10);
          const b=[...grid.querySelectorAll('button')].find(x=>x.textContent===String(d));
          if(b) b.classList.add('sel');
        }
      }
      function applyDay(r,d){
        window.__calSel=d; window.__calDay=r;    // 记录选中日（概览刷新时尊重）
        const fmt=o=>('¥'+(o?parseFloat(o.cost||0):0).toFixed(4)+' · '+((o?parseInt(o.tokens||0):0))+' tok · '+((o?parseInt(o.sessions||0):0))+' 会话');
        $('st-sessions').textContent=r.sessions||0;
        $('st-tokens').textContent=r.tokens||0;
        $('st-sent').textContent=r.sent||0;
        $('st-cost').textContent='¥'+(r.cost||0).toFixed(4);
        $('st-dcost').textContent=fmt(r);
        $('st-dlabel').textContent=d+' 用量（'+(r.sent||0)+' 条）';
        $('st-extra').textContent=fmt(r);
        $('st-pcost').textContent=fmt(r);
        $('st-plabel').textContent='截止 '+d+' 用量';
      }
      window.applyDay = applyDay;
      window.__renderCal = ()=>{ renderCal(); };   // 计费删除后重绘日历
      function renderCal(){
        const y=Math.floor(ym/100), m=ym%100; ymEl.textContent=y+'年'+m+'月';
        const startDay=(new Date(y,m-1,1).getDay()+6)%7, days=new Date(y,m,0).getDate();
        grid.innerHTML='';
        ['一','二','三','四','五','六','日'].forEach(w=>{const d=document.createElement('div');d.style.textAlign='center';d.style.color='var(--tx2)';d.textContent=w;grid.appendChild(d);});
        for(let i=0;i<startDay;i++) grid.appendChild(document.createElement('div'));
        const today=new Date();
        for(let day=1;day<=days;day++){
          const c=document.createElement('button'); c.type='button'; c.className='ghost'; c.style.padding='4px 0'; c.style.fontSize='12px'; c.style.cursor='pointer';
          c.textContent=day;
          const ds=y+'-'+pad(m)+'-'+pad(day);
          if(ds===today.getFullYear()+'-'+pad(today.getMonth()+1)+'-'+pad(today.getDate())) c.style.outline='1px solid var(--blue)';
          c.onclick=()=>{ loadDay(ds); };
          grid.appendChild(c);
        }
        markSel();
      }
      async function loadDay(d){
        sel=d; markSel();
        det.textContent='加载中 '+d+'…';
        try{
          const r=await getJSON('/api/stats/cal',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({d})});
          det.textContent=d+'：'+(r.sessions||0)+' 会话 · '+(r.tokens||0)+' tok · ¥'+(r.cost||0).toFixed(4)+' · '+(r.sent||0)+' 条';
          applyDay(r,d);
        }catch(e){ det.textContent='加载失败：'+e.message; }
      }
      const prev=$('calPrev'), next=$('calNext'), ymBtn=$('calYM');
      if(prev) prev.onclick=()=>{ ym = ym%100===1 ? (Math.floor(ym/100)-1)*100+12 : ym-1; renderCal(); };
      if(next) next.onclick=()=>{ ym = ym%100===12 ? (Math.floor(ym/100)+1)*100+1 : ym+1; renderCal(); };
      // 点击「年月」→ 年份选择弹层（带浮出特效）
      if(ymBtn) ymBtn.onclick = ()=>{
        const curY = Math.floor(ym/100);
        const bx = document.createElement('div'); bx.className='box cal-year-dlg';
        bx.style.cssText='width:min(420px,92vw)';
        const yrs=[]; for(let yy=curY-4; yy<=curY+6; yy++) yrs.push(yy);
        bx.innerHTML='<b style="color:var(--blue)">跳到年份</b>'
          +'<div style="display:grid;grid-template-columns:repeat(6,1fr);gap:6px;margin-top:12px">'
          + yrs.map(yy=>'<button class="ghost '+(yy===curY?'on':'')+'" data-y="'+yy+'" style="padding:6px 0;border-radius:9px;'+(yy===curY?'background:var(--blue);color:#fff;':'' )+'">'+yy+'</button>').join('')
          +'</div>'
          +'<div class="btns" style="margin-top:12px;justify-content:flex-end"><button class="ghost" id="cyCancel">关闭</button></div>';
        const mm=document.createElement('div'); mm.className='mask'; mm.style.background='rgba(8,14,26,.6)';
        mm.appendChild(bx); document.body.appendChild(mm); maskOpen(mm);
        // 特效：弹层浮出
        bx.style.animation='calPop .3s cubic-bezier(.2,1.4,.4,1)';
        const st=document.createElement('style'); st.textContent='@keyframes calPop{from{opacity:0;transform:translateY(14px) scale(.96)}to{opacity:1;transform:none}}'; document.head.appendChild(st);
        const close=()=>{ maskClose(mm); mm.remove(); };
        bx.querySelector('#cyCancel').onclick=close;
        bx.querySelectorAll('[data-y]').forEach(b=>{
          b.onclick=()=>{
            const y=parseInt(b.dataset.y,10), m=ym%100||1;
            ym=y*100+(m<=12?m:1); close(); renderCal();
          };
        });
      };
      renderCal();
    })();
    $('st-ac').textContent = '¥' + (s.stats.avg_cost||0).toFixed(4);
    $('st-extra').textContent = '¥' + (s.stats.extra_now_cost||0).toFixed(4) + (s.stats.extra_now_tokens?(' · ' + s.stats.extra_now_tokens + ' tok'):'');
    $('st-extra2').textContent = '¥' + (s.stats.extra_total_cost||0).toFixed(4) + (s.stats.extra_total_tokens?(' · ' + s.stats.extra_total_tokens + ' tok'):'');
    $('pauseBtn').textContent = s.paused ? '恢复' : '暂停';
    const tb = $('group-table').querySelector('tbody'); tb.innerHTML='';
    for(const g of s.groups){
      const tr=document.createElement('tr');
      tr.innerHTML='<td>'+esc(g.name)+'</td><td><span class="pill '+(g.target?'ok':'off')+'">'+(g.target?'监听':'忽略')+'</span></td>';
      tb.appendChild(tr);
    }
    // 鲸语模式：动态刷新的文本（暂停/恢复等）重新套上鲸语文案
    if(typeof applyWhale==='function' && getPath(cfg,'ui.text_style')==='whale') applyWhale();
  }catch(e){}
}

async function loadLog(){
  try{ const l = await getJSON('/api/logs'); $('log').textContent = l.lines.join('\n'); $('log').scrollTop = $('log').scrollHeight; }catch(e){}
}

/* ── 计费日志勾选删除弹窗（更不透明设计，按天勾选，可一键勾一天/一月，删除后概览自动刷新）── */
function openBillDlg(bills){
  const box = document.createElement('div');
  box.className = 'box bill-dlg';
  box.style.cssText = 'width:min(620px,94vw);max-height:84vh;display:flex;flex-direction:column';
  const listHtml = bills.map(b=>
    '<div class="row" style="display:flex;align-items:center;gap:8px;padding:7px 10px;border-bottom:1px solid rgba(148,196,255,.08);margin:0;font-size:13px;border-radius:8px">'
    +'<label style="display:flex;align-items:center;gap:8px;flex:1;min-width:0;margin:0">'
    +'<input type="checkbox" class="billDay" data-day="'+esc(b.day)+'" style="width:15px;height:15px;flex-shrink:0"> '
    +'<b style="min-width:96px;font-weight:600;color:var(--tx);flex-shrink:0">'+esc(b.day)+'</b> '
    +'<span class="hint" style="flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--tx2);font-size:12.5px">'
    +b.tokens+' tok · ¥'+b.cost.toFixed(4)+' · '+b.calls+' 次 · '+b.sessions+' 会话</span>'
    +'</label></div>'
  ).join('');
  const _now = new Date();
  const _pad = n=>String(n).padStart(2,'0');
  const _years = [];
  // 可查年份 = 当前年往前 5 年 ~ 当前年 + 账单里出现的年份（更早/更晚也并入），降序
  const _curY = _now.getFullYear();
  for(let y=_curY; y>=_curY-5; y--) _years.push(y);
  bills.forEach(b=>{ const y=parseInt(String(b.day||'').slice(0,4),10); if(y && _years.indexOf(y)<0) _years.push(y); });
  _years.sort((a,b)=>b-a);
  const _yOpts = _years.map(y=>'<option value="'+y+'">'+y+'年</option>').join('');
  const _selStyle = 'background:var(--input-bg);color:var(--tx);border:1px solid var(--bd);border-radius:8px;padding:3px 8px;font-size:12.5px';
  box.innerHTML =
    '<div class="bd-head"><b class="whale-tag">🗑 勾选删除计费日志</b><span class="hint">共 '+bills.length+' 天，精确到年月日；删除后概览自动刷新</span>'
    +'<span class="sp" style="flex:1"></span><button class="ghost tiny" id="bdClose">✕</button></div>'
    +'<div class="hint" style="margin:0 0 6px">勾选要删除的天（可一键勾今日/本月）；操作不可恢复。</div>'
    +'<div class="bd-sel">'
      +'<button class="ghost" id="bdSelAll">☑ 全选</button>'
      +'<button class="ghost" id="bdSelNone">清空勾选</button>'
      +'<button class="ghost" id="bdSelDay">✔ 勾选今日</button>'
      +'<button class="ghost" id="bdSelMonth">✔ 勾选本月</button>'
    +'</div>'
    +'<div class="bd-find" style="display:flex;align-items:center;gap:6px;margin:0 0 8px;flex-wrap:wrap">'
      +'<b style="font-size:12.5px;color:var(--blue)">📅 按日期定位</b>'
      +'<select id="bdY" style="'+_selStyle+';width:86px">'+_yOpts+'</select>'
      +'<select id="bdM" style="'+_selStyle+';width:72px"></select>'
      +'<select id="bdD" style="'+_selStyle+';width:72px"></select>'
      +'<button class="ghost tiny" id="bdFind" style="padding:7px 12px">定位到该日</button>'
      +'<span class="hint" id="bdFindRst" style="font-size:12px;color:var(--tx2);word-break:break-all"></span>'
    +'</div>'
    +'<div class="bill-list">'+listHtml+'</div>'
    +'<div class="hint" id="bdSum" style="margin-top:8px"></div>'
    +'<div class="btns" style="justify-content:flex-end;margin-top:10px">'
      +'<button class="pri" id="bdOk">确认删除</button>'
      +'<button class="ghost" id="bdCancel">取消</button>'
    +'</div>';
  const mm = document.createElement('div'); mm.className = 'mask'; mm.style.background = 'rgba(5,9,17,.88)';   // 更不透明
  mm.appendChild(box); document.body.appendChild(mm); maskOpen(mm);
  box.style.animation = 'calPop .3s cubic-bezier(.2,1.4,.4,1)';
  const selDays = ()=>[...box.querySelectorAll('.billDay:checked')].map(c=>c.dataset.day);
  const setAll = (on)=>{ box.querySelectorAll('.billDay').forEach(c=>{ c.checked = on; }); };
  const sum = ()=>{
    const days = selDays();
    let tok = 0, cost = 0;
    bills.forEach(b=>{ if(days.indexOf(b.day)>=0){ tok += b.tokens; cost += b.cost; } });
    $('bdSum').textContent = '已选 '+days.length+' 天 · '+tok+' tok · ¥'+cost.toFixed(4)+(days.length===bills.length?'（全部选中）':'');
  };
  box.querySelectorAll('.billDay').forEach(c=> c.addEventListener('change', sum));
  box.querySelector('#bdSelAll').onclick = ()=>{ setAll(true); sum(); };
  box.querySelector('#bdSelNone').onclick = ()=>{ setAll(false); sum(); };
  box.querySelector('#bdSelDay').onclick = ()=>{
    setAll(false);
    const t=new Date(); const s=t.getFullYear()+'-'+String(t.getMonth()+1).padStart(2,'0')+'-'+String(t.getDate()).padStart(2,'0');
    box.querySelectorAll('.billDay').forEach(c=>{ if(c.dataset.day===s) c.checked=true; });
    sum();
  };
  box.querySelector('#bdSelMonth').onclick = ()=>{
    setAll(false);
    const t=new Date(); const m=t.getFullYear()+'-'+String(t.getMonth()+1).padStart(2,'0');
    box.querySelectorAll('.billDay').forEach(c=>{ if(c.dataset.day.startsWith(m)) c.checked=true; });
    sum();
  };
  /* 按年月日定位：选年/月/日 → 找到则列表滚动+高亮+显示记录；没有则提示"没有计费记录" */
  const selY = box.querySelector('#bdY'), selM = box.querySelector('#bdM'), selD = box.querySelector('#bdD');
  const rst = box.querySelector('#bdFindRst');
  function fillDays(){
    const y=parseInt(selY.value,10), m=parseInt(selM.value,10)||1;
    const days=new Date(y,m,0).getDate();
    const cur=parseInt(selD.value,10)||_now.getDate();
    selD.innerHTML = Array.from({length:days},(_,i)=>'<option value="'+(i+1)+'">'+(i+1)+'日</option>').join('');
    selD.value = Math.min(cur, days);
    if(selD._refresh) selD._refresh();   // 选项重建后刷按钮文字
  }
  selM.innerHTML = Array.from({length:12},(_,i)=>'<option value="'+(i+1)+'">'+(i+1)+'月</option>').join('');
  selM.value = _now.getMonth()+1;
  fillDays();
  enhanceSelect(selY); enhanceSelect(selM); enhanceSelect(selD);   // 与功能栏同款自绘下拉
  selY.addEventListener('change', fillDays);
  selM.addEventListener('change', fillDays);
  box.querySelector('#bdFind').onclick = ()=>{
    const day = selY.value+'-'+_pad(selM.value)+'-'+_pad(selD.value);
    box.querySelectorAll('.row.hi').forEach(r=>r.classList.remove('hi'));
    const chk = box.querySelector('.billDay[data-day="'+day+'"]');
    if(chk){
      const row = chk.closest('.row');
      row.scrollIntoView({block:'center', behavior:'smooth'});
      row.classList.add('hi');
      setTimeout(()=>row.classList.remove('hi'), 1800);
      const b = bills.find(x=>x.day===day) || {tokens:0, cost:0, calls:0, sessions:0};
      rst.innerHTML = '✅ '+day+'：'+b.tokens+' tok · ¥'+b.cost.toFixed(4)+' · '+b.calls+' 次 · '+b.sessions+' 会话 <span style="color:var(--tx2)">（已在列表定位）</span>';
    } else {
      rst.innerHTML = '⚠️ '+day+' 没有计费记录';
    }
  };
  const close = ()=>{ maskClose(mm); mm.remove(); };
  box.querySelector('#bdClose').onclick = close;
  box.querySelector('#bdCancel').onclick = close;
  box.querySelector('#bdOk').onclick = async ()=>{
    const days = selDays();
    if(!days.length){ toast('请先勾选要删除的天'); return; }
    if(!await uiConfirm('确认删除所选 '+days.length+' 天的计费日志？删除后不可恢复。')) return;
    try{
      const r = await getJSON('/api/stats/cal_delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({days})});
      if(r.ok){
        close();
        toast('✅ 已删除 '+r.removed.length+' 天计费日志');
        if(typeof loadStatus==='function') loadStatus();          // 概览自动刷新（含今日/累计/日历）
        if(window.__renderCal) window.__renderCal();              // 日历重绘（删掉的天从日历与明细消失）
        if(window.applyDay && window.__calDay) window.applyDay({sessions:0,tokens:0,sent:0,cost:0}, window.__calDay);
      } else toast(r.error||'删除失败');
    }catch(e){ toast('删除失败：'+e.message); }
  };
  sum();
}

/* ── 运行明细：思考过程 / token / 工具调用 ── */
async function loadSessions(){
  if($('sessSelDel')) $('sessSelDel').onclick = async ()=>{
    const sel=[...document.querySelectorAll('#sessList .sessSel:checked')].map(x=>x.dataset.date).filter(Boolean);
    if(!sel.length){ return; }
    if(!await uiConfirm('确认删除所选 '+sel.length+' 个日期的运行明细与对话历史？')) return;
    try{
      const r=await getJSON('/api/sessions/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({dates:sel})});
      if(r.ok) loadSessions(); else toast(r.error||'删除失败');
    }catch(e){ toast('删除失败：'+e.message); }
  };
  const el = $('sessList');
  try{
    const r = await getJSON('/api/sessions?limit=30');
    const list = (r && r.sessions) || [];
    if(!list.length){
      el.innerHTML = '<div class="hint" style="padding:14px;text-align:center;color:var(--tx2)">还没有运行记录——群里 @ 机器人说句话后，这里会出现每一轮的思考过程 / token / 工具调用。</div>';
      return;
    }
    el.innerHTML='';
    const showDetail = $('sessExpand') ? $('sessExpand').checked : false;
    for(const e of list){
      const card=document.createElement('div');
      card.className='dlist';
      const tools=(e.tools||[]).map(t=>'<span class="pill">'+esc(t.name)+'</span>').join(' ');
      const reason=(e.reasoning||'').trim();
      const rt = parseInt(e.reasoning_tokens||0);
      const tt = parseInt(e.tokens||0);
      const rpct = (tt>0 && rt>0) ? (' · 推理 '+rt+' tok（'+Math.round(rt/tt*100)+'%）') : '';
      let html='<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">'
        +'<label style="display:flex;align-items:center;gap:4px;cursor:pointer" title="勾选删除"><input type="checkbox" class="sessSel" data-date="'+esc(String(e.ts||'').slice(0,10))+'">删</label>'
        +'<b>'+esc(e.chat_name||e.chat_key)+'</b>'
        +'<span class="pill '+(e.ok?'ok':'off')+'">'+esc(e.status||'')+'</span>'
        +'<span class="hint" style="font-size:11px">'+esc((e.ts||'').replace('T',' '))+' · '+esc(e.latency_ms||0)+'ms</span>'
        +'<span class="hint" style="font-size:11px">'+esc(e.tokens||0)+' tok · ¥'+((e.cost||0).toFixed(4))+'</span></div>';
      if(e.reply) html+='<div class="hint" style="margin-top:3px">发：'+esc(e.reply)+'</div>';
      if(showDetail){
        if(e.trigger) html+='<div class="hint" style="margin-top:6px">触发：'+esc(e.trigger.slice(0,120))+'</div>';
        if(tools) html+='<div style="margin-top:6px">工具：'+tools+'</div>';
        if(e.error) html+='<div style="margin-top:6px;color:var(--err-tx)">失败：'+esc(e.error)+'</div>';
        if(reason){
          html+='<details style="margin-top:6px"><summary class="hint" style="cursor:pointer;user-select:none">推理文本（'+reason.length+' 字'+esc(rpct)+'）</summary>'
            +'<pre class="out" style="margin-top:6px;max-height:220px;overflow:auto;white-space:pre-wrap;cursor:text">'+esc(reason)+'</pre></details>';
        }
      }
      card.innerHTML=html;
      el.appendChild(card);
    }
    $('sessBox').scrollTop = $('sessBox').scrollHeight;  // 始终滚到最新
  }catch(e){ el.innerHTML='<div class="hint" style="padding:14px;text-align:center">加载失败：'+esc(String(e))+'</div>'; }
}

/* 联网搜索：当前引擎字段 ↔ web_search.<provider> 小节 专用同步 */
function wsSyncToForm(){
  if(!cfg) return;
  const prov = getPath(cfg,'web_search.provider') || 'bing';
  if($('wsProvider')) $('wsProvider').value = prov;
  const sec = (cfg.web_search && cfg.web_search[prov]) || {};
  if($('wsKey')) $('wsKey').value = sec.api_key || '';
  if($('wsUrl')) $('wsUrl').value = sec.base_url || '';
  if($('wsModel')) $('wsModel').value = sec.model || '';
  if($('wsEngine')) $('wsEngine').value = sec.engine || '';
  if($('wsCount')) $('wsCount').value = sec.count || 6;
  wsShowRows(prov);
}
function wsSyncFromForm(){
  if(!$('wsProvider') || !cfg) return;
  const prov = $('wsProvider').value;
  if(!cfg.web_search) cfg.web_search = {};
  const pre = cfg.web_search[prov] || {};
  if($('wsKey')) pre.api_key = $('wsKey').value;
  if($('wsUrl')) pre.base_url = $('wsUrl').value;
  if($('wsModel')) pre.model = $('wsModel').value;
  if($('wsEngine')) pre.engine = $('wsEngine').value;
  if($('wsCount')) pre.count = parseInt($('wsCount').value) || 6;
  cfg.web_search[prov] = pre;
}
function wsShowRows(prov){
  document.querySelectorAll('[data-ws]').forEach(el=>{
    el.style.display = (el.dataset.ws === prov) ? '' : 'none';
  });
}
(function(){
  const sel = document.querySelector('[data-cfg="web_search.provider"]');
  if(sel){
    sel.addEventListener('change', ()=>wsShowRows(sel.value));
  }
})();

async function saveAllBtn(btn){
  const cur = btn ? btn.textContent : '保存';
  if(btn){ btn.disabled = true; btn.textContent = '保存中…'; }
  try{
    let raw = null;
    try{ raw = JSON.parse($('rawjson').value); }catch(e){}
    // 优先用「界面表单」的改动（syncFromForm），避免 rawjson 旧值覆盖界面修改（如 text_style 切换丢失）。
    // 仅当用户确实改了「原始JSON」且表单未改动时才用 rawjson——此处以界面为主。
    syncFromForm(); wsSyncFromForm(); if(typeof syncMemGroupsToCfg==='function') syncMemGroupsToCfg();
    await getJSON('/api/config', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(cfg)});
    toast('✅ 已保存，刷新页面生效…');
    setTimeout(()=>{
      const u = new URL(location.href);
      u.searchParams.set('v', Date.now());   // 带时间戳刷新=不读缓存（鲸语切换必生效）
      location.replace(u.toString());
    }, 700);
  }catch(e){ toast('保存失败：'+e.message); if(btn){ btn.disabled = false; btn.textContent = cur; } }
}

/* ── 自绘下拉组件：替换所有原生 select（弹层样式可控，DeepSeek 风）；单元素可复用（弹窗内动态 select 也用）── */
function enhanceSelect(sel){
  if(sel._enhanced) return sel;
  sel._enhanced = true;
  const w = sel.style && sel.style.width;
  const wrap = document.createElement('div'); wrap.className='dsel';
  wrap.style.width = w || '100%';
  const btn = document.createElement('button'); btn.type='button'; btn.className='dsel-btn';
  btn.innerHTML = '<span class="txt"></span><span class="arr">▾</span>';
  const menu = document.createElement('div'); menu.className='dsel-menu dn';
  const sel2 = sel; // 原 select 隐藏但保留值
  sel2.style.display = 'none';
  function refreshText(){
    const o = sel2.options[sel2.selectedIndex];
    btn.querySelector('.txt').textContent = (o && o.textContent) || sel2.value || '—';
  }
  sel2._refresh = refreshText;   // 程序改 value 后调用（只刷按钮文字，不触发业务 change）
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
    document.querySelectorAll('.dsel.open-z').forEach(d=>{ d.classList.remove('open-z'); d.style.zIndex=''; });
    document.querySelectorAll('.card.fx-overflow,.box.fx-overflow,.bill-dlg.fx-overflow').forEach(c=>c.classList.remove('fx-overflow'));
    if(!open){
      buildMenu(); refreshText(); menu.classList.remove('dn');
      // 关键：.dsel(z=70) 自身建立层叠上下文，菜单(220)在它内部——必须把本 wrap 提到 500，
      // 否则后面的兄弟下拉（模型行等）会盖住菜单（"叠上"根因）
      wrap.classList.add('open-z'); wrap.style.zIndex='500';
      let host = wrap.closest('.card, .box, .bill-dlg');
      if(host){ host.classList.add('fx-overflow'); }
    }
  });
  document.addEventListener('click', ()=>{
    menu.classList.add('dn');
    if(wrap.classList.contains('open-z')){ wrap.classList.remove('open-z'); wrap.style.zIndex=''; }
  });
  sel2.addEventListener('change', ()=>{ buildMenu(); refreshText(); });
  sel2.insertAdjacentElement('afterend', wrap);
  wrap.appendChild(btn); wrap.appendChild(menu);
  refreshText();
  return sel;
}
function enhanceSelects(){
  document.querySelectorAll('select').forEach(enhanceSelect);
}
/* ── 所有搜索栏统一加「搜索」按钮（点击=模拟触发 input，各列表联动）── */
(function(){
  function bind(){
    document.querySelectorAll('.group-search').forEach(inp=>{
      if(inp._hasBtn) return; inp._hasBtn = true;
      const btn = document.createElement('button');
      btn.type = 'button'; btn.className = 'ghost';
      btn.textContent = '搜索';
      btn.style.cssText = 'padding:3px 12px;margin-left:6px;border-radius:8px';
      btn.onclick = ()=>{ inp.dispatchEvent(new Event('input', {bubbles:true})); };
      inp.insertAdjacentElement('afterend', btn);
    });
  }
  bind();
  new MutationObserver(bind).observe(document.body, {childList:true, subtree:true});
})();
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
    models:['deepseek-v4-flash-vision-exp','deepseek-v4-pro-0813','deepseek-v4-flash-0731','deepseek-v4-flash','deepseek-v4-pro','deepseek-v3.2','deepseek-v3.1-terminus','deepseek-r1-0528','deepseek-chat','deepseek-reasoner']},
  moonshot:{label:'Moonshot Kimi', base:'https://api.moonshot.cn/v1', keyHint:'sk-',
    models:['kimi-k3','kimi-k2.7-code','kimi-k2.6','kimi-k2','kimi-k2-0905-preview','kimi-k2-0711-preview','moonshot-v1-128k','moonshot-v1-32k','moonshot-v1-8k']},
  zhipu:{label:'智谱 GLM', base:'https://open.bigmodel.cn/api/paas/v4', keyHint:'',
    models:['glm-5.3','glm-5.3-flash','glm-5.2','glm-5.1','glm-5-turbo','glm-5','glm-5v-turbo','glm-4.7','glm-4.7-flash','glm-4.7-flashx','glm-4.6','glm-4.5','glm-4.5-air','glm-4.6v','glm-4.6v-flashx','glm-4.5v','glm-4-plus','glm-4-long','glm-4-flash','glm-4v-plus']},
  qwen:{label:'通义千问（阿里）', base:'https://dashscope.aliyuncs.com/compatible-mode/v1', keyHint:'sk-',
    models:['qwen3.8-max','qwen3.8-max-0902','qwen3.8-flash','qwen3.8-2.4t-a95b','qwen3.7-max','qwen3.7-plus','qwen3.7-plus-2026-05-26','qwen3.7-flash','qwen3.6-flash','qwen3.5-plus','qwen3.5-397b-a17b','qwen3-max','qwen3-plus','qwen3-235b-a22b-instruct','qwen3-235b-a22b-thinking-2507','qwen3-coder-plus','qwen3-32b','qwen-max','qwen-plus','qwen-flash','qwen-turbo','qwen-long','qwen-vl-max','qwen-vl-plus']},
  minimax:{label:'MiniMax', base:'https://api.minimaxi.com/v1', keyHint:'',
    models:['MiniMax-M3','MiniMax-M2.7','MiniMax-M2.7-Highspeed','MiniMax-M2.5','MiniMax-M2.5-Highspeed','MiniMax-M2.1','MiniMax-M2.1-Highspeed','MiniMax-M2','MiniMax-M1-80k','abab6.5s-chat']},
  xiaomi:{label:'小米 MiMo', base:'https://api.xiaomimimo.com/v1', keyHint:'',
    models:['mimo-v2.5','mimo-v2.5-pro','mimo-v2.5-pro-ultraspeed','mimo-v2.5-flash']},
  hunyuan:{label:'腾讯混元', base:'https://api.hunyuan.cloud.tencent.com/v1', keyHint:'',
    models:['hunyuan-a13b','hunyuan-role-latest','hunyuan','hy3','hy4-preview']},
  ernie:{label:'百度文心', base:'https://qianfan.baidubce.com/v2', keyHint:'',
    models:['ernie-5.0','ernie-4.5']},
  doubao:{label:'豆包（火山方舟）', base:'https://ark.cn-beijing.volces.com/api/v3', keyHint:'',
    models:['doubao-pro','doubao-lite','doubao-seed-1.6-250615','doubao-1.5-pro-32k','doubao-vision-pro-32k']},
  openai:{label:'ChatGPT（OpenAI）', base:'https://api.openai.com/v1', keyHint:'sk-',
    models:['gpt-5.6-sol','gpt-5.6-terra','gpt-5.6-luna','gpt-5.6-cyber','gpt-5.5','gpt-5.5-pro','gpt-5.4','gpt-5.4-mini','gpt-5.4-nano','gpt-5.2','gpt-5.1','gpt-5','gpt-5-mini','gpt-5-nano','gpt-4.1','gpt-4.1-mini','gpt-4.1-nano','gpt-4o','gpt-4o-mini','gpt-4-turbo','o3','o3-mini','o4-mini','gpt-oss-120b','gpt-oss-20b']},
  claude:{label:'Claude（Anthropic，OpenAI 兼容端点）', base:'https://api.anthropic.com/v1', keyHint:'sk-ant-',
    models:['claude-fable-5.1','claude-mythos-5.1','claude-fable-5','claude-mythos-5','claude-opus-5','claude-opus-4.8','claude-opus-4.7','claude-opus-4.6','claude-opus-4.5','claude-sonnet-5','claude-sonnet-4.6','claude-haiku-4.5','claude-haiku-4.5-batch','claude-opus-4-1-20250805','claude-sonnet-4-5-20250929','claude-3-7-sonnet-20250219','claude-3-5-haiku-20241022']},
  gemini:{label:'Gemini（Google）', base:'https://generativelanguage.googleapis.com/v1beta/openai', keyHint:'AIza',
    models:['gemini-3.8-flash','gemini-3.7-flash','gemini-3.6-flash','gemini-3.5-flash','gemini-3.5-flash-lite','gemini-3.1-pro','gemini-3.1-flash-lite','gemini-3-flash','gemini-3-pro-preview','gemini-2.5-pro','gemini-2.5-flash','gemini-2.5-flash-lite','gemini-2.0-flash','gemini-1.5-pro']},
  grok:{label:'Grok（xAI）', base:'https://api.x.ai/v1', keyHint:'xai-',
    models:['grok-4.6','grok-4.5','grok-4','grok-3','grok-3-mini','grok-2-latest']},
  nvidia:{label:'NVIDIA（Nemotron）', base:'https://integrate.api.nvidia.com/v1', keyHint:'nvapi-',
    models:['nemotron-3-ultra','nemotron-3.5-lightning','nemotron-3-ultra-550b']},
  openrouter:{label:'OpenRouter（聚合）', base:'https://openrouter.ai/api/v1', keyHint:'sk-or-',
    models:['muse-spark-1.3','muse-spark-1.2','muse-spark-1.1','llama-4-maverick','llama-3.3-70b','mistral-large-3','mistral-medium-3.5','command-a','solar-pro-4','step-3.7-flash','longcat-2.0','ling-3.0-flash','granite-4.0-h-micro','inkling-with-ai']},
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
  if(askKey){
    // 换厂商必弹（含 deepseek）：让用户确认该公司的 API Key（预填当前值，可覆盖/跳过）
    // 例外：若该厂商已有真实 Key 且与输入框一致，则不打扰
    const have = (pk && pk.value || '').trim();
    const isMasked = have.includes('••••') || have.startsWith('sk-***') || !have;
    const saved = cfg && cfg.api && cfg.api.provider_keys && cfg.api.provider_keys[provider];
    // deepseek 默认：首次（无真实 Key 时）必须让用户知道要填 Key——非打码且已有值才跳过
    if(!isMasked && (provider!=='deepseek' || saved)) return;
    const m=document.createElement('div'); m.className='mask';
    m.innerHTML='<div class="box"><h1>'+p.label+' API Key</h1><p>已切换到 '+p.label+'（Base URL：'+p.base+'）。请填写该公司的 API Key（'+(p.keyHint||'见官网')+' 开头）。</p><input type="password" id="pkCmd" placeholder="'+(p.keyHint||'')+'..." value="'+have.replace(/"/g,'')+'"><div class="btns" style="justify-content:center"><button class="pri" id="pkOk">保存 Key</button><button class="ghost" id="pkSame">沿用现有 Key</button><button class="ghost" id="pkNo">暂不填</button></div></div>';
    document.body.appendChild(m); maskOpen(m);
    $('pkOk').onclick=()=>{ const v=$('pkCmd').value.trim(); if(v&&pk) pk.value=v; maskClose(m); m.remove(); toast('已填入 '+p.label+' Key，点「保存 Key」或「保存设置」生效'); };
    $('pkSame').onclick=()=>{ maskClose(m); m.remove(); };
    $('pkNo').onclick=()=>{ maskClose(m); m.remove(); };
  }
}
$('providerSel').addEventListener('change', ()=>applyProvider($('providerSel').value, true));
/* ── 左上角鲸鱼徽章：可拖拽鲸鱼（icon-whale 白鲸） + 三态随机返回 ──
   拖出：按住鲸鱼 → 跟手（弹簧跟随+速度拉伸）→ 松开随机选一种返回方式。
   返回方式（等概率随机）：
     A 蠕动回去：转身 → 波形蠕动（身体波浪+正弦摆尾）→ 循弧线回框 → 缩身钻入
     B 纸飞机：原地翻转变白 → 平滑 morph 成纸飞机（折痕显现）→ 抛物线滑翔（上冲→滑降）→ 入框翻回鲸鱼
     C 扎入消失：冲刺拉伸 → 缩小透视消失（0.5s）→ 从框中心喷出（气泡粒子）→ 回弹落稳
   每个大动作拆成细帧：phase 状态机 + rAF，物理用弹簧/正弦/抛物线。 */
(function(){
  const badge = $('whaleBadge');
  if(!badge) return;
  const hero = badge.querySelector('img');
  const BADGE_W = 52, BADGE_H = 52;
  let FLY = null;               // 飞行实例（游离的克隆鲸鱼）
  const dragging = {on:false, dx:0, dy:0, vx:0, vy:0, tx:0, ty:0};
  let returnTimer = null;

  /* ── 图标静态果冻弹跳（悬停时，无拖拽时） ── */
  hero.style.transformOrigin = 'bottom center';
  hero.style.transition = 'transform .07s cubic-bezier(.3,1.4,.6,1)';
  let idleJelly = true;
  function jelly(seq){
    const poses = [
      'scaleY(0.62) scaleX(1.22)', 'scaleY(1.24) scaleX(0.84)',
      'scaleY(0.58) scaleX(1.26)', 'scaleY(1.14) scaleX(0.9)',
      'scaleY(0.9) scaleX(1.08)', 'scaleY(1.04) scaleX(0.98)',
      'scaleY(1) scaleX(1)',
    ];
    if(seq >= poses.length){ idleJelly = true; if(!FLY) hero.style.transform = 'scaleY(1) scaleX(1)'; return; }
    hero.style.transform = poses[seq];
    setTimeout(()=>jelly(seq + 1), 80 + seq * 30);
  }
  badge.addEventListener('mouseenter', ()=>{ if(!idleJelly || FLY) return; idleJelly = false; jelly(0); });
  badge.addEventListener('mouseleave', ()=>{ /* 继续放完 */ });

  /* ── helpers ── */
  function makeFly(){
    if(FLY) return FLY;
    const div = document.createElement('div');
    div.className = 'whale-fly';
    div.innerHTML = '<img src="/assets/icon-whale.png" alt="">';
    document.body.appendChild(div);
    FLY = div;
    return div;
  }
  function removeFly(){
    if(FLY){ FLY.remove(); FLY = null; }
  }
  function badgeCenter(){
    const r = badge.getBoundingClientRect();
    return {x: r.left + r.width/2, y: r.top + r.height/2};
  }

  /* ── 拖拽：pointer 事件（鼠标+触屏） ── */
  let wiggle = 0;   // 被抓扭动相位
  badge.addEventListener('pointerdown', (ev)=>{
    if(FLY) return;                        // 正在飞行，忽略
    dragging.on = true;
    dragging.tx = dragging.dx = ev.clientX;
    dragging.ty = dragging.dy = ev.clientY;
    dragging.vx = dragging.vy = 0;
    dragging.t0 = performance.now();
    wiggle = 0;
    idleJelly = false;
    hero.style.transition = 'none';
    // 视觉连续：克隆一个游离鲸鱼跟手，本体淡出（返回动画用克隆体）
    hero.style.opacity = '0.12';
    const fly = makeFly();
    fly.style.left = ev.clientX + 'px'; fly.style.top = ev.clientY + 'px';
    hero.classList.add('whale-grabbing');
    badge.classList.add('whale-open');     // 取消裁切
    try{ badge.setPointerCapture(ev.pointerId); }catch(e){}
    ev.preventDefault();
  });
  badge.addEventListener('pointermove', (ev)=>{
    if(!dragging.on) return;
    dragging.tx = ev.clientX; dragging.ty = ev.clientY;
    dragging.vx = dragging.tx - dragging.dx; dragging.vy = dragging.ty - dragging.dy;
    dragging.dx = dragging.tx; dragging.dy = dragging.ty;
    /* 跟手 + 速度拉伸 + 被抓扭动（挣扎：后半身摆动，幅度随时间衰减"挣扎累了"） */
    if(FLY){
      const dxc = Math.min(Math.abs(dragging.vx), 60), dyc = Math.min(Math.abs(dragging.vy), 40);
      const sx = 1 + dxc / 200, sy = 1 + dyc / 200;
      const ang = Math.atan2(dragging.vy, dragging.vx);
      const held = (performance.now() - (dragging.t0 || performance.now())) / 1000;
      const amp = Math.max(0.08, 0.4 - held * 0.12);          // 挣扎幅度衰减（0.4→0.08）
      wiggle += 0.45;
      const wig = Math.sin(wiggle) * amp;                      // 扭动（摆尾）
      FLY.style.left = ev.clientX + 'px'; FLY.style.top = ev.clientY + 'px';
      FLY.style.transform = 'translate(-50%,-50%) scale(' + sx + ',' + sy + ')'
        + ' rotate(' + (ang * 0.22 + wig * 0.35) + 'rad) scaleX(' + (1 + wig * 0.12) + ')';
    }
  });
  badge.addEventListener('pointerup', (ev)=>{
    if(!dragging.on) return;
    dragging.on = false;
    hero.style.opacity = '';
    hero.classList.remove('whale-grabbing');
    badge.classList.remove('whale-open');
    hero.style.transition = 'transform .25s cubic-bezier(.3,1.4,.6,1)';
    hero.style.transform = '';
    /* 选择返回方式：三次等概率随机；留在拖拽落点 → 从落点触发返回（距离决定时长） */
    const mode = Math.floor(Math.random() * 3);   // 0=蠕动 1=纸飞机 2=扎入
    const from = {x: ev.clientX, y: ev.clientY};
    startReturn(mode, from);
  });
  badge.addEventListener('pointercancel', ()=>{
    if(!dragging.on) return;
    dragging.on = false;
    hero.style.opacity = '';
    hero.classList.remove('whale-grabbing');
    badge.classList.remove('whale-open');
    hero.style.transition = '';
    hero.style.transform = '';
    if(FLY) removeFly();
    hero.style.opacity = '1';
  });

  /* ═══════════ 返回方式：蠕动 / 纸飞机 / 扎入（等概率随机） ═══════════ */
  let planeState = null, zapState = null;
  function startReturn(mode, from){
    const start = {x: from.x, y: from.y};
    const c = badgeCenter();
    const t0 = performance.now();
    const mid = {x: c.x + (start.x - c.x) * 0.15 + (Math.random()*40 - 20),
                 y: c.y - 80 - Math.random()*40};
    const fly = makeFly();
    const img = fly.querySelector('img');
    img.style.display = 'block'; img.style.opacity = '';
    planeState = null; zapState = null;

    /* ── 时长按距离计算（控制台「光标设置」可调系数 whale_anim.*） ──
       公式（dist = 拖拽落点到徽章中心距离 px）：
         蠕动: dur = clamp(260 × dist/100 × k_worm, 600, 2400)
         纸飞机: fly = clamp(170 × dist/100 × k_plane, 420, 1500) + morph 360 + unmorph 220
         扎入: dash = clamp(90 × dist/100 × k_zap, 200, 720) + 消失 0.5s 固定 + 冒出 300
       蠕动最慢、纸飞机更快、扎入按距离自适应（短拖拽都快，长拖拽蠕动能到 2.4s 上限不再加长） */
    let km = 1;
    try{
      const ao = getPath(cfg,'ui.whale_anim') || {};
      km = Math.max(0.3, Math.min(3, parseFloat(ao[['worm','plane','zap'][mode]]) || 1));
    }catch(e){}
    const dist = Math.hypot(start.x - c.x, start.y - c.y);
    const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
    const dur  = mode === 0 ? clamp(260 * dist / 100 * km, 600, 2400) : 0;
    const flyD = mode === 1 ? clamp(170 * dist / 100 * km, 420, 1500) : 0;
    const zapD = mode === 2 ? clamp(90 * dist / 100 * km, 200, 720) : 0;
    // 调试日志：每次返回记录（距离/方式/时长），控制台可查
    try{
      const names = ['蠕动','纸飞机','扎入'];
      console.log('[whale-return] mode=' + names[mode] + ' dist=' + Math.round(dist) + 'px dur=' +
        Math.round(mode===0?dur:(mode===1?flyD+580:zapD+780)) + 'ms（系数 ' + km + '）');
    }catch(e){}

    if(mode === 0){
      /* A 蠕动：贝塞尔弧线 + 身体波浪 + 摆尾 + 临近缩身钻入（自驱动） */
      function worm(now){
        const t = Math.min(1, (now - t0) / dur);
        const e = t * t * (3 - 2 * t);
        const bx = quadBez(start.x, mid.x, c.x, e);
        const by = quadBez(start.y, mid.y, c.y, e);
        const wave = Math.sin(e * Math.PI * 6) * 0.16;
        const tilt = Math.sin(e * Math.PI * 6 + 1.2) * 0.3;
        const scale = t < 0.75 ? 1 : Math.max(0.05, 1 - (t - 0.75) * 2.2);
        fly.style.left = bx + 'px'; fly.style.top = by + 'px';
        fly.style.transform = 'translate(-50%,-50%) scale(' + scale + ') scaleY(' + (1 + wave) + ') scaleX(' + (1 - wave * 0.5) + ') rotate(' + tilt + 'rad)';
        if(t >= 1){ finishReturn(fly); return; }
        requestAnimationFrame(worm);
      }
      requestAnimationFrame(worm);
    } else if(mode === 1){
      planePhase(fly, c, start, flyD);
    } else {
      zapPhase(fly, c, start, zapD);
    }
  }

  function finishReturn(fly){
    // 入框：从框中心回弹出现（0.14s 超弹）+ 气泡粒子
    const c = badgeCenter();
    fly.style.left = c.x + 'px'; fly.style.top = c.y + 'px';
    fly.style.transition = 'transform .14s cubic-bezier(.2,1.6,.5,1), opacity .2s';
    fly.style.transform = 'translate(-50%,-50%) scale(0)';
    fly.style.opacity = '1';
    requestAnimationFrame(()=>{ fly.style.transform = 'translate(-50%,-50%) scale(1.06)'; });
    spawnBubbles(c.x, c.y);
    hero.style.opacity = '';             // 本体恢复可见
    setTimeout(()=>{ fly.style.opacity = '0'; }, 180);
    setTimeout(()=>{ removeFly(); }, 420);
    idleJelly = true;
    // 徽章本体恢复过冲
    hero.style.transition = 'transform .18s cubic-bezier(.34,1.4,.64,1)';
    hero.style.transform = 'scaleY(1.12) scaleX(0.9)';
    setTimeout(()=>{ hero.style.transform = ''; }, 120);
  }

  /* 纸飞机：morph 白化 → 抛物线滑翔 → 入框翻回（自驱动 rAF；flyMs=滑翔时长） */
  function planePhase(fly, c, start, flyMs){
    flyMs = flyMs || 780;
    // 初始化纸飞机
    const p = document.createElement('div');
    p.className = 'paper-plane';
    p.innerHTML = '<svg viewBox="0 0 64 40" width="58" height="36"><path d="M2 20 L62 2 L38 24 L34 38 Z" fill="#F8FAFF" stroke="#9FC2DE" stroke-width="1.4" stroke-linejoin="round"/><path d="M2 20 L62 2 L34 28 Z" fill="#E8F0FF" opacity="0.85"/><path d="M34 38 L38 24 L34 28 Z" fill="#D8E4F8" opacity="0.9"/></svg>';
    fly.appendChild(p);
    planeState = {p, t0: performance.now(), phase: 'morph'};
    const img = fly.querySelector('img');
    function tick(){
      const st = planeState;
      const ET = performance.now() - st.t0;
      if(st.phase === 'morph'){
        const m = Math.min(1, ET / 360);
        const e = m * m * (3 - 2 * m);
        img.style.opacity = String(1 - e);
        p.style.opacity = String(e);
        fly.style.transform = 'translate(-50%,-50%) scale(' + (1 - e * 0.25) + ') rotate(' + (e * 0.9) + 'rad)';
        if(m >= 1){ st.phase = 'fly'; st.t0 = performance.now();
          img.style.display = 'none'; p.style.opacity = '1'; }
        requestAnimationFrame(tick);
        return;
      }
      if(st.phase === 'fly'){
        const ft = Math.min(1, ET / flyMs);
        const fall = ft >= 0.35 ? Math.pow((ft - 0.35) / 0.65, 1.6) : 0;
        const x = quadBez(start.x, start.x + (c.x - start.x) * 0.35, c.x, ft);
        const y = quadBez(start.y, start.y - 110, c.y + 18, ft);
        const pitch = -0.5 + (ft < 0.35 ? Math.sin(ft / 0.35 * Math.PI) * 0.2 : fall * 0.35);
        p.style.transform = 'rotate(' + pitch + 'rad)';
        fly.style.left = x + 'px'; fly.style.top = y + 'px';
        fly.style.transform = 'translate(-50%,-50%) scale(' + (ft > 0.85 ? Math.max(0.1, 1 - (ft - 0.85) * 3) : 1) + ')';
        if(ft >= 1){ st.phase = 'unmorph'; st.t0 = performance.now(); }
        requestAnimationFrame(tick);
        return;
      }
      /* unmorph: plane 淡出 → whale 淡入 */
      const m2 = Math.min(1, ET / 220);
      const e2 = m2 * m2 * (3 - 2 * m2);
      p.style.opacity = String(1 - e2);
      if(m2 > 0.9) img.style.display = 'block';
      img.style.opacity = String(m2);
      if(m2 >= 1){ finishReturn(fly); return; }
      requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }

  /* 扎入消失：冲刺拉伸 → 透视缩小 → 0.5s 空 → 从框中心喷出（dashMs=冲刺时长） */
  function zapPhase(fly, c, start, dashMs){
    dashMs = dashMs || 260;
    zapState = {t0: performance.now(), phase: 'dash',
                exit: {x: c.x + 34, y: c.y - 40}};
    const img = fly.querySelector('img');
    function tick(){
      const st = zapState;
      const ET = performance.now() - st.t0;
      if(st.phase === 'dash'){
        // 1) 冲刺（0~dashMs）：加速拉伸冲向右下，缩小透视
        const d = Math.min(1, ET / dashMs);
        const e = 1 - Math.pow(1 - d, 3);               // easeOutCubic 冲刺
        img.style.transform = 'scale(1,' + (1 - e * 0.8) + ')';  // 纵向拉长（冲刺拉伸）
        const x = start.x + (st.exit.x - start.x) * e;
        const y = start.y + (st.exit.y - start.y) * e;
        fly.style.left = x + 'px'; fly.style.top = y + 'px';
        fly.style.transform = 'translate(-50%,-50%) scale(' + (1 - e * 0.75) + ') rotate(' + (e * 0.6) + 'rad)';
        if(d >= 1){ st.phase = 'hole'; st.t0 = performance.now(); fly.style.opacity = '0'; }
        requestAnimationFrame(tick);
        return;
      }
      if(st.phase === 'hole'){
        // 2) 消失 0.5s（480ms），保持透明
        if(ET >= 480){
          st.phase = 'emit'; st.t0 = performance.now();
          img.style.display = 'block'; img.style.transform = '';
          fly.style.left = c.x + 'px'; fly.style.top = c.y + 'px';
        }
        requestAnimationFrame(tick);
        return;
      }
      // 3) 冒出（emit 0~300ms）：从框中心喷出，缩放超弹 + 回弹落稳
      const e3 = Math.min(1, ET / 300);
      const pop = 1 - Math.pow(1 - e3, 3);
      const scale = Math.max(0.1, 0.2 + pop * 1.1 - Math.sin(e3 * Math.PI) * 0.15);
      fly.style.opacity = '1';
      fly.style.transform = 'translate(-50%,-50%) scale(' + scale + ')';
      if(e3 >= 1){ finishReturn(fly); return; }
      requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }

  /* 小泡泡粒子（入框/冒出时） */
  function spawnBubbles(x, y){
    for(let i = 0; i < 7; i++){
      const b = document.createElement('i');
      b.className = 'whale-bubble';
      b.textContent = '·';
      const ang = Math.random() * Math.PI * 2, r0 = 6 + Math.random() * 10;
      b.style.left = x + Math.cos(ang) * r0 + 'px';
      b.style.top = y + Math.sin(ang) * r0 + 'px';
      document.body.appendChild(b);
      setTimeout(()=>{ b.style.opacity = '0'; b.style.transform = 'translate(' + Math.cos(ang) * 34 + 'px,' + (Math.sin(ang) * 34 - 10) + 'px) scale(1.6)'; }, 20);
      setTimeout(()=>b.remove(), 700);
    }
  }

  function quadBez(a, m, b, t){
    const u = 1 - t;
    return u * u * a + 2 * u * t * m + t * t * b;
  }
})();

/* ── 首次运行向导：厂商/模型/Key → 检测微信+勾选群 → 鼠标操作检测 → 完成 ── */
// 首次向导：页面生命周期内只弹一次（完成/跳过后不再弹，防止「完成→重载→又弹」循环）
let _onboardOnce = false;
async function onboarding(){
  if(!cfg || _onboardOnce) return;
  const key = getPath(cfg,'api.api_key') || '';
  // 已有真实 Key 或打码 Key（已配置）→ 不打扰；仅「无 Key/占位符」才显示向导
  if(key && key !== '******' && !key.includes('在这里填') && key.includes('••••')) return;  // 打码=已配置
  if(key && !key.includes('在这里填') && key !== '******' && !key.includes('••••')) return;  // 真实=已配置
  _onboardOnce = true;
  const m = document.createElement('div'); m.className='mask'; m.id='onboard';
  m.innerHTML='<div class="box" style="max-width:620px">'+ICON+'<h1>欢迎使用 wx-agent · 三步上手</h1>'+
    '<p id="obDesc">第 1 步/共 3 步：选择模型厂商 → 选择模型 → 填入该厂商的 API Key。</p>'+
    '<div class="mid" style="text-align:left">'+
      '<div class="row"><label>模型厂商</label><div class="grow"><select id="obProvider">'+
        Object.keys(PROVIDERS).filter(k=>k!=='custom').map(k=>'<option value="'+k+'">'+PROVIDERS[k].label+'</option>').join('')+
      '</select></div></div>'+
      '<div class="row"><label>模型</label><div class="grow"><select id="obModel"></select></div></div>'+
      '<div class="row"><label>API Key</label><div class="grow"><input type="password" id="obKey" placeholder="粘贴该厂商的 Key（如 sk-...）"><div class="hint" id="obKeyHint"></div></div></div>'+
    '</div>'+
    '<div id="obBody"></div>'+
    '<div class="btns" style="justify-content:center;margin-top:10px"><button class="pri" id="obNext">下一步</button><button class="ghost" id="obLater">跳过向导</button></div></div>';
  document.body.appendChild(m); maskOpen(m);
  let step = 1, picked = [];
  /* 厂商/模型联动（复用全局 PROVIDERS 与组件） */
  function obRenderModels(prov){
    const sel = $('obModel'); sel.innerHTML = '';
    const p = PROVIDERS[prov] || PROVIDERS.deepseek;
    p.models.forEach(mm=>{ const o=document.createElement('option'); o.value=mm; o.textContent=mm; sel.appendChild(o); });
    $('obKeyHint').textContent = p.keyHint ? ('Key 以「'+p.keyHint+'」开头；'+p.label+' 可在官网申请') : ('在 '+p.label+' 官网申请 Key');
  }
  $('obProvider').addEventListener('change', ()=>{ obRenderModels($('obProvider').value); });
  obRenderModels($('obProvider').value);
  // 预填现有 Key（如果有）
  const preKey = getPath(cfg,'api.api_key') || '';
  if(preKey && preKey !== '******' && !preKey.includes('••••') && !preKey.includes('在这里填')) $('obKey').value = preKey;
  $('obNext').onclick = async ()=>{
    try{
      if(step===1){
        const prov = $('obProvider').value;
        const p = PROVIDERS[prov];
        const k = $('obKey').value.trim();
        const model = $('obModel').value;
        if(k){ setPath(cfg,'api.api_key',k); }
        if(k && !k.includes('••••') && !k.startsWith('sk-***')){
          if(!cfg.api.provider_keys) cfg.api.provider_keys = {};
          cfg.api.provider_keys[prov] = k;
        }
        setPath(cfg,'api.model',model);
        setPath(cfg,'api.base_url', p.base);
        if(cfg.api.provider) cfg.api.provider = '';   // 走顶层 base_url/api_key（向导场景）
        await getJSON('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(cfg)});
        toast('已保存 '+p.label+' 配置（'+model+'）');
        const r = await getJSON('/api/wechat-groups');
        const groups = r.groups||[];
        $('obDesc').textContent = '第 2 步/共 3 步：勾选需要机器人监听的群（全不勾=监听所有群）。检测到 '+groups.length+' 个群聊。';
        $('obProvider').closest('.mid').style.display='none';
        const body=$('obBody'); body.innerHTML='';
        picked = [];  // 重新开始（防重复调用残留）
        const pickSet = new Set((wlList||[]));
        renderGroupList(body, groups, pickSet, (s)=>{ picked = [...s]; });
        picked = [...pickSet];
        if(groups.length){ body.querySelector('.group-search').focus(); }
        $('obNext').textContent='下一步'; step=2; return;
      }
      if(step===2){
        if(picked.length){ wlList = picked.slice(); setPath(cfg,'wechat.group_name_white_list', wlList.slice()); await getJSON('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(cfg)}); renderChips(); }
        $('obDesc').textContent = '第 3 步/共 3 步：代码与依赖检测（不动鼠标，几秒完成：环境/依赖/微信接入/配置逐项检查）。需要更多「鼠标操作检测」可在检测中心用单独按钮。';
        $('obBody').innerHTML='<pre class="out" id="obCheck" style="height:190px">体检中…</pre>';
        $('obNext').textContent='完成'; step=3;
        const r = await getJSON('/api/selfcheck',{method:'POST',headers:{'Content-Type':'application/json'},body:'{"mode":"code"}',timeoutMs:60000});
        let lines=[r.summary||'',''];
        for(const c of (r.checks||[])){
          lines.push((c.status==='ok'?'✅':(c.status==='warn'?'⚠️':'❌'))+' '+c.name+'：'+c.detail);
          if(c.hint) lines.push('   建议：'+c.hint);
        }
        $('obCheck').textContent = lines.join('\n');
        return;
      }
      if(step===3){
        maskClose(m); m.remove();
        _onboardOnce = true;  // 完成：不再弹（即便 Key 仍空也不再打扰）
        loadStatus();
        try{ cfg = await getJSON('/api/config'); syncToForm(); }catch(e){}
        loadMemory(''); toast('🎉 部署完成！');
      }
    }catch(e){ toast('出错：'+e.message); }
  };
  $('obLater').onclick = ()=>{ maskClose(m); m.remove(); _onboardOnce = true; };
}

/* 事件绑定 */
$('api.temperature').addEventListener('input',()=>$('api.temperature-v').textContent=$('api.temperature').value);
document.querySelectorAll('[data-save]').forEach(b=> b.addEventListener('click', ()=>saveAllBtn(b)));
$('saveAll').onclick = ()=>saveAllBtn();
$('refreshLog').onclick = loadLog;
$('balance-badge').onclick = loadBalance;
$('rawJsonBtn').onclick = ()=>{ window.open('/api/config'+(URL_TOKEN?('?token='+URL_TOKEN):''),'_blank'); };
$('pauseBtn').onclick = async ()=>{
  try{ await getJSON($('pauseBtn').textContent.includes('暂停')?'/api/pause':'/api/resume',{method:'POST'}); loadStatus(); }catch(e){toast(e.message)}
};
/* ── 通用确认弹窗（mask + box + 果冻图标），替代原生 confirm ── */
function confirmBox(title, lines, okLabel, onOk, danger){
  const m = document.createElement('div'); m.className='mask';
  const lh = (lines||[]).map(s=>'<p style="text-align:left;margin:4px 0">'+s+'</p>').join('');
  m.innerHTML = '<div class="box">'
    + ICON.replace('whale-badge big', 'whale-badge big')   // 果冻动画由 _iconCss 自动触发
    + '<h1>'+title+'</h1>'+lh
    + '<div class="btns" style="justify-content:center">'
    + '<button class="'+(danger?'danger':'pri')+'" id="cboxOk">'+okLabel+'</button>'
    + '<button class="ghost" id="cboxNo">取消</button></div></div>';
  document.body.appendChild(m); maskOpen(m);
  $('cboxOk').onclick = ()=>{ maskClose(m); m.remove(); onOk && onOk(); };
  $('cboxNo').onclick = ()=>{ maskClose(m); m.remove(); };
  return m;
}

$('stopBtn').onclick = ()=>{
  confirmBox('停止机器人？', [
    '将同时停止 <b>机器人 + 看门狗</b>（都不会再自动拉起，也不会再弹新控制台）。',
    '停止后控制台将不再自动刷新状态；<b>启动机器人.vbs</b> 可随时重新启动。',
    '微信窗口保持打开即可，机器人不会再发消息；群聊数据不会丢失。',
  ], '确认停止', async ()=>{
    try{
      await getJSON('/api/shutdown',{method:'POST'});
      toast('已发出停止指令，机器人即将退出…');
      $('dot').className='dot';
      // 展示停止详情弹窗
      const m = confirmBox('机器人已停止 ✔', [
        '已结束全部进程（机器人 + 看门狗）。',
        '想再次运行：双击项目根目录 <b>启动机器人.vbs</b>，或点「重启」按钮。<br>',
        '控制台将自动关闭；日志已保存在 logs\\wx_agent.log。',
      ], '知道了');
      /* 关闭自动 tag */
      setTimeout(()=>{ try{ if(!window.closed) window.close(); }catch(_e){} }, 4000);
    }catch(e){
      toast('停止指令未送达（机器人可能已经不在运行）——页面稍后会显示「机器人已停止」');
    }
  });
};
$('restartBtn').onclick = ()=>{
  confirmBox('重启机器人？', [
    '先在后台启动一个新实例（<b>2 秒左右</b>），然后旧实例自动退出——无缝接替，<b>不中断监听</b>。',
    '新实例会重新打开控制台标签（若自动打开已开启）。',
  ], '确认重启', async ()=>{
    try{
      await getJSON('/api/restart',{method:'POST'});
      toast('已发出重启指令，等待新实例接管…');
    }catch(e){ toast('重启失败：'+e.message); }
  });
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
async function runCodeCheck(deps){
  const btn=$('codeCheck'), btn2=$('codeCheckDeps');
  if(btn){ btn.disabled=true; btn.textContent='检测中…'; }
  if(btn2){ btn2.disabled=true; btn2.textContent='检测中…'; }
  const pre = $('codeResult') || (()=>{
    const p2=document.createElement('pre'); p2.className='out'; p2.id='codeResult';
    document.getElementById('selfCheckResult').insertAdjacentElement('beforebegin', p2);
    return p2; })();
  pre.classList.remove('dn');
  $('codeCheckTip').textContent='代码检测启动中…';
  try{
    await getJSON('/api/code-check',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({deps:!!deps}),timeoutMs:15000});
    // 轮询进度（实时逐项：概览状态条显示 百分比+当前项；结果面板逐项滚动）
    let done=false, r=null, lastItems='';
    for(let i=0;i<300 && !done;i++){
      const pr = await getJSON('/api/code-check/progress',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}',timeoutMs:10000});
      const prg = (pr&&pr.progress)||{};
      const d=prg.done||0, t=prg.total||0, cur=prg.current||'';
      if(pr && pr.done){ done=true; r=pr.result; }
      $('codeCheckTip').textContent = done ? '代码检测完成' : ('检测中 '+(t?Math.round(d/t*100):0)+'% · '+cur);
      const items = (pr&&pr.items)||[];
      if(items.length){
        const key = JSON.stringify(items.map(x=>x.status+x.name));
        if(key !== lastItems){
          lastItems = key;
          const lines = ['===== 代码检测 '+(done?'':'（进行中…）')+' =====',''];
          for(const c of items){
            const mark = c.status==='ok'?'✅':(c.status==='warn'?'⚠️':(c.status==='fail'?'❌':'ℹ️'));
            lines.push(mark+' '+c.name+'：'+c.detail);
          }
          pre.textContent = lines.join('\n');
          pre.scrollTop = pre.scrollHeight;   // 逐项实时滚动
        }
      }
      await new Promise(res=>setTimeout(res,150));
    }
    let lines = r ? ['===== 代码检测 =====', r.summary||'', ''] : ['===== 代码检测 =====','（仍在检测中，请稍后再查看）'];
    for(const c of (r&&r.checks||[])){
      const mark = c.status==='ok'?'✅':(c.status==='warn'?'⚠️':(c.status==='fail'?'❌':'ℹ️'));
      lines.push(mark+' '+c.name+'：'+c.detail);
      if(c.hint) lines.push('    建议：'+c.hint);
    }
    pre.textContent = lines.join('\n');
    $('codeCheckTip').textContent = r ? ('✅ 代码检测完成：' + (r.summary||'')) : '代码检测仍在进行中…';
    if(r) toast('✅ 代码检测完成：'+(r.summary||'')); else toast('代码检测仍在进行中…', 4000);
  }catch(e){
    pre.textContent='代码检测失败：'+e.message;
    $('codeCheckTip').textContent='❌ 代码检测失败：'+e.message;
  }finally{
    if(btn){ btn.disabled=false; btn.textContent='代码检测'; }
    if(btn2){ btn2.disabled=false; btn2.textContent='代码检测＋依赖核对'; }
  }
}
$('codeCheck').onclick = ()=>runCodeCheck(false);
if($('codeCheckDeps')) $('codeCheckDeps').onclick = ()=>runCodeCheck(true);

$('selfCheck').onclick = async ()=>{
  const btn=$('selfCheck'); btn.disabled=true;
  if($('selfCheckStop')) $('selfCheckStop').disabled=false;
  const pre=$('selfCheckResult'); pre.classList.remove('dn');
  pre.textContent='鼠标操作检测中（约 40~70 秒：环境/配置/点击 + 程序鼠标操作，期间请勿动鼠标；可随时点「停止检测」）…';
  try{
    const r = await getJSON('/api/selfcheck',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}',timeoutMs:180000});
    let lines=['===== 鼠标操作检测 =====', r.summary||'', ''];
    for(const c of (r.checks||[])){
      const mark = c.status==='ok'?'✅':(c.status==='warn'?'⚠️':(c.status==='fail'?'❌':'ℹ️'));
      lines.push(mark+' '+c.name+'：'+c.detail);
      if(c.hint) lines.push('    建议：'+c.hint);
    }
    if(r.cancelled) lines.push('\n（检测已被手动停止）');
    pre.textContent = lines.join('\n');
    if(r.cancelled) toast('✅ 已停止检测');
  }catch(e){ pre.textContent='检测失败：'+e.message; }
  finally{ btn.disabled=false; if($('selfCheckStop')) $('selfCheckStop').disabled=true; }
};
if($('selfCheckStop')) $('selfCheckStop').onclick = async ()=>{
  try{
    await getJSON('/api/selfcheck-stop',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    toast('已发出停止请求（当前检测项跑完即停）');
  }catch(e){ toast('停止失败：'+e.message); }
};
$('pokeTest').onclick = async ()=>{
  const btn=$('pokeTest'); btn.disabled=true;
  const only = $('pokeVerifyOnly').checked;
  $('uiTestResult').textContent=(only?'简易检测中':'完整执行中')+'（约 10~25 秒，请勿动鼠标）…'; $('uiTestDetail').textContent='';
  try{
    const r = await getJSON('/api/poke-test',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({group_wxid: $('pokeGroup').value||'', verify_only:only})});
    $('uiTestResult').textContent = (r.ok?'✅ ':'❌ ')+(r.message||r.error||'(无结果)');
    $('uiTestDetail').textContent = '目标：'+(r.target?(r.target.name+' / '+r.target.id+' 在群「'+(r.group||'?')+'」'):'未解析')+
      (r.verify_only?'\n（简易模式：仅验证菜单可弹，未实际拍）':'')+
      '\n步骤：\n'+((r.steps||[]).join('\n')||(r.error||''));
  }catch(e){ $('uiTestResult').textContent='❌ '+e.message; }
  finally{ btn.disabled=false; }
};
/* 拍一拍目标群下拉（填充监听目标群） */
/* 共享记忆群选择：勾选哪些群互享记忆 */
let _memGroups = [];
async function loadMemGroups(){
  const box = $('memGroupsBox'); if(!box) return;
  try{
    _memGroups = (await getJSON('/api/wechat-groups')).groups || [];
    const sel = getPath(cfg,'memory.shared_groups') || [];
    box.innerHTML = '';
    if(!_memGroups.length){ box.innerHTML = '<span class="hint">未检测到群（启动机器人并检测群后这里会列出）</span>'; return; }
    _memGroups.forEach(g=>{
      const nm = g.name || g.nick || g.wxid || '';
      const lab = document.createElement('label');
      lab.style.cssText = 'display:inline-flex;align-items:center;gap:4px;padding:4px 9px;border:1px solid var(--bd);border-radius:10px;background:var(--input-bg);cursor:pointer';
      const ck = document.createElement('input'); ck.type = 'checkbox'; ck.className = 'memGroupCk';
      ck.checked = sel.some(s=>s===nm || s===g.wxid);
      lab.appendChild(ck); lab.appendChild(document.createTextNode(' '+nm));
      box.appendChild(lab);
    });
  }catch(e){ box.innerHTML = '<span class="hint">群列表读取失败：'+e.message+'</span>'; }
}

/* ── 控制台内弹窗（替代浏览器原生 alert/confirm/prompt）── */
function uiConfirm(msg){
  return new Promise((res)=>{
    const m = confirmBox('确认操作', [msg], '确定', ()=>res(true), true);
    const no = m.querySelector('#cboxNo');
    if(no) no.onclick = ()=>{ maskClose(m); m.remove(); res(false); };
  });
}
function uiPrompt(msg, def){
  return new Promise((res)=>{
    const m = document.createElement('div'); m.className='mask';
    m.innerHTML = '<div class="box">'
      + '<h1>输入</h1><p style="text-align:left;margin:6px 0">'+msg+'</p>'
      + '<input id="uiPromptInput" class="inp" value="'+String(def||'')+'" style="width:100%;margin:6px 0">'
      + '<div class="btns" style="justify-content:center">'
      + '<button class="pri" id="uiPromptOk">确定</button>'
      + '<button class="ghost" id="uiPromptNo">取消</button></div></div>';
    document.body.appendChild(m); maskOpen(m);
    const inp = m.querySelector('#uiPromptInput');
    inp.focus(); inp.select();
    m.querySelector('#uiPromptOk').onclick = ()=>{ const v=inp.value.trim(); maskClose(m); m.remove(); res(v||null); };
    m.querySelector('#uiPromptNo').onclick = ()=>{ maskClose(m); m.remove(); res(null); };
  });
}

function syncMemGroupsToCfg(){
  if(!cfg) return;
  const names = [];
  document.querySelectorAll('.memGroupCk').forEach((ck,i)=>{
    if(ck.checked && _memGroups[i]) names.push(_memGroups[i].name || _memGroups[i].nick || _memGroups[i].wxid || '');
  });
  if(!cfg.memory) cfg.memory = {};
  cfg.memory.shared_groups = names;
}
(function(){
  const saveBtn = document.querySelector('[data-save]');
  // 所有数据保存前并入 shared_groups（追加在 saveAllBtn 内的 wsSyncFromForm 后）
  const _orig = window.syncMemGroupsToCfg;
  window.addEventListener('load', ()=>loadMemGroups());
})();

/* ── 自定义角色卡：模型评分 / 模型补足 ── */
(async function(){
  const btn = document.getElementById('pScoreLLM');
  const en = document.getElementById('pEnrich');
  const rst = document.getElementById('pScoreRst');
  if(!btn) return;
  function curText(){ const ta = document.querySelector('[data-cfg="persona.role_text"]'); return ta ? ta.value : ''; }
  btn.onclick = async ()=>{
    if(!document.getElementById('pUseLlm').checked){ rst.textContent = '未勾选"允许模型处理"——本地规则无法保证贴合度，评分需模型参与（勾选后点此）'; return; }
    const t = curText();
    if(!t.trim()){ rst.textContent = '请先填写角色文本（或从选单选一个）'; return; }
    rst.textContent = '模型评分中（约 10~30 秒）…';
    try{
      const r = await getJSON('/api/persona/score',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:t, llm:true})});
      if(r.ok) rst.textContent = '✅ 模型评分 '+Number(r.score).toFixed(1)+' 分　'+(r.reason||'');
      else rst.textContent = '评分失败：'+(r.error||'');
    }catch(e){ rst.textContent = '评分失败：'+e.message; }
  };
  en.onclick = async ()=>{
    const t = curText();
    const name = ((document.querySelector('[data-cfg="persona.bot_name"]')||{}).value||'').trim();
    if(!name){ rst.textContent = '请先填「人设名」（模型按角色名联网整理设定）'; return; }
    const rounds = parseInt((document.getElementById('pRounds')||{}).value || '1');
    rst.textContent = '模型补足中（'+rounds+' 轮，每轮 10~30 秒，按人设贴近度修正）…';
    try{
      const r = await getJSON('/api/persona/ai-enrich',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name, text:t, rounds})});
      if(r.ok){
        const ta = document.querySelector('[data-cfg="persona.role_text"]');
        if(ta){ ta.value = r.text; }
        const tr = (r.trace||[]).map(x=>'第'+x.round+'轮:'+(x.score!=null?x.score.toFixed(2)+'分':'—')).join(' → ');
        rst.textContent = '✅ 补足完成（'+tr+'）'+(r.score!=null?' 最终 '+r.score.toFixed(2)+' 分':'')+'——点下方「保存」落盘后生效';
        toast('✅ 补足完成，记得保存');
      } else rst.textContent = '模型失败：'+(r.error||'');
    }catch(e){ rst.textContent = '模型失败：'+e.message; }
  };
})();

/* ── 角色评分表（已并入人设选单 v2 卡片；此块仅保留导出入口）── */
(async function(){
  const exp = document.getElementById('rateExport');
  if(!exp) return;
  exp.onclick = async ()=>{
    try{
      const r = await getJSON('/api/community/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:'persona_ratings'})});
      toast(r.ok ? '✅ 评分已导出' : '导出失败');
    }catch(e){ toast('导出失败：'+e.message); }
  };
})();

/* ── 人设选单 v2（分区 chips + 两行卡[系统分/用户打分/描述] + ➕新建分区/添加角色）── */
(async function(){
  const box = document.getElementById('personaList');
  if(!box) return;
  let list = [], customs = [], scores = {}, userCats = {}, builtCats = [], curCat = '', sortByScore = false, favs = {};
  try{
    const r = await getJSON('/api/personas');
    list = r.personas || [];
    const rc = await getJSON('/api/personas/custom');
    customs = (rc.custom || []).map(c=>({key:c.key, name:c.name, text:c.text, cat:c.cat || '📝 自定义'}));
    try{
      const rs = await getJSON('/api/personas/scores');
      (rs.rows||[]).forEach(x=>{ scores[x.key] = x; });
    }catch(e){}
    try{ const rf = await getJSON('/api/personas/favs'); favs = rf.favs || {}; }catch(e){}
    try{
      const rc2 = await getJSON('/api/persona/cats');
      builtCats = rc2.built || [];
      (rc2.user||[]).forEach(c=>{ userCats[c.name] = c.desc || ''; });
    }catch(e){}
  }catch(e){ box.innerHTML = '<span class="hint">读取失败：'+e.message+'</span>'; return; }
  async function rc_load(){
    try{ const rc = await getJSON('/api/personas/custom'); customs = (rc.custom||[]).map(c=>({key:c.key,name:c.name,text:c.text,cat:c.cat||'📝 自定义'})); render(); }
    catch(e){}
  }
  function allCats(){
    const cs = {};
    list.forEach(p=>{ cs[p.cat || '🔥 网络热门'] = true; });
    customs.forEach(p=>{ cs[p.cat || '📝 自定义'] = true; });
    Object.keys(userCats).forEach(c=>{ cs[c] = true; });
    return Object.keys(cs);
  }
  function renderChips(){
    const wrap = document.getElementById('personaCats'); if(!wrap) return;
    wrap.querySelectorAll('.pCatChip').forEach(c=>c.remove());
    allCats().forEach(c=>{
      const b = document.createElement('button');
      b.className = 'pCatChip ghost';
      b.style.cssText = 'padding:2px 12px;border-radius:20px;'+(curCat===c?'background:var(--blue-soft);color:var(--blue);font-weight:700':'');
      b.textContent = c;
      b.onclick = ()=>{ curCat = (curCat===c?'':c); renderChips(); render(); };
      wrap.insertBefore(b, document.getElementById('pCatAdd'));
      // 用户分区：右键/小 ✕ 删除
      const isBuilt = builtCats.includes(c);
      const isCustomDefault = c.indexOf('📝') >= 0;
      if(!isBuilt && !isCustomDefault){
        const x = document.createElement('span');
        x.textContent = ' ✕';
        x.style.color = 'var(--err-tx)';
        x.title = '删除分区「'+c+'」（分区下的自定义卡会移回 📝 自定义）';
        x.onclick = async (ev)=>{
          ev.stopPropagation();
          if(!await uiConfirm('删除分区「'+c+'」？其中自定义角色会自动移回「📝 自定义」。')) return;
          try{
            await getJSON('/api/persona/cats/del',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:c})});
            delete userCats[c];
            if(curCat === c) curCat = '';
            toast('✅ 分区「'+c+'」已删除'); renderChips(); rc_load();
          }catch(e){ toast('删除失败：'+e.message); }
        };
        b.appendChild(x);
      }
    });
  }
  function stars(key, n, editable){
    let s = '';
    for(let i=1;i<=5;i++){
      const on = n >= i;
      s += '<span data-k="'+key+'" data-v="'+i+'" style="cursor:'+(editable?'pointer':'default')+';font-size:15px;margin:0 1px;color:'+(on?'#FFD54F':'var(--tx2)')+'">'+(on?'★':'☆')+'</span>';
    }
    return s;
  }
  function renderChips(){
    const wrap = document.getElementById('personaCats'); if(!wrap) return;
    wrap.querySelectorAll('.pCatChip').forEach(c=>c.remove());
    allCats().forEach(c=>{
      const b = document.createElement('button');
      b.className = 'pCatChip ghost';
      b.style.cssText = 'padding:2px 12px;border-radius:20px;'+(curCat===c?'background:var(--blue-soft);color:var(--blue);font-weight:700':'');
      b.textContent = c;
      b.onclick = ()=>{ curCat = (curCat===c?'':c); renderChips(); render(); };
      wrap.insertBefore(b, document.getElementById('pCatAdd'));
    });
  }
  function render(){
    const q = ((document.getElementById('personaSearch')||{}).value || '').trim().toLowerCase();
    const all = list.concat(customs);
    let show = all.filter(p => !curCat || (p.cat||'🔥 网络热门') === curCat);
    show = show.filter(p => !q || p.name.includes(q) || (p.key||'').includes(q) || (p.text||'').includes(q));
    // ① 星标置顶（始终最上） ② 按评估分排序（当前视图）
    if(sortByScore){
      show = show.slice().sort((a,b)=> ((scores[b.key]||{}).model||0) - ((scores[a.key]||{}).model||0));
    }
    show = show.slice().sort((a,b)=> ((favs[a.key]?0:1) - (favs[b.key]?0:1)));
    box.innerHTML = '';
    if(!show.length){ box.innerHTML = '<span class="hint">没有匹配</span>'; return; }
    show.forEach(p=>{
      const sc = scores[p.key] || {};
      const model = sc.model;
      const fav = !!favs[p.key];
      const card = document.createElement('div');
      card.style.cssText = 'border:1px solid var(--bd);border-radius:10px;margin:4px 0;padding:7px 9px;position:relative';
      card.onmouseenter = ()=> card.style.background = 'var(--hover-bg)';
      card.onmouseleave = ()=> card.style.background = '';
      card.innerHTML =
        '<div style="display:flex;align-items:center;gap:8px">'+
          '<button class="ghost fav" title="'+(fav?'取消星标':'收藏置顶')+'" style="padding:0 6px;color:'+(fav?'#FFD54F':'var(--tx2)')+'">'+(fav?'★':'☆')+'</button>'+
          '<b style="flex:1">🐟 '+esc(p.name)+'</b>'+
          (model!=null?('<span class="hint">模型 <b style="color:var(--warn)">'+Number(model).toFixed(2)+'</b></span>'):'')+
          (p.key && String(p.key).indexOf('custom_')===0 ? '<button class="ghost move" style="padding:1px 8px">移动</button><button class="ghost del" style="padding:1px 8px;color:var(--err-tx)">删</button>' : '')+
          '<button class="ghost use" style="padding:1px 12px">使用</button>'+
          '<span class="dots" title="更多操作">⋯</span>'+
        '</div>'+
        '<div class="hint" style="margin-top:3px">'+(p.text||'').replace(/\n/g,' ').slice(0,60)+(p.text&&p.text.length>60?'…':'')+'</div>';
      const dots = card.querySelector('.dots');
      dots.style.cssText = 'cursor:pointer;color:var(--tx2);padding:0 4px;font-weight:700;transform:rotate(90deg);display:inline-block';
      dots.onclick = (ev)=>{
        ev.stopPropagation();
        const menu = document.createElement('div');
        menu.className = 'dsel-menu dn';
        menu.style.cssText = 'position:absolute;right:6px;top:22px;z-index:60;display:block';
        const items = [
          ['为模型打星', async ()=>{ await getJSON('/api/personas/rate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:p.key, score:5})}); toast('✅ 已为「'+p.name+'」打星'); render(); }],
          ['编辑角色卡', async ()=>{ const ta=document.querySelector('[data-cfg="persona.role_text"]'); if(ta){ ta.value=p.text; } syncToForm(); toast('已填入编辑区，点保存生效'); }],
          ['导出其评分', async ()=>{ await getJSON('/api/community/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:'persona_ratings'})}); toast('✅ 评分已导出'); }],
          ['复制角色名', async ()=>{ await navigator.clipboard.writeText(p.name); toast('已复制'); }],
        ];
        menu.innerHTML = items.map((x,i)=>'<li data-i="'+i+'">'+x[0]+'</li>').join('');
        card.appendChild(menu);
        menu.querySelectorAll('li').forEach((li,i)=>{ li.onclick = async (ev)=>{ ev.stopPropagation(); menu.remove(); items[i][1](); }; });
        setTimeout(()=>{ document.addEventListener('click', ()=>menu.remove(), {once:true}); }, 0);
      };
      const favBtn = card.querySelector('.fav');
      favBtn.onclick = async (ev)=>{
        ev.stopPropagation();
        try{
          await getJSON('/api/personas/fav',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:p.key, fav:!fav})});
          favs[p.key] = !fav; render();
        }catch(e){ toast('星标失败：'+e.message); }
      };
      box.appendChild(card);
      const useBtn = card.querySelector('.use');
      useBtn.onclick = async (ev)=>{
        ev.stopPropagation();
        try{
          if(!cfg.persona) cfg.persona = {};
          // 备份上一个应用的人设（一键恢复用）
          const prev = {name: getPath(cfg,'persona.bot_name')||'', text: getPath(cfg,'persona.role_text')||''};
          cfg.persona.last_used = prev;
          cfg.persona.bot_name = p.name;
          cfg.persona.role_text = p.text;
          await getJSON('/api/config', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(cfg)});
          cfg = await getJSON('/api/config');
          syncToForm();
          toast('✅ 已切换人设「'+p.name+'」并保存（重启机器人后生效）');
        }catch(e){ toast('应用失败：'+e.message); }
      };
      const delBtn = card.querySelector('.del');
      if(delBtn) delBtn.onclick = async (ev)=>{
        ev.stopPropagation();
        if(!await uiConfirm('删除「'+p.name+'」？')) return;
        try{
          await getJSON('/api/personas/custom/del',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:p.key})});
          toast('已删除'); rc_load();
        }catch(e){ toast('删除失败：'+e.message); }
      };
      const mvBtn = card.querySelector('.move');
      if(mvBtn) mvBtn.onclick = async (ev)=>{
        ev.stopPropagation();
        const cats = allCats().join('、');
        const cat = await uiPrompt('移到哪个分区？可填已有分区（'+cats+'）或输入新名字自动新建', p.cat);
        if(cat===null) return;
        if(!cat.trim()){ toast('分区名不能为空'); return; }
        try{
          await getJSON('/api/personas/custom',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:p.key,name:p.name,text:p.text,cat:cat.trim()})});
          toast('✅ 已移到「'+cat.trim()+'」'); rc_load();
        }catch(e){ toast('移动失败：'+e.message); }
      };
    });
  }
  /* 排序按钮：WPS 式点击切换（正序/倒序），图标随状态变化 */
  (function(){
    const s = document.getElementById('pSort'), so = document.getElementById('pSortOff');
    const hint = document.getElementById('pSortHint');
    if(s){
      s.onclick = ()=>{ 
        sortByScore = !sortByScore;         // 点一下正序，再点一下倒序
        s.textContent = sortByScore ? '↑ 按评估分数排序' : '↓ 按评估分数排序';
        if(hint) hint.textContent = sortByScore ? '（切换为：低→高；再点恢复高→低）' : '（点一下正序，再点一下倒序）';
        render(); 
        toast(sortByScore ? '已按评估分低→高（倒序）' : '已按评估分高→低（正序）');
      };
      if(sortByScore) s.textContent = '↑ 按评估分数排序';
    }
    if(so) so.onclick = ()=>{ sortByScore = false; if(s) s.textContent = '↓ 按评估分数排序'; if(hint) hint.textContent = '（点一下正序，再点一下倒序）'; render(); };
    // 恢复上个人设：从 cfg.persona.last_used 读回（应用人设时自动备份）
    const rp = document.getElementById('pRestorePrev');
    if(rp){
      rp.onclick = async ()=>{
        const prev = getPath(cfg,'persona.last_used') || {};
        if(!prev.name && !prev.text){ toast('还没有可恢复的人设（先应用过一次）'); return; }
        if(!await uiConfirm('恢复上个人设「'+ (prev.name||'未命名') +'」？当前人设将被替换。')) return;
        try{
          if(!cfg.persona) cfg.persona = {};
          // 当前人设备份（再点恢复一次可回到它？不，保持单向：恢复后 last_used=当前，避免循环）
          cfg.persona.bot_name = prev.name || '';
          cfg.persona.role_text = prev.text || '';
          const r = await getJSON('/api/config', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(cfg)});
          if(r && r.ok === false){ toast('恢复失败：'+(r.error||'')); return; }
          cfg = await getJSON('/api/config'); syncToForm();
          toast('✅ 已恢复上个人设「'+(prev.name||'未命名')+'」并保存（重启后生效）');
        }catch(e){ toast('恢复失败：'+e.message); }
      };
    }
  })();
  /* ➕ 新建分区 / 添加角色（弹出菜单；分区名下拉=已有分区+自定义，选自定义才让输入；选已有自动匹配描述） */
  const addBtn = document.getElementById('pCatAdd');
  if(addBtn) addBtn.onclick = ()=>{
    const box2 = document.createElement('div'); box2.className = 'box'; box2.style.textAlign = 'left';
    const cats = allCats();
    // 下拉：已有分区（内置 + 用户自定义）最后一项=自定义
    const catOpts = builtCats.concat(Object.keys(userCats));
    const allOpts = [];
    catOpts.forEach((c, i)=>{ allOpts.push('<option value="'+esc(c)+'">'+esc(c)+'</option>'); });
    allOpts.push('<option value="__custom__">✏️ 自定义…</option>');
    const catSelHtml = '<select id="pAddCatSel" class="dsel-native">' + allOpts.join('') + '</select>';
    box2.innerHTML =
      '<h1>➕ 新建分区 / 添加到分区</h1>'+
      '<div class="row"><label>类型</label><div class="grow"><select id="pAddType"><option value="cat">新建分区</option><option value="persona">添加角色到分区</option></select></div></div>'+
      '<div class="row" id="pAddCatRow"><label>分区名</label><div class="grow">'+catSelHtml+'<input id="pAddCat" class="dn" placeholder="自定义分区名（如 🎮 我的游戏）"></div></div>'+
      '<div class="row" id="pAddDescRow"><label>分区描述</label><div class="grow"><input id="pAddDesc" placeholder="这分区的角色都是什么（可选；新建分区时填）"></div></div>'+
      '<div class="row" id="pAddNameRow" style="display:none"><label>角色名</label><div class="grow"><input id="pAddName" placeholder="角色名"></div></div>'+
      '<div class="row" id="pAddTextRow" style="display:none"><label>角色文本</label><div class="grow"><textarea id="pAddText" rows="5" placeholder="角色设定（会交补足引擎+评分）"></textarea></div></div>'+
      '<div class="hint" id="pAddCats" style="margin:4px 0">已有分区：'+(catOpts.join('、'))+'</div>'+
      '<div class="btns" style="justify-content:flex-end;margin-top:8px"><button class="pri" id="pAddOk">创建</button><button class="ghost" id="pAddCancel">取消</button></div>';
    const mm = document.createElement('div'); mm.className = 'mask'; mm.appendChild(box2);
    document.body.appendChild(mm); maskOpen(mm);
    const typeSel = box2.querySelector('#pAddType');
    const catSel = box2.querySelector('#pAddCatSel');
    const catInput = box2.querySelector('#pAddCat');
    const descInput = box2.querySelector('#pAddDesc');
    function catValue(){
      if(catSel.value === '__custom__'){
        catInput.classList.remove('dn'); return (catInput.value || '').trim();
      }
      catInput.classList.add('dn');
      return catSel.value;
    }
    typeSel.onchange = ()=>{
      const isP = typeSel.value === 'persona';
      box2.querySelector('#pAddNameRow').style.display = isP?'':'none';
      box2.querySelector('#pAddTextRow').style.display = isP?'':'none';
      // 添加角色到分区时：分区名必须选已有/自定义；新建分区时固定「自定义」输入
      if(isP){
        catSel.disabled = false;
        // 恢复下拉（而不是强制自定义）
      }else{
        catSel.value = '__custom__'; catInput.classList.remove('dn'); catSel.disabled = true;
      }
    };
    // 选已有分区 → 自动带出分区描述；描述输入仅在新建分区时可用
    catSel.onchange = ()=>{
      const v = catSel.value;
      if(v === '__custom__'){ catInput.classList.remove('dn'); descInput.disabled = false; return; }
      catInput.classList.add('dn');
      const d = userCats[v] || '';
      descInput.value = d;
      descInput.disabled = true;
    };
    // 初始：新建分区模式 → 自定义输入
    catSel.value = '__custom__'; catInput.classList.remove('dn'); catSel.disabled = true;
    box2.querySelector('#pAddCancel').onclick = ()=>{ maskClose(mm); mm.remove(); };
    box2.querySelector('#pAddOk').onclick = async ()=>{
      const cat = catValue();
      if(!cat){ toast('分区名不能为空'); return; }
      try{
        if(typeSel.value === 'cat'){
          const desc = (box2.querySelector('#pAddDesc').value||'').trim();
          await getJSON('/api/persona/cats/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:cat, desc})});
          userCats[cat] = desc;
          toast('✅ 分区「'+cat+'」已创建（在分区栏可看/删除）');
          renderChips();
        }else{
          const name = (box2.querySelector('#pAddName').value||'').trim();
          const text = (box2.querySelector('#pAddText').value||'').trim();
          if(!name || !text){ toast('角色名和文本都要填'); return; }
          // 自定义新分区名时先落盘（加入「自定义分区」列表）
          if(catSel.value === '__custom__' && cat && !catOpts.includes(cat)){
            const desc = (box2.querySelector('#pAddDesc').value||'').trim();
            await getJSON('/api/persona/cats/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:cat, desc})});
            userCats[cat] = desc;
          }
          const r = await getJSON('/api/personas/custom',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name, text, cat})});
          if(r.ok){
            toast('✅ 角色「'+name+'」已加入分区「'+cat+'」');
            renderChips(); rc_load();
          }
          else toast('失败：'+(r.error||''));
        }
        maskClose(mm); mm.remove();
      }catch(e){ toast('失败：'+e.message); }
    };
  };
  if(document.getElementById('personaSearch')) document.getElementById('personaSearch').addEventListener('input', render);
  renderChips(); render();
})();

/* 角色卡 → 行为档位推荐（模型多维度评估 + 本地兜底；应用后写入 cfg 并即时生效） */
(async function(){
  const btn = $('roleHintBtn');
  if(!btn) return;
  btn.onclick = async ()=>{
    const txt = document.querySelector('[data-cfg="persona.role_text"]');
    const rt = txt ? (txt.value || '') : '';
    $('roleHintRst').textContent = '正在按角色卡评估行为档（模型多维度）…';
    try{
      const res = await getJSON('/api/persona/behavior-recommend',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({text:rt.slice(0,2400)})});
      if(res && res.error){ $('roleHintRst').textContent = '评估失败：'+res.error; return; }
      const part = res.participation || 'medium', st = (res.sticker ?? 0);
      // 关键：enhanceSelects 自绘下拉只监听原生 change，程序赋值需手动触发刷新按钮文字（否则显示旧值）
      const pSel = $('roleHintPart'), sSel = $('roleHintSticker');
      pSel.value = part;   sSel.value = String(st);
      try{ pSel.dispatchEvent(new Event('change', {bubbles:true})); }catch(e){}
      try{ sSel.dispatchEvent(new Event('change', {bubbles:true})); }catch(e){}
      try{ if(pSel._refresh) pSel._refresh(); }catch(e){}
      try{ if(sSel._refresh) sSel._refresh(); }catch(e){}
      const via = res.via === 'llm' ? '（模型评估'+(res.reason?('：'+res.reason):'')+'）' : '（本地规则）';
      $('roleHintRst').textContent = '推荐：参与度 ' + (part==='high'?'活跃':part==='low'?'安静':'普通') + ' · 表情包 ' + st + ' 级 ' + via
        + (res.marks?(' [活跃m×'+res.marks.active+' 安静m×'+res.marks.passive+']'):'');
      $('roleHintDetail').style.display = '';
    }catch(e){ $('roleHintRst').textContent = '评估失败：'+e.message; }
  };
  const apply = $('roleHintApply');
  if(apply){
    apply.onclick = async ()=>{
      try{
        setPath(cfg, 'persona.participation', $('roleHintPart').value);
        setPath(cfg, 'store.sticker_level', parseInt($('roleHintSticker').value) || 0);
        const r = await getJSON('/api/config', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(cfg)});
        if(r && r.ok === false){ toast('应用失败：'+(r.error||'')); return; }
        cfg = await getJSON('/api/config'); syncToForm();
        toast('已应用行为档（参与度/表情包）并保存');
      }catch(e){ toast('应用失败：'+e.message); }
    };
  }
})();

/* 高级功能页：UI 布局状态 */
(async function(){
  try{
    const r = await getJSON('/api/ui-layout');
    const box = $('uiLayoutStat');
    if(box && r.ok){
      const it = r.layout || {};
      box.textContent = '已标定 ' + ((it.sidebar_items||[]).length||0) + ' 个侧栏图标' + (it.sidebar_items?'（'+it.sidebar_items.join(',')+'）':'');
    }
  }catch(e){ if($('uiLayoutStat')) $('uiLayoutStat').textContent = '读取失败：'+e.message; }
})();
if($('uiLayoutReload')) $('uiLayoutReload').onclick = ()=>location.reload();
if($('uiRecalibrate')) $('uiRecalibrate').onclick = async ()=>{
  if(!confirmBox) return;
  const b = $('uiRecalibrate'); b.disabled = true; b.textContent = '标定中…（微信前台）';
  try{
    const r = await getJSON('/api/ui/recalibrate', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
    if(r.ok) toast('✅ 标定完成：检测到 '+r.count+' 个侧栏图标');
    else toast('标定失败：'+(r.error||''));
  }catch(e){ toast('标定失败：'+e.message); }
  b.disabled = false; b.textContent = '重新标定（接管鼠标）';
  location.reload();
};

/* 壁纸视频：video 成功→启用；失败→CSS 波浪 */
(function(){
  const v = document.getElementById('wallVideo');
  if(!v) return;
  v.addEventListener('loadeddata', ()=>{
    try{
      if((getPath(cfg,'ui.theme')||'whale')==='whale' && (getPath(cfg,'ui.background')||'')!=='custom') document.body.classList.add('wall-video');
    }catch(e){ document.body.classList.add('wall-video'); }
  });
  v.addEventListener('error', ()=>{ document.body.classList.remove('wall-video'); });
  const _sel = document.querySelector('[data-cfg="ui.theme"]');
  if(_sel){
    _sel.addEventListener('change', ()=>{
      document.body.classList.toggle('wall-video', _sel.value==='whale' && !v.error);
    });
  }
})();

/* ── 水光粼粼 v10：跟随鼠标的小圈扭曲透镜；波纹速度更快，并随鼠标拖动速度加快而加快 ── */
/* ── 水光波纹 v11「投石入水」：中心最强 + 波纹环一圈圈向外荡开 + 强距离衰减；
    参数由控制台「🌊 水光波纹」卡调节（cfg.ui.wave_fx），保存后即时生效 ── */
(function(){
  const ID = 'cardWave2';
  const f = document.getElementById(ID);
  const turb = f ? f.querySelector('feTurbulence') : null;
  const disp = f ? f.querySelector('feDisplacementMap') : null;
  const lens = document.getElementById('waveLens');

  /* ① 参数（默认与 agent/config.py ui.wave_fx 一致；从 cfg 读，保留未设置的默认值） */
  const DEFAULTS = {enabled:false, scale:17, speed:5.2, mouse_gain:0.02, max_gain:8.0,
                    radius:260, falloff:4.0, rings:1, ring_speed:0.40};
  let W = Object.assign({}, DEFAULTS);
  function readParams(){
    try{
      const wf = (cfg && getPath(cfg,'ui.wave_fx')) || {};
      for(const k of Object.keys(DEFAULTS)){
        const v = wf[k];
        if(v!==undefined && v!==null && v!=='') W[k]= (typeof DEFAULTS[k]==='boolean') ? !!v : Number(v);
      }
      // 防手改 config 出现非法值（控制台已限界；此处兜底防 NaN/负环）
      W.scale = Math.max(0, Math.min(40, W.scale));
      W.speed = Math.max(0.2, Math.min(20, W.speed));
      W.mouse_gain = Math.max(0, Math.min(0.1, W.mouse_gain));
      W.max_gain = Math.max(0, Math.min(20, W.max_gain));
      W.radius = Math.max(80, Math.min(600, W.radius));
      W.falloff = Math.max(0.3, Math.min(6, W.falloff));
      W.rings = Math.max(1, Math.min(6, Math.round(W.rings)));
      W.ring_speed = Math.max(0.1, Math.min(1.5, W.ring_speed));
      if(isNaN(W.scale)||isNaN(W.speed)||isNaN(W.falloff)||isNaN(W.rings)) W = Object.assign({}, DEFAULTS);
    }catch(e){}
  }
  readParams();
  document.addEventListener('DOMContentLoaded', readParams);

  /* ② 波光流动：相位累计 + 鼠标速度联动（更快的基础波速，随拖动大幅提速） */
  const BASE = 17;                        // 兜底（实际用 W.scale）
  let _ph = 0;                            // 累计相位（rad）
  let _mouseSpeed = 0;
  let _lastEv = null, _lastEvT = 0;
  document.addEventListener('mousemove', (ev)=>{
    const now = performance.now();
    if(_lastEv){
      const dt = Math.max(1, now - _lastEvT) / 1000;
      const dx = ev.clientX - _lastEv.x, dy = ev.clientY - _lastEv.y;
      const v = Math.sqrt(dx*dx + dy*dy) / dt;
      _mouseSpeed = _mouseSpeed * 0.7 + v * 0.3;
    }
    _lastEv = {x: ev.clientX, y: ev.clientY}; _lastEvT = now;
  }, {passive:true});

  /* ③ 模块内投石入水 mask：中心=鼠标（--wx/--wy）。
     单环带模型：环位置 phase 随时间 0→1 缓慢推进（一圈≈2.5s，肉眼可见时间差）；
     环带宽窄(0.028)，环带强度随半径极强衰减(pow 4)——只有面前这一圈在动，扩散到边缘即消失。 */
  function waveMaskAt(now){
    const phase = ((now / 1000) * W.ring_speed) % 1;
    const N = 64;
    const stops = [];
    for(let i=0;i<=N;i++){
      const r = i / N;
      // 环带：高斯（低频噪波经位移后呈现为柔和的波前）
      const g = Math.exp(-Math.pow((r - phase) / 0.028, 2));
      // 极强衰减：环带 + 与半径平方衰减；只有中心附近的环强，走远就淡出
      let a = g * 1.0 * Math.pow(1 - phase, 4.0) + Math.pow(1 - r, 8.0) * 0.35;
      stops.push(Math.min(1, a).toFixed(3) + ' ' + (i*100/N).toFixed(1) + '%');
    }
    return 'radial-gradient(circle at var(--wx,50%) var(--wy,50%),' + stops.join(',') + ')';
  }

  /* 周期动画：扭曲滤镜持续流动 + mask 环带每 ~110ms 推进（一波接一波扩散） */
  let _lastMaskAt = 0;
  setInterval(()=>{
    if(!turb || !disp || !lens) return;
    if(!W.enabled){ lens.style.opacity='0'; return; }
    const now = performance.now();
    const dt = 0.033;
    const gain = Math.min(W.max_gain, _mouseSpeed * W.mouse_gain);
    const phSpeed = Math.min(20, W.speed + gain);
    _ph += phSpeed * dt;
    const ph = _ph;
    const fx = 0.008 + 0.004 * Math.sin(ph * 0.9);
    const fy = 0.011 + 0.005 * Math.cos(ph * 0.7);
    try{ turb.setAttribute('baseFrequency', fx.toFixed(4) + ' ' + fy.toFixed(4)); }catch(e){}
    const s = Math.max(0.5, W.scale * (1 + 0.38 * Math.sin(ph * 1.3)));
    try{ disp.setAttribute('scale', s.toFixed(2)); }catch(e){}
    if(now - _lastMaskAt > 110){
      _lastMaskAt = now;
      const m = waveMaskAt(now);
      try{ lens.style.maskImage = m; lens.style.webkitMaskImage = m; }catch(e){}
    }
  }, 33);

  /* ④ 透镜 = 光标所在的整个模块（卡片/侧栏/顶栏/弹层…）：
        - 模块 rect 设置透镜 size/pos/border-radius（波纹绝不越过模块边界）
        - --wx/--wy = 鼠标在模块内的相对位置（渐变中心=鼠标）
     无模块（空白处）→ 以小圆环显示（W.radius），只影响局部。 */
  const MODULES = '.card,.side,.topbar,.box,.menu,.mask .box';
  let _hostEl = null;
  function fitLensToHost(el, ev){
    const r = el.getBoundingClientRect();
    if(!r.width || !r.height) return false;
    lens.style.left = '0px'; lens.style.top = '0px';
    lens.style.width = r.width + 'px'; lens.style.height = r.height + 'px';
    lens.style.margin = '0';
    let br = '0';
    try{ br = getComputedStyle(el).borderRadius || '0'; }catch(e){}
    lens.style.borderRadius = br;
    // 顶栏置顶（40 < 顶栏50）：功能栏滚到顶栏下方的波纹不穿透顶栏；
    // 但宿主本身是顶栏/弹层/遮罩时给 9999，保证宿主自身区域也能被扭曲。 */
    const isTop = el.classList.contains('topbar');
    lens.style.zIndex = (isTop || el.closest('.mask') || el.classList.contains('menu')) ? '9999' : '40';
    lens.style.transform = 'translate3d(' + r.left + 'px,' + r.top + 'px,0)';
    lens.style.setProperty('--wx', (ev.clientX - r.left) + 'px');
    lens.style.setProperty('--wy', (ev.clientY - r.top) + 'px');
    _hostEl = el;
    return true;
  }
  if(lens){
    document.addEventListener('mousemove', (ev)=>{
      const host = ev.target && ev.target.closest ? ev.target.closest(MODULES) : null;
      if(host && W.enabled && fitLensToHost(host, ev)){
        lens.style.opacity = '1';
      } else {
        lens.style.opacity = '0'; _hostEl = null;
      }
    }, {passive:true});
    document.addEventListener('mouseleave', ()=>{ lens.style.opacity = '0'; _hostEl = null; });
  }

  /* ⑤ 应用按钮：保存 cfg.ui.wave_fx 并即时读回 */
  const applyBtn = document.getElementById('wavefxApply');
  if(applyBtn){
    applyBtn.addEventListener('click', async ()=>{
      try{
        const patch = {};
        document.querySelectorAll('[data-cfg^="ui.wave_fx."]').forEach(el=>{
          const key = el.dataset.cfg.slice('ui.wave_fx.'.length);
          if(!key) return;
          const num = parseFloat(el.value);
          patch[key] = (el.type==='checkbox') ? !!el.checked : (isNaN(num) ? el.value : num);
        });
        await getJSON('/api/config', {method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({ui:{wave_fx: patch}})});
        cfg = await getJSON('/api/config');
        readParams();
        // 即时生效：透镜隐藏后重新显示即可
        lens.style.opacity = '0';
        setTimeout(()=>{ lens.style.opacity = '1'; }, 60);
        toast('✅ 水光波纹已应用');
      }catch(e){ toast('保存失败：'+e.message); }
    });
  }

  /* ⑥ 逐字浮动：光标划过卡片标题时拆成字（一次拆好缓存）*/
  let lastT = 0;
  document.addEventListener('mousemove', (ev)=>{
    const now = performance.now();
    if(now - lastT < 90) return;
    lastT = now;
    const targetEl = ev.target && ev.target.closest ? ev.target.closest('.card,.side,.box,.menu') : null;
    if(targetEl && !targetEl._waves && targetEl.classList.contains('card')){
      targetEl._waves = true;
      targetEl.querySelectorAll('h2, h3, .desc > b').forEach(h=>{
        if(h.dataset.waved) return;
        const t = h.textContent;
        if(t.length > 60) return;
        h.dataset.waved = '1';
        const sp = document.createElement('span');
        sp.innerHTML = Array.from(t).map((ch, i)=>
          '<span class="wave-char" style="animation-delay:'+(i*0.12).toFixed(2)+'s">'+esc(ch)+'</span>').join('');
        h.innerHTML = '';
        h.appendChild(sp);
      });
    }
  });
  function bindFloat(){
    document.querySelectorAll('.card:not(.float-a)').forEach((el, i)=>{
      el.classList.add('float-a');
      el.style.animationDelay = (i % 7) * 0.7 + 's';   // 卡片轻柔浮沉（性能安全：仅 transform）
    });
  }
  bindFloat();
  new MutationObserver(bindFloat).observe(document.body, {childList:true, subtree:true});
})();

/* ── 🐋 鲸语版界面文案：DeepSeek 梗（V我50/服务器繁忙/CPU在烧/先白嫖）；功能说明照旧 ── */
/* 彩蛋按钮状态机（不放任何文档；用户点四下自己发现） */
const _EASTER_TXT = [
  "🤫 小鲸鱼的秘密档案（阅后即焚）\n\n" +
  "· 顶栏那只鲸鱼：可以按住拖出来放飞，它会自己游回来。\n" +
  "· 光标设置：点一下预览图（或按住指针），它会点头。\n" +
  "· 界面适配 → 界面文案风格换成「🐋 鲸语」：整页小鲸鱼开始碎碎念。\n" +
  "· 水光波纹：往水面上丢颗石子，看它一圈圈荡开。\n" +
  "· 顶栏的构建号（b.xxxx-xxxx）每次更新都会变——没变说明连的是旧版本。\n\n" +
  "——这不是功能说明，这只是一只小鲸鱼自己写的小抄。🐋"
];
(function(){
  const b = document.getElementById('easterEgg');
  if(!b) return;
  let step = 0;
  function render(){
    b.style.transition = 'transform .25s ease, color .25s ease';
    if(step === 0){ b.textContent='不要点！'; b.style.color='#ff5252'; b.style.transform='none'; b.style.opacity='.5'; }
    else if(step === 1){ b.textContent='绝对不要点'; b.style.color='#ff5252';
      b.style.transform='scale(.92) skew(-8deg)'; b.style.opacity='.6'; }
    else if(step === 2){ b.textContent='一键揭秘'; b.style.color='#111'; b.style.transform='scale(1)'; b.style.opacity='.7'; }
    else { b.textContent='一键揭秘'; }
  }
  b.onclick = (e)=>{
    e.stopPropagation();
    step += 1;
    if(step >= 3){
      if(step === 3){ render(); return; }   // 第 3 次：文案变"一键揭秘"
      // 第 4 次：弹出档案
      const box=document.createElement('div');
      box.className='box bill-dlg';
      box.style.cssText='width:min(560px,92vw);max-height:80vh;display:flex;flex-direction:column;text-align:left';
      box.innerHTML='<div class="bd-head"><b class="whale-tag">🐋 小鲸鱼的秘密档案</b>'
        +'<span class="sp" style="flex:1"></span><button class="ghost tiny" id="easterClose">✕ 关上</button></div>'
        +'<pre class="hint" style="white-space:pre-wrap;line-height:1.9;margin:4px 0 0;font-size:13.5px;color:var(--tx)">'+esc(_EASTER_TXT[0])+'</pre>';
      const mm=document.createElement('div'); mm.className='mask'; mm.style.background='rgba(5,9,17,.88)';
      mm.appendChild(box); document.body.appendChild(mm); maskOpen(mm);
      box.style.animation='calPop .3s cubic-bezier(.2,1.4,.4,1)';
      box.querySelector('#easterClose').onclick=()=>{ maskClose(mm); mm.remove(); };
      step = 0; render();
      return;
    }
    render();
  };
})();

const WHALE_TXT = {
  "wx-agent 控制台": "🐋 鲸鲸号 · 深度摸鱼",
  "概览": "🐋 概览 · 我是AI，别催，CPU还在烧",
  "检测中心（代码检测 / 鼠标操作检测）": "检测中心（先体检，再摸鱼）",
  "体检与功能自检": "检测中心 · 出远门前先体检",
  "功能自检清单（按重要性排序）": "功能自检清单（按重要性，一个一个过）",
  "调试 · 高级功能": "调试 · 高级功能（一般人我不告诉他）",
  "调试·高级功能": "调试·高级功能（一般人我不告诉他）",
  "运行明细": "📋 运行明细 · 内心戏全程有记录",
  "模型 API": "🧊 模型 API · 让我先推理一下，别插嘴",
  "微信": "💬 微信 · 收到，正在假装思考",
  "拍一拍（行为）": "👋 拍一拍 · 拍我干嘛，我只是个蓝鲸",
  "拍一拍": "👋 拍一拍 · 拍我干嘛，我只是个蓝鲸",
  "记忆（群友印象）": "🧠 记忆 · 好像记得…算了不装了",
  "记忆（共享设置）": "🤝 记忆共享 · 它记得=我记得，别问",
  "记忆": "🧠 记忆 · 好像记得…算了不装了",
  "记忆共享": "🤝 记忆共享 · 它记得=我记得，别问",
  "人设与响应": "🎭 人设 · 今天演谁？剧本拿来",
  "社区与学习": "📚 社区 · 好东西先白嫖再说",
  "发送限制": "🚦 发送限制 · 我不回你，就是我在偷懒",
  "联网搜索": "🔎 联网搜索 · 我去搜搜，先不告诉你结果",
  "服务器": "🖥️ 服务器 · 服务器繁忙，再试一次",
  "界面适配（DPI / 遮挡 / 主题）": "🎨 界面 · AI也要体面",
  "界面适配": "🎨 界面 · AI也要体面",
  "🐋 光标设置": "🖱️ 光标设置 · 别看我，看我的鼠标",
  "光标设置": "🖱️ 光标设置 · 别看我，看我的鼠标",
  "🌊 水光波纹（鼠标投石入水）": "🌊 水光波纹 · 人家怕水，我就爱摸鱼",
  "🌊 水光波纹": "🌊 水光波纹 · 人家怕水，我就爱摸鱼",
  "水光波纹": "🌊 水光波纹 · 人家怕水，我就爱摸鱼",
  "运行日志": "📜 运行日志 · 我的内心OS全在这",
  "完整配置 JSON（高级）": "📄 原始 JSON · 底裤都给你看",
  "原始 JSON": "📄 原始 JSON · 底裤都给你看",
  "保存全部设置": "保存全部设置（存好了，我不会失忆的）",
  "暂停": "⏸ 暂停（歇会儿）",
  "恢复": "▶ 恢复（满血）",
  "停止": "停止（打烊）",
  "重启": "重启（我又行了）",
  "测试 API 连通": "测试 API 连通（先冲个电，马上好）",
  "代码检测＋依赖核对": "代码检测＋依赖核对（少了什么先补课）",
  "代码检测": "代码检测（先查bug，再查心情）",
  "查看进度条": "查看进度条（别催，在跑了）",
  "鼠标操作检测": "鼠标操作检测（AI也要做视力检查）",
  "一键体检": "鼠标操作检测（AI也要做视力检查）",
  "停止检测": "停止检测（不测了，我摊牌）",
  "拍一拍检测": "拍一拍检测（别真拍我）",
  "模型评分": "模型评分（AI打分，绝不偏袒）",
  "模型补足": "模型补足（拾掇拾掇，更像本人）",
  "根据角色卡推荐行为档": "根据角色卡推荐行为档（我懂你）",
  "保存 Key": "保存 Key（钥匙收好了）",
  "重置 Key（重新填写）": "重置 Key（换把钥匙）",
};
function applyWhale(){
  try{
    if(!cfg || (getPath(cfg,'ui.text_style')||'') !== 'whale') return;
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while(walker.nextNode()) nodes.push(walker.currentNode);
    for(const n of nodes){
      const t = (n.nodeValue||'').trim();
      if(WHALE_TXT[t] && n.nodeValue.indexOf(WHALE_TXT[t]) < 0){
        n.nodeValue = n.nodeValue.replace(t, WHALE_TXT[t]);
      }
    }
    // 顶栏徽标
    const lg = document.querySelector('.logo span');
    if(lg && lg.textContent.indexOf('鲸鲸号') < 0){
      lg.innerHTML = lg.innerHTML.replace('wx-agent 控制台', '🐋 鲸鲸号 · 深度摸鱼');
    }
    document.title = '🐋 鲸鲸号 · 深度摸鱼';
  }catch(e){}
}
document.addEventListener('DOMContentLoaded', applyWhale);

/* ── 程序鼠标检验区（按钮直控鼠标；搜索框过滤 + 滚动槽）── */
const UI_TESTS = [
  {id:"moments_open",  name:"朋友圈：打开",        desc:"点侧栏朋友圈图标 → 验证「朋友圈」窗口出现"},
  {id:"moments_close", name:"朋友圈：关闭",        desc:"点窗口右上角叉号 → 验证已关（可反复测）"},
  {id:"moments_like",  name:"朋友圈：点赞第一条",  desc:"蓝点→「赞」→自动关窗（对第一条动态）"},
  {id:"moments_comment", name:"朋友圈：评论第一条", desc:"蓝点→「评论」→输入测试评论→发送→关窗（会真评论）"},
  {id:"moments_scroll", name:"朋友圈：滚动刷",     desc:"滚轮滚动信息流（幅度按窗口高）→ 关窗"},
  {id:"moments_publish", name:"朋友圈：纯文字发布 dry", desc:"长按相机2秒→弹窗输入测试文字→到输入框即止（不点发表、不真发；逐屏截图 _scratch/shots）"},
  {id:"emoji_collect", name:"表情：收藏（右键）",  desc:"右键最近一条 [表情]/[图片] →「添加到表情」（会真收藏）"},
  {id:"emoji_panel",   name:"表情：面板发送",      desc:"点输入栏笑脸→面板→爱心→点表情→发送（未指定名字时）"},
  {id:"message_collect", name:"消息：收藏",        desc:"右键最新消息→「收藏」（验证菜单通路）"},
  {id:"message_recall", name:"消息：撤回",         desc:"右键自己最新消息→「撤回」（2分钟内有效）"},
  {id:"windows_clean", name:"窗口：清理残留",      desc:"枚举并关闭所有微信残留子窗口（叉号→验证→兜底）"},
  {id:"recalibrate",   name:"UI 图标库：重新标定", desc:"接管鼠标检测侧栏图标序列并写 ui_layout.json"},
];
async function runUiTest(id, btn){
  const res = document.getElementById('uiTestRst_' + id);
  const stop = document.getElementById('uiTestStop_' + id);
  if(!btn) btn = document.getElementById('uiTestBtn_' + id);
  if(!btn) return;
  btn.disabled = true; btn.textContent = '执行中…';
  if(stop) stop.disabled = false;
  if(res) res.textContent = '';
  try{
    const r = await getJSON('/api/ui-test', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({kind:id})});
    if(res){
      const ok = r && r.ok;
      res.textContent = ok ? ('✅ ' + (r.note || '成功')) : ('❌ ' + (r.error || '失败'));
      res.style.color = ok ? 'var(--ok-tx)' : 'var(--err-tx)';
    }
  }catch(e){ if(res){ res.textContent = '❌ ' + e.message; res.style.color = 'var(--err-tx)'; } }
  btn.disabled = false; btn.textContent = '执行';
  if(stop) stop.disabled = true;
}
async function stopUiTest(id){
  try{
    await getJSON('/api/ui-test/stop', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
    toast('已请求停止（当前动作完成即中止）');
    setTimeout(()=>{ const b = document.getElementById('uiTestBtn_' + id); if(b){ b.disabled = false; b.textContent = '执行'; } }, 1500);
  }catch(e){ toast('停止失败：'+e.message); }
}
function renderUiTests(){
  const box = document.getElementById('uiTestBox'); if(!box) return;
  const q = (document.getElementById('uiTestSearch') || {}).value || '';
  box.innerHTML = '';
  UI_TESTS.filter(t => !q || t.name.includes(q) || t.desc.includes(q)).forEach(t=>{
    const row = document.createElement('div');
    row.style.cssText = 'display:flex;align-items:center;gap:10px;padding:7px 4px;border-bottom:1px solid var(--bd)';
    row.innerHTML = '<div style="flex:1"><b>'+t.name+'</b><div class="hint">'+t.desc+'</div>'+
      '<span id="uiTestRst_'+t.id+'" style="font-size:12.5px"></span></div>'+
      '<button class="pri" id="uiTestBtn_'+t.id+'" style="white-space:nowrap">执行</button>'+
      '<button class="ghost" id="uiTestStop_'+t.id+'" style="white-space:nowrap;color:var(--err-tx)" disabled>停止</button>';
    box.appendChild(row);
    row.querySelector('#uiTestBtn_'+t.id).onclick = ()=>runUiTest(t.id);
    row.querySelector('#uiTestStop_'+t.id).onclick = ()=>stopUiTest(t.id);
  });
}
if(document.getElementById('uiTestSearch')){
  document.getElementById('uiTestSearch').addEventListener('input', renderUiTests);
  renderUiTests();
}

/* 表情包收藏夹（搜索框 + 滚动槽 + 最多显示 60 个） */
let _emojiAll = [];
async function loadEmojis(){
  try{
    const r = await getJSON('/api/emojis');
    const box = $('emojiBox');
    if(!box) return;
    _emojiAll = (r.emojis||[]);
    renderEmojis();
    $('emojiCount').textContent = '共 ' + _emojiAll.length + ' 个（机器人发送用 send_emoji）';
  }catch(e){
    const box = $('emojiBox');
    if(box) box.innerHTML = '<span class="hint">读取失败：'+e.message+'</span>';
  }
}
function renderEmojis(){
  const box = $('emojiBox'); if(!box) return;
  const q = ($('emojiSearch') && $('emojiSearch').value || '').trim().toLowerCase();
  const list = q ? _emojiAll.filter(e=>e.name.toLowerCase().includes(q)) : _emojiAll;
  if(!list.length){
    box.innerHTML = '<span class="hint">' + (q ? '没有匹配「'+q+'」的表情' : '收藏夹为空：群里收到好玩的表情后，机器人可用 collect_emoji 收藏。') + '</span>';
    return;
  }
  const show = list.slice(0, 60);
  box.innerHTML = '';
  show.forEach(e=>{
    const wrap = document.createElement('div');
    wrap.style.cssText = 'position:relative;flex:0 0 auto';
    const img = document.createElement('img');
    img.src = '/assets/emoji/' + encodeURIComponent(e.name) + '?t=' + Date.now();
    img.style.cssText = 'width:44px;height:44px;object-fit:contain;border:1px solid var(--bd);border-radius:8px;background:var(--card)';
    img.title = e.name;
    img.onerror = ()=>{ img.style.opacity = '.2'; img.title = e.name + '（图片读取失败）'; };
    wrap.appendChild(img);
    const del = document.createElement('button');
    del.textContent = '×';
    del.title = '删除 ' + e.name;
    del.style.cssText = 'position:absolute;top:-5px;right:-5px;width:18px;height:18px;line-height:16px;padding:0;border-radius:50%;background:var(--err);color:#fff;font-size:12px;cursor:pointer;border:none';
    del.onclick = async (ev)=>{
      ev.stopPropagation();
      if(!await uiConfirm('删除表情「'+e.name+'」？')) return;
      try{
        const r = await getJSON('/api/emojis/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:e.name})});
        if(r.ok){ toast('已删除'); loadEmojis(); } else toast('删除失败：'+(r.error||''));
      }catch(ex){ toast('删除失败：'+ex.message); }
    };
    wrap.appendChild(del);
    box.appendChild(wrap);
  });
  if(list.length > show.length){
    const more = document.createElement('div');
    more.className = 'hint'; more.style.cssText = 'width:100%';
    more.textContent = '…还有 ' + (list.length - show.length) + ' 个（滚到底或用搜索）';
    box.appendChild(more);
  }
}
if($('emojiRefresh')) $('emojiRefresh').onclick = loadEmojis;
if($('emojiSearch')) $('emojiSearch').addEventListener('input', renderEmojis);
loadEmojis();

async function loadPokeGroups(){
  try{
    const r = await getJSON('/api/wechat-groups');
    const sel = $('pokeGroup'); const cur = sel.value;
    sel.innerHTML = '<option value="">自动（最近有人发言的群）</option>';
    (r.groups||[]).forEach(g=>{ const o=document.createElement('option'); o.value=g.wxid; o.textContent=g.name; sel.appendChild(o); });
    if(cur) sel.value = cur;
  }catch(e){}
}
/* 功能自检清单：localStorage 记忆勾选 */
function ckInit(){
  let saved = [];
  try{ saved = JSON.parse(localStorage.getItem('wxAgent.checklist')||'[]'); }catch(e){}
  document.querySelectorAll('#checkList .ck').forEach((ck,i)=>{ ck.checked = saved.includes(i);
    ck.addEventListener('change', ckCount);
  });
  ckCount();
}
function ckCount(){
  const list = document.querySelectorAll('#checkList .ck');
  let arr = [];
  list.forEach((ck,i)=>{ if(ck.checked) arr.push(i); });
  try{ localStorage.setItem('wxAgent.checklist', JSON.stringify(arr)); }catch(e){}
  $('ckCount').textContent = '已完成 '+arr.length+' / '+list.length;
}
$('ckReset').onclick = ()=>{ document.querySelectorAll('#checkList .ck').forEach(ck=>ck.checked=false); ckCount(); };
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
        if(!await uiConfirm('删除「'+name+'」的全部印象？')) return;
        try{
          await getJSON('/api/memory',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chat_key:sel.value,user_id:m.userId})});
          toast('已删除'); loadMemory(sel.value);
        }catch(e){ toast('删除失败：'+e.message); }
      };
      tr.innerHTML='<td><input type="checkbox" class="memPick" data-uid="'+esc(String(m.userId||''))+'" data-nm="'+esc(name)+'" style="width:15px;height:15px"></td><td>'+esc(name)+'</td><td>'+n+'</td><td>'+memTime(m.updatedAt)+'</td>';
      tr.querySelector('.memPick').onchange = ()=>{
        const any = !!document.querySelector('.memPick:checked');
        const b = $('memClearSel'); if(b) b.disabled = !any;
      };
      const editBtn=document.createElement('button'); editBtn.className='ghost'; editBtn.textContent='编辑';
      editBtn.onclick=async ()=>{
        const cur=(m.impressions||[]).map(e=>e.content||'').join('\n');
        const box=document.createElement('div'); box.className='box'; box.style.textAlign='left';
        box.innerHTML='<h1>编辑「'+esc(name)+'」的印象</h1><p>每行一条印象；清空=删除全部。</p>'+
          '<textarea id="memEdit" rows="6" class="out">'+esc(cur)+'</textarea>'+
          '<div class="btns" style="justify-content:flex-end;margin-top:10px"><button class="pri" id="memEditOk">保存</button><button class="ghost" id="memEditCancel">取消</button></div>';
        const mm=document.createElement('div'); mm.className='mask'; mm.appendChild(box);
        document.body.appendChild(mm); maskOpen(mm);
        $('memEditOk').onclick=async ()=>{
          try{
            const lines=($('memEdit').value||'').split('\n').map(s=>s.trim()).filter(Boolean);
            await getJSON('/api/memory',{method:'POST',headers:{'Content-Type':'application/json'},
              body:JSON.stringify({action:'update',chat_key:sel.value,user_id:m.userId,name:m.name,contents:lines})});
            toast('已更新'); maskClose(mm); mm.remove(); loadMemory(sel.value);
          }catch(e){ toast('更新失败：'+e.message); }
        };
        $('memEditCancel').onclick=()=>{ maskClose(mm); mm.remove(); };
      };
      tr.appendChild(editBtn);
      tr.appendChild(del);
      tb.appendChild(tr);
    }
  }catch(e){ $('memEmpty').style.display='block'; $('memEmpty').textContent='加载失败：'+e.message; }
}
let memMembers = [];
/* 清除勾选的记忆 / 清除全部 / 清除会话日志 */
(async function(){
  const selB = document.getElementById('memClearSel'), allB = document.getElementById('memClearAll'), rst = document.getElementById('memClearRst');
  const sessB = document.getElementById('sessClear');
  if(!selB || !allB) return;
  selB.onclick = async ()=>{
    const picked = Array.from(document.querySelectorAll('.memPick:checked'));
    if(!picked.length){ toast('请先勾选要清除的成员'); return; }
    if(!await uiConfirm('清除勾选的 '+picked.length+' 位成员全部印象？')) return;
    try{
      for(const p of picked){
        await getJSON('/api/memory',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({chat_key:(sel?sel.value:''),user_id:p.dataset.uid})});
      }
      if(rst) rst.textContent = '✅ 已清除 '+picked.length+' 位';
      toast('✅ 已清除 '+picked.length+' 位成员印象');
      if(typeof loadMemory === 'function') loadMemory(sel ? sel.value : '');
    }catch(e){ toast('清除失败：'+e.message); }
  };
  allB.onclick = async ()=>{
    if(!await uiConfirm('⚠️ 清除全部记忆（所有群所有成员印象+共享记忆）？不可恢复！')) return;
    try{
      const r = await getJSON('/api/memory',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({action:'clear_all'})});
      if(rst) rst.textContent = '✅ '+(r.ok?'全部记忆已清除':('失败：'+(r.error||'')));
      toast(r.ok ? '✅ 全部记忆已清除' : '清除失败');
      if(typeof loadMemory === 'function') loadMemory('');
    }catch(e){ toast('清除失败：'+e.message); }
  };
  if(document.getElementById('memCheckAll')) document.getElementById('memCheckAll').onchange = (e)=>{
    document.querySelectorAll('.memPick').forEach(c=>{ c.checked = e.target.checked; });
    selB.disabled = !e.target.checked;
  };
  if(sessB) sessB.onclick = async ()=>{
    if(!await uiConfirm('⚠️ 清除全部会话日志（运行明细里的对话历史）？模型之后不会再记得这些对话。')) return;
    try{
      const r = await getJSON('/api/memory',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({action:'clear_sessions'})});
      if(rst) rst.textContent = '✅ '+(r.note||'已清除');
      toast('✅ 会话日志已清除');
      try{ loadSessions(); }catch(e){}
    }catch(e){ toast('清除失败：'+e.message); }
  };
})();
$('memChats').addEventListener('change', ()=>loadMemory($('memChats').value));
$('memRefresh').onclick = ()=>loadMemory($('memChats').value);
/* 记忆页群搜索：过滤下拉选项；回车选中第一个匹配（群多时最顺手） */
$('memSearch').addEventListener('input', ()=>{
  const kw=$('memSearch').value.trim().toLowerCase();
  Array.from($('memChats').options).forEach(o=>{
    if(!o.value) return;
    o.hidden = !!(kw && !o.textContent.toLowerCase().includes(kw));
  });
});
$('memSearch').addEventListener('keydown', (e)=>{
  if(e.key!=='Enter') return;
  const kw=$('memSearch').value.trim().toLowerCase();
  const opt = Array.from($('memChats').options).find(o=>o.value && o.textContent.toLowerCase().includes(kw));
  if(opt){ $('memChats').value=opt.value; loadMemory(opt.value); }
  e.preventDefault();
});

/* 导航：滚动同步高亮 + 蓝色指示条平滑滑动 */
(function(){
  const navEl = document.querySelector('#nav');
  const ind = document.createElement('div'); ind.className='nav-ind';
  navEl.insertBefore(ind, navEl.firstChild);
  const links = Array.from(document.querySelectorAll('#nav a'));
  function moveInd(a){ ind.style.opacity=1; ind.style.top = Math.round(a.offsetTop + a.offsetHeight/2 - 1.5)+'px'; }
  function currentSection(){
    const secs = Array.from(document.querySelectorAll('section[data-sec]'));
    const y = window.scrollY + 90;
    let cur = secs[0];
    for(const s of secs){ if(s.offsetTop <= y) cur = s; }
    return cur;
  }
  function sync(){
    const cur = currentSection();
    const a = links.find(x => x.getAttribute('href') === '#'+cur.id);
    if(a){ links.forEach(x=>x.classList.toggle('on', x===a)); moveInd(a); }
  }
  window.addEventListener('scroll', ()=>requestAnimationFrame(sync), {passive:true});
  links.forEach(a=>a.addEventListener('click', ()=>{
    links.forEach(x=>x.classList.remove('on'));
    a.classList.add('on'); moveInd(a);
  }));
  setTimeout(sync, 400);
})();

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
      '<p style="text-align:left;margin:4px 0">· 全部进程已结束（机器人 + 看门狗），不会再自动拉起。</p>'+
      '<p style="text-align:left;margin:4px 0">· 想再次运行：双击根目录 <b>启动机器人.vbs</b>（完全无窗口），或 <b>一键启动.bat</b>（依赖检查+自检+启动）。</p>'+
      '<p style="text-align:left;margin:4px 0">· 群聊与存档数据不会丢失，下次启动自动恢复。</p>'+
      '<p style="text-align:left;margin:4px 0">· 日志已保存在 logs\\wx_agent.log，供排查。</p>'+
      '<div class="hint">正在尝试自动关闭本标签页；约 3 秒后关不掉就请手动关闭（浏览器会拦截脚本关闭，属正常现象）。</div></div>';
    document.body.appendChild(ov); maskOpen(ov);
    // 稳定关闭：先 window.open 建立「脚本可关」的同源窗口再 close（绕过浏览器限制）
    setTimeout(()=>{ try{ window.open('', '_self'); setTimeout(()=>{ try{window.close();}catch(_e){} }, 800); }catch(_e){} }, 3000);
    // 兜底：仍未关闭（浏览器强拦 close）→ 替换为空白页，避免残留旧界面
    setTimeout(()=>{ try{ if(!window.closed) location.replace('about:blank'); }catch(_e){} }, 6000);
  }
}

load();
loadMemory('');
loadPokeGroups();
ckInit();
enhanceSelects();
setInterval(loadStatus, 8000);
setInterval(loadBalance, 30000);
setInterval(()=>{ if($('autolog').checked) loadLog(); }, 4000);
setInterval(checkAlive, 6000);
$('sessRefresh').onclick = ()=>loadSessions();
addEventListener('hashchange', ()=>{ if(location.hash==='#sec-sessions') loadSessions(); });
$('sessExpand').addEventListener('change', ()=>loadSessions());
/* ── 社区与学习：每群档位 / 屏蔽名单 / 导出 / 导入 ── */
function renderGroupTierBox(){
  const box = $('groupTierBox'); if(!box) return;
  const unified = getPath(cfg,'store.unified_tier');
  if(unified !== false){ box.innerHTML='<div class="hint">勾选"每群独立档位"后，这里列出每个群可单独设置档位。</div>'; return; }
  const groups = (cfg && cfg.wechat && cfg.wechat.group_name_white_list) || [];
  const gt = getPath(cfg,'store.group_tier') || {};
  box.innerHTML='';
  if(!groups.length){ box.innerHTML='<div class="hint">没有群白名单——群列表为空（在「微信」卡勾选群后此处自动列出）。</div>'; return; }
  groups.forEach(g=>{
    const row=document.createElement('div'); row.className='row';
    row.innerHTML='<label>'+esc(g)+'</label><div class="grow"><select data-group-tier="'+esc(g)+'">'+
      '<option value="">跟随全局</option><option value="1">1 档</option><option value="2">2 档</option>'+
      '<option value="3">3 档</option><option value="4">4 档</option></select></div>';
    const sel=row.querySelector('select');
    sel.value = gt[g] != null ? String(gt[g]) : '';
    sel.addEventListener('change', ()=>{
      const v = sel.value ? parseInt(sel.value,10) : null;
      const cur = getPath(cfg,'store.group_tier') || {};
      if(v === null) delete cur[g]; else cur[g] = v;
      setPath(cfg,'store.group_tier', cur);
    });
    box.appendChild(row);
  });
}
$('unifiedTierChk').addEventListener('change', ()=>renderGroupTierBox());
/* 打开种子库 / 确认上传（联动社区上传开关+URL） */
(function(){
  const ob = document.getElementById('openSeedBtn'), ub = document.getElementById('uploadSeeds');
  if(!ob || !ub) return;
  ob.onclick = async ()=>{
    try{
      const r = await getJSON('/api/open-path',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:'data/seed_library.json'})});
      if(!r.ok) toast('打开失败：'+(r.error||''));
      else toast('✅ 已打开种子库（编辑后保存即可生效）');
    }catch(e){ toast('打开失败：'+e.message); }
  };
  const chk = document.querySelector('[data-cfg="community.upload_enabled"]');
  const url = document.querySelector('[data-cfg="community.holyshits_upload_url"]');
  const fbUrl = document.querySelector('[data-cfg="community.feedback_upload_url"]');
  const fbBtn = document.getElementById('uploadFeedback');
  function syncState(){
    ub.disabled = !(chk && chk.checked && url && url.value.trim());
    if(fbBtn) fbBtn.disabled = !(chk && chk.checked && fbUrl && fbUrl.value.trim());
  }
  syncState();
  if(chk) chk.addEventListener('input', syncState);
  if(url) url.addEventListener('input', syncState);
  if(fbUrl) fbUrl.addEventListener('input', syncState);
  ub.onclick = async ()=>{
    if(!await uiConfirm('确认把当前种子库上传到配置的服务器？')) return;
    try{
      const r = await getJSON('/api/community/upload',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:'holyshits'})});
      const msg = r && r.ok ? ('✅ 已上传 '+((r.count||r.uploaded||0))+' 条') : ('上传失败：'+(r.error||'未配置'));
      $('uploadRst').textContent = msg; toast(msg);
    }catch(e){ $('uploadRst').textContent = '上传失败：'+e.message; }
  };
  if(fbBtn) fbBtn.onclick = async ()=>{
    if(!await uiConfirm('确认把意见反馈上传到配置的服务器？')) return;
    try{
      const r = await getJSON('/api/community/upload',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:'feedback'})});
      const msg = r && r.ok ? ('✅ 意见已上传 '+((r.count||r.uploaded||0))+' 条') : ('上传失败：'+(r.error||'未配置'));
      $('uploadRst').textContent = msg; toast(msg);
    }catch(e){ $('uploadRst').textContent = '上传失败：'+e.message; }
  };
})();

/* ── 费用计算器（官方峰谷价）── */
(function(){
  const btn = document.getElementById('fcCalc');
  if(!btn) return;
  const fmt = n => n>=0.01 ? ('¥'+n.toFixed(2)) : ('¥'+n.toFixed(4));
  btn.onclick = () => {
    const msgs = Math.max(0, parseFloat(document.getElementById('fcMsgs').value)||0);
    const ti = Math.max(0, parseFloat(document.getElementById('fcIn').value)||0);
    const to = Math.max(0, parseFloat(document.getElementById('fcOut').value)||0);
    const peak = document.getElementById('fcPeak').value === '1';
    const pIn = peak ? 2 : 1, pOut = peak ? 8 : 4, pHit = peak ? 0.04 : 0.02;
    const per = (ti*pIn + to*pOut)/1e6;
    const perHit = (ti*pHit + to*pOut)/1e6;
    const day = msgs*per, dayHit = msgs*perHit;
    document.getElementById('fcResult').textContent =
      '每消息 ≈ '+fmt(per)+(perHit<per?('（缓存命中输入 ≈ '+fmt(perHit)+'）'):'')+
      '；每日 '+msgs+' 条 ≈ '+fmt(day)+'；月成本 ≈ '+fmt(day*30)+'（空闲月 = '+fmt(day*15)+'）';
  };
})();
/* ── 概览右上角计费删除按钮：页面加载级绑定（不依赖 loadSessions 是否执行过）── */
(function(){
  const all = document.getElementById('costClearAll');
  const sel = document.getElementById('costClearSel');
  const ex = document.getElementById('dataExport');
  const im = document.getElementById('dataImport');
  const file = document.getElementById('dataImportFile');
  const q = URL_TOKEN ? ('?token='+URL_TOKEN) : '';
  if(ex) ex.onclick = async ()=>{
    try{
      const r = await fetch('/api/data/export'+q, {method:'POST'});
      if(!r.ok){ toast('导出失败：HTTP '+r.status); return; }
      const blob = await r.blob();
      const d = new Date();
      const ds = d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'wx-agent-数据迁移-'+ds+'.zip';
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(()=>URL.revokeObjectURL(a.href), 5000);
      toast('已导出记录（计费+对话），文件名见下载');
    }catch(e){ toast('导出失败：'+e.message); }
  };
  if(im) im.onclick = ()=>{ if(file) file.click(); };
  if(file) file.onchange = async ()=>{
    const f = file.files && file.files[0];
    if(!f) return;
    try{
      const r = await fetch('/api/data/import'+q, {method:'POST', body: f});
      const j = await r.json();
      if(j && j.ok){
        toast('迁移完成：'+j.note);
        if(typeof loadSessions==='function') loadSessions();
        if(window.__renderCal) window.__renderCal();
        if(typeof loadStatus==='function') loadStatus();
      } else {
        toast('迁移失败：'+((j && j.error) || '未知'));
      }
    }catch(e){ toast('迁移失败：'+e.message); }
    file.value='';
  };
  if(all) all.onclick = async ()=>{
    if(!await uiConfirm('确认一键删除全部计费历史（今日/周期/累计用量的历史记录）？')) return;
    try{
      const r=await getJSON('/api/stats/cal_clear',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
      toast(r.ok?('已清空计费历史'):(r.error||'失败'));
      if(r.ok) loadStatus();
    }catch(e){ toast('失败：'+e.message); }
  };
  if(sel) sel.onclick = async ()=>{
    try{
      const r = await getJSON('/api/stats/cal_list',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
      const bills = (r && r.bills) || [];
      if(!bills.length){ toast('当前没有可删除的计费日志'); return; }
      openBillDlg(bills);
    }catch(e){ toast('加载计费日志失败：'+e.message); }
  };
})();

$('seedImportBtn').onclick = async ()=>{
  try{
    const text = $('seedImport').value || '';
    if(!text.trim()){ toast('请先粘贴要导入的金句文本'); return; }
    const r = await getJSON('/api/scoring/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text})});
    $('seedImportRst').textContent = r.ok ? ('已导入 '+r.imported+' 条') : ('失败：'+r.error);
    toast(r.ok ? ('✅ 金句已导入种子库 '+r.imported+' 条') : ('导入失败：'+r.error));
  }catch(e){ toast('导入失败：'+e.message); }
};
/* 选择文件导入种子库（txt/json；读入→文本→查重合并→生效） */
(function(){
  const btn = document.getElementById('seedImportFile');
  if(!btn) return;
  const file = document.getElementById('seedFile');
  btn.onclick = ()=> file && file.click();
  if(file) file.onchange = async ()=>{
    const f = file.files && file.files[0];
    if(!f) return;
    const text = await f.text();
    if(!text.trim()){ toast('文件为空'); return; }
    try{
      const r = await getJSON('/api/scoring/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text})});
      $('seedImportRst').textContent = r.ok ? ('✅ 从文件导入 '+r.imported+' 条（查重后）') : ('导入失败：'+(r.error||''));
      toast(r.ok ? ('✅ 文件导入 '+r.imported+' 条，已查重并生效') : ('导入失败：'+r.error));
    }catch(e){ toast('导入失败：'+e.message); }
    file.value = '';
  };
})();
/* 高级功能页：种子库状态 */
async function loadSeedStats(){
  try{
    const r = await getJSON('/api/scoring/stats',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    if(r.ok && $('seedStats')){
      const d = r.data || {};
      $('seedStats').textContent = '种子库 '+ (d.seed_count||0) +' 条 · 已学反应 '+ (d.reaction_count||0) +' 条 · 高分参考 '+ ((d.top||[]).length||0) +' 条';
    }
  }catch(e){ if($('seedStats')) $('seedStats').textContent = '种子库状态读取失败（'+e.message+'）'; }
}
if($('seedReload')) $('seedReload').onclick = loadSeedStats;
loadSeedStats();
/* ⑦ 金句自定义选单：自定义金句添加进库 */
(function(){ const a=$('seedCustomAdd'); if(!a) return;
  a.onclick = async ()=>{
    const inp=$('seedCustomTxt'); const t=(inp&&inp.value)||'';
    if(!t.trim()){ if($('seedCustomRst')) $('seedCustomRst').textContent='先写一句金句'; return; }
    try{
      const r=await getJSON('/api/scoring/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:t.trim()})});
      if($('seedCustomRst')) $('seedCustomRst').textContent = r.ok?('已添加 '+r.imported+' 条'):(r.error||'添加失败');
      if(inp) inp.value='';
      if(typeof loadSeedStats==="function") loadSeedStats();
    }catch(e){ if($('seedCustomRst')) $('seedCustomRst').textContent='添加失败：'+e.message; }
  };
})();
/* ③ 地址栏防窥视：① 一律移除 ?token=（登录后防复制登入）；
   ② 可选「地址乱码化」（ui.obscure_url=true，默认关）：路径也换成随机乱码串（刷新靠会话 cookie）。 */
(function(){
  try{
    if(location.search.indexOf('token=')>=0){
      const u = new URL(location.href);
      u.searchParams.delete('token');
      history.replaceState({}, '', u.toString());
    }
    if((cfg && getPath(cfg,'ui.obscure_url'))){
      let garb = '';
      const chars = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789#$~';
      for(let i=0;i<16;i++) garb += chars[Math.floor(Math.random()*chars.length)];
      const u = new URL(location.href);
      u.pathname = '/' + garb;
      u.search = '';
      u.hash = '';
      history.replaceState({}, '', u.toString());
    }
  }catch(e){}
})();
/* 常驻进度栏：页面加载即显示代码检测状态（运行中实时百分比，结束后保留结果提示） */
(function(){
  const persist=()=>{ if($('codeCheckTip') && !$('codeCheckTip').textContent) $('codeCheckTip').textContent='代码检测：尚未运行（点「检测中心」页的代码检测/代码检测＋依赖核对）'; };
  persist();
  document.addEventListener('DOMContentLoaded', persist);
})();
/* 机器学习：确定学习 / 学习评估 */
(function(){
  const r=$('learnRst');
  if($('learnApply')) $('learnApply').onclick = async ()=>{
    if(r) r.textContent='正在确认学习机制…';
    try{
      const res=await getJSON('/api/learning/start',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
      if(r) r.textContent = res.ok ? (res.note||'✅ 已开启') : ('开启失败：'+(res.error||res.note));
    }catch(e){ if(r) r.textContent='开启失败：'+e.message; }
  };
  if($('learnEval')) $('learnEval').onclick = async ()=>{
    if(r) r.textContent='正在让模型评估学习效果（按评分细则）…';
    try{
      const res=await getJSON('/api/learning/evaluate',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
      if(r) r.innerHTML = res.eval ? ('评估：'+res.eval) : (res.error||res.note||'评估完成');
    }catch(e){ if(r) r.textContent='评估失败：'+e.message; }
  };
})();
async function doExport(kind, label){
  const btn = document.getElementById('export'+label); if(btn) btn.disabled = true;
  try{
    const r = await getJSON('/api/community/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind})});
    if(r.ok){
      $('exportRst').textContent = '已导出 '+r.count+' 条 → '+r.path;
      let op = document.getElementById('openExportDir');
      if(!op){        op = document.createElement('button'); op.id='openExportDir'; op.className='ghost';
        op.style.marginLeft='8px'; op.textContent='打开所在位置';
        $('exportRst').parentNode.appendChild(op);
        op.onclick = async ()=>{
          const rr = await getJSON('/api/open-path',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:r.path})});
          if(!rr.ok) toast('打开失败：'+(rr.error||''));
        };
      }
    } else { $('exportRst').textContent = '失败：'+(r.error||''); }
    toast(r.ok ? ('✅ '+label+' 已导出') : ('导出失败：'+r.error));
  }catch(e){ $('exportRst').textContent='失败：'+e.message; toast('导出失败：'+e.message); }
  if(btn) btn.disabled = false;
}
$('exportHolyshits').onclick = ()=>doExport('holyshits','Holyshits');
/* 显式「打开导出文件夹」：打开配置的导出目录（绝对路径，不存在则提示） */
(function(){
  const b = document.getElementById('openExportDir');
  if(!b) return;
  b.onclick = async ()=>{
    try{
      const dir = (getPath(cfg,'community.export_dir')||'exports').trim()||'exports';
      const r = await getJSON('/api/open-path',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:dir})});
      if(!r.ok) toast('打开失败：'+((r.error||'')+(r.note||'')));
      else toast('✅ 已打开导出文件夹');
    }catch(e){ toast('打开失败：'+e.message); }
  };
})();
$('exportFeedback').onclick = ()=>doExport('feedback','Feedback');
$('exportMessages').onclick = ()=>doExport('messages','Messages');
const _tierSel = document.querySelector('[data-cfg="store.context_tier"]');
if(_tierSel) _tierSel.addEventListener('change', ()=>updateTierRows());
</script>
<div style="position:fixed;left:4px;bottom:2px;font-size:10px;color:#8aa0c0;opacity:.55;z-index:9">wx-agent build 2026-09-09</div></body>
</html>
"""
