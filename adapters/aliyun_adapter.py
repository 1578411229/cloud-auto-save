# -*- coding: utf-8 -*-
"""
阿里云盘适配器
基于 aligo 库实现
"""
import re
import time
import logging
from typing import Dict, List, Tuple, Optional, Any

from adapters.base_adapter import BaseCloudDriveAdapter

# 尝试导入 aligo
try:
    from aligo import Aligo
    ALIGO_AVAILABLE = True
except ImportError:
    ALIGO_AVAILABLE = False
    logging.warning("[Aliyun] aligo 库未安装，阿里云盘功能不可用")


class AliyunAdapter(BaseCloudDriveAdapter):
    """阿里云盘适配器"""

    DRIVE_TYPE = "aliyun"

    def __init__(self, cookie: str = "", index: int = 0):
        """
        初始化阿里云盘适配器
        
        Args:
            cookie: 这里实际是 refresh_token
            index: 账户索引
        """
        super().__init__(cookie, index)
        self.client: Optional[Any] = None
        self.refresh_token = cookie.strip() if cookie else ""
        self._share_tokens: Dict[str, str] = {}  # share_id -> share_token
        self._file_cache: Dict[str, Dict] = {}  # file_id -> file_info

        if not ALIGO_AVAILABLE:
            logging.error("[Aliyun] aligo 库未安装")
            return

        if self.refresh_token:
            try:
                # 使用 refresh_token 初始化 aligo
                self.client = Aligo(refresh_token=self.refresh_token)
            except Exception as e:
                logging.error(f"[Aliyun] 创建客户端失败: {e}")

    def init(self) -> Any:
        """初始化账户，验证 token 有效性"""
        if not ALIGO_AVAILABLE:
            logging.error("[Aliyun] aligo 库未安装，无法初始化")
            return False

        if not self.client:
            logging.error("[Aliyun] 客户端未创建，请检查 refresh_token")
            return False

        try:
            user = self.client.get_user()
            if user:
                self.is_active = True
                self.nickname = user.nick_name or user.user_name or f"阿里云盘用户{self.index}"
                return {
                    "user_id": user.user_id,
                    "user_name": user.user_name,
                    "nick_name": user.nick_name,
                    "nickname": self.nickname,
                }
        except Exception as e:
            logging.error(f"[Aliyun] 初始化失败: {e}")

        return False

    def get_account_info(self) -> Any:
        """获取账户信息"""
        return self.init()

    def get_stoken(self, pwd_id: str, passcode: str = "") -> Dict:
        """
        获取分享令牌
        pwd_id = share_id
        passcode = share_pwd
        """
        if not ALIGO_AVAILABLE or not self.client:
            return {"status": 500, "code": 1, "message": "阿里云盘客户端未初始化"}

        try:
            # 获取分享令牌
            share_token = self.client.get_share_token(pwd_id, passcode)
            if share_token:
                self._share_tokens[pwd_id] = share_token.share_token
                return {
                    "status": 200,
                    "code": 0,
                    "data": {
                        "stoken": share_token.share_token,
                        "share_id": pwd_id,
                        "expire_time": share_token.expire_time,
                    },
                    "message": "success",
                }
        except Exception as e:
            error_msg = str(e)
            logging.error(f"[Aliyun] 获取分享令牌失败: {error_msg}")
            
            # 解析错误信息
            if "ShareLink is cancelled" in error_msg:
                return {"status": 400, "code": 1, "message": "分享链接已取消"}
            if "share_pwd" in error_msg.lower() or "密码" in error_msg:
                return {"status": 400, "code": 1, "message": "提取码错误"}
            if "not found" in error_msg.lower():
                return {"status": 400, "code": 1, "message": "分享链接不存在"}

        return {"status": 400, "code": 1, "message": "分享链接无效或已失效"}

    def get_detail(
        self,
        pwd_id: str,
        stoken: str,
        pdir_fid: str,
        _fetch_share: int = 0,
        fetch_share_full_path: int = 0,
    ) -> Dict:
        """获取分享文件详情列表"""
        if not ALIGO_AVAILABLE or not self.client:
            return {"code": 1, "message": "阿里云盘客户端未初始化", "data": {"list": []}}

        try:
            # 确保有分享令牌
            if pwd_id not in self._share_tokens:
                self._share_tokens[pwd_id] = stoken

            # parent_file_id，root 为根目录
            parent_file_id = pdir_fid if pdir_fid and pdir_fid != "0" else "root"

            # 获取分享文件列表
            file_list = []
            marker = None

            while True:
                result = self.client.get_share_file_list(
                    pwd_id,
                    parent_file_id=parent_file_id,
                    marker=marker,
                )

                if not result or not result.items:
                    break

                for item in result.items:
                    file_info = self._convert_share_item(item)
                    file_list.append(file_info)
                    self._file_cache[item.file_id] = file_info

                marker = result.next_marker
                if not marker:
                    break

            return {
                "code": 0,
                "message": "success",
                "data": {"list": file_list},
                "metadata": {"_total": len(file_list)},
            }

        except Exception as e:
            logging.error(f"[Aliyun] 获取分享详情失败: {e}")
            return {"code": 1, "message": f"获取分享详情失败: {e}", "data": {"list": []}}

    def _convert_share_item(self, item: Any) -> Dict:
        """转换阿里云盘分享文件项为统一格式"""
        is_dir = item.type == "folder"
        return {
            "fid": item.file_id,
            "file_name": item.name,
            "file_type": 0 if is_dir else 1,
            "dir": is_dir,
            "size": item.size if hasattr(item, "size") else 0,
            "updated_at": int(time.mktime(item.updated_at.timetuple())) if hasattr(item, "updated_at") and item.updated_at else 0,
            "share_fid_token": item.file_id,
        }

    def ls_dir(self, pdir_fid: str, **kwargs) -> Dict:
        """列出用户网盘目录内容"""
        if not ALIGO_AVAILABLE or not self.client:
            return {"code": 1, "message": "阿里云盘客户端未初始化", "data": {"list": []}}

        try:
            parent_file_id = pdir_fid if pdir_fid and pdir_fid != "0" else "root"
            
            file_list = []
            marker = None

            while True:
                result = self.client.get_file_list(
                    parent_file_id=parent_file_id,
                    marker=marker,
                )

                if not result or not result.items:
                    break

                for item in result.items:
                    file_info = self._convert_dir_item(item)
                    file_list.append(file_info)
                    self._file_cache[item.file_id] = file_info

                marker = result.next_marker
                if not marker:
                    break

            return {
                "code": 0,
                "message": "success",
                "data": {"list": file_list},
                "metadata": {"_total": len(file_list)},
            }

        except Exception as e:
            logging.error(f"[Aliyun] 列出目录失败: {e}")
            return {"code": 1, "message": f"列出目录失败: {e}", "data": {"list": []}}

    def _convert_dir_item(self, item: Any) -> Dict:
        """转换阿里云盘目录文件项为统一格式"""
        is_dir = item.type == "folder"
        return {
            "fid": item.file_id,
            "file_name": item.name,
            "file_type": 0 if is_dir else 1,
            "dir": is_dir,
            "size": item.size if hasattr(item, "size") else 0,
            "updated_at": int(time.mktime(item.updated_at.timetuple())) if hasattr(item, "updated_at") and item.updated_at else 0,
        }

    def save_file(
        self,
        fid_list: List[str],
        fid_token_list: List[str],
        to_pdir_fid: str,
        pwd_id: str,
        stoken: str,
    ) -> Dict:
        """转存文件到指定目录"""
        if not ALIGO_AVAILABLE or not self.client:
            return {"code": 1, "message": "阿里云盘客户端未初始化", "data": {}}

        try:
            # 目标目录 ID
            to_parent_file_id = to_pdir_fid if to_pdir_fid and to_pdir_fid != "0" else "root"

            # 转存文件
            # fid_token_list 包含要转存的 file_id
            result = self.client.share_file_saveto_drive(
                share_id=pwd_id,
                file_id_list=fid_token_list,
                to_parent_file_id=to_parent_file_id,
            )

            if result:
                return {
                    "code": 0,
                    "message": "success",
                    "data": {
                        "task_id": f"aliyun_sync_{pwd_id}_{int(time.time())}",
                        "_sync": True,  # 阿里云盘转存是同步的
                    },
                }

        except Exception as e:
            error_msg = str(e)
            logging.error(f"[Aliyun] 转存失败: {error_msg}")
            
            # 解析错误信息
            if "QuotaExhausted" in error_msg:
                return {"code": 1, "message": "网盘空间不足", "data": {}}
            if "FileAlreadyExists" in error_msg:
                return {"code": 0, "message": "文件已存在", "data": {"_sync": True}}
            
            return {"code": 1, "message": f"转存失败: {error_msg}", "data": {}}

        return {"code": 1, "message": "转存失败", "data": {}}

    def query_task(self, task_id: str) -> Dict:
        """
        查询任务状态
        阿里云盘转存是同步操作，直接返回完成状态
        """
        return {
            "status": 200,
            "code": 0,
            "data": {
                "status": 2,  # 2 = 完成
                "task_title": "转存文件",
            },
            "message": "success",
        }

    def mkdir(self, dir_path: str) -> Dict:
        """创建目录"""
        if not ALIGO_AVAILABLE or not self.client:
            return {"code": 1, "message": "阿里云盘客户端未初始化"}

        try:
            # 解析路径
            parts = dir_path.strip("/").split("/")
            
            # 逐级创建目录
            parent_id = "root"
            created_id = ""
            
            for part in parts:
                if not part:
                    continue
                    
                # 检查目录是否存在
                existing = self._find_file_in_dir(parent_id, part)
                if existing:
                    parent_id = existing["fid"]
                    created_id = parent_id
                else:
                    # 创建目录
                    result = self.client.create_folder(part, parent_file_id=parent_id)
                    if result:
                        parent_id = result.file_id
                        created_id = result.file_id
                    else:
                        return {"code": 1, "message": f"创建目录 {part} 失败"}

            dir_name = parts[-1] if parts else "新建文件夹"
            return {
                "code": 0,
                "message": "success",
                "data": {"fid": created_id, "file_name": dir_name},
            }

        except Exception as e:
            logging.error(f"[Aliyun] 创建目录失败: {e}")
            return {"code": 1, "message": f"创建目录失败: {e}"}

    def _find_file_in_dir(self, parent_id: str, name: str) -> Optional[Dict]:
        """在目录中查找文件"""
        try:
            result = self.client.get_file_list(parent_file_id=parent_id)
            if result and result.items:
                for item in result.items:
                    if item.name == name:
                        return {"fid": item.file_id, "name": item.name}
        except Exception:
            pass
        return None

    def rename(self, fid: str, file_name: str) -> Dict:
        """重命名文件"""
        if not ALIGO_AVAILABLE or not self.client:
            return {"code": 1, "message": "阿里云盘客户端未初始化"}

        try:
            result = self.client.rename_file(fid, file_name)
            if result:
                return {"code": 0, "message": "success"}
            return {"code": 1, "message": "重命名失败"}

        except Exception as e:
            logging.error(f"[Aliyun] 重命名失败: {e}")
            return {"code": 1, "message": f"重命名失败: {e}"}

    def delete(self, filelist: List[str]) -> Dict:
        """删除文件（移入回收站）"""
        if not ALIGO_AVAILABLE or not self.client:
            return {"code": 1, "message": "阿里云盘客户端未初始化"}

        try:
            result = self.client.batch_move_to_trash(filelist)
            if result:
                return {"code": 0, "message": "success"}
            return {"code": 1, "message": "删除失败"}

        except Exception as e:
            logging.error(f"[Aliyun] 删除失败: {e}")
            return {"code": 1, "message": f"删除失败: {e}"}

    def get_fids(self, file_paths: List[str]) -> List[Dict]:
        """根据路径获取文件 ID"""
        if not ALIGO_AVAILABLE or not self.client:
            return []

        results = []
        for path in file_paths:
            if not path or path == "/":
                results.append({"file_path": "/", "fid": "root"})
                continue

            path = path.strip("/")
            parts = path.split("/")
            
            # 逐级查找
            current_id = "root"
            found = True
            
            for part in parts:
                if not part:
                    continue
                    
                file_info = self._find_file_in_dir(current_id, part)
                if file_info:
                    current_id = file_info["fid"]
                else:
                    found = False
                    break
            
            if found:
                results.append({"file_path": f"/{path}", "fid": current_id})

        return results

    def extract_url(self, url: str) -> Tuple[Optional[str], str, Any, List]:
        """
        解析阿里云盘分享链接
        
        支持格式:
        - https://www.aliyundrive.com/s/{share_id}
        - https://www.alipan.com/s/{share_id}
        - https://www.aliyundrive.com/s/{share_id}/folder/{folder_id}
        """
        pwd_id = None
        passcode = ""
        pdir_fid = "root"
        paths = []

        # 提取分享 ID
        match_s = re.search(r"(?:aliyundrive|alipan)\.com/s/([a-zA-Z0-9]+)", url)
        if match_s:
            pwd_id = match_s.group(1)

        # 提取提取码
        match_pwd = re.search(r"(?:pwd|password|code)=([a-zA-Z0-9]+)", url)
        if match_pwd:
            passcode = match_pwd.group(1)

        # 提取子目录 ID
        match_folder = re.search(r"/folder/([a-zA-Z0-9]+)", url)
        if match_folder:
            pdir_fid = match_folder.group(1)

        return pwd_id, passcode, pdir_fid, paths
