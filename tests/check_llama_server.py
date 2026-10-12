# coding: utf-8
"""
qwen3_llama_server 引擎测试（voice-ime 第一阶段）

前置：llama-server 已启动
    llama-server -m models/Qwen3-ASR-1.7B-HF/Qwen3-ASR-1.7B-Q8_0.gguf \
                 --mmproj models/Qwen3-ASR-1.7B-HF/mmproj.gguf \
                 --port 8080

用法：
    python tests/check_llama_server.py <wav文件> [wav文件...]
"""
import os
import sys
import time

REPO_ROOT = '/home/hatch/workspace/voice-ime-dev'
os.chdir(REPO_ROOT)
sys.path.insert(0, REPO_ROOT)

import numpy as np
import soundfile as sf


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    from config_server import Qwen3LlamaServerArgs
    from core.server.engines.qwen3_llama_server.asr_engine import (
        Qwen3LlamaServerEngine, Qwen3LlamaServerConfig,
    )

    args = {k: v for k, v in Qwen3LlamaServerArgs.__dict__.items()
            if not k.startswith('_')}
    config = Qwen3LlamaServerConfig(**args)
    engine = Qwen3LlamaServerEngine(config)

    for wav_path in sys.argv[1:]:
        data, sr = sf.read(wav_path, dtype='float32')
        if data.ndim > 1:
            data = data.mean(axis=1)
        assert sr == 16000, f"采样率须为 16000: {wav_path}"
        dur = len(data) / 16000

        stream = engine.create_stream()
        stream.accept_waveform(16000, data)
        t0 = time.time()
        engine.decode_stream(stream, language='Chinese')
        dt = time.time() - t0
        print(f"[测试] {wav_path}: {dur:.1f}s 音频, 耗时 {dt:.1f}s "
              f"(RTF={dt/dur:.2f})")
        print(f"[测试] 转写: {stream.result.text}")

    engine.cleanup()


if __name__ == '__main__':
    main()
