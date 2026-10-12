# coding: utf-8
"""
Qwen3-ASR 引擎独立测试脚本（voice-ime 第一阶段）

用法：
    python tests/test_qwen_asr_engine.py <wav文件> [wav文件...]

要求：
    - 16kHz 单声道 WAV（可用 ffmpeg 转换：ffmpeg -i in.mp3 -ar 16000 -ac 1 out.wav）
    - 模型文件位于 models/Qwen3-ASR/Qwen3-ASR-1.7B/（见 config_server.py）
    - llama.cpp 动态库位于 core/server/engines/qwen_asr_gguf/inference/bin/
      （Linux: libllama.so + libggml.so + libggml-base.so；
       Windows: llama.dll + ggml.dll + ggml-base.dll）
"""
import os
import sys
import time

# 确保从仓库根目录运行
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(REPO_ROOT)
sys.path.insert(0, REPO_ROOT)

import numpy as np
import soundfile as sf


def load_audio_16k(path: str) -> np.ndarray:
    data, sr = sf.read(path, dtype='float32')
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != 16000:
        raise ValueError(f"采样率须为 16000，当前 {sr}：{path}")
    return data


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    from config_server import Qwen3ASRGGUFArgs
    from core.server.engines.qwen_asr_gguf.inference.schema import ASREngineConfig
    from core.server.engines.qwen_asr_gguf.inference.asr import QwenASREngine

    # 注意：llama 模块初始化时会 chdir 到 bin/ 目录，这里切回仓库根目录
    os.chdir(REPO_ROOT)

    args = {k: v for k, v in Qwen3ASRGGUFArgs.__dict__.items() if not k.startswith('_')}
    # Linux 测试环境：强制 CPU（无 GPU）
    args['onnx_provider'] = 'CPU'
    args['llm_use_gpu'] = False
    config = ASREngineConfig(**args)

    print(f"[测试] 模型目录：{config.model_dir}")
    for fn in (config.encoder_frontend_fn, config.encoder_backend_fn, config.llm_fn):
        p = os.path.join(config.model_dir, fn)
        print(f"[测试] {'OK ' if os.path.exists(p) else '缺失'} {p}")

    t0 = time.time()
    engine = QwenASREngine(config)
    print(f"[测试] 引擎加载耗时：{time.time() - t0:.1f}s")

    try:
        from core.server.engines.qwen_asr_gguf.asr_engine import QwenASRStream
        use_stream = True
    except ImportError:
        use_stream = False

    results = []
    for wav_path in sys.argv[1:]:
        audio = load_audio_16k(wav_path)
        dur = len(audio) / 16000
        print(f"\n[测试] {wav_path}：{dur:.1f}s")

        t0 = time.time()
        if use_stream:
            from core.server.engines.qwen_asr_gguf.asr_engine import QwenASREngine as AdapterEngine
            adapter = AdapterEngine(config)
            stream = adapter.create_stream()
            stream.accept_waveform(16000, audio)
            adapter.decode_stream(stream, language='Chinese')
            text = stream.result.text
            adapter.cleanup()
        else:
            audio_embd, _ = engine.encoder.encode(audio)
            full_embd = engine._build_prompt_embd(audio_embd, "", None, "Chinese")
            res = engine._safe_decode(full_embd, "", rollback_num=5,
                                      is_last_chunk=True, temperature=0.4, streaming=False)
            text = res.text
        dt = time.time() - t0
        print(f"[测试] 耗时 {dt:.2f}s，RTF={dt / dur:.3f}")
        print(f"[测试] 转写：{text}")
        results.append((wav_path, dur, dt, text))

    engine.shutdown()

    print("\n===== 汇总 =====")
    for wav_path, dur, dt, text in results:
        print(f"{os.path.basename(wav_path)} ({dur:.1f}s, RTF={dt/dur:.2f}): {text}")


if __name__ == '__main__':
    main()
