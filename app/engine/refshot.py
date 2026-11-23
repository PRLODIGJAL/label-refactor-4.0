# -*- coding: utf-8 -*-
"""refshot.py —— 从研发给的图纸 PDF 里取「叠影」基准图。

叠影是什么
----------
界面上那张半透明垫底的图（顶栏「叠影」），用来核对「我画的这块和原稿在不在
同一个位置」。它只参与屏幕显示，**不进导出**。

为什么不能一直吃 GRA
--------------------
以前叠影写死在 `data/参考/GRA-0001.png` —— 那是照图纸二次画出来的 GRA 成品
效果图。实际业务里研发只给 DWG 图纸，GRA 是后面才有的产物，甚至是拿这套工具
自己渲出来的。也就是说：**新来的模板根本没有 GRA**。叠影必须能从图纸本身取。

怎么取才准
----------
图纸第 1 页通常把设计稿整张**以图片置入**（`dev/make_ref_1087.py` 量过：
7681×8331，等效 1087dpi）。按它的放置框渲染出来，就是干净的一张成品图 ——
不含 NOTE 引线、不含图框、不含标题栏。

为什么不 `extract_image` 直接把内嵌 JPEG 抠出来：置入时带了变换矩阵，直取
出来是转 180° 的（`dev/make_ref_1087.py` 头部记着这个坑）。按放置框渲染，
取向才是正的。

取法优先级
----------
1. 页内有「够大的」置入图 → 按放置框渲染（干净，推荐）
2. 没有（全矢量画的图纸）→ 整页渲染，标注和图框会一起进来，属已知代价，
   靠「自动对齐」+ 叠影不透明度凑合看

接口
----
    page_count(pdf)      有多少页
    page_size(pdf, i)    第 i 页的毫米尺寸
    render(pdf, i, dpi)  第 i 页整页渲染 → PIL.Image
    take(path, i)        → (PIL.Image, 说明)；PDF 取页内成品图，图片直接读
    save(im, tag)        存进 data/参考/，返回**相对 data/** 的路径

`save()` 返回相对路径是刻意的：模板 json 里存相对路径，整个文件夹搬到别的
电脑才不会断链（和「数据源」一个规矩）。
"""
import io
import os
import re

from PIL import Image

import paths

# 叠影只在屏幕上用，再大只是白占内存和渲染时间。
# 2600px ≈ 181mm 画布 365dpi，缩到窗口那点大小时完全看不出差别。
MAX_PX = 2600

# 判定「这是成品图不是图标」的两道门槛：
#   · 占页面面积 ≥ 10%
#   · 原始像素短边 ≥ 700
# 设计稿里常和成品图一起置入小 logo、印章、示意小图，不筛掉会被当成成品图。
MIN_SPAN = 0.10
MIN_SIDE = 700


def _doc(path):
    """打开 PDF。调用方负责 close()。"""
    import pymupdf
    return pymupdf.open(path)


def _is_pdf(path: str) -> bool:
    return os.path.splitext(str(path))[1].lower() == ".pdf"


def page_count(path: str) -> int:
    """PDF 页数；打不开就当 1 页（让调用方走「不选页」的路，不炸界面）。"""
    if not _is_pdf(path):
        return 1
    d = _doc(path)
    try:
        return int(d.page_count)
    finally:
        d.close()


def page_size(path: str, i: int = 0):
    """第 i 页的毫米尺寸 → (宽, 高)。"""
    d = _doc(path)
    try:
        r = d[i].rect
        return (round(r.width * 25.4 / 72.0, 2), round(r.height * 25.4 / 72.0, 2))
    finally:
        d.close()


def render(path: str, i: int = 0, dpi: int = 300) -> Image.Image:
    """整页渲染成 PIL 图（不缩，保留调用方要的 dpi）。"""
    d = _doc(path)
    try:
        pix = d[i].get_pixmap(dpi=dpi)
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    finally:
        d.close()


def _art_rect(page):
    """页里那张「成品图」→ (放置框 (x0,y0,x1,y1), xref)；没有就 (None, None)。

    先用 `get_images()` 按**像素大小**排一遍（免费），再对最像的那张问放置框。
    不能`get_image_info(xrefs=True)` 一次扫完：它要为每张图算 MD5 摘要反查 xref，
    这张图纸上就是 1.0s（64MP 的图要 0.36s/次）。按像素从大到小试，一般第一次
    就命中，总耗时 0.4s 左右。
    """
    parea = abs(page.rect.width * page.rect.height) or 1.0
    cands = [it for it in page.get_images(full=True)
             if min(int(it[2] or 0), int(it[3] or 0)) >= MIN_SIDE]
    # 像素最多的排前面：图纸里成品图一定是最大那张，图标印章都在它后面
    cands.sort(key=lambda it: -int(it[2] or 0) * int(it[3] or 0))
    best = None
    for info in cands[:3]:
        xref = info[0]
        try:
            rects = page.get_image_rects(xref)
        except Exception:
            continue
        for r in rects:
            area = abs(r.width * r.height)
            if area / parea < MIN_SPAN:
                continue
            if best is None or area > best[0]:
                best = (area, (r.x0, r.y0, r.x1, r.y1), xref)
        if best is not None:
            break
    if not best:
        return None, None
    return best[1], best[2]


def _rendered(page, rect, dpi=90):
    """按放置框渲染一小张 —— 当「朝向的标准答案」用，所以只要低 dpi。"""
    pix = page.get_pixmap(clip=rect, dpi=dpi)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def _variants(im):
    """原图 + 3 个旋转，每个再翻一次水平镜像 = 8 个候选朝向。"""
    for angle in (0, 90, 180, 270):
        r = im if angle == 0 else im.rotate(angle, expand=True)
        yield r
        yield r.transpose(Image.FLIP_LEFT_RIGHT)


def _upright(im, truth):
    """从 8 个朝向里挑和真实渲染最像的那个。

    为什么不直接推 PDF 的放置矩阵：矩阵里 v 轴朝上还是朝下、置入时有没有带
    flip，不同导出器写法不一致。推错一次整张叠影就上下颠倒，而且**颠倒不显眼**
    —— 标签里文字多，扫一眼看不出来，等发现时已经按它调了半天版面。
    这里拿「渲染出来的样子」当标准答案，8 个候选各比一遍灰度差，错不了。
    代价：8 次 180px 的小图比较，几十毫秒。
    """
    tw = 180
    th = max(1, int(round(tw * truth.height / float(truth.width))))
    t = truth.convert("L").resize((tw, th), Image.BOX)
    tp = t.load()
    best, best_d = None, None
    for v in _variants(im):
        sp = v.convert("L").resize((tw, th), Image.BOX).load()
        d = 0
        for y in range(0, th, 2):
            for x in range(0, tw, 2):
                d += abs(tp[x, y] - sp[x, y])
        if best_d is None or d < best_d:
            best, best_d = v, d
    return best


def _from_image(doc, page, rect, xref, dpi):
    """只取那张内嵌图（不带页面上的 NOTE 引线）。取不到返回 None。

    为什么不直接渲染 clip：图纸上的 NOTE 引线是**页面内容**（不是注释），
    渲染出来会横穿整张标签。它们在我们的模板里没有对应元素，留着会一直算进
    差异分，还会把「自动对齐」带偏。内嵌图本身是干净的。
    """
    try:
        raw = doc.extract_image(xref)
    except Exception:
        return None
    if not raw or not raw.get("image"):
        return None
    try:
        im = Image.open(io.BytesIO(raw["image"]))
        # JPEG 解码时先让解码器按 1/2、1/4、1/8 出小图。
        # 原图 64MP，完整解码再 LANCZOS 要 0.7s；draft 之后 0.15s。
        target_w = max(1, int(round((rect[2] - rect[0]) / 72.0 * dpi)))
        try:
            im.draft("RGB", (target_w, target_w))
        except Exception:
            pass
        im.load()
    except Exception:
        return None
    if im.mode in ("P", "LA", "RGBA", "L"):
        # 带 alpha 的图：垫白底，否则透明处转 RGB 会变黑
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, im)
    im = im.convert("RGB")
    # 先缩到目标像素再判朝向：原图 7681×8331（64MP），在它上面转 4 次太慢。
    # 90° 整数倍旋转和缩放可交换，先缩不影响判断。
    if im.width > target_w:
        h = max(1, int(round(im.height * target_w / float(im.width))))
        im = im.resize((target_w, h), Image.LANCZOS)
    return _upright(im, _rendered(page, rect))


def _shrink(im: Image.Image) -> Image.Image:
    """长边压到 MAX_PX 以内（只缩不放）。"""
    if im.width <= MAX_PX:
        return im
    h = max(1, int(round(im.height * MAX_PX / float(im.width))))
    return im.resize((MAX_PX, h), Image.LANCZOS)


def take(path: str, i: int = 0, dpi: int = 300):
    """取一张叠影基准图 → (PIL.Image, 说明文字)。

    Args:
        path: 图纸 PDF 或图片文件
        i: 取第几页（0 开始；图片忽略）
        dpi: PDF 渲染精度。300 够叠影用；再高点只是变慢。

    Returns:
        (图, 说明)。说明是给状态栏的一句话，短、带尺寸。

    Raises:
        FileNotFoundError: 文件不存在
        Exception: pymupdf 打不开 / 页号越界，交给界面弹「换不了叠影」
    """
    if not os.path.exists(path):
        raise FileNotFoundError(path)

    if not _is_pdf(path):
        im = Image.open(path)
        im.load()
        im = _shrink(im.convert("RGB"))
        return im, "图片 %d×%d" % im.size

    d = _doc(path)
    try:
        page = d[i]
        rect, xref = _art_rect(page)
        im = None
        # 取法口径（2026-11-20 用户拍板）：「DWG 第一页截图」= 整页合成渲染
        # 后按标签图放置框裁剪 —— 位图与矢量（绿箭头/NOTE 引线）一起出来，
        # 裁剪框就是标签图真实边界，贴回画布 (0,0) 四边自然对齐。
        # 旧口径只抽内嵌位图原图：没有绿箭头、且自带白边与画布不齐。
        if rect is not None:
            import pymupdf
            pix = page.get_pixmap(clip=pymupdf.Rect(rect), dpi=dpi)
            im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            note = "第 %d 页 · 整页截图（含引线/箭头）" % (i + 1)
        if im is None:
            # 页内连放置图都没有：整页渲染兜底（标注和图框一起进来）。
            pix = page.get_pixmap(dpi=dpi)
            im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            note = "第 %d 页 · 整页（无页内成品图）" % (i + 1)
    finally:
        d.close()

    im = _shrink(im)
    return im, note + " %d×%d" % im.size


def trim_black_border(im, pad_ratio=0.0, thr=235):
    """裁掉截图四周的黑边 / 黑字残留，让内容正好贴边（2026-11-20 用户规则 1.2）。

    为什么必须裁
    -----------
    从 DWG 第一页裁出来的叠影，四周常带一圈黑边：图纸框线、NOTE 引线的
    残留、压在图外面的标注。这些黑边会让"叠影居中"变成假居中 ——
    元素坐标整体偏一截，后面所有对位都是歪的。

    做法
    ----
    找**最外圈的墨迹包围盒**（灰度 < thr 的像素），裁到它。
    可选再留一点白边（pad_ratio），给元素拖出纸面留余地。

    Args:
        im: PIL 图（RGB）。
        pad_ratio: 裁完后四周再留的空白比例（相对短边）。默认 0 = 正好贴边。
        thr: 判断"是墨"的灰度阈值。默认 235（比 200 宽松，能吃掉浅灰框线）。

    Returns:
        PIL.Image: 裁好的图（裁不出来就原样返回）。
    """
    g = im.convert("L")
    w, h = g.size
    px = g.load()
    x0, y0, x1, y1 = w, h, -1, -1
    # 逐像素扫太慢（300dpi 有 2500×2500）；按步长抽样定大致范围，再精修
    step = max(1, min(w, h) // 600)
    for y in range(0, h, step):
        for x in range(0, w, step):
            if px[x, y] < thr:
                if x < x0: x0 = x
                if x > x1: x1 = x
                if y < y0: y0 = y
                if y > y1: y1 = y
    if x1 < 0:
        return im                        # 全白，没内容
    # 精修：在粗框外扩 step 的范围内逐像素找真边界
    ex0, ey0 = max(0, x0 - step), max(0, y0 - step)
    ex1, ey1 = min(w, x1 + step + 1), min(h, y1 + step + 1)
    fx0, fy0, fx1, fy1 = ex1, ey1, ex0, ey0
    for y in range(ey0, ey1):
        for x in range(ex0, ex1):
            if px[x, y] < thr:
                if x < fx0: fx0 = x
                if x > fx1: fx1 = x
                if y < fy0: fy0 = y
                if y > fy1: fy1 = y
    if fx1 < fx0:
        return im
    out = im.crop((fx0, fy0, fx1 + 1, fy1 + 1))
    if pad_ratio > 0:
        pw = int(round(min(out.size) * pad_ratio))
        if pw > 0:
            canvas = Image.new("RGB", (out.width + 2 * pw, out.height + 2 * pw),
                               "white")
            canvas.paste(out, (pw, pw))
            out = canvas
    return out


def center_on_canvas(im, margin_ratio=0.0):
    """把内容居中放进一张更大的白画布，**四边留白相等**（规则 1.2）。

    这是"叠影必须在白布上四边等宽"的直接实现。留白宽度 =
    内容短边 × margin_ratio（默认 0 = 白布正好包住内容）。

    Returns:
        (PIL.Image, (左, 上, 右, 下) 留白像素) —— 留白四值相等。
    """
    if margin_ratio <= 0:
        return im, (0, 0, 0, 0)
    m = int(round(min(im.size) * margin_ratio))
    if m <= 0:
        return im, (0, 0, 0, 0)
    canvas = Image.new("RGB", (im.width + 2 * m, im.height + 2 * m), "white")
    canvas.paste(im, (m, m))
    return canvas, (m, m, m, m)


def _safe(tag: str) -> str:
    s = re.sub(r'[\\/:*?"<>|\s]+', "-", str(tag or "")).strip("-")
    return s or "叠影"


def save(im: Image.Image, tag: str = "叠影", tpl_name: str = None) -> str:
    """存进模板自己的 参考/ 文件夹，返回相对路径（模板里就存这个）。

    同名覆盖是刻意的：同一份图纸同一页取两次，不该堆出
    「叠影_xxx(2).jpg」。换了来源才换文件名。
    tpl_name 不传（老调用）退回 data/参考/。
    """
    if tpl_name:
        import store
        d = os.path.join(store.tpl_dir(tpl_name), "参考")
    else:
        d = os.path.join(paths.DATA, "参考")
    os.makedirs(d, exist_ok=True)
    name = "叠影_%s.jpg" % _safe(tag)
    fp = os.path.join(d, name)
    # q92 + 不做色度抽样：叠影要拿来算差异分，色块边界糊了会凭空多出差异。
    im.convert("RGB").save(fp, "JPEG", quality=92, subsampling=0, optimize=True)
    return "参考/" + name
