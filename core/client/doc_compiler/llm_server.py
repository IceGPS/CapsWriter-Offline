# coding: utf-8
"""
本地 llama-server 按需启停（v5 内容总结用，端口 8081）

背景：内容总结是低频任务（每天一次），Qwen3.8-27B Q4_K_M 常驻要占 ~16GB
内存，不划算。改成用时加载、用完卸载。

ensure_llm_server() 上下文管理器：
  1. 先探活 /health：已有服务在跑 → 直接用，用完不关（可能是用户手动起的）。
  2. 没在跑 → 按配置起一个 llama-server 子进程（无窗口），等 /health 就绪
     （27B 模型加载约 1~2 分钟）。
  3. 退出时：如果是本管理器起的进程，终止并回收；不是则不动。

配置来源（优先级从高到低）：
  1. config_client.py 的 ClientConfig（llm_server_* 字段）
  2. 本文件 DEFAULTS（与 packaging/start_llm_server.bat 的参数保持一致）
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

try:
    # 包模式
    from .llm_client import LLMUnavailable
except ImportError:
    # 脚本模式：python core/client/doc_compiler/llm_server.py
    from llm_client import LLMUnavailable

DEFAULTS = {
    "exe": "llama-server/llama-server.exe",  # 相对程序根目录（base_dir）
    "model": r"C:\AI\models\gguf\lmstudio-community\Qwen3.8-27B-GGUF\Qwen3.8-27B-Q4_K_M.gguf",
    "alias": "qwen",
    "port": 8081,
    "ctx": 32768,
    "ngl": 99,
    "start_timeout": 600,  # 等模型加载的最长秒数
}

# ClientConfig 属性名 -> DEFAULTS 键名
_CFG_FIELDS = {
    "llm_server_exe": "exe",
    "llm_server_model": "model",
    "llm_server_alias": "alias",
    "llm_server_port": "port",
    "llm_server_ctx": "ctx",
    "llm_server_ngl": "ngl",
    "llm_server_start_timeout": "start_timeout",
}


def load_cfg() -> dict:
    """读配置：优先 config_client.py，读不到则用 DEFAULTS。"""
    cfg = dict(DEFAULTS)
    try:
        from config_client import ClientConfig
        for attr, key in _CFG_FIELDS.items():
            if hasattr(ClientConfig, attr):
                cfg[key] = getattr(ClientConfig, attr)
    except Exception:
        pass
    return cfg


def health_ok(base_url: str, timeout: int = 5) -> bool:
    """探活：/health 返回 200 且 status 为 ok（或无 status 字段）即就绪。"""
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/health",
                                    timeout=timeout) as r:
            if r.status != 200:
                return False
            try:
                body = json.loads(r.read().decode("utf-8"))
            except Exception:
                return True  # 200 即视为就绪（旧版本无 JSON）
            return body.get("status", "ok") == "ok"
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _launch(cfg: dict, base_dir: Path) -> "subprocess.Popen":
    exe = Path(cfg["exe"])
    if not exe.is_absolute():
        exe = base_dir / exe
    if not exe.exists():
        raise LLMUnavailable(f"llama-server not found: {exe}")
    model = str(cfg["model"])
    if not Path(model).exists():
        raise LLMUnavailable(f"LLM model not found: {model}\n"
                             f"请检查 config_client.py 中的 llm_server_model 路径")
    args = [str(exe), "-m", model, "--alias", str(cfg["alias"]),
            "--port", str(cfg["port"]), "-c", str(cfg["ctx"]),
            "-ngl", str(cfg["ngl"])]
    kwargs: dict = {"cwd": str(base_dir),
                    "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.Popen(args, **kwargs)


@contextmanager
def ensure_llm_server(base_url: str = "http://127.0.0.1:8081",
                      base_dir: Path | str | None = None):
    """
    确保 LLM 服务可用（上下文管理器，yield 出可用的 base_url）。

    - 服务已在跑：直接用，退出时不关闭。
    - 服务没在跑：按需启动一个子进程，等就绪后 yield；退出时终止该进程。
    """
    base = Path(base_dir).resolve() if base_dir else Path(__file__).resolve().parents[3]
    if health_ok(base_url):
        yield base_url
        return
    cfg = load_cfg()
    proc = _launch(cfg, base)
    try:
        deadline = time.time() + int(cfg["start_timeout"])
        while time.time() < deadline:
            if proc.poll() is not None:
                raise LLMUnavailable("llama-server 启动后异常退出，请检查模型路径与显存")
            if health_ok(base_url):
                break
            time.sleep(5)
        else:
            raise LLMUnavailable(
                f"llama-server 启动超时（>{cfg['start_timeout']}s），模型可能还在加载或卡住")
        yield base_url
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
