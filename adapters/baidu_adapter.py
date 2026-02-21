# -*- coding: utf-8 -*-
"""
百度网盘适配器
基于 baidupcs-py 库实现
"""
import re
import logging
import time
import traceback
from typing import Dict, List, Tuple, Optional, Any

from adapters.base_adapter import BaseCloudDriveAdapter

# 尝试导入 baidupcs-py
try:
    from baidupcs_py import BaiduPCSApi
    BAIDUPCS_AVAILABLE = True
except ImportError:
    BAIDUPCS_AVAILABLE = False
    logging.warning("[Baidu] baidupcs-py 库未安装，百度网盘功能不可用")


class BaiduAdapter(BaseCloudDriveAdapter):
    """百度网盘适配器"""

    DRIVE_TYPE = "baidu"

    # 错误码映射
    ERROR_CODES = {
        -6: "认证失败，请检查Cookie是否有效",
        -65: "访问频率过高，请稍后重试",
        145: "分享链接已失效",
        200025: "提取码错误",
        31066: "目录不存在",
        31061: "文件已存在",
        31299: "文件夹创建失败",
        -9: "文件不存在或已被删除",
        -3: "转存文件数超过限制",
        -7: "分享文件夹等待审核中",
        -21: "您的账户被锁定",
        2: "参数错误",
        111: "有其他异步任务正在执行",
        12: "转存文件失败",
        4: "分享提取码错误",
        105: "分享链接已过期",
        -1: "分享链接不存在",
    }

    def __init__(self, cookie: str = "", index: int = 0):
        super().__init__(cookie, index)
        self.client: Optional[Any] = None
        self._cookies_dict: Dict[str, str] = {}
        self._share_info: Dict[str, Dict] = {}  # 缓存分享信息

        if not BAIDUPCS_AVAILABLE:
            logging.error("[Baidu] baidupcs-py 库未安装")
            return

        # 解析 cookie
        if cookie:
            self._parse_cookies(cookie)
            # 创建 BaiduPCSApi 客户端
            self.client = BaiduPCSApi(cookies=self._cookies_dict)
            # try:
            #     bduss = self._cookies_dict.get("BDUSS", "")
            #     stoken = self._cookies_dict.get("STOKEN", "")
            #     if bduss:
            #         self.client = BaiduPCSApi(bduss=bduss, stoken=stoken)
            # except Exception as e:
            #     logging.error(f"[Baidu] 创建客户端失败: {e}")

    def _parse_cookies(self, cookie: str):
        """解析 cookie 字符串"""
        for item in cookie.split(";"):
            item = item.strip()
            if "=" in item:
                k, v = item.split("=", 1)
                self._cookies_dict[k.strip()] = v.strip()

    def _get_error_message(self, errno: int) -> str:
        """获取错误码对应的提示信息"""
        return self.ERROR_CODES.get(errno, f"未知错误 (errno={errno})")

    def _resolve_fid_to_path(self, fid: str) -> str:
        """
        将 fid 解析为百度网盘 API 可用的路径。
        
        通过 self.client.list 从根目录开始逐级检索，
        直到找到目标 fs_id 对应的完整路径。
        
        fid 可能是:
        - 路径字符串（以 / 开头），直接返回
        - "0" 或空，表示根目录，返回 /
        - fs_id（纯数字字符串），通过逐级遍历目录树查找
        """
        if not fid or fid == "0":
            return "/"
        # 已经是路径格式
        if fid.startswith("/"):
            return fid
        # fs_id 必须是数字
        if not fid.isdigit():
            logging.warning(f"[Baidu] fid={fid} 格式无法识别")
            return "/"
        if not self.client:
            return "/"

        # BFS: 从根目录逐级遍历，查找匹配 fs_id 的文件/目录
        dirs_to_search = ["/"]
        max_depth = 10  # 防止无限遍历

        for depth in range(max_depth):
            next_dirs = []
            for current_dir in dirs_to_search:
                try:
                    items = self.client.list(current_dir)
                except Exception as e:
                    logging.debug(f"[Baidu] 列出 {current_dir} 失败: {e}")
                    continue

                for item in items:
                    if str(item.fs_id) == fid:
                        logging.debug(f"[Baidu] fid={fid} 解析为路径: {item.path}")
                        return item.path
                    if item.is_dir:
                        next_dirs.append(item.path)

            if not next_dirs:
                break
            dirs_to_search = next_dirs

        logging.warning(f"[Baidu] 遍历目录树后仍未找到 fid={fid} 对应的路径")
        return "/"

    def _bfs_share_tree(
        self,
        shared_paths: List,
        uk: int,
        share_id: int,
        bdstoken: str,
        target_fid: str,
        max_depth: int = 5,
    ) -> Tuple[str, List[Dict]]:
        """
        在分享目录树中 BFS 查找 target_fid (fs_id)。

        Args:
            shared_paths: client.shared_paths() 返回的根级条目列表
            uk, share_id, bdstoken: 分享元数据
            target_fid: 要查找的 fs_id（字符串）
            max_depth: 最大搜索深度

        Returns:
            (path_string, breadcrumb) 其中 breadcrumb 格式为
            [{"fid": "...", "file_name": "..."}, ...]
            未找到时返回 ("", [])
        """
        # 先检查根级条目是否就是目标
        for item in shared_paths:
            if str(item.fs_id) == target_fid:
                filename = item.path.split('/')[-1]
                return item.path, [
                    {"fid": str(item.fs_id), "file_name": filename}
                ]

        # BFS 队列: [(目录路径, 累计面包屑)]
        queue: List[Tuple[str, List[Dict]]] = []
        for item in shared_paths:
            if item.is_dir:
                filename = item.path.split('/')[-1]
                queue.append((
                    item.path,
                    [{"fid": str(item.fs_id), "file_name": filename}],
                ))

        for _ in range(max_depth):
            next_queue: List[Tuple[str, List[Dict]]] = []
            for current_path, current_breadcrumb in queue:
                try:
                    items = self.client.list_shared_paths(
                        current_path, uk, share_id, bdstoken
                    )
                except Exception as e:
                    logging.debug(f"[Baidu] 列出分享目录 {current_path} 失败: {e}")
                    continue

                for item in items:
                    item_fid = str(item.fs_id)
                    item_name = item.path.split('/')[-1]
                    new_breadcrumb = current_breadcrumb + [
                        {"fid": item_fid, "file_name": item_name}
                    ]
                    if item_fid == target_fid:
                        return item.path, new_breadcrumb
                    if item.is_dir:
                        next_queue.append((item.path, new_breadcrumb))

            if not next_queue:
                break
            queue = next_queue

        logging.warning(
            f"[Baidu] 分享目录树中未找到 fid={target_fid}，深度限制={max_depth}"
        )
        return "", []

    def _resolve_share_fid_to_path(
        self, share_url: str, passcode: str, fid: str
    ) -> str:
        """
        将分享链接中的 fid (fs_id) 解析为路径字符串。

        fid 可能是:
        - 路径字符串（以 / 开头），直接返回
        - "0"/"/" 或空，表示根目录，返回 "/"
        - fs_id（纯数字字符串），通过 BFS 搜索分享目录树查找
        """
        if not fid or fid == "0" or fid == "/":
            return "/"
        if fid.startswith("/"):
            return fid
        if not fid.isdigit():
            logging.warning(f"[Baidu] share fid={fid} 格式无法识别")
            return "/"
        if not self.client:
            return "/"

        try:
            self.client.access_shared(share_url, passcode)
            shared_paths = self.client.shared_paths(share_url)
            if not shared_paths:
                return "/"
            first = shared_paths[0]
            path_str, _ = self._bfs_share_tree(
                shared_paths, first.uk, first.share_id, first.bdstoken, fid
            )
            return path_str if path_str else "/"
        except Exception as e:
            logging.error(f"[Baidu] _resolve_share_fid_to_path 失败: {e}")
            return "/"

    def init(self) -> Any:
        """初始化账户，验证 cookie 有效性"""
        if not BAIDUPCS_AVAILABLE:
            logging.error("[Baidu] baidupcs-py 库未安装，无法初始化")
            return False

        if not self.client:
            logging.error("[Baidu] 客户端未创建，请检查Cookie格式")
            return False

        try:
            user_info = self.client.user_info()
            if user_info:
                self.is_active = True
                self.nickname = user_info.user_name or f"百度用户{self.index}"
                return {
                    "user_id": user_info.user_id,
                    "user_name": user_info.user_name,
                    "nickname": self.nickname,
                }
        except Exception as e:
            logging.error(f"[Baidu] 初始化失败: {e}")

        return False

    def _resolve_share_path(
        self,
        share_url: str,
        cid: str,
    ) -> List[Dict]:
        """
        在分享目录树中 BFS 查找 cid 的完整路径（面包屑）。
        返回格式与 Quark full_path 一致: [{"fid": "...", "file_name": "..."}]
        """
        if not cid or cid in ("0", "/"):
            return []
        if not self.client:
            return []

        try:
            shared_paths = self.client.shared_paths(share_url)
            if not shared_paths:
                return []
            first = shared_paths[0]
            _, breadcrumb = self._bfs_share_tree(
                shared_paths, first.uk, first.share_id, first.bdstoken, cid
            )
            return breadcrumb
        except Exception as e:
            logging.error(f"[Baidu] _resolve_share_path 失败: {e}")
            return []

    def get_account_info(self) -> Any:
        """获取账户信息"""
        return self.init()

    def get_stoken(self, pwd_id: str, passcode: str = "") -> Dict:
        """
        获取分享令牌（验证分享链接有效性）
        pwd_id = share_id (如 1ABCdefGHI)
        passcode = 提取码
        """
        if not BAIDUPCS_AVAILABLE or not self.client:
            return {"status": 500, "code": 1, "message": "百度网盘客户端未初始化"}

        try:
            # 构建分享链接
            share_url = f"https://pan.baidu.com/s/{pwd_id}?pwd={passcode}"

            # 访问分享
            self.client.access_shared(share_url, passcode)
            return {
                "status": 200,
                "code": 0,
                "data": {
                    "stoken": f"{pwd_id}:{passcode}",
                    "share_id": pwd_id,
                },
                "message": "success",
            }
        except Exception as e:
            error_msg = str(e)
            logging.error(f"[Baidu] 访问分享失败: {error_msg}")
            # 解析错误码
            errno_match = re.search(r"errno[=:]?\s*(-?\d+)", error_msg)
            if errno_match:
                errno = int(errno_match.group(1))
                return {
                    "status": 400,
                    "code": errno,
                    "message": self._get_error_message(errno),
                }

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
        if not BAIDUPCS_AVAILABLE or not self.client:
            return {"code": 1, "message": "百度网盘客户端未初始化", "data": {"list": []}}

        try:
            # 解析 stoken
            parts = stoken.split(":") if stoken else [pwd_id, ""]
            share_id = parts[0]
            passcode = parts[1] if len(parts) > 1 else ""

            share_url = f"https://pan.baidu.com/s/{share_id}"
            self.client.access_shared(share_url, passcode)

            # 获取分享根目录信息（只调用一次）
            shared_paths = self.client.shared_paths(share_url)
            if not shared_paths:
                return {
                    "code": 0,
                    "message": "success",
                    "data": {"list": [], "full_path": []},
                    "metadata": {"_total": 0},
                }

            first = shared_paths[0]
            uk = first.uk
            share_id_num = first.share_id
            bdstoken = first.bdstoken

            # 解析 pdir_fid，同时获取路径和面包屑
            is_root = not pdir_fid or pdir_fid in ("0", "/")
            remote_path = ""
            full_path = []

            if is_root:
                remote_path = first.path
            elif pdir_fid.startswith("/"):
                remote_path = pdir_fid
            elif pdir_fid.isdigit():
                # 通过 BFS 一次性获取路径和面包屑
                path_str, breadcrumb = self._bfs_share_tree(
                    shared_paths, uk, share_id_num, bdstoken, pdir_fid
                )
                remote_path = path_str if path_str else first.path
                if fetch_share_full_path:
                    full_path = breadcrumb
            else:
                logging.warning(f"[Baidu] pdir_fid={pdir_fid} 格式无法识别，使用根目录")
                remote_path = first.path

            # 获取文件列表
            folder_files = self.client.list_shared_paths(
                remote_path, uk, share_id_num, bdstoken
            )
            if not folder_files:
                return {
                    "code": 0,
                    "message": "success",
                    "data": {"list": [], "full_path": full_path},
                    "metadata": {"_total": 0},
                }

            # 转换文件列表
            file_list = [self._convert_shared_item(item) for item in folder_files]

            return {
                "code": 0,
                "message": "success",
                "data": {"list": file_list, "full_path": full_path},
                "metadata": {"_total": len(file_list)},
            }

        except Exception as e:
            logging.error(f"[Baidu] 获取分享详情失败: {e}")
            return {"code": 1, "message": f"获取分享详情失败: {e}", "data": {"list": []}}

    def _convert_shared_item(self, item: Any) -> Dict:
        """转换百度分享文件项为统一格式"""
        is_dir = item.is_dir if hasattr(item, "is_dir") else False
        return {
            "fid": str(item.fs_id) if hasattr(item, "fs_id") else "",
            "file_name": item.server_filename if hasattr(item, "server_filename") else item.path.split("/")[-1],
            "file_type": 0 if is_dir else 1,
            "dir": is_dir,
            "size": item.size if hasattr(item, "size") else 0,
            "updated_at": item.server_mtime if hasattr(item, "server_mtime") else 0,
            "share_fid_token": str(item.fs_id) if hasattr(item, "fs_id") else "",
            "path": item.path if hasattr(item, "path") else "",
        }

    def ls_dir(self, pdir_fid: str, **kwargs) -> Dict:
        """列出用户网盘目录内容"""
        if not BAIDUPCS_AVAILABLE or not self.client:
            return {"code": 1, "message": "百度网盘客户端未初始化", "data": {"list": []}}

        try:
            # 将 fid 解析为路径（fid 可能是 fs_id 数字串或路径）
            remote_path = self._resolve_fid_to_path(str(pdir_fid) if pdir_fid else "0")

            files = self.client.list(remote_path)
            file_list = []

            for item in files:
                fs_id = str(item.fs_id)
                server_filename = item.path.split('/')[-1]
                # 使用 path 作为 fid，这样后续 ls_dir(fid) 可以直接使用
                file_info = {
                    "fid": item.path,
                    "file_name": server_filename,
                    "file_type": 0 if item.is_dir else 1,
                    "dir": item.is_dir,
                    "size": item.size,
                    "updated_at": item.server_mtime,
                    "share_fid_token": fs_id,
                    "path": item.path,
                }
                file_list.append(file_info)

            return {
                "code": 0,
                "message": "success",
                "data": {"list": file_list},
                "metadata": {"_total": len(file_list)},
            }

        except Exception as e:
            logging.error(f"[Baidu] 列出目录失败: {e}")
            return {"code": 1, "message": f"列出目录失败: {e}", "data": {"list": []}}

    def save_file(
        self,
        fid_list: List[str],
        fid_token_list: List[str],
        to_pdir_fid: str,
        pwd_id: str,
        stoken: str,
        file_names: List[str] = None,
    ) -> Dict:
        """转存文件到指定目录"""
        if not BAIDUPCS_AVAILABLE or not self.client:
            return {"code": 1, "message": "百度网盘客户端未初始化", "data": {}}

        try:
            # 解析参数
            parts = stoken.split(":") if stoken else [pwd_id, ""]
            share_id = parts[0]
            passcode = parts[1] if len(parts) > 1 else ""
            share_url = f"https://pan.baidu.com/s/{share_id}"

            # 将目标 fid 解析为路径
            remote_dir = self._resolve_fid_to_path(to_pdir_fid if to_pdir_fid else "0")

            # --- 记录转存前目标目录的文件列表 ---
            before_items = {}  # {fid: file_name}
            try:
                before_dir = self.ls_dir(to_pdir_fid if to_pdir_fid else "0")
                if before_dir.get("code") == 0:
                    for item in before_dir.get("data", {}).get("list", []):
                        before_items[item.get("fid", "")] = item.get("file_name", "")
            except Exception:
                pass

            # 获取分享信息
            if share_id not in self._share_info:
                token_result = self.get_stoken(share_id, passcode)
                if token_result.get("code") != 0:
                    return {"code": 1, "message": token_result.get("message"), "data": {}}

            # 获取分享根目录信息（只调用一次）
            shared_paths = self.client.shared_paths(share_url)
            if not shared_paths:
                return {"code": 1, "message": f"转存失败: 链接失效或网络异常", "data": {}}

            first = shared_paths[0]
            uk = first.uk
            share_id_num = first.share_id
            bdstoken = first.bdstoken

            # 转存文件
            # fid_token_list 包含 fs_id
            fs_ids = [int(fid) for fid in fid_token_list if fid]
            result = self.client.transfer_shared_paths(
                remote_dir,
                fs_ids,
                uk,
                share_id_num,
                bdstoken,
                share_url,
            )

            # --- 转存后列目录，按文件名建立新 fid 映射 ---
            time.sleep(5)
            name_to_new_fid = {}  # {file_name: new_fid}
            try:
                after_dir = self.ls_dir(to_pdir_fid if to_pdir_fid else "0")
                if after_dir.get("code") == 0:
                    for item in after_dir.get("data", {}).get("list", []):
                        fid = item.get("fid", "")
                        fname = item.get("file_name", "")
                        # 只记录新增的文件（不在转存前的 fid 列表中）
                        if fid and fid not in before_items:
                            name_to_new_fid[fname] = fid
            except Exception as e:
                logging.error(f"[Baidu] 转存后获取目录失败: {e}")

            # --- 按 file_names 顺序组装 save_as_top_fids ---
            saved_fids = []
            if file_names:
                for fname in file_names:
                    new_fid = name_to_new_fid.get(fname, "")
                    if new_fid:
                        saved_fids.append(new_fid)
                    else:
                        # 如果按文件名找不到，可能是文件名有特殊字符被改变
                        # 尝试模糊匹配（去除特殊字符后比较）
                        fname_clean = re.sub(r'[^\w\s\.]', '', fname)
                        found = False
                        for k, v in name_to_new_fid.items():
                            k_clean = re.sub(r'[^\w\s\.]', '', k)
                            if fname_clean == k_clean:
                                saved_fids.append(v)
                                found = True
                                break
                        if not found:
                            logging.warning(f"[Baidu] 未找到文件 '{fname}' 的新 fid")
                            saved_fids.append("")  # 占位，保持索引对齐
            else:
                # 兼容旧调用方式：直接返回新增文件的 fid 列表
                saved_fids = list(name_to_new_fid.values())

            if not result:
                return {
                    "code": 0,
                    "message": "success",
                    "data": {
                        "task_id": f"baidu_sync_{share_id}",
                        "save_as_top_fids": saved_fids,
                        "_sync": True,  # 百度转存是同步的
                    },
                }

        except Exception as e:
            error_msg = str(e)
            logging.error(f"[Baidu] 转存失败: {error_msg}")
            # 解析错误码
            errno_match = re.search(r"errno[=:]?\s*(-?\d+)", error_msg)
            if errno_match:
                errno = int(errno_match.group(1))
                return {"code": errno, "message": self._get_error_message(errno), "data": {}}
            return {"code": 1, "message": f"转存失败: {error_msg}", "data": {}}

        return {"code": 1, "message": "转存失败", "data": {}}

    def query_task(self, task_id: str) -> Dict:
        """
        查询任务状态
        百度网盘转存是同步操作，直接返回完成状态
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
        if not BAIDUPCS_AVAILABLE or not self.client:
            return {"code": 1, "message": "百度网盘客户端未初始化"}

        try:
            if not dir_path.startswith("/"):
                dir_path = "/" + dir_path

            self.client.makedir(dir_path)
            dir_name = dir_path.rstrip("/").split("/")[-1]

            # 返回 path 作为 fid（与 ls_dir 保持一致）
            return {
                "code": 0,
                "message": "success",
                "data": {"fid": dir_path, "file_name": dir_name},
            }

        except Exception as e:
            error_msg = str(e)
            logging.error(f"[Baidu] 创建目录失败: {error_msg}")
            # 目录可能已存在
            if "31061" in error_msg or "already" in error_msg.lower():
                dir_name = dir_path.rstrip("/").split("/")[-1]
                return {
                    "code": 0,
                    "message": "目录已存在",
                    "data": {"fid": dir_path, "file_name": dir_name},
                }
            return {"code": 1, "message": f"创建目录失败: {error_msg}"}

    def rename(self, fid: str, file_name: str) -> Dict:
        """重命名文件"""
        if not BAIDUPCS_AVAILABLE or not self.client:
            return {"code": 1, "message": "百度网盘客户端未初始化"}

        try:
            old_path = self._resolve_fid_to_path(fid)
            if old_path == "/":
                return {"code": 1, "message": "未找到文件路径，请先刷新目录"}

            # 构建新路径
            parent_path = "/".join(old_path.rstrip("/").split("/")[:-1]) or "/"
            new_path = f"{parent_path}/{file_name}"

            self.client.rename(old_path, new_path)
            return {"code": 0, "message": "success"}

        except Exception as e:
            logging.error(f"[Baidu] 重命名失败: {e}")
            return {"code": 1, "message": f"重命名失败: {e}"}

    def delete(self, filelist: List[str]) -> Dict:
        """删除文件"""
        if not BAIDUPCS_AVAILABLE or not self.client:
            return {"code": 1, "message": "百度网盘客户端未初始化"}

        try:
            paths = []
            for fid in filelist:
                path = self._resolve_fid_to_path(fid)
                if path != "/":
                    paths.append(path)

            if not paths:
                return {"code": 1, "message": "未找到要删除的文件"}

            self.client.remove(*paths)
            return {"code": 0, "message": "success"}

        except Exception as e:
            logging.error(f"[Baidu] 删除失败: {e}")
            return {"code": 1, "message": f"删除失败: {e}"}

    def get_fids(self, file_paths: List[str]) -> List[Dict]:
        """根据路径获取文件 ID（百度网盘使用路径作为标识）"""
        if not BAIDUPCS_AVAILABLE or not self.client:
            return []

        results = []
        for path in file_paths:
            if not path or path == "/":
                results.append({"file_path": "/", "fid": "/"})
                continue

            path = path.strip()
            if not path.startswith("/"):
                path = "/" + path

            # 检查路径是否存在
            try:
                parent_path = "/".join(path.rstrip("/").split("/")[:-1]) or "/"
                target_name = path.rstrip("/").split("/")[-1]
                files = self.client.list(parent_path)
                for f in files:
                    if f.path.split('/')[-1] == target_name:
                        results.append({"file_path": path, "fid": path})
                        break
            except Exception:
                pass

        return results

    def extract_url(self, url: str) -> Tuple[Optional[str], str, Any, List]:
        """
        解析百度网盘分享链接
        支持格式:
        - https://pan.baidu.com/s/1ABCdefGHI
        - https://pan.baidu.com/s/1ABCdefGHI?pwd=xxxx
        - https://pan.baidu.com/share/init?surl=ABCdefGHI
        """
        pwd_id = None
        passcode = ""
        pdir_fid = "/"  # 百度使用路径
        paths = []

        # 提取分享 ID
        # 格式1: /s/1xxxxx
        match_s = re.search(r"/s/([a-zA-Z0-9_-]+)", url)
        if match_s:
            pwd_id = match_s.group(1)
        else:
            # 格式2: surl=xxxxx
            match_surl = re.search(r"surl=([a-zA-Z0-9_-]+)", url)
            if match_surl:
                pwd_id = "1" + match_surl.group(1)

        # 提取提取码
        match_pwd = re.search(r"(?:pwd|password)=([a-zA-Z0-9]+)", url)
        if match_pwd:
            passcode = match_pwd.group(1)
        else:
            # 尝试从 #xxx 提取
            match_hash = re.search(r"#([a-zA-Z0-9]{4})\b", url)
            if match_hash:
                passcode = match_hash.group(1)

        # 提取子目录 ID（去除可能的尾部参数）
        if "#/list/share/" in url:
            raw_fid = url.split("#/list/share/")[-1]
            match_fid = re.match(r"(\w+)", raw_fid)
            if match_fid:
                pdir_fid = match_fid.group(1)

        return pwd_id, passcode, pdir_fid, paths
