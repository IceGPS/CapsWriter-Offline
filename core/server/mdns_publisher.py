# coding: utf-8
"""
mDNS 服务发布：让 Android 端自动发现局域网内的 voice-ime PC 服务端。

服务类型：_voiceime._tcp.local.
服务名：VoiceIME-<电脑名>
TXT 记录：model=<模型名>；剪贴板同步启用时追加 clip=1、id=<本机UUID>、name=<电脑名>
（设计文档第 7.1 节）。
"""
import socket
import logging

logger = logging.getLogger(__name__)

SERVICE_TYPE = "_voiceime._tcp.local."

_zeroconf = None
_service_info = None


def get_local_ip():
    """获取本机局域网 IP"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def start_mdns(port: int, model: str = "", extra_props: dict | None = None) -> bool:
    """
    发布 mDNS 服务，返回是否成功。

    Args:
        port: 服务端口
        model: 语音模型名（写入 TXT 的 model）
        extra_props: 追加的 TXT 记录（键值都会转为 str），如剪贴板同步的
            {"clip": "1", "id": <uuid>, "name": <电脑名>}
    """
    global _zeroconf, _service_info
    try:
        from zeroconf import ServiceInfo, Zeroconf
    except ImportError:
        logger.warning("[mDNS] zeroconf 未安装，跳过服务发布（pip install zeroconf）")
        return False

    try:
        hostname = socket.gethostname()
        service_name = f"VoiceIME-{hostname}.{SERVICE_TYPE}"
        ip = get_local_ip()
        props = {}
        if model:
            props["model"] = model
        if extra_props:
            for k, v in extra_props.items():
                props[str(k)] = str(v)

        _service_info = ServiceInfo(
            SERVICE_TYPE,
            service_name,
            addresses=[socket.inet_aton(ip)],
            port=port,
            properties=props,
        )
        _zeroconf = Zeroconf()
        _zeroconf.register_service(_service_info)
        logger.info(f"[mDNS] 已发布服务: {service_name} ({ip}:{port}) TXT={props}")
        return True
    except Exception as e:
        logger.warning(f"[mDNS] 发布失败: {e}")
        return False


def stop_mdns():
    """注销 mDNS 服务"""
    global _zeroconf, _service_info
    try:
        if _zeroconf and _service_info:
            _zeroconf.unregister_service(_service_info)
            _zeroconf.close()
    except Exception:
        pass
    _zeroconf = None
    _service_info = None
