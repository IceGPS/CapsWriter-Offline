# coding: utf-8
"""
Windows 剪贴板监听线程。

实现方式：创建 message-only 隐藏窗口，AddClipboardFormatListener 注册，
独立线程跑 GetMessage 消息泵。剪贴板变化（WM_CLIPBOARDUPDATE）时读取
文本并回调。

线程模型：
- 所有 Win32 调用都在 watcher 线程内完成（创建窗口、消息泵、注销）。
- 回调 on_text(text) 在 watcher 线程被调用；调用方负责把工作抛回主循环
  （ClipSyncService 用 asyncio.run_coroutine_threadsafe）。
- stop() 可在任意线程调用：向 watcher 线程投递 WM_QUIT，清理工作
  （RemoveClipboardFormatListener / DestroyWindow）在 watcher 线程内收尾。

非 Windows 平台：start() 直接返回 False，不抛异常（便于开发机导入与单测）。
"""
from __future__ import annotations

import logging
import sys
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

WM_CLIPBOARDUPDATE = 0x031D
WM_QUIT = 0x0012
HWND_MESSAGE = -3


def _pyclip():
    """延迟导入 pyclip（Windows 剪贴板读写）；缺失时给出明确错误。"""
    try:
        import pyclip
        return pyclip
    except ImportError as e:
        raise RuntimeError("pyclip 未安装，剪贴板读写不可用（pip install pyclip）") from e


class ClipboardWatcher(threading.Thread):
    """
    剪贴板监听线程。

    Args:
        on_text: 回调 text -> None，在 watcher 线程内调用，必须线程安全。
    """

    def __init__(self, on_text: Callable[[str], None]):
        super().__init__(name="ClipSyncWatcher", daemon=True)
        self._on_text = on_text
        self._tid: Optional[int] = None      # watcher 线程的 Win32 线程 ID
        self._running = False

    # ---------- 对外接口 ----------

    def start_listening(self) -> bool:
        """启动监听线程并等待窗口就绪；返回是否成功进入监听状态。"""
        if sys.platform != "win32":
            logger.warning("[剪贴板同步] 非 Windows 平台，跳过剪贴板监听")
            return False
        if self._running:
            return True
        self._ready = threading.Event()
        self._failed = None
        super().start()
        self._ready.wait(timeout=10)
        if self._failed:
            logger.warning(f"[剪贴板同步] 剪贴板监听启动失败: {self._failed}")
            return False
        self._running = True
        return True

    def stop_listening(self) -> None:
        """停止监听（任意线程可调用，幂等）。"""
        if not self._running:
            return
        self._running = False
        try:
            import ctypes
            if self._tid:
                ctypes.windll.user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        except Exception:
            pass
        self.join(timeout=5)

    # ---------- 线程主体（仅 Windows） ----------

    def run(self) -> None:
        try:
            self._run_message_loop()
        except Exception as e:
            self._failed = e
            self._ready.set()
            logger.warning(f"[剪贴板同步] 剪贴板监听线程异常退出: {e}", exc_info=True)

    def _run_message_loop(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        # ---- 函数签名 ----
        WNDPROC_T = ctypes.WINFUNCTYPE(
            wintypes.LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        )

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC_T),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HCURSOR),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        user32.RegisterClassW.restype = wintypes.ATOM
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
        ]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.AddClipboardFormatListener.argtypes = [wintypes.HWND]
        user32.AddClipboardFormatListener.restype = wintypes.BOOL
        user32.RemoveClipboardFormatListener.argtypes = [wintypes.HWND]
        user32.RemoveClipboardFormatListener.restype = wintypes.BOOL
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.DestroyWindow.restype = wintypes.BOOL
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = wintypes.LRESULT
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        user32.GetMessageW.restype = ctypes.c_int
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]

        # ---- 窗口过程 ----
        def wndproc(hwnd, msg, wparam, lparam):
            if msg == WM_CLIPBOARDUPDATE:
                self._handle_clipboard_update()
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wndproc_ref = WNDPROC_T(wndproc)  # 持有引用，防止 GC 回收

        # ---- 注册窗口类 ----
        hinst = kernel32.GetModuleHandleW(None)
        class_name = "LiushengClipSyncWatcher"
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._wndproc_ref
        wc.hInstance = hinst
        wc.lpszClassName = class_name
        atom = user32.RegisterClassW(ctypes.byref(wc))
        if not atom:
            err = kernel32.GetLastError()
            if err != 1410:  # 1410 = 类已存在，可继续用
                raise RuntimeError(f"RegisterClassW 失败，错误码 {err}")

        # ---- 创建 message-only 窗口并注册监听 ----
        hwnd = user32.CreateWindowExW(
            0, class_name, None, 0, 0, 0, 0, 0,
            HWND_MESSAGE, None, hinst, None,
        )
        if not hwnd:
            raise RuntimeError(f"CreateWindowExW 失败，错误码 {kernel32.GetLastError()}")
        if not user32.AddClipboardFormatListener(hwnd):
            user32.DestroyWindow(hwnd)
            raise RuntimeError(f"AddClipboardFormatListener 失败，错误码 {kernel32.GetLastError()}")

        self._tid = kernel32.GetCurrentThreadId()
        self._ready.set()
        logger.info("[剪贴板同步] Windows 剪贴板监听已启动")

        # ---- 消息泵 ----
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        # ---- 收尾（仍在 watcher 线程） ----
        try:
            user32.RemoveClipboardFormatListener(hwnd)
        except Exception:
            pass
        try:
            user32.DestroyWindow(hwnd)
        except Exception:
            pass
        logger.info("[剪贴板同步] Windows 剪贴板监听已停止")

    def _handle_clipboard_update(self) -> None:
        """WM_CLIPBOARDUPDATE 处理：读文本，调回调。异常一律吞掉，只记日志。"""
        try:
            data = _pyclip().paste()
        except Exception as e:
            logger.debug(f"[剪贴板同步] 读取剪贴板失败（可能非文本内容）: {e}")
            return
        try:
            text = data.decode("utf-8") if isinstance(data, bytes) else str(data)
        except Exception as e:
            logger.debug(f"[剪贴板同步] 剪贴板解码失败: {e}")
            return
        if not text:
            return
        try:
            self._on_text(text)
        except Exception:
            logger.warning("[剪贴板同步] on_text 回调异常", exc_info=True)
