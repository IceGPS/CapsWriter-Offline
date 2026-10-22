# coding: utf-8
"""
CapsWriter Offline 客户端主程序门面类 (Facade)

采用外观模式统一管理音频流 (AudioStreamManager)、
识别结果处理 (ResultProcessor) 和快捷键管理 (ShortcutManager)。
"""

import os
import sys
import asyncio
from pathlib import Path

from .state import ClientState
from . import logger
from config_client import ClientConfig as Config, __version__
from core.tools.signal_handler import register_signal
from .state import console
from .connection import WebSocketManager
from typing import TYPE_CHECKING, Optional
from .manager import (
    TrayManager,
    MicRunner, FileRunner
)
from .audio.stream import AudioStreamManager
from .shortcut.shortcut_manager import ShortcutManager
from .shortcut.shortcut_config import Shortcut

from .udp.udp_control import UDPController

from .hotword.manager import HotwordManager
from .llm.llm_handler import LLMHandler
from .output.text_output import TextOutput
from .doc_compiler.log import DailyLogger
from core.tools.empty_working_set import empty_current_working_set
from platform import system



class CapsWriterClient:
    """
    CapsWriter 客户端门面类
    
    管理的外部接口简洁：start()。
    """
    def __init__(self):
        # 确保正确的工作目录
        self.base_dir = Path(__file__).parents[2]
        os.chdir(self.base_dir)
            
        # 初始化事件循环
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
            
        # 初始化状态容器
        self.state = ClientState(app=self)

        # 初始化热词管理器
        self.hotword = HotwordManager(
            hotword_files=None,
            threshold=Config.hot_thresh,
            similar_threshold=Config.hot_similar
        )

        # 4. 初始化 LLM 润色系统
        self.llm = LLMHandler(app=self)
        
        self.output = TextOutput()

        # voice-ime 日志层：记录每次转写的 {ts, text, audio} 三元组
        # v5.4+：原版 diary 日记不再写入，docs/daily 每日文档已替代它
        self.daily_logger = DailyLogger(base_dir=self.base_dir)

        # 初始化各管理器
        self.ws = WebSocketManager(self)
        self.tray = TrayManager(self)

        # 实例化硬件资源管理组件
        self.stream = AudioStreamManager(self)
        self.shortcut = ShortcutManager(self, [Shortcut(**sc) for sc in Config.shortcuts])
        self.udp = UDPController(self.shortcut)

        # 内存清理
        empty_current_working_set()

    def stop(self):
        """
        统一释放所有资源（清理顺序：硬件 -> 托盘 -> WebSocket -> State）
        """
        logger.info("正在执行 CapsWriterClient 资源释放...")

        # 1. 停止核心运行组件
        self.udp.stop()
        self.shortcut.stop()
        self.stream.stop()

        # 2. 托盘资源
        self.tray.stop()

        # 3. 关闭监控
        self.hotword.stop()
        self.llm.stop()

        # 4. 关闭 WebSocket 连接
        self.ws.close_sync()

        # 5. 重置 State
        try:
            self.state.reset()
        except Exception as e:
            logger.warning(f"重置状态时发生错误: {e}")

        # 6. 停止事件循环（最后一步，确保前面的异步操作已调度）
        self.loop.stop()

        logger.info("资源释放完成")
        console.print('[green4]再见！')


    def start_daily_doc_timer(self):
        """
        启动每日文档自动编译定时器：每天 23:55 检查当天日志，
        仅当有新日志时编译（幂等，重复运行无副作用）。
        """
        async def _scheduler():
            from datetime import datetime as _dt, timedelta as _td
            from .doc_compiler.compile_day import compile_day, needs_compile
            while True:
                now = _dt.now()
                target = now.replace(hour=23, minute=55, second=0, microsecond=0)
                if target <= now:
                    target += _td(days=1)
                await asyncio.sleep((target - now).total_seconds())
                try:
                    today = _dt.now().strftime('%Y-%m-%d')
                    if needs_compile(today, self.base_dir, Config.phone_log_dirs):
                        out = compile_day(today, self.base_dir, Config.phone_log_dirs)
                        logger.info(f"已自动编译今日文档: {out}")
                    # v5: 编译之后尝试自动生成内容总结；LLM 不可达则静默跳过
                    try:
                        from .doc_compiler.summarize import summarize_day, needs_summary
                        from .doc_compiler.llm_client import LLMUnavailable
                        if needs_summary(today, self.base_dir):
                            sout = summarize_day(today, self.base_dir)
                            if sout:
                                logger.info(f"已自动生成内容总结: {sout}")
                    except LLMUnavailable:
                        logger.info("LLM 服务不可达，跳过自动内容总结")
                    except Exception as e2:
                        logger.warning(f"自动内容总结失败: {e2}")
                except Exception as e:
                    logger.warning(f"自动编译今日文档失败: {e}")

        self.loop.create_task(_scheduler())
        logger.info("每日文档自动编译定时器已启动（每天 23:55，有新日志时编译）")

    def start(self):
        """
        启动客户端 (唯一入口)
        
        自动根据命令行参数识别模式。内部管理异步循环。
        """

        # 注册退出函数
        register_signal(self.stop)

        files = [Path(f) for f in sys.argv[1:] if os.path.exists(f)]

        if files:
            # 文件转录模式
            runner = FileRunner(self, files)
        else:
            # 麦克风实时模式
            runner = MicRunner(self)
        
        try:
            self.loop.run_until_complete(runner.run())
        except RuntimeError:
            ...


