# coding: utf-8
"""
剪贴板局域网同步主服务（Windows 服务端）。

协议依据：设计文档第 7 节（Android ↔ Windows 同步协议 v1）。
本模块只实现 Windows 侧；与 Android 的互操作完全由第 7 节定义，
线格式细节见 crypto.py。

行为摘要：
- 配对：手机发 pair_req → PC 弹窗确认 → 同意则持久化 {id, name, key} 并回 pair_ok。
- 手机→PC：收到 clip → AES-GCM 解密 → 写入 Windows 剪贴板（自我抑制防回环）。
- PC→手机：本机复制 → 对每个已配对手机用各自密钥加密广播 clip。
- 防回环：只广播本机用户主动复制的内容；收到的 clip 永不转发（7.3/7.4）。
- 任一端可发 unpair 取消配对。

线程模型：
- WebSocket 消息处理在 asyncio 事件循环线程。
- 剪贴板监听在独立 watcher 线程，通过 run_coroutine_threadsafe 把文本
  抛回事件循环。
- 配对弹窗通过 run_in_executor 在线程池展示，不阻塞事件循环。
- 托盘菜单回调在 pystray 线程，只做轻量状态变更 + run_coroutine_threadsafe。
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import socket
import sys
import time

from config_server import ServerConfig as Config

from . import crypto
from .store import ClipSyncStore
from .watcher import ClipboardWatcher

logger = logging.getLogger(__name__)

# 自我抑制窗口（秒）：我们自己写入剪贴板后，监听到相同文本的变化直接跳过（7.4）
_SUPPRESS_WINDOW = 5.0


class ClipSyncService:
    """剪贴板同步服务。由 CapsWriterServer 持有，随服务端启停。"""

    def __init__(self, app):
        self.app = app
        self._loop: asyncio.AbstractEventLoop | None = None
        self._store = ClipSyncStore(app.base_dir / "clipsync.json")
        self._watcher: ClipboardWatcher | None = None
        # device_id -> websocket（在线绑定；重连时按最新 clip/pair_req 刷新）
        self._device_sockets: dict = {}
        # socket_id(str) -> device_id（反向索引，用于 unpair 定位发送方）
        self._socket_devices: dict = {}
        # 自我抑制：最近一次由本服务写入剪贴板的 (文本, monotonic时间)
        self._self_write_text: str | None = None
        self._self_write_time: float = 0.0
        # 去重：最近一次向外广播的文本
        self._last_broadcast_text: str | None = None

    # ---------- 生命周期 ----------

    @property
    def device_id(self) -> str:
        return self._store.device_id

    def is_enabled(self) -> bool:
        return self._store.enabled and crypto.available()

    def start(self) -> None:
        """服务端启动时调用。"""
        self._loop = self.app.loop
        if not crypto.available():
            logger.warning("[剪贴板同步] pycryptodome 未安装，剪贴板同步功能已禁用 "
                           "（pip install pycryptodome 后重启生效）")
            return
        if self._store.enabled:
            self._start_watcher()
        logger.info(f"[剪贴板同步] 就绪 device_id={self.device_id[:8]}… "
                    f"enabled={self._store.enabled} paired={len(self._store.paired)}")

    def stop(self) -> None:
        """服务端退出时调用。"""
        self._stop_watcher()

    def get_mdns_props(self) -> dict:
        """供 mdns_publisher 的 TXT 记录（7.1）。未启用或无加密能力时返回空。"""
        if self.is_enabled():
            return {"clip": "1", "id": self.device_id, "name": socket.gethostname()}
        return {}

    def set_enabled(self, enabled: bool) -> bool:
        """托盘开关。返回最终是否处于启用状态。"""
        if enabled and not crypto.available():
            logger.warning("[剪贴板同步] pycryptodome 未安装，无法启用（pip install pycryptodome）")
            return False
        self._store.set_enabled(enabled)
        if enabled:
            self._start_watcher()
        else:
            self._stop_watcher()
        self._republish_mdns()
        self._refresh_tray()
        logger.info(f"[剪贴板同步] 开关 -> {'开' if enabled else '关'}")
        return enabled

    # ---------- WebSocket 消息入口（ws_recv 分流调用） ----------

    async def handle_ws_message(self, websocket, data: dict) -> None:
        msg_type = data.get("type")
        try:
            if msg_type == "pair_req":
                await self._on_pair_req(websocket, data)
            elif msg_type == "clip":
                await self._on_clip(websocket, data)
            elif msg_type == "unpair":
                await self._on_unpair(websocket, data)
            else:
                logger.warning(f"[剪贴板同步] 未知消息类型: {msg_type}")
        except Exception:
            logger.warning("[剪贴板同步] 消息处理异常", exc_info=True)

    def on_socket_closed(self, socket_id: str) -> None:
        """ws_recv 连接清理时调用，清除该连接的设备绑定。"""
        dev_id = self._socket_devices.pop(socket_id, None)
        if dev_id is not None:
            ws = self._device_sockets.get(dev_id)
            if ws is not None and str(getattr(ws, "id", "")) == socket_id:
                del self._device_sockets[dev_id]

    # ---------- 配对（7.2） ----------

    async def _on_pair_req(self, websocket, data: dict) -> None:
        if not self.is_enabled():
            await self._send_json(websocket, {"type": "pair_reject"})
            logger.info("[剪贴板同步] 收到 pair_req，但功能未启用，已拒绝")
            return
        dev_id = data.get("id")
        name = str(data.get("name") or "未知手机")
        key_b64 = data.get("key")
        try:
            key = base64.b64decode(key_b64)
            if len(key) != crypto.KEY_LEN:
                raise ValueError("bad key length")
        except Exception:
            await self._send_json(websocket, {"type": "pair_reject"})
            logger.warning("[剪贴板同步] pair_req 密钥非法，已拒绝")
            return
        if not dev_id:
            await self._send_json(websocket, {"type": "pair_reject"})
            return

        if dev_id in self._store.paired:
            # 幂等：重复配对直接成功（刷新密钥与绑定）
            self._store.add_paired(dev_id, name, key_b64)
            self._bind(websocket, dev_id)
            await self._send_json(websocket, {"type": "pair_ok"})
            logger.info(f"[剪贴板同步] 重复配对直接通过: {name}")
            return

        # 弹窗请用户确认（线程池，不阻塞事件循环）
        loop = asyncio.get_running_loop()
        agree = await loop.run_in_executor(None, self._ask_user, name)
        if agree:
            self._store.add_paired(dev_id, name, key_b64)
            self._bind(websocket, dev_id)
            await self._send_json(websocket, {"type": "pair_ok"})
            logger.info(f"[剪贴板同步] 配对成功: {name} ({str(dev_id)[:8]}…)")
        else:
            await self._send_json(websocket, {"type": "pair_reject"})
            logger.info(f"[剪贴板同步] 用户拒绝配对: {name}")
        self._refresh_tray()

    def _ask_user(self, name: str) -> bool:
        """配对确认弹窗。返回 True=同意。"""
        if sys.platform != "win32":
            logger.warning("[剪贴板同步] 非 Windows，自动拒绝配对")
            return False
        try:
            import ctypes
            MB_YESNO = 0x04
            MB_ICONQUESTION = 0x20
            MB_TOPMOST = 0x00040000
            IDYES = 6
            rc = ctypes.windll.user32.MessageBoxW(
                None,
                f"手机「{name}」请求配对剪贴板共享。\n\n"
                "同意后：\n"
                "• 该手机复制的内容会自动同步到这台电脑的剪贴板；\n"
                "• 这台电脑复制的内容会推送到该手机的输入法；\n"
                "• 传输经 AES 加密，仅限局域网。",
                "留声输入法 · 剪贴板共享",
                MB_YESNO | MB_ICONQUESTION | MB_TOPMOST,
            )
            return rc == IDYES
        except Exception as e:
            logger.warning(f"[剪贴板同步] 配对弹窗失败，视为拒绝: {e}")
            return False

    async def _on_unpair(self, websocket, data: dict) -> None:
        dev_id = self._socket_devices.get(str(getattr(websocket, "id", "")))
        if not dev_id:
            return
        info = self._store.remove_paired(dev_id)
        if info is not None:
            self._device_sockets.pop(dev_id, None)
            logger.info(f"[剪贴板同步] 对方取消配对: {info.get('name')}")
            self._refresh_tray()

    def unpair_device(self, device_id: str) -> None:
        """本端（托盘）主动取消配对，并通知对方。"""
        info = self._store.remove_paired(device_id)
        if info is None:
            return
        ws = self._device_sockets.pop(device_id, None)
        for sid, did in list(self._socket_devices.items()):
            if did == device_id:
                del self._socket_devices[sid]
        if ws is not None and self._loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._send_json(ws, {"type": "unpair"}), self._loop
                )
            except RuntimeError:
                pass  # 事件循环已关闭，配对信息已持久化删除即可
            except Exception:
                logger.warning("[剪贴板同步] 发送 unpair 失败", exc_info=True)
        logger.info(f"[剪贴板同步] 已取消配对: {info.get('name')}")
        self._refresh_tray()

    def paired_devices(self) -> list[tuple[str, str]]:
        """[(device_id, name), ...]，供托盘菜单展示。"""
        return self._store.list_paired()

    # ---------- 剪贴板同步消息（7.3） ----------

    async def _on_clip(self, websocket, data: dict) -> None:
        dev_id = data.get("from")
        info = self._store.get_paired(dev_id) if dev_id else None
        if info is None:
            logger.warning("[剪贴板同步] 收到未配对设备的 clip，已忽略")
            return
        # 绑定在线连接（天然处理手机重连，7.5）
        self._bind(websocket, dev_id)
        try:
            blob = base64.b64decode(data["data"])
            key = base64.b64decode(info["key"])
            text = crypto.decrypt(key, blob).decode("utf-8")
        except Exception as e:
            logger.warning(f"[剪贴板同步] clip 解密失败（{info.get('name')}），已丢弃: {e}")
            return
        if not text:
            return
        # 自我抑制：先记录，再写入（7.4）
        self._self_write_text = text
        self._self_write_time = time.monotonic()
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._write_clipboard_text, text)
        except Exception as e:
            logger.warning(f"[剪贴板同步] 写入 Windows 剪贴板失败: {e}")
            return
        logger.info(f"[剪贴板同步] 已写入剪贴板（来自 {info.get('name')}，{len(text)}字）")
        # 注意：收到的 clip 永不转发（7.3）

    async def _on_local_clipboard(self, text: str) -> None:
        """
        本机剪贴板变化（watcher 线程经 run_coroutine_threadsafe 投递）。
        只广播本机用户主动复制的内容（7.3）。
        """
        if not self.is_enabled():
            return
        if not text:
            return
        now = time.monotonic()
        # 自我抑制：这是我们自己刚才写入的（手机推过来的），跳过（7.4）
        if text == self._self_write_text and (now - self._self_write_time) < _SUPPRESS_WINDOW:
            return
        # 去重：和上次广播完全相同，不重复推送
        if text == self._last_broadcast_text:
            return
        if not self._store.paired:
            return
        plaintext = text.encode("utf-8")
        ts = int(time.time() * 1000)
        sent_any = False
        for dev_id, info in list(self._store.paired.items()):
            ws = self._device_sockets.get(dev_id)
            if ws is None:
                continue  # 对方不在线：best-effort，不做离线队列（4.7）
            try:
                key = base64.b64decode(info["key"])
                blob = crypto.encrypt(key, plaintext)
                payload = {
                    "type": "clip",
                    "from": self.device_id,
                    "ts": ts,
                    "data": base64.b64encode(blob).decode("ascii"),
                }
                await ws.send(json.dumps(payload, ensure_ascii=False))
                sent_any = True
            except Exception as e:
                logger.warning(f"[剪贴板同步] 推送到 {info.get('name')} 失败: {e}")
                self._device_sockets.pop(dev_id, None)
        if sent_any:
            self._last_broadcast_text = text
            logger.info(f"[剪贴板同步] 已广播剪贴板（{len(text)}字）")

    # ---------- 内部工具 ----------

    def _bind(self, websocket, device_id: str) -> None:
        sid = str(getattr(websocket, "id", ""))
        self._device_sockets[device_id] = websocket
        if sid:
            self._socket_devices[sid] = device_id

    async def _send_json(self, websocket, payload: dict) -> None:
        try:
            await websocket.send(json.dumps(payload, ensure_ascii=False))
        except Exception as e:
            logger.warning(f"[剪贴板同步] 发送失败: {e}")

    @staticmethod
    def _write_clipboard_text(text: str) -> None:
        try:
            import pyclip
        except ImportError as e:
            raise RuntimeError("pyclip 未安装（pip install pyclip）") from e
        pyclip.copy(text)

    def _watcher_callback(self, text: str) -> None:
        """watcher 线程调用：把文本抛回事件循环。"""
        if self._loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._on_local_clipboard(text), self._loop)
        except RuntimeError:
            pass  # 循环未运行/已关闭
        except Exception:
            logger.warning("[剪贴板同步] 提交广播任务失败", exc_info=True)

    def _start_watcher(self) -> None:
        if self._watcher is not None:
            return
        watcher = ClipboardWatcher(self._watcher_callback)
        if watcher.start_listening():
            self._watcher = watcher
        # start_listening 失败时内部已记日志；watcher 线程自行退出，无需保留引用

    def _stop_watcher(self) -> None:
        watcher, self._watcher = self._watcher, None
        if watcher is not None:
            try:
                watcher.stop_listening()
            except Exception:
                logger.warning("[剪贴板同步] 停止监听异常", exc_info=True)

    def _republish_mdns(self) -> None:
        """开关变化后重发布 mDNS（更新 clip TXT）。"""
        try:
            from ..mdns_publisher import stop_mdns, start_mdns
            stop_mdns()
            start_mdns(int(Config.port), extra_props=self.get_mdns_props())
        except Exception as e:
            logger.warning(f"[剪贴板同步] mDNS 重发布失败: {e}")

    def _refresh_tray(self) -> None:
        try:
            from core.ui.tray import refresh_tray_menu
            refresh_tray_menu()
        except Exception:
            pass
