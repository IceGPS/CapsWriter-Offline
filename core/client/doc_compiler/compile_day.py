# coding: utf-8
"""
每日文档编译器

流程：读取当天 jsonl → 清洗（语气词过滤）→ 按 30 分钟分节 →
      按分段规则组成段落（R1 默认独立成段 / R2 碎片合并 / R4 空条跳过）→
      写 docs/daily/YYYY/MM/YYYY-MM-DD.md（按年/月分片，单目录文件数可控）

分段规则（用户 2026-10-11 确认）：
  R1 默认独立成段：每次语音输入（jsonl 里的一条）独立成一段，段间空一行。
  R2 碎片合并（同时满足才合并）：(a) 与上一条间隔 < 60 秒；
      (b) 上一条"没说完"（清洗后字数 < 8，或结尾不是 。！？!?）；
      合并后单段超过 200 字则不合并。
  R3 时间分节不变：30 分钟一个 ## HH:MM 小节，跨节必分段。
  R4 空条跳过：清洗后变空的条目不输出、不占段落。

幂等：输出完全由日志推导，重复运行结果一致（内容无变化时不重写文件）。
手动锁定：旧文档中段内含有 <!-- manual --> 的二级标题段落，会按标题原样保留，
          重编时不覆盖。用法见 packaging/README_安装说明.txt。

运行方式：
  python core/client/doc_compiler/compile_day.py [YYYY-MM-DD]   # 脚本模式（无需装客户端依赖）
  python -m core.client.doc_compiler.compile_day [YYYY-MM-DD]   # 包模式（需客户端环境）
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

try:
    # 包模式：python -m core.client.doc_compiler.compile_day
    from .clean import split_sentences, clean_text
    from .corrections import build_correction_stats_md
    from .log import DailyLogger  # noqa: F401（供外部复用，保持包内一致）
    from .paths import daily_doc_path, daily_log_path
except ImportError:
    # 脚本模式：python core/client/doc_compiler/compile_day.py
    from clean import split_sentences, clean_text
    from corrections import build_correction_stats_md
    from log import DailyLogger  # noqa: F401
    from paths import daily_doc_path, daily_log_path

MANUAL_MARK = "<!-- manual -->"
GROUP_GAP_MINUTES = 30  # R3: 相邻两条记录间隔超过 30 分钟则另起一节
MERGE_GAP_SECONDS = 60  # R2(a): 碎片合并要求与上一条间隔 < 60 秒
MERGE_MAX_CHARS = 200   # R2: 合并后单段超过 200 字则不合并
FRAG_MAX_CHARS = 8      # R2(b): 清洗后字数 < 8 视为"没说完"
LAST_COMPILE_FILE = ".last_compile.json"


def _default_base_dir() -> Path:
    # core/client/doc_compiler/compile_day.py -> 上 3 级 = 程序根目录
    return Path(__file__).resolve().parents[3]


def load_entries(date_str: str, base_dir: Path) -> list[dict]:
    """读取当天 jsonl，按时间排序。"""
    p = daily_log_path(base_dir, date_str)
    if not p.exists():
        return []
    entries: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            e["_dt"] = datetime.fromisoformat(e.get("ts", ""))
        except ValueError:
            continue
        entries.append(e)
    entries.sort(key=lambda e: e["_dt"])
    return entries


def clean_entries(entries: list[dict]) -> list[dict]:
    """逐条清洗（带跨条目的上下文，用于问答应答"嗯"的判断）。"""
    prev_last = ""
    for e in entries:
        raw = e.get("text") or ""
        if e.get("status") != "ok" or not raw.strip():
            e["_clean"] = ""
        else:
            e["_clean"] = clean_text(raw, prev_last)
        if e["_clean"]:
            sents = split_sentences(e["_clean"])
            if sents:
                prev_last = sents[-1]
    return entries


def group_entries(entries: list[dict]) -> list[list[dict]]:
    """按时间分组：相邻记录间隔在 30 分钟内合并为一段。"""
    groups: list[list[dict]] = []
    for e in entries:
        if not e.get("_clean"):
            continue
        if groups and (e["_dt"] - groups[-1][-1]["_dt"]) <= timedelta(minutes=GROUP_GAP_MINUTES):
            groups[-1].append(e)
        else:
            groups.append([e])
    return groups


_END_PUNC = "。！？!?"


def _is_unfinished(text: str) -> bool:
    """R2(b): 判断上一条是否"没说完"——清洗后字数 < 8，或结尾不是 。！？!?。"""
    t = text.strip()
    if not t:
        return False
    return len(t) < FRAG_MAX_CHARS or t[-1] not in _END_PUNC


def _dedup_sentences(text: str) -> str:
    """段内连续重复句去重。"""
    out: list[str] = []
    for s in split_sentences(text):
        if not out or out[-1] != s:
            out.append(s)
    return "".join(out)


def build_paragraphs(entries: list[dict]) -> list[str]:
    """
    R1/R2/R4：把一个时间节内的条目组成段落列表。

    - R1：默认每条独立成段
    - R2：碎片合并——与上一条间隔 <60s 且上一条没说完，且合并后 <=200 字；
          合并时直接拼接（上一条若以"，"结尾则自然衔接）
    - R4：空条目跳过（不输出、不占段落）
    - 连续重复条目去重
    """
    paras: list[str] = []
    prev_clean = ""
    prev_dt = None
    for e in entries:
        cur = _dedup_sentences((e.get("_clean") or "").strip())
        if not cur:
            continue  # R4
        if cur == prev_clean:
            prev_dt = e["_dt"]  # 重复条目去重，时间照常推进
            continue
        merged = False
        if paras and prev_dt is not None:
            gap = (e["_dt"] - prev_dt).total_seconds()
            if gap < MERGE_GAP_SECONDS and _is_unfinished(prev_clean):
                candidate = paras[-1] + cur
                if len(candidate) <= MERGE_MAX_CHARS:
                    paras[-1] = candidate
                    merged = True
        if not merged:
            paras.append(cur)
        prev_clean = cur
        prev_dt = e["_dt"]
    return paras


def extract_manual_sections(md_text: str) -> dict[str, str]:
    """
    按 ## 标题切分旧文档，返回手动锁定的段落 {标题: 段落原文}。

    标记写法（两种都支持）：
      1. 写在二级标题前面一行：<!-- manual -->\n## 08:31
      2. 写在段落内部任意位置
    """
    sections: dict[str, str] = {}
    cur_head: str | None = None
    cur_lines: list[str] = []
    cur_manual = False
    pending_manual = False  # 标题前一行的标记

    def _flush() -> None:
        if cur_head is not None and (cur_manual or MANUAL_MARK in "".join(cur_lines)):
            sections[cur_head] = "".join(cur_lines)

    for line in md_text.splitlines(keepends=True):
        if line.startswith("## "):
            _flush()
            cur_head = line.strip()
            cur_lines = [line]
            cur_manual = pending_manual
            pending_manual = False
        elif cur_head is not None:
            cur_lines.append(line)
        elif MANUAL_MARK in line:
            pending_manual = True
    _flush()
    return sections


def compile_day(date_str: str, base_dir: str | Path | None = None) -> Path | None:
    """
    编译某一天的文档。

    Returns:
        生成的文档路径；当天无日志时返回 None。
    """
    base = Path(base_dir) if base_dir else _default_base_dir()
    entries = load_entries(date_str, base)
    if not entries:
        return None

    entries = clean_entries(entries)
    groups = group_entries(entries)

    out_path = daily_doc_path(base, date_str)
    manual = extract_manual_sections(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}

    lines = [
        f"# {date_str}",
        "",
        "<!-- voice-ime 每日文档（自动生成）。手动锁定段落的方法见安装说明。 -->",
        "",
    ]
    for g in groups:
        head = "## " + g[0]["_dt"].strftime("%H:%M")
        if head in manual:
            lines.append(manual[head].rstrip("\n"))
            lines.append("")
            continue
        paras = build_paragraphs(g)
        body = "\n\n".join(paras)
        if not body:
            continue
        lines.append(head)
        lines.append("")
        lines.append(body)
        lines.append("")

    # 语音纠错统计（确定性生成，无事件则不加；纠错条目不进正文，见 clean_entries）
    stats_md = build_correction_stats_md(entries)
    if stats_md:
        lines.append(stats_md.rstrip("\n"))
        lines.append("")

    content = "\n".join(lines).rstrip("\n") + "\n"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not (out_path.exists() and out_path.read_text(encoding="utf-8") == content):
        out_path.write_text(content, encoding="utf-8")
    _record_compile(date_str, base, len(entries))
    return out_path


def _record_compile(date_str: str, base: Path, count: int) -> None:
    p = base / "logs" / LAST_COMPILE_FILE
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, json.JSONDecodeError):
        data = {}
    data[date_str] = {"count": count, "at": datetime.now().astimezone().isoformat()}
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def needs_compile(date_str: str, base_dir: str | Path | None = None) -> bool:
    """当天日志条数是否多于上次编译时（供定时器判断"有新日志"）。"""
    base = Path(base_dir) if base_dir else _default_base_dir()
    lp = daily_log_path(base, date_str)
    if not lp.exists():
        return False
    try:
        with open(lp, encoding="utf-8") as f:
            count = sum(1 for line in f if line.strip())
    except OSError:
        return False
    try:
        data = json.loads((base / "logs" / LAST_COMPILE_FILE).read_text(encoding="utf-8"))
        done = data.get(date_str, {}).get("count", 0)
    except (OSError, json.JSONDecodeError):
        done = 0
    return count > done


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="voice-ime 每日文档编译器")
    ap.add_argument("date", nargs="?", default=datetime.now().strftime("%Y-%m-%d"),
                    help="日期 YYYY-MM-DD，默认今天")
    ap.add_argument("--base-dir", default=None, help="程序根目录，默认自动推导")
    args = ap.parse_args(argv)
    out = compile_day(args.date, args.base_dir)
    if out:
        print(f"done: {out}")
    else:
        print("no logs, skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
