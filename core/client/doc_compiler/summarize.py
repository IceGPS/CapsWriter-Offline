# coding: utf-8
"""
每日文档内容整理与总结（v5）

输入：docs/daily/YYYY/MM/YYYY-MM-DD.md（v4 编译器已清洗+分段的版本）
输出：docs/daily/YYYY/MM/YYYY-MM-DD.summary.md（按年/月分片，v5.3+）

流程：读文档 → 按 ## HH:MM 节切分 → 超长则分块整理 →
      本地大模型整理（主题分节/去重/要点）→ 再写今日总结

总结是派生文档：日志层和 docs/daily 原文档不受影响。

LLM 服务不可达时抛 LLMUnavailable（来自 llm_client），调用方捕获后静默跳过。

运行方式：
  python core/client/doc_compiler/summarize.py [YYYY-MM-DD] [--base-dir DIR]
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    # 包模式：python -m core.client.doc_compiler.summarize
    from .llm_client import LLMUnavailable, chat as _default_chat
    from .paths import daily_doc_path, daily_summary_path
except ImportError:
    # 脚本模式：python core/client/doc_compiler/summarize.py
    from llm_client import LLMUnavailable, chat as _default_chat
    from paths import daily_doc_path, daily_summary_path

LAST_SUMMARY_FILE = ".last_summary.json"
MAX_CHARS_PER_CALL = 20000  # 单次送给模型的输入上限（约 2 万字）

ORGANIZE_SYSTEM = """你是一位中文语音转写文档整理助手。用户每天通过语音输入记录想法、工作和生活，下面是某一天的转写记录（已做基础清洗和分段）。

请按以下要求整理：
1. 按主题重新分节：用 ## 加简短标题概括每节主题（不要用时间做标题）
2. 同一主题的段落归拢到一起，不同主题分开
3. 删除重复的句子、明显的口误，以及明显无意义的测试语句（如"现在试试语音输入"这类）
4. 每节末尾提炼 1-3 条要点，用 - 列表写出
5. 只使用原文中的信息，不要编造、不要脑补；拿不准归属的内容单独成节，不要强行归类
6. 尽量保留原文语句，不要大段改写

直接输出整理后的 Markdown，不要解释，不要加开场白。"""

SUMMARY_SYSTEM = """你是一位中文文档助手。根据下面整理好的今日记录，用 3-5 句话写一段"今日总结"，概括今天最主要的内容和事项。只基于原文信息，不要编造。直接输出总结正文，不要加标题。"""


def _default_base_dir() -> Path:
    # core/client/doc_compiler/summarize.py -> 上 3 级 = 程序根目录
    return Path(__file__).resolve().parents[3]


def _doc_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def split_sections(md_text: str) -> list[tuple[str, str]]:
    """按 ## 二级标题切分文档，返回 [(标题, 正文)]。跳过顶部注释。"""
    sections: list[tuple[str, str]] = []
    cur_head: str | None = None
    cur_lines: list[str] = []
    for line in md_text.splitlines():
        if line.startswith("## "):
            if cur_head is not None:
                sections.append((cur_head, "\n".join(cur_lines).strip()))
            cur_head = line.strip()
            cur_lines = []
        elif cur_head is not None:
            if line.strip().startswith("<!--"):
                continue
            cur_lines.append(line)
    if cur_head is not None:
        sections.append((cur_head, "\n".join(cur_lines).strip()))
    return [(h, b) for h, b in sections if b]


def build_chunks(md_text: str) -> list[str]:
    """
    切分输入：总长 <= 上限则整篇一块；超长则按 ## 节分块
    （单节仍超长则硬截断并标注，极端情况）。
    """
    sections = split_sections(md_text)
    if not sections:
        body = "\n".join(
            l for l in md_text.splitlines()
            if l.strip() and not l.startswith("#") and not l.strip().startswith("<!--")
        ).strip()
        return [body] if body else []
    full = "\n\n".join(f"{h}\n\n{b}" for h, b in sections)
    if len(full) <= MAX_CHARS_PER_CALL:
        return [full]
    chunks: list[str] = []
    for h, b in sections:
        text = f"{h}\n\n{b}"
        if len(text) > MAX_CHARS_PER_CALL:
            text = text[:MAX_CHARS_PER_CALL] + "\n\n（本节过长，已截断）"
        chunks.append(text)
    return chunks


def organize_chunk(chunk: str, chat_fn, base_url: str, model: str) -> str:
    """调 LLM 整理一个块，返回整理后的 Markdown。"""
    out = chat_fn(
        [{"role": "system", "content": ORGANIZE_SYSTEM},
         {"role": "user", "content": f"请整理以下今日记录：\n\n---\n{chunk}\n---"}],
        base_url=base_url, model=model,
    )
    out = (out or "").strip()
    if not out:
        raise LLMUnavailable("empty LLM response")
    return out


def write_summary(date_str: str, organized: str, summary_text: str,
                  base: Path) -> Path:
    out_path = daily_summary_path(base, date_str)
    content = (
        f"# {date_str} 内容总结\n\n"
        f"<!-- voice-ime 内容总结（本地大模型自动生成，原文档不受影响）。 -->\n\n"
        f"{organized.strip()}\n\n"
        f"## 今日总结\n\n"
        f"{summary_text.strip()}\n"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    return out_path


def _record_summary(date_str: str, base: Path, doc_hash: str) -> None:
    p = base / "logs" / LAST_SUMMARY_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, json.JSONDecodeError):
        data = {}
    data[date_str] = {"doc_hash": doc_hash,
                      "at": datetime.now().astimezone().isoformat()}
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def needs_summary(date_str: str, base_dir: str | Path | None = None) -> bool:
    """原文档是否存在且自上次总结后有变化（供定时器/托盘判断）。"""
    base = Path(base_dir) if base_dir else _default_base_dir()
    src = base / "docs" / "daily" / f"{date_str}.md"
    if not src.exists():
        return False
    try:
        h = _doc_hash(src.read_text(encoding="utf-8"))
    except OSError:
        return False
    try:
        data = json.loads((base / "logs" / LAST_SUMMARY_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    return data.get(date_str, {}).get("doc_hash") != h


def summarize_day(date_str: str,
                  base_dir: str | Path | None = None,
                  base_url: str = "http://127.0.0.1:8081",
                  model: str = "qwen",
                  chat_fn=None,
                  on_demand_server: bool = True) -> Path | None:
    """
    对某一天的文档做内容整理与总结。

    on_demand_server=True（默认）时：LLM 服务没在跑会自动按需启动，
    总结完成后关闭（v5.1+：27B 模型不再需要常驻内存）。服务已在跑
    （如用户手动起了 start_llm_server.bat）则直接用，用完不关。

    Returns:
        生成的 .summary.md 路径；无原文档时返回 None。
    Raises:
        LLMUnavailable: LLM 服务不可达或按需启动失败（调用方捕获后静默跳过）
    """
    if not on_demand_server:
        return _summarize_day_inner(date_str, base_dir, base_url, model, chat_fn)
    try:
        # 包模式 / 脚本模式双兼容
        from .llm_server import ensure_llm_server
    except ImportError:
        from llm_server import ensure_llm_server
    with ensure_llm_server(base_url, base_dir) as url:
        return _summarize_day_inner(date_str, base_dir, url, model, chat_fn)


def _summarize_day_inner(date_str: str,
                         base_dir: str | Path | None = None,
                         base_url: str = "http://127.0.0.1:8081",
                         model: str = "qwen",
                         chat_fn=None) -> Path | None:
    """真正的整理逻辑（要求调用方保证 LLM 服务可用）。"""
    chat = chat_fn or _default_chat
    base = Path(base_dir) if base_dir else _default_base_dir()
    src = daily_doc_path(base, date_str)
    if not src.exists():
        return None
    raw = src.read_text(encoding="utf-8")
    if not raw.strip():
        return None

    chunks = build_chunks(raw)
    if not chunks:
        return None

    organized_parts = [organize_chunk(c, chat, base_url, model) for c in chunks]
    organized = "\n\n".join(organized_parts)

    summary_text = chat(
        [{"role": "system", "content": SUMMARY_SYSTEM},
         {"role": "user", "content": organized}],
        base_url=base_url, model=model,
    ).strip()
    if not summary_text:
        raise LLMUnavailable("empty LLM response")

    out = write_summary(date_str, organized, summary_text, base)
    _record_summary(date_str, base, _doc_hash(raw))
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="voice-ime 每日文档内容总结（v5，需本地 LLM）")
    ap.add_argument("date", nargs="?", default=datetime.now().strftime("%Y-%m-%d"),
                    help="日期 YYYY-MM-DD，默认今天")
    ap.add_argument("--base-dir", default=None, help="程序根目录，默认自动推导")
    ap.add_argument("--base-url", default="http://127.0.0.1:8081", help="LLM 服务地址")
    ap.add_argument("--model", default="qwen", help="模型别名")
    ap.add_argument("--no-on-demand", action="store_true",
                    help="不按需启停 LLM 服务（要求服务已在跑）")
    args = ap.parse_args(argv)
    try:
        out = summarize_day(args.date, args.base_dir, args.base_url, args.model,
                            on_demand_server=not args.no_on_demand)
    except LLMUnavailable as e:
        print(f"LLM unavailable, skipped: {e}")
        return 2
    if out:
        print(f"done: {out}")
    else:
        print("no daily doc, skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
