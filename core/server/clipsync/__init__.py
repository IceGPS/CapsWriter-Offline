# coding: utf-8
"""
剪贴板局域网同步（Windows 服务端）

设计依据：~/workspace/goals/voice-input-method-project/files/clipboard-history-design.md
第 4.5 / 4.7 / 7 节（Android ↔ Windows 同步协议 v1）。

模块划分：
- crypto:  AES-GCM 加解密（协议 7.3 线格式的唯一实现）
- store:   配对信息 + 本机设备 UUID + 开关的 JSON 持久化
- watcher: Windows 剪贴板监听线程（AddClipboardFormatListener）
- service: ClipSyncService —— 配对、消息收发、广播、自我抑制的主逻辑
"""
from .service import ClipSyncService

__all__ = ['ClipSyncService']
