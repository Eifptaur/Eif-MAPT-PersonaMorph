# -*- coding: utf-8 -*-
"""右下角鲸鱼挂件 —— 上游 DeepSeek-Balance-Whale-Widget 的**原版复刻**。

用户裁定（2026-09-25 截图）：此前 Qt 版做成了「胶囊卡片」（圆角卡 + 余额/今日已用/
每轮消耗四行灰字）——不是原版。原版形态（whale-widget/client/widget.js :245-272
:10293-10297）＝
  · 透明方块（无边框无底色，默认边长 min(250px, 视口短边×0.28)）；
  · 右下角鲸鱼图（边长的 59.45%）；
  · 左上一只**对话气泡**（SVG：白底圆角泡 + 两粒渐小的泡，描边 #203170）；
  · 气泡内文字（色 #536ba9、水平垂直居中）：**三行**结构 ——
    `.dshwv-label`「DeepSeek 余额」66u/600 + `.dshwv-amount` 余额 128u/800
    + `.dshwv-hint` 说明 56u/#9fb0d9（u = 边长/1026，即 `--dshw-u`）。
气泡用**原版 SVG 原文**经 QSvgRenderer 渲染（同一份 path/ellipse，不手转弧线）；
QtSvg 缺席时退化成圆角矩形泡（数据照常）。数据面照旧复用 /dsh-whale/*（余额/今日
已用/上轮消耗/高峰提示），30s 轮询、点击刷新、拖拽换位、位置记 QSettings 全保留。

排版口径逐项对齐原版 CSS（`.dshwv-text` / `.dshwv-*` 那组规则）——**一个字都不许裁**：
  · 三行字号全走 `u` 制（不是自拟的 W 比例），比例与原版一致；
  · `.dshwv-text` 的 `left/top/width/height` 百分比分母是**气泡**（aspect 1026/700），
    不是正方窗口 —— 按窗口算会把文字区撑高 1.46 倍、长文案压到鲸鱼身上；
  · 原版 `.dshwv-text{white-space:nowrap}` 仍会被 `.dshwv-wrap{white-space:normal;
    max-width:560u}` 覆盖（靠 `ln.w` 标记）⇒ amount/hint 走**折行**；
  · 折行也放不下时（如超长金额断不开）逐档缩字号，绝不用 `elidedText` 截字。
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QByteArray, QRect, QRectF, QSettings, Qt, QTimer
from PySide6.QtGui import QFont, QFontMetrics, QPainter, QPixmap, QColor
from PySide6.QtWidgets import QApplication, QWidget

ROOT = Path(__file__).resolve().parent.parent
_ASSET = ROOT / "whale-widget" / "assets" / "DSniang1.png"
_SET = ("WXAgent", "persona-morph-ui")

# 原版气泡 SVG 原文（widget.js :10293-10297 逐字；viewBox 1026×700）——
# 白底泡体 + 拖尾两粒小泡，描边 #203170、线宽 18。
_BUBBLE_SVG = (
    '<svg viewBox="0 0 1026 700" preserveAspectRatio="xMidYMid meet" '
    'xmlns="http://www.w3.org/2000/svg">'
    '<path fill="#FFFFFF" stroke="#203170" stroke-width="18" stroke-linejoin="round" '
    'stroke-linecap="round" d="M 827 248 A 373 232 0 1 0 81 246 A 373 232 0 0 0 301 465 '
    'A 57 32 10 0 0 413 484 A 373 232 0 0 0 827 248 Z"/>'
    '<ellipse cx="352" cy="561" rx="37.5" ry="26" fill="#FFFFFF" stroke="#203170" '
    'stroke-width="18"/>'
    '<ellipse cx="442" cy="646" rx="24.5" ry="18" fill="#FFFFFF" stroke="#203170" '
    'stroke-width="18"/>'
    '</svg>')
_INK = QColor("#536ba9")       # 原版 .dshwv-text{color:#536ba9}（label/amount 同色）
_HINT_INK = QColor("#9fb0d9")  # 原版 .dshwv-hint{color:#9fb0d9}（比正文浅一档）
_LAB_INK = _INK                # label 与 amount 同色（原版三行共用 .dshwv-text 的 color）
_VBW, _VBH = 1026.0, 700.0 # 气泡 viewBox（aspect 1026/700）
_WHALE_RATIO = 0.5945 # 鲸图边长占比（widget.js :253）
_ZWSP = "\u200b" # 零宽空格：不可见，只提供一个换行点


def _wrapable(s: str) -> str:
    """给**断不开的长串**补断点 —— 千分位逗号后插零宽空格。

    Qt 的 `TextWordWrap` 只在空白/CJK 边界断行（逗号、句点都不算断点，与 CSS
    一致），而 Qt 的 `drawText` 一旦给 rect 就会**裁掉**溢出部分。上游是
    `overflow:visible`，溢出时字往外漫、一个字不丢；Qt 要做等价效果就得让它
    真能断行。金额加千分位逗号是唯一会长到超宽的纯数字串，故只处理它。
    零宽空格不进 `horizontalAdvance`（宽 0），量宽与显示口径都不受影响。
    """
    return s.replace(",", "," + _ZWSP) if "," in s else s


class WhaleWidget(QWidget):
    """无边框置顶小窗（原版复刻）：透明方块 + 气泡 + 鲸鱼；点击刷新、拖拽移动。"""

    W, H = 250, 250 # 原版默认边长（widget.js :245 clamp 的桌面上限值）

    def __init__(self, t, parent=None): # noqa: ANN001
        super().__init__(parent)
        self.setObjectName("WhaleWidget")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(self.W, self.H)
        self._drag0: QPoint | None = None
        self._win0: QPoint | None = None
        self._moved = False
        self._busy = False

        # 气泡内文字（原版口径：余额大字 + 说明小字，居中，#536ba9）
        # amount 行只放金额（「DeepSeek 余额」在 label 行，原版同）——
        # 加载态对齐原版 `render()`：`amount = '…'`、`hint = '加载中…'`。
        self.bal = "…"
        self.hint = "加载中…"

        try:
            pm = QPixmap(str(_ASSET))
            self._whale = pm if not pm.isNull() else QPixmap()
        except Exception: # noqa: BLE001 — 素材缺失时只有气泡，数据照常显示
            self._whale = QPixmap()

        # 气泡渲染器：原版 SVG 原文（QtSvg 缺席 ⇒ None，paintEvent 走圆角矩形退化）
        self._bub = None
        try:
            from PySide6.QtSvg import QSvgRenderer # noqa: PLC0415

            r = QSvgRenderer(QByteArray(_BUBBLE_SVG.encode("utf-8")))
            self._bub = r if r.isValid() else None
        except Exception: # noqa: BLE001
            self._bub = None

        self.restyle(t)

        # 位置：上次拖到哪就还在哪；没有记录落右下角
        pos = QSettings(*_SET).value("whale_pos")
        moved = False
        if isinstance(pos, list) and len(pos) == 2:
            try:
                self.move(int(pos[0]), int(pos[1]))
                moved = True
            except (TypeError, ValueError):
                moved = False
        if not moved:
            self._move_default()

        self._timer = QTimer(self)
        self._timer.setInterval(30000) # 清单 loadBalance 30s 口径
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        QTimer.singleShot(600, self.refresh) # 进窗即拉一次

    # ------------------------------------------------------------ 外观

    def restyle(self, t) -> None: # noqa: ANN001
        """主题切换时刷新字体（挂件是独立顶层窗，不走 Shell 的 QSS 重建）。

        字体**不靠继承**：挂件虽然 `parent=Shell`，但它是 `Qt.Tool` 顶层窗，
        一旦被单独拿出来渲染（离线探针、截图）就拿不到 Shell 的字体，
        `self.font()` 会退回一个没有中文字形的默认族 ⇒ 气泡里全是豆腐块。
        这里显式用主题的字体族起链（`qfont` 的回退链含 emoji 字体，
        与产品其余部分同一套口径），离线渲染也与线上一致。
        """
        self.t = t
        self._base_font = self._resolve_font(t)

    @staticmethod
    def _resolve_font(t): # noqa: ANN001
        """取一套带中文回退链的基准字体；主题不可用时退回当前控件字体。"""
        try:
            from stylekit_qt import qfont # noqa: PLC0415

            return qfont(t, 16)
        except Exception: # noqa: BLE001 — 主题对象异常不影响挂件出图
            f = QFont()
            f.setFamilies(["Microsoft YaHei UI", "Microsoft YaHei", "SimHei", "sans-serif"])
            return f

    def paintEvent(self, _e) -> None: # noqa: N802
        """原版复刻：先画气泡（原版 SVG），再画右下鲸鱼图，最后画气泡内文字。"""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # ① 气泡：占满窗口宽，高按 viewBox 比例（1026/700）
        bw = float(self.W)
        bh = bw * _VBH / _VBW
        if self._bub is not None:
            self._bub.render(p, QRectF(0, 0, bw, bh))
        else: # 退化：QtSvg 缺席时给个白底圆角泡（描边同色），文字照常
            p.setPen(QColor("#203170"))
            p.setBrush(QColor("#FFFFFF"))
            p.drawRoundedRect(QRectF(6, 6, bw - 12, bh - 12), 40, 40)

        # ② 鲸鱼图：右下角，边长 59.45%（原版 object-fit contain、右下对齐）。
        #    按 devicePixelRatio 放大再标 DPR —— 不做这一步高分屏会发糊（r9 用户实锤）。
        if self._whale is not None and not self._whale.isNull():
            dpr = self.devicePixelRatioF() or 1.0
            w = self.W * _WHALE_RATIO
            pm = self._whale.scaled(int(w * dpr), int(w * dpr),
                                    Qt.AspectRatioMode.KeepAspectRatio,
                                    Qt.TransformationMode.SmoothTransformation)
            pm.setDevicePixelRatio(dpr)
            p.drawPixmap(int(self.W - w), int(self.H - w), pm)

        # ③ 气泡内文字：中心 (44.25%, 36%)，区域宽 66% 高 64%
        #    ⛔ 「漏字」根因：原版 `.dshwv-text{white-space:nowrap}`（不换行、不省略），
        #    字号按 `--dshw-u = base/1026` 缩放 —— label 66u / amount 128u /
        #    period 104u / hint 56u。此前 Qt 侧自拟了 0.088W / 0.044W 两档 +
        #    `elidedText(ElideRight)`，把 hint 截成「…」= 用户看到的漏字。
        #
        # 原版三行结构（widget.js `textBox` 子元素顺序）：
        #   .dshwv-label「DeepSeek 余额」66u/600 + .dshwv-amount 128u/800
        #   + .dshwv-hint 56u #9fb0d9。此前 Qt 把 label 并进 amount（「余额 ¥x」），
        #   128u 的大字背着一串中文 ⇒ 必然超宽。
        #
        # 长行怎么办 —— 原版有第二套规则 `.dshwv-wrap{white-space:normal;
        # max-width:calc(u*560);line-height:1.2}`，靠 `ln.w` 标记决定该行
        # **换行**而不是 nowrap。Qt 侧照此分两类：
        #   · label：nowrap（固定 6 字，永远不会溢出）；
        #   · amount / hint：**换行**（Qt.TextWordWrap + 560u 宽上限）。
        #
        # ⛔ 为什么 amount 也必须是 wrap：原版 `.dshwv-text` 的 `white-space:nowrap`
        #    被 `.dshwv-wrap` 的 `white-space:normal` 覆盖 —— 一旦某行带 `ln.w`，
        #    该行**折行**而不是溢出。amount 走 wrap 有双重收益：
        #      ① `¥ 123,456.78` 超宽时在 `¥ ` 后断行 —— 与浏览器一致，一个字不裁；
        #      ② 金额本身不该无限缩字号（缩到 8px 不可读），换行比缩字更接近原版。
        #    此前只让 hint 换行、amount 靠 nowrap+缩字号 ⇒ 超大金额缩到下限
        #    仍超宽 22px 被 `drawText` 裁掉（探针实锤）。
        # ⛔ 百分比的分母是**气泡**不是窗口：原版 `.dshwv-text` 挂在
        #    `.dshwv-pop`（`aspect-ratio:1026/700`）下，`left/top/width/height`
        #    的 `%` 全部相对 **.dshwv-pop 的盒子**解析。而 .dshwv-pop 的宽等于
        #    `--dshw-base`（=W）、高 = `W * 700/1026`（气泡是扁的，不是正方）。
        #    此前按 `self.H`（正方形）算 ⇒ 文字区被撑到 160px 高、中心落在
        #    90px —— 比气泡真实的纵向中心（61px）低 29px，长 hint 就压到鲸鱼身上。
        #    按 `bh` 算即与原版一致。
        cx, cy = bw * 0.4425, bh * 0.36
        tw = bw * 0.66
        th = bh * 0.64
        area = QRectF(cx - tw / 2, cy - th / 2, tw, th)
        u = self.W / 1026.0 # 原版 --dshw-u（base=W）
        wrap_w = min(tw, u * 560.0) # 原版 .dshwv-wrap max-width:560u

        def _mk(px: int, weight) -> QFont: # noqa: ANN001
            f = QFont(getattr(self, "_base_font", None) or self.font())
            f.setPixelSize(max(8, int(px)))
            f.setWeight(weight)
            return f

        # 行表：(字体, 文本, 颜色, 是否 wrap)
        #   label  → nowrap（定长 6 字）
        #   amount → wrap（超宽时在 `¥ ` 后断行 —— 原版 fmt() 就带这个空格）
        #   hint   → wrap（多段「·」拼接，必然最长）
        #
        # ⛔ 换行点从哪来：Qt 的 `TextWordWrap` 只在**空白与 CJK 边界**断行，
        #    逗号/句点**不是**断点（CSS 同理）。所以 `¥123,456.78` 这种纯数字串
        #    在 Qt 里断不开 —— 而 Qt 的 `drawText(rect, …)` 会用 rect **裁掉**
        #    溢出部分（浏览器是 overflow:visible 直接外溢、一个字不丢；Qt 会切字）。
        #    两条路：① 按原版 fmt() 补上 `¥ ` 的空格（天然断点）；② 千分位逗号后
        #    插 U+200B 零宽空格（不可见，只提供一个断点）。两条都做 ⇒ 任意长度金额
        #    都能折行，一个字不裁。
        lines = [(_mk(round(u * 66), QFont.Weight.DemiBold), "DeepSeek 余额", _LAB_INK, False)]
        if self.bal:
            lines.append((_mk(round(u * 128), QFont.Weight.ExtraBold), _wrapable(self.bal), _INK, True))
        hint_txt = " · ".join(x for x in (self.hint or "").split("\n") if x)
        if hint_txt:
            lines.append((_mk(round(u * 56), QFont.Weight.Normal), _wrapable(hint_txt), _HINT_INK, True))

        gap = max(1, int(round(u * 9))) # 原版 hint margin-top:9u

        def _layout(scale: float) -> tuple[list, float, float]:
            """按 scale 排一次版 → (行表, 总高, 最大溢出量)。

            ⚠️ `QFontMetrics.boundingRect` 只吃 **QRect**（整型）——传 QRectF 会
            TypeError（实测）。高用 4000 当"不限高"，取回真实需要的高度。

            "溢出量"的口径（对 nowrap 与 wrap **统一**）：
              · nowrap 行：需要宽 > 可用宽 = 溢出（drawText 会裁掉右边）；
              · wrap  行：断行后仍有**单段比可用宽还长** = 溢出（Qt 断不开它）。
            两种溢出都会让 `drawText` 静默裁字，所以都进 `over`，都由外层缩字号兜。
            （实测：`¥ 123,456.78` 在 250px 底、29px 字号下，`123,` 一段就有 144px，
             超过文字区的 136px —— 补空格与零宽空格都救不了，只能缩字号。）
            """
            out, total, over = [], 0.0, 0.0
            for f, txt, col, do_wrap in lines:
                ff = QFont(f)
                if scale < 0.999:
                    ff.setPixelSize(max(7, int(round(f.pixelSize() * scale))))
                mm = QFontMetrics(ff)
                avail = wrap_w if do_wrap else tw
                flags = (int(Qt.AlignmentFlag.AlignHCenter)
                         | (int(Qt.TextFlag.TextWordWrap) if do_wrap
                            else int(Qt.AlignmentFlag.AlignVCenter)))
                need = mm.boundingRect(QRect(0, 0, int(avail), 4000), flags, txt)
                h = float(need.height())
                if txt.strip() and not txt.isspace():
                    if do_wrap:
                        # 断行后每段都必须放得下（段 = 空白/零宽空格切出的最小块）
                        for seg in txt.split(" "):
                            for sub in seg.split(_ZWSP):
                                if sub:
                                    over = max(over, mm.horizontalAdvance(sub) - float(avail))
                    else:
                        over = max(over, float(need.width()) - float(avail))
                out.append((txt, ff, col, avail, h, do_wrap))
                total += h
            total += gap * max(0, len(lines) - 1)
            return out, total, over

        # 逐档缩字号：**纵向塞不下**（total > 文字区高）或**横向有断不开的长段**
        # （over > 0.5，见 _layout 的溢出口径）任一成立就缩一档。
        # 下限 0.40：`¥ 123,456.78` 这类超长金额在 250px 底、29px 字号下，
        # `123,` 一段就 144px（文字区仅 136px）—— 缩到 0.76 才放得下。
        # 再长就压到 0.40（约 9px），仍不裁字。
        scale = 1.0
        laid, total, over = _layout(scale)
        while (total > area.height() or over > 0.5) and scale > 0.401:
            scale = max(0.40, scale - 0.06)
            laid, total, over = _layout(scale)

        y = area.top() + max(0.0, (area.height() - total) / 2)
        for txt, ff, col, avail, h, do_wrap in laid:
            p.setFont(ff)
            p.setPen(col)
            flags = (Qt.AlignmentFlag.AlignHCenter
                     | (Qt.TextFlag.TextWordWrap if do_wrap else Qt.AlignmentFlag.AlignVCenter))
            p.drawText(QRectF(area.left(), y, avail, h), int(flags), txt)
            y += h + gap

    # ------------------------------------------------------------ 数据

    def refresh(self) -> None:
        """拉一次 /dsh-whale/*（后台线程 + QTimer 回主线程同工位 _async 模式）。"""
        if self._busy:
            return
        self._busy = True
        self.bal = "…"
        self.hint = "加载中…"
        self.update()
        bx: dict = {"done": False, "bal": None, "lt": None, "err": None}

        def _work() -> None:
            import config_io # noqa: PLC0415

            try:
                bx["bal"] = config_io.get_json("/dsh-whale/balance.json", timeout=15.0)
                try:
                    bx["lt"] = config_io.get_json("/dsh-whale/last-turn.json", timeout=8.0)
                except Exception: # noqa: BLE001 — 上轮消耗拿不到不影响余额
                    bx["lt"] = None
            except Exception as e: # noqa: BLE001
                bx["err"] = str(e)
            bx["done"] = True

        threading.Thread(target=_work, daemon=True, name="whale-fetch").start()

        def _apply() -> None:
            if not bx["done"]:
                QTimer.singleShot(300, _apply)
                return
            self._busy = False
            self._apply_data(bx.get("bal"), bx.get("lt"), bx.get("err"))

        QTimer.singleShot(300, _apply)

    def _apply_data(self, bal, lt, err) -> None: # noqa: ANN001
        sym = "¥"
        hint1 = hint2 = ""
        if isinstance(bal, dict) and bal.get("ok"):
            sym = "$" if str(bal.get("currency") or "CNY").upper() == "USD" else "¥"
            try:
                total = float(bal.get("totalBalance") or 0)
            except (TypeError, ValueError):
                total = 0.0
            # 只放数字 —— 原版 amount 行就是纯金额（「DeepSeek 余额」在 label 行）。
            # `"¥ 1,234.56"` 的**空格**照原版 fmt()（widget.js :12358
            # `'¥ ' + fixed`）——它同时是唯一的天然换行点，超大金额靠它折行不裁字。
            self.bal = "%s %s" % (sym, format(total, ",.2f"))
            try:
                today = float(bal.get("todayUsage") or 0)
            except (TypeError, ValueError):
                today = 0.0
            hint1 = "今日已用 %s %.4f" % (sym, today)
            hint2 = "高峰时段（价贵）" if bal.get("isPeak") else ""
        elif isinstance(bal, dict) and bal.get("ok") is False:
            self.bal = ""
            hint1 = str(bal.get("error") or "没配模型 Key")[:22]
        else:
            self.bal = ""
            hint1 = (str(err) if err else "后台没连上")[:22]
        if isinstance(lt, dict) and lt.get("turn") is not None:
            try:
                amt = float(lt.get("amount") or 0)
            except (TypeError, ValueError):
                amt = 0.0
            tok = lt.get("tokens")
            hint2 = " ".join([x for x in (hint2, "上轮 %s %.4f%s" % (
                sym, amt, (" · %s tok" % tok) if tok else "")) if x]).strip()
        self.hint = "\n".join([x for x in (hint1, hint2) if x])
        self.update()

    # ------------------------------------------------------------ 交互（点击刷新 / 拖拽移动 / 右键菜单）

    def contextMenuEvent(self, ev) -> None: # noqa: N802
        """右键菜单 —— 原版 widget.js 菜单按钮（:286）的 Qt 等价核心项。
        原版的音效/角色皮肤等浏览器特效项不搬（whale-widget/PORT-NOTES 明示的裁剪）；
        余额显示三选一在顶栏「已改」里（两者独立，web 同款）。"""
        from PySide6.QtWidgets import QMenu # noqa: PLC0415

        m = QMenu(self)
        m.addAction("刷新余额", self.refresh)
        m.addAction("回到右下角", self.reset_position)
        m.exec(ev.globalPos())

    def mousePressEvent(self, ev) -> None: # noqa: N802
        if ev.button() == Qt.MouseButton.LeftButton:
            self._drag0 = ev.globalPosition().toPoint()
            self._win0 = self.pos()
            self._moved = False

    def mouseMoveEvent(self, ev) -> None: # noqa: N802
        if self._drag0 is None or self._win0 is None:
            return
        d = ev.globalPosition().toPoint() - self._drag0
        if not self._moved and (abs(d.x()) + abs(d.y())) > 5:
            self._moved = True # 位移阈值：小于它算「点击」而非拖拽
        if self._moved:
            self.move(self._win0 + d)

    def mouseReleaseEvent(self, ev) -> None: # noqa: N802
        if self._drag0 is not None:
            if self._moved:
                QSettings(*_SET).setValue("whale_pos", [self.x(), self.y()])
            else:
                self.refresh() # 点击（没拖动）= 刷新
        self._drag0 = None
        self._win0 = None

    def _move_default(self) -> None:
        try:
            scr = QApplication.primaryScreen()
            geo = scr.availableGeometry() if scr else None
            if geo is not None:
                self.move(geo.right() - self.W - 18, geo.bottom() - self.H - 18)
        except Exception: # noqa: BLE001
            pass

    def reset_position(self) -> None:
        """回到右下角（位置记忆清掉）——留给「找不到挂件了」的救援路径。"""
        QSettings(*_SET).setValue("whale_pos", "")
        self._move_default()
