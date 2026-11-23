# -*- coding: utf-8 -*-
"""learn.py —— 技能库的「学习记录」（P2）。

问题
----
`data/技能/` 下有一堆手册，但**都是写死的**。人每次纠正 AI 的判断
（对位偏了、选错图、链接了错列），那个纠正只活在当前这一次操作里 ——
下次开新模板，同样的错还会再犯一遍。

用户口径（P2）
-------------
> 新增 data/技能/学习记录.md（或 jsonl），由 ctl 在每次
> **对位 / 选图 / 链接被人工纠正时**追加一条（症状、原因、规则）。

设计取舍：**_md 便于人读，_jsonl 便于机器读，两份都写**
------------------------------------------------------
· `学习记录.md`    —— 按分类分组的人类可读整理版，AI 下一轮直接读这个
· `学习记录.jsonl` —— 一行一条，机器追加、去重、统计都方便

同一份数据写两遍会不会不一致？会 —— 但 md 是**从 jsonl 重新生成**的
（`rebuild_md()`），不是手写的。任何新增都先落 jsonl，再重建 md。
这样只有一个真来源，md 永远是派生文件。

什么时候记
---------
只在这三种**人工纠正**时记（用户明确的三类）：
    place    对位偏了，人挪了
    pick     选图选错了，人换了
    link     链接错列了，人改了

**不记**自动成功的操作 —— 那会把记录淹成流水账，下次读的时候
找不到"踩过哪些坑"。
"""
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
for _p in (os.path.join(_ROOT, "app"), _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import paths  # noqa: E402

SKILL_DIR = os.path.join(paths.DATA, "技能")
JSONL = os.path.join(SKILL_DIR, "学习记录.jsonl")
MD = os.path.join(SKILL_DIR, "学习记录.md")

# 三种人工纠正（用户口径）。key → 中文名
KINDS = {
    "place": "对位",
    "pick": "选图",
    "link": "链接",
}


def _ensure_dir():
    os.makedirs(SKILL_DIR, exist_ok=True)


def record(kind, symptom, cause, rule, tpl=None, detail=None, when=None):
    """追加一条学习记录。

    Args:
        kind: "place" / "pick" / "link"（见 KINDS）
        symptom: **症状** —— 看到的是什么现象（"REF 那块往右下偏了 0.8mm"）
        cause: **原因** —— 为什么会这样（"叠影那行和上一行墨迹连在一起"）
        rule: **规则** —— 下次该怎么办（"对位前先看窗口里有没有邻行墨迹"）
        tpl: 模板名（可选，便于区分是哪张标签学的）
        detail: 结构化补充（可选，dict）
        when: 时间戳（默认现在）

    Returns:
        记录 dict；三个必填字段有空就返回 None（不许记半条）
    """
    kind = str(kind or "").strip()
    symptom = str(symptom or "").strip()
    cause = str(cause or "").strip()
    rule = str(rule or "").strip()
    if kind not in KINDS:
        return None
    if not (symptom and cause and rule):
        return None
    _ensure_dir()
    rec = {
        "时间": when or time.strftime("%Y-%m-%d %H:%M:%S"),
        "类别": kind,
        "类别名": KINDS[kind],
        "模板": str(tpl or ""),
        "症状": symptom,
        "原因": cause,
        "规则": rule,
    }
    if detail:
        rec["补充"] = detail
    try:
        with open(JSONL, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        return None
    rebuild_md()
    return rec


def load():
    """读所有记录（按写入顺序）。坏行跳过。"""
    if not os.path.isfile(JSONL):
        return []
    out = []
    try:
        with open(JSONL, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    d = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                if isinstance(d, dict):
                    out.append(d)
    except OSError:
        return []
    return out


def _sig(r):
    """去重签名：同一类别 + 同一句话算重复（换了模板也算同一条规律）。"""
    return (r.get("类别"), r.get("症状"), r.get("规则"))


def dedup(recs=None):
    """去重（保留最后一次，因为规则可能被修正过）。"""
    recs = recs if recs is not None else load()
    seen = {}
    for r in recs:
        seen[_sig(r)] = r
    # 按时间顺序输出
    return sorted(seen.values(), key=lambda r: r.get("时间") or "")


def rebuild_md(recs=None):
    """从 jsonl 重建 md。**md 永远是派生的，不要手改。**

    分组顺序按 KINDS 的定序（对位 → 选图 → 链接），组内按时间倒序
    （最近学的放前面 —— 下次读的人先看到最新的教训）。
    """
    recs = dedup(recs) if recs is None else recs
    _ensure_dir()
    lines = [
        "# 学习记录",
        "",
        "> 这份文件由 `app/engine/learn.py` **自动生成**，不要手改。",
        "> 手改会被下次 `rebuild_md()` 覆盖。要加记录，用：",
        ">",
        "> ```",
        "> runtime/bin/python3 -B app/engine/ctl.py learn \\",
        ">   --kind place --symptom \"...\" --cause \"...\" --rule \"...\"",
        "> ```",
        "",
        "记录的是**人工纠正**：对位偏了、选图错了、链接错列了。",
        "自动成功的操作不记 —— 那会淹成流水账，下次读的人找不到重点。",
        "",
        "---",
        "",
    ]
    if not recs:
        lines.append("_还没有记录。第一次纠正时会自动写进来。_")
        lines.append("")
    else:
        lines.append("共 **%d** 条（去重后）。" % len(recs))
        lines.append("")
        for key, label in KINDS.items():
            grp = [r for r in recs if r.get("类别") == key]
            if not grp:
                continue
            lines.append("## %s（%d 条）" % (label, len(grp)))
            lines.append("")
            for r in sorted(grp, key=lambda x: x.get("时间") or "",
                            reverse=True):
                lines.append("### %s　`%s`%s" % (
                    r.get("症状", ""),
                    r.get("时间", ""),
                    ("　·　" + r["模板"]) if r.get("模板") else ""))
                lines.append("")
                lines.append("- **原因**：%s" % r.get("原因", ""))
                lines.append("- **规则**：%s" % r.get("规则", ""))
                if r.get("补充"):
                    lines.append("- **补充**：`%s`" % json.dumps(
                        r["补充"], ensure_ascii=False))
                lines.append("")
            lines.append("")
    try:
        with open(MD, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return MD
    except OSError:
        return None


def stats():
    """按类别统计条数（给 UI/报告用）。"""
    recs = dedup()
    out = {k: 0 for k in KINDS}
    for r in recs:
        k = r.get("类别")
        if k in out:
            out[k] += 1
    out["总数"] = len(recs)
    return out
