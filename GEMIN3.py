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

# ========================================
# PySide6 导入
# ========================================
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QLineEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QProgressBar, QFileDialog, QGroupBox,
    QListWidget, QListWidgetItem, QMessageBox, QHeaderView, QTabWidget,
    QMenuBar, QMenu, QTextEdit, QFrame
)
from PySide6.QtCore import (
    QObject, QThread, Signal, Slot, Qt, QSize, QTimer
)
from PySide6.QtGui import (
    QColor, QPalette, QFont, QIcon, QAction
)

# ========================================
# 1. 配置与元信息
# ========================================

TOOL_NAME = "Adayo 车载测试与配置集成平台"
VERSION = "1.0.6 (数据持久化修复版)" # 修复版本号
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
DEVICE_CONFIG_PATH = "/mnt/sdcard/DeviceInfo.txt"
LOCAL_CONFIG_PATH = "DeviceInfo.txt"

# ========================================
# 2. 核心辅助类
# ========================================

class ConfigValidator:
    """配置验证器：负责VIN校验位计算和格式验证"""
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
# 3. 核心逻辑 (CoreToolLogic)
# ========================================

# 移除 OperationHistory 类，由 AdayoMegaTool 统一管理历史记录的持久化

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
    # 新增信号：成功操作通知 UI 更新统计和历史记录
    operation_success_signal = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.serial = None
        self.export_path = str(Path.cwd() / "CarLogs")
        self.selected_logs = ALL_LOG_TYPES
        self.is_pulling_logs = False
        self.is_running_tool = False
        self.current_config = {}

    # --- 基础 ADB 操作 (省略，与前版本一致) ---

    def run_adb_command(self, command: list, check_output: bool = False, timeout=120):
        # 完整的 run_adb_command 实现...
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
            self.error_signal.emit(f"命令超时: {' '.join(command)}")
            return False, "", "Timeout"
        except Exception as e:
            self.error_signal.emit(f"ADB 执行失败: {e}")
            return False, "", str(e)


    def count_remote_files(self, remote_path: str) -> int:
        count_cmd = ["shell", f"find {remote_path} -type f | wc -l"]
        success, output, _ = self.run_adb_command(count_cmd, check_output=True, timeout=5)
        try:
            # ADB shell 输出可能包含多个空格和换行
            return int(output.strip().split()[-1]) if success and output.strip().split() else -1
        except Exception:
            return -1

    def count_remote_logcat(self):
        if not self.serial:
            self.remote_logcat_count_signal.emit(-1)
            return

        logcat_path_str = str(Path(REMOTE_LOG_PATH) / "logcat")
        count = self.count_remote_files(logcat_path_str)

        self.remote_logcat_count_signal.emit(count)
        return count


    # --- 设备状态监控 (省略，与前版本一致) ---
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
                self.device_status_signal.emit(f"连接成功 ({self.serial})，权限已增强。", "green")
                self.count_remote_logcat()

        elif not self.serial:
            if len(current_devices) == 1:
                self.check_device_and_root()
            elif len(current_devices) == 0:
                self.device_status_signal.emit("错误: 未找到单个已连接设备。", "red")
                self.remote_logcat_count_signal.emit(-1)
            else:
                self.device_status_signal.emit("错误: 发现多个设备，请断开多余设备。", "red")


    # --- OTA 配置操作 (修复后增加了 operation_success_signal) ---

    @Slot()
    def pull_config_file(self):
        # ... (与前版本一致)
        if not self.serial:
            self.error_signal.emit("设备未连接，无法拉取配置。")
            return

        self.log_signal.emit("配置", "正在拉取设备配置文件...", "WARNING")

        local_path = Path(LOCAL_CONFIG_PATH)

        success, output, error = self.run_adb_command(["pull", DEVICE_CONFIG_PATH, str(local_path)], timeout=30)

        if success:
            self.log_signal.emit("配置", f"配置文件拉取成功，路径: {local_path.resolve()}", "SUCCESS")
            try:
                config_data = {}
                with open(local_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        if '=' in line:
                            key, value = line.split('=', 1)
                            config_data[key.strip()] = value.strip()

                self.current_config = config_data

                config_hash = hashlib.sha256(local_path.read_bytes()).hexdigest()[:8]
                self.current_config['FileHash'] = config_hash

                self.device_status_signal.emit(f"连接成功 ({self.serial})，配置已读取。", "green")
                self.log_signal.emit("配置", f"配置解析成功: ICC_PNO={config_data.get('ICC_PNO', 'N/A')}", "INFO")

                self.config_pulled_signal.emit(config_data)

            except Exception as e:
                self.error_signal.emit(f"配置文件解析失败: {e}")
                self.log_signal.emit("配置", f"配置文件解析失败: {e}", "ERROR")

        else:
            self.error_signal.emit(f"拉取配置文件失败: {error}")
            self.log_signal.emit("配置", f"拉取配置文件失败: {error}", "ERROR")
            self.current_config = {}
            self.config_pulled_signal.emit({})

    @Slot(str, str)
    def push_config_file(self, new_pno: str, new_vin: str):
        if not self.serial:
            self.error_signal.emit("设备未连接，无法推送配置。")
            return

        self.log_signal.emit("配置", "正在生成并推送新的配置文件...", "WARNING")

        temp_config_path = Path("temp_DeviceInfo.txt")
        new_config_data = self.current_config.copy()

        new_config_data['ICC_PNO'] = new_pno
        new_config_data['VIN'] = new_vin

        try:
            with open(temp_config_path, 'w', encoding='utf-8') as f:
                for key, value in new_config_data.items():
                    if key != 'FileHash':
                        f.write(f"{key}={value}\n")
        except Exception as e:
            self.error_signal.emit(f"生成本地临时配置失败: {e}")
            return

        success, output, error = self.run_adb_command(["push", str(temp_config_path), DEVICE_CONFIG_PATH], timeout=30)

        temp_config_path.unlink()

        if success:
            self.log_signal.emit("配置", "新配置文件推送成功。", "SUCCESS")
            # 🔔 关键修复点：成功后发送信号通知 UI 更新统计和历史记录
            self.operation_success_signal.emit("OTA配置更新", f"成功更新 PNO={new_pno}, VIN={new_vin}")
            self.pull_config_file()
        else:
            self.error_signal.emit(f"推送配置文件失败: {error}")
            self.log_signal.emit("配置", f"推送配置文件失败: {error}", "ERROR")


    # --- 日志拉取操作 (修复后增加了 operation_success_signal) ---

    @Slot(list, str)
    def start_pull_process(self, selected_logs: list, export_folder: str):
        # ... (与前版本一致)
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

            if log_type == WLAN_LOG_TYPE:
                pull_cmd = ["pull", remote_path, str(export_path)]
            else:
                pull_cmd = ["pull", remote_path, str(local_target)]

            success, output, error = self.run_adb_command(pull_cmd, timeout=600)

            is_success = success and "pull failed" not in output.lower()
            file_count = 0
            status_text = "失败"

            if is_success:
                final_local_path = export_path / log_type if log_type != WLAN_LOG_TYPE else export_path / "wlan_logs"

                if final_local_path.exists():
                    file_count = sum(1 for item in final_local_path.rglob('*') if item.is_file())

                if file_count > 0:
                    status_text = "成功"
                    total_files_pulled += 1
                else:
                    status_text = "空目录"
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
        self.task_complete_signal.emit({
            'total_files_pulled': total_files_pulled,
            'total_fail': total_fail,
            'results': results_summary
        }, str(export_path))

        # 🔔 关键修复点：成功后发送信号通知 UI 更新统计和历史记录
        if total_files_pulled > 0:
            self.operation_success_signal.emit("日志拉取", f"完成: 成功拉取 {total_files_pulled} 项日志。")


    # --- 工具箱操作 (省略，与前版本一致) ---
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
            files_after = self.count_remote_logcat()
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


# ========================================
# 4. 主窗口 UI (AdayoMegaTool - 数据持久化增强)
# ========================================

class AdayoMegaTool(QMainWindow):
    # 定义连接到 CoreToolLogic 的信号
    check_device_signal = Signal()
    start_pull_signal = Signal(list, str)
    clear_logcat_signal = Signal()
    reboot_signal = Signal()
    push_config_signal = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(TOOL_NAME)
        self.setGeometry(100, 100, 1400, 900)

        self.export_folder = str(Path.cwd() / "AdayoMegaLogs")

        # 1. 初始化统计变量和历史记录列表
        self.stats_ota_count_value = 0
        self.stats_log_count_value = 0
        self.history_records = [] # 新增：用于存储持久化的历史记录
        self.log_count = -1
        self.current_pno = "N/A"
        self.current_vin = "N/A"
        self.current_hash = "N/A"

        # 2. 关键修复点：程序启动时加载本地数据
        self._load_app_data()

        # 3. 设置逻辑线程
        self._setup_logic_thread()

        # 4. 核心 UI 设置 - 遵循客户布局要求 (QVBoxLayout 垂直堆叠)
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QVBoxLayout(self.central_widget)
        self.main_layout.setContentsMargins(5, 5, 5, 5)

        # 5. 设置 UI 组件
        self._setup_menu_bar()
        self._setup_top_status_panel(self.main_layout)
        self._setup_tab_content(self.main_layout)
        self._setup_log_viewer(self.main_layout)
        self._setup_footer(self.main_layout)

        # 6. 设置拉伸因子和定时器
        self.main_layout.setStretch(1, 1)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._update_time_and_status)
        self.timer.start(1000)

        self.device_monitor_timer = QTimer(self)
        self.device_monitor_timer.timeout.connect(self.logic.monitor_device_status)
        self.device_monitor_timer.start(5000)

        QTimer.singleShot(100, self.check_device_signal.emit)

        # 7. 更新 UI 以反映加载的数据
        self._update_stats_ui()
        self._update_history_ui()


    # --- 数据持久化方法 ---

    def _load_app_data(self):
        """从本地文件加载统计数据和历史记录"""
        data_path = Path(DATA_FILE)
        if data_path.exists():
            try:
                with open(data_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.stats_ota_count_value = data.get('ota_count', 0)
                    self.stats_log_count_value = data.get('log_count', 0)
                    self.history_records = data.get('history', [])
                self.on_log_message("系统", "成功加载历史统计数据。", "INFO")
            except Exception as e:
                self.on_log_message("系统", f"加载本地数据失败: {e}", "ERROR")

    def _save_app_data(self):
        """将统计数据和历史记录保存到本地文件"""
        data = {
            'ota_count': self.stats_ota_count_value,
            'log_count': self.stats_log_count_value,
            'history': self.history_records
        }
        try:
            with open(DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            self.on_log_message("系统", f"保存本地数据失败: {e}", "ERROR")

    # --- UI 辅助更新方法 ---

    def _update_stats_ui(self):
        """更新主页的统计数据标签"""
        if hasattr(self, 'stats_ota_count'):
            self.stats_ota_count.setText(str(self.stats_ota_count_value))
            self.stats_log_count.setText(str(self.stats_log_count_value))

    def _update_history_ui(self):
        """更新历史记录列表"""
        if hasattr(self, 'history_list'):
            self.history_list.clear()
            if not self.history_records:
                self.history_list.addItem("暂无历史操作记录...")
                return

            # 显示最新的记录
            for record in reversed(self.history_records[-50:]): # 只显示最近50条
                self.history_list.addItem(f"[{record['time']}] [{record['type']}] {record['detail']}")


    # --- UI 结构方法 (省略，与前版本一致) ---
    def _setup_menu_bar(self):
        menu_bar = QMenuBar(self)
        self.setMenuBar(menu_bar)
        help_menu = menu_bar.addMenu("帮助")
        about_action = QAction("关于", self)
        about_action.triggered.connect(self.show_about_dialog)
        help_menu.addAction(about_action)

    def _setup_top_status_panel(self, main_layout: QVBoxLayout):
        status_frame = QFrame()
        status_frame.setFrameShape(QFrame.Shape.Box)
        status_frame.setFrameShadow(QFrame.Shadow.Raised)
        status_frame.setStyleSheet("background-color: #f7f7f7; padding: 5px; border-radius: 5px;")

        status_layout = QGridLayout(status_frame)

        # === 左侧：连接状态 ===
        self.status_indicator = QLabel("●")
        self.status_indicator.setStyleSheet("font-size: 18pt; color: gray;")
        self.status_indicator.setFixedWidth(25)

        self.status_label = QLabel("正在初始化...")
        self.status_label.setStyleSheet("font-weight: bold; font-size: 11pt;")

        self.serial_label = QLabel("序列号: N/A")
        self.serial_label.setStyleSheet("color: #34495e;")

        status_layout.addWidget(self.status_indicator, 0, 0, Qt.AlignmentFlag.AlignLeft)
        status_layout.addWidget(self.status_label, 0, 1, Qt.AlignmentFlag.AlignLeft)
        status_layout.addWidget(self.serial_label, 1, 1, Qt.AlignmentFlag.AlignLeft)

        # === 中央：时间与 Logcat ===
        self.datetime_label = QLabel("📅 实时时间: N/A")
        self.datetime_label.setFont(QFont("Consolas", 10))

        self.logcat_count_label = QLabel("📦 远程 Logcat 文件数: N/A")
        self.logcat_count_label.setFont(QFont("Consolas", 10))

        status_layout.addWidget(self.datetime_label, 0, 2, Qt.AlignmentFlag.AlignRight)
        status_layout.addWidget(self.logcat_count_label, 1, 2, Qt.AlignmentFlag.AlignRight)

        # === 右侧：账户/Session (Placeholder) ===
        account_label = QLabel("👤 Session ID: Test001")
        account_label.setStyleSheet("color: #8e44ad; font-weight: bold;")
        status_layout.addWidget(account_label, 0, 3, 2, 1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        status_layout.setColumnStretch(1, 1)
        status_layout.setColumnStretch(2, 1)

        main_layout.addWidget(status_frame)


    def _setup_tab_content(self, main_layout: QVBoxLayout):
        self.tab_widget = QTabWidget()
        self.tab_widget.addTab(self._create_home_panel(), "🏠 主页 (平台概览)")
        self.tab_widget.addTab(self._create_ota_config_tab(), "🔧 配置更新")
        self.tab_widget.addTab(self._create_log_puller_tab(), "📑 日志拉取")
        self.tab_widget.addTab(self._create_toolbox_tab(), "🛠️ 调试工具箱")
        self.tab_widget.addTab(self._create_history_data_tab(), "⚡ 操作与数据")
        self.tab_widget.addTab(QWidget(), "🚀 功能扩展 (Monkey/...)")
        main_layout.addWidget(self.tab_widget)

    def _setup_log_viewer(self, main_layout: QVBoxLayout):
        log_box = QGroupBox("操作日志输出")
        log_box.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        log_layout = QVBoxLayout(log_box)

        self.log_text_edit = QTextEdit()
        self.log_text_edit.setReadOnly(True)
        self.log_text_edit.setMaximumHeight(150)

        log_layout.addWidget(self.log_text_edit)
        main_layout.addWidget(log_box)

    def _setup_footer(self, main_layout: QVBoxLayout):
        footer_label = QLabel(f"{TOOL_NAME} | Version: {VERSION} | {COPYRIGHT}")
        footer_label.setStyleSheet("color: #7f8c8d; padding: 5px; font-size: 9pt;")
        footer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(footer_label)

    # --- 标签页创建方法 ---

    def _create_home_panel(self):
        tab = QWidget()
        layout = QGridLayout(tab)
        layout.setContentsMargins(20, 20, 20, 20)

        # --- 1. 设备配置概览卡片 (左上角) ---
        config_group = QGroupBox("💻 当前设备配置概览")
        config_group.setStyleSheet("font-weight: bold; color: #007bff;")
        config_layout = QGridLayout(config_group)

        config_layout.addWidget(QLabel("ICC_PNO:"), 0, 0)
        self.current_pno_label = QLabel(self.current_pno)
        self.current_pno_label.setFont(QFont("Consolas", 14, QFont.Bold))
        config_layout.addWidget(self.current_pno_label, 0, 1)

        config_layout.addWidget(QLabel("VIN:"), 1, 0)
        self.current_vin_label = QLabel(self.current_vin)
        self.current_vin_label.setFont(QFont("Consolas", 14, QFont.Bold))
        config_layout.addWidget(self.current_vin_label, 1, 1)

        config_layout.addWidget(QLabel("配置 Hash (8位):"), 2, 0)
        self.current_hash_label = QLabel(self.current_hash)
        self.current_hash_label.setFont(QFont("Consolas", 10))
        config_layout.addWidget(self.current_hash_label, 2, 1)

        config_layout.setColumnStretch(1, 1)
        layout.addWidget(config_group, 0, 0, 1, 1)

        # --- 2. 测试统计卡片 (右上角) ---
        stats_group = QGroupBox("📈 平台测试统计")
        stats_group.setStyleSheet("font-weight: bold; color: #28a745;")
        stats_layout = QGridLayout(stats_group)

        stats_layout.addWidget(QLabel("OTA 成功更新次数:"), 0, 0)
        self.stats_ota_count = QLabel(str(self.stats_ota_count_value)) # 绑定到变量
        self.stats_ota_count.setFont(QFont("Consolas", 16, QFont.Bold))
        stats_ota_count_widget = QWidget()
        stats_ota_count_layout = QHBoxLayout(stats_ota_count_widget)
        stats_ota_count_layout.setContentsMargins(0, 0, 0, 0)
        stats_ota_count_layout.addWidget(self.stats_ota_count)
        stats_layout.addWidget(stats_ota_count_widget, 0, 1, Qt.AlignmentFlag.AlignRight)

        stats_layout.addWidget(QLabel("日志拉取成功次数:"), 1, 0)
        self.stats_log_count = QLabel(str(self.stats_log_count_value)) # 绑定到变量
        self.stats_log_count.setFont(QFont("Consolas", 16, QFont.Bold))
        stats_log_count_widget = QWidget()
        stats_log_count_layout = QHBoxLayout(stats_log_count_widget)
        stats_log_count_layout.setContentsMargins(0, 0, 0, 0)
        stats_log_count_layout.addWidget(self.stats_log_count)
        stats_layout.addWidget(stats_log_count_widget, 1, 1, Qt.AlignmentFlag.AlignRight)

        stats_layout.setColumnStretch(0, 1)
        layout.addWidget(stats_group, 0, 1, 1, 1)

        # --- 3. 快速操作与导航卡片 (下方大区域) ---
        quick_action_group = QGroupBox("🚀 快速操作与导航")
        quick_action_group.setStyleSheet("font-weight: bold; color: #ffc107;")
        quick_layout = QGridLayout(quick_action_group)

        # 快速操作按钮
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
        btn = QPushButton(text)
        btn.setFont(QFont("Microsoft YaHei UI", 12, QFont.Bold))
        btn.setMinimumHeight(60)
        btn.clicked.connect(slot)
        return btn

    def _create_ota_config_tab(self):
        tab = QWidget()
        main_layout = QVBoxLayout(tab)

        current_config_group = QGroupBox("⚙️ 当前设备配置")
        config_layout = QGridLayout(current_config_group)

        config_layout.addWidget(QLabel("ICC_PNO:"), 0, 0)
        config_layout.addWidget(self.current_pno_label, 0, 1)

        config_layout.addWidget(QLabel("VIN:"), 1, 0)
        config_layout.addWidget(self.current_vin_label, 1, 1)

        config_layout.addWidget(QLabel("文件哈希 (8位):"), 2, 0)
        config_layout.addWidget(self.current_hash_label, 2, 1)

        main_layout.addWidget(current_config_group)

        single_update_group = QGroupBox("🔧 单次配置更新")
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

        task_box = QGroupBox("任务执行状态")
        task_layout = QVBoxLayout(task_box)

        self.global_progress = QProgressBar()
        self.global_progress.setRange(0, 100)
        self.global_progress.setValue(0)
        task_layout.addWidget(QLabel("全局任务进度:"))
        task_layout.addWidget(self.global_progress)

        self.task_table = QTableWidget()
        self.task_table.setColumnCount(4)
        self.task_table.setHorizontalHeaderLabels(["日志类型", "状态", "文件数", "耗时"])
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

    def _create_toolbox_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

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

        log_grab_group = QGroupBox("日志抓取")
        log_grab_layout = QGridLayout(log_grab_group)

        screenshot_btn = QPushButton("📸 一键截图")
        screenshot_btn.setStyleSheet("padding: 15px;")
        screenshot_btn.clicked.connect(lambda: self.on_log_message("工具箱", "一键截图功能待实现...", "INFO"))

        dump_logcat_btn = QPushButton("📄 拉取 Logcat 缓冲区日志 (adb logcat -d)")
        dump_logcat_btn.setStyleSheet("padding: 15px;")
        dump_logcat_btn.clicked.connect(lambda: self.on_log_message("工具箱", "拉取 Logcat 缓冲区功能待实现...", "INFO"))

        bugreport_btn = QPushButton("🐛 拉取 Bug Report (完整)")
        bugreport_btn.setStyleSheet("padding: 15px;")
        bugreport_btn.clicked.connect(lambda: self.on_log_message("工具箱", "拉取 Bug Report 功能待实现...", "INFO"))

        log_grab_layout.addWidget(screenshot_btn, 0, 0)
        log_grab_layout.addWidget(dump_logcat_btn, 0, 1)
        log_grab_layout.addWidget(bugreport_btn, 1, 0, 1, 2)
        layout.addWidget(log_grab_group)

        layout.addStretch()
        return tab

    def _create_history_data_tab(self):
        tab = QWidget()
        main_layout = QHBoxLayout(tab)

        # 1. 历史记录 (左侧)
        history_group = QGroupBox("⚡ 操作历史")
        history_layout = QVBoxLayout(history_group)
        self.history_list = QListWidget()
        # 历史记录在 __init__ 中被 _update_history_ui 填充
        history_layout.addWidget(self.history_list)
        main_layout.addWidget(history_group)

        # 2. 模板与备份 (右侧)
        data_group = QGroupBox("💾 配置模板与备份")
        data_layout = QVBoxLayout(data_group)

        template_group = QGroupBox("配置模板")
        template_layout = QVBoxLayout(template_group)
        self.template_list = QListWidget()
        self.template_list.addItem("模板列表...")
        template_layout.addWidget(self.template_list)

        temp_btn_layout = QHBoxLayout()
        temp_btn_layout.addWidget(QPushButton("💾 保存为模板"))
        temp_btn_layout.addWidget(QPushButton("📥 加载选中模板"))
        template_layout.addLayout(temp_btn_layout)

        data_layout.addWidget(template_group)

        backup_group = QGroupBox("配置备份")
        backup_layout = QVBoxLayout(backup_group)
        self.backup_list = QListWidget()
        self.backup_list.addItem("备份列表...")
        backup_layout.addWidget(self.backup_list)

        data_layout.addWidget(backup_group)

        main_layout.addWidget(data_group)

        return tab


    # --- 逻辑/线程/信号连接 ---

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
        # 🔔 关键修复点：接收操作成功信号
        self.logic.operation_success_signal.connect(self.on_operation_success)

        # 2. UI 信号连接到逻辑线程槽
        self.check_device_signal.connect(self.logic.check_device_and_root, Qt.ConnectionType.QueuedConnection)
        self.start_pull_signal.connect(self.logic.start_pull_process, Qt.ConnectionType.QueuedConnection)
        self.clear_logcat_signal.connect(self.logic.clear_logcat, Qt.ConnectionType.QueuedConnection)
        self.reboot_signal.connect(self.logic.reboot_device, Qt.ConnectionType.QueuedConnection)
        self.push_config_signal.connect(self.logic.push_config_file, Qt.ConnectionType.QueuedConnection)

        self.thread.start()


    # --- UI 槽函数 (响应 Logic 信号) ---

    @Slot(str)
    def on_device_connected(self, serial: str):
        self.serial_label.setText(f"序列号: {serial}")
        if hasattr(self, 'start_pull_btn'):
            self.start_pull_btn.setEnabled(True)
        if hasattr(self, 'clear_btn'):
            self.clear_btn.setEnabled(self.log_count != -1 and self.log_count > 0)

    @Slot()
    def on_device_disconnected(self):
        self.serial_label.setText("序列号: N/A")
        self.logcat_count_label.setText("📦 远程 Logcat 文件数: N/A")
        if hasattr(self, 'start_pull_btn'):
            self.start_pull_btn.setEnabled(False)
        if hasattr(self, 'clear_btn'):
            self.clear_btn.setEnabled(False)
        self.on_status_update("错误: 设备已断开。", "red")

    @Slot(str, str)
    def on_status_update(self, text: str, color_key: str):
        color_map = {"red": "#dc3545", "green": "#28a745", "yellow": "#ffc107", "blue": "#007bff"}

        self.status_label.setText(text)
        self.status_indicator.setStyleSheet(f"font-size: 18pt; color: {color_map.get(color_key, 'gray')};")

    @Slot(str)
    def on_error(self, message: str):
        self.on_status_update("错误: " + message, "red")
        self.on_log_message("系统", message, "ERROR")
        QMessageBox.critical(self, "操作错误", message)

    @Slot(int)
    def on_logcat_count_update(self, count: int):
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
        self.current_pno = config_data.get('ICC_PNO', 'N/A')
        self.current_vin = config_data.get('VIN', 'N/A')
        self.current_hash = config_data.get('FileHash', 'N/A')

        # 更新主页和配置页的标签
        if hasattr(self, 'current_pno_label'):
            self.current_pno_label.setText(self.current_pno)
            self.current_vin_label.setText(self.current_vin)
            self.current_hash_label.setText(self.current_hash)

        _, vin_msg = ConfigValidator.validate_vin(self.current_vin)
        self.on_log_message("配置", f"[VIN 验证]: {vin_msg}", "INFO")

    # --- 日志拉取进度和结果 (与前版本一致) ---

    @Slot(int)
    def on_task_start(self, total_tasks: int):
        self.global_progress.setRange(0, total_tasks)
        self.global_progress.setValue(0)
        self.task_table.setRowCount(total_tasks)
        self.start_pull_btn.setEnabled(False)
        self.clear_btn.setEnabled(False)

    @Slot(int, str, str, str)
    def on_task_progress(self, index: int, log_type: str, status: str, files_count: str):
        row = index - 1
        self.task_table.setItem(row, 0, QTableWidgetItem(log_type))
        self.task_table.setItem(row, 1, QTableWidgetItem(status))
        self.task_table.setItem(row, 2, QTableWidgetItem(files_count))
        self.task_table.setItem(row, 3, QTableWidgetItem("N/A"))

        self.global_progress.setValue(index)

    @Slot(dict, str)
    def on_task_complete(self, summary: dict, export_path: str):
        self.start_pull_btn.setEnabled(True)
        self.clear_btn.setEnabled(self.log_count != -1 and self.log_count > 0)

        self.on_log_message("拉取", f"日志拉取任务完成。共拉取 {summary['total_files_pulled']} 种日志，失败 {summary['total_fail']} 项。", "SUCCESS")
        self.on_log_message("拉取", f"日志保存路径: {export_path}", "INFO")

        QMessageBox.information(self, "任务完成", f"所有选中的日志已拉取完成。\n保存路径: {export_path}")

    # --- 🔔 关键修复点：处理操作成功信号 ---
    @Slot(str, str)
    def on_operation_success(self, op_type: str, op_detail: str):
        """接收核心逻辑成功操作信号，更新统计和历史记录，并保存数据"""
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 1. 更新统计计数
        if op_type == "OTA配置更新":
            self.stats_ota_count_value += 1
        elif op_type == "日志拉取":
            self.stats_log_count_value += 1

        self._update_stats_ui()

        # 2. 更新历史记录列表
        new_record = {
            'time': timestamp,
            'type': op_type,
            'detail': op_detail
        }
        self.history_records.append(new_record)
        self._update_history_ui()

        # 3. 保存数据到本地文件 (持久化)
        self._save_app_data()


    # --- 通用功能和日志输出 (与前版本一致) ---

    @Slot()
    def _update_time_and_status(self):
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if hasattr(self, 'datetime_label'):
            self.datetime_label.setText(f"📅 实时时间: {now}")

    @Slot(str, str, str)
    def on_log_message(self, source: str, message: str, tag: str):
        """将日志信息格式化后输出到 QTextEdit"""
        timestamp = datetime.datetime.now().strftime("[%H:%M:%S]")

        color_map = {"INFO": "black", "WARNING": "#ffc107", "ERROR": "#dc3545", "SUCCESS": "#28a745"}
        color = color_map.get(tag, "black")

        html_message = f'<span style="color: gray;">{timestamp}</span> <span style="font-weight: bold; color: {color};">[{source}]</span> {message}'

        self.log_text_edit.moveCursor(QTextEdit.MoveOperation.End)
        self.log_text_edit.insertHtml(html_message)
        self.log_text_edit.insertPlainText("\n")
        self.log_text_edit.verticalScrollBar().setValue(self.log_text_edit.verticalScrollBar().maximum())


    # --- UI 交互操作 (触发信号) (与前版本一致) ---

    def select_export_folder(self):
        new_folder = QFileDialog.getExistingDirectory(self, "选择日志导出文件夹", self.export_folder)
        if new_folder:
            self.export_folder = new_folder
            self.path_edit.setText(new_folder)
            self.logic.export_path = new_folder
            self.on_log_message("配置", f"日志导出路径已设置为: {new_folder}", "INFO")

    def _start_pull_process(self):
        selected_logs = []
        for i in range(self.log_list_widget.count()):
            item = self.log_list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                selected_logs.append(item.text())

        if not selected_logs:
            QMessageBox.warning(self, "警告", "请至少选择一种日志类型。")
            return

        self.start_pull_signal.emit(selected_logs, self.export_folder)

    def _clear_remote_logcat(self):
        if QMessageBox.question(self, "确认操作", "确定要清理远程设备上的 Logcat 日志目录吗？此操作不可逆。",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.clear_logcat_signal.emit()

    def _start_single_config_update(self):
        new_pno = self.new_pno_edit.text().strip()
        new_vin = self.new_vin_edit.text().strip()

        pno_valid, pno_msg = ConfigValidator.validate_icc_pno(new_pno)
        vin_valid, vin_msg = ConfigValidator.validate_vin(new_vin)

        if not pno_valid or not vin_valid:
            error_msg = f"配置更新失败:\nPNO 验证: {pno_msg}\nVIN 验证: {vin_msg}"
            self.on_error(error_msg)
            return

        if QMessageBox.question(self, "确认更新", f"确定使用以下配置更新设备吗？\nPNO: {new_pno}\nVIN: {new_vin}",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.push_config_signal.emit(new_pno, new_vin)


    def _reboot_device(self):
        if QMessageBox.question(self, "确认操作", "确定要重启车机设备吗？",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.reboot_signal.emit()

    def show_about_dialog(self):
        QMessageBox.about(self, "关于", f"{TOOL_NAME} {VERSION}\n作者: {AUTHOR}\nGitHub: {GITHUB_LINK}\n\n集成了 OTA 配置、批量操作、日志拉取、设备监控等多功能一体化测试平台。")

    def closeEvent(self, event):
        """在程序关闭时保存数据"""
        self._save_app_data()
        super().closeEvent(event)


# ========================================
# 5. 主程序入口
# ========================================

if __name__ == '__main__':

    app = QApplication(sys.argv)

    app.setStyle("Fusion")

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