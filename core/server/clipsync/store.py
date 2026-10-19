# coding: utf-8
"""
剪贴板同步的本地持久化。

文件：<base_dir>/clipsync.json
内容：
{
    "device_id": "<本机稳定 UUID，首次生成后不变>",
    "enabled": false,                       // 托盘总开关，默认关
    "paired": {                             // 已配对手机
        "<phone-uuid>": {"name": "<手机名>", "key": "<base64 AES-256密钥>"}
    }
}

写入采用临时文件 + 原子替换，避免异常退出写坏文件。
"""
from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path

from .. import logger  # Server module logger


class ClipSyncStore:
    """剪贴板同步状态的 JSON 持久化。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.device_id: str = str(uuid.uuid4())
        self.enabled: bool = False
        self.paired: dict = {}
        self.load()

    # ---------- 读写 ----------

    def load(self) -> None:
        """从磁盘加载；文件不存在或损坏时用默认值并（损坏时）备份原文件。"""
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except FileNotFoundError:
            logger.info(f"[剪贴板同步] 配置文件不存在，使用默认配置: {self.path}")
            self.save()
            return
        except Exception as e:
            bak = self.path.with_suffix('.json.bad')
            try:
                os.replace(self.path, bak)
            except Exception:
                pass
            logger.warning(f"[剪贴板同步] 配置文件损坏，已备份为 {bak} 并重置: {e}")
            self.save()
            return

        try:
            self.device_id = str(data.get('device_id') or self.device_id)
            self.enabled = bool(data.get('enabled', False))
            paired = data.get('paired') or {}
            self.paired = {str(k): dict(v) for k, v in paired.items() if isinstance(v, dict)}
        except Exception as e:
            logger.warning(f"[剪贴板同步] 配置解析失败，使用默认值: {e}")

    def save(self) -> None:
        """原子写入磁盘。"""
        data = {
            'device_id': self.device_id,
            'enabled': self.enabled,
            'paired': self.paired,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix='.tmp')
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, self.path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except Exception:
                    pass
                raise
        except Exception as e:
            logger.warning(f"[剪贴板同步] 配置保存失败: {e}")

    # ---------- 配对表操作 ----------

    def add_paired(self, device_id: str, name: str, key_b64: str) -> None:
        self.paired[str(device_id)] = {'name': name, 'key': key_b64}
        self.save()

    def remove_paired(self, device_id: str) -> dict | None:
        info = self.paired.pop(str(device_id), None)
        if info is not None:
            self.save()
        return info

    def get_paired(self, device_id: str) -> dict | None:
        return self.paired.get(str(device_id))

    def list_paired(self) -> list[tuple[str, str]]:
        """返回 [(device_id, name), ...]，供托盘菜单展示。"""
        return [(did, str(info.get('name', did))) for did, info in self.paired.items()]

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        self.save()
