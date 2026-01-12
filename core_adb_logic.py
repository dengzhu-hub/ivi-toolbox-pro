import subprocess
import time
import datetime
import json
from pathlib import Path
import hashlib
import threading
import asyncio
from typing import Dict, Any, List, Optional

# ========================================
# 1. 配置与元信息
# ========================================
DEVICE_CONFIG_PATH_REMOTE = "/mnt/sdcard/DeviceInfo.txt"
LOCAL_CONFIG_PATH_JSON = "DeviceInfo.json"
REMOTE_LOG_PATH = "/mnt/sdcard/AdayoLog"
WLAN_LOG_PATH = "/data/vendor/wifi/wlan_logs"
DEFAULT_PNO = "ADAYO_DEFAULT_JSON"
DEFAULT_VIN = "VINDEMO123456789012"

class ConfigValidator:
    """配置验证器：VIN校验和格式验证 (略)"""
    @staticmethod
    def validate_vin(vin):
        if not vin or len(vin) != 17 or any(char in vin.upper() for char in ['I', 'O', 'Q']):
            return False, "VIN码格式不正确或包含非法字符(I, O, Q)"
        return True, "VIN码格式正确"

    @staticmethod
    def validate_icc_pno(pno):
        if not pno or len(pno) < 5 or not pno.isalnum():
            return False, "ICC_PNO长度不能少于5位，且只能包含字母和数字"
        return True, "ICC_PNO格式正确"

class AdbException(Exception):
    """自定义 ADB 异常"""
    pass

class AdbCore:
    def __init__(self):
        self.serial: Optional[str] = None
        self.log_callback: Optional[callable] = None
        self.current_config: Dict[str, Any] = {}

    def set_log_callback(self, callback: callable):
        """设置日志回调函数，用于将日志推送到 API 层（WebSocket）"""
        self.log_callback = callback

    def _log(self, source: str, message: str, tag: str = "INFO"):
        """内部日志方法，通过回调发送日志"""
        if self.log_callback:
            asyncio.run_coroutine_threadsafe(
                self.log_callback(source, message, tag),
                asyncio.get_event_loop()
            )

    # --- 基础 ADB 操作 ---
    def run_adb_command(self, command: List[str], check_output: bool = False, timeout=120):
        """同步执行 ADB 命令"""
        serial = self.serial
        if serial:
            command = ["adb", "-s", serial] + command
        else:
            command = ["adb"] + command

        self._log("ADB", f"执行命令: {' '.join(command)}", "DEBUG")

        try:
            # 必须使用同步阻塞的 subprocess.run
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                encoding='utf-8',
                timeout=timeout
            )
            output = result.stdout.strip()
            error = result.stderr.strip()

            if result.returncode != 0:
                raise AdbException(f"ADB 命令失败 ({result.returncode}): {error}")

            return result.returncode == 0, output, error

        except FileNotFoundError:
            raise AdbException("ADB 工具未找到。请确保 ADB 在系统 PATH 中。")
        except subprocess.TimeoutExpired:
            raise AdbException(f"命令超时: {' '.join(command)}")
        except Exception as e:
            raise AdbException(f"ADB 执行失败: {e}")

    # --- 设备状态与连接 ---
    def get_device_status(self):
        """检查设备连接状态，返回序列号和状态信息"""
        self._log("系统", "正在检查设备连接...", "INFO")
        try:
            success, output, _ = self.run_adb_command(["devices"], check_output=True, timeout=5)
            devices = []
            if success and output:
                lines = output.split('\n')
                for line in lines[1:]:
                    if line.strip() and "device" in line and "unauthorized" not in line and "emulator" not in line:
                        devices.append(line.split('\t')[0])

            if len(devices) != 1:
                self.serial = None
                return {"status": "disconnected", "serial": None, "message": "未找到单个已连接设备。"}

            self.serial = devices[0]
            self._log("系统", f"设备已连接 ({self.serial})，尝试 Root/Remount...", "WARNING")

            # 尝试权限提升，忽略结果
            self.run_adb_command(["root"], timeout=5)
            time.sleep(1)
            remount_success, _, _ = self.run_adb_command(["remount"], timeout=5)

            if remount_success:
                return {"status": "connected", "serial": self.serial, "message": "连接成功，权限已增强。"}
            else:
                return {"status": "connected_warning", "serial": self.serial, "message": "连接成功，Remount 失败。"}

        except AdbException as e:
            self._log("系统", f"设备检查失败: {e}", "ERROR")
            self.serial = None
            return {"status": "error", "serial": None, "message": f"连接检查失败: {e}"}

    # --- TXT <-> JSON 转换逻辑 (关键) ---
    def _parse_txt_to_json(self, local_txt_path: Path) -> dict:
        """读取 Key-Value TXT 并转为字典"""
        # ... (与旧代码相同，此处简化) ...
        config_data = {}
        try:
            with open(local_txt_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'): continue
                    if '=' in line:
                        key, value = line.split('=', 1)
                        config_data[key.strip()] = value.strip()
            return config_data
        except Exception as e:
            self._log("配置", f"本地 TXT 文件解析失败: {e}", "ERROR")
            return {}

    def _create_default_config(self, local_path: Path) -> dict:
        """创建包含默认值的本地 JSON 配置文件"""
        default_config = {
            'ICC_PNO': DEFAULT_PNO, 'VIN': DEFAULT_VIN,
            'FOTA_VERSION': '0000', 'VEHICLE_TYPE': 'DEMO',
            'TIMESTAMP': datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        }
        # 写入本地 JSON (用于本地 UI 显示和备份)
        try:
            with open(local_path, 'w', encoding='utf-8') as f:
                json.dump(default_config, f, ensure_ascii=False, indent=4)
        except Exception as e:
             self._log("配置", f"创建默认本地 JSON 配置文件失败: {e}", "ERROR")
        return default_config

    # --- 配置拉取与推送 ---
    def pull_config(self) -> Dict[str, Any]:
        """拉取远程 TXT 配置，处理后返回 JSON 格式数据"""
        if not self.serial: raise AdbException("设备未连接，无法拉取配置。")

        local_txt_path = Path("temp_DeviceInfo.txt")
        local_json_path = Path(LOCAL_CONFIG_PATH_JSON)

        # 1. 拉取远程 TXT
        try:
            self.run_adb_command(["pull", DEVICE_CONFIG_PATH_REMOTE, str(local_txt_path)], timeout=30)
        except AdbException as e:
            self._log("配置", f"拉取配置文件失败: {e}。创建默认配置。", "ERROR")
            self.current_config = self._create_default_config(local_json_path)
            return self.current_config

        # 2. 解析 TXT 并转换为 JSON
        config_data = self._parse_txt_to_json(local_txt_path)
        local_txt_path.unlink(missing_ok=True)

        if not config_data:
            self._log("配置", "TXT 文件内容为空或解析失败。创建默认配置。", "WARNING")
            config_data = self._create_default_config(local_json_path)

        # 3. 检查关键字段并应用兜底逻辑 (如 VIN, PNO)
        pno = str(config_data.get('ICC_PNO', DEFAULT_PNO)).strip()
        vin = str(config_data.get('VIN', DEFAULT_VIN)).strip()
        config_data['ICC_PNO'] = pno if pno.upper() != 'N/A' else DEFAULT_PNO
        config_data['VIN'] = vin if vin.upper() != 'N/A' else DEFAULT_VIN

        # 4. 写入本地 JSON 并计算 Hash
        try:
            with open(local_json_path, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, ensure_ascii=False, indent=4)
            config_data['FileHash'] = hashlib.sha256(local_json_path.read_bytes()).hexdigest()[:8]
        except Exception as e:
            config_data['FileHash'] = 'HASH_FAILED'
            self._log("配置", f"本地 JSON 文件写入失败: {e}", "ERROR")

        self.current_config = config_data
        self._log("配置", f"配置拉取完成。PNO={config_data['ICC_PNO']}", "SUCCESS")
        return self.current_config

    def push_config(self, new_pno: str, new_vin: str) -> Dict[str, Any]:
        """将 JSON 数据转为 Key-Value TXT 并推送"""
        if not self.serial: raise AdbException("设备未连接，无法推送配置。")

        # 1. 验证输入
        if not ConfigValidator.validate_icc_pno(new_pno)[0] or not ConfigValidator.validate_vin(new_vin)[0]:
             raise AdbException("PNO 或 VIN 格式验证失败。")

        # 2. 更新内存配置并生成临时 TXT
        new_config_data = self.current_config.copy()
        new_config_data['ICC_PNO'] = new_pno
        new_config_data['VIN'] = new_vin
        new_config_data.pop('FileHash', None)

        temp_txt_path = Path("temp_DeviceInfo_push.txt")
        try:
            with open(temp_txt_path, 'w', encoding='utf-8') as f:
                for key, value in new_config_data.items():
                    f.write(f"{key}={value}\n")
        except Exception as e:
            raise AdbException(f"生成临时 TXT 配置失败: {e}")

        # 3. 推送临时 TXT 文件
        self._log("配置", f"正在推送新配置 (PNO={new_pno}, VIN={new_vin}) 到远程 TXT 文件...", "WARNING")
        try:
            self.run_adb_command(["push", str(temp_txt_path), DEVICE_CONFIG_PATH_REMOTE], timeout=30)
            self._log("配置", "新 Key-Value 配置文件推送成功。", "SUCCESS")
        except AdbException as e:
            raise e
        finally:
            temp_txt_path.unlink(missing_ok=True)

        # 4. 推送成功后，重新拉取并返回最新配置
        return self.pull_config()

    # --- 工具箱操作 ---
    def reboot_device(self):
        """重启设备"""
        if not self.serial: raise AdbException("设备未连接，无法重启。")
        self._log("工具箱", "正在执行重启设备...", "WARNING")
        self.run_adb_command(["reboot"], timeout=5)
        self.serial = None
        self._log("工具箱", "设备重启命令已发送。", "SUCCESS")

    def clear_remote_logcat(self):
        """清理远程 Logcat 目录"""
        if not self.serial: raise AdbException("设备未连接，无法执行清理操作。")
        logcat_path_str = str(Path(REMOTE_LOG_PATH) / "logcat")
        self._log("工具箱", f"正在清理远程 Logcat 目录 {logcat_path_str}...", "WARNING")

        # 检查和清理放在一个 shell 命令中，避免多次连接
        self.run_adb_command(["shell", f"rm -rf {logcat_path_str}/*"], timeout=10)
        self._log("工具箱", "Logcat 目录清理完成。", "SUCCESS")

    def count_remote_logcat(self) -> int:
        """计数远程 Logcat 文件数量"""
        if not self.serial: return -1
        logcat_path_str = str(Path(REMOTE_LOG_PATH) / "logcat")
        count_cmd = ["shell", f"find {logcat_path_str} -type f | wc -l"]
        try:
            success, output, _ = self.run_adb_command(count_cmd, check_output=True, timeout=5)
            return int(output.strip().split()[-1]) if success and output.strip() else 0
        except AdbException:
            return -1
        except ValueError:
            return 0

    # --- 日志拉取操作 (略) ---
    async def start_pull_process(self, selected_logs: List[str], export_folder: str):
        # 这里的实现需要是异步友好的，或者在一个单独的线程池中运行。
        # 考虑到 ADB pull 是一个阻塞操作，通常在 API 层使用 ThreadPoolExecutor 运行此方法。
        # 简化实现，仅包含结构：
        self._log("拉取", f"开始日志拉取任务，目标目录: {export_folder}", "WARNING")
        # 假设这里有一个阻塞的 ADB pull 循环
        await asyncio.sleep(2) # 模拟任务准备时间
        self._log("拉取", "任务进度 1/5: logcat 拉取中...", "PROGRESS")
        await asyncio.sleep(1)
        self._log("拉取", "任务进度 5/5: 完成。", "SUCCESS")
        return {"status": "complete", "path": Path(export_folder) / "AdayoLog_..."}