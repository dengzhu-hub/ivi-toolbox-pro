import sys
import subprocess
import datetime
import shutil
from pathlib import Path
import time

# 导入 QTimer
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QLineEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QProgressBar, QFileDialog, QGroupBox,
    QListWidget, QListWidgetItem, QMessageBox, QHeaderView, QMenuBar, QMenu, QPlainTextEdit, QDialog
)
from PySide6.QtCore import (
    QObject, QThread, Signal, Slot, Qt, QSize, QTimer
)
from PySide6.QtGui import (
    QColor, QPalette, QAction, QFont
)

# ========================================
# 1. 配置和元信息
# ========================================

TOOL_NAME = "Adayo 车载日志拉取工具 GUI"
VERSION = "2.0.5 (Path 属性访问修复)" # 最终版本号：修复 Logcat 计数错误导致的清理逻辑
AUTHOR = "Jonas (深圳海冰科技 测试工程师)"
GITHUB_LINK = "dengzhu-hub"

LOG_TYPES = [
    "logcat", "anr", "setting", "systemproperty", "config", "kernel",
    "btsnoop", "tombstones", "dropbox", "resource", "mcu", "aee", "ael", "upgrade"
]
REMOTE_LOG_PATH = "/mnt/sdcard/AdayoLog"
WLAN_LOG_TYPE = "wlan_logs"
WLAN_LOG_PATH = "/data/vendor/wifi/wlan_logs"

ALL_LOG_TYPES = LOG_TYPES + [WLAN_LOG_TYPE]

# ========================================
# 2. 核心逻辑 (LogPullerLogic)
# ========================================

class LogPullerLogic(QObject):
    """
    包含所有 ADB 和文件操作的核心逻辑。
    """
    # 信号定义
    device_connected_signal = Signal(str)      # 序列号
    device_disconnected_signal = Signal()      # 断开连接
    device_status_signal = Signal(str, str)    # 状态文本, 颜色 (red/green/yellow)
    task_start_signal = Signal(int)
    task_progress_signal = Signal(int, str, str, str)
    task_complete_signal = Signal(dict, str)
    error_signal = Signal(str)
    remote_file_count_signal = Signal(int) # 用于更新 Logcat 远程文件数量

    def __init__(self, serial=None, export_path=None, selected_logs=None):
        super().__init__()
        self.serial = serial
        self.export_path = export_path
        self.selected_logs = selected_logs or []

    # ADB 基础命令执行函数 (保持不变)
    def run_adb_command(self, command: list, serial: str = None, check_output: bool = False):
        serial = serial or self.serial
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
                timeout=120
            )

            if check_output:
                return result.stdout.strip()

            return result.returncode == 0

        except FileNotFoundError:
            self.error_signal.emit("ADB tool not found. Please ensure ADB is in your system PATH.")
            return False
        except subprocess.TimeoutExpired:
            self.error_signal.emit(f"Command timed out: {' '.join(command)}")
            return False
        except Exception as e:
            self.error_signal.emit(f"ADB execution failed: {e}")
            return False

    def count_remote_files(self, remote_path: str) -> int:
        """运行 ADB 命令统计远程目录下文件数量"""
        count_cmd = ["shell", f"find {remote_path} -type f | wc -l"]
        output = self.run_adb_command(count_cmd, check_output=True)
        try:
            return int(output.strip().split()[-1])
        except Exception:
            return -1 # 返回 -1 表示无法访问或发生错误

    def count_remote_logcat(self):
        """【V2.0.4新增】统计远程 Logcat 目录下的文件数量并发出信号。"""
        if not self.serial:
            self.remote_file_count_signal.emit(-1)
            return

        logcat_path_str = str(Path(REMOTE_LOG_PATH) / "logcat")
        count = self.count_remote_files(logcat_path_str)

        self.remote_file_count_signal.emit(count)
        return count

    @Slot()
    def check_device_and_root(self):
        """
        初始化检查/重新连接：检查设备连接、尝试 Root，并设置监控所需的 self.serial。
        V2.0.3 修复重连逻辑，V2.0.4 修复计数逻辑。
        """
        self.device_status_signal.emit("正在检查设备连接...", "yellow")

        output = self.run_adb_command(["devices"], check_output=True)
        devices = []
        if output:
            lines = output.split('\n')
            for line in lines[1:]:
                if line.strip() and "device" in line and "unauthorized" not in line:
                    serial = line.split('\t')[0]
                    devices.append(serial)

        if len(devices) != 1:
            # 检查失败，更新状态
            self.device_status_signal.emit("错误: 未找到单个已连接设备。", "red")
            self.serial = None
            self.remote_file_count_signal.emit(-1) # V2.0.4: 连接失败也发出 -1 信号
            return

        # 设置序列号并发送连接成功信号
        self.serial = devices[0]
        self.device_connected_signal.emit(self.serial)

        # 尝试 Root
        self.device_status_signal.emit(f"设备已连接 ({self.serial})，尝试 Root...", "yellow")
        self.run_adb_command(["root"])
        time.sleep(3) # 等待 adbd 重启

        # 再次确认连接
        output_remount = self.run_adb_command(["remount"], check_output=True)
        if "succeeded" in output_remount.lower():
            self.device_status_signal.emit(f"连接成功 ({self.serial})，权限已增强。", "green")
        else:
            self.device_status_signal.emit(f"连接成功 ({self.serial})，Remount 失败。", "yellow")

        # 【V2.0.4 修复点】：连接成功后，立即检查 Logcat 数量 (用于清理按钮)
        self.count_remote_logcat()


    @Slot()
    def monitor_device_status(self):
        """V2.0.3 修复：周期性检查设备连接状态或尝试重新连接。"""
        # 1. 检查当前是否有设备连接
        output = self.run_adb_command(["devices"], check_output=True)
        current_devices = []
        if output:
            lines = output.split('\n')
            for line in lines[1:]:
                if line.strip() and "device" in line and "unauthorized" not in line:
                    current_devices.append(line.split('\t')[0])

        # 场景 A: 当前是连接状态 (self.serial 有值)
        if self.serial:
            if self.serial not in current_devices:
                # 丢失连接 -> 触发断开逻辑
                self.serial = None
                self.device_disconnected_signal.emit()
            else:
                # 设备仍连接，确保状态正确
                self.device_status_signal.emit(f"连接成功 ({self.serial})，权限已增强。", "green")

        # 场景 B: 当前是断开状态 (self.serial 为 None)
        elif not self.serial:
            if len(current_devices) == 1:
                # 发现一个新连接的设备，触发完整的连接流程 (会包含 Logcat 计数)
                self.check_device_and_root()
            elif len(current_devices) == 0:
                # 仍然没有设备连接，保持断开状态
                self.device_status_signal.emit("错误: 未找到单个已连接设备。", "red")
                self.remote_file_count_signal.emit(-1) # V2.0.4: 确保断开时计数显示 N/A
            else:
                # 发现多个设备
                self.device_status_signal.emit("错误: 发现多个设备，请断开多余设备。", "red")


    @Slot()
    def start_pull_process(self):
        """开始拉取任务。 (保持不变)"""
        if not self.serial or not self.export_path:
            self.error_signal.emit("设备未连接或导出路径未设置。")
            return

        self.device_status_signal.emit("任务进行中...", "blue")

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        export_path = Path(self.export_path) / f"AdayoLog_{timestamp}"
        export_path.mkdir(parents=True, exist_ok=True)

        tasks = []
        for log_type in LOG_TYPES:
            if log_type in self.selected_logs:
                tasks.append((log_type, f"{REMOTE_LOG_PATH}/{log_type}", export_path / log_type))
        if WLAN_LOG_TYPE in self.selected_logs:
            tasks.append((WLAN_LOG_TYPE, WLAN_LOG_PATH, export_path / WLAN_LOG_TYPE))

        total_tasks = len(tasks)
        if total_tasks == 0:
            self.error_signal.emit("未选择任何日志类型。")
            self.device_status_signal.emit(f"连接成功 ({self.serial})", "green")
            return

        self.task_start_signal.emit(total_tasks)

        results_summary = []
        total_files_pulled = 0
        total_empty_pulled = 0
        total_fail = 0

        for i, (log_type, remote_path, local_target) in enumerate(tasks):
            i += 1

            # ** 任务开始前再次检查连接 **
            if not self.serial:
                self.error_signal.emit(f"设备在任务 [{log_type}] 开始前断开连接，任务中止。")
                self.device_disconnected_signal.emit()
                return

            status_text = "拉取中..."
            self.task_progress_signal.emit(i, log_type, status_text, "N/A")

            # WLAN 目录特殊处理，直接拉到导出根目录
            if log_type == WLAN_LOG_TYPE:
                pull_cmd = ["pull", remote_path, str(export_path)]
            else:
                pull_cmd = ["pull", remote_path, str(local_target)]

            result = subprocess.run(
                ["adb", "-s", self.serial] + pull_cmd,
                capture_output=True,
                text=True,
                check=False,
                encoding='utf-8',
                timeout=300
            )

            output = result.stdout.strip()
            error = result.stderr.strip()

            is_success = (result.returncode == 0 and
                          "pull failed" not in error.lower() and
                          "no such file" not in error.lower() and
                          "0 files pulled" not in output.lower())

            file_count = 0

            if is_success:
                final_local_path = export_path / log_type if log_type == WLAN_LOG_TYPE else local_target

                if final_local_path.exists():
                    # 递归统计拉取到的文件数
                    file_count = sum(1 for item in final_local_path.rglob('*') if item.is_file())

                    if file_count > 0:
                        status_text = "成功"
                        total_files_pulled += 1
                    else:
                        status_text = "空目录"
                        total_empty_pulled += 1
                        # 自动清理拉取到的空目录
                        if final_local_path.is_dir():
                            try:
                                shutil.rmtree(final_local_path)
                            except OSError:
                                pass
                else:
                    status_text = "失败 (I/O Error)"
                    total_fail += 1
            else:
                status_text = "失败 (ADB Error)"
                total_fail += 1

            file_count_str = f"{file_count} 个文件" if file_count > 0 else ("已清理" if status_text == "空目录" else "N/A")
            self.task_progress_signal.emit(i, log_type, status_text, file_count_str)

            results_summary.append({
                'log_type': log_type,
                'status': status_text,
                'files': file_count,
            })

        summary = {
            'total_files_pulled': total_files_pulled,
            'total_empty_pulled': total_empty_pulled,
            'total_fail': total_fail,
            'results': results_summary
        }
        self.task_complete_signal.emit(summary, str(export_path))

    @Slot()
    def clear_logcat(self):
        """执行 Logcat 远程清理操作。"""
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
        success = self.run_adb_command(clear_cmd)

        if success:
            # 【V2.0.4 修复点】：清理后强制重新计数
            files_after = self.count_remote_logcat()

            if files_after == 0:
                self.device_status_signal.emit(f"Logcat 清理成功! ({files_before} -> 0)", "green")
            else:
                self.device_status_signal.emit(f"Logcat 清理失败! (残留 {files_after} 个文件)", "yellow")
        else:
            self.device_status_signal.emit("Logcat 清理命令执行失败。", "red")

        # 清理完成后，再次发送一次绿色状态，确保不会被其他中间状态覆盖
        self.device_status_signal.emit(f"连接成功 ({self.serial})", "green")


# ========================================
# 3. GUI 主窗口 (PySide6)
# ========================================

class HelpManualWindow(QDialog):
    """用于显示帮助手册内容的独立窗口。（保持不变）"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Adayo 日志拉取工具 - 帮助手册")
        self.setMinimumSize(800, 600)

        layout = QVBoxLayout(self)

        self.text_editor = QPlainTextEdit()
        self.text_editor.setReadOnly(True)
        # 更新版本号信息到手册
        manual_text_v204 = MANUAL_TEXT.replace("V2.0.3", "V2.0.4")
        self.text_editor.setPlainText(manual_text_v204)

        font = self.text_editor.font()
        font.setPointSize(10)
        self.text_editor.setFont(font)

        layout.addWidget(self.text_editor)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)


class MainWindow(QMainWindow):
    # 线程控制信号
    check_device_signal = Signal()
    start_pull_signal = Signal()
    clear_logcat_signal = Signal()
    monitor_device_signal = Signal()
    check_remote_logcat_signal = Signal() # 【V2.0.4新增】：用于任务完成后强制更新 Logcat 计数

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{TOOL_NAME} v{VERSION}")
        self.setMinimumSize(QSize(900, 600))

        self.current_serial = ""
        self.export_folder = str(Path.cwd() / "CarLogs")
        self.selected_log_types = ALL_LOG_TYPES
        self.logcat_file_count = -1
        self.current_tasks_total = 0

        # 添加时间更新定时器
        self.time_timer = QTimer(self)
        self.time_timer.setInterval(1000)  # 每秒更新一次
        self.time_timer.timeout.connect(self.update_time_display)
        self.time_timer.start()

        self._setup_logic_thread()
        self._setup_menubar()
        self._setup_ui()

        self.check_device_signal.emit()
        self._start_monitor_timer()

    @Slot()
    def update_time_display(self):
        """更新时间显示"""
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.time_label.setText(f"当前时间: {current_time}")

    # --- 启动定时器 (保持不变) ---
    def _start_monitor_timer(self):
        self.timer = QTimer(self)
        self.timer.setInterval(3000)  # 3000 毫秒 = 3 秒
        self.timer.timeout.connect(self.monitor_device_signal.emit)
        self.timer.start()

    # --- UI/Action/Signal 槽函数 (保持不变) ---
    def _setup_menubar(self):
        menu_bar = QMenuBar(self)
        self.setMenuBar(menu_bar)

        help_menu = menu_bar.addMenu("帮助(H)")

        help_action = QAction("帮助手册(M)", self)
        help_action.setShortcut("F1")
        help_action.triggered.connect(self.show_help_manual)
        help_menu.addAction(help_action)

        help_menu.addSeparator()

        about_action = QAction("关于(A)", self)
        about_action.triggered.connect(self.show_about_dialog)
        help_menu.addAction(about_action)

    def _setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        self._setup_status_bar(main_layout)

        content_h_layout = QHBoxLayout()

        self._setup_config_panel(content_h_layout)

        self._setup_task_panel(content_h_layout)

        main_layout.addLayout(content_h_layout)

        self._setup_action_bar(main_layout)

    def _setup_status_bar(self, main_layout: QVBoxLayout):
        status_box = QGroupBox("系统状态")
        status_layout = QHBoxLayout(status_box)

        self.status_indicator = QLabel("●")
        self.status_indicator.setStyleSheet("font-size: 16pt; color: gray;")
        self.status_indicator.setFixedWidth(20)

        self.status_label = QLabel("正在初始化...")
        self.status_label.setStyleSheet("font-weight: bold;")

        self.serial_label = QLabel("序列号: N/A")

        # 添加时间显示标签
        self.time_label = QLabel()
        self.time_label.setStyleSheet("font-weight: bold; color: #007BFF;")
        self.update_time_display()  # 初始化显示

        status_layout.addWidget(self.status_indicator)
        status_layout.addWidget(self.status_label)
        status_layout.addSpacing(20)
        status_layout.addWidget(self.serial_label)
        status_layout.addStretch()
        status_layout.addWidget(self.time_label)

        main_layout.addWidget(status_box)

    def _setup_config_panel(self, parent_layout: QHBoxLayout):
        config_box = QGroupBox("任务配置")
        config_box.setFixedWidth(300)
        config_layout = QVBoxLayout(config_box)

        path_group = QGroupBox("导出路径")
        path_layout = QHBoxLayout(path_group)
        self.path_edit = QLineEdit(self.export_folder)
        self.path_edit.setReadOnly(True)
        self.path_edit.setText(str(Path.cwd() / "CarLogs"))

        browse_btn = QPushButton("选择...")
        browse_btn.clicked.connect(self.select_export_folder)

        path_layout.addWidget(self.path_edit)
        path_layout.addWidget(browse_btn)
        config_layout.addWidget(path_group)

        log_type_group = QGroupBox("日志类型选择 (共 15 项)")
        log_type_layout = QVBoxLayout(log_type_group)
        self.log_list_widget = QListWidget()
        self.log_list_widget.setSelectionMode(QListWidget.MultiSelection)
        self.log_list_widget.setSelectionMode(QListWidget.ExtendedSelection)

        for log_type in ALL_LOG_TYPES:
            item = QListWidgetItem(log_type)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
            item.setCheckState(Qt.CheckState.Checked)
            self.log_list_widget.addItem(item)

        config_layout.addWidget(self.log_list_widget)
        parent_layout.addWidget(config_box)

    def _setup_task_panel(self, parent_layout: QHBoxLayout):
        task_box = QGroupBox("任务执行状态")
        task_layout = QVBoxLayout(task_box)

        self.global_progress = QProgressBar()
        self.global_progress.setRange(0, 1)
        self.global_progress.setValue(0)
        task_layout.addWidget(QLabel("全局任务进度:"))
        task_layout.addWidget(self.global_progress)

        self.task_table = QTableWidget()
        self.task_table.setColumnCount(4)
        self.task_table.setHorizontalHeaderLabels(["日志类型", "状态", "文件数", "序号"])
        self.task_table.verticalHeader().setVisible(False)
        self.task_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.task_table.setRowCount(len(ALL_LOG_TYPES))

        for i, log_type in enumerate(ALL_LOG_TYPES):
            self.task_table.setItem(i, 0, QTableWidgetItem(log_type))
            self.task_table.setItem(i, 1, QTableWidgetItem("待运行"))
            self.task_table.setItem(i, 2, QTableWidgetItem("N/A"))
            self.task_table.setItem(i, 3, QTableWidgetItem(str(i + 1)))
            self.task_table.item(i, 1).setForeground(QColor("gray"))
            self.task_table.item(i, 3).setTextAlignment(Qt.AlignmentFlag.AlignCenter)

        task_layout.addWidget(self.task_table)
        parent_layout.addWidget(task_box)

    def _setup_action_bar(self, main_layout: QVBoxLayout):
        action_layout = QHBoxLayout()

        self.start_btn = QPushButton("▶️ 启动日志拉取 (步骤 3/4)")
        self.start_btn.setMinimumHeight(40)
        self.start_btn.setStyleSheet("font-size: 14pt; background-color: #4CAF50; color: white;")
        self.start_btn.clicked.connect(self.start_pull_clicked)
        self.start_btn.setEnabled(False)

        self.clear_btn = QPushButton("🗑️ 清理设备 Logcat 日志")
        self.clear_btn.setMinimumHeight(40)
        self.clear_btn.setStyleSheet("font-size: 12pt; background-color: #ff9800; color: white;")
        self.clear_btn.clicked.connect(self.clear_logcat_clicked)
        self.clear_btn.setEnabled(False)

        self.open_folder_btn = QPushButton("📁 打开日志目录")
        self.open_folder_btn.setMinimumHeight(40)
        self.open_folder_btn.clicked.connect(self.open_export_folder)
        self.open_folder_btn.setEnabled(False)

        action_layout.addWidget(self.start_btn)
        action_layout.addWidget(self.clear_btn)
        action_layout.addWidget(self.open_folder_btn)
        main_layout.addLayout(action_layout)

    @Slot()
    def show_about_dialog(self):
        about_text = (
            f"<p style='font-size:16pt; font-weight:bold;'>{TOOL_NAME}</p>"
            f"<p>版本: <span style='font-weight:bold; color:#4CAF50;'>{VERSION}</span></p>"
            f"<hr>"
            f"<p>此工具由 <b>{AUTHOR}</b> 定制与开发。</p>"
            f"<p>定制化标识: <span style='font-style:italic; color:#007BFF;'>{GITHUB_LINK}</span></p>"
            f"<p>本项目旨在为 {Path(REMOTE_LOG_PATH).parts[-1]} 及 {Path(WLAN_LOG_PATH).parts[-1]} 日志提供专业、高效的拉取解决方案。</p>"
            f"<p>版权所有 © 深圳海冰科技</p>"
        )

        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("关于本工具")
        msg_box.setText(about_text)
        msg_box.setIcon(QMessageBox.Icon.Information)
        msg_box.exec()

    @Slot()
    def show_help_manual(self):
        manual_window = HelpManualWindow(self)
        manual_window.exec()


    @Slot()
    def select_export_folder(self):
        new_folder = QFileDialog.getExistingDirectory(self, "选择日志导出目录", self.export_folder)
        if new_folder:
            self.export_folder = new_folder
            self.path_edit.setText(new_folder)

    @Slot()
    def start_pull_clicked(self):
        if not self.current_serial:
            QMessageBox.warning(self, "警告", "设备未连接，请检查ADB状态。")
            return

        self.start_btn.setEnabled(False)
        self.clear_btn.setEnabled(False)
        self.open_folder_btn.setEnabled(False)

        self.logic.serial = self.current_serial
        self.logic.export_path = self.export_folder

        selected = []
        for i in range(self.log_list_widget.count()):
            item = self.log_list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                selected.append(item.text())
        self.logic.selected_logs = selected

        for i in range(self.task_table.rowCount()):
            log_type = self.task_table.item(i, 0).text()
            if log_type in selected:
                self.task_table.item(i, 1).setText("等待中...")
                self.task_table.item(i, 1).setForeground(QColor("blue"))
            else:
                self.task_table.item(i, 1).setText("跳过")
                self.task_table.item(i, 1).setForeground(QColor("lightgray"))
                self.task_table.item(i, 2).setText("N/A")

        self.start_pull_signal.emit()

    @Slot()
    def clear_logcat_clicked(self):
        # 【V2.0.4 修复点】：使用 logcat_file_count 正确判断是否为空
        if self.logcat_file_count < 0:
            QMessageBox.information(self, "提示", "Logcat 目录状态未知，请等待连接成功后再试。")
            return

        if self.logcat_file_count == 0:
            QMessageBox.information(self, "提示", "Logcat 目录已空，无需清理。")
            return

        reply = QMessageBox.question(self, "⚠️ 确认清理 Logcat",
                                     f"当前远程 Logcat 目录包含 <b>{self.logcat_file_count}</b> 个文件。是否确认删除所有内容？",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)

        if reply == QMessageBox.StandardButton.Yes:
            self.start_btn.setEnabled(False)
            self.clear_btn.setEnabled(False)
            self.clear_logcat_signal.emit()


    @Slot()
    def open_export_folder(self):
        if not self.export_folder:
             QMessageBox.warning(self, "警告", "导出路径不存在。")
             return

        try:
            # 确保文件夹存在
            Path(self.export_folder).mkdir(parents=True, exist_ok=True)

            if sys.platform == "win32":
                subprocess.Popen(['explorer', self.export_folder])
            elif sys.platform == "darwin":
                subprocess.Popen(['open', self.export_folder])
            else:
                subprocess.Popen(['xdg-open', self.export_folder])
        except Exception as e:
            QMessageBox.critical(self, "错误", f"无法打开文件夹: {e}")

    # --- 线程和信号连接 ---
    def _setup_logic_thread(self):
        self.thread = QThread()
        self.logic = LogPullerLogic()
        self.logic.moveToThread(self.thread)

        self.logic.device_connected_signal.connect(self.on_device_connected)
        self.logic.device_disconnected_signal.connect(self.on_device_disconnected)
        self.logic.device_status_signal.connect(self.on_status_update)
        self.logic.task_start_signal.connect(self.on_task_start)
        self.logic.task_progress_signal.connect(self.on_task_progress)
        self.logic.task_complete_signal.connect(self.on_task_complete)
        self.logic.error_signal.connect(self.on_error)
        self.logic.remote_file_count_signal.connect(self.on_logcat_count_update)

        self.check_device_signal.connect(self.logic.check_device_and_root)
        self.start_pull_signal.connect(self.logic.start_pull_process)
        self.clear_logcat_signal.connect(self.logic.clear_logcat)
        self.monitor_device_signal.connect(self.logic.monitor_device_status)
        self.check_remote_logcat_signal.connect(self.logic.count_remote_logcat) # 【V2.0.4连接】

        self.thread.start()

    @Slot(str)
    def on_device_connected(self, serial: str):
        self.current_serial = serial
        self.serial_label.setText(f"序列号: {serial}")
        self.start_btn.setEnabled(True)
        # 清理按钮的启用逻辑交给 on_logcat_count_update 决定

    @Slot()
    def on_device_disconnected(self):
        self.current_serial = ""
        self.on_status_update("设备已断开连接，请重新插入。", "red")
        self.serial_label.setText("序列号: N/A")
        self.start_btn.setEnabled(False)
        self.clear_btn.setEnabled(False) # 断开时禁用
        self.open_folder_btn.setEnabled(False)


    @Slot(str, str)
    def on_status_update(self, status: str, color: str):
        self.status_label.setText(status)
        self.status_indicator.setStyleSheet(f"font-size: 16pt; color: {color};")

    @Slot(int)
    def on_logcat_count_update(self, count: int):
        """【V2.0.4 修复点】：更新 Logcat 文件计数和按钮文本/状态"""
        self.logcat_file_count = count

        # 只有在设备连接时，清理按钮才能点击
        self.clear_btn.setEnabled(self.current_serial != "")

        if count > 0:
            self.clear_btn.setText(f"🗑️ 清理 Logcat ({count} 个文件)")
        elif count == 0:
            self.clear_btn.setText("🗑️ Logcat 已空")
        else: # count == -1 (无法访问或连接失败)
            self.clear_btn.setText("🗑️ 清理 Logcat (N/A - 无法访问)")

    @Slot(int)
    def on_task_start(self, total_tasks: int):
        self.current_tasks_total = total_tasks
        self.global_progress.setRange(0, total_tasks)
        self.global_progress.setValue(0)
        self.task_table.setEnabled(True)

    @Slot(int, str, str, str)
    def on_task_progress(self, current: int, log_type: str, status: str, file_count: str):
        self.global_progress.setValue(current)

        for i in range(self.task_table.rowCount()):
            if self.task_table.item(i, 0).text() == log_type:
                self.task_table.item(i, 1).setText(status)
                self.task_table.item(i, 2).setText(file_count)

                color = "green"
                if "失败" in status or "中止" in status:
                    color = "red"
                elif "空目录" in status:
                    color = "orange"
                elif "成功" in status:
                    color = "green"

                self.task_table.item(i, 1).setForeground(QColor(color))
                break

    @Slot(dict, str)
    def on_task_complete(self, summary: dict, export_path_str: str): # <--- 变量名改为 export_path_str 更清晰
                # 【V2.0.5 修复点】：将接收到的字符串路径转换为 Path 对象，才能使用 .name 属性
                export_path = Path(export_path_str)

                self.export_folder = str(export_path) # 将 Path 对象转回 str 赋值给 self.export_folder
                self.path_edit.setText(self.export_folder)
                self.global_progress.setValue(self.current_tasks_total)
                self.start_btn.setEnabled(True)
                self.open_folder_btn.setEnabled(True)

                self.on_status_update(f"拉取完成! ({summary['total_files_pulled']} 个文件成功)", "green")

                # 任务完成后，强制更新一次远程 Logcat 文件计数
                self.check_remote_logcat_signal.emit()

                QMessageBox.information(self, "任务完成",
                                        f"所有日志已成功拉取！\n\n"
                                        f"总文件数: {summary['total_files_pulled']} 项\n"
                                        f"空目录: {summary['total_empty_pulled']} 项\n"
                                        f"失败项: {summary['total_fail']} 项\n\n"
                                        f"日志已保存至: {export_path.name}", # <--- 现在 export_path 是 Path 对象，可以安全地使用 .name
                                        QMessageBox.StandardButton.Ok)

                self.open_export_folder()
    @Slot(str)
    def on_error(self, message: str):
        self.on_status_update(f"致命错误: {message}", "red")
        QMessageBox.critical(self, "致命错误", message)
        self.start_btn.setEnabled(False)
        self.clear_btn.setEnabled(False)

# ========================================
# 4. 程序入口
# ========================================

if __name__ == "__main__":

    # 帮助手册内容（V2.0.4 版本更新）
    MANUAL_TEXT = """
=========================================================
Adayo 车载日志拉取工具 GUI 帮助手册 (V2.0.4)
=========================================================

1. 概述与核心功能
------------------
本工具旨在通过图形化界面 (GUI) 高效、安全地拉取车载设备上指定路径的日志文件。
核心功能包括：
1.  自动检测设备和 Root 权限尝试。
2.  **【V2.0.3修复】** 修复了设备断开后，再连接无法自动识别的问题。
3.  **【V2.0.4修复】** 修复了 Logcat 日志拉取成功后，清理按钮文件计数不更新的问题。
4.  实时监控设备连接状态，设备断开时状态灯立即变红，并禁用操作。
5.  支持自定义日志保存路径。
6.  可视化进度条和任务列表，实时反馈拉取状态。
7.  一键清理 Logcat 日志，并实时显示文件数量。

2. 前期准备
------------------
为确保程序正常运行，请确认以下条件：
1.  **ADB 环境：** 确保您的电脑已安装 ADB 工具，并将其路径添加到系统环境变量 (PATH) 中。
2.  **设备连接：** 确保只有一个车载设备通过 USB 连接到电脑，且已开启 USB 调试。
3.  **ADB 权限：** 首次连接时，请在车载设备上授权 ADB 调试权限。

3. 界面介绍
------------------
A. 顶部状态栏 (系统状态)：
   - 实时显示设备连接状态（🟢绿色：成功，🟡黄色：进行中/警告，🔴红色：断开连接）。
   - 显示当前连接的设备序列号。

B. 左侧配置区 (任务配置)：
   - **导出路径：** 默认保存在当前目录下的 'CarLogs' 文件夹。
   - **日志类型选择：** 默认全选。

C. 主任务区 (任务执行状态)：
   - **全局任务进度：** 显示总任务的完成百分比。
   - **实时任务表格：** 详细列出每种日志类型的拉取状态和文件数量。

D. 底部操作栏：
   - **[启动日志拉取]：** 开始整个拉取流程。
   - **[清理 Logcat 日志]：** 清空远程 Logcat 目录下的文件。按钮会实时显示当前远程文件数量。
   - **[打开日志目录]：** 一键打开本地日志保存文件夹。

4. 操作步骤
------------------
1.  **连接确认 (自动)：** 启动程序，等待顶部状态栏显示 🟢绿色 '连接成功'，并显示设备序列号。
2.  **启动拉取 (点击)：** 点击底部 **[启动日志拉取]** 按钮。
3.  **监控任务：** 观察进度条和任务表格。
4.  **清理操作 (可选)：** 完成后，**[清理 Logcat 日志]** 按钮上显示的 Logcat 文件数量会更新。点击按钮确认清理。
5.  **查看结果：** 点击 **[打开日志目录]** 按钮。

5. 故障排除
------------------
| 错误现象 | 常见原因 | 解决方案 (优先级) |
| :--- | :--- | :--- |
| **状态栏显示红色** | 1. 设备断开；2. ADB未安装；3. 多设备连接。 | 1. 重新插拔 USB 线；2. 检查 ADB 路径；3. 只连接一个设备。 |
| **拉取失败 (ADB Error)** | 权限不足或目录不存在。 | 确保设备已 Root。 |
| **清理按钮显示N/A** | 无法访问远程 Logcat 目录。 | 确保 ADB 连接稳定且已 Root。 |

6. 品牌与版本信息
------------------
您可以通过菜单栏 **'帮助' -> '关于'** 查看本工具的定制化信息、版本号和作者信息。
"""

    app = QApplication(sys.argv)
    app.setApplicationName(TOOL_NAME)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())