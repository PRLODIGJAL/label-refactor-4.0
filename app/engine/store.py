# -*- coding: utf-8 -*-
"""store.py —— 模板存储：一模板一文件夹，自包含。

目录结构（模板/ 独立于 data/，在软件根目录，直接拖拽就能复制）：
    软件根目录/
    ├── 模板/
    │   └── <模板名>/
    │       ├── 模板.json      # 模板定义（固定就叫这个名）
    │       ├── 素材/          # 这个模板用到的图
    │       ├── 参考/          # 叠影参考图
    │       ├── 数据源/        # Excel 数据表
    │       └── 版本存档/      # 旧版本
    └── data/
        ├── 图库/              # 共享图标库（加图默认从这里挑）
        ├── 字体/              # 渲染字库
        ├── 预览/              # 预览图缓存
        └── 上次选择.json

为什么一模板一文件夹：
  · 每个模板就是一份完整的东西，不用猜「这张图属于哪个模板」
  · 导出 = 把文件夹拷出去；导入 = 把文件夹收进来，两边格式一样
  · 删模板 = 删文件夹，不留孤儿素材
"""
import json
import os
import re
import shutil
import time

import paths

SCHEMA = "labeldesigner/2"

# 资源子目录名（模板文件夹内 / 导出包内的固定命名）
CATS = ("素材", "参考", "数据源")


def _safe(name: str) -> str:
    """模板名 → 安全文件夹名。"""
    s = re.sub(r'[\\/:*?"<>|]', "-", str(name)).strip()
    return s or "未命名模板"


def tpl_dir(name: str) -> str:
    """模板文件夹：根目录 模板/<名>/。"""
    return os.path.join(paths.TPL_DIR, _safe(name))


def tpl_path(name: str) -> str:
    """模板 JSON 固定叫 模板.json。"""
    return os.path.join(tpl_dir(name), "模板.json")


def cat_dir(name: str, cat: str) -> str:
    """模板内的资源子目录（素材/参考/数据源/版本存档）。"""
    return os.path.join(tpl_dir(name), cat)


def blank_template(name: str) -> dict:
    """新建模板 = 一张白纸：没有底图、没有图形、没有文字块。"""
    return {
        "格式": SCHEMA,
        "模板名": name,
        "画布": {"宽": 200.0, "高": 300.0},
        "底图": None,                  # dataURL 或 None
        "图形": [],                    # L2：{name, src|type, x, y, w, h, fill}
        "文字": [],                    # L3/L4：{k, name, layer, lines:[{x,y,sz,b,t}], bars:[]}
        "字体": "hei",
        "版本": "Rev01",          # 同一套版式换一版就 +1，旧版自动存档（前缀统一 Rev）
        "改于": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def norm_rev(rev) -> str:
    """版本号前缀统一成 Rev：rev01 / v3 / 05 → Rev01 / Rev03 / Rev05。

    双击改版本号、显示版本号都过这里 —— 写什么进来都是 Rev 开头。
    老模板的版本可能是整数（1）—— 一律 str()，不能直接 .strip()。
    """
    s = str(rev if rev is not None else "").strip()
    if not s:
        return "Rev01"
    m = re.match(r"^(?:rev\.?|v)?\s*(\d+)$", s, re.I)
    if m:
        num = m.group(1)
        return "Rev" + (num.zfill(2) if len(num) < 2 else num)
    # 带自定义后缀（Rev19-final）：只换前缀
    s = re.sub(r"^(rev\.?|v)", "Rev", s, flags=re.I)
    return s if s[:3].lower() == "rev" else "Rev" + s


def next_rev(rev: str) -> str:
    """Rev01 → Rev02，rev19 → Rev20，无数字就接 Rev01（前缀统一 Rev）。"""
    if not str(rev if rev is not None else "").strip():
        return "Rev01"
    s = norm_rev(rev)
    m = re.match(r"^(.*?)(\d+)$", s)
    if m:
        num = m.group(2)
        return m.group(1) + str(int(num) + 1).zfill(len(num))
    return "Rev01"


def archive_dir(name: str) -> str:
    """版本存档就放在模板自己文件夹里。"""
    return os.path.join(tpl_dir(name), "版本存档")


def bump_version(tpl: dict) -> str:
    """升版本：先把当前内容存档，再把版本号 +1。返回新版本号。"""
    old = tpl.get("版本") or "rev01"
    d = archive_dir(tpl["模板名"])
    os.makedirs(d, exist_ok=True)
    fp = os.path.join(d, "%s_%s.json" % (_safe(norm_rev(old)),
                                        time.strftime("%Y%m%d_%H%M%S")))
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(tpl, f, ensure_ascii=False, indent=1)
    new = next_rev(old)
    tpl["版本"] = new
    return new


def list_versions(name: str) -> list:
    """这个模板存过哪些旧版本（按文件名升序，调用方要什么顺序自己排）。"""
    d = archive_dir(name)
    if not os.path.isdir(d):
        return []
    return sorted(fn for fn in os.listdir(d) if fn.endswith(".json"))


def rev_num(rev) -> int:
    """版本号里的数字部分。「1」/「Rev02」/「rev3-final」→ 1 / 2 / 3。

    拿不到数字就当 0 —— 版本栏排序时排在最后，不会把整条链顶乱。
    """
    m = re.search(r"(\d+)", norm_rev(rev))
    return int(m.group(1)) if m else 0


def rev_sort_key(fn: str):
    """存档文件名 → 排序键：先按版本号、再按时间戳（都取「越大越新」）。

    版本栏要「从左到右由高到低」，直接拿文件名排会变成字符串序
    （「1_…」「Rev02_…」「Rev03_…」凑巧还行，换到 Rev10 就错了）。
    """
    base = os.path.splitext(os.path.basename(str(fn)))[0]
    ver = base.split("_")[0]
    ts = base.split("_", 1)[1] if "_" in base else ""
    return (rev_num(ver), ts)


def list_templates() -> list:
    """返回 [(模板名, 模板.json 路径, 修改时间, 版本)]，按名字排序。"""
    migrate_layout()
    out = []
    if not os.path.isdir(paths.TPL_DIR):
        return out
    for fn in sorted(os.listdir(paths.TPL_DIR)):
        d = os.path.join(paths.TPL_DIR, fn)
        if not os.path.isdir(d) or fn.startswith((".", "_")):
            continue
        fp = os.path.join(d, "模板.json")
        if not os.path.isfile(fp):
            continue
        try:
            with open(fp, encoding="utf-8") as f:
                t = json.load(f)
            out.append((t.get("模板名") or fn, fp,
                        t.get("改于") or "", t.get("版本") or "rev01"))
        except Exception:
            out.append((fn + "（损坏）", fp, "", ""))
    out.sort(key=lambda t: t[0])
    return out


def _hex_rgb(c):
    """'#RRGGBB' → (r,g,b)；看不懂返回 None。"""
    s = str(c or "").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        return None
    try:
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except ValueError:
        return None


def purge_color(tpl: dict) -> dict:
    """把模板里所有「彩色」清掉 —— 黑稿模板不该存任何颜色。

    业务口径（2026-11-20 用户明确，原文：「如果底图是有彩色的部分，比方说
    那个 产品线A、颜色条框、某医疗器械公司这些地方，就直接不添加相关的图案」）：
    现场只有黑墨，**彩色部分建模板时就不放图案**，而不是"放上去再转黑白"。
    产品线A/产品线B 彩色字标、彩色横幅底衬、彩色 logo 这类，L2 里根本不该
    有对应元素。本函数是最后一道兜底（防历史存档/别人手工加进来）。

    为什么还要在数据层做，而不是只在渲染时兜底：渲染兜底只能保证「这一版
    画出来是黑的」，可颜色还留在 json 里，下一次有人拿它去彩印、或者改
    渲染口径，彩色就又冒出来了。现场只有黑墨，颜色是纯粹的负担。

    规则（黑 / 白 / 灰不动）：
      · 图形 fill 是彩色 → 删掉 fill（这一块本来就不上墨，留着只会误导）
      · 图形 stroke / 文字 color 是彩色 → 改 #111111
      · 顺带清掉模板级的「底色」之类字段
    """
    def is_col(c):
        rgb = _hex_rgb(c)
        if rgb is None:
            return False
        if max(rgb) - min(rgb) <= 28:
            return False                     # 灰阶
        return max(rgb) > 90                 # 深到发黑的也不算彩色

    for layer in ("图形", "文字"):
        for e in tpl.get(layer) or []:
            for k in ("stroke", "color", "c", "box_color"):
                if is_col(e.get(k)):
                    e[k] = "#111111"
            if is_col(e.get("fill")):
                # 置空而不是删键：留着键 = 「这块明确不填充」，
                # 删了键会被当成「只描边」画出一圈黑框。
                e["fill"] = ""
            for ln in e.get("lines") or []:
                if is_col(ln.get("c")):
                    ln["c"] = "#111111"
            for bar in e.get("bars") or []:
                for k in ("color", "c"):
                    if is_col(bar.get(k)):
                        bar[k] = "#111111"
    for grp in tpl.get("组") or []:
        for e in grp.get("元素") or []:
            for k in ("stroke", "color", "c"):
                if is_col(e.get(k)):
                    e[k] = "#111111"
            if is_col(e.get("fill")):
                # 置空而不是删键：留着键 = 「这块明确不填充」，
                # 删了键会被当成「只描边」画出一圈黑框。
                e["fill"] = ""
    return tpl


def normalize(tpl: dict) -> dict:
    """补齐界面上要用的字段（就地改，返回同一个 dict）。

    3.0 的声明式文字块只写 t/x/y/w/h，没有 name/k；但界面列表面板、
    复制、撤销都按 name/k 找块。在这里统一补上，省得每个调用点都判空。
    """
    for g in tpl.get("图形") or []:
        # 顺应性卡这种复合图形用 x0/x1/ys 描述自己的范围，没有 x/y；
        # 界面按 x/y/w/h 画包围盒，这里换算一份。
        if g.get("x") is None and "x0" in g:
            g["x"] = float(g["x0"])
            ys = g.get("ys") or [0]
            g["y"] = float(ys[0])
            g.setdefault("w", float(g.get("x1", g["x0"])) - float(g["x0"]))
            g.setdefault("h", float(ys[-1]) - float(ys[0]))
        if "w" not in g:
            g["w"] = abs(float(g.get("x2", g.get("x", 0))) - float(g.get("x", 0))) or 0.3
        if "h" not in g:
            g["h"] = abs(float(g.get("y2", g.get("y", 0))) - float(g.get("y", 0))) or 0.3
    # ★ 画布边距（P1-6）：默认取软件设置的全局值，没设过就是 4
    # 只补字段，**不改画布尺寸** —— 画布宽高由 frame.canvas_rect 现算，
    # 两者不会打架。老模板没有这个字段，补上 4 即可（canvas_rect 会
    # 按"画布 - 2m"反推框内，所以升级后尺寸不变）。
    if "画布边距_mm" not in tpl:
        try:
            import frame as _FR
            tpl["画布边距_mm"] = _FR.global_default_margin()
        except Exception:
            tpl["画布边距_mm"] = 4.0

    # ★ 字体拆成 L3 / L4 两份（P1-3，2026-11-23）
    # 老模板只有一个 tpl["字体"]，L3 和 L4 共用。现在要各管各的。
    # 迁移规则：**旧值两份都拿**，不覆盖已有的新字段 ——
    # 这样既能读老模板，又不会把已经分开设好的值冲掉。
    # 默认值统一取 labelkit.DEFAULT_FONT（全项目只此一处）。
    import labelkit as _LK
    _old = tpl.get(_LK.LEGACY_FONT_KEY)
    for _k in _LK.FONT_KEYS.values():
        if not tpl.get(_k):
            tpl[_k] = _old or _LK.DEFAULT_FONT
    # 旧字段留着（老版本读得回来），但**不再被渲染使用** ——
    # 用它会让"改了 L4 字体，L3 跟着变"，正是这次要分开的东西。
    tpl.setdefault(_LK.LEGACY_FONT_KEY, tpl[_LK.FONT_KEYS["L3"]])

    # ★ xs → tracking 迁移（P1-5，2026-11-23）
    # xs 是横向拉伸（把字压扁），tracking 是字距（字形不变）。
    # 用户口径：UI 不再给 L3/L4 暴露 xs，旧模板的 xs 迁成 tracking。
    #
    # ★ 换算必须**用渲染器自己量**，不能拿 labelkit.text_metrics 估：
    #   试过一版用 text_metrics，换算结果和 xs 路的实际宽度差 1.3~1.7mm
    #   （两个"自然宽"不是同一个量：一个是前进宽、一个是墨迹宽）。
    #   正确做法是让 render 量两次 —— 带 xs 的宽、不带 xs 的宽，差值就是
    #   "多出来的宽度"，摊到字间隙里就得 tracking。这样两条路**逐字节等价**。
    _dropped = []
    _todo = [b for b in (tpl.get("文字") or []) if "xs" in b]
    if _todo:
        try:
            import render as _Rd       # 延迟 import，避免模块级循环
        except Exception:
            _Rd = None
        for _b in _todo:
            _xs = float(_b.get("xs") or 1.0)
            _s = str(_b.get("t") or "")
            if _Rd is None or abs(_xs - 1.0) < 1e-6 or len(_s) < 2:
                if _Rd is not None and abs(_xs - 1.0) >= 1e-6 and len(_s) < 2:
                    _dropped.append({"name": _b.get("name") or "",
                                     "xs": _xs,
                                     "原因": "单字/空串，没有字间隙可摊"})
                _b.pop("xs", None)
                continue
            try:
                import labelkit as _LKm2
                _fam = (tpl.get("字体_L3") or tpl.get("字体")
                        or _LKm2.DEFAULT_FONT)
                _no = dict(_b)
                _no.pop("xs", None)
                _w_xs = _Rd.text_bbox(dict(_b, xs=_xs), {}, _fam,
                                      with_box=False)[2]
                _w_0 = _Rd.text_bbox(_no, {}, _fam, with_box=False)[2]
                _trk = (_w_xs - _w_0) / max(1, len(_s) - 1)
                _b["tracking"] = round(_trk, 4)
                _b["_xs_from"] = _xs        # 留痕迹，便于回溯
                _h_lim = 0.10 * float(_b.get("h") or 2.0)
                if abs(_trk) > _h_lim:
                    _dropped.append({"name": _b.get("name") or "", "xs": _xs,
                                     "tracking": round(_trk, 4),
                                     "限": round(_h_lim, 4),
                                     "原因": "换算出的字距超字高 ±10%"})
            except Exception as _e:                     # noqa: BLE001
                _dropped.append({"name": _b.get("name") or "", "xs": _xs,
                                 "原因": "换算失败(%s)，xs 已丢弃" % _e})
            _b.pop("xs", None)
    if _dropped:
        tpl["_xs迁移超限"] = _dropped

    purge_color(tpl)
    for i, b in enumerate(tpl.get("文字") or []):
        if not b.get("k"):
            b["k"] = "t%02d" % (i + 1)
        if not b.get("name"):
            b["name"] = (b.get("t") or "文字 %d" % (i + 1))[:24]
    return tpl


def load(name: str) -> dict:
    migrate_layout()
    with open(tpl_path(name), encoding="utf-8") as f:
        d = json.load(f)
    d.setdefault("画布", {"宽": 200.0, "高": 300.0})
    d.setdefault("图形", [])
    d.setdefault("文字", [])
    # 默认字体统一取 labelkit.DEFAULT_FONT（P1-3：全项目只留一处）
    import labelkit as _LK
    d.setdefault(_LK.LEGACY_FONT_KEY, _LK.DEFAULT_FONT)
    d.setdefault("版本", "rev01")
    return normalize(d)


def save(tpl: dict) -> str:
    # ★ 存量骤降告警（2026-11-23）
    # 教训：P0-2 期间有条 dev 脚本对真实模板跑了一遍「加元素 → undo」，
    # 结果 4 个条码被静默写没了 —— 而且没报错，只是 L5 对齐率从 81.5%
    # 掉到 70.2%，是事后比对才发现的。这类"少了一半内容却不报错"的存盘
    # 是最难查的，所以在写盘这一层兜一下。
    _warn_shrink(tpl)
    migrate_layout()
    purge_color(tpl)          # 黑稿模板：存盘前把残留的颜色一律清掉
    tpl["格式"] = SCHEMA
    tpl["改于"] = time.strftime("%Y-%m-%d %H:%M:%S")
    d = tpl_dir(tpl["模板名"])
    os.makedirs(d, exist_ok=True)
    fp = tpl_path(tpl["模板名"])
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(tpl, f, ensure_ascii=False, indent=1)
    return fp


_LAST_COUNT = {}


def _warn_shrink(tpl: dict):
    """同一模板两次存盘之间元素数骤降 → 打一行警告。

    只警告不阻止：真有删除操作（用户主动删元素）也是合法的，
    这里要的是"有人注意到"，而不是拦住正常使用。
    判定：比上次少 2 个以上，或少了超过 20%。
    """
    try:
        name = tpl.get("模板名") or ""
        cur = len(tpl.get("图形") or []) + len(tpl.get("文字") or [])
        prev = _LAST_COUNT.get(name)
        _LAST_COUNT[name] = cur
        if prev and prev > 0 and cur < prev:
            if prev - cur >= 2 or (prev - cur) / float(prev) > 0.2:
                import sys as _sys
                print("[store] ⚠️ 「%s」元素数从 %d 掉到 %d —— 如果是误删，"
                      "从版本存档恢复" % (name, prev, cur), file=_sys.stderr)
    except Exception:
        pass


def delete(name: str) -> bool:
    """删模板 = 删整个文件夹（素材/参考/数据源/版本存档一起走）。"""
    d = tpl_dir(name)
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
        return True
    return False


def copy_template(src: str, dst: str) -> str:
    """整份复制模板（素材/参考/数据源/版本存档一起走），换个名字。

    「只拷 模板.json」是错的：引用文件不在新文件夹里，数据表/图全接不上。
    """
    sdir, ddir = tpl_dir(src), tpl_dir(dst)
    if not os.path.isdir(sdir):
        raise FileNotFoundError("模板不在：%s" % src)
    if os.path.isdir(ddir):
        shutil.rmtree(ddir, ignore_errors=True)
    shutil.copytree(sdir, ddir)
    tpl = load(dst)
    tpl["模板名"] = dst
    save(tpl)
    return dst


def rename(old: str, new: str) -> str:
    new = new.strip()
    if not new or new == old:
        return old
    tpl = load(old)
    tpl["模板名"] = new
    fp_new = save(tpl)          # 先在新文件夹落好
    if _safe(old) != _safe(new):
        delete(old)             # 再删旧文件夹
    return fp_new


def export(src_name: str, dst_path: str) -> None:
    """导出成单个 json（不带图片；带图发同事请用 export_folder）。"""
    tpl = load(src_name)
    with open(dst_path, "w", encoding="utf-8") as f:
        json.dump(tpl, f, ensure_ascii=False, indent=1)


# ── 引用解析 ──────────────────────────────────────────────────────
# 模板里图可以存三种形态：dataURL（自包含）、文件名（bomed_top.png）、
# 或带类别的相对路径（参考/叠影_x.jpg、数据源/x.xlsx）。
# 找文件时按「模板自己文件夹 → 图库 → 旧共享池」的顺序，老模板不用改也能开。

def resolve_ref(name: str, ref: str):
    """引用 → 绝对路径（找不到返回 None）。dataURL 原样返回。"""
    if not ref:
        return None
    ref = str(ref)
    if ref.startswith("data:"):
        return ref
    if os.path.isabs(ref):
        return ref if os.path.isfile(ref) else None
    r = ref.replace("\\", "/")
    base = os.path.basename(r)
    tdir = tpl_dir(name) if name else ""
    cands = []
    if tdir:
        cands.append(os.path.join(tdir, r))
        for c in CATS:
            cands.append(os.path.join(tdir, c, base))
    # 旧共享池（迁移前的老结构）+ 图库
    cands.append(os.path.join(paths.DATA, r))
    cands.append(os.path.join(paths.IMG_LIB, base))
    for c in CATS:
        cands.append(os.path.join(paths.DATA, c, base))
    cands.append(os.path.join(paths.DATA, "_归档_旧结构", r))
    cands.append(os.path.join(paths.DATA, "_归档_旧结构", "素材", base))
    for p in cands:
        if p and os.path.isfile(p):
            return p
    return None


def put_asset(name: str, src_path: str, cat: str = "素材") -> str:
    """把一个外部文件收进模板文件夹，返回存进 JSON 的引用字符串。

    素材存裸文件名（和老模板一致）；参考/数据源带类别前缀。
    """
    base = os.path.basename(src_path)
    dst_dir = cat_dir(name, cat)
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, base)
    if os.path.abspath(src_path) != os.path.abspath(dst):
        if os.path.isfile(dst):
            stem, ext = os.path.splitext(base)
            n = 2
            while os.path.isfile(dst):
                dst = os.path.join(dst_dir, "%s_%d%s" % (stem, n, ext))
                n += 1
            base = os.path.basename(dst)
        shutil.copy2(src_path, dst)
    return base if cat == "素材" else "%s/%s" % (cat, base)


# ── 自包含文件夹导出 / 导入 ──────────────────────────────────────
# 存储格式 = 导出包格式 = 一模板一文件夹。导出把文件夹拷出去，
# 导入把文件夹整个收进来（移动到 模板/<名>/），两边对称。


def _collect_refs(tpl: dict) -> dict:
    """扫描模板里所有引用的外部文件，按类别返回 {类别: [引用...]}。

    迁移旧结构时用。dataURL 自动跳过。
    """
    refs: dict = {"素材": [], "参考": [], "数据源": []}

    def _add(rel: str) -> None:
        rel = str(rel or "").strip()
        if not rel or rel.startswith("data:"):
            return
        if os.path.isabs(rel):
            try:
                rel = os.path.relpath(rel, paths.DATA)
            except ValueError:
                return
        r = rel.replace("\\", "/")
        if r.startswith("参考/"):
            cat = "参考"
        elif r.startswith("数据源/"):
            cat = "数据源"
        else:
            cat = "素材"
            if "/" not in r:
                rel = r  # 裸文件名
            else:
                rel = r
        if rel not in refs[cat]:
            refs[cat].append(rel)

    _add(tpl.get("底图"))
    _add(tpl.get("参考图"))
    for g in tpl.get("图形") or []:
        _add(g.get("src"))
    for grp in tpl.get("组") or []:
        for e in grp.get("元素") or []:
            _add(e.get("src"))
    ds = tpl.get("数据源") or {}
    _add(ds.get("path"))
    return refs


def export_folder(src_name: str, dst_dir: str) -> str:
    """导出自包含文件夹：整个模板文件夹拷出去 + 说明.txt。

    Args:
        src_name: 模板名
        dst_dir: 目标父目录（在下面建一个「模板名」文件夹）

    Returns:
        创建的文件夹绝对路径。

    文件夹结构（和软件里 模板/<名>/ 一模一样）：
        模板名/
        ├── 模板.json
        ├── 素材/...
        ├── 参考/...
        ├── 数据源/...
        └── 说明.txt
    """
    tpl = load(src_name)
    src = tpl_dir(src_name)
    folder = os.path.join(dst_dir, _safe(src_name))
    if os.path.abspath(folder) == os.path.abspath(src):
        raise ValueError("导出目标就是模板自己所在的位置，换一个文件夹")
    if os.path.isdir(folder):
        shutil.rmtree(folder)
    # 版本存档不外发（旧版是内部草稿），.DS_Store 之类不要
    shutil.copytree(src, folder,
                    ignore=shutil.ignore_patterns("版本存档", ".DS_Store",
                                                  "__MACOSX", "Thumbs.db"))

    # 说明
    counts = {}
    for cat in CATS:
        d = os.path.join(folder, cat)
        if os.path.isdir(d):
            counts[cat] = len([x for x in os.listdir(d)
                               if os.path.isfile(os.path.join(d, x))])
    lines = [
        "标签重构工具 —— 模板导出包",
        "========================",
        "",
        "模板：%s" % src_name,
        "画布：%.1f × %.1f mm" % (float(tpl.get("画布", {}).get("宽", 0)),
                               float(tpl.get("画布", {}).get("高", 0))),
        "",
        "使用方法（空电脑）：",
        "1. 安装标签重构工具 3.0",
        "2. 打开软件 → 点「导入模板」→ 选这个文件夹",
        "3. 整个文件夹会被收进软件的 模板/ 下，模板直接可用",
        "",
        "包含文件：",
        "  模板.json —— 模板定义（版面、文字、数据映射）",
    ]
    for cat in CATS:
        if counts.get(cat):
            lines.append("  %s/ —— %d 个文件" % (cat, counts[cat]))
    with open(os.path.join(folder, "说明.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return folder


def import_folder(src_dir: str) -> str:
    """导入模板文件夹：整个文件夹收进 模板/<名>/（移动进来）。

    Args:
        src_dir: 模板文件夹路径（导出包，或任何带模板 json 的文件夹）

    Returns:
        落地后的模板名。

    Raises:
        FileNotFoundError: 文件夹里找不到模板 JSON。
    """
    migrate_layout()
    src_dir = os.path.abspath(src_dir)
    if not os.path.isdir(src_dir):
        raise FileNotFoundError("不是文件夹：%s" % src_dir)

    # 找模板 JSON（叫「模板.json」最好；否则取第一个 .json）
    tpl_fp = None
    for fn in sorted(os.listdir(src_dir)):
        p = os.path.join(src_dir, fn)
        if not os.path.isfile(p) or fn.startswith("."):
            continue
        if fn == "模板.json":
            tpl_fp = p
            break
        if fn.endswith(".json") and tpl_fp is None:
            tpl_fp = p
    if not tpl_fp:
        raise FileNotFoundError("文件夹里没有找到模板 JSON：%s" % src_dir)

    with open(tpl_fp, encoding="utf-8") as f:
        d = json.load(f)

    # 名字：模板里写的优先；重名自动加 (2)(3)…
    name = d.get("模板名") or os.path.splitext(os.path.basename(tpl_fp))[0]
    base, i = name, 2
    used = {t[0] for t in list_templates()}
    while name in used:
        name = "%s (%d)" % (base, i)
        i += 1
    d["模板名"] = name

    # 归一化文件夹内部命名：模板 json 统一叫 模板.json
    if os.path.basename(tpl_fp) != "模板.json":
        new_fp = os.path.join(src_dir, "模板.json")
        if os.path.exists(new_fp):
            os.remove(new_fp)
        os.rename(tpl_fp, new_fp)

    # 整个文件夹移进 模板/<名>/；移动失败（跨盘/只读）退回复制
    target = tpl_dir(name)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    if os.path.isdir(target):
        shutil.rmtree(target, ignore_errors=True)
    moved = False
    try:
        shutil.move(src_dir, target)
        moved = True
    except Exception:
        shutil.copytree(src_dir, target)
    if not os.path.isdir(target):
        raise RuntimeError("导入失败：模板文件夹没有落到 %s" % target)

    # 清掉系统垃圾文件
    for junk in (".DS_Store", "Thumbs.db", "desktop.ini", "__MACOSX"):
        p = os.path.join(target, junk)
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
        elif os.path.isfile(p):
            try:
                os.remove(p)
            except OSError:
                pass

    # 覆写模板名（重名加过序号的要写回去）
    save(d)
    return name


def import_file(src_path: str) -> str:
    """导入单个 json；重名自动加 (2)(3)…，返回落地后的模板名。"""
    with open(src_path, encoding="utf-8") as f:
        d = json.load(f)
    if "模板名" not in d and "revs" in d:
        raise ValueError("这是旧版网页备份，请用「迁移旧备份」功能")
    name = d.get("模板名") or os.path.splitext(os.path.basename(src_path))[0]
    base, i = name, 2
    used = {t[0] for t in list_templates()}
    while name in used:
        name = "%s (%d)" % (base, i)
        i += 1
    d["模板名"] = name
    save(d)
    return name


def import_legacy(src_path: str) -> str:
    """把网页版（revs/blocks/l2）的备份 json 迁成新结构。

    网页版把文字块放在 revs[rev].blocks，图形放 l2；这里取当前版本拍平。
    """
    with open(src_path, encoding="utf-8") as f:
        d = json.load(f)
    name = d.get("模板名") or "导入模板"
    revs = d.get("revs") or {}
    rev = revs.get(d.get("rev") or "rev01") or (list(revs.values())[0] if revs else {})
    tpl = blank_template(name)
    if rev.get("w") and rev.get("h"):
        tpl["画布"] = {"宽": float(rev["w"]), "高": float(rev["h"])}
    tpl["底图"] = rev.get("base")
    for g in rev.get("l2") or []:
        item = {"name": g.get("name", "图形"), "x": g["x"], "y": g["y"],
                "w": g["w"], "h": g["h"]}
        if g.get("type") == "rect":
            item["type"] = "rect"
            item["fill"] = g.get("fill", "#1B5EAB")
        else:
            item["src"] = g.get("src")
        tpl["图形"].append(item)
    for b in rev.get("blocks") or []:
        tpl["文字"].append({
            "k": b.get("k"), "name": b.get("name", b.get("k")),
            "layer": b.get("layer", "L3"),
            "lines": b.get("lines") or [], "bars": b.get("bars") or [],
        })
    tpl["字体"] = rev.get("font") or "hei"
    base, i = name, 2
    used = {t[0] for t in list_templates()}
    while name in used:
        name = "%s (%d)" % (base, i)
        i += 1
    tpl["模板名"] = name
    save(tpl)
    return name


# ── 旧目录结构迁移（一次性，幂等）────────────────────────────────
# 3.0 早期是「data/模板/*.json + data/素材/ 大池子」；现在改成
# 一模板一文件夹。老数据第一次打开时自动搬好，搬完打标记不再跑。

_MIGRATE_MARK = ".目录结构v3"


def migrate_layout() -> None:
    """把旧结构搬成「根目录 模板/ 一模板一文件夹」。幂等。"""
    mark = os.path.join(paths.DATA, _MIGRATE_MARK)
    if os.path.isfile(mark):
        return
    paths.ensure_dirs()

    # 0) 模板目录提升：data/模板/ → 根/模板/（v2 → v3，独立于 data/）
    old_tpl = os.path.join(paths.DATA, "模板")
    if os.path.isdir(old_tpl):
        for fn in os.listdir(old_tpl):
            s_ = os.path.join(old_tpl, fn)
            d_ = os.path.join(paths.TPL_DIR, fn)
            if not os.path.exists(d_):
                shutil.move(s_, d_)
            elif os.path.isdir(s_) and os.path.isdir(d_):
                # 两边都有同名模板文件夹：留根目录的，旧的挪进归档
                arch = os.path.join(paths.DATA, "_归档_旧结构", "模板")
                os.makedirs(arch, exist_ok=True)
                shutil.move(s_, os.path.join(arch, fn))
            else:
                shutil.move(s_, d_ + ".old")
        shutil.rmtree(old_tpl, ignore_errors=True)

    # 1) 字体搬出素材池 → data/字体/
    old_font = os.path.join(paths.DATA, "素材", "字体")
    if os.path.isdir(old_font):
        for fn in os.listdir(old_font):
            s = os.path.join(old_font, fn)
            if os.path.isfile(s):
                shutil.copy2(s, os.path.join(paths.FONT_DIR, fn))

    # 2) 每个散 json → 自己的文件夹；引用的文件从池子里拷进来
    if os.path.isdir(paths.TPL_DIR):
        for fn in os.listdir(paths.TPL_DIR):
            if not fn.endswith(".json") or fn.startswith("."):
                continue
            fp = os.path.join(paths.TPL_DIR, fn)
            if not os.path.isfile(fp):
                continue
            try:
                with open(fp, encoding="utf-8") as f:
                    d = json.load(f)
            except Exception:
                continue
            name = d.get("模板名") or os.path.splitext(fn)[0]
            tdir = tpl_dir(name)
            os.makedirs(tdir, exist_ok=True)
            for cat in CATS:
                os.makedirs(os.path.join(tdir, cat), exist_ok=True)
            shutil.move(fp, os.path.join(tdir, "模板.json"))
            # 引用文件 → 模板自己的资源目录
            for cat, items in _collect_refs(d).items():
                for rel in items:
                    base = os.path.basename(rel.replace("\\", "/"))
                    src = None
                    for c in (os.path.join(paths.DATA, rel),
                              os.path.join(paths.DATA, "素材", base),
                              os.path.join(paths.DATA, "参考", base),
                              os.path.join(paths.DATA, "数据源", base),
                              os.path.join(paths.DATA, base)):
                        if os.path.isfile(c):
                            src = c
                            break
                    if src:
                        shutil.copy2(src, os.path.join(tdir, cat, base))
            # 版本存档跟着模板走
            old_arch = os.path.join(paths.DATA, "版本存档", _safe(name))
            if os.path.isdir(old_arch):
                shutil.move(old_arch, os.path.join(tdir, "版本存档"))

    # 3) 素材池 → 图库（留着以后挑图）
    old_lib = os.path.join(paths.DATA, "素材")
    if os.path.isdir(old_lib):
        for fn in os.listdir(old_lib):
            if fn == "字体":
                continue
            s = os.path.join(old_lib, fn)
            if os.path.isfile(s):
                shutil.copy2(s, os.path.join(paths.IMG_LIB, fn))
        shutil.rmtree(old_lib, ignore_errors=True)

    # 4) 剩下的旧目录（参考/数据源/版本存档）挪进归档，data/ 要干净
    arch = os.path.join(paths.DATA, "_归档_旧结构")
    for cat in ("参考", "数据源", "版本存档"):
        p = os.path.join(paths.DATA, cat)
        if os.path.isdir(p) and os.listdir(p):
            os.makedirs(arch, exist_ok=True)
            dst = os.path.join(arch, cat)
            if os.path.isdir(dst):
                # 合并进去，不覆盖
                for fn in os.listdir(p):
                    s, e = os.path.join(p, fn), os.path.join(dst, fn)
                    if not os.path.exists(e):
                        shutil.move(s, e)
                shutil.rmtree(p, ignore_errors=True)
            else:
                shutil.move(p, dst)
        elif os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)

    with open(mark, "w", encoding="utf-8") as f:
        f.write("v2 一模板一文件夹 %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
