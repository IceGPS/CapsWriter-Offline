# coding: utf-8
from __future__ import annotations
import os
from typing import TYPE_CHECKING
from config_server import ServerConfig as Config
from ..state import console
from .. import logger # Server module logger
if TYPE_CHECKING:
    from ..app import CapsWriterServer


class TrayManager:
    """
    托盘管理器：负责系统托盘图标的初始化、菜单构建及回调处理。
    """
    def __init__(self, app: CapsWriterServer):
        self.app = app

    def start(self):
        """初始化系统托盘图标"""
        if not Config.enable_tray:
            return

        try:
            from . import enable_min_to_tray
        except ImportError as e:
            logger.warning(f"托盘模块导入失败，跳过托盘功能: {e}")
            return

        # 获取图标路径
        icon_path = os.path.join(self.app.base_dir, 'assets', 'icon.ico')
        
        # 启用托盘
        enable_min_to_tray(
            'CapsWriter Server',
            icon_path,
            exit_callback=self._request_exit,
            more_options=[
                # (名称, 回调, checked_fn)：可勾选的剪贴板共享总开关（默认关）
                ('📋 剪贴板共享', self._toggle_clipsync,
                 lambda item: self.app.clipsync.is_enabled()),
            ],
            menu_provider=self._clipsync_menu,
        )
        logger.info("托盘图标已启用")

    def _toggle_clipsync(self, icon=None, item=None):
        """托盘菜单：切换剪贴板共享开关"""
        clipsync = self.app.clipsync
        clipsync.set_enabled(not clipsync.is_enabled())

    def _clipsync_menu(self):
        """动态菜单：已配对设备子菜单（每设备一个取消配对项）。"""
        try:
            from pystray import Menu, MenuItem as item
        except ImportError:
            return []
        devices = self.app.clipsync.paired_devices()
        if not devices:
            sub = Menu(item('（无已配对设备）', None, enabled=False))
        else:
            sub = Menu(*[
                item(f'❌ {name}', self._make_unpair_cb(device_id))
                for device_id, name in devices
            ])
        return [item('📱 已配对设备', sub)]

    def _make_unpair_cb(self, device_id: str):
        def _cb(icon=None, item=None):
            self.app.clipsync.unpair_device(device_id)
        return _cb

    def _request_exit(self, icon=None, item=None):
        """托盘图标引用的退出回调"""
        logger.info("托盘退出: 用户点击退出菜单，准备清理资源并退出")
        self.app.stop()

    def stop(self):
        """停止托盘图标"""
        if not Config.enable_tray:
            return
            
        try:
            from core.ui.tray import stop_tray
            stop_tray()
            logger.info("TrayManager: 托盘图标已卸载")
        except Exception as e:
            logger.debug(f"TrayManager: 卸载托盘时发生错误: {e}")
