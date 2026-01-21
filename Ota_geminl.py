import sys
import json
import subprocess
import time
import re
import os
from datetime import datetime
from PyQt6.QtGui import QColor, QPainter, QCursor, QIcon, QPalette
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QGroupBox,
    QFormLayout,
    QMessageBox,
    QProgressBar,
    QStyleFactory,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QFileDialog,
    QCheckBox,
    QSizePolicy,
    QScrollArea,
    QSpacerItem,
)
from PyQt6.QtCore import (
    Qt,
    QThread,
    pyqtSignal,
    QTimer,
    QPropertyAnimation,
    QEasingCurve,
    QSize,
    QRect,
)


# ==========================================
# 核心逻辑层 (Backend Logic)
# ==========================================


class AdbWorker(QThread):
    """后台工作线程:处理所有耗时的 ADB 操作,避免界面卡死"""

    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(bool)
    data_loaded_signal = pyqtSignal(dict)
    operation_finished_signal = pyqtSignal(bool, str)

    def __init__(self, task_type, data=None):
        super().__init__()
        self.task_type = task_type
        self.data_to_push = data
        self.remote_path = "/mnt/sdcard/DeviceInfo.txt"
        self.local_filename = "DeviceInfo.txt"
        self.root_pwd = os.getenv("ADB_ROOT_PWD", "adayo@N51")

    def run(self):
        self.progress_signal.emit(True)
        try:
            check_code, _, _ = self.run_cmd("adb get-state")
            if check_code != 0 and self.task_type != "connect":
                raise Exception("设备已断开连接,请检查数据线!")

            if self.task_type == "connect":
                self.do_connect_and_root()
            elif self.task_type == "pull":
                self.do_pull()
            elif self.task_type == "push":
                self.do_push()
        except Exception as e:
            self.log_signal.emit(f"[系统错误] {str(e)}")
            self.operation_finished_signal.emit(False, str(e))
        finally:
            self.progress_signal.emit(False)

    def run_cmd(self, args, shell=True):
        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=shell,
            encoding="utf-8",
            errors="ignore",
            startupinfo=startupinfo,
        )
        stdout, stderr = process.communicate()
        return process.returncode, stdout.strip(), stderr.strip()

    def do_connect_and_root(self):
        code, out, _ = self.run_cmd("adb devices")
        if len(out.split("\n")) <= 1:
            self.log_signal.emit("[错误] 未发现任何 ADB 设备,请检查数据线!")
            self.operation_finished_signal.emit(False, "未连接设备")
            return

        self.log_signal.emit(">>> 开始连接设备并获取 Root 权限...")
        self.log_signal.emit("[1/4] 等待设备连接...")
        self.run_cmd("adb wait-for-device")

        self.log_signal.emit("[2/4] 发送授权密码...")
        self.run_cmd(f'adb shell "setprop service.adb.root.password {self.root_pwd}"')

        self.log_signal.emit("[3/4] 执行 Root 重启...")
        self.run_cmd("adb root")

        self.log_signal.emit("等待 adbd 重启 (3秒)...")
        time.sleep(3)

        code, out, _ = self.run_cmd("adb shell id")
        if "uid=0" not in out:
            raise Exception("获取 Root 权限失败,请检查连接或密码。")

        self.log_signal.emit("[4/4] 挂载分区 (Remount)...")
        self.run_cmd("adb remount")

        self.log_signal.emit(">>> ✅ 设备连接成功且已获取 Root 权限")
        self.operation_finished_signal.emit(True, "Connected")

    def do_pull(self):
        self.log_signal.emit(f">>> 从设备拉取文件: {self.remote_path}")

        code, _, _ = self.run_cmd(f'adb shell "ls {self.remote_path}"')
        if code != 0:
            raise Exception("设备中未找到目标文件 DeviceInfo.txt")

        code, out, err = self.run_cmd(
            f'adb pull "{self.remote_path}" "{self.local_filename}"'
        )
        if code != 0:
            raise Exception(f"拉取失败: {err}")

        self.log_signal.emit("文件拉取成功,正在解析...")

        try:
            with open(self.local_filename, "r", encoding="utf-8") as f:
                content = f.read()
                data = json.loads(content)
                self.data_loaded_signal.emit(data)
                self.log_signal.emit(f"解析成功: {content}")
                self.operation_finished_signal.emit(True, "Pull Success")
        except json.JSONDecodeError:
            raise Exception("文件内容不是有效的 JSON 格式")
        except Exception as e:
            raise Exception(f"读取文件失败: {str(e)}")

    def do_push(self):
        self.log_signal.emit(">>> 准备推送到设备...")

        if not self.data_to_push:
            raise Exception("没有数据可推送")

        temp_file = f"{self.local_filename}.tmp"
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(
                    self.data_to_push, f, ensure_ascii=False, separators=(",", ":")
                )
            self.log_signal.emit("本地临时文件生成成功")
        except Exception as e:
            raise Exception(f"本地写入失败: {str(e)}")

        backup_path = f"{self.remote_path}.backup"
        self.run_cmd(
            f'adb shell "cp {self.remote_path} {backup_path} 2>/dev/null || true"'
        )

        code, out, err = self.run_cmd(f'adb push "{temp_file}" "{self.remote_path}"')
        if code != 0:
            raise Exception(f"Push 失败: {err}")

        self.log_signal.emit("正在验证设备端文件完整性...")
        _, remote_out, _ = self.run_cmd(f'adb shell "cat {self.remote_path}"')

        if len(remote_out) > 10 and "VIN" in remote_out:
            self.run_cmd(f'adb shell "rm {backup_path} 2>/dev/null || true"')
            if os.path.exists(temp_file):
                os.remove(temp_file)
            self.log_signal.emit(">>> ✅ 推送并验证成功!")
            self.operation_finished_signal.emit(True, "Push Success")
        else:
            self.run_cmd(f'adb shell "mv {backup_path} {self.remote_path}"')
            self.log_signal.emit("❌ 验证失败,已自动恢复原文件")
            self.operation_finished_signal.emit(False, "设备端校验不通过,已回滚")


class LoginDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(450, 300)
        self.setup_ui()

    def setup_ui(self):
        self.main_container = QFrame(self)
        self.main_container.setGeometry(10, 10, 430, 280)
        self.main_container.setStyleSheet(
            """
            QFrame {
                background-color: white;
                border-radius: 15px;
            }
        """
        )

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setXOffset(0)
        shadow.setYOffset(5)
        shadow.setColor(QColor(0, 0, 0, 80))
        self.main_container.setGraphicsEffect(shadow)

        layout = QHBoxLayout(self.main_container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        left_panel = QFrame()
        left_panel.setStyleSheet(
            """
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0078d7, stop:1 #005a9e);
            border-top-left-radius: 15px;
            border-bottom-left-radius: 15px;
        """
        )
        left_panel.setFixedWidth(160)

        v_left = QVBoxLayout(left_panel)
        logo_label = QLabel("OTA\nPRO")
        logo_label.setStyleSheet("color: white; font-size: 28px; font-weight: bold;")
        logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v_left.addWidget(logo_label)
        layout.addWidget(left_panel)

        right_panel = QWidget()
        v_right = QVBoxLayout(right_panel)
        v_right.setContentsMargins(30, 20, 30, 20)

        btn_close = QPushButton("×", right_panel)
        btn_close.setGeometry(230, 5, 30, 30)
        btn_close.setStyleSheet(
            "background:none; color:#888; font-size:20px; border:none;"
        )
        btn_close.clicked.connect(self.reject)

        title = QLabel("系统安全验证")
        title.setStyleSheet(
            "font-size: 18px; color: #333; font-weight: bold; margin-bottom: 10px;"
        )
        v_right.addWidget(title)

        self.btn_help = QPushButton("ⓘ 操作手册 (Manual)")
        self.btn_help.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_help.setStyleSheet(
            """
            QPushButton {
                background: none; border: none; color: #0078d7;
                text-decoration: underline; font-size: 12px; text-align: left;
            }
            QPushButton:hover { color: #005a9e; }
        """
        )
        self.btn_help.clicked.connect(self.show_manual)
        v_right.addWidget(self.btn_help)

        self.user_input = QLineEdit()
        self.user_input.setPlaceholderText("请输入管理账号")
        self.user_input.setMinimumHeight(35)

        self.pass_input = QLineEdit()
        self.pass_input.setPlaceholderText("请输入登录密码")
        self.pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.pass_input.setMinimumHeight(35)

        # 2. 再绑定逻辑信号 (此时 self.pass_input 已经存在了)
        self.user_input.returnPressed.connect(self.pass_input.setFocus)
        self.pass_input.returnPressed.connect(self.check_login)

        v_right.addWidget(self.user_input)
        v_right.addWidget(self.pass_input)
        v_right.addSpacing(15)

        self.btn_login = QPushButton("立即登录")
        self.btn_login.setMinimumHeight(40)
        self.btn_login.setStyleSheet(
            """
            QPushButton {
                background-color: #0078d7;
                color: white;
                border-radius: 5px;
                font-size: 14px;
            }
            QPushButton:hover { background-color: #005a9e; }
            QPushButton:pressed { background-color: #004578; }
        """
        )
        self.btn_login.clicked.connect(self.check_login)
        self.btn_login.setDefault(True)
        v_right.addWidget(self.btn_login)
        layout.addWidget(right_panel)

    def keyPressEvent(self, event):
        # 如果按下的是回车键（Enter 或 Return）
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.check_login()  # 强制执行登录校验
        else:
            super().keyPressEvent(event)  # 其他按键维持原状

    def show_manual(self):
        manual_text = """
        <h3 style='color:#0078d7;'>OTA PRO 调试工具指南</h3>
        <hr>
        <p><b>1. 登录账户信息:</b></p>
        <table border='0' cellpadding='4'>
            <tr><td><b>管理员:</b></td><td><code>admin</code></td><td>/ 123456</td></tr>
            <tr><td><b>工程师:</b></td><td><code>engineer</code></td><td>/ adayo2026</td></tr>
        </table>
        <p><b>2. 快速使用步骤:</b></p>
        <ol>
            <li>连接车机 USB 并确保 ADB 开启。</li>
            <li>先点击<b>[连接设备]</b>进行 Root 授权。</li>
            <li>授权后点击<b>[从设备读取]</b>同步当前参数。</li>
            <li>修改参数后点击<b>[保存并推送]</b>完成更新。</li>
        </ol>
        <p style='color:#e67e22;'><i>提示:系统会自动将输入转为大写,禁止输入中文。</i></p>
        """
        QMessageBox.about(self, "系统操作手册", manual_text)

    def check_login(self):
        username = self.user_input.text().strip()
        password = self.pass_input.text().strip()

        try:
            with open("users.json", "r", encoding="utf-8") as f:
                users = json.load(f)

            if users.get(username) == password:
                self.accept()
            else:
                self.user_input.setStyleSheet("border: 1px solid red;")
                self.pass_input.setStyleSheet("border: 1px solid red;")
                QMessageBox.warning(
                    self,
                    "验证失败",
                    "账号或密码错误!\n若忘记账号密码,请查看界面上方的【操作手册】。",
                )
        except Exception as e:
            QMessageBox.critical(self, "系统错误", f"配置文件读取失败: {str(e)}")


# ==========================================
# 现代化卡片组件
# ==========================================


class ModernCard(QFrame):
    """现代化卡片容器"""

    def __init__(self, title="", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setup_ui(title)

    def setup_ui(self, title):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 15, 20, 15)
        layout.setSpacing(12)

        if title:
            title_label = QLabel(title)
            title_label.setStyleSheet(
                "font-size: 14px; font-weight: bold; color: #333;"
            )
            layout.addWidget(title_label)


class StatusIndicator(QWidget):
    """现代化状态指示器"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.status = "disconnected"
        self.setFixedHeight(40)
        self.setup_ui()

    def setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.dot = QLabel("●")
        self.dot.setStyleSheet("font-size: 20px; color: #dc3545;")

        self.text = QLabel("未连接")
        self.text.setStyleSheet("font-size: 14px; font-weight: 500; color: #666;")

        layout.addWidget(self.dot)
        layout.addWidget(self.text)
        layout.addStretch()

    def set_status(self, connected):
        if connected:
            self.status = "connected"
            self.dot.setStyleSheet("font-size: 20px; color: #28a745;")
            self.text.setText("已连接 (Root)")
            self.text.setStyleSheet(
                "font-size: 14px; font-weight: 500; color: #28a745;"
            )
        else:
            self.status = "disconnected"
            self.dot.setStyleSheet("font-size: 20px; color: #dc3545;")
            self.text.setText("未连接")
            self.text.setStyleSheet("font-size: 14px; font-weight: 500; color: #666;")


class ModernButton(QPushButton):
    """现代化按钮"""

    def __init__(self, text, style_type="primary", parent=None):
        super().__init__(text, parent)
        self.style_type = style_type
        self.apply_style()

    def apply_style(self):
        base_style = """
            QPushButton {
                border: none;
                border-radius: 6px;
                padding: 10px 20px;
                font-size: 13px;
                font-weight: 500;
                min-height: 36px;
            }
            QPushButton:disabled {
                background-color: #e9ecef;
                color: #adb5bd;
            }
        """

        if self.style_type == "primary":
            style = (
                base_style
                + """
                QPushButton {
                    background-color: #0078d7;
                    color: white;
                }
                QPushButton:hover {
                    background-color: #005a9e;
                }
                QPushButton:pressed {
                    background-color: #004578;
                }
            """
            )
        elif self.style_type == "success":
            style = (
                base_style
                + """
                QPushButton {
                    background-color: #28a745;
                    color: white;
                }
                QPushButton:hover {
                    background-color: #218838;
                }
                QPushButton:pressed {
                    background-color: #1e7e34;
                }
            """
            )
        elif self.style_type == "danger":
            style = (
                base_style
                + """
                QPushButton {
                    background-color: #dc3545;
                    color: white;
                }
                QPushButton:hover {
                    background-color: #c82333;
                }
                QPushButton:pressed {
                    background-color: #bd2130;
                }
            """
            )
        else:  # secondary
            style = (
                base_style
                + """
                QPushButton {
                    background-color: #6c757d;
                    color: white;
                }
                QPushButton:hover {
                    background-color: #5a6268;
                }
                QPushButton:pressed {
                    background-color: #545b62;
                }
            """
            )

        self.setStyleSheet(style)


class ModernInput(QLineEdit):
    """现代化输入框"""

    def __init__(self, placeholder="", parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setStyleSheet(
            """
            QLineEdit {
                border: 2px solid #e9ecef;
                border-radius: 6px;
                padding: 10px 12px;
                font-size: 13px;
                background-color: white;
                min-height: 38px;
            }
            QLineEdit:focus {
                border: 2px solid #0078d7;
                background-color: #f8f9fa;
            }
            QLineEdit:disabled {
                background-color: #e9ecef;
                color: #6c757d;
            }
        """
        )


# ==========================================
# 主界面 (Modern UI)
# ==========================================


class OTAConfigApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OTA Device Configuration Manager Pro")
        self.resize(1000, 700)

        self.worker = None
        self.inputs = {}
        self.is_dark_mode = False

        self.input_debounce_timer = QTimer()
        self.input_debounce_timer.setSingleShot(True)
        self.input_debounce_timer.timeout.connect(self.apply_text_formatting)
        self.pending_line_edit = None

        self.heart_timer = QTimer(self)
        self.heart_timer.timeout.connect(self.auto_check_adb)
        self.heart_timer.start(3000)

        self.log_expanded = False

        self.setup_ui()
        self.apply_theme()

        self.log("程序已启动,请点击 [连接设备] ...")

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)

        # 顶部工具栏
        toolbar = self.create_toolbar()
        main_layout.addWidget(toolbar)

        # 内容区域 - 左右分栏
        content_layout = QHBoxLayout()
        content_layout.setSpacing(15)

        # 左侧 - 设备信息卡片
        left_card = self.create_device_info_card()
        content_layout.addWidget(left_card, 1)

        # 右侧 - 配置编辑卡片
        right_card = self.create_config_card()
        content_layout.addWidget(right_card, 2)

        main_layout.addLayout(content_layout, 1)

        # 底部日志面板
        log_panel = self.create_log_panel()
        main_layout.addWidget(log_panel)

        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(3)
        self.progress_bar.setStyleSheet(
            """
            QProgressBar {
                border: none;
                background-color: transparent;
            }
            QProgressBar::chunk {
                background-color: #0078d7;
            }
        """
        )
        self.progress_bar.hide()
        main_layout.addWidget(self.progress_bar)

    def create_toolbar(self):
        """创建顶部工具栏"""
        toolbar = QFrame()
        toolbar.setStyleSheet(
            """
            QFrame {
                background-color: white;
                border-radius: 8px;
                padding: 10px;
            }
        """
        )

        layout = QHBoxLayout(toolbar)
        layout.setContentsMargins(15, 10, 15, 10)

        # Logo 和标题
        logo_label = QLabel("OTA PRO")
        logo_label.setStyleSheet(
            """
            font-size: 18px;
            font-weight: bold;
            color: #0078d7;
            padding: 5px 10px;
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #e3f2fd, stop:1 transparent);
            border-radius: 5px;
        """
        )
        layout.addWidget(logo_label)

        # 状态指示器
        self.status_indicator = StatusIndicator()
        layout.addWidget(self.status_indicator)

        layout.addStretch()

        # 主题切换
        self.theme_toggle = QCheckBox("深色模式")
        self.theme_toggle.setStyleSheet(
            """
            QCheckBox {
                font-size: 12px;
                color: #666;
                spacing: 5px;
            }
            QCheckBox::indicator {
                width: 40px;
                height: 20px;
                border-radius: 10px;
            }
            QCheckBox::indicator:unchecked {
                background-color: #ccc;
            }
            QCheckBox::indicator:checked {
                background-color: #0078d7;
            }
        """
        )
        self.theme_toggle.toggled.connect(self.toggle_theme)
        layout.addWidget(self.theme_toggle)

        # 连接按钮
        self.btn_connect = ModernButton("连接设备", "primary")
        self.btn_connect.clicked.connect(self.start_connect)
        layout.addWidget(self.btn_connect)

        return toolbar

    def create_device_info_card(self):
        """创建设备信息卡片"""
        card = ModernCard("设备状态")

        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(15)
        shadow.setXOffset(0)
        shadow.setYOffset(2)
        shadow.setColor(QColor(0, 0, 0, 30))
        card.setGraphicsEffect(shadow)

        layout = card.layout()

        # 设备信息显示区域
        info_text = QLabel(
            "等待连接设备...\n\n请使用 USB 数据线连接车机，\n并确保已开启 ADB 调试模式。"
        )
        info_text.setStyleSheet(
            """
            font-size: 13px;
            color: #666;
            line-height: 1.6;
            padding: 20px;
            background-color: #f8f9fa;
            border-radius: 6px;
        """
        )
        info_text.setWordWrap(True)
        info_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(info_text)

        # 快捷操作
        layout.addSpacing(10)

        self.btn_pull = ModernButton("从设备读取", "success")
        self.btn_pull.clicked.connect(self.start_pull)
        self.btn_pull.setEnabled(False)
        layout.addWidget(self.btn_pull)

        self.btn_import = ModernButton("导入本地配置", "secondary")
        self.btn_import.clicked.connect(self.import_local_config)
        layout.addWidget(self.btn_import)

        layout.addStretch()

        return card

    def create_config_card(self):
        """创建配置编辑卡片"""
        self.config_card = ModernCard("设备配置")
        self.config_card.setEnabled(False)

        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(15)
        shadow.setXOffset(0)
        shadow.setYOffset(2)
        shadow.setColor(QColor(0, 0, 0, 30))
        self.config_card.setGraphicsEffect(shadow)

        layout = self.config_card.layout()

        # 表单区域
        form_widget = QWidget()
        form_layout = QFormLayout(form_widget)
        form_layout.setSpacing(15)
        form_layout.setContentsMargins(0, 10, 0, 10)
        form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        fields = [
            ("ICC_PNO", "请输入 ICC PNO 编号"),
            ("VIN", "请输入 17 位车架号"),
            ("f1A1", "请输入 f1A1 完整数据"),
            ("0525", "请输入 0525 参数"),
        ]

        for key, placeholder in fields:
            input_field = ModernInput(placeholder)
            input_field.textChanged.connect(
                lambda text, obj=input_field: self.handle_text_change(obj)
            )

            label = QLabel(f"{key}:")
            label.setStyleSheet("font-size: 13px; font-weight: 500; color: #495057;")

            form_layout.addRow(label, input_field)
            self.inputs[key] = input_field

        layout.addWidget(form_widget)

        # 操作按钮
        layout.addSpacing(10)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.btn_push = ModernButton("保存并推送", "danger")
        self.btn_push.clicked.connect(self.start_push)
        self.btn_push.setEnabled(False)
        btn_layout.addWidget(self.btn_push)

        layout.addLayout(btn_layout)

        return self.config_card

    def create_log_panel(self):
        """创建可折叠日志面板"""
        panel = QFrame()
        panel.setStyleSheet(
            """
            QFrame {
                background-color: white;
                border-radius: 8px;
            }
        """
        )

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 日志头部
        header = QFrame()
        header.setStyleSheet(
            """
            QFrame {
                background-color: #f8f9fa;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                padding: 10px 15px;
            }
        """
        )
        header.setFixedHeight(45)

        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(10, 5, 10, 5)

        log_title = QLabel("运行日志")
        log_title.setStyleSheet("font-size: 13px; font-weight: 600; color: #495057;")
        header_layout.addWidget(log_title)

        self.log_status = QLabel("准备就绪")
        self.log_status.setStyleSheet("font-size: 12px; color: #6c757d;")
        header_layout.addWidget(self.log_status)

        header_layout.addStretch()

        self.btn_toggle_log = QPushButton("展开 ▼")
        self.btn_toggle_log.setStyleSheet(
            """
            QPushButton {
                background: none;
                border: none;
                color: #0078d7;
                font-size: 12px;
                padding: 5px 10px;
            }
            QPushButton:hover {
                color: #005a9e;
            }
        """
        )
        self.btn_toggle_log.clicked.connect(self.toggle_log_panel)
        header_layout.addWidget(self.btn_toggle_log)

        layout.addWidget(header)

        # 日志内容区域
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumHeight(0)
        self.console.setStyleSheet(
            """
            QTextEdit {
                font-family: 'Consolas', 'Monaco', monospace;
                font-size: 12px;
                background-color: #1e1e1e;
                color: #00ff00;
                border: none;
                padding: 10px;
            }
        """
        )
        layout.addWidget(self.console)

        return panel

    def toggle_log_panel(self):
        """切换日志面板展开/收起"""
        if self.log_expanded:
            self.console.setMaximumHeight(0)
            self.btn_toggle_log.setText("展开 ▼")
            self.log_expanded = False
        else:
            self.console.setMaximumHeight(200)
            self.btn_toggle_log.setText("收起 ▲")
            self.log_expanded = True

    def toggle_theme(self, checked):
        """切换深色/浅色主题"""
        self.is_dark_mode = checked
        self.apply_theme()

    def apply_theme(self):
        """应用主题样式"""
        if self.is_dark_mode:
            # 深色主题
            self.setStyleSheet(
                """
                QMainWindow {
                    background-color: #1a1a1a;
                }
                QFrame#card {
                    background-color: #2d2d2d;
                    border-radius: 8px;
                }
                QLabel {
                    color: #e0e0e0;
                }
            """
            )
        else:
            # 浅色主题
            self.setStyleSheet(
                """
                QMainWindow {
                    background-color: #f0f2f5;
                }
                QFrame#card {
                    background-color: white;
                    border-radius: 8px;
                }
            """
            )

    def handle_text_change(self, line_edit):
        """防抖处理:延迟300ms后再格式化"""
        self.pending_line_edit = line_edit
        self.input_debounce_timer.start(300)

    def apply_text_formatting(self):
        """实际执行文本格式化"""
        if not self.pending_line_edit:
            return

        line_edit = self.pending_line_edit
        current_text = line_edit.text()
        processed_text = current_text.upper().replace(" ", "")

        if current_text != processed_text:
            cursor_pos = line_edit.cursorPosition()
            line_edit.blockSignals(True)
            line_edit.setText(processed_text)
            line_edit.blockSignals(False)
            line_edit.setCursorPosition(min(cursor_pos, len(processed_text)))

    def auto_check_adb(self):
        """后台静默检查 ADB 状态"""
        if self.worker and self.worker.isRunning():
            return

        if self.progress_bar.isHidden():
            try:
                process = subprocess.run(
                    "adb get-state",
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=1.0,
                )

                is_connected = "device" in process.stdout
                current_connected = self.status_indicator.status == "connected"

                if not is_connected and current_connected:
                    self.status_indicator.set_status(False)
                    self.toggle_loading(False)
                    self.log("[系统] 检测到设备断开连接")

            except (subprocess.TimeoutExpired, Exception):
                pass

    def import_local_config(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择本地配置文件", "", "JSON Files (*.json);;Text Files (*.txt)"
        )
        if file_path:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.populate_ui(data)
                    self.log(f"✅ 已从本地成功载入: {file_path}")
            except Exception as e:
                QMessageBox.critical(self, "导入失败", f"无效的 JSON 文件: {str(e)}")

    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.console.append(f"[{timestamp}] {message}")
        self.log_status.setText(message[:50] + "..." if len(message) > 50 else message)
        sb = self.console.verticalScrollBar()
        sb.setValue(sb.maximum())

    def toggle_loading(self, is_loading):
        if is_loading:
            self.progress_bar.show()
            self.btn_connect.setEnabled(False)
            self.btn_pull.setEnabled(False)
            self.btn_push.setEnabled(False)
            self.config_card.setEnabled(False)
        else:
            self.progress_bar.hide()
            self.btn_connect.setEnabled(True)
            is_connected = self.status_indicator.status == "connected"
            self.btn_pull.setEnabled(is_connected)
            self.btn_push.setEnabled(is_connected)
            self.config_card.setEnabled(is_connected)

    def start_connect(self):
        if self.worker and self.worker.isRunning():
            self.log("[警告] 操作正在进行中,请稍候...")
            return

        self.worker = AdbWorker("connect")
        self.connect_worker_signals()
        self.worker.start()

    def start_pull(self):
        if self.worker and self.worker.isRunning():
            self.log("[警告] 操作正在进行中,请稍候...")
            return

        self.worker = AdbWorker("pull")
        self.connect_worker_signals()
        self.worker.start()

    def start_push(self):
        if self.worker and self.worker.isRunning():
            self.log("[警告] 操作正在进行中,请稍候...")
            return

        data = {key: le.text().strip().upper() for key, le in self.inputs.items()}

        is_valid, error_msg = self.validate_data(data)
        if not is_valid:
            QMessageBox.critical(self, "数据违规", error_msg)
            return

        reply = QMessageBox.question(
            self,
            "确认推送",
            f"即将覆盖设备上的配置:\nVIN: {data.get('VIN')}\n\n确定要继续吗?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.worker = AdbWorker("push", data=data)
            self.connect_worker_signals()
            self.worker.start()

    def connect_worker_signals(self):
        try:
            self.worker.log_signal.disconnect()
            self.worker.progress_signal.disconnect()
            self.worker.data_loaded_signal.disconnect()
            self.worker.operation_finished_signal.disconnect()
        except TypeError:
            pass

        self.worker.log_signal.connect(self.log)
        self.worker.progress_signal.connect(self.toggle_loading)
        self.worker.data_loaded_signal.connect(self.populate_ui)
        self.worker.operation_finished_signal.connect(self.on_operation_finished)

    def validate_data(self, data):
        """符号级校验逻辑"""
        patterns = {
            "ICC_PNO": r"^[A-Z0-9_-]+$",
            "VIN": r"^[A-Z0-9]{17}$",
            "f1A1": r"^[A-F0-9]+$",
            "0525": r"^[A-F0-9]+$",
        }

        for key, pattern in patterns.items():
            val = data.get(key, "")
            if not re.match(pattern, val):
                return (
                    False,
                    f"字段 [{key}] 格式非法!\n当前输入: {val}\n\n规则:仅限大写字母、数字、-、_",
                )

        return True, ""

    def populate_ui(self, data):
        """将 JSON 数据填入输入框"""
        for key, val in data.items():
            if key in self.inputs:
                self.inputs[key].setText(str(val))
            else:
                self.log(f"[提示] 发现未定义字段: {key} = {val}")

    def on_operation_finished(self, success, message):
        if success:
            if message == "Connected":
                self.status_indicator.set_status(True)
                self.start_pull()
            elif message == "Push Success":
                QMessageBox.information(
                    self, "成功", "配置文件已成功推送到设备并生效。"
                )
        else:
            QMessageBox.critical(self, "操作失败", f"原因: {message}")
            self.status_indicator.set_status(False)
            self.btn_pull.setEnabled(False)
            self.btn_push.setEnabled(False)
            self.config_card.setEnabled(False)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle(QStyleFactory.create("Fusion"))

    login = LoginDialog()
    if login.exec() == QDialog.DialogCode.Accepted:
        window = OTAConfigApp()
        window.show()
        sys.exit(app.exec())
    else:
        sys.exit(0)
