# -*- coding: utf-8 -*-
"""
云盘适配器模块
支持多网盘的统一接口
"""

from adapters.base_adapter import BaseCloudDriveAdapter
from adapters.quark_adapter import QuarkAdapter
from adapters.cloud115_adapter import Cloud115Adapter
from adapters.adapter_factory import AdapterFactory, AccountManager

__all__ = [
    "BaseCloudDriveAdapter",
    "QuarkAdapter",
    "Cloud115Adapter",
    "AdapterFactory",
    "AccountManager",
]

__version__ = "1.0.0"
