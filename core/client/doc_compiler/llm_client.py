# coding: utf-8
"""
本地大模型客户端（v5：内容整理与总结）

极简 OpenAI-compatible 聊天客户端，只依赖标准库 urllib。
默认连接本机 llama-server（端口 8081）。

v5.1+：summarize.summarize_day 默认按需启停 LLM 服务（llm_server.py），
27B 模型不再需要常驻内存；服务已在跑时直接用。

服务端不可达时抛 LLMUnavailable，调用方捕获后静默跳过（不崩）。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request


class LLMUnavailable(Exception):
    """LLM 服务不可达（没启动、端口不对、超时、响应异常）"""
    pass


def chat(messages: list[dict],
         base_url: str = "http://127.0.0.1:8081",
         model: str = "qwen",
         timeout: int = 600,
         temperature: float = 0.3,
         max_tokens: int = 4096) -> str:
    """
    调 OpenAI-compatible /v1/chat/completions，返回助手回复文本。

    Args:
        messages: [{"role": "system"/"user", "content": "..."}]
        base_url: llama-server 地址，默认 http://127.0.0.1:8081
        model: 模型别名（start_llm_server.bat 用 --alias qwen 启动）
        timeout: 单次请求超时秒数（总结任务允许较长等待）

    Raises:
        LLMUnavailable: 服务端不可达或响应异常
    """
    url = base_url.rstrip("/") + "/v1/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise LLMUnavailable(f"LLM server unreachable at {base_url}: {e}") from e
    except (json.JSONDecodeError, ValueError) as e:
        raise LLMUnavailable(f"Bad JSON from LLM server: {e}") from e

    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise LLMUnavailable(f"Unexpected LLM response shape: {e}") from e
