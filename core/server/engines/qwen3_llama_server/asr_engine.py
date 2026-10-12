# coding: utf-8
"""
Qwen3-ASR llama-server HTTP 引擎适配器（voice-ime 第一阶段）

架构：
    CapsWriter 服务端 --HTTP--> 本地 llama-server --mtmd--> Qwen3-ASR-1.7B

llama-server 启动示例：
    llama-server -m models/Qwen3-ASR-1.7B-HF/Qwen3-ASR-1.7B-Q8_0.gguf \\
                 --mmproj models/Qwen3-ASR-1.7B-HF/mmproj.gguf \\
                 --port 8080

调用 OpenAI 兼容接口 POST /v1/audio/transcriptions。
"""
import io
import os
import time
import wave
import tempfile
from dataclasses import dataclass
from typing import Optional, List

import numpy as np

from ..base import BaseASREngine, RecognitionStream, EngineCapabilities
from .. import logger


@dataclass
class Qwen3LlamaServerConfig:
    """llama-server 引擎配置"""
    server_url: str = 'http://127.0.0.1:8080'  # llama-server 地址
    model: str = 'Qwen3-ASR-1.7B'               # 传给 API 的 model 字段
    language: str = 'zh'                        # 转写语言（zh/en/auto）
    timeout: float = 300.0                      # 单次请求超时（秒）
    verbose: bool = False


class Qwen3LlamaServerStream(RecognitionStream):
    """识别流：缓存音频，decode 时一次性发送"""
    def __init__(self, sample_rate=16000):
        super().__init__(sample_rate)
        self.audio_data = None

    def accept_waveform(self, sample_rate, audio):
        self.sample_rate = sample_rate
        self.audio_data = np.asarray(audio, dtype=np.float32)


def _write_wav_16k(audio: np.ndarray, sample_rate: int) -> bytes:
    """float32 音频 -> 16k 单声道 WAV 字节"""
    if sample_rate != 16000:
        raise ValueError(f"仅支持 16000Hz，当前 {sample_rate}")
    pcm = np.clip(audio, -1.0, 1.0)
    pcm16 = (pcm * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm16.tobytes())
    return buf.getvalue()


class Qwen3LlamaServerEngine(BaseASREngine):
    """通过本地 llama-server HTTP 接口调用 Qwen3-ASR 的引擎"""

    def __init__(self, config: Qwen3LlamaServerConfig):
        super().__init__(config)
        # 启动时检查服务可用性（不阻塞：失败只告警，首次 decode 时再报错）
        if not self._ping():
            logger.warning(
                f"[qwen3_llama_server] 无法连接 {config.server_url}，"
                f"请先启动 llama-server（见模块 docstring）"
            )

    @property
    def capabilities(self) -> List[EngineCapabilities]:
        return [EngineCapabilities.ASR, EngineCapabilities.PUNC]

    def _ping(self) -> bool:
        try:
            import requests
            r = requests.get(self.config.server_url + '/health', timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    def create_stream(self, hotwords: Optional[str] = None) -> Qwen3LlamaServerStream:
        return Qwen3LlamaServerStream()

    def decode_stream(self, stream: Qwen3LlamaServerStream,
                      context: Optional[str] = None, **kwargs):
        if stream.audio_data is None or len(stream.audio_data) == 0:
            return
        import requests

        wav_bytes = _write_wav_16k(stream.audio_data, stream.sample_rate)
        language = kwargs.get('language') or self.config.language
        # 统一语言代码：Chinese/中文 -> zh
        lang_map = {'Chinese': 'zh', '中文': 'zh', 'English': 'en', '英文': 'en'}
        language = lang_map.get(language, language)

        files = {'file': ('audio.wav', wav_bytes, 'audio/wav')}
        data = {'model': self.config.model, 'language': language,
                'response_format': 'json'}
        # Qwen3-ASR 的 prompt：明确要求转写
        data['prompt'] = 'Transcribe the speech to text.'

        t0 = time.time()
        try:
            r = requests.post(
                self.config.server_url + '/v1/audio/transcriptions',
                files=files, data=data, timeout=self.config.timeout,
            )
            r.raise_for_status()
            text = r.json().get('text', '').strip()
        except Exception as e:
            logger.error(f"[qwen3_llama_server] 转写请求失败: {e}")
            text = ''

        dt = time.time() - t0
        dur = len(stream.audio_data) / stream.sample_rate
        logger.info(f"[qwen3_llama_server] 转写完成: {dur:.1f}s 音频, "
                    f"耗时 {dt:.1f}s (RTF={dt/dur:.2f}): {text}")
        stream.result.text = text
        stream.result.duration = dur

    def update_hotwords(self, hotwords: List[str]):
        pass  # llama-server 暂不支持动态热词

    def cleanup(self):
        pass  # 无本地资源，llama-server 独立进程管理
