# coding: utf-8
"""
音频文件管理模块

提供 AudioFileManager 类用于管理音频文件的创建、写入、完成和重命名。

录音统一存为 MP3（v5.2+，替代 WAV，省空间）：
  1. lameenc（pip 包，内置 LAME，无需外部依赖）—— 首选
  2. FFmpeg 子进程 —— 备用（192k，老逻辑保留）
  3. WAV（wave 模块）—— 兜底

码率：单声道 64k / 双声道 96k，CBR。语音内容归档足够，
是 WAV（48kHz 16bit 单声道 = 768k）的约 1/12。
"""

from __future__ import annotations

import re
import shutil
import tempfile
import time
import wave
from os import makedirs
from pathlib import Path
from subprocess import DEVNULL, PIPE, Popen
from typing import Optional, Tuple, Union

import numpy as np

from config_client import ClientConfig as Config
from . import logger

try:
    import lameenc
    _HAS_LAMEENC = True
except ImportError:
    lameenc = None  # type: ignore
    _HAS_LAMEENC = False

# 音频文件句柄类型
AudioWriter = Union[Popen, wave.Wave_write, "lameenc.Encoder"]


class AudioFileManager:
    """
    音频文件管理器

    负责音频文件的完整生命周期管理：
    - 创建：按 lameenc > FFmpeg > WAV 的优先级选择编码器，一律输出 MP3（除兜底外）
    - 写入：将音频数据写入文件
    - 完成：关闭文件句柄（lameenc 需要 flush 尾部数据）
    - 重命名：根据识别文本重命名文件
    """

    SAMPLE_RATE = 48000
    MP3_BITRATE_MONO = 64    # kbps，单声道语音归档
    MP3_BITRATE_STEREO = 96  # kbps，双声道

    def __init__(self):
        """初始化音频文件管理器"""
        self.file_path: Optional[Path] = None
        self.file_handle: Optional[AudioWriter] = None
        self.channels: int = 1
        self._lame_file = None  # lameenc 输出的目标文件对象
        self._has_ffmpeg = shutil.which('ffmpeg') is not None

        if _HAS_LAMEENC:
            logger.debug("检测到 lameenc，将使用 MP3 格式保存录音")
        elif self._has_ffmpeg:
            logger.debug("检测到 FFmpeg，将使用 MP3 格式保存录音")
        else:
            logger.debug("未检测到 lameenc/FFmpeg，将使用 WAV 格式保存录音")

    def _mp3_bitrate(self, channels: int) -> int:
        return self.MP3_BITRATE_STEREO if channels > 1 else self.MP3_BITRATE_MONO

    def create(self, channels: int, time_start: float) -> Tuple[Path, AudioWriter]:
        """
        创建音频文件

        Args:
            channels: 音频声道数
            time_start: 录音开始时间戳

        Returns:
            (文件路径, 文件写入句柄) 元组
        """
        self.channels = channels

        # 构建目录和文件名
        local_time = time.localtime(time_start)
        time_year = time.strftime('%Y', local_time)
        time_month = time.strftime('%m', local_time)
        time_ymdhms = time.strftime("%Y%m%d-%H%M%S", local_time)

        folder_path = Path() / time_year / time_month / 'assets'
        makedirs(folder_path, exist_ok=True)

        # 创建临时文件名
        file_path = tempfile.mktemp(prefix=f'({time_ymdhms})', dir=folder_path)
        file_path = Path(file_path)

        if _HAS_LAMEENC:
            # 首选：lameenc 进程内编码，无外部依赖
            file_path = file_path.with_suffix('.mp3')
            enc = lameenc.Encoder()
            enc.set_bit_rate(self._mp3_bitrate(channels))
            enc.set_in_sample_rate(self.SAMPLE_RATE)
            enc.set_channels(channels)
            enc.set_quality(2)  # 高质量，速度仍远快于实时
            self._lame_file = open(file_path, 'wb')
            file_handle = enc
            logger.debug(f"创建 MP3 文件 (lameenc {self._mp3_bitrate(channels)}k): {file_path}")
        elif self._has_ffmpeg:
            # 备用：FFmpeg 子进程输出 MP3
            file_path = file_path.with_suffix('.mp3')
            ffmpeg_command = [
                'ffmpeg', '-y',
                '-f', 'f32le',
                '-ar', str(self.SAMPLE_RATE),
                '-ac', str(channels),
                '-i', '-',
                '-b:a', '192k',
                str(file_path),
            ]
            file_handle = Popen(ffmpeg_command, stdin=PIPE, stdout=DEVNULL, stderr=DEVNULL)
            logger.debug(f"创建 MP3 文件 (ffmpeg): {file_path}")
        else:
            # 兜底：WAV
            file_path = file_path.with_suffix('.wav')
            file_handle = wave.open(str(file_path), 'w')
            file_handle.setnchannels(channels)
            file_handle.setsampwidth(2)  # 16-bit
            file_handle.setframerate(self.SAMPLE_RATE)
            logger.debug(f"创建 WAV 文件: {file_path}")

        self.file_path = file_path
        self.file_handle = file_handle

        return file_path, file_handle

    def write(self, data: np.ndarray) -> None:
        """
        写入音频数据

        Args:
            data: 音频数据数组（float32 格式）
        """
        if self.file_handle is None:
            logger.warning("尝试写入数据但文件未打开")
            return

        if _HAS_LAMEENC and isinstance(self.file_handle, lameenc.Encoder):
            # lameenc：float32 -> int16，编码后写入文件
            int_data = (np.clip(data, -1.0, 1.0) * (2**15 - 1)).astype(np.int16).tobytes()
            mp3_data = self.file_handle.encode(int_data)
            if mp3_data and self._lame_file:
                self._lame_file.write(mp3_data)
        elif isinstance(self.file_handle, Popen):
            # FFmpeg 进程
            self.file_handle.stdin.write(data.tobytes())
            self.file_handle.stdin.flush()
        elif isinstance(self.file_handle, wave.Wave_write):
            # WAV 文件：转换 float32 -> int16
            int_data = (data * (2**15 - 1)).astype(np.int16).tobytes()
            self.file_handle.writeframes(int_data)

    def finish(self) -> Optional[Path]:
        """
        完成音频文件写入

        Returns:
            音频文件路径
        """
        if self.file_handle is None:
            return self.file_path

        try:
            if _HAS_LAMEENC and isinstance(self.file_handle, lameenc.Encoder):
                # lameenc：flush 尾部数据后关闭文件
                tail = self.file_handle.flush()
                if tail and self._lame_file:
                    self._lame_file.write(tail)
                if self._lame_file:
                    self._lame_file.close()
                    self._lame_file = None
                logger.debug("MP3 文件已关闭 (lameenc)")
            elif isinstance(self.file_handle, Popen):
                self.file_handle.stdin.close()
                logger.debug("FFmpeg 进程已关闭")
            elif isinstance(self.file_handle, wave.Wave_write):
                self.file_handle.close()
                logger.debug("WAV 文件已关闭")
        except Exception as e:
            logger.error(f"关闭音频文件时发生错误: {e}")
        finally:
            self.file_handle = None

        return self.file_path

    def rename(self, text: str, time_start: float) -> Optional[Path]:
        """
        根据识别文本重命名音频文件

        Args:
            text: 识别出的文本
            time_start: 录音开始时间戳

        Returns:
            重命名后的文件路径，如果失败返回 None
        """
        if self.file_path is None or not self.file_path.exists():
            logger.warning(f"文件不存在，无法重命名: {self.file_path}")
            return None

        # 构建新文件名
        time_ymdhms = time.strftime("%Y%m%d-%H%M%S", time.localtime(time_start))

        # 截取文本并清理非法字符
        text_clean = text[:Config.audio_name_len]
        text_clean = re.sub(r'[\\/:\"*?<>|]', ' ', text_clean)

        file_stem = f'({time_ymdhms}){text_clean}'
        new_path = self.file_path.with_name(file_stem + self.file_path.suffix)

        try:
            self.file_path.rename(new_path)
            logger.debug(f"音频文件已重命名: {self.file_path.name} -> {new_path.name}")
            self.file_path = new_path
            return new_path
        except Exception as e:
            logger.error(f"重命名音频文件失败: {e}")
            return self.file_path
