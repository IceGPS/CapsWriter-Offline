# coding: utf-8
"""
日志层：每次转写完成记录 {ts, text, audio} 三元组（v5.3+ 按年/月分片）

- 录音：直接引用原文件（年/月/assets/ 下的 MP3，重命名后的最终路径），
  v5.4+ 不再复制，省一半存储
- 日志：logs/YYYY/MM/YYYY-MM-DD.jsonl（每行一个 JSON）

即使转写失败/为空也记录（text 为空字符串，status 标记），保证录音不丢。
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Union


class DailyLogger:
    """每日转写日志记录器（只依赖标准库）"""

    def __init__(self, base_dir: Union[str, Path]):
        self.base_dir = Path(base_dir)
        self.logs_dir = self.base_dir / "logs"

    def log_transcript(
        self,
        time_start: Optional[float],
        text: Optional[str],
        audio_path: Optional[Union[str, Path]] = None,
        status: str = "ok",
        dur_ms: Optional[int] = None,
    ) -> Optional[Path]:
        """
        记录一次转写。

        Args:
            time_start: 录音开始时间戳（epoch 秒）；为 None 则取当前时间
            text: 原始转写文本（含语气词）；为 None 则记为空字符串
            audio_path: 录音文件路径；为 None 或文件不存在则 audio 记为 null
            status: 'ok' / 'empty'（转写为空）/ 'error'
            dur_ms: 转写耗时毫秒（可选）

        Returns:
            当天 jsonl 日志路径
        """
        ts = time_start if time_start else time.time()
        day = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        try:
            from .paths import daily_log_path
        except ImportError:
            from paths import daily_log_path

        # 1. 录音：直接引用原文件（v5.4+ 不再复制）
        #    调用方传的是重命名后的最终路径（result_processor 先 rename 再记日志），
        #    文件在 年/月/assets/ 下持久保存，不会被删除，引用安全。
        audio_rel = None
        if audio_path:
            src = Path(audio_path)
            if src.exists() and src.is_file():
                try:
                    audio_rel = src.relative_to(self.base_dir).as_posix()
                except ValueError:
                    audio_rel = src.as_posix()

        # 2. 追加 jsonl
        log_path = daily_log_path(self.base_dir, day)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        line = {
            "ts": datetime.fromtimestamp(ts).astimezone().isoformat(),
            "text": text or "",
            "audio": audio_rel,
            "status": status,
        }
        if dur_ms is not None:
            line["dur_ms"] = dur_ms
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
        return log_path
