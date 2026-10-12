# coding: utf-8
"""
按日期分片的存储路径（v5.3+）

原则：单目录文件数永远可控（原版 CapsWriter 按年/月分片的思想）。
所有按天产生的文件都存到 年/月/ 下：

- docs/daily/YYYY/MM/YYYY-MM-DD.md（每日文档，v4 编译器输出）
- docs/daily/YYYY/MM/YYYY-MM-DD.summary.md（内容总结，v5 输出）
- logs/YYYY/MM/YYYY-MM-DD.jsonl（转写日志，v4 日志层）
- recordings/YYYY/MM/YYYY-MM-DD/HHMMSS.mp3（录音）

v5.3 起不再考虑老版本平铺路径的兼容（启用时间短，无历史包袱）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

BaseDir = Union[str, Path]


def _ym(date_str: str) -> tuple[str, str]:
    """'YYYY-MM-DD' -> ('YYYY', 'MM')"""
    y, m, _ = date_str.split("-")
    return y, m


def daily_doc_path(base_dir: BaseDir, date_str: str) -> Path:
    """每日文档路径：docs/daily/YYYY/MM/YYYY-MM-DD.md"""
    y, m = _ym(date_str)
    return Path(base_dir) / "docs" / "daily" / y / m / f"{date_str}.md"


def daily_summary_path(base_dir: BaseDir, date_str: str) -> Path:
    """内容总结路径：docs/daily/YYYY/MM/YYYY-MM-DD.summary.md"""
    y, m = _ym(date_str)
    return Path(base_dir) / "docs" / "daily" / y / m / f"{date_str}.summary.md"


def daily_log_path(base_dir: BaseDir, date_str: str) -> Path:
    """转写日志路径：logs/YYYY/MM/YYYY-MM-DD.jsonl"""
    y, m = _ym(date_str)
    return Path(base_dir) / "logs" / y / m / f"{date_str}.jsonl"


def daily_recordings_dir(base_dir: BaseDir, date_str: str) -> Path:
    """当天录音目录：recordings/YYYY/MM/YYYY-MM-DD/"""
    y, m = _ym(date_str)
    return Path(base_dir) / "recordings" / y / m / date_str
