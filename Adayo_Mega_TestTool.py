import sys
import subprocess
import datetime
import shutil
import time
import os
from pathlib import Path
import json
import re
import hashlib
import csv
import threading
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Set, Tuple, Iterator, Union, TextIO

# ========================================
# PySide6 导入
# ========================================
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QLineEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QProgressBar, QFileDialog, QGroupBox,
    QListWidget, QListWidgetItem, QMessageBox, QHeaderView, QTabWidget,
    QMenuBar, QMenu, QFrame, QTextEdit, QInputDialog, QComboBox, QSizePolicy,
)
from PySide6.QtCore import (
    QObject, QThread, Signal, Slot, Qt, QSize, QTimer, QCoreApplication
)
from PySide6.QtGui import (
    QColor, QPalette, QFont, QIcon, QAction, QTextCursor
)

# ========================================
# 1. 配置与元信息
# ========================================

TOOL_NAME = "Adayo 车载测试与配置集成平台"
VERSION = "1.0.21 (专业 Logcat 集成)" # <--- 版本更新
AUTHOR = "Jonas / Professional Automotive Engineer Team"
GITHUB_LINK = "dengzhu-hub"
COPYRIGHT = f"© 2024-{datetime.datetime.now().year} Adayo Mega Tool. All rights reserved."

# 持久化数据文件
DATA_FILE = "app_data.json"

# Log Puller 配置
LOG_TYPES = [
    "logcat", "anr", "setting", "systemproperty", "config", "kernel",
    "btsnoop", "tombstones", "dropbox", "resource", "mcu", "aee", "ael", "upgrade"
]
REMOTE_LOG_PATH = "/mnt/sdcard/AdayoLog"
WLAN_LOG_TYPE = "wlan_logs"
WLAN_LOG_PATH = "/data/vendor/wifi/wlan_logs"
ALL_LOG_TYPES = LOG_TYPES + [WLAN_LOG_TYPE]

# OTA Config 配置
DEVICE_CONFIG_PATH_REMOTE = "/mnt/sdcard/DeviceInfo.txt"
LOCAL_CONFIG_PATH_JSON = "DeviceInfo.json"

# --- Logcat 结构与常量 (从 logcat_calude_monitor.py 移植) ---
class LogLevel(Enum):
    FATAL = 'F'
    ERROR = 'E'
    WARN = 'W'
    INFO = 'I'
    DEBUG = 'D'
    VERBOSE = 'V'
    UNKNOWN = 'U'

@dataclass
class LogEntry:
    timestamp: datetime
    level: LogLevel
    pid: int
    tid: int
    tag: str
    message: str
    raw_line: str

# Logcat Parser Regex (adb logcat -v threadtime 格式)
LOGCAT_REGEX = re.compile(
    r'^(?P<month>\d{2})-(?P<day>\d{2})\s+'
    r'(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})\.(?P<millisecond>\d{3})\s+'
    r'(?P<pid>\d+)\s+(?P<tid>\d+)\s+'
    r'(?P<level>[A-Z])\s+'
    r'(?P<tag>[^:]*):\s+'
    r'(?P<message>.*)$'
)

# Logcat Table Columns
LOGCAT_COLUMNS = ["时间", "级别", "PID", "TID", "标签 (Tag)", "信息 (Message)"]
MAX_LIVE_LOG_ROWS = 5000 # 限制实时日志最大行数，防止内存溢出

# ========================================
# 2. 核心辅助类
# ========================================

class ConfigValidator:
    """配置验证器：负责VIN校验位计算和格式验证 (保持不变)"""
    @staticmethod
    def validate_vin(vin):
        if not vin or len(vin) != 17 or any(char in vin.upper() for char in ['I', 'O', 'Q']):
            return False, "VIN码格式不正确或包含非法字符(I, O, Q)"
        return True, "VIN码格式正确，校验位验证通过 (简化检查)"

    @staticmethod
    def validate_icc_pno(pno):
        if not pno or len(pno) < 5 or not pno.isalnum():
            return False, "ICC_PNO长度不能少于5位，且只能包含字母和数字"
        return True, "ICC_PNO格式正确"

# ========================================
# 3. Logcat 实时监控 Worker (新增)
# ========================================

class LogcatMonitorWorker(QObject):
    """
    运行在独立线程中的 ADB Logcat 实时监控 Worker。
    负责执行 adb logcat 命令、实时读取输出、解析日志行，并发送给 UI。
    """
    new_log_line_signal = Signal(LogEntry)
    status_signal = Signal(str)

    def __init__(self, serial: str):
        super().__init__()
        self.serial = serial
        self._running = False
        self._adb_process = None
        self.adb_path = "adb"
        self.logcat_command = ["logcat", "-v", "threadtime"] # 标准的 Logcat 命令

    def _parse_log_line(self, line: str) -> Optional[LogEntry]:
        """解析单行 Logcat 日志为 LogEntry 对象"""
        match = LOGCAT_REGEX.match(line)
        if match:
            try:
                data = match.groupdict()
                now = datetime.now()

                # 尝试构建 LogEntry 中的时间戳
                log_datetime = datetime(
                    now.year, # 使用当前年份
                    int(data['month']),
                    int(data['day']),
                    int(data['hour']),
                    int(data['minute']),
                    int(data['second']),
                    int(data['millisecond']) * 1000
                )

                level_char = data['level']
                level = LogLevel(level_char) if level_char in [l.value for l in LogLevel] else LogLevel.UNKNOWN

                return LogEntry(
                    timestamp=log_datetime,
                    level=level,
                    pid=int(data['pid']),
                    tid=int(data['tid']),
                    tag=data['tag'].strip(),
                    message=data['message'].strip(),
                    raw_line=line.strip()
                )
            except Exception:
                # 解析失败，返回 None
                pass

        return None

    @Slot()
    def start_monitor(self):
        """启动 ADB Logcat 实时监控进程"""
        if self._running or not self.serial:
            return

        self._running = True
        self.status_signal.emit("正在启动实时 Logcat...")

        # 1. 清除缓冲区 (异步执行，不等待结果)
        try:
            subprocess.run([self.adb_path, "-s", self.serial, "logcat", "-c"],
                           capture_output=True, text=True, check=False, timeout=5)
        except Exception:
            pass # 忽略清理失败

        # 2. 启动新的 ADB Logcat 进程
        try:
            command = [self.adb_path, "-s", self.serial] + self.logcat_command
            self.status_signal.emit(f"执行命令: {' '.join(command)}")

            self._adb_process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, # 捕捉错误输出，避免进程异常
                universal_newlines=True,
                encoding='utf-8',
                bufsize=1 # 行缓冲
            )
            self.status_signal.emit("实时 Logcat 启动成功，开始接收数据。")

            # 3. 实时读取输出
            for line in iter(self._adb_process.stdout.readline, ''):
                if not self._running:
                    break

                parsed_entry = self._parse_log_line(line)
                if parsed_entry:
                    self.new_log_line_signal.emit(parsed_entry)

        except FileNotFoundError:
            self.status_signal.emit("ADB 工具未找到。请检查 PATH 配置。")
        except Exception as e:
            if self._running: # 只有在运行时出错才报告
                self.status_signal.emit(f"Logcat 监控启动失败: {e}")
        finally:
            self._running = False
            self.stop_monitor()

    @Slot()
    def stop_monitor(self):
        """停止 ADB Logcat 进程"""
        if not self._running:
            return

        self._running = False
        if self._adb_process:
            self.status_signal.emit("正在停止 Logcat 进程...")
            try:
                # 尝试发送终止信号
                self._adb_process.terminate()
                self._adb_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                # 超时则杀死进程
                try:
                    self._adb_process.kill()
                    self._adb_process.wait(timeout=1)
                except Exception:
                    pass
            except Exception:
                 pass

            self._adb_process = None
            self.status_signal.emit("Logcat 进程已清理。")

# ========================================
# 4. 核心逻辑 (CoreToolLogic)
# ========================================

class CoreToolLogic(QObject):
    """
    包含所有 ADB、文件操作、配置更新和日志拉取的核心逻辑。
    使用 QThread 运行，确保 UI 响应。
    """
    # 信号定义
    device_connected_signal = Signal(str)
    device_disconnected_signal = Signal()
    device_status_signal = Signal(str, str)
    task_start_signal = Signal(int)
    task_progress_signal = Signal(int, str, str, str)
    task_complete_signal = Signal(dict, str)
    error_signal = Signal(str)
    remote_logcat_count_signal = Signal(int)
    log_signal = Signal(str, str, str)
    config_pulled_signal = Signal(dict)
    operation_success_signal = Signal(str, str)
    screenshot_complete_signal = Signal(str, str)

    # --- Live Logcat 信号 (新增) ---
    live_monitor_status_signal = Signal(str)

    # --- JSON 默认配置常量 ---
    DEFAULT_PNO = "ADAYO_DEFAULT_JSON"
    DEFAULT_VIN = "VINDEMO123456789012"

    def __init__(self):
        super().__init__()
        self.serial = None
        self.export_path = str(Path.cwd() / "CarLogs")
        self.selected_logs = ALL_LOG_TYPES
        self.is_pulling_logs = False
        self.is_running_tool = False
        self.current_config = {}

    # --- 基础 ADB 操作 (保持不变) ---
    def run_adb_command(self, command: list, check_output: bool = False, timeout=120):
        serial = self.serial
        if serial:
            command = ["adb", "-s", serial] + command
        else:
            command = ["adb"] + command

        try:
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

            if check_output:
                return result.returncode == 0, output, error

            return result.returncode == 0, output, error

        except FileNotFoundError:
            self.error_signal.emit("ADB tool not found. 请确保 ADB 在系统 PATH 中。")
            return False, "", "ADB not found"
        except subprocess.TimeoutExpired:
            # 对于截图操作，超时是很常见的，需要特别处理
            is_timeout = True
            try:
                # 尝试杀死子进程及其子进程
                pass
            except Exception:
                pass

            self.error_signal.emit(f"命令超时: {' '.join(command)}")
            return False, "", "Timeout"
        except Exception as e:
            self.error_signal.emit(f"ADB 执行失败: {e}")
            return False, "", str(e)


    # --- Log 计数辅助方法 (保持不变) ---
    def count_remote_files(self, remote_path: str) -> int:
        """Helper to count files in a remote directory."""
        if not self.serial:
            return -1

        # 使用 ls -1 | wc -l 统计文件数
        ls_cmd = ["shell", f"ls -1 {remote_path} | wc -l"]
        success, output, _ = self.run_adb_command(ls_cmd, timeout=5)

        if success and output.strip().isdigit():
            return int(output.strip())

        # Fallback: 检查目录是否存在 ('test -d' 是更可靠的目录存在性检查)
        check_dir_cmd = ["shell", f"test -d {remote_path} && echo 'Exists' || echo 'NotExists'"]
        _, dir_output, _ = self.run_adb_command(check_dir_cmd, timeout=5)

        if dir_output.strip() == 'Exists':
            # 目录存在但计数失败或返回 0，假设 0 文件
            return 0

        # 目录不存在或严重错误
        return -1

    @Slot()
    def count_remote_logcat(self):
        """Counts the number of logcat files in the remote logcat directory."""
        if not self.serial:
            self.remote_logcat_count_signal.emit(-1)
            return

        logcat_path_str = str(Path(REMOTE_LOG_PATH) / "logcat")
        count = self.count_remote_files(logcat_path_str)

        self.remote_logcat_count_signal.emit(count)


    # --- 设备状态监控 (保持不变) ---
    @Slot()
    def check_device_and_root(self):
        self.device_status_signal.emit("正在检查设备连接...", "yellow")

        success, output, _ = self.run_adb_command(["devices"], check_output=True, timeout=5)
        devices = []
        if success and output:
            lines = output.split('\n')
            for line in lines[1:]:
                if line.strip() and "device" in line and "unauthorized" not in line and "emulator" not in line:
                    serial = line.split('\t')[0]
                    devices.append(serial)

        if len(devices) != 1:
            self.device_status_signal.emit("错误: 未找到单个已连接设备。", "red")
            self.serial = None
            self.remote_logcat_count_signal.emit(-1)
            self.device_disconnected_signal.emit()
            return

        self.serial = devices[0]
        self.device_connected_signal.emit(self.serial)

        self.device_status_signal.emit(f"设备已连接 ({self.serial})，尝试 Root/Remount...", "yellow")
        self.run_adb_command(["root"], timeout=10)
        time.sleep(1)

        remount_success, _, _ = self.run_adb_command(["remount"], timeout=5)

        if remount_success:
            self.device_status_signal.emit(f"连接成功 ({self.serial})，权限已增强。", "green")
        else:
            self.device_status_signal.emit(f"连接成功 ({self.serial})，Remount 失败。", "yellow")

        self.pull_config_file()
        self.count_remote_logcat()

    @Slot()
    def monitor_device_status(self):
        success, output, _ = self.run_adb_command(["devices"], check_output=True, timeout=5)
        current_devices = []
        if success and output:
            lines = output.split('\n')
            for line in lines[1:]:
                if line.strip() and "device" in line and "unauthorized" not in line and "emulator" not in line:
                    current_devices.append(line.split('\t')[0])

        if self.serial:
            if self.serial not in current_devices:
                self.serial = None
                self.device_disconnected_signal.emit()
            else:
                self.device_status_signal.emit(f"连接成功 ({self.serial})", "green")
                self.count_remote_logcat()

        elif not self.serial:
            if len(current_devices) == 1:
                self.check_device_and_root()
            elif len(current_devices) == 0:
                self.device_status_signal.emit("错误: 未找到单个已连接设备。", "red")
                self.remote_logcat_count_signal.emit(-1)
            else:
                self.device_status_signal.emit("错误: 发现多个设备，请断开多余设备。", "red")

    # --- 配置操作 (保持不变) ---
    def _create_default_config(self, local_path: Path) -> dict:
        """创建包含默认值的本地 JSON 配置文件"""
        default_config = {
            'ICC_PNO': self.DEFAULT_PNO,
            'VIN': self.DEFAULT_VIN,
            'FOTA_VERSION': '0000',
            'VEHICLE_TYPE': 'DEMO',
            'TIMESTAMP': datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        }

        try:
            # 使用 JSON 格式写入文件
            with open(local_path, 'w', encoding='utf-8') as f:
                json.dump(default_config, f, ensure_ascii=False, indent=4)

            self.log_signal.emit("配置", f"已在本地创建默认 JSON 配置文件: {local_path.name}", "INFO")
        except Exception as e:
            self.log_signal.emit("配置", f"创建默认本地 JSON 配置文件失败: {e}", "ERROR")

        return default_config

    def _parse_remote_config(self, local_txt_path: Path) -> dict:
        """读取本地 TXT 文件，尝试 JSON 解析，失败则回退到 Key-Value TXT 解析"""
        config_data = {}
        try:
            content = local_txt_path.read_text(encoding='utf-8').strip()

            if not content:
                self.log_signal.emit("配置", "TXT 文件内容为空，无法解析。", "WARNING")
                return {}

            # ** 优先级 1: 尝试 JSON 解析 **
            if (content.startswith('{') and content.endswith('}')) or (content.startswith('[') and content.endswith(']')):
                try:
                    config_data = json.loads(content)
                    self.log_signal.emit("配置", "成功以 JSON 格式解析远程配置。", "SUCCESS")
                    return config_data
                except json.JSONDecodeError:
                    self.log_signal.emit("配置", "JSON 解析失败，回退到 Key-Value 解析...", "WARNING")

            # ** 优先级 2: Key-Value TXT 解析 **
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith('#'):
                    continue

                if '=' in line:
                    key, value = line.split('=', 1)
                    config_data[key.strip()] = value.strip()

            if not config_data:
                self.log_signal.emit("配置", "Key-Value 解析失败，未找到有效配置。", "WARNING")

            return config_data

        except Exception as e:
            self.error_signal.emit(f"本地配置文本文件解析失败: {e}")
            self.log_signal.emit("配置", f"解析失败: {e}", "ERROR")
            return {}

    @Slot()
    def pull_config_file(self):
        if not self.serial:
            self.error_signal.emit("设备未连接，无法拉取配置。")
            self.config_pulled_signal.emit({})
            return

        self.log_signal.emit("配置", "正在拉取设备配置 (远程 TXT -> 本地 JSON)...", "WARNING")

        # 1. 定义本地临时 TXT 路径 和 本地最终 JSON 路径
        local_txt_path = Path("temp_DeviceInfo.txt")
        local_json_path = Path(LOCAL_CONFIG_PATH_JSON)

        # 尝试清理旧的 JSON 文件
        if local_json_path.exists():
            local_json_path.unlink()

        # 2. 尝试拉取远程 TXT 文件到本地临时路径
        success, output, error = self.run_adb_command(["pull", DEVICE_CONFIG_PATH_REMOTE, str(local_txt_path)], timeout=30)

        config_data = {}

        if success and local_txt_path.exists():
            self.log_signal.emit("配置", f"远程 TXT 文件拉取成功，开始解析配置。", "SUCCESS")

            # 3. 解析 Key-Value/JSON (TXT) 到字典
            config_data = self._parse_remote_config(local_txt_path)

            # 4. 检查解析结果并应用兜底逻辑
            if not config_data:
                 self.error_signal.emit("TXT 文件内容为空或解析失败。已创建默认配置。")
                 config_data = self._create_default_config(local_json_path)
            else:
                # 检查关键字段是否为空或 N/A
                pno = str(config_data.get('ICC_PNO', 'N/A')).strip()
                vin = str(config_data.get('VIN', 'N/A')).strip()

                if not pno or pno.upper() == 'N/A':
                    self.log_signal.emit("配置", f"警告: 远程配置中 ICC_PNO 缺失或为空，将使用默认值 {self.DEFAULT_PNO}。", "WARNING")
                    config_data['ICC_PNO'] = self.DEFAULT_PNO

                if not vin or vin.upper() == 'N/A':
                    self.log_signal.emit("配置", f"警告: 远程配置中 VIN 缺失或为空，将使用默认值 {self.DEFAULT_VIN}。", "WARNING")
                    config_data['VIN'] = self.DEFAULT_VIN

            # 5. 清理临时 TXT 文件
            local_txt_path.unlink()

        else:
            # 拉取命令失败 (文件不存在，权限问题等)
            self.error_signal.emit(f"拉取远程配置文件失败，已在本地创建默认 JSON 配置文件。错误: {error}")
            self.log_signal.emit("配置", f"拉取配置文件失败: {error}", "ERROR")
            config_data = self._create_default_config(local_json_path)

        # 6. 将最终 (可能已修正或默认) 配置保存为本地 JSON 文件
        self.current_config = config_data
        try:
            # 确保保存为本地 JSON 文件
            with open(local_json_path, 'w', encoding='utf-8') as f:
                json.dump(self.current_config, f, ensure_ascii=False, indent=4)

            # 计算哈希值
            config_hash = hashlib.sha256(local_json_path.read_bytes()).hexdigest()[:8]
            self.current_config['FileHash'] = config_hash
        except Exception as e:
            self.current_config['FileHash'] = 'HASH_FAILED'
            self.error_signal.emit(f"本地 JSON 文件写入失败: {e}")

        self.device_status_signal.emit(f"连接成功 ({self.serial})，配置已读取。", "green")
        self.log_signal.emit("配置", f"当前设备配置 ICC_PNO={self.current_config.get('ICC_PNO')}", "INFO")

        self.config_pulled_signal.emit(self.current_config)

    @Slot(str, str)
    def push_config_file(self, new_pno: str, new_vin: str):
        if not self.serial:
            self.error_signal.emit("设备未连接，无法推送配置。")
            return

        self.log_signal.emit("配置", "正在生成并推送新的 Key-Value 配置文件...", "WARNING")

        temp_txt_path = Path("temp_DeviceInfo.txt")
        new_config_data = self.current_config.copy()

        # 更新关键字段
        new_config_data['ICC_PNO'] = new_pno
        new_config_data['VIN'] = new_vin

        new_config_data.pop('FileHash', None) # 移除哈希字段，不对设备推送

        try:
            # 将配置转换回 Key-Value (TXT) 格式写入临时文件
            with open(temp_txt_path, 'w', encoding='utf-8') as f:
                for key, value in new_config_data.items():
                    f.write(f"{key}={value}\n")

        except Exception as e:
            self.error_signal.emit(f"生成本地临时 TXT 配置失败: {e}")
            return

        success, output, error = self.run_adb_command(["push", str(temp_txt_path), DEVICE_CONFIG_PATH_REMOTE], timeout=30)

        temp_txt_path.unlink() # 清理临时文件

        if success:
            self.log_signal.emit("配置", "新 Key-Value 配置文件推送成功。", "SUCCESS")
            self.operation_success_signal.emit("OTA配置更新", f"成功更新 PNO={new_pno}, VIN={new_vin}")
            self.pull_config_file() # 推送成功后再次拉取，更新UI
        else:
            self.error_signal.emit(f"推送 Key-Value 配置文件失败: {error}")
            self.log_signal.emit("配置", f"推送 Key-Value 配置文件失败: {error}", "ERROR")

    # --- 日志拉取和工具箱操作 (保持不变) ---
    @Slot(list, str)
    def start_pull_process(self, selected_logs: list, export_folder: str):
        if not self.serial or not export_folder:
            self.error_signal.emit("设备未连接或导出路径未设置。")
            return
        if self.is_pulling_logs:
            self.error_signal.emit("日志拉取任务正在运行中。")
            return

        self.is_pulling_logs = True
        self.device_status_signal.emit("任务进行中...", "blue")
        self.log_signal.emit("拉取", f"开始日志拉取任务，目标目录: {export_folder}", "WARNING")

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        export_path = Path(export_folder) / f"AdayoLog_{timestamp}"
        export_path.mkdir(parents=True, exist_ok=True)

        tasks = []
        for log_type in LOG_TYPES:
            if log_type in selected_logs:
                tasks.append((log_type, f"{REMOTE_LOG_PATH}/{log_type}", export_path / log_type))
        if WLAN_LOG_TYPE in selected_logs:
            tasks.append((WLAN_LOG_TYPE, WLAN_LOG_PATH, export_path / WLAN_LOG_TYPE))

        total_tasks = len(tasks)
        if total_tasks == 0:
            self.error_signal.emit("未选择任何日志类型。")
            self.device_status_signal.emit(f"连接成功 ({self.serial})", "green")
            self.is_pulling_logs = False
            return

        self.task_start_signal.emit(total_tasks)

        results_summary = []
        total_files_pulled = 0
        total_fail = 0

        for i, (log_type, remote_path, local_target) in enumerate(tasks):
            i += 1
            if not self.serial:
                self.error_signal.emit(f"设备在任务 [{log_type}] 期间断开连接，任务中止。")
                self.device_disconnected_signal.emit()
                self.is_pulling_logs = False
                return

            self.task_progress_signal.emit(i, log_type, "拉取中...", "N/A")

            # ADB Pull 命令
            if log_type == WLAN_LOG_TYPE:
                # ADB pull /data/vendor/wifi/wlan_logs /local/AdayoLog_ts 会在本地生成 /local/AdayoLog_ts/wlan_logs 目录
                pull_cmd = ["pull", remote_path, str(export_path)]
            else:
                pull_cmd = ["pull", remote_path, str(local_target)]

            success, output, error = self.run_adb_command(pull_cmd, timeout=600)

            is_success = success and "pull failed" not in output.lower()
            file_count = 0
            status_text = "失败"

            if is_success:
                # 确定最终的本地路径，以便计数
                if log_type == WLAN_LOG_TYPE:
                    final_local_path = export_path / "wlan_logs"
                else:
                    final_local_path = local_target

                if final_local_path.exists():
                    file_count = sum(1 for item in final_local_path.rglob('*') if item.is_file())

                if file_count > 0:
                    status_text = "成功"
                    total_files_pulled += 1
                else:
                    status_text = "空目录"
                    # 清理空目录，保持输出整洁
                    if final_local_path.is_dir():
                        try: shutil.rmtree(final_local_path)
                        except OSError: pass
            else:
                status_text = "失败"
                total_fail += 1

            file_count_str = f"{file_count} 个文件" if file_count > 0 else ("已清理" if status_text == "空目录" else "N/A")
            self.task_progress_signal.emit(i, log_type, status_text, file_count_str)
            results_summary.append({'log_type': log_type, 'status': status_text, 'files': file_count})

        self.is_pulling_logs = False
        self.device_status_signal.emit(f"连接成功 ({self.serial})", "green")
        summary = {
            'total_files_pulled': total_files_pulled,
            'total_fail': total_fail,
            'results': results_summary
        }
        self.task_complete_signal.emit(summary, str(export_path))

        if summary['total_files_pulled'] > 0:
            self.operation_success_signal.emit("日志拉取", f"完成: 成功拉取 {summary['total_files_pulled']} 项日志。")


    @Slot()
    def clear_logcat(self):
        if not self.serial:
            self.error_signal.emit("设备未连接或已断开，无法执行清理操作。")
            return

        logcat_path_str = str(Path(REMOTE_LOG_PATH) / "logcat")
        files_before = self.count_remote_files(logcat_path_str)
        if files_before < 0:
            self.error_signal.emit(f"清理 Logcat 失败: 无法访问目录 {logcat_path_str}。")
            return

        self.device_status_signal.emit(f"正在执行 Logcat 清理 ({files_before} -> 0)...", "blue")
        clear_cmd = ["shell", f"rm -rf {logcat_path_str}/*"]
        success, _, error = self.run_adb_command(clear_cmd)

        if success:
            files_after = self.count_remote_files(logcat_path_str)
            self.count_remote_logcat() # 立即更新计数
            if files_after == 0:
                self.device_status_signal.emit(f"Logcat 清理成功 ({self.serial})", "green")
            else:
                self.device_status_signal.emit(f"Logcat 清理警告 ({self.serial})", "yellow")
        else:
            self.error_signal.emit(f"Logcat 清理失败: {error}")

    @Slot()
    def reboot_device(self):
        if not self.serial:
            self.error_signal.emit("设备未连接，无法重启。")
            return
        self.log_signal.emit("工具箱", "正在执行重启设备...", "WARNING")

        success, _, error = self.run_adb_command(["reboot"], timeout=5)

        if success:
            self.log_signal.emit("工具箱", "设备重启命令已发送，请等待重新连接...", "SUCCESS")
            self.serial = None
            self.device_disconnected_signal.emit()
        else:
            self.error_signal.emit(f"重启设备失败: {error}")

    # --- 截图核心逻辑 (保持不变) ---

    def _screenshot_cycle_helper(self, timestamp: str, export_folder: str, prefix: str = "") -> tuple[bool, str]:
        """执行单次截图、拉取、清理的原子操作，返回成功状态和本地路径。"""
        filename = f"screenshot_{prefix}{timestamp}.png" if prefix else f"screenshot_{timestamp}.png"
        # 远程路径 (使用 /sdcard/Download 作为临时保存位置，确保权限)
        remote_path = f"/sdcard/Download/{filename}"
        # 本地路径 (保存到 Logs 根目录下的 Screenshots 子文件夹)
        local_path = Path(export_folder) / "Screenshots" / filename
        local_path.parent.mkdir(parents=True, exist_ok=True)

        # 1. Capture screen (生成远程文件)
        capture_success, _, capture_error = self.run_adb_command(["shell", "screencap", "-p", remote_path], timeout=30)

        if not capture_success:
            self.log_signal.emit("截图", f"截图失败 (screencap): {capture_error}", "ERROR")
            # 尝试清理可能存在的空文件或权限问题
            self.run_adb_command(["shell", "rm", remote_path], timeout=5)
            return False, ""

        # 2. Pull file (拉取到本地)
        pull_success, _, pull_error = self.run_adb_command(["pull", remote_path, str(local_path)], timeout=60)

        # 3. Remove remote file (清理远程设备，防止文件堆积)
        self.run_adb_command(["shell", "rm", remote_path], timeout=5)

        if not pull_success:
            self.log_signal.emit("截图", f"文件拉取失败 (pull): {pull_error}", "ERROR")
            return False, ""

        # 使用 resolve() 确保路径是绝对路径
        return True, str(local_path.resolve())

    @Slot(str, int, int, int, str)
    def start_screenshot_task(self, mode: str, delay: int, count: int, interval: int, export_folder: str):
        """
        根据模式启动截图任务 (单次, 延时, 批量)。
        """
        if not self.serial:
            self.error_signal.emit("设备未连接，无法执行截图任务。")
            return

        # 1. 单次即时截图
        if mode == 'single':
            self.log_signal.emit("截图", "正在执行单次即时截图...", "WARNING")
            self.device_status_signal.emit("正在截图...", "blue")
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            success, local_path = self._screenshot_cycle_helper(timestamp, export_folder)
            self.device_status_signal.emit(f"连接成功 ({self.serial})", "green")
            if success:
                self.screenshot_complete_signal.emit("SUCCESS", local_path)
            else:
                self.error_signal.emit("单次即时截图失败，请检查设备连接或权限。")
            return

        # 2. 延时截图
        elif mode == 'delay':
            self.log_signal.emit("截图", f"开始延时截图任务，延迟 {delay} 秒...", "WARNING")
            self.device_status_signal.emit(f"等待 {delay} 秒...", "blue")
            time.sleep(delay)
            self.device_status_signal.emit("正在截图...", "blue")

            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            success, local_path = self._screenshot_cycle_helper(timestamp, export_folder, prefix="delay_")
            self.device_status_signal.emit(f"连接成功 ({self.serial})", "green")
            if success:
                self.screenshot_complete_signal.emit("SUCCESS", local_path)
            else:
                self.error_signal.emit("延时截图失败，请检查设备连接或权限。")
            return

        # 3. 批量间隔截图
        elif mode == 'batch':
            self.log_signal.emit("截图", f"开始批量截图任务: {count} 次，间隔 {interval} 秒。", "WARNING")
            self.task_start_signal.emit(count) # 使用 Log Puller 的进度条

            successful_count = 0

            for i in range(1, count + 1):
                if not self.serial:
                    self.error_signal.emit("设备断开连接，批量任务中止。")
                    break

                self.task_progress_signal.emit(i, f"批量截图 {i}/{count}", "拉取中...", "N/A")
                self.device_status_signal.emit(f"批量截图 {i}/{count} 正在进行...", "blue")

                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                success, local_path = self._screenshot_cycle_helper(timestamp, export_folder, prefix=f"batch_{i:03d}_")

                status_text = "成功" if success else "失败"
                if success:
                    successful_count += 1

                self.task_progress_signal.emit(i, f"批量截图 {i}/{count}", status_text, local_path.split(os.sep)[-1])

                if i < count:
                    self.log_signal.emit("截图", f"等待 {interval} 秒进行下一次截图...", "INFO")
                    time.sleep(interval)

            self.device_status_signal.emit(f"连接成功 ({self.serial})", "green")

            summary = {
                'total_files_pulled': successful_count,
                'total_fail': count - successful_count,
                'results': []
            }
            self.task_complete_signal.emit(summary, str(Path(export_folder) / "Screenshots"))

            if successful_count > 0:
                self.operation_success_signal.emit("批量截图", f"完成: 成功截图 {successful_count} 张。")
                self.screenshot_complete_signal.emit("BATCH_SUCCESS", f"成功截图 {successful_count} 张，保存至 {Path(export_folder) / 'Screenshots'}")
            else:
                self.error_signal.emit("批量截图任务失败，未能保存任何文件。")

        else:
            self.error_signal.emit(f"不支持的截图模式: {mode}")


# ========================================
# 5. 主窗口 UI (AdayoMegaTool)
# ========================================

class AdayoMegaTool(QMainWindow):
    check_device_signal = Signal()
    start_pull_signal = Signal(list, str)
    clear_logcat_signal = Signal()
    reboot_signal = Signal()
    push_config_signal = Signal(str, str)
    start_screenshot_signal = Signal(str, int, int, int, str)

    # --- Live Logcat 信号 (新增) ---
    start_live_logcat_signal = Signal()
    stop_live_logcat_signal = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle(TOOL_NAME)
        self.setGeometry(100, 100, 1400, 900)

        self.export_folder = str(Path.cwd() / "AdayoMegaLogs")

        # 统计数据初始化
        self.stats_ota_count_value = 0
        self.stats_log_count_value = 0
        self.history_records = []
        self.log_count = -1
        self.current_pno = "N/A"
        self.current_vin = "N/A"
        self.current_hash = "N/A"
        self.config_templates = {}

        # Logcat Live Monitor State (新增)
        self.logcat_thread: Optional[QThread] = None
        self.logcat_worker: Optional[LogcatMonitorWorker] = None
        self.logcat_total_lines = 0 # 总接收行数
        self.logcat_displayed_lines = 0 # 过滤后显示行数
        self.logcat_filter_criteria = {
            'min_level': LogLevel.VERBOSE,
            'tag_regex': None,
            'msg_regex': None,
            'pid_tid': None
        }

        # 2. 设置逻辑线程
        self._setup_logic_thread()

        # 3. 核心 UI 设置
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QVBoxLayout(self.central_widget)
        self.main_layout.setContentsMargins(5, 5, 5, 5)

        self._setup_menu_bar()
        self._setup_top_status_panel(self.main_layout)
        self._setup_tab_content(self.main_layout)
        self._setup_log_viewer(self.main_layout)
        self._setup_footer(self.main_layout)

        # 4. 加载数据
        self._load_app_data()

        # 5. 设置定时器
        self.main_layout.setStretch(1, 1)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._update_time_and_status)
        self.timer.start(1000)

        self.device_monitor_timer = QTimer(self)
        self.device_monitor_timer.timeout.connect(self.logic.monitor_device_status)
        self.device_monitor_timer.start(5000)

        # 6. 立即更新 UI 状态
        self._update_stats_ui()
        self._update_history_ui()
        self._update_template_ui()
        self.on_config_pulled(self.logic.current_config)

        QTimer.singleShot(100, self.check_device_signal.emit)


    # --- UI 结构构建方法 (新增 Logcat Tab) ---

    def _setup_tab_content(self, parent_layout):
        self.tab_widget = QTabWidget()
        self.tab_widget.addTab(self._create_home_panel(), "🏠 首页概览")
        self.tab_widget.addTab(self._create_ota_config_tab(), "🔧 OTA 配置")
        self.tab_widget.addTab(self._create_log_puller_tab(), "📑 日志拉取")
        self.tab_widget.addTab(self._create_logcat_monitor_tab(), "📊 实时 Logcat 监控") # <--- 新增 Logcat 监控 Tab
        self.tab_widget.addTab(self._create_toolbox_tab(), "🛠️ 调试工具箱")
        self.tab_widget.addTab(self._create_history_data_tab(), "⚡ 操作与数据")
        parent_layout.addWidget(self.tab_widget)

    # ... 其他 UI setup 方法保持不变 ...
    def _setup_top_status_panel(self, parent_layout):
        # ... (保持不变)
        top_frame = QFrame()
        top_frame.setFrameShape(QFrame.Shape.StyledPanel)
        top_layout = QHBoxLayout(top_frame)

        # 1. 设备状态
        device_status_layout = QVBoxLayout()
        self.serial_label = QLabel("序列号: N/A")
        self.logcat_count_label = QLabel("📦 远程 Logcat 文件数: N/A")
        device_status_layout.addWidget(self.serial_label)
        device_status_layout.addWidget(self.logcat_count_label)
        top_layout.addLayout(device_status_layout)

        # 2. 状态指示
        status_layout = QVBoxLayout()
        self.status_label = QLabel("正在初始化...")
        self.status_label.setFont(QFont("Microsoft YaHei UI", 12))
        self.status_indicator = QLabel("●")
        self.status_indicator.setFont(QFont("Arial", 18, QFont.Bold))
        self.status_indicator.setStyleSheet("color: gray;")

        status_hbox = QHBoxLayout()
        status_hbox.addWidget(self.status_indicator)
        status_hbox.addWidget(self.status_label)
        status_hbox.setAlignment(Qt.AlignmentFlag.AlignLeft)

        status_layout.addLayout(status_hbox)

        self.datetime_label = QLabel("📅 实时时间: N/A")
        status_layout.addWidget(self.datetime_label)
        top_layout.addLayout(status_layout)

        top_layout.addStretch()

        parent_layout.addWidget(top_frame)

    def _setup_log_viewer(self, parent_layout):
        log_group = QGroupBox("📜 实时日志输出")
        log_group.setFixedHeight(200)
        log_layout = QVBoxLayout(log_group)
        self.log_text_edit = QTextEdit()
        self.log_text_edit.setReadOnly(True)
        log_layout.addWidget(self.log_text_edit)
        parent_layout.addWidget(log_group)

    def _setup_menu_bar(self):
        # ... (保持不变)
        menu_bar = self.menuBar()

        # 文件菜单
        file_menu = menu_bar.addMenu("文件")

        exit_action = QAction(QIcon.fromTheme("application-exit"), "退出", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # 帮助菜单
        help_menu = menu_bar.addMenu("帮助")

        about_action = QAction("关于", self)
        about_action.triggered.connect(self.show_about_dialog)
        help_menu.addAction(about_action)

    def _setup_footer(self, parent_layout):
        # ... (保持不变)
        footer_layout = QHBoxLayout()
        footer_label = QLabel(f"{COPYRIGHT} | {TOOL_NAME} {VERSION}")
        footer_label.setStyleSheet("color: gray; font-size: 8pt;")

        self.github_link = QLabel(f"[GitHub: {GITHUB_LINK}]")
        self.github_link.setOpenExternalLinks(True)
        self.github_link.setStyleSheet("color: blue; font-size: 8pt;")

        footer_layout.addWidget(footer_label)
        footer_layout.addStretch()
        footer_layout.addWidget(self.github_link)

        parent_layout.addLayout(footer_layout)

    def _create_home_panel(self):
        # ... (保持不变)
        tab = QWidget()
        layout = QGridLayout(tab)
        layout.setContentsMargins(20, 20, 20, 20)

        config_group = QGroupBox("💻 当前设备配置概览")
        config_group.setStyleSheet("font-weight: bold; color: #007bff;")
        config_layout = QGridLayout(config_group)

        config_layout.addWidget(QLabel("ICC_PNO:"), 0, 0)
        self.home_pno_label = QLabel(self.current_pno)
        self.home_pno_label.setFont(QFont("Consolas", 14, QFont.Bold))
        config_layout.addWidget(self.home_pno_label, 0, 1)

        config_layout.addWidget(QLabel("VIN:"), 1, 0)
        self.home_vin_label = QLabel(self.current_vin)
        self.home_vin_label.setFont(QFont("Consolas", 14, QFont.Bold))
        config_layout.addWidget(self.home_vin_label, 1, 1)

        config_layout.addWidget(QLabel("配置 Hash (8位):"), 2, 0)
        self.home_hash_label = QLabel(self.current_hash)
        self.home_hash_label.setFont(QFont("Consolas", 10))
        config_layout.addWidget(self.home_hash_label, 2, 1)

        config_layout.setColumnStretch(1, 1)
        layout.addWidget(config_group, 0, 0, 1, 1)

        stats_group = QGroupBox("📈 平台测试统计")
        stats_group.setStyleSheet("font-weight: bold; color: #28a745;")
        stats_layout = QGridLayout(stats_group)

        stats_layout.addWidget(QLabel("OTA 成功更新次数:"), 0, 0)
        self.stats_ota_count = QLabel(str(self.stats_ota_count_value))
        self.stats_ota_count.setFont(QFont("Consolas", 16, QFont.Bold))
        stats_ota_count_widget = QWidget()
        stats_ota_count_layout = QHBoxLayout(stats_ota_count_widget)
        stats_ota_count_layout.setContentsMargins(0, 0, 0, 0)
        stats_ota_count_layout.addWidget(self.stats_ota_count, alignment=Qt.AlignmentFlag.AlignRight)
        stats_layout.addWidget(stats_ota_count_widget, 0, 1)

        stats_layout.addWidget(QLabel("日志拉取成功次数:"), 1, 0)
        self.stats_log_count = QLabel(str(self.stats_log_count_value))
        self.stats_log_count.setFont(QFont("Consolas", 16, QFont.Bold))
        stats_log_count_widget = QWidget()
        stats_log_count_layout = QHBoxLayout(stats_log_count_widget)
        stats_log_count_layout.setContentsMargins(0, 0, 0, 0)
        stats_log_count_layout.addWidget(self.stats_log_count, alignment=Qt.AlignmentFlag.AlignRight)
        stats_layout.addWidget(stats_log_count_widget, 1, 1)

        stats_layout.setColumnStretch(0, 1)
        layout.addWidget(stats_group, 0, 1, 1, 1)

        quick_action_group = QGroupBox("🚀 快速操作与导航")
        quick_action_group.setStyleSheet("font-weight: bold; color: #ffc107;")
        quick_layout = QGridLayout(quick_action_group)

        btn_config = self._create_quick_button("🔧 跳转：配置更新", lambda: self.tab_widget.setCurrentIndex(1))
        btn_log = self._create_quick_button("📑 跳转：日志拉取", lambda: self.tab_widget.setCurrentIndex(2))
        btn_reboot = self._create_quick_button("🔁 一键重启车机 (ADB)", self._reboot_device)
        btn_clear_logcat = self._create_quick_button("🧹 清理远程 Logcat", self._clear_remote_logcat)

        quick_layout.addWidget(btn_config, 0, 0)
        quick_layout.addWidget(btn_log, 0, 1)
        quick_layout.addWidget(btn_reboot, 1, 0)
        quick_layout.addWidget(btn_clear_logcat, 1, 1)

        quick_layout.setRowStretch(2, 1)
        quick_layout.setColumnStretch(0, 1)
        quick_layout.setColumnStretch(1, 1)

        layout.addWidget(quick_action_group, 1, 0, 1, 2)

        layout.setRowStretch(1, 1)
        return tab

    def _create_quick_button(self, text, slot):
        # ... (保持不变)
        btn = QPushButton(text)
        btn.setFont(QFont("Microsoft YaHei UI", 12, QFont.Bold))
        btn.setMinimumHeight(60)
        btn.clicked.connect(slot)
        return btn

    def _create_ota_config_tab(self):
        # ... (保持不变)
        tab = QWidget()
        main_layout = QVBoxLayout(tab)

        current_config_group = QGroupBox("⚙️ 当前设备配置 (本地 JSON 格式)")
        config_layout = QGridLayout(current_config_group)

        self.current_pno_label = QLabel(self.current_pno)
        self.current_vin_label = QLabel(self.current_vin)
        self.current_hash_label = QLabel(self.current_hash)

        config_layout.addWidget(QLabel("ICC_PNO:"), 0, 0)
        config_layout.addWidget(self.current_pno_label, 0, 1)

        config_layout.addWidget(QLabel("VIN:"), 1, 0)
        config_layout.addWidget(self.current_vin_label, 1, 1)

        config_layout.addWidget(QLabel("文件哈希 (8位):"), 2, 0)
        config_layout.addWidget(self.current_hash_label, 2, 1)

        main_layout.addWidget(current_config_group)

        single_update_group = QGroupBox("🔧 单次配置更新 (将推送 Key-Value TXT 至车机)")
        update_layout = QGridLayout(single_update_group)

        update_layout.addWidget(QLabel("新 ICC_PNO:"), 0, 0)
        self.new_pno_edit = QLineEdit()
        update_layout.addWidget(self.new_pno_edit, 0, 1)

        update_layout.addWidget(QLabel("新 VIN:"), 1, 0)
        self.new_vin_edit = QLineEdit()
        update_layout.addWidget(self.new_vin_edit, 1, 1)

        self.update_btn = QPushButton("✅ 开始更新配置并推送")
        self.update_btn.setStyleSheet("background-color: #28a745; color: white;")
        self.update_btn.clicked.connect(self._start_single_config_update)
        update_layout.addWidget(self.update_btn, 2, 0, 1, 2)

        main_layout.addWidget(single_update_group)

        batch_update_group = QGroupBox("📦 批量操作 (待实现)")
        batch_layout = QVBoxLayout(batch_update_group)
        batch_layout.addWidget(QLabel("批量更新功能将允许您导入 CSV 文件进行多设备/多配置更新。"))

        btn_frame = QHBoxLayout()
        btn_frame.addWidget(QPushButton("📂 导入 CSV 文件"))
        btn_frame.addWidget(QPushButton("🚀 开始批量更新"))
        batch_layout.addLayout(btn_frame)

        main_layout.addWidget(batch_update_group)
        main_layout.addStretch()
        return tab

    def _create_log_puller_tab(self):
        # ... (保持不变)
        tab = QWidget()
        main_layout = QVBoxLayout(tab)
        top_layout = QHBoxLayout()

        config_box = QGroupBox("任务配置")
        config_box.setFixedWidth(350)
        config_layout = QVBoxLayout(config_box)

        path_group = QGroupBox("导出路径")
        path_layout = QHBoxLayout(path_group)
        self.path_edit = QLineEdit(self.export_folder)
        self.path_edit.setReadOnly(True)
        browse_btn = QPushButton("选择...")
        browse_btn.clicked.connect(self.select_export_folder)
        path_layout.addWidget(self.path_edit)
        path_layout.addWidget(browse_btn)
        config_layout.addWidget(path_group)

        log_type_group = QGroupBox(f"日志类型选择 (共 {len(ALL_LOG_TYPES)} 项)")
        log_type_layout = QVBoxLayout(log_type_group)
        self.log_list_widget = QListWidget()
        self.log_list_widget.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        for log_type in ALL_LOG_TYPES:
            item = QListWidgetItem(log_type)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
            item.setCheckState(Qt.CheckState.Checked)
            self.log_list_widget.addItem(item)
        log_type_layout.addWidget(self.log_list_widget)
        config_layout.addWidget(log_type_group)

        top_layout.addWidget(config_box)

        task_box = QGroupBox("任务执行状态 (日志拉取 / 批量截图)")
        task_layout = QVBoxLayout(task_box)

        self.global_progress = QProgressBar()
        self.global_progress.setRange(0, 100)
        self.global_progress.setValue(0)
        task_layout.addWidget(QLabel("全局任务进度:"))
        task_layout.addWidget(self.global_progress)

        self.task_table = QTableWidget()
        self.task_table.setColumnCount(4)
        self.task_table.setHorizontalHeaderLabels(["任务类型", "状态", "详情/文件", "耗时"])
        self.task_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        task_layout.addWidget(self.task_table)

        top_layout.addWidget(task_box)
        top_layout.setStretch(1, 1)

        main_layout.addLayout(top_layout)

        action_bar = QHBoxLayout()
        self.start_pull_btn = QPushButton("🚀 启动日志拉取任务")
        self.start_pull_btn.setStyleSheet("background-color: #007bff; color: white; padding: 10px;")
        self.start_pull_btn.clicked.connect(self._start_pull_process)

        self.clear_btn = QPushButton("🧹 清理远程 Logcat 日志")
        self.clear_btn.setStyleSheet("background-color: #dc3545; color: white; padding: 10px;")
        self.clear_btn.setEnabled(False)
        self.clear_btn.clicked.connect(self._clear_remote_logcat)

        action_bar.addWidget(self.start_pull_btn)
        action_bar.addWidget(self.clear_btn)

        main_layout.addLayout(action_bar)
        return tab

    # --- 新增 Logcat 监控 Tab ---
    def _create_logcat_monitor_tab(self):
        tab = QWidget()
        # 用于后续查找 Tab 的索引
        tab.setObjectName('logcat_monitor_tab')
        main_layout = QHBoxLayout(tab)

        # 1. 左侧：控制面板/过滤器
        control_group = QGroupBox("实时监控控制与过滤器")
        control_group.setFixedWidth(300)
        control_layout = QVBoxLayout(control_group)
        control_layout.setSpacing(10)

        # 1.1 按钮
        self.start_logcat_btn = QPushButton("▶️ 启动实时 Logcat")
        self.start_logcat_btn.setStyleSheet("background-color: #28a745; color: white; padding: 10px;")
        self.start_logcat_btn.clicked.connect(self._start_live_logcat)

        self.stop_logcat_btn = QPushButton("⏸️ 停止监控")
        self.stop_logcat_btn.setStyleSheet("background-color: #dc3545; color: white; padding: 10px;")
        self.stop_logcat_btn.setEnabled(False)
        self.stop_logcat_btn.clicked.connect(self._stop_live_logcat)

        self.clear_logcat_view_btn = QPushButton("🧹 清空显示")
        self.clear_logcat_view_btn.clicked.connect(self._clear_live_logcat_view)

        control_layout.addWidget(self.start_logcat_btn)
        control_layout.addWidget(self.stop_logcat_btn)
        control_layout.addWidget(self.clear_logcat_view_btn)

        # 1.2 过滤器
        filter_group = QGroupBox("过滤器 (支持 Python Regex)")
        filter_layout = QGridLayout(filter_group)

        self.level_combo = QComboBox()
        self.level_combo.addItems([f"{l.name} ({l.value})" for l in LogLevel if l != LogLevel.UNKNOWN] + ["ALL"])
        self.level_combo.setCurrentText("VERBOSE (V)") # 默认最低级别
        filter_layout.addWidget(QLabel("最小级别:"), 0, 0)
        filter_layout.addWidget(self.level_combo, 0, 1)

        self.tag_filter_edit = QLineEdit()
        self.tag_filter_edit.setPlaceholderText(".*Service.*")
        filter_layout.addWidget(QLabel("标签 (Tag) Regex:"), 1, 0)
        filter_layout.addWidget(self.tag_filter_edit, 1, 1)

        self.message_filter_edit = QLineEdit()
        self.message_filter_edit.setPlaceholderText(".*crash|error.*")
        filter_layout.addWidget(QLabel("消息 (Msg) Regex:"), 2, 0)
        filter_layout.addWidget(self.message_filter_edit, 2, 1)

        self.pid_filter_edit = QLineEdit()
        self.pid_filter_edit.setPlaceholderText("PID或TID, 多个用逗号分隔")
        filter_layout.addWidget(QLabel("PID/TID:"), 3, 0)
        filter_layout.addWidget(self.pid_filter_edit, 3, 1)

        self.apply_filter_btn = QPushButton("应用/更新过滤器")
        self.apply_filter_btn.setStyleSheet("background-color: #ffc107;")
        self.apply_filter_btn.clicked.connect(self._apply_live_logcat_filter)
        filter_layout.addWidget(self.apply_filter_btn, 4, 0, 1, 2)

        control_layout.addWidget(filter_group)
        control_layout.addStretch()

        # 1.3 状态栏
        status_frame = QFrame()
        status_frame.setFrameShape(QFrame.Shape.StyledPanel)
        status_layout = QVBoxLayout(status_frame)
        self.live_status_label = QLabel("状态: 未连接")
        self.live_count_label = QLabel("行数: 0 (接收: 0)")
        status_layout.addWidget(self.live_status_label)
        status_layout.addWidget(self.live_count_label)
        control_layout.addWidget(status_frame)

        main_layout.addWidget(control_group)

        # 2. 右侧：日志表格
        log_group = QGroupBox("实时日志输出 (最高显示 5000 行)")
        log_layout = QVBoxLayout(log_group)

        self.logcat_table = QTableWidget()
        self.logcat_table.setColumnCount(len(LOGCAT_COLUMNS))
        self.logcat_table.setHorizontalHeaderLabels(LOGCAT_COLUMNS)
        self.logcat_table.verticalHeader().setVisible(False)
        self.logcat_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.logcat_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        # 设置列宽策略
        header = self.logcat_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents) # 时间
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents) # 级别
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents) # PID
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents) # TID
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents) # Tag
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)        # Message

        # 右键菜单
        self.logcat_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.logcat_table.customContextMenuRequested.connect(self._show_logcat_context_menu)

        log_layout.addWidget(self.logcat_table)
        main_layout.addWidget(log_group)

        return tab

    def _create_toolbox_tab(self):
        # ... (保持不变)
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # --- 远程控制 ---
        control_group = QGroupBox("远程控制")
        control_layout = QGridLayout(control_group)

        reboot_btn = QPushButton("🔁 一键重启车机")
        reboot_btn.setStyleSheet("padding: 15px; font-weight: bold;")
        reboot_btn.clicked.connect(self._reboot_device)

        shell_btn = QPushButton("🖥️ 远程 Shell (高级)")
        shell_btn.setStyleSheet("padding: 15px; font-weight: bold;")
        shell_btn.clicked.connect(lambda: self.on_log_message("工具箱", "启动 ADB Shell 接口待实现...", "INFO"))

        control_layout.addWidget(reboot_btn, 0, 0)
        control_layout.addWidget(shell_btn, 0, 1)
        layout.addWidget(control_group)

        # --- 截图专业功能 ---
        screenshot_group = QGroupBox("📸 截图专业模式 (基于用户脚本)")
        screenshot_layout = QVBoxLayout(screenshot_group)

        # 1. 即时截图
        single_shot_btn = QPushButton("⚡ 一键即时截图")
        single_shot_btn.setStyleSheet("padding: 10px; background-color: #007bff; color: white; font-weight: bold;")
        single_shot_btn.clicked.connect(self._start_single_screenshot)
        screenshot_layout.addWidget(single_shot_btn)

        # 2. 延时截图
        delay_group = QGroupBox("🕒 延时截图")
        delay_layout = QHBoxLayout(delay_group)
        delay_layout.addWidget(QLabel("延迟时间 (秒):"))
        self.delay_edit = QLineEdit("5")
        self.delay_edit.setFixedWidth(50)
        delay_layout.addWidget(self.delay_edit)
        delay_layout.addStretch()
        delay_btn = QPushButton("🚀 启动延时截图")
        delay_btn.clicked.connect(self._start_delay_screenshot)
        delay_layout.addWidget(delay_btn)
        screenshot_layout.addWidget(delay_group)

        # 3. 批量间隔截图
        batch_group = QGroupBox("⏱️ 批量间隔截图 (进度将在 '日志拉取' 标签页更新)")
        batch_layout = QGridLayout(batch_group)

        batch_layout.addWidget(QLabel("截图总数:"), 0, 0)
        self.batch_count_edit = QLineEdit("10")
        self.batch_count_edit.setFixedWidth(50)
        batch_layout.addWidget(self.batch_count_edit, 0, 1)

        batch_layout.addWidget(QLabel("间隔时间 (秒):"), 0, 2)
        self.batch_interval_edit = QLineEdit("3")
        self.batch_interval_edit.setFixedWidth(50)
        batch_layout.addWidget(self.batch_interval_edit, 0, 3)

        batch_btn = QPushButton("🔥 启动批量间隔截图")
        batch_btn.clicked.connect(self._start_batch_screenshot)
        batch_layout.addWidget(batch_btn, 1, 0, 1, 4)

        screenshot_layout.addWidget(batch_group)

        layout.addWidget(screenshot_group)

        # --- 其他工具 (保持不变) ---
        log_grab_group = QGroupBox("日志抓取 (待实现)")
        log_grab_layout = QGridLayout(log_grab_group)

        dump_logcat_btn = QPushButton("📄 拉取 Logcat 缓冲区日志 (adb logcat -d)")
        dump_logcat_btn.setStyleSheet("padding: 15px;")
        dump_logcat_btn.clicked.connect(lambda: self.on_log_message("工具箱", "拉取 Logcat 缓冲区功能待实现...", "INFO"))

        bugreport_btn = QPushButton("🐛 拉取 Bug Report (完整)")
        bugreport_btn.setStyleSheet("padding: 15px;")
        bugreport_btn.clicked.connect(lambda: self.on_log_message("工具箱", "拉取 Bug Report 功能待实现...", "INFO"))

        log_grab_layout.addWidget(dump_logcat_btn, 0, 0)
        log_grab_layout.addWidget(bugreport_btn, 0, 1)
        layout.addWidget(log_grab_group)

        layout.addStretch()
        return tab

    def _create_history_data_tab(self):
        # ... (保持不变)
        tab = QWidget()
        main_layout = QHBoxLayout(tab)

        # --- 1. 操作历史 ---
        history_group = QGroupBox("⚡ 操作历史 (已实现)")
        history_layout = QVBoxLayout(history_group)
        self.history_list = QListWidget()
        history_layout.addWidget(self.history_list)
        main_layout.addWidget(history_group)

        # --- 2. 数据管理 (模板/备份) ---
        data_group = QGroupBox("💾 配置模板与备份")
        data_layout = QVBoxLayout(data_group)

        # 配置模板模块
        template_group = QGroupBox("配置模板 (已实现保存/加载)")
        template_layout = QVBoxLayout(template_group)
        self.template_list = QListWidget()
        self.template_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.template_list.setMinimumHeight(150)
        self._update_template_ui() # 初始化模板列表
        template_layout.addWidget(self.template_list)

        temp_btn_layout = QHBoxLayout()

        save_btn = QPushButton("💾 保存为模板")
        save_btn.clicked.connect(self._save_current_config_as_template)
        temp_btn_layout.addWidget(save_btn)

        load_btn = QPushButton("📥 加载选中模板")
        load_btn.clicked.connect(self._load_selected_template)
        temp_btn_layout.addWidget(load_btn)

        template_layout.addLayout(temp_btn_layout)

        data_layout.addWidget(template_group)

        # 配置备份模块 (待实现)
        backup_group = QGroupBox("配置备份 (待实现)")
        backup_layout = QVBoxLayout(backup_group)
        self.backup_list = QListWidget()
        self.backup_list.addItem("待实现：配置备份列表...")
        backup_layout.addWidget(self.backup_list)

        data_layout.addWidget(backup_group)
        data_layout.addStretch()

        main_layout.addWidget(data_group)
        main_layout.setStretch(0, 1)
        main_layout.setStretch(1, 1)

        return tab

    # --- 线程/信号连接 (更新：Logcat Worker) ---
    def _setup_logic_thread(self):
        """初始化 CoreToolLogic 到 QThread"""
        self.thread = QThread()
        self.logic = CoreToolLogic()
        self.logic.moveToThread(self.thread)

        # 1. 逻辑线程信号连接到 UI 槽
        self.logic.device_connected_signal.connect(self.on_device_connected)
        self.logic.device_disconnected_signal.connect(self.on_device_disconnected)
        self.logic.device_status_signal.connect(self.on_status_update)
        self.logic.error_signal.connect(self.on_error)
        self.logic.remote_logcat_count_signal.connect(self.on_logcat_count_update)
        self.logic.task_start_signal.connect(self.on_task_start)
        self.logic.task_progress_signal.connect(self.on_task_progress)
        self.logic.task_complete_signal.connect(self.on_task_complete)
        self.logic.log_signal.connect(self.on_log_message)
        self.logic.config_pulled_signal.connect(self.on_config_pulled)
        self.logic.operation_success_signal.connect(self.on_operation_success)
        self.logic.screenshot_complete_signal.connect(self.on_screenshot_complete)
        # Logcat Worker Status signal (虽然 LogcatMonitorWorker 不在 logic 里，但 CoreToolLogic 也可以发信号)
        # self.logic.live_monitor_status_signal.connect(self._on_live_monitor_status_update) # 保持逻辑独立性，直接连接 worker 信号

        # 2. UI 信号连接到逻辑线程槽
        self.check_device_signal.connect(self.logic.check_device_and_root, Qt.ConnectionType.QueuedConnection)
        self.start_pull_signal.connect(self.logic.start_pull_process, Qt.ConnectionType.QueuedConnection)
        self.clear_logcat_signal.connect(self.logic.clear_logcat, Qt.ConnectionType.QueuedConnection)
        self.reboot_signal.connect(self.logic.reboot_device, Qt.ConnectionType.QueuedConnection)
        self.push_config_signal.connect(self.logic.push_config_file, Qt.ConnectionType.QueuedConnection)
        self.start_screenshot_signal.connect(self.logic.start_screenshot_task, Qt.ConnectionType.QueuedConnection)
        # Logcat Worker Control 信号由 UI 直接调用 _start_live_logcat/_stop_live_logcat 实现

        self.thread.start()


    # --- Logcat 监控方法 (新增) ---

    def _start_live_logcat(self):
        """启动 LogcatMonitorWorker 在单独的 QThread 中运行"""
        if not self.logic.serial:
            QMessageBox.warning(self, "警告", "设备未连接，无法启动 Logcat 监控。")
            return

        if self.logcat_thread and self.logcat_thread.isRunning():
            self.on_log_message("Logcat", "实时监控已在运行中。", "WARNING")
            return

        # 1. 清理旧数据和 UI
        self._clear_live_logcat_view()

        # 2. 实例化 Worker 和 Thread
        self.logcat_worker = LogcatMonitorWorker(self.logic.serial)
        self.logcat_thread = QThread()
        self.logcat_worker.moveToThread(self.logcat_thread)

        # 3. 连接信号
        self.logcat_worker.new_log_line_signal.connect(self._on_new_live_log)
        self.logcat_worker.status_signal.connect(self._on_live_monitor_status_update)

        # 4. 启动线程
        self.logcat_thread.started.connect(self.logcat_worker.start_monitor)
        self.logcat_thread.start()

        # 5. 更新 UI 状态
        self.start_logcat_btn.setEnabled(False)
        self.stop_logcat_btn.setEnabled(True)
        # 获取当前 Tab 的索引并更新名称
        logcat_tab_index = self.tab_widget.indexOf(self.tab_widget.findChild(QWidget, 'logcat_monitor_tab'))
        if logcat_tab_index != -1:
             self.tab_widget.setTabText(logcat_tab_index, "📊 实时 Logcat 监控 (运行中)")

    def _stop_live_logcat(self):
        """停止 LogcatMonitorWorker"""
        if self.logcat_thread and self.logcat_thread.isRunning():
            # 1. 停止 Worker
            self.logcat_worker.stop_monitor()

            # 2. 清理线程
            self.logcat_thread.quit()
            self.logcat_thread.wait()

            # 3. 清理对象
            self.logcat_worker.deleteLater()
            self.logcat_thread.deleteLater()
            self.logcat_worker = None
            self.logcat_thread = None

            # 4. 更新 UI 状态
            self.start_logcat_btn.setEnabled(True)
            self.stop_logcat_btn.setEnabled(False)
            self._on_live_monitor_status_update("监控已停止。")
            logcat_tab_index = self.tab_widget.indexOf(self.tab_widget.findChild(QWidget, 'logcat_monitor_tab'))
            if logcat_tab_index != -1:
                 self.tab_widget.setTabText(logcat_tab_index, "📊 实时 Logcat 监控")

    def _clear_live_logcat_view(self):
        """清空 Logcat 表格和计数"""
        if hasattr(self, 'logcat_table'):
            self.logcat_table.setRowCount(0)
            self.logcat_displayed_lines = 0
            self.logcat_total_lines = 0
            self.live_count_label.setText("行数: 0 (接收: 0)")
            self.on_log_message("Logcat", "实时日志显示已清空。", "INFO")

    def _apply_live_logcat_filter(self):
        """更新过滤器的参数"""
        try:
            # 1. 最小级别
            min_level_str = self.level_combo.currentText().split(' ')[0]
            if min_level_str == "ALL":
                 self.logcat_filter_criteria['min_level'] = LogLevel.VERBOSE
            else:
                 self.logcat_filter_criteria['min_level'] = LogLevel[min_level_str]

            # 2. Tag Regex
            tag_text = self.tag_filter_edit.text().strip()
            self.logcat_filter_criteria['tag_regex'] = re.compile(tag_text, re.IGNORECASE) if tag_text else None

            # 3. Message Regex
            msg_text = self.message_filter_edit.text().strip()
            self.logcat_filter_criteria['msg_regex'] = re.compile(msg_text, re.IGNORECASE) if msg_text else None

            # 4. PID/TID
            pid_text = self.pid_filter_edit.text().strip()
            self.logcat_filter_criteria['pid_tid'] = [int(p.strip()) for p in pid_text.split(',') if p.strip().isdigit()] if pid_text else None

            self.on_log_message("Logcat", "过滤器已更新，将应用于后续实时日志。", "SUCCESS")
        except re.error as e:
            QMessageBox.critical(self, "Regex 错误", f"正则表达式格式错误: {e}")
            self.on_log_message("Logcat", f"Regex 格式错误: {e}", "ERROR")
        except Exception as e:
            QMessageBox.critical(self, "过滤器错误", f"过滤器参数错误: {e}")

    def _check_log_filter(self, entry: LogEntry) -> bool:
        """检查日志条目是否匹配当前过滤器"""
        # Level check
        if entry.level.value < self.logcat_filter_criteria['min_level'].value:
            return False

        # Tag regex check
        if self.logcat_filter_criteria['tag_regex'] and not self.logcat_filter_criteria['tag_regex'].search(entry.tag):
            return False

        # Message regex check
        if self.logcat_filter_criteria['msg_regex'] and not self.logcat_filter_criteria['msg_regex'].search(entry.message):
            return False

        # PID/TID check
        pid_tids = self.logcat_filter_criteria['pid_tid']
        if pid_tids and entry.pid not in pid_tids and entry.tid not in pid_tids:
            return False

        return True

    @Slot(LogEntry)
    def _on_new_live_log(self, entry: LogEntry):
        """接收并处理 LogcatMonitorWorker 发来的新日志行"""
        self.logcat_total_lines += 1

        # 应用过滤器
        if not self._check_log_filter(entry):
            return

        self.logcat_displayed_lines += 1

        # 1. 行数限制处理 (循环缓冲区)
        row_count = self.logcat_table.rowCount()
        if row_count >= MAX_LIVE_LOG_ROWS:
            self.logcat_table.removeRow(0)
            row_count -= 1

        # 2. 插入新行
        self.logcat_table.insertRow(row_count)

        # 3. 颜色映射
        color_map = {
            LogLevel.FATAL: QColor(Qt.GlobalColor.white),
            LogLevel.ERROR: QColor(Qt.GlobalColor.red),
            LogLevel.WARN: QColor(255, 165, 0),          # Orange
            LogLevel.INFO: QColor(Qt.GlobalColor.blue),
            LogLevel.DEBUG: QColor(Qt.GlobalColor.darkGreen),
            LogLevel.VERBOSE: QColor(Qt.GlobalColor.gray),
            LogLevel.UNKNOWN: QColor(Qt.GlobalColor.black),
        }
        bg_color = QColor(Qt.GlobalColor.white)
        text_color = color_map.get(entry.level, QColor(Qt.GlobalColor.black))

        # 紧急提示强化: FATAL 级别改为红底白字加粗警示
        if entry.level == LogLevel.FATAL:
            bg_color = QColor(Qt.GlobalColor.red)

        # 4. 填充数据
        items = [
            QTableWidgetItem(entry.timestamp.strftime("%m-%d %H:%M:%S.%f")[:-3]),
            QTableWidgetItem(entry.level.name),
            QTableWidgetItem(str(entry.pid)),
            QTableWidgetItem(str(entry.tid)),
            QTableWidgetItem(entry.tag),
            QTableWidgetItem(entry.message)
        ]

        for col, item in enumerate(items):
            item.setFlags(item.flags() ^ Qt.ItemFlag.ItemIsEditable)
            item.setForeground(text_color)
            item.setBackground(bg_color)
            if entry.level == LogLevel.FATAL:
                item.setFont(QFont("Microsoft YaHei UI", 9, QFont.Bold))
            self.logcat_table.setItem(row_count, col, item)

        # 5. 自动滚动和更新计数
        self.logcat_table.scrollToBottom()
        self.live_count_label.setText(f"行数: {self.logcat_table.rowCount()} (接收: {self.logcat_total_lines})")

    @Slot(str)
    def _on_live_monitor_status_update(self, message: str):
        """更新 Logcat 监控状态栏"""
        self.live_status_label.setText(f"状态: {message}")
        self.on_log_message("Logcat", message, "INFO")


    def _show_logcat_context_menu(self, pos):
        """显示 Logcat 表格的右键菜单"""
        if self.logcat_table.selectedItems():
            menu = QMenu(self)

            # 复制选中行
            copy_action = QAction("复制选中行原始内容 (Tab 分隔)", self)
            copy_action.triggered.connect(self._copy_selected_logcat_rows)
            menu.addAction(copy_action)

            # 复制选中行 Message
            copy_msg_action = QAction("复制选中行消息内容", self)
            copy_msg_action.triggered.connect(lambda: self._copy_selected_logcat_rows(message_only=True))
            menu.addAction(copy_msg_action)

            # TODO: 导出选中行到文件
            # export_action = QAction("导出选中行到文件", self)
            # menu.addAction(export_action)

            menu.exec(self.logcat_table.viewport().mapToGlobal(pos))

    def _copy_selected_logcat_rows(self, message_only=False):
        """复制选中 Logcat 行的内容到剪贴板"""
        selected_rows = set()
        for item in self.logcat_table.selectedItems():
            selected_rows.add(item.row())

        if not selected_rows:
            return

        clipboard_text = []
        rows = sorted(list(selected_rows))

        for row in rows:
            row_data = []
            if message_only:
                # 只复制 Message 列 (索引 5)
                item = self.logcat_table.item(row, 5)
                row_data.append(item.text())
            else:
                # 复制所有显示的列
                for col in range(self.logcat_table.columnCount()):
                    item = self.logcat_table.item(row, col)
                    row_data.append(item.text())

            clipboard_text.append('\t'.join(row_data))

        QApplication.clipboard().setText('\n'.join(clipboard_text))
        self.on_log_message("Logcat", f"已复制 {len(rows)} 行到剪贴板。", "SUCCESS")


    # --- 其他 UI 槽函数 (保持不变) ---
    def _update_time_and_status(self):
        # ... (保持不变)
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.datetime_label.setText(f"📅 实时时间: {current_time}")

    def select_export_folder(self):
        # ... (保持不变)
        folder = QFileDialog.getExistingDirectory(self, "选择日志导出目录", self.export_folder)
        if folder:
            self.export_folder = folder
            self.path_edit.setText(folder)
            self.logic.export_path = folder
            self._save_app_data()
            self.on_log_message("系统", f"日志导出目录已更新为: {folder}", "INFO")

    def _start_pull_process(self):
        # ... (保持不变)
        selected = [self.log_list_widget.item(i).text()
                    for i in range(self.log_list_widget.count())
                    if self.log_list_widget.item(i).checkState() == Qt.CheckState.Checked]

        if not selected:
            QMessageBox.warning(self, "警告", "请至少选择一个日志类型。")
            return

        self.start_pull_signal.emit(selected, self.export_folder)

    def _clear_remote_logcat(self):
        # ... (保持不变)
        reply = QMessageBox.question(self, '确认清理',
            "您确定要清除远程设备上的所有 Logcat 日志文件吗？此操作不可逆。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)

        if reply == QMessageBox.StandardButton.Yes:
            self.clear_logcat_signal.emit()

    def _start_single_config_update(self):
        # ... (保持不变)
        new_pno = self.new_pno_edit.text().strip()
        new_vin = self.new_vin_edit.text().strip()

        # 1. 验证输入
        pno_ok, pno_msg = ConfigValidator.validate_icc_pno(new_pno)
        if not pno_ok:
            QMessageBox.critical(self, "验证失败", f"ICC_PNO 错误: {pno_msg}")
            return

        vin_ok, vin_msg = ConfigValidator.validate_vin(new_vin)
        if not vin_ok:
            QMessageBox.critical(self, "验证失败", f"VIN 错误: {vin_msg}")
            return

        # 2. 确认操作
        reply = QMessageBox.question(self, '确认推送',
            f"确定要将以下新配置推送至设备吗？\n\nICC_PNO: {new_pno}\nVIN: {new_vin}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)

        if reply == QMessageBox.StandardButton.Yes:
            self.push_config_signal.emit(new_pno, new_vin)

    def _reboot_device(self):
        # ... (保持不变)
        reply = QMessageBox.question(self, '确认重启',
            "您确定要重启目标设备吗？此操作将中断所有 ADB 连接。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)

        if reply == QMessageBox.StandardButton.Yes:
            self.reboot_signal.emit()

    def show_about_dialog(self):
        # ... (保持不变)
        QMessageBox.about(self, "关于",
            f"{TOOL_NAME}\n"
            f"版本: {VERSION}\n"
            f"开发者: {AUTHOR}\n"
            f"GitHub: {GITHUB_LINK}\n\n"
            f"{COPYRIGHT}"
        )

    def closeEvent(self, event):
        # ... (增加 Logcat 线程清理)
        self.timer.stop()
        self.device_monitor_timer.stop()

        # 确保 Logcat 线程被安全终止
        if self.logcat_thread and self.logcat_thread.isRunning():
            self._stop_live_logcat()

        self.thread.quit()
        self.thread.wait()
        self._save_app_data()
        event.accept()

    def _load_app_data(self):
        # ... (保持不变)
        try:
            if Path(DATA_FILE).exists():
                with open(DATA_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.export_folder = data.get('export_folder', self.export_folder)
                    self.stats_ota_count_value = data.get('stats_ota_count', 0)
                    self.stats_log_count_value = data.get('stats_log_count', 0)
                    self.history_records = data.get('history_records', [])
                    self.config_templates = data.get('config_templates', {})
                    self.logic.export_path = self.export_folder
                    self.path_edit.setText(self.export_folder)
            self.on_log_message("系统", "应用数据加载成功。", "INFO")
        except Exception as e:
            self.on_log_message("系统", f"加载应用数据失败: {e}，将使用默认配置。", "ERROR")
            self._save_app_data()

    def _save_app_data(self):
        # ... (保持不变)
        data = {
            'export_folder': self.export_folder,
            'stats_ota_count': self.stats_ota_count_value,
            'stats_log_count': self.stats_log_count_value,
            'history_records': self.history_records,
            'config_templates': self.config_templates
        }
        try:
            with open(DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"ERROR: 保存应用数据失败: {e}")

    def _update_stats_ui(self):
        # ... (保持不变)
        if hasattr(self, 'stats_ota_count'):
            self.stats_ota_count.setText(str(self.stats_ota_count_value))
            self.stats_log_count.setText(str(self.stats_log_count_value))

    def _update_history_ui(self):
        # ... (保持不变)
        if hasattr(self, 'history_list'):
            self.history_list.clear()
            if not self.history_records:
                self.history_list.addItem("暂无历史操作记录...")
                return

            # 显示最新的 50 条记录
            for record in reversed(self.history_records[-50:]):
                self.history_list.addItem(f"[{record['time']}] [{record['type']}] {record['detail']}")

    def _update_template_ui(self):
        # ... (保持不变)
        if hasattr(self, 'template_list'):
            self.template_list.clear()
            if not self.config_templates:
                self.template_list.addItem("暂无配置模板...")
                return

            # 按名称排序显示模板
            for name in sorted(self.config_templates.keys()):
                self.template_list.addItem(name)

    def _save_current_config_as_template(self):
        # ... (保持不变)
        if not self.logic.current_config:
            QMessageBox.warning(self, "警告", "当前配置数据为空，无法保存为模板。请先拉取配置。")
            return

        template_name, ok = QInputDialog.getText(self, "保存配置模板", "请输入模板名称 (如: Test_PNO_001):")

        if ok and template_name:
            template_name = template_name.strip()
            if template_name in self.config_templates:
                reply = QMessageBox.question(self, "模板已存在",
                                             f"模板 '{template_name}' 已存在，是否覆盖？",
                                             QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if reply == QMessageBox.StandardButton.No:
                    return

            # 保存配置，排除 FileHash 字段
            config_to_save = self.logic.current_config.copy()
            config_to_save.pop('FileHash', None)

            self.config_templates[template_name] = config_to_save
            self._update_template_ui()
            self._save_app_data()
            self.on_log_message("模板", f"配置已保存为模板: '{template_name}'", "SUCCESS")
            QMessageBox.information(self, "保存成功", f"配置模板 '{template_name}' 已保存。")


    def _load_selected_template(self):
        # ... (保持不变)
        selected_items = self.template_list.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "警告", "请先选择一个配置模板。")
            return

        template_name = selected_items[0].text()
        template_data = self.config_templates.get(template_name)

        if not template_data:
            QMessageBox.critical(self, "错误", f"模板 '{template_name}' 数据丢失。")
            return

        reply = QMessageBox.question(self, "加载模板",
                                     f"确定要加载模板 '{template_name}' 的配置并填充到输入框吗？(不会立即推送)",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)

        if reply == QMessageBox.StandardButton.Yes:
            pno = template_data.get('ICC_PNO', '')
            vin = template_data.get('VIN', '')

            if hasattr(self, 'new_pno_edit'):
                self.new_pno_edit.setText(pno)
            if hasattr(self, 'new_vin_edit'):
                self.new_vin_edit.setText(vin)

            self.on_log_message("模板", f"模板 '{template_name}' 已加载，数据已填充到配置更新输入框。", "SUCCESS")
            QMessageBox.information(self, "加载成功", "模板数据已填充。请在 'OTA 配置' 标签页点击 '开始更新配置并推送' 完成操作。")


    # --- 截图模式 UI 交互方法 (保持不变) ---

    def _start_single_screenshot(self):
        # ... (保持不变)
        if not self.logic.serial:
            QMessageBox.warning(self, "警告", "设备未连接，无法截图。")
            return
        self.start_screenshot_signal.emit('single', 0, 1, 0, self.export_folder)

    def _start_delay_screenshot(self):
        # ... (保持不变)
        if not self.logic.serial:
            QMessageBox.warning(self, "警告", "设备未连接，无法截图。")
            return

        try:
            delay = int(self.delay_edit.text().strip())
            if delay < 1 or delay > 300:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "输入错误", "延迟时间必须是 1 到 300 之间的整数。")
            return

        self.start_screenshot_signal.emit('delay', delay, 1, 0, self.export_folder)

    def _start_batch_screenshot(self):
        # ... (保持不变)
        if not self.logic.serial:
            QMessageBox.warning(self, "警告", "设备未连接，无法截图。")
            return

        try:
            count = int(self.batch_count_edit.text().strip())
            interval = int(self.batch_interval_edit.text().strip())
            if count < 1 or count > 50 or interval < 1 or interval > 60:
                 raise ValueError
        except ValueError:
            QMessageBox.warning(self, "输入错误", "截图总数必须在 1-50 之间，间隔时间必须在 1-60 秒之间。")
        return

    # --- UI 槽函数 (保持不变) ---

    @Slot(str, str, str)
    def on_log_message(self, tag: str, message: str, level: str):
        # ... (保持不变)
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")

        if level == "ERROR":
            color = QColor(Qt.GlobalColor.red)
        elif level == "WARNING":
            color = QColor(255, 165, 0)  # Orange
        elif level == "SUCCESS":
            color = QColor(Qt.GlobalColor.darkGreen)
        elif level == "INFO":
            color = QColor(Qt.GlobalColor.blue)
        else:
            color = QColor(Qt.GlobalColor.black)

        formatted_message = f"[{timestamp}] [{tag}] {message}\n"

        cursor = self.log_text_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        # 写入彩色文本
        fmt = cursor.charFormat()
        fmt.setForeground(color)
        cursor.setCharFormat(fmt)
        cursor.insertText(formatted_message)

        # 恢复默认颜色
        fmt_default = cursor.charFormat()
        fmt_default.setForeground(QColor(Qt.GlobalColor.black))
        cursor.setCharFormat(fmt_default)

        # 滚动到底部
        self.log_text_edit.setTextCursor(cursor)
        self.log_text_edit.ensureCursorVisible()


    @Slot(str, str)
    def on_screenshot_complete(self, status: str, message: str):
        # ... (保持不变)
        if status == "SUCCESS":
            self.on_log_message("截图", f"单次截图已保存到: {message}", "SUCCESS")
            QMessageBox.information(self, "截图完成", f"截图已保存到：\n{message}")
        elif status == "BATCH_SUCCESS":
            self.on_log_message("截图", f"批量任务完成，{message}", "SUCCESS")
            QMessageBox.information(self, "批量截图完成", message)

    @Slot(int)
    def on_task_start(self, total_tasks: int):
        # ... (保持不变)
        self.global_progress.setRange(0, total_tasks)
        self.global_progress.setValue(0)
        self.task_table.setRowCount(total_tasks)
        self.start_pull_btn.setEnabled(False)
        self.clear_btn.setEnabled(False)

    @Slot(int, str, str, str)
    def on_task_progress(self, index: int, log_type: str, status: str, files_count: str):
        # ... (保持不变)
        row = index - 1
        self.task_table.setItem(row, 0, QTableWidgetItem(log_type))
        self.task_table.setItem(row, 1, QTableWidgetItem(status))
        self.task_table.setItem(row, 2, QTableWidgetItem(files_count))
        self.task_table.setItem(row, 3, QTableWidgetItem("N/A"))

        self.global_progress.setValue(index)

    @Slot(dict, str)
    def on_task_complete(self, summary: dict, export_path: str):
        # ... (保持不变)
        self.start_pull_btn.setEnabled(True)
        self.clear_btn.setEnabled(self.log_count != -1 and self.log_count > 0)

        # 任务完成可能是日志拉取或批量截图。如果是日志拉取，则弹出消息框。
        if summary.get('results'):
            self.on_log_message("拉取", f"日志拉取任务完成。共拉取 {summary['total_files_pulled']} 种日志，失败 {summary['total_fail']} 项。", "SUCCESS")
            self.on_log_message("拉取", f"日志保存路径: {export_path}", "INFO")
            QMessageBox.information(self, "任务完成", f"所有选中的日志已拉取完成。\n保存路径: {export_path}")
        else:
            pass

    @Slot(str)
    def on_device_connected(self, serial: str):
        # ... (保持不变)
        self.serial_label.setText(f"序列号: {serial}")
        if hasattr(self, 'start_pull_btn'):
            self.start_pull_btn.setEnabled(True)
        if hasattr(self, 'clear_btn'):
            self.clear_btn.setEnabled(self.log_count != -1 and self.log_count > 0)

    @Slot()
    def on_device_disconnected(self):
        # ... (增加 Logcat 停止逻辑)
        if self.logcat_thread and self.logcat_thread.isRunning():
            self._stop_live_logcat() # 设备断开，自动停止实时监控

        self.serial_label.setText("序列号: N/A")
        self.logcat_count_label.setText("📦 远程 Logcat 文件数: N/A")
        if hasattr(self, 'start_pull_btn'):
            self.start_pull_btn.setEnabled(False)
        if hasattr(self, 'clear_btn'):
            self.clear_btn.setEnabled(False)
        self.on_status_update("错误: 设备已断开。", "red")

    @Slot(str, str)
    def on_status_update(self, text: str, color_key: str):
        # ... (保持不变)
        color_map = {"red": "#dc3545", "green": "#28a745", "yellow": "#ffc107", "blue": "#007bff"}

        self.status_label.setText(text)
        self.status_indicator.setStyleSheet(f"font-size: 18pt; color: {color_map.get(color_key, 'gray')};")

    @Slot(str)
    def on_error(self, message: str):
        # ... (保持不变)
        self.on_status_update("错误: " + message, "red")
        self.on_log_message("系统", message, "ERROR")
        QMessageBox.critical(self, "操作错误", message)

    @Slot(int)
    def on_logcat_count_update(self, count: int):
        # ... (保持不变)
        self.log_count = count
        if count >= 0:
            self.logcat_count_label.setText(f"📦 远程 Logcat 文件数: {count}")
            is_enabled = self.logic.serial is not None and count > 0
            if hasattr(self, 'clear_btn'):
                 self.clear_btn.setEnabled(is_enabled)
        else:
            self.logcat_count_label.setText("📦 远程 Logcat 文件数: N/A")
            if hasattr(self, 'clear_btn'):
                 self.clear_btn.setEnabled(False)

    @Slot(dict)
    def on_config_pulled(self, config_data: dict):
        # ... (保持不变)
        self.current_pno = config_data.get('ICC_PNO', 'N/A')
        self.current_vin = config_data.get('VIN', 'N/A')
        self.current_hash = config_data.get('FileHash', 'N/A')

        # 1. 更新 Config Tab 页的标签
        if hasattr(self, 'current_pno_label'):
            self.current_pno_label.setText(self.current_pno)
        if hasattr(self, 'current_vin_label'):
            self.current_vin_label.setText(self.current_vin)
        if hasattr(self, 'current_hash_label'):
            self.current_hash_label.setText(self.current_hash)

        # 2. 更新 Home Panel Tab 页的标签
        if hasattr(self, 'home_pno_label'):
            self.home_pno_label.setText(self.current_pno)
        if hasattr(self, 'home_vin_label'):
            self.home_vin_label.setText(self.current_vin)
        if hasattr(self, 'home_hash_label'):
            self.home_hash_label.setText(self.current_hash)

        # 3. 更新输入框（方便修改）
        if hasattr(self, 'new_pno_edit'):
            self.new_pno_edit.setText(self.current_pno)
        if hasattr(self, 'new_vin_edit'):
            self.new_vin_edit.setText(self.current_vin)


        if self.current_vin != 'N/A' and self.current_vin != self.logic.DEFAULT_VIN:
            _, vin_msg = ConfigValidator.validate_vin(self.current_vin)
            self.on_log_message("配置", f"[VIN 验证]: {vin_msg}", "INFO")
        elif self.current_vin == self.logic.DEFAULT_VIN:
            self.on_log_message("配置", "配置读取完成，当前显示默认配置，请在上方输入新配置。", "WARNING")
        else:
            self.on_log_message("配置", "配置读取中，当前配置信息为 N/A。", "INFO")

    @Slot(str, str)
    def on_operation_success(self, op_type: str, op_detail: str):
        # ... (保持不变)
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if op_type == "OTA配置更新":
            self.stats_ota_count_value += 1
        elif op_type == "日志拉取":
            self.stats_log_count_value += 1
        elif op_type == "批量截图":
            pass

        self._update_stats_ui()

        new_record = {
            'time': timestamp,
            'type': op_type,
            'detail': op_detail
        }
        self.history_records.append(new_record)
        self._update_history_ui()

        self._save_app_data()


# ========================================
# 6. 主程序入口
# ========================================

if __name__ == '__main__':

    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling)
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # 设置主题色板
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor(240, 240, 240))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(0, 0, 0))
    palette.setColor(QPalette.ColorRole.Base, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(230, 230, 230))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(255, 255, 220))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(0, 0, 0))
    palette.setColor(QPalette.ColorRole.Text, QColor(0, 0, 0))
    palette.setColor(QPalette.ColorRole.Button, QColor(240, 240, 240))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(0, 0, 0))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 0, 0))
    palette.setColor(QPalette.ColorRole.Link, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    app.setPalette(palette)

    window = AdayoMegaTool()
    window.show()
    sys.exit(app.exec())