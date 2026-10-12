# coding: utf-8
"""
端到端测试：启动 CapsWriter 服务端（无头模式），用 websocket 客户端发送
测试音频，验证完整识别链路。

用法：
    python tests/check_e2e.py <wav文件> [wav文件...]

流程：
    1. 禁用托盘，启动 CapsWriterServer（主进程 + 识别子进程）
    2. websocket 连接 ws://127.0.0.1:6016（子协议 binary）
    3. 发送 AudioMessage（base64 float32 音频，is_final=True）
    4. 等待 RecognitionMessage(is_final=True)，打印文本与耗时
    5. 关闭服务端
"""
import os
import sys
import time
import json
import base64
import asyncio
import uuid

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(REPO_ROOT)
sys.path.insert(0, REPO_ROOT)

import numpy as np
import soundfile as sf


def load_audio_f32(path: str) -> np.ndarray:
    data, sr = sf.read(path, dtype='float32')
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != 16000:
        raise ValueError(f"采样率须为 16000，当前 {sr}")
    return data.astype(np.float32)


async def transcribe_one(wav_path: str, timeout: float = 300.0) -> dict:
    import websockets
    from core.protocol import AudioMessage, RecognitionMessage

    audio = load_audio_f32(wav_path)
    dur = len(audio) / 16000
    task_id = uuid.uuid4().hex

    msg = AudioMessage(
        task_id=task_id,
        source='mic',
        data=base64.b64encode(audio.tobytes()).decode('ascii'),
        is_final=True,
        time_start=time.time(),
        seg_duration=60.0,
        seg_overlap=4.0,
        context='',
        language='auto',
    )

    t0 = time.time()
    async with websockets.connect(
        'ws://127.0.0.1:6016', subprotocols=['binary'],
        max_size=None, proxy=None,
    ) as ws:
        await ws.send(msg.to_json())
        final = None
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            res = RecognitionMessage.from_dict(json.loads(raw))
            if res.task_id != task_id:
                continue
            if res.is_final:
                final = res
                break
    dt = time.time() - t0
    return {
        'wav': wav_path, 'duration': dur, 'elapsed': dt,
        'rtf': dt / dur if dur else 0,
        'text': final.text if final else '',
        'text_accu': final.text_accu if final else '',
    }


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    # 无头模式：禁用托盘
    import config_server
    config_server.ServerConfig.enable_tray = False
    config_server.ServerConfig.addr = '127.0.0.1'

    from multiprocessing import Process

    def run_server():
        from core.server.app import CapsWriterServer
        CapsWriterServer().start()

    server_proc = Process(target=run_server, daemon=True)
    server_proc.start()
    print("[e2e] 服务端进程已启动，等待就绪...", flush=True)

    # 等待端口就绪（最多 300s，模型加载较慢）
    import socket
    ready = False
    for _ in range(300):
        try:
            with socket.create_connection(('127.0.0.1', 6016), timeout=1):
                ready = True
                break
        except OSError:
            time.sleep(1)
    if not ready:
        print("[e2e] 服务端 300s 未就绪，终止")
        server_proc.terminate()
        sys.exit(1)
    print("[e2e] 服务端就绪", flush=True)
    # 等子进程模型加载完成
    time.sleep(5)

    results = []
    try:
        for wav in sys.argv[1:]:
            print(f"\n[e2e] 测试 {wav} ...", flush=True)
            r = asyncio.run(transcribe_one(wav))
            print(f"[e2e] 耗时 {r['elapsed']:.1f}s（音频 {r['duration']:.1f}s，RTF={r['rtf']:.2f}）")
            print(f"[e2e] 转写：{r['text']}")
            results.append(r)
    finally:
        server_proc.terminate()
        server_proc.join(timeout=10)
        print("\n[e2e] 服务端已关闭")

    print("\n===== 端到端汇总 =====")
    for r in results:
        print(f"{os.path.basename(r['wav'])} ({r['duration']:.1f}s, RTF={r['rtf']:.2f}): {r['text']}")


if __name__ == '__main__':
    main()
