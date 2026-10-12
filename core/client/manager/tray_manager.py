# coding: utf-8
import os
from . import logger
import os, sys, subprocess, time
from config_client import ClientConfig as Config


class TrayManager:
    """
    托盘管理器：负责系统托盘图标的初始化、菜单构建及回调处理。
    """
    def __init__(self, app):
        self.app = app

    @property
    def state(self):
        return self.app.state

    def start(self):
        """初始化系统托盘图标"""
        if not Config.enable_tray:
            return

        try:
            from ..ui import enable_min_to_tray
        except ImportError as e:
            logger.warning(f"托盘模块导入失败，跳过托盘功能: {e}")
            return

        # 获取图标路径
        icon_path = os.path.join(self.app.base_dir, 'assets', 'icon.ico')
        
        # 启用托盘
        enable_min_to_tray(
            'voice-ime',
            icon_path,
            exit_callback=self.app.stop,
            more_options=[
                ('📋 复制结果', self._copy_last_result),
                ('📁 每日文档', self._open_diary),
                ('📄 生成今日文档', self._compile_today),
                ('📝 生成内容总结', self._summarize_today),
                ('📝 上下文', self._add_context),
                ('✨ 热词', self._add_hotword),
                ('🧹 清除记忆', self._clear_memory),
                ('♻️ 重开音频', self._restart_audio),
            ]
        )
        logger.info("托盘图标已启用")

    def stop(self):
        """停止托盘图标"""
        if not Config.enable_tray:
            return
            
        try:
            from ..ui import stop_tray
            stop_tray()
            logger.info("TrayManager: 托盘图标已卸载")
        except Exception as e:
            logger.debug(f"TrayManager: 卸载托盘时发生错误: {e}")

    def _restart_audio(self):
        """重启音频流回调"""
        if hasattr(self.app, 'stream') and self.app.stream:
            self.app.stream.reopen()
            logger.info("用户请求重启音频")

    def _clear_memory(self):
        """清除 LLM 对话历史回调"""
        from ..ui import toast
        if self.app.llm:
            self.app.llm.clear_history()
            toast("清除成功：已清除所有角色的对话历史记录", duration=3000, bg="#075077")

    def _open_diary(self):
        """用系统默认方式打开当月每日文档文件夹回调（v5.4+：原版 diary 已停用）"""
        from datetime import datetime
        try:
            from ..doc_compiler.paths import daily_doc_path
        except ImportError:
            from doc_compiler.paths import daily_doc_path
        today = datetime.now().strftime('%Y-%m-%d')
        folder = daily_doc_path(self.app.base_dir, today).parent
        folder.mkdir(parents=True, exist_ok=True)
        if sys.platform == 'win32':
            os.startfile(folder)
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', folder])
        else:
            subprocess.Popen(['xdg-open', folder])

    def _compile_today(self):
        """编译今日文档回调（托盘菜单"📄 生成今日文档"）"""
        try:
            from datetime import datetime
            from ..doc_compiler.compile_day import compile_day
            today = datetime.now().strftime('%Y-%m-%d')
            out = compile_day(today, self.app.base_dir)
            if out:
                logger.info(f"今日文档已生成: {out}")
            else:
                logger.info("今日暂无转写日志，未生成文档")
        except Exception as e:
            logger.warning(f"生成今日文档失败: {e}")

    def _summarize_today(self):
        """内容总结回调（托盘菜单"📝 生成内容总结"，v5）"""
        try:
            from datetime import datetime
            from ..doc_compiler.summarize import summarize_day
            from ..doc_compiler.llm_client import LLMUnavailable
            today = datetime.now().strftime('%Y-%m-%d')
            out = summarize_day(today, self.app.base_dir)
            if out:
                logger.info(f"内容总结已生成: {out}")
            else:
                logger.info("今日暂无可总结的文档")
        except LLMUnavailable:
            logger.warning("LLM 服务启动失败：请检查 config_client.py 中的 llm_server_model 路径是否正确")
        except Exception as e:
            logger.warning(f"生成内容总结失败: {e}")

    def _add_hotword(self):
        """用系统默认方式打开热词文件回调"""
        
        target = os.path.abspath('hot.txt')
        if sys.platform == 'win32':
            os.startfile(target)
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', target])
        else:
            subprocess.Popen(['xdg-open', target])

    def _add_context(self):
        """打开编辑上下文界面回调"""
        try:
            from ..ui import on_edit_context
            on_edit_context()
        except ImportError as e:
            logger.warning(f"无法导入上下文菜单处理器: {e}")

    def _copy_last_result(self):
        """复制最后一次识别结果到剪贴板回调"""
        text = self.state.last_output_text
        if text:
            from ..llm.llm_clipboard import copy_to_clipboard
            copy_to_clipboard(text)

    def _request_exit(self, icon=None, item=None):
        """托盘图标引用的退出回调"""
        logger.info("托盘退出: 用户点击退出菜单，准备清理资源并退出")
        self.app.stop()
