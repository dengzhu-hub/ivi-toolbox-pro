import os
import subprocess
import time
import sys
import re
import threading
import platform
from datetime import datetime
from typing import List, Optional, Tuple, Dict

# ==========================================
# 0. 依赖检查与 UI 库加载
# ==========================================
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.progress import (
        Progress,
        SpinnerColumn,
        TextColumn,
        BarColumn,
        FileSizeColumn,
    )
    from rich.prompt import Prompt
    from rich.layout import Layout
    from rich.align import Align
    from rich.live import Live
    from rich.text import Text
    from rich import box
except ImportError:
    print("\n[!] 缺失组件: rich. 请执行: pip install rich")
    sys.exit(1)


## ==========================================
# 1. 驱动层: 增强型 ADB 核心引擎 (修复 Timeout 参数)
# ==========================================
class AdbDriver:
    def __init__(self, device_id: Optional[str] = None):
        self.device_id = device_id
        self.timeout = 15  # 默认超时

    def run(self, command: str, timeout: int = None) -> Tuple[bool, str]:
        # 关键修复：允许调用时临时指定超时时间
        target_timeout = timeout if timeout is not None else self.timeout

        prefix = f"adb -s {self.device_id} " if self.device_id else "adb "
        full_cmd = prefix + command
        try:
            # 增加 Windows 兼容性设置，防止 CMD 弹窗闪烁
            startupinfo = None
            if os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            proc = subprocess.Popen(
                full_cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",  # 强制 UTF-8
                errors="replace",  # 防止乱码崩溃
                startupinfo=startupinfo,  # 隐藏弹窗
            )
            try:
                # 使用动态传入的 target_timeout
                stdout, stderr = proc.communicate(timeout=target_timeout)
                rc = proc.returncode
                # [BUG FIX] adb pull/push 无论成功失败，进度和错误信息都输出到 stderr
                # 原来 rc==0 只取 stdout 导致 out="" → 后续 "No such" 判断全部失效
                # 统一合并 stdout+stderr，保证任何 adb 子命令的输出都能被捕获
                output = (stdout + stderr).strip()
                return (rc == 0, output)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()
                return False, f"Command timed out after {target_timeout} seconds"
        except Exception as e:
            return False, str(e)


# ==========================================
# 2. 核心模块: 权限解锁专家
# ==========================================
class PrivilegeUnlocker:
    def __init__(self, driver: AdbDriver, console: Console):
        self.driver = driver
        self.console = console
        self.root_pwd = "adayo@N51"

    def execute_unlock_sequence(self):
        self.console.clear()
        self.console.print(
            Panel(
                "[bold red]🔓 正在执行深度提权 (Root + RW)[/bold red]",
                style="red",
                box=box.HEAVY,
            )
        )

        with Progress(
            SpinnerColumn(),
            TextColumn("[cyan]{task.description}"),
            console=self.console,
        ) as progress:
            task = progress.add_task("解锁 Verity...", total=None)
            self.driver.run(f"shell setprop service.adb.root.password {self.root_pwd}")
            self.driver.run("root")
            time.sleep(2)
            self.driver.run("wait-for-device")
            s, v_out = self.driver.run("disable-verity")
            progress.stop()

            if "reboot" in v_out.lower() or "verity is enabled" in v_out.lower():
                self.console.print(f"[yellow]⚠ 需要重启生效 Verity 设置...[/yellow]")
                self.driver.run("reboot")
                time.sleep(10)
                with self.console.status("[bold yellow]等待设备重连..."):
                    self.driver.run("wait-for-device", timeout=60)
                    time.sleep(3)
                self.console.print("[green]✓ 设备已重连[/green]")

        steps = [
            ("注入密码", f"shell setprop service.adb.root.password {self.root_pwd}"),
            ("Root", "root"),
            ("等待ADB", "wait-for-device"),
            ("Remount", "remount"),
            ("Mount /", "shell mount -o rw,remount /"),
            ("Mount /system", "shell mount -o rw,remount /system"),
            ("SELinux", "shell setenforce 0"),
        ]

        with Progress(
            SpinnerColumn(),
            TextColumn("[cyan]{task.description}"),
            BarColumn(),
            console=self.console,
        ) as progress:
            task = progress.add_task("提权中...", total=len(steps))
            for desc, cmd in steps:
                progress.update(task, description=desc)
                if cmd == "wait-for-device":
                    time.sleep(1)
                    self.driver.run(cmd, timeout=30)
                else:
                    self.driver.run(cmd)
                progress.advance(task)

        Prompt.ask("\n按回车返回...")


# ==========================================
# 3. [重写] 核心模块: 实时日志引擎 (LiveLogcatPro)
# ==========================================
class LiveLogcatPro:
    """专业版实时日志引擎: 支持监控、分卷录制、实时统计"""

    def __init__(self, driver: AdbDriver, console: Console):
        self.driver = driver
        self.console = console
        self.is_recording = False
        self.log_thread = None
        self.save_dir = os.path.join(os.getcwd(), "captured_logs")
        if not os.path.exists(self.save_dir):
            os.makedirs(self.save_dir)

        # 统计数据
        self.start_time = None
        self.current_file_path = "N/A"
        self.total_size_bytes = 0
        self.max_file_size_mb = 50
        self.rotation_index = 0

    def start_background(self):
        """启动后台录制"""
        if self.is_recording:
            self.console.print("[yellow]日志录制已在运行中[/yellow]")
            return

        self.is_recording = True
        self.rotation_index = 0
        self.start_time = datetime.now()
        self.total_size_bytes = 0

        self.driver.run("logcat -G 20M")  # 扩大缓冲区
        self.driver.run("logcat -c")  # 清除缓存

        self.log_thread = threading.Thread(target=self._recorder_worker, daemon=True)
        self.log_thread.start()

        # 启动后直接进入仪表盘，给用户反馈
        self.show_recording_dashboard()

    def stop(self):
        if not self.is_recording:
            self.console.print("[yellow]当前未在录制[/yellow]")
            return

        self.is_recording = False
        if self.log_thread:
            self.log_thread.join(timeout=2)

        duration = datetime.now() - self.start_time if self.start_time else 0
        self.console.print(
            Panel(
                f"[bold red]🛑 录制结束[/bold red]\n"
                f"时长: {str(duration).split('.')[0]}\n"
                f"路径: {self.save_dir}",
                border_style="red",
            )
        )
        time.sleep(2)

    def show_recording_dashboard(self):
        """显示实时录制状态仪表盘"""
        if not self.is_recording:
            self.console.print("[red]未在录制[/red]")
            return

        self.console.clear()
        self.console.print("[dim]按 Ctrl+C 返回日志菜单 (录制将继续在后台运行)[/dim]")

        try:
            with Live(refresh_per_second=4) as live:
                while self.is_recording:
                    duration = datetime.now() - self.start_time

                    # 获取当前文件大小
                    current_size = 0
                    if os.path.exists(self.current_file_path):
                        current_size = os.path.getsize(self.current_file_path)

                    # 格式化大小
                    size_mb = current_size / (1024 * 1024)

                    grid = Table.grid(expand=True, padding=(1, 2))
                    grid.add_column(justify="center", ratio=1)

                    # 构建动态面板
                    status_panel = Panel(
                        f"[bold green]🔴 REC[/bold green]\n\n"
                        f"[cyan]⏱️ 录制时长:[/cyan]  {str(duration).split('.')[0]}\n"
                        f"[cyan]💾 当前文件:[/cyan]  {size_mb:.2f} MB / {self.max_file_size_mb} MB (分卷限制)\n"
                        f"[cyan]📂 写入路径:[/cyan]  {os.path.basename(self.current_file_path)}\n"
                        f"[cyan]🔢 当前分卷:[/cyan]  Part {self.rotation_index}",
                        title="[bold magenta]后台录制监控仪表盘[/bold magenta]",
                        border_style="green",
                        box=box.ROUNDED,
                    )

                    live.update(Align.center(status_panel))
                    time.sleep(0.5)
        except KeyboardInterrupt:
            # 用户按 Ctrl+C 只是退出查看，不停止录制
            pass

    def stream_console_log(self):
        """前台实时彩色日志流 (Monitor Mode)"""
        self.console.clear()
        self.console.print(
            Panel(
                "[bold cyan]📺 实时日志监控台 (Live Monitor)[/bold cyan]\n[dim]按 Ctrl+C 停止监控[/dim]",
                style="cyan",
            )
        )

        # 如果后台正在录制，不要清空 logcat，否则会影响录制文件的完整性
        if not self.is_recording:
            self.driver.run("logcat -c")

        cmd = f"adb -s {self.driver.device_id} logcat -v threadtime"

        try:
            process = subprocess.Popen(
                cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="ignore",
            )

            while True:
                line = process.stdout.readline()
                if not line:
                    break

                # 简单解析日志级别进行着色
                line = line.strip()
                style = "white"
                if " E " in line or "FATAL" in line:
                    style = "bold red"
                elif " W " in line:
                    style = "yellow"
                elif " D " in line:
                    style = "blue"
                elif " I " in line:
                    style = "green"
                elif " V " in line:
                    style = "dim white"

                # 如果有 Crash，加背景高亮
                if (
                    "FATAL EXCEPTION" in line
                    or " AndroidRuntime:" in line
                    and " E " in line
                ):
                    self.console.print(line, style="bold white on red")
                else:
                    # 修改后（修复）：
                    if "FATAL" in line:
                        self.console.print(
                            line, style="bold white on red", markup=False
                        )  # <--- 加了这个
                    else:
                        self.console.print(
                            line, style=style, markup=False
                        )  # <--- 加了这个
        except KeyboardInterrupt:
            process.terminate()
            self.console.print("\n[yellow]监控已暂停[/yellow]")
            time.sleep(1)

    def _get_new_filepath(self, timestamp_base):
        self.rotation_index += 1
        return os.path.join(
            self.save_dir,
            f"logcat_{self.driver.device_id}_{timestamp_base}_part{self.rotation_index}.txt",
        )

    def _recorder_worker(self):
        timestamp_base = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.current_file_path = self._get_new_filepath(timestamp_base)

        cmd = f"adb -s {self.driver.device_id} logcat -v threadtime"

        try:
            process = subprocess.Popen(
                cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="ignore",
            )

            with open(self.current_file_path, "w", encoding="utf-8") as f:
                while self.is_recording:
                    line = process.stdout.readline()
                    if not line:
                        break
                    f.write(line)

                    # 简单的分卷检查 (每写入一定量后检查文件大小)
                    # 避免频繁 IO 操作，这里简化处理
                    if f.tell() > self.max_file_size_mb * 1024 * 1024:
                        f.close()
                        self.current_file_path = self._get_new_filepath(timestamp_base)
                        f = open(self.current_file_path, "w", encoding="utf-8")

            process.terminate()
        except Exception as e:
            print(f"Recorder Error: {e}")


class OfflineLogManager:
    """离线日志管家"""

    def __init__(self, driver: AdbDriver, console: Console):
        self.driver = driver
        self.console = console
        self.local_export_dir = os.path.join(os.getcwd(), "exported_logs")
        if not os.path.exists(self.local_export_dir):
            os.makedirs(self.local_export_dir)

    def _check_root(self) -> bool:
        s, uid = self.driver.run("shell id")
        if "uid=0" not in uid:
            self.console.print("[bold red]❌ 此操作必须拥有 Root 权限！[/bold red]")
            return False
        return True

    def clean_logs(self):
        if not self._check_root():
            return
        self.console.clear()
        self.console.print(
            Panel("[bold red]🗑️ 正在清理车机日志...[/bold red]", style="red")
        )
        self.driver.run("remount")

        tasks = [
            ("Cleaning Logcat Dir", "rm -rf /mnt/sdcard/AdayoLog/logcat"),
            ("Cleaning Tombstones", "rm -rf /mnt/sdcard/AdayoLog/tombstones"),
            ("Cleaning YUV Files", "rm /mnt/sdcard/dvr_video/test/yuv*.yuv"),
            ("Syncing Disk", "sync"),
        ]

        with Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(),
            console=self.console,
        ) as progress:
            task = progress.add_task("清理中...", total=len(tasks))
            for desc, cmd in tasks:
                progress.update(task, description=f"[yellow]{desc}[/yellow]")
                self.driver.run(f"shell {cmd}")
                progress.advance(task)
                time.sleep(0.2)

        self.console.print("[bold green]✔ 日志清理完毕[/bold green]")
        Prompt.ask("按回车继续")

    def pull_all_logs(self):
        if not self._check_root():
            return
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest_dir = os.path.join(self.local_export_dir, f"DeviceLog_{timestamp}")
        os.makedirs(dest_dir)

        self.console.clear()
        self.console.print(
            Panel(
                f"[bold cyan]📥 正在全量导出日志[/bold cyan]\n[dim]保存至: {dest_dir}[/dim]",
                style="cyan",
            )
        )

        targets = [
            ("/mnt/sdcard/AdayoLog", "AdayoLog"),
            ("/data/vendor/wifi", "WiFi_Logs"),
            ("/mnt/sdcard/ota/android", "OTA_Logs"),
        ]

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            console=self.console,
        ) as progress:
            main_task = progress.add_task("导出中...", total=len(targets))
            for remote, local_name in targets:
                progress.update(main_task, description=f"拉取 {local_name}...")
                s, ls = self.driver.run(f"shell ls {remote}")
                if "No such" in ls:
                    self.console.print(f"[yellow]⚠ 跳过不存在路径: {remote}[/yellow]")
                else:
                    self.driver.run(
                        f'pull {remote} "{os.path.join(dest_dir, local_name)}"',
                        timeout=300,
                    )
                progress.advance(main_task)

        self.console.print(f"[bold green]✔ 导出完成[/bold green]")
        if platform.system() == "Windows":
            os.startfile(dest_dir)
        Prompt.ask("按回车继续")


class LogCenter:
    """日志功能聚合菜单"""

    def __init__(self, driver: AdbDriver, console: Console):
        self.driver = driver
        self.console = console
        self.live_log = LiveLogcatPro(driver, console)
        self.offline_mgr = OfflineLogManager(driver, console)

    def run_menu(self):
        while True:
            self.console.clear()

            # 状态指示
            rec_status = (
                "[bold green]● 正在录制[/bold green]"
                if self.live_log.is_recording
                else "[dim]⚪ 未启动[/dim]"
            )

            self.console.print(
                Panel(
                    f"[bold magenta]📊 车机日志中心 (Log Center)[/bold magenta]\n[dim]实时状态: {rec_status}[/dim]",
                    style="magenta",
                    box=box.HEAVY,
                )
            )

            menu = Table.grid(padding=(0, 2))
            menu.add_row(
                "[yellow]1[/yellow]",
                "📺 [bold cyan]实时监控台[/bold cyan] [dim](Live Monitor - 类Android Studio)[/dim]",
            )
            menu.add_row(
                "[yellow]2[/yellow]",
                "▶️ [bold green]启动后台录制[/bold green] [dim](+ 自动打开仪表盘)[/dim]",
            )
            menu.add_row(
                "[yellow]3[/yellow]",
                "📈 [bold]查看录制仪表盘[/bold] [dim](查看当前录制进度/大小)[/dim]",
            )
            menu.add_row("[yellow]4[/yellow]", "⏹️ [bold red]停止录制[/bold red]")
            menu.add_row(
                "[yellow]5[/yellow]", "🧹 一键清理日志 [dim](rm AdayoLog/YUV...)[/dim]"
            )
            menu.add_row(
                "[yellow]6[/yellow]", "📥 全量导出日志 [dim](Pull All -> PC)[/dim]"
            )
            menu.add_row("[yellow]7[/yellow]", "📂 打开本地日志目录")
            menu.add_row("[yellow]b[/yellow]", "返回主菜单")

            self.console.print(Panel(menu, border_style="magenta"))
            choice = Prompt.ask("请选择").lower()

            if choice == "1":
                self.live_log.stream_console_log()
            elif choice == "2":
                self.live_log.start_background()
            elif choice == "3":
                self.live_log.show_recording_dashboard()  # 新增：随时查看状态
            elif choice == "4":
                self.live_log.stop()
            elif choice == "5":
                self.offline_mgr.clean_logs()
            elif choice == "6":
                self.offline_mgr.pull_all_logs()
            elif choice == "7":
                path = os.path.join(os.getcwd(), "captured_logs")
                if not os.path.exists(path):
                    os.makedirs(path)
                os.startfile(path) if platform.system() == "Windows" else None
            elif choice == "b":
                return


# ==========================================
# 3. 核心模块: 全能工程仪表盘 (MAX版)
# ==========================================
class DeviceDashboard:
    def __init__(self, driver: AdbDriver, console: Console):
        self.driver = driver
        self.console = console

    def _get_prop(self, key: str) -> str:
        s, o = self.driver.run(f"shell getprop {key}")
        return o.strip() if s else "N/A"

    def _get_shell(self, cmd: str) -> str:
        s, o = self.driver.run(f"shell {cmd}")
        return o.strip() if s else "Unknown"

    def show(self):
        self.console.clear()

        with self.console.status("[bold green]正在深度读取工程信息..."):
            # --- 1. 身份识别 ---
            model = self._get_prop("ro.product.model")
            brand = self._get_prop("ro.product.brand")
            device = self._get_prop("ro.product.device")
            serial = self._get_prop("ro.serialno")
            board = self._get_prop("ro.board.platform")

            # --- 2. 软件版本 ---
            android_ver = self._get_prop("ro.build.version.release")
            sdk_ver = self._get_prop("ro.build.version.sdk")
            build_id = self._get_prop("ro.build.display.id")  # 关键：显示完整的编译号
            if build_id == "N/A":
                build_id = self._get_prop("ro.build.id")
            build_type = self._get_prop("ro.build.type")  # 关键：user 还是 userdebug
            sec_patch = self._get_prop("ro.build.version.security_patch")
            fingerprint = self._get_prop("ro.build.fingerprint")

            # --- 3. 硬件规格 (RAM/ROM) ---
            # 获取 RAM
            mem_info = self._get_shell("cat /proc/meminfo")
            mem_total_kb = re.search(r"MemTotal:\s+(\d+)", mem_info)
            ram_txt = "Unknown"
            if mem_total_kb:
                gb = int(mem_total_kb.group(1)) / 1024 / 1024
                ram_txt = f"{gb:.1f} GB"

            # 获取 ROM (/data 分区)
            df_data = self._get_shell("df -h /data")
            # 解析最后一行: /dev/block/... 50G 10G 40G 20% /data
            rom_txt = "Unknown"
            lines = df_data.splitlines()
            if len(lines) > 1:
                parts = lines[-1].split()
                if len(parts) >= 4:
                    rom_txt = f"{parts[2]} Used / {parts[1]} Total ({parts[-2]})"

            # --- 4. 显示与电源 ---
            wm_size = self._get_shell("wm size").split(":")[-1].strip()
            wm_den = self._get_shell("wm density").split(":")[-1].strip()

            # --- 5. 网络连接 ---
            # IP
            ip_info = self._get_shell("ip addr show wlan0")
            ip_addr = "Disconnected"
            mac_addr = "Unknown"
            m_ip = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", ip_info)
            if m_ip:
                ip_addr = m_ip.group(1)

            # MAC (尝试从文件读，更准)
            mac_file = self._get_shell("cat /sys/class/net/wlan0/address")
            if "No such" not in mac_file:
                mac_addr = mac_file

            # --- 6. 运行状态 ---
            uptime_sec = float(self._get_shell("cat /proc/uptime").split()[0])
            uptime_str = str(
                datetime.fromtimestamp(time.time() - uptime_sec).strftime(
                    "%Y-%m-%d %H:%M:%S 启动"
                )
            )
            hours = int(uptime_sec // 3600)
            mins = int((uptime_sec % 3600) // 60)
            run_time = f"{hours}小时 {mins}分"

        # === 渲染 UI ===

        # 顶部：型号大标题
        title_panel = Panel(
            Align.center(
                f"[bold cyan]{brand} {model}[/bold cyan]  [dim]({device})[/dim]  [bold yellow]{board}[/bold yellow]"
            ),
            style="blue",
            box=box.HEAVY,
        )

        # 区域 1: 软件构建 (Build Info)
        grid_sw = Table.grid(expand=True, padding=(0, 1))
        grid_sw.add_column(style="cyan", justify="right")
        grid_sw.add_column(style="white")
        grid_sw.add_row("Android:", f"{android_ver} (API {sdk_ver})")
        grid_sw.add_row(
            "Build Type:",
            f"[{'green' if 'debug' in build_type else 'red'}]{build_type}[/]",
        )
        grid_sw.add_row("Security:", sec_patch)
        grid_sw.add_row("Build ID:", f"[yellow]{build_id}[/yellow]")
        grid_sw.add_row("Fingerprint:", f"[dim]{fingerprint[:30]}...[/dim]")

        # 区域 2: 硬件资源 (Hardware)
        grid_hw = Table.grid(expand=True, padding=(0, 1))
        grid_hw.add_column(style="green", justify="right")
        grid_hw.add_column(style="white")
        grid_hw.add_row("RAM Total:", ram_txt)
        grid_hw.add_row("Data Disk:", rom_txt)
        grid_hw.add_row("Resolution:", wm_size)
        grid_hw.add_row("Density:", f"{wm_den} dpi")
        grid_hw.add_row("Serial:", serial)

        # 区域 3: 网络与状态 (Net & Status)
        grid_net = Table.grid(expand=True, padding=(0, 1))
        grid_net.add_column(style="magenta", justify="right")
        grid_net.add_column(style="white")
        grid_net.add_row("WLAN IP:", ip_addr)
        grid_net.add_row("MAC Addr:", mac_addr)
        grid_net.add_row("Uptime:", run_time)
        grid_net.add_row("Boot Time:", f"[dim]{uptime_str}[/dim]")

        # 布局组合
        layout = Layout()
        layout.split_column(Layout(name="header", size=3), Layout(name="main", ratio=1))
        layout["header"].update(title_panel)

        layout["main"].split_row(Layout(name="left"), Layout(name="right"))

        layout["left"].update(
            Panel(grid_sw, title="🤖 软件构建信息", border_style="cyan")
        )

        layout["right"].split_column(
            Layout(Panel(grid_hw, title="⚙️ 硬件与存储", border_style="green")),
            Layout(Panel(grid_net, title="🌐 网络与运行状态", border_style="magenta")),
        )

        self.console.print(layout)
        Prompt.ask("\n按回车返回...")


class AppManager:
    def __init__(self, driver: AdbDriver, console: Console):
        self.driver = driver
        self.console = console

    def _get_packages(self, mode="all") -> List[str]:
        flag = "-3" if mode == "3" else ("-s" if mode == "s" else "")
        s, out = self.driver.run(f"shell pm list packages {flag}")
        packages = []
        for line in out.splitlines():
            if "package:" in line:
                packages.append(line.split(":")[-1].strip())
        return sorted(packages)

    def run_uninstall_wizard(self):
        while True:
            self.console.clear()
            self.console.print(Panel("[bold red]🗑️ 应用管理[/bold red]", style="red"))
            menu = Table.grid(padding=(0, 2))
            menu.add_row("[yellow]1[/yellow]", "🔍 搜索卸载")
            menu.add_row("[yellow]2[/yellow]", "📂 浏览第三方应用")
            menu.add_row("[yellow]b[/yellow]", "返回")
            self.console.print(Panel(menu, border_style="yellow"))
            choice = Prompt.ask("选择").lower()
            if choice == "1":
                k = Prompt.ask("关键词").strip()
                if not k:
                    continue
                l = [p for p in self._get_packages("all") if k.lower() in p.lower()]
                self._show_list_and_act(l, f"搜索: {k}")
            elif choice == "2":
                l = self._get_packages("3")
                self._show_list_and_act(l, "第三方应用")
            elif choice == "b":
                return

    def _show_list_and_act(self, packages, title):
        if not packages:
            return
        table = Table(title=f"{title} ({len(packages)})", box=box.ROUNDED)
        table.add_column("ID", justify="center", width=4)
        table.add_column("Package")
        for i, p in enumerate(packages):
            table.add_row(str(i + 1), p)
        self.console.clear()
        self.console.print(table)
        try:
            raw = Prompt.ask("输入ID卸载 (0返回)")
            if raw == "0":
                return
            idx = int(raw) - 1
            if 0 <= idx < len(packages):
                self._execute_uninstall(packages[idx])
        except:
            pass

    def _execute_uninstall(self, pkg):
        if Prompt.ask(f"确认卸载 {pkg}?", choices=["y", "n"], default="n") == "y":
            s, out = self.driver.run(f"uninstall {pkg}")
            if not s:
                s, out = self.driver.run(f"shell pm uninstall --user 0 {pkg}")
            self.console.print(
                "[green]成功[/green]" if s else f"[red]失败: {out}[/red]"
            )
            Prompt.ask("继续")


class ScreenshotTool:
    def __init__(self, driver: AdbDriver, console: Console):
        self.driver = driver
        self.console = console
        self.base_dir = os.path.join(os.getcwd(), "screenshots")
        if not os.path.exists(self.base_dir):
            os.makedirs(self.base_dir)

    def run_menu(self):
        self.console.clear()
        self.console.print(Panel("[bold cyan]📸 截图工具[/bold cyan]", style="cyan"))
        self._do_single_shot()
        Prompt.ask("截图已保存，按回车返回")

    def _do_single_shot(self):
        path = os.path.join(
            self.base_dir, f"screen_{datetime.now().strftime('%H%M%S')}.png"
        )
        self.driver.run(f"shell screencap -p /data/local/tmp/s.png")
        self.driver.run(f'pull /data/local/tmp/s.png "{path}"')
        self.console.print(f"[green]保存至: {path}[/green]")
        if platform.system() == "Windows":
            os.startfile(path)


# ==========================================
# 5. 主程序
# ==========================================
class CarHouseKeepApp:
    def __init__(self):
        self.console = Console()
        self.driver = AdbDriver()
        self.unlocker = PrivilegeUnlocker(self.driver, self.console)
        self.dashboard = DeviceDashboard(self.driver, self.console)
        self.app_manager = AppManager(self.driver, self.console)
        self.screenshot_tool = ScreenshotTool(self.driver, self.console)
        self.log_center = LogCenter(self.driver, self.console)
        self.version = "v7.0-MONITOR-PRO"

    def _make_header(self):
        grid = Table.grid(expand=True)
        grid.add_column(justify="left", ratio=1)
        grid.add_column(justify="right", ratio=1)
        grid.add_row(
            "[bold cyan]IVI TOOLBOX PRO[/bold cyan]", f"[dim]{self.version}[/dim]"
        )
        return Panel(grid, style="bright_blue", box=box.HEAVY)

    def action_install(self):
        path = Prompt.ask("\n[bold]拖入APK文件[/bold]").strip().strip('"')
        if not os.path.exists(path):
            return
        self.console.print("[cyan]开始安装...[/cyan]")
        success, out = self.driver.run(f'install -r -d -g -t "{path}"', timeout=120)
        if success:
            self.console.print("[green]安装成功[/green]")
        else:
            self.console.print(f"[red]安装失败: {out}[/red]")
        Prompt.ask("按回车继续")

    # ==========================================
    # 7. 主界面 UI 渲染引擎 (v9.0 Professional)
    # ==========================================
    def main_menu(self):
        # 缓存设备信息，避免每次刷新都请求 ADB，造成闪烁
        cached_model = None
        cached_android = None

        while True:
            self.console.clear()

            # --- 1. 获取基础状态数据 ---
            # 设备连接
            s, out = self.driver.run("devices")
            devs = [
                l.split()[0]
                for l in out.splitlines()
                if "device" in l and "List" not in l
            ]

            if not devs:
                self.console.print(
                    Panel(
                        Align.center(
                            "[bold red]❌ 未检测到设备连接[/bold red]\n[dim]请检查 USB 连接或 ADB 驱动[/dim]"
                        ),
                        border_style="red",
                        padding=(1, 2),
                    )
                )
                if (
                    Prompt.ask("操作选择", choices=["Retry", "Quit"], default="Retry")
                    == "Quit"
                ):
                    break
                continue

            self.driver.device_id = devs[0]

            # 首次运行或设备变更时获取型号信息 (缓存机制)
            if not cached_model:
                s_m, m = self.driver.run("shell getprop ro.product.model")
                cached_model = m.strip() if s_m else "Unknown"
                s_v, v = self.driver.run("shell getprop ro.build.version.release")
                cached_android = v.strip() if s_v else "Unknown"

            # 权限状态
            s, uid_out = self.driver.run("shell id")
            is_root = "uid=0" in uid_out
            perm_text = (
                "[bold green]ROOT (Unlocked)[/bold green]"
                if is_root
                else "[bold yellow]USER (Locked)[/bold yellow]"
            )

            # 日志状态
            is_rec = self.log_center.live_log.is_recording
            rec_status = (
                "[bold white on red] ● REC [/bold white on red]"
                if is_rec
                else "[dim] ○ IDLE [/dim]"
            )

            # 时间信息
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

            # --- 2. 构建 UI 组件 ---

            # [顶部] 标题栏与元数据
            header_grid = Table.grid(expand=True)
            header_grid.add_column(justify="left", ratio=1)
            header_grid.add_column(justify="right", ratio=1)
            header_grid.add_row(
                f"[bold cyan]IVI TOOLBOX PRO[/bold cyan] [dim]{self.version}[/dim]",
                f"[bold magenta]Jonas[/bold magenta] | [dim]dengzhu-hub[/dim]",
            )

            # [中部] 设备遥测仪表盘
            dash_table = Table(
                box=box.SIMPLE, show_header=False, expand=True, padding=(0, 1)
            )
            dash_table.add_column("Key", style="cyan", justify="right", ratio=1)
            dash_table.add_column("Val", style="white", justify="left", ratio=2)
            dash_table.add_column("Key2", style="cyan", justify="right", ratio=1)
            dash_table.add_column("Val2", style="white", justify="left", ratio=2)

            dash_table.add_row(
                "Device:",
                f"[bold white]{cached_model}[/bold white]",
                "Android:",
                cached_android,
            )
            dash_table.add_row(
                "Serial:",
                f"[dim]{self.driver.device_id}[/dim]",
                "Privilege:",
                perm_text,
            )
            dash_table.add_row(
                "Log Status:", rec_status, "Sys Time:", f"[yellow]{now_str}[/yellow]"
            )

            # [下部] 功能矩阵 (双列布局)
            menu_table = Table(
                box=box.ROUNDED,
                show_header=True,
                header_style="bold blue",
                expand=True,
                border_style="dim",
            )
            menu_table.add_column("🛠️ 核心运维", ratio=1)
            menu_table.add_column("🧰 应用工具", ratio=1)

            menu_table.add_row(
                "[bold yellow]1[/bold yellow]  🚀 工程提权 [dim](Root/Remount)[/dim]",
                "[bold yellow]4[/bold yellow]  💿 智能安装 APK [dim](Auto-Grant)[/dim]",
            )
            menu_table.add_row(
                "[bold yellow]2[/bold yellow]  📊 系统监控 [dim](Top/Sentinel)[/dim]",
                "[bold yellow]5[/bold yellow]  🗑️ 应用卸载 [dim](App Manager)[/dim]",
            )
            menu_table.add_row(
                "[bold yellow]3[/bold yellow]  📺 日志中心 [dim](Live/Pull/Clean)[/dim]",
                "[bold yellow]6[/bold yellow]  📸 专业截图 [dim](Burst/Delay)[/dim]",
            )
            menu_table.add_row(
                "[bold yellow]7[/bold yellow]  🔄 重启设备 [dim](Reboot)[/dim]",
                "[bold red]q[/bold red]   退出系统",
            )

            # --- 3. 组合渲染 ---
            layout = Layout()
            layout.split_column(
                Layout(Panel(header_grid, style="blue", box=box.HEAVY), size=3),
                Layout(
                    Panel(
                        dash_table,
                        title="[bold green]📡 实时遥测 (Telemetry)[/bold green]",
                        border_style="green",
                    ),
                    size=5,
                ),
                Layout(menu_table),
            )

            self.console.print(layout)

            # --- 4. 交互逻辑 ---
            choice = Prompt.ask(
                "\n[bold cyan]请输入指令[/bold cyan]", default=""
            ).lower()

            if choice == "1":
                self.unlocker.execute_unlock_sequence()
            elif choice == "2":
                self.sentinel.start_monitor()
            elif choice == "3":
                self.log_center.run_menu()
            elif choice == "4":
                self.action_install()
            elif choice == "5":
                self.app_mgr.run_menu()
            elif choice == "6":
                self.screenshot_tool.run_menu()
            elif choice == "7":
                if Prompt.ask("确认重启?", choices=["y", "n"]) == "y":
                    self.driver.run("reboot")
                    cached_model = None  # 重启后清除缓存
            elif choice == "q":
                self.console.print("[green]再见！[/green]")
                break


if __name__ == "__main__":
    app = CarHouseKeepApp()
    try:
        app.main_menu()
    except KeyboardInterrupt:
        print("\nExit")
