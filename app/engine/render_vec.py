# -*- coding: utf-8 -*-
"""render_vec.py —— 模板 → 矢量 PDF。

和 render.py 是同一张版面的两套画法：
  · render.py 把每个元素画成位图（Pillow），给界面预览和 PNG 用；
  · 这里把每个元素画成**真矢量**（pymupdf 的原生文字 / 线条 / 矩形），
    导出 PDF 时放大无限清晰。

为什么不是「把位图塞进 PDF 再叠文字层」
------------------------------------
render.py 导出的 PDF 是「图片 + 不可见文字」，画面是一张 300dpi 位图。
放大到 400%、拿去喷绘都会糊。要真清晰，得让 PDF 里的每一个字、每一条线
都是矢量对象，阅读器在任意缩放级别重新光栅化。

关键难点是**文字要和位图版一字不差地对上**。位图版里文字的落点靠
labelkit 的 `text_h`（墨迹上沿对齐）/ `fit`（拉满盒子）算出来的；这里
复用了 render.py 里那套度量（`text_metrics` / `_text_ink_bbox`），只把
「落点 → 位图」换成「落点 → pymupdf 文本」，所以两版对得上一字不差。

字体嵌入
--------
图纸规定的是思源黑体（Noto Sans SC），随程序放在 data/素材/字体。
pymupdf 的 insert_font 能直接嵌这些 .otf，PDF 里的字和界面/PNG 是同一个
字体，不存在「PDF 里换了个字体、字宽不一样」的问题。字重按 kind 选四档。
"""

import io
import os

import pymupdf

import labelkit as LK
import render as R          # 复用它的度量与占位符填充
from placeholders import fill_placeholders

_MU = 72 / 25.4             # 毫米 → PDF 点


# ── 字体 ──────────────────────────────────────────────────────────
# kind → (字体文件, pymupdf 里注册的名字)。文件随程序走，两平台同源。
_FONTFILE = {
    "siyuanl": "NotoSansSC-Light.otf",
    "siyuan":  "NotoSansSC-Regular.otf",
    "siyuanm": "NotoSansSC-Medium.otf",
    "siyuanb": "NotoSansSC-Bold.otf",
    # 没有自带字体的族，落回思源正体 —— 字形差异极小，且这些族在两张模板里
    # 只用于纯数字/拉丁（中文由 cjk_kind 兜回有汉字的族）。
    "hei":     "NotoSansSC-Regular.otf",
    "heib":    "NotoSansSC-Bold.otf",
    "song":    "NotoSansSC-Regular.otf",
    "songb":   "NotoSansSC-Bold.otf",
    "lat":     "NotoSansSC-Regular.otf",
    "latb":    "NotoSansSC-Bold.otf",
    "mono":    "NotoSansSC-Regular.otf",
    "impact":  "NotoSansSC-Bold.otf",
    "times":   "NotoSansSC-Regular.otf",
    "timesb":  "NotoSansSC-Bold.otf",
    "georgia": "NotoSansSC-Regular.otf",
    "georgiab": "NotoSansSC-Bold.otf",
}


class _VecPage:
    """一次矢量绘制：持有一个 pymupdf page + 已注册字体表。"""

    def __init__(self, page, fontdir):
        self.page = page
        self._fonts = {}          # kind → 注册名
        self._byfile = {}         # 字体文件路径 → 注册名（按文件去重）
        self._fontobjs = {}       # kind → pymupdf.Font（量宽用）
        self._fontdir = fontdir
        self._imgcache = {}       # 内容键 → xref（fit/vtext 位图去重）

    def _font(self, kind):
        kind = kind or "siyuan"
        name = self._fonts.get(kind)
        if name is None:
            fp = _FONTFILE.get(kind)
            path = os.path.join(self._fontdir, fp) if fp else ""
            if fp and os.path.exists(path):
                # 按**文件**去重：siyuan/hei/song/lat 都落到同一个 Regular.otf，
                # 各注册一个名字只会把同一份 8MB 字体嵌 4 遍。
                name = self._byfile.get(path)
                if name is None:
                    name = "f%d" % len(self._fonts)
                    self.page.insert_font(fontfile=path, fontname=name)
                    self._byfile[path] = name
                try:
                    self._fontobjs[kind] = pymupdf.Font(fontfile=path)
                except Exception:
                    self._fontobjs[kind] = None
            else:
                name = "china-ss"
                self._fontobjs[kind] = None
            self._fonts[kind] = name
        return name

    def _advance(self, s, kind, sz):
        """pymupdf 口径的文本前进宽度（点）。拿不到就用经验值估。"""
        fo = self._fontobjs.get(kind)
        if fo is not None:
            try:
                return fo.text_length(s, fontsize=sz)
            except Exception:
                pass
        return sz * 0.55 * len(s)

    # 单位：毫米入参，内部转点
    def _baseline(self, s, size, kind):
        """墨迹上沿 → 基线 的毫米偏移。

        位图版（labelkit.text_h）的 y 是**墨迹上沿**：它先按字高定字号，再拿
        textbbox 量出「墨迹顶相对基线的高度」把字贴到 y 上。这里要反向推出
        基线在哪：基线 = 墨迹上沿 + (字体的 ascent − textbbox.top)。

        PIL 的 textbbox((0,0)) 返回的 top 是「墨迹顶相对**ascent 线**的距离」
        （正数 = 在 ascent 线下方），所以墨迹顶到基线的距离 = ascent − top。
        """
        if not s:
            return size * 0.78
        try:
            from PIL import Image, ImageDraw
            px = max(4, int(round(size * 300 / 25.4)))
            f = LK.font_px(kind or "siyuan", px)
            asc, _desc = f.getmetrics()
            d = ImageDraw.Draw(Image.new("L", (4, 4)))
            b = d.textbbox((0, 0), s, font=f)
            above = (asc - b[1]) / (300 / 25.4)   # 墨迹顶到基线，毫米
            return above
        except Exception:
            return size * 0.78

    def text(self, x, y, s, size, kind, color="#111111", align="left",
             rot=0):
        """在 (x, y) 写一段字。y 是**墨迹上沿**（和 labelkit.text_h 口径一致）。"""
        if not s:
            return
        sz = max(1.0, size * _MU)
        base = (y + self._baseline(s, size, kind)) * _MU
        px = x * _MU
        if align == "center":
            w = pymupdf.get_text_length(s, fontname=self._font(kind), fontsize=sz)
            px -= w / 2.0
        elif align == "right":
            w = pymupdf.get_text_length(s, fontname=self._font(kind), fontsize=sz)
            px -= w
        try:
            self.page.insert_text((px, base), s, fontname=self._font(kind),
                                  fontsize=sz, color=_rgb(color))
        except Exception:
            pass

    def text_h(self, x, y, h, s, kind, color="#111111", align="left",
               maxw=None, valign="top"):
        """按目标字高 h 定字号（不拉伸），墨迹上沿对齐 y —— 镜像 labelkit.text_h。"""
        if not s:
            return
        kind = kind or ("hei" if LK.has_cjk(s) else "lat")
        w0, h0, bb0, _k = LK.text_metrics(s, 10.0, kind)
        if h0 <= 0:
            return
        size = 10.0 * h / h0
        w = w0 * size / 10.0
        if maxw and w > maxw:
            size *= float(maxw) / w
            w = float(maxw)
        base_mm = y + self._baseline(s, size, kind)
        px = x * _MU
        sz = max(1.0, size * _MU)
        if align == "center":
            px -= w / 2.0 * _MU
        elif align == "right":
            px -= w * _MU
        if valign == "middle":
            base_mm -= (h0 * size / 10.0 - h) / 2.0
        try:
            self.page.insert_text((px, base_mm * _MU), s,
                                  fontname=self._font(kind), fontsize=sz,
                                  color=_rgb(color))
        except Exception:
            pass
        return w

    def _insert_bmp(self, key, buf, x, y, w, h):
        """按内容去重嵌位图：相同 PNG 字节只嵌一次，重复处复用同一 xref。

        四宫格同一段「拉伸字」会各画一遍，若每遍都新嵌一张 PNG，一张 PDF
        轻松上百张图、十几兆。这里按 (kind,color,text,尺寸) 作键，第二次起
        直接用第一次插入的 xref，PDF 里只存一份。
        """
        xref = self._imgcache.get(key)
        if xref is None:
            xref = self.page.insert_image(
                pymupdf.Rect(x * _MU, y * _MU, (x + w) * _MU, (y + h) * _MU),
                stream=buf)
            self._imgcache[key] = xref
        else:
            self.page.insert_image(
                pymupdf.Rect(x * _MU, y * _MU, (x + w) * _MU, (y + h) * _MU),
                xref=xref)

    def fit(self, x, y, w, h, s, kind, color="#111111", align="left"):
        """拉满 w×h 盒子（字标 / logo 用）。

        完全照 labelkit.fit 的算法：同一探测字号、同一墨迹裁剪、同一拉伸，
        只是把结果以图的方式嵌进 PDF —— 所以和位图版一字不差。这类「拉伸
        到盒子」的字在源文件里本来就是位图，硬转矢量反而会因侧承不同错位。
        """
        if not s or w <= 0 or h <= 0:
            return
        kind = kind or ("hei" if LK.has_cjk(s) else "latb")
        try:
            from PIL import Image
            # 探测字号和 labelkit.fit 完全一致：min(180, max(32, round(h*k)))
            probe = min(180, max(32, int(round(h * 300 / 25.4))))
            mask = LK.font_px(kind, probe).getmask(s, mode="L")
            tmp = Image.frombytes("L", mask.size, bytes(mask))
            bb = tmp.getbbox()
            if bb:
                tmp = tmp.crop(bb)
            if tmp.width <= 0 or tmp.height <= 0:
                return
            tw, th = max(1, int(round(w * 300 / 25.4))), max(1, int(round(h * 300 / 25.4)))
            tmp = tmp.resize((tw, th), Image.LANCZOS)
            px = {"left": x, "center": x - w / 2.0, "right": x - w}[align]
            rgb = _rgb(color)
            from PIL import Image as _I
            colored = _I.new("RGB", tmp.size,
                             (int(rgb[0] * 255), int(rgb[1] * 255), int(rgb[2] * 255)))
            buf = _I.new("RGB", tmp.size, (255, 255, 255))
            buf.paste(colored, (0, 0), tmp)
            key = ("fit", kind, color, s, tw, th)
            self._insert_bmp(key, _png_bytes(buf), px, y, w, h)
        except Exception:
            pass

    def rect(self, x0, y0, x1, y1, w=0.25, color="#000000", fill=None, r=0):
        p = self.page
        rct = pymupdf.Rect(x0 * _MU, y0 * _MU, x1 * _MU, y1 * _MU)
        if r:
            p.draw_rect(rct, color=_rgb(fill) if fill else None,
                        fill=_rgb(fill) if fill else None,
                        width=w * _MU, radius=r * _MU)
        elif fill:
            p.draw_rect(rct, color=None, fill=_rgb(fill))
        else:
            p.draw_rect(rct, color=_rgb(color), fill=None, width=w * _MU)

    def line(self, x0, y0, x1, y1, w=0.25, color="#000000"):
        self.page.draw_line(pymupdf.Point(x0 * _MU, y0 * _MU),
                            pymupdf.Point(x1 * _MU, y1 * _MU),
                            color=_rgb(color), width=w * _MU)

    def image(self, path, x, y, w=None, h=None):
        """贴位图素材（logo、字标图、底图）。这些本来就是图，没法矢量化。

        注意：不能直接 insert_image(filename=path)。pymupdf 会把 PNG 解码成
        未压缩的原始 RGB 再存，一张 62KB 的底图会被撑到 26MB。这里用 Pillow
        重新编码：无透明的 RGB 转 JPEG（底图/照片类压缩率极高），带透明的
        RGBA 走压缩 PNG，再以 stream 形式嵌入，保住压缩。
        """
        if not path or not os.path.exists(path):
            return
        try:
            from PIL import Image
            im = Image.open(path)
            iw, ih = im.size
            if w is None and h is None:
                w, h = iw / 25.4, ih / 25.4
            if w is None:
                w = h * iw / float(ih)
            if h is None:
                h = w * ih / float(iw)
            buf = _image_bytes(im)
            if buf is None:
                return
            self.page.insert_image(
                pymupdf.Rect(x * _MU, y * _MU, (x + w) * _MU, (y + h) * _MU),
                stream=buf)
        except Exception:
            pass


def _rgb(hexs):
    hexs = str(hexs or "#000000").lstrip("#")
    if len(hexs) < 6:
        hexs = "000000"
    return (int(hexs[0:2], 16) / 255, int(hexs[2:4], 16) / 255,
            int(hexs[4:6], 16) / 255)


def _png_bytes(img):
    """PIL 图 → PNG 字节流（pymupdf insert_image 用）。"""
    b = io.BytesIO()
    img.save(b, "PNG")
    return b.getvalue()


def _image_bytes(im):
    """素材图 → 压缩字节流。

    RGB（无透明）转 JPEG，压缩率远高于 PNG；RGBA（带透明）走 PNG 保留 alpha。
    返回 bytes 或 None（无法处理时）。
    """
    try:
        from PIL import Image
        if im.mode in ("RGBA", "LA", "P"):
            # 有透明通道：转 RGBA 再压 PNG，避免丢透明
            rgba = im.convert("RGBA")
            b = io.BytesIO()
            rgba.save(b, "PNG", optimize=True)
            return b.getvalue()
        rgb = im.convert("RGB")
        b = io.BytesIO()
        rgb.save(b, "JPEG", quality=90)
        return b.getvalue()
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════
#  绘制：镜像 render.py 的叠放顺序
# ══════════════════════════════════════════════════════════════════
def _draw_graphic(v, g, fields, fam):
    t = g.get("type") or ("rect" if g.get("fill") else "image")
    x, y = float(g.get("x", 0)), float(g.get("y", 0))
    w, h = float(g.get("w", 0)), float(g.get("h", 0))

    if t == "rect":
        lw = float(g.get("lw", 0.25))
        stroke, fill = g.get("stroke"), g.get("fill")
        if g.get("r"):
            v.rect(x, y, x + w, y + h, w=lw, color=stroke or "#000000",
                   fill=fill, r=float(g["r"]))
        elif fill and not stroke:
            v.rect(x, y, x + w, y + h, fill=fill)
        else:
            v.rect(x, y, x + w, y + h, w=lw, color=stroke or "#000000")

    elif t == "line":
        v.line(x, y, float(g.get("x2", x)), float(g.get("y2", y)),
               w=float(g.get("lw", 0.25)), color=g.get("stroke") or "#000000")

    elif t == "barcode":
        # 条码当前留空（外部工具生成），但真给 seq 时也能画：一条条竖线就是矢量
        seq = fill_placeholders(str(g.get("seq", "")), fields)
        if seq:
            _vec_barcode(v, x, y, w, h, seq)

    elif t == "qrcode":
        seq = fill_placeholders(str(g.get("seq", "")), fields)
        if seq:
            _vec_qr(v, x, y, w, seq, g.get("color") or "#000000")

    elif t == "hourglass":
        _vec_hourglass(v, x, y, w, h, g.get("color") or "#000000")

    elif t == "vtext":
        text = fill_placeholders(str(g.get("t", "")), fields)
        if text:
            _vec_vtext(v, x, y, w, h, text, g.get("color") or "#111111",
                       g.get("kind") or fam)

    elif t == "vimage":
        # 位图竖排，直接贴图兜底
        src = g.get("src")
        v.image(_asset(src), x, y, w, h)

    elif t == "comp":
        _vec_comp(v, g)
    elif t == "compvals":
        _vec_comp_values(v, g, fields)

    else:  # image
        v.image(_asset(g.get("src")), x, y, w, h)


# 当前渲染的模板名（save_pdf / save_batch_pdf 入口登记），找素材用
_TPL_NAME = None


def _asset(src):
    if not src:
        return ""
    if src.startswith("data:"):
        return ""                      # dataURL 少见，位图兜底已在 image() 里跳过
    if os.path.isabs(src):
        return src
    import store
    return store.resolve_ref(_TPL_NAME, src) or ""


def _draw_block(v, b, fields, fam):
    """文字块分发：text / list / table / vtext / legacy（和 render._draw_block 对齐）。"""
    t = b.get("type")
    if t == "text":
        _text_box(v, b, fields, fam)
    elif t == "list":
        _list_box(v, b, fields, fam)
    elif t == "table":
        _table_box(v, b, fields, fam)
    elif t == "vtext":
        _vtext(v, b, fields, fam)
    else:
        _legacy_block(v, b, fields, fam)


def _text_box(v, b, fields, fam):
    text = fill_placeholders(str(b.get("t", "")), fields)
    if not text:
        return
    x, y = float(b.get("x", 0)), float(b.get("y", 0))
    w, h = float(b.get("w", 0) or 0), float(b.get("h", 0) or 0)
    kind = R._kind_of(b, text, fam)
    color = b.get("color") or "#111111"
    align = b.get("align") or "left"
    xs = float(b.get("xs") or 1.0)

    if b.get("fit") and w and h:
        v.fit(x, y, w, h, text, kind=kind, color=color, align=align)
    elif h and xs != 1.0:
        tw = R._ink_w(text, h, fam, dict(b, kind=kind)) * xs
        v.fit(x, y, tw, h, text, kind=kind, color=color, align=align)
    elif h:
        v.text_h(x, y, h, text, kind=kind, color=color, align=align,
                 maxw=w or None, valign=b.get("valign") or "top")
    else:
        v.text(x, y, text, float(b.get("size", 3)), kind=kind, color=color)

    if b.get("box"):
        bx0, by0, bw, bh = R._box_geom(b, fields, fam)
        v.rect(bx0, by0, bx0 + bw, by0 + bh,
               w=float(b.get("box_lw", 0.2)),
               color=b.get("box_color") or color)


def _list_box(v, b, fields, fam):
    val = fields.get(str(b.get("字段") or ""))
    if val in (None, ""):
        return
    items = list(val) if isinstance(val, (list, tuple)) else str(val).split("\n")
    x, y = float(b.get("x", 0)), float(b.get("y", 0))
    w, h = float(b.get("w", 0) or 0), float(b.get("h", 0) or 0)
    step = float(b.get("step", 3.0))
    color = b.get("color") or "#111111"
    xs = float(b.get("xs") or 1.0)
    ws = b.get("宽") or []
    kind = b.get("kind") or LK.cjk_kind(fam)
    for i, it in enumerate(items[:int(b.get("max", 200))]):
        s = str(it)
        if not s:
            continue
        yy = y + i * step
        tw = ws[i] if i < len(ws) else 0
        if tw and h:
            v.fit(x, yy, float(tw), h, s, kind=kind, color=color)
        elif b.get("fit") and w and h:
            v.fit(x, yy, w, h, s, kind=kind, color=color)
        elif h and xs != 1.0:
            v.fit(x, yy, R._ink_w(s, h, fam, {"kind": kind}) * xs, h, s,
                  kind=kind, color=color)
        else:
            v.text_h(x, yy, h or 2.0, s, kind=kind, color=color, maxw=w or None)


def _table_box(v, b, fields, fam):
    cols = b.get("cols") or [10, 10, 10, 10]
    rows = b.get("rows") or [4]
    cells = b.get("cells") or []
    gx, gy = float(b.get("x", 0)), float(b.get("y", 0))
    lw = float(b.get("lw", 0.25))
    cy = gy
    for ri, rh in enumerate(rows):
        cx = gx
        for ci, cw in enumerate(cols):
            v.rect(cx, cy, cx + cw, cy + rh, w=lw,
                   color=b.get("stroke") or "#333333")
            txt = ""
            if ri < len(cells) and ci < len(cells[ri]):
                txt = cells[ri][ci] or ""
            txt = fill_placeholders(txt, fields)
            if txt:
                v.fit(cx + 1.2, cy + rh * 0.12, cw - 2.4, rh * 0.7, txt,
                      kind=LK.cjk_kind(fam), color="#111111")
            cx += cw
        cy += rh


def _vtext(v, b, fields, fam):
    text = fill_placeholders(str(b.get("t", "")), fields)
    if not text:
        return
    x, y = float(b.get("x", 0)), float(b.get("y", 0))
    w, h = float(b.get("w", 0) or 0), float(b.get("h", 0) or 0)
    color = b.get("color") or "#111111"
    kind = b.get("kind") or fam
    _vec_vtext(v, x, y, w, h, text, color, kind)


def _legacy_block(v, b, fields, fam):
    """2.0 遗留：bars（条码）+ lines（散点文字）。"""
    for bar in b.get("bars") or []:
        seq = fill_placeholders(str(bar.get("seq", "")), fields)
        if seq:
            _vec_barcode(v, float(bar.get("x", 0)), float(bar.get("y", 0)),
                         float(bar.get("w", 0)) or None,
                         float(bar.get("h", 10)), seq)
    for ln in b.get("lines") or []:
        text = fill_placeholders(ln.get("t", ""), fields)
        if not text:
            continue
        kind = fam if LK.has_cjk(text) else ("latb" if ln.get("b") else "lat")
        v.text(float(ln.get("x", 0)), float(ln.get("y", 0)), text,
               float(ln.get("sz", 3)), kind=kind,
               color=ln.get("c") or "#111111")


def _draw_group(v, grp, fields, fam):
    elems = grp.get("元素") or []
    gdx, gdy = float(grp.get("dx", 0) or 0), float(grp.get("dy", 0) or 0)
    for pos in grp.get("位置") or []:
        ox = float(pos.get("x", 0)) + gdx
        oy = float(pos.get("y", 0)) + gdy
        cw = float(pos.get("w", 0))
        tags = pos.get("tag") or []
        if isinstance(tags, str):
            tags = [tags]
        for e in elems:
            need = e.get("need")
            if need and need not in tags:
                continue
            g = dict(e)
            if "dxr" in g:
                g["x"] = ox + cw - float(g["dxr"])
                g.setdefault("align", "right")
            else:
                g["x"] = ox + float(g.get("dx", 0))
            g["y"] = oy + float(g.get("dy", 0))
            if g.get("type") in (None, "text"):
                _text_box(v, g, fields, fam)
            elif g.get("type") == "list":
                _list_box(v, g, fields, fam)
            else:
                _draw_graphic(v, g, fields, fam)


def _vec_comp(v, g):
    x0, x1 = float(g["x0"]), float(g["x1"])
    xm = float(g["xm"])
    ys = [float(t) for t in g["ys"]]
    lw = float(g.get("lw", 0.25))
    for y in ys:
        v.line(x0, y, x1, y, w=lw)
    v.line(x0, ys[0], x0, ys[-1], w=lw)
    v.line(x1, ys[0], x1, ys[-1], w=lw)
    v.line(xm, ys[0], xm, ys[1], w=lw)


def _vec_comp_values(v, g, fields):
    x0, x1 = float(g["x0"]), float(g["x1"])
    xm = float(g["xm"])
    ys = [float(t) for t in g["ys"]]
    rows = fields.get(g.get("字段") or "顺应性") or g.get("rows") or []
    dy = float(g.get("dy", 1.33))
    th = float(g.get("th", 1.95))
    th_r = float(g.get("th_r", 1.45))
    lcx = x0 + float(g.get("lcx", 9.10))
    dcx = float(g.get("dcx") or (xm + x1) / 2.0 + 1.60)
    for i, row in enumerate(rows[:len(ys) - 2]):
        p, d, note = (list(row) + ["", "", ""])[:3]
        yy = ys[i + 1] + dy
        ps = fill_placeholders(str(p), fields)
        ns = fill_placeholders(str(note), fields)
        if ps and ns:
            th_n = float(g.get("th_note", 1.15))
            w1 = _text_w_mm(ps, th, "lat")
            w2 = _text_w_mm(ns, th_n, "hei")
            gap = float(g.get("gap", 0.8))
            avail = (xm - x0) - 3.0
            total = w1 + gap + w2
            if total > avail > 0:
                r = avail / total
                th, th_n = th * r, th_n * r
                w1 = _text_w_mm(ps, th, "lat")
                total = w1 + gap + w2
            start = lcx - total / 2.0
            v.text_h(start, yy, th, ps, "lat", "#000000")
            v.text_h(start + w1 + gap, yy + 0.28, th_n, ns, "hei", "#111111")
        elif ps:
            v.text_h(lcx, yy, th, ps, "lat", "#000000", align="center")
        if d:
            v.text_h(dcx, yy, th_r, fill_placeholders(str(d), fields),
                     "lat", "#000000", align="center")


def _text_w_mm(s, h, kind):
    w0, h0, _bb, _k = LK.text_metrics(s, 10.0, kind)
    return w0 * (h / h0) if h0 > 0 else 0.0


def _vec_hourglass(v, x, y, w, h, color):
    bw = w * 0.14
    top, bot = y + h * 0.16, y + h * 0.84
    mid = (top + bot) / 2.0
    # 上横条、下横条
    v.rect(x - bw, y, x + w + bw, y + h * 0.16, fill=color)
    v.rect(x - bw, y + h * 0.84, x + w + bw, y + h, fill=color)
    # 两个三角
    v.page.draw_polyline(
        [pymupdf.Point(x * _MU, top * _MU), pymupdf.Point((x + w) * _MU, top * _MU),
         pymupdf.Point((x + w / 2) * _MU, mid * _MU)],
        color=_rgb(color), fill=_rgb(color), closePath=True)
    v.page.draw_polyline(
        [pymupdf.Point((x + w / 2) * _MU, mid * _MU), pymupdf.Point((x + w) * _MU, bot * _MU),
         pymupdf.Point(x * _MU, bot * _MU)],
        color=_rgb(color), fill=_rgb(color), closePath=True)


def _vec_vtext(v, x, y, w, h, text, color, fam):
    """竖排：镜像位图版 _vtext —— 横排 → 转 90° → 拉伸到盒子。"""
    if not text:
        return
    try:
        from PIL import Image, ImageDraw
        # 和 labelkit._vtext 同源：12mm 字号、300dpi、裁剪墨迹、转 90°、拉伸
        kk = 300 / 25.4
        tmp = Image.new("L", (1400, 300), 0)
        ImageDraw.Draw(tmp).text((14, 14), text, font=LK.font(fam, 12, 300),
                                 fill=255)
        bb = tmp.getbbox()
        if not bb:
            return
        tmp = tmp.crop(bb).rotate(90, expand=True)
        tw, th = max(1, int(round(w * kk))), max(1, int(round(h * kk)))
        tmp = tmp.resize((tw, th), Image.LANCZOS)
        rgb = _rgb(color)
        buf = Image.new("RGB", tmp.size, (255, 255, 255))
        buf.paste(Image.new("RGB", tmp.size,
                            (int(rgb[0] * 255), int(rgb[1] * 255), int(rgb[2] * 255))),
                  (0, 0), tmp)
        key = ("vtext", fam, color, text, tw, th)
        v._insert_bmp(key, _png_bytes(buf), x, y, w, h)
    except Exception:
        pass


def _vec_barcode(v, x, y, w, h, seq):
    """EAN-128 的条码直接画竖线（简化：按字符宽度均分）。"""
    import barcode as BC
    try:
        bits = BC.bits(seq, "C")
    except Exception:
        return
    n = len(bits)
    if n == 0:
        return
    bw = w / n
    for i, bit in enumerate(bits):
        if bit in ("1", 1):
            v.rect(x + i * bw, y, x + (i + 1) * bw, y + h, fill="#000000")


def _vec_qr(v, x, y, w, seq, color):
    from qrcode_min import matrix
    try:
        m = matrix(seq)
    except Exception:
        return
    n = len(m)
    cw = w / n
    for r, row in enumerate(m):
        for c, cell in enumerate(row):
            if cell:
                v.rect(x + c * cw, y + r * cw, x + (c + 1) * cw, y + (r + 1) * cw,
                       fill=color)


# ══════════════════════════════════════════════════════════════════
#  入口
# ══════════════════════════════════════════════════════════════════
def _page_draw(page, tpl, fields, fontdir):
    """在一页上把整张标签画成矢量。"""
    v = _VecPage(page, fontdir)
    w_mm, h_mm = float(tpl["画布"]["宽"]), float(tpl["画布"]["高"])

    # 底图（L1）：本身就是图，贴满整页
    base = tpl.get("底图")
    if base:
        base_path = _asset(base)
        if base_path and os.path.exists(base_path):
            v.image(base_path, 0, 0, w_mm, h_mm)

    # 字体不再取一个全局值 —— L3 / L4 各自独立（P1-3），
    # 每个元素画之前用 render.fam_of(tpl, obj) 现取。

    # 叠放顺序和 render.py 完全一致：按层，层内图形先、文字后
    import layers
    order = {k: i for i, k in enumerate(layers.ORDER)}
    draws = []
    for i, g in enumerate(tpl.get("图形") or []):
        draws.append((order.get(layers.of(g, "g"), 1), 0, i, "g", g))
    for i, grp in enumerate(tpl.get("组") or []):
        draws.append((order["L2"], 1, i, "gr", grp))
    for i, b in enumerate(tpl.get("文字") or []):
        draws.append((order.get(layers.of(b, "t"), 2), 2, i, "t", b))
    draws.sort(key=lambda d: (d[0], d[1], d[2]))
    for _o, _k, _i, kind, obj in draws:
        # 每个元素按自己那一层取字体（P1-3：L3 / L4 独立）
        _fam = render.fam_of(tpl, obj)
        if kind == "g":
            _draw_graphic(v, obj, fields, _fam)
        elif kind == "gr":
            _draw_group(v, obj, fields, _fam)
        else:
            _draw_block(v, obj, fields, _fam)


def _collect_chars(obj, acc):
    """递归收集对象里所有字符串的字符，用于字体子集化。"""
    if isinstance(obj, str):
        acc.update(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_chars(v, acc)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _collect_chars(v, acc)


def _subset_fontdir(tpl, fields_list):
    """按「模板 + 数据」实际用到的字符，生成一份子集字体到临时目录。

    完整 NotoSansSC 每个 8MB，一张 PDF 光字体就 16MB+。这里用 fontTools
    把四档字重各裁成只含实际用字的子集（每个 ~50KB），度量与完整字体
    逐字一致，只是字符集变小 —— 所以位图版和矢量版仍然对得上。

    返回 (临时目录, 清理函数)；拿不到 fontTools 时退回完整字体目录。
    """
    import paths
    full_dir = paths.FONT_DIR
    try:
        from fontTools.subset import Subsetter, Options
        from fontTools.ttLib import TTFont
    except Exception:
        return full_dir, (lambda: None)

    chars = set()
    _collect_chars(tpl, chars)
    for f in fields_list:
        _collect_chars(f, chars)
    if not chars:
        return full_dir, (lambda: None)

    import tempfile
    tmpdir = tempfile.mkdtemp(prefix="vecfonts_")
    opts = Options()
    opts.name_IDs = [1, 2, 3, 4, 6]
    opts.name_languages = ["*"]
    text = "".join(chars)
    for fname in _FONTFILE.values():
        src = os.path.join(full_dir, fname)
        dst = os.path.join(tmpdir, fname)
        if not os.path.exists(src) or os.path.exists(dst):
            continue
        try:
            f = TTFont(src)
            ss = Subsetter(opts)
            ss.populate(text=text)
            ss.subset(f)
            f.save(dst)
        except Exception:
            # 单个字体子集化失败就复制原文件兜底
            try:
                import shutil
                shutil.copy(src, dst)
            except Exception:
                pass

    def _cleanup():
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    return tmpdir, _cleanup


def save_pdf(tpl, path, fields=None, dpi=300):
    """导出矢量 PDF（单页）。

    dpi 参数保留（签名兼容 render.save_pdf），但矢量 PDF 与 dpi 无关 ——
    字和线都是矢量，任意缩放清晰。
    """
    fields = fields or tpl.get("示例数据") or {}
    global _TPL_NAME
    _TPL_NAME = (tpl or {}).get("模板名") or None
    w_mm, h_mm = float(tpl["画布"]["宽"]), float(tpl["画布"]["高"])
    fontdir, cleanup = _subset_fontdir(tpl, [fields])
    try:
        doc = pymupdf.open()
        page = doc.new_page(width=w_mm * _MU, height=h_mm * _MU)
        _page_draw(page, tpl, fields, fontdir)
        _finish(doc, path)
    finally:
        cleanup()
    return path


def save_batch_pdf(tpl, path, records, dpi=300):
    """导出矢量 PDF（多页，一条规格一页）。"""
    global _TPL_NAME
    _TPL_NAME = (tpl or {}).get("模板名") or None
    w_mm, h_mm = float(tpl["画布"]["宽"]), float(tpl["画布"]["高"])
    fontdir, cleanup = _subset_fontdir(tpl, list(records))
    try:
        doc = pymupdf.open()
        for rec in records:
            page = doc.new_page(width=w_mm * _MU, height=h_mm * _MU)
            _page_draw(page, tpl, rec, fontdir)
        _finish(doc, path)
    finally:
        cleanup()
    return len(records)


def _finish(doc, path):
    """保存 PDF。字体已在上游按需子集化，这里直接压缩保存。"""
    doc.save(path, garbage=4, deflate=True)
    doc.close()


def _font_dir():
    import paths
    return paths.FONT_DIR
