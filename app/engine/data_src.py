# -*- coding: utf-8 -*-
"""data_src.py —— 读数据表（xlsx / csv / json），给 L4 供值。

为什么自己解 xlsx 而不装 openpyxl：
  xlsx 本质是 zip + xml，读表头和数据只要几十行；少一个依赖，
  打包体积小、离线部署少一个出错点。
"""
import csv
import io
import json
import os
import re
import zipfile
from xml.etree import ElementTree as ET

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


def _col_index(ref: str) -> int:
    """A1 → 0，B7 → 1，AA3 → 26。"""
    m = re.match(r"([A-Z]+)", ref or "")
    if not m:
        return 0
    n = 0
    for ch in m.group(1):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def read_xlsx(path: str) -> dict:
    """返回 {sheet 名: [[单元格, ...], ...]}，全部按文本取。"""
    out = {}
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", NS):
                shared.append("".join(t.text or "" for t in si.iter(
                    "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = {}
        if "xl/_rels/workbook.xml.rels" in names:
            rr = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
            for rel in rr:
                rels[rel.get("Id")] = rel.get("Target")
        for sh in wb.find("m:sheets", NS):
            title = sh.get("name")
            rid = sh.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
            target = rels.get(rid, "")
            part = "xl/" + target.lstrip("/").replace("xl/", "", 1) if target else ""
            if part not in names:
                cand = [n for n in names if n.startswith("xl/worksheets/sheet")]
                part = cand[0] if cand else ""
            if not part or part not in names:
                continue
            root = ET.fromstring(z.read(part))
            rows = []
            for row in root.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row"):
                cells = {}
                for c in row.findall("m:c", NS):
                    idx = _col_index(c.get("r"))
                    t = c.get("t")
                    v = c.find("m:v", NS)
                    if t == "s" and v is not None:
                        try:
                            val = shared[int(v.text)]
                        except Exception:
                            val = ""
                    elif t == "inlineStr":
                        val = "".join(x.text or "" for x in c.iter(
                            "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"))
                    else:
                        val = v.text if v is not None else ""
                    cells[idx] = (val or "").strip()
                if cells:
                    width = max(cells) + 1
                    rows.append([cells.get(i, "") for i in range(width)])
            if rows:
                out[title] = rows
    return out


def read_csv(path: str) -> dict:
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "gbk", "utf-8"):
        try:
            text = raw.decode(enc)
            break
        except Exception:
            continue
    else:
        text = raw.decode("utf-8", "replace")
    rows = [r for r in csv.reader(io.StringIO(text)) if any(x.strip() for x in r)]
    return {"CSV": rows}


def read_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    if isinstance(d, dict) and "cols" in d and "rows" in d:
        rows = [list(d["cols"])] + [r if isinstance(r, list) else
                                    [r.get(c, "") for c in d["cols"]] for r in d["rows"]]
        return {"JSON": rows}
    if isinstance(d, list) and d and isinstance(d[0], dict):
        cols = list(d[0].keys())
        return {"JSON": [cols] + [[str(r.get(c, "")) for c in cols] for r in d]}
    raise ValueError("这个 json 不像表格")


def read_table(path: str) -> dict:
    """按扩展名分流，返回 {表名: 二维数组}。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        return read_xlsx(path)
    if ext in (".csv", ".tsv"):
        return read_csv(path)
    if ext == ".json":
        return read_json(path)
    raise ValueError("不支持的表格类型：%s" % ext)


def pick_records(path: str, sheet: str = None) -> tuple:
    """取一张表 → (表头, [每行 dict])。

    优先用含 NOTE 列的表（工具导出的「NOTE投喂」）；否则用第一张有数据的表。
    """
    tables = read_table(path)
    if not tables:
        raise ValueError("这个文件里没有可读的表")
    name = sheet
    if not name:
        for nm, rows in tables.items():
            head = [str(x) for x in (rows[0] if rows else [])]
            if any(h.upper().startswith("NOTE") for h in head):
                name = nm
                break
        name = name or next(iter(tables))
    rows = tables[name]
    if len(rows) < 2:
        raise ValueError("表「%s」只有表头，没有数据行" % name)
    head = [str(x).strip() for x in rows[0]]
    recs = []
    for r in rows[1:]:
        rec = {}
        for i, h in enumerate(head):
            if h:
                rec[h] = r[i] if i < len(r) else ""
        if any(str(v).strip() for v in rec.values()):
            recs.append(rec)
    return head, recs, name
