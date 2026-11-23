# -*- coding: utf-8 -*-
"""lib.py —— 图库的唯一入口：序号 ↔ 文件 ↔ 宽高比。

用户定的口径（2026-11-23 P0-3）
---------------------------
    「L2 的 image 元素必须带 lib_id（图库序号）和 lib_ratio（图库原图宽高比）。
      加图改成只能从图库选择窗口添加；粘贴图片、从文件加图不再生成 L2。
      src 不再存 base64，存相对图库路径。
      例外：右上角黑色大色块是 rect，允许，且固定在 L2 最底层。」

为什么要有 lib_id / lib_ratio
-------------------------
以前 L2 元素只存 `src`（图片路径）。问题：
  · 路径是相对模板目录的 `素材/lib_079.jpg`，等于把图库原图**拷了一份**进模板。
    图库更新了，模板里还是老的；同一张图在 20 个模板里存 20 份。
  · 宽高各自独立可编辑 → 用户随手改一个，圆形图标被拉成椭圆。
    这是「看得见的错」，而且很难查（存盘看着没问题）。

现在：
  · `lib_id`   图库序号（int）。图真正从 `paths.IMG_LIB` 里取，不拷副本。
  · `lib_ratio` 原图宽/高。改宽按它算高、改高按它算宽，**保证等比**。
  · `src` 只作缓存/回退（老模板兼容），新加的一律靠 lib_id 定位。
"""
import json
import os

import paths

# 索引文件名（图库目录里）
INDEX_NAME = "_图库索引.json"

# 序号 → 文件名。首次读取后缓存在内存，图库目录变了要 refresh()
_INDEX = None


def index_path():
    return os.path.join(paths.IMG_LIB, INDEX_NAME)


def _load_index():
    """读 `_图库索引.json`：`[{"序号": 1, "文件": "序号001_image3.png"}, ...]`

    索引不存在时**兜底扫描文件名**：`序号079_image80.jpg` 里的 079 就是序号。
    这样图库只要按命名规范放好，不加索引也能用。
    """
    global _INDEX
    if _INDEX is not None:
        return _INDEX
    out = {}
    fp = index_path()
    if os.path.isfile(fp):
        try:
            with open(fp, encoding="utf-8") as f:
                for row in json.load(f):
                    no = row.get("序号")
                    fn = row.get("文件")
                    if no is None or not fn:
                        continue
                    out[int(no)] = fn
        except Exception:
            out = {}
    if not out:
        out = _scan()
    _INDEX = out
    return out


def _scan():
    """兜底：从文件名 `序号NNN_imageXxx.ext` 里抠序号。"""
    import glob
    import re
    out = {}
    if not os.path.isdir(paths.IMG_LIB):
        return out
    for fp in glob.glob(os.path.join(paths.IMG_LIB, "序号*")):
        m = re.match(r"序号(\d+)", os.path.basename(fp))
        if m:
            out[int(m.group(1))] = os.path.basename(fp)
    return out


def refresh():
    """图库换了（用户换了目录 / 加了新图）后调一次。"""
    global _INDEX
    _INDEX = None


def all_ids():
    """所有可用序号，升序。"""
    return sorted(_load_index().keys())


def path_of(lib_id):
    """序号 → 绝对路径；找不到返回 None。"""
    if lib_id in (None, ""):
        return None
    try:
        no = int(lib_id)
    except (TypeError, ValueError):
        return None
    fn = _load_index().get(no)
    if not fn:
        return None
    fp = os.path.join(paths.IMG_LIB, fn)
    return fp if os.path.isfile(fp) else None


def file_of(lib_id):
    """序号 → 文件名（不含目录）。"""
    try:
        return _load_index().get(int(lib_id))
    except (TypeError, ValueError):
        return None


def ratio_of(lib_id):
    """序号 → 原图宽/高。

    读不到图就返回 1.0（正方形）——**不能返回 None**：调用方到处在做
    `w / ratio` 的除法，None 会炸；给个保守值至少不会把版面弄乱。
    """
    fp = path_of(lib_id)
    if not fp:
        return 1.0
    try:
        from PIL import Image
        with Image.open(fp) as im:
            w, h = im.size
        return round(float(w) / float(h), 6) if h else 1.0
    except Exception:
        return 1.0


def rel_src(lib_id):
    """序号 → 存进模板的 `src`（相对图库目录）。

    为什么要存相对路径而不是只存 lib_id：
      · 模板换台电脑打开、图库没同步时，至少还能按 src 找回老图；
      · 老模板只有 src 没有 lib_id，两者并存才能平滑迁移。
    """
    fn = file_of(lib_id)
    return fn or ""


def id_by_content(src):
    """旧素材名 → 图库序号：**按文件内容**找同一张图。

    为什么需要它：老模板的 src 是 `balloon_icon.jpg` 这种手命名，文件名里
    没有序号，`id_from_src` 反推不出来。但人家的内容就是图库里第 93 号那张图
    （只是改过名）。用 MD5 一比就认出来了 —— 实测 9 个旧素材里救回 8 个。

    找不到返回 None（图库里确实没有这张图）。
    """
    import hashlib
    import os
    cands = [src]
    try:
        import store
        p = store.resolve_ref("", src) or ""
        if p:
            cands.append(p)
    except Exception:
        pass
    for c in cands:
        if not c or not os.path.isfile(c):
            continue
        try:
            h = hashlib.md5(open(c, "rb").read()).hexdigest()
        except Exception:
            continue
        for no, fn in _load_index().items():
            fp = os.path.join(paths.IMG_LIB, fn)
            if not os.path.isfile(fp):
                continue
            try:
                if hashlib.md5(open(fp, "rb").read()).hexdigest() == h:
                    return no
            except Exception:
                continue
    return None


def describe(lib_id):
    """给界面用的一行文字。"""
    fn = file_of(lib_id)
    return ("#%s  %s" % (lib_id, fn)) if fn else "（已失效：图库第 %s 号找不到）" % lib_id


def id_from_src(src):
    """从老模板的 `src` 反推序号（迁移用）。

    支持的写法：
      · `素材/lib_079.jpg`        → 79
      · `素材/序号111_image112.jpg` → 111
      · `序号079_image80.jpg`     → 79
    反推不出来返回 None。
    """
    import re
    s = str(src or "")
    base = os.path.basename(s)
    m = re.search(r"lib_(\d+)", base)
    if m:
        return int(m.group(1))
    m = re.search(r"序号(\d+)", base)
    if m:
        return int(m.group(1))
    # 按文件名去索引里反查
    for no, fn in _load_index().items():
        if fn == base:
            return no
    return None
