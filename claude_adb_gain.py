#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CAR HOUSE KEEP - Professional ADB CLI
A robust, extensible Android Debug Bridge management tool
Author: Senior Python Engineer
Version: 2.0.0
"""

import os
import sys
import time
import subprocess
from abc import ABC, abstractmethod
from typing import Tuple, Optional, List
from pathlib import Path
from dataclasses import dataclass
from enum import Enum


# ============================================================================
# CONFIGURATION & CONSTANTS
# ============================================================================

class Config:
    """Global configuration management"""
    ADB_ROOT_PASSWORD = "adayo@N51"
    APP_NAME = "CAR HOUSE KEEP - PROFESSIONAL ADB CLI"
    VERSION = "2.0.0"
    RETRY_DELAY = 2
    MAX_RETRIES = 3


class Color:
    """ANSI color codes for terminal output"""
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

    @classmethod
    def colorize(cls, text: str, color: str) -> str:
        """Wrap text with color codes"""
        return f"{color}{text}{cls.ENDC}"


class Status(Enum):
    """Operation status codes"""
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    WARNING = "WARNING"
    INFO = "INFO"


# ============================================================================
# CORE COMPONENTS
# ============================================================================

@dataclass
class CommandResult:
    """Encapsulate command execution results"""
    success: bool
    output: str
    error: str = ""

    def __bool__(self) -> bool:
        return self.success


class ADBExecutor:
    """Low-level ADB command executor with error handling"""

    def __init__(self, device_id: Optional[str] = None):
        self.device_id = device_id

    def execute(self, cmd: str, timeout: int = 30) -> CommandResult:
        """
        Execute ADB command with robust error handling

        Args:
            cmd: Command to execute (without 'adb' prefix)
            timeout: Command timeout in seconds

        Returns:
            CommandResult object
        """
        prefix = f"adb -s {self.device_id} " if self.device_id else "adb "
        full_cmd = prefix + cmd

        try:
            process = subprocess.run(
                full_cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding='utf-8',
                errors='replace'
            )

            return CommandResult(
                success=(process.returncode == 0),
                output=process.stdout.strip(),
                error=process.stderr.strip()
            )

        except subprocess.TimeoutExpired:
            return CommandResult(
                success=False,
                output="",
                error=f"Command timeout after {timeout}s"
            )
        except Exception as e:
            return CommandResult(
                success=False,
                output="",
                error=f"Execution error: {str(e)}"
            )


class DeviceManager:
    """Manage ADB device connections and status"""

    def __init__(self, executor: ADBExecutor):
        self.executor = executor
        self.current_device: Optional[str] = None

    def get_connected_devices(self) -> List[str]:
        """Get list of connected devices"""
        result = self.executor.execute("devices")
        if not result:
            return []

        lines = result.output.splitlines()[1:]  # Skip header
        devices = []

        for line in lines:
            if line.strip() and '\tdevice' in line:
                device_id = line.split('\t')[0].strip()
                devices.append(device_id)

        return devices

    def select_device(self) -> bool:
        """Select and verify device connection"""
        devices = self.get_connected_devices()

        if not devices:
            return False

        self.current_device = devices[0]
        self.executor.device_id = self.current_device
        return True

    def get_device_info(self) -> dict:
        """Retrieve device information"""
        if not self.current_device:
            return {}

        info = {
            'id': self.current_device,
            'model': self._get_prop('ro.product.model'),
            'android': self._get_prop('ro.build.version.release'),
            'sdk': self._get_prop('ro.build.version.sdk')
        }
        return info

    def _get_prop(self, prop_name: str) -> str:
        """Get device property value"""
        result = self.executor.execute(f"shell getprop {prop_name}")
        return result.output if result else "Unknown"


# ============================================================================
# FEATURE MODULES (STRATEGY PATTERN)
# ============================================================================

class Feature(ABC):
    """Abstract base class for all features"""

    def __init__(self, executor: ADBExecutor):
        self.executor = executor

    @abstractmethod
    def execute(self) -> bool:
        """Execute the feature"""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Feature display name"""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Feature description"""
        pass


class RootPrivilegeFeature(Feature):
    """One-click root privilege escalation"""

    @property
    def name(self) -> str:
        return "一键提权"

    @property
    def description(self) -> str:
        return "Root / Remount / SELinux Off"

    def execute(self) -> bool:
        """Execute privilege escalation sequence"""
        ui = UIManager()
        ui.print_section("正在执行提权序列")

        steps = [
            ("设置 Root 密码", lambda: self.executor.execute(
                f"shell setprop service.adb.root.password {Config.ADB_ROOT_PASSWORD}"
            )),
            ("请求 Root 权限", lambda: self._request_root()),
            ("执行 Remount", lambda: self.executor.execute("remount")),
            ("关闭 SELinux", lambda: self.executor.execute("shell setenforce 0")),
            ("禁用 Verity", lambda: self.executor.execute("disable-verity"))
        ]

        for i, (step_name, step_func) in enumerate(steps, 1):
            ui.print_step(i, len(steps), step_name)
            result = step_func()

            if not result and i <= 2:  # Critical steps
                ui.print_status(Status.FAILED, f"{step_name} 失败: {result.error}")
                return False

            time.sleep(0.5)

        ui.print_status(Status.SUCCESS, "全权限已开启")
        return True

    def _request_root(self) -> CommandResult:
        """Request root access with retry"""
        result = self.executor.execute("root")
        time.sleep(Config.RETRY_DELAY)
        return result


class APKInstallerFeature(Feature):
    """APK installation with drag-and-drop support"""

    @property
    def name(self) -> str:
        return "安装 APK"

    @property
    def description(self) -> str:
        return "支持拖拽路径"

    def execute(self) -> bool:
        """Execute APK installation"""
        ui = UIManager()
        ui.print_section("APK 安装向导")

        # Get APK path
        print(Color.colorize("请拖入 APK 文件或输入路径:", Color.OKCYAN))
        raw_path = input(Color.colorize(">>> ", Color.BOLD)).strip()

        # Sanitize path
        apk_path = self._sanitize_path(raw_path)

        # Validate
        validation_error = self._validate_apk_path(apk_path)
        if validation_error:
            ui.print_status(Status.FAILED, validation_error)
            return False

        # Install
        ui.print_step(1, 1, f"安装 {Path(apk_path).name}")
        result = self.executor.execute(f'install -r -d -t "{apk_path}"')

        if result:
            ui.print_status(Status.SUCCESS, "APK 安装成功")
            return True
        else:
            ui.print_status(Status.FAILED, f"安装失败: {result.error}")
            return False

    def _sanitize_path(self, path: str) -> str:
        """Clean up dragged file path"""
        return path.replace('"', '').replace("'", '').strip()

    def _validate_apk_path(self, path: str) -> Optional[str]:
        """Validate APK file path"""
        if not path.lower().endswith('.apk'):
            return "文件类型错误: 必须是 .apk 文件"

        if not os.path.exists(path):
            return f"文件不存在: {path}"

        if not os.path.isfile(path):
            return "路径必须指向文件"

        return None


class RebootFeature(Feature):
    """Device reboot functionality"""

    @property
    def name(self) -> str:
        return "重启设备"

    @property
    def description(self) -> str:
        return "安全重启 Android 设备"

    def execute(self) -> bool:
        """Execute device reboot"""
        ui = UIManager()
        ui.print_section("设备重启")

        confirm = input(Color.colorize("确认重启设备? (y/N): ", Color.WARNING))
        if confirm.lower() != 'y':
            ui.print_status(Status.INFO, "已取消重启")
            return False

        result = self.executor.execute("reboot")
        if result:
            ui.print_status(Status.SUCCESS, "设备正在重启...")
            time.sleep(3)
            return True
        else:
            ui.print_status(Status.FAILED, f"重启失败: {result.error}")
            return False


# ============================================================================
# USER INTERFACE
# ============================================================================

class UIManager:
    """Centralized UI rendering and formatting"""

    @staticmethod
    def clear_screen():
        """Clear terminal screen"""
        os.system('cls' if os.name == 'nt' else 'clear')

    @staticmethod
    def print_header():
        """Print application header"""
        header = f"""
╔══════════════════════════════════════════════════════════════╗
║  {Config.APP_NAME:<56} ║
║  Version: {Config.VERSION:<48} ║
║  Status: High Privilege Mode | Testing Only                 ║
╚══════════════════════════════════════════════════════════════╝
"""
        print(Color.colorize(header, Color.OKCYAN + Color.BOLD))

    @staticmethod
    def print_device_status(device_info: dict):
        """Print device connection status"""
        if not device_info:
            print(Color.colorize("⚠ 未检测到设备连接", Color.FAIL))
            return

        status = f"""
┌─ 设备信息 {'─' * 48}
│ 设备 ID:  {device_info.get('id', 'N/A')}
│ 型   号:  {device_info.get('model', 'N/A')}
│ 系   统:  Android {device_info.get('android', 'N/A')} (SDK {device_info.get('sdk', 'N/A')})
└{'─' * 60}
"""
        print(Color.colorize(status, Color.OKGREEN))

    @staticmethod
    def print_menu(features: List[Feature]):
        """Print feature menu"""
        print(Color.colorize("\n╔═ 可用功能 ═" + "═" * 47 + "╗", Color.BOLD))

        for i, feature in enumerate(features, 1):
            print(f"  {Color.colorize(f'[{i}]', Color.WARNING)} "
                  f"{feature.name} - {Color.colorize(feature.description, Color.OKCYAN)}")

        print(f"  {Color.colorize('[q]', Color.WARNING)} 退出程序")
        print(Color.colorize("╚" + "═" * 60 + "╝\n", Color.BOLD))

    @staticmethod
    def print_status(status: Status, message: str):
        """Print formatted status message"""
        symbols = {
            Status.SUCCESS: "✓",
            Status.FAILED: "✗",
            Status.WARNING: "⚠",
            Status.INFO: "ℹ"
        }
        colors = {
            Status.SUCCESS: Color.OKGREEN,
            Status.FAILED: Color.FAIL,
            Status.WARNING: Color.WARNING,
            Status.INFO: Color.OKBLUE
        }

        symbol = symbols.get(status, "•")
        color = colors.get(status, Color.ENDC)
        print(Color.colorize(f"{symbol} {message}", color))

    @staticmethod
    def print_section(title: str):
        """Print section header"""
        print("\n" + Color.colorize(f"{'─' * 60}", Color.OKCYAN))
        print(Color.colorize(f"  {title}", Color.BOLD))
        print(Color.colorize(f"{'─' * 60}", Color.OKCYAN))

    @staticmethod
    def print_step(current: int, total: int, description: str):
        """Print progress step"""
        print(f"  [{current}/{total}] {description} ...", end=" ", flush=True)
        print(Color.colorize("✓", Color.OKGREEN))


# ============================================================================
# APPLICATION CONTROLLER
# ============================================================================

class ADBProCLI:
    """Main application controller"""

    def __init__(self):
        self.executor = ADBExecutor()
        self.device_manager = DeviceManager(self.executor)
        self.ui = UIManager()
        self.features: List[Feature] = []
        self._register_features()

    def _register_features(self):
        """Register all available features (easy to extend)"""
        self.features = [
            RootPrivilegeFeature(self.executor),
            APKInstallerFeature(self.executor),
            RebootFeature(self.executor)
        ]

    def run(self):
        """Main application loop"""
        while True:
            self.ui.clear_screen()
            self.ui.print_header()

            # Check device connection
            if not self.device_manager.select_device():
                self.ui.print_status(
                    Status.FAILED,
                    "未检测到设备！请检查 USB/网络连接"
                )
                input(Color.colorize("\n按回车键刷新...", Color.WARNING))
                continue

            # Display device info
            device_info = self.device_manager.get_device_info()
            self.ui.print_device_status(device_info)

            # Show menu
            self.ui.print_menu(self.features)

            # Get user input
            choice = input(Color.colorize("请选择操作: ", Color.BOLD)).strip().lower()

            # Handle choice
            if choice == 'q':
                self.ui.print_status(Status.INFO, "感谢使用！")
                break

            if choice.isdigit():
                index = int(choice) - 1
                if 0 <= index < len(self.features):
                    print()  # Spacing
                    self.features[index].execute()
                    input(Color.colorize("\n按回车返回主菜单...", Color.OKCYAN))
                else:
                    self.ui.print_status(Status.FAILED, "无效选项！")
                    time.sleep(1)
            else:
                self.ui.print_status(Status.FAILED, "无效输入！")
                time.sleep(1)


# ============================================================================
# ENTRY POINT
# ============================================================================

def main():
    """Application entry point"""
    try:
        app = ADBProCLI()
        app.run()
    except KeyboardInterrupt:
        print(Color.colorize("\n\n程序已被用户中断", Color.WARNING))
        sys.exit(0)
    except Exception as e:
        print(Color.colorize(f"\n严重错误: {str(e)}", Color.FAIL))
        sys.exit(1)


if __name__ == "__main__":
    main()