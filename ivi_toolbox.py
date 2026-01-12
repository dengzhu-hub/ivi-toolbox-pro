import os
import subprocess
import difflib
import json
import time
import sys
import re
import threading
from typing import List, Optional, Tuple, Dict
from datetime import datetime
from abc import ABC, abstractmethod
from rich.align import Align  # 必须添加这一行
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
import requests
# 依赖检查
try:
    from rich.console import Console
    from rich.live import Live
    from rich.table import Table
    from rich.panel import Panel
    from rich.layout import Layout
    from rich.text import Text
    from rich.style import Style
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
    from rich.prompt import Prompt
    from rich import box
    import pexpect
    from PIL import Image, ImageDraw, ImageFont  # For watermarking screenshots
except ImportError as e:
    print(f"\n[!] 缺失组件: {e.name}. 请执行: pip install rich pexpect pillow")
    sys.exit(1)



# ==========================================
# [新增] 基础架构: 全局配置加载器 (Config Engine)
# ==========================================
class ConfigLoader:
    """负责外部 config.json 的读取、写入与默认值生成"""
    CONFIG_FILE = "config.json"

# 默认配置模板
    DEFAULT_CONFIG = {
        "root_password": "adayo@N51",
        "unsplash_keys": [
            "BD0I4Br4tLY4WVyNFCNIzxB-IUn1uMkSP4Ebl8Bf4AY"
        ],
        "paths": {
            "materials": "test_materials",
            "screenshots": "screenshots",
            "logs": "captured_logs"
        },
        # [新增] Unsplash 全量官方主题库 (Slug ID)
        "unsplash_catalog": {
            "🚗 车载/交通": ["traffic", "car", "vehicle", "interior"],
            "🖥️ 科技/数码": ["technology", "artificial-intelligence", "cyberpunk"],
            "🎨 纹理/背景": ["textures-patterns", "wallpapers", "3d-renders", "experimental"],
            "🏙️ 建筑/城市": ["architecture", "interiors", "street-photography", "travel"],
            "🌿 自然/风光": ["nature", "animals", "spirituality"],
            "👥 人文/商业": ["people", "business-work", "fashion", "film"],
            "🍜 生活/健康": ["food-drink", "health", "arts-culture", "history", "athletics"]
        }
    }

    def __init__(self):
        self.data = self._load()

    def _load(self) -> dict:
        if not os.path.exists(self.CONFIG_FILE):
            # 不存在则创建默认配置
            self._save(self.DEFAULT_CONFIG)
            return self.DEFAULT_CONFIG

        try:
            with open(self.CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"[!] 配置文件损坏，加载默认配置: {e}")
            return self.DEFAULT_CONFIG

    def _save(self, data: dict):
        try:
            with open(self.CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"[!] 配置保存失败: {e}")

    def get(self, key, default=None):
        """获取配置项"""
        return self.data.get(key, default)

    def set(self, key, value):
        """更新并持久化配置"""
        self.data[key] = value
        self._save(self.data)

# ==========================================
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
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            proc = subprocess.Popen(
                full_cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',       # 强制 UTF-8
                errors='replace',       # 防止乱码崩溃
                startupinfo=startupinfo # 隐藏弹窗
            )
            try:
                # 使用动态传入的 target_timeout
                stdout, stderr = proc.communicate(timeout=target_timeout)
                rc = proc.returncode
                output = stdout.strip() if rc == 0 else stderr.strip()
                return (rc == 0, output)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()
                return False, f"Command timed out after {target_timeout} seconds"
        except Exception as e:
            return False, str(e)

# ==========================================
# 2. 核心模块: 日志自动归档引擎 (LogRecorder)
# ==========================================
class LogRecorder:
    """后台日志监控与归档引擎"""
    def __init__(self, driver: AdbDriver):
        self.driver = driver
        self.is_recording = False
        self.log_thread = None
        self.log_file_path = ""
        self.save_dir = os.path.join(os.getcwd(), "test_logs")

        if not os.path.exists(self.save_dir):
            os.makedirs(self.save_dir)

    def _log_worker(self):
        """后台线程：实时拉取 logcat 并保存"""
        # 清除旧日志缓存，确保从当前时刻开始抓取
        self.driver.run("logcat -c")

        prefix = f"adb -s {self.driver.device_id} " if self.driver.device_id else "adb "
        cmd = prefix + "logcat -v threadtime"

        with open(self.log_file_path, "w", encoding="utf-8") as f:
            process = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,encoding='utf-8',    # 新增
    errors='replace' )
            while self.is_recording:
                line = process.stdout.readline()
                if not line: break
                f.write(line)
                # 工业级特性：实时检测异常关键词
                if "FATAL EXCEPTION" in line or "ANR in" in line:
                    # 这里可以扩展触发弹窗或截图逻辑
                    pass
            process.terminate()

    def start(self):
        """启动归档"""
        if self.is_recording: return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file_path = os.path.join(self.save_dir, f"log_{self.driver.device_id}_{timestamp}.log")

        self.is_recording = True
        self.log_thread = threading.Thread(target=self._log_worker, daemon=True)
        self.log_thread.start()

    def stop(self):
        """停止归档"""
        self.is_recording = False
        if self.log_thread:
            self.log_thread.join(timeout=2)
        return self.log_file_path

# ==========================================
# 驱动层扩展: BaseSource for IVI
# ==========================================
class BaseSource(ABC):
    @abstractmethod
    def run_command(self, cmd: str, use_root: bool = False) -> str: pass

# ==========================================
# 修复后的 AdbSource 类 (最大权限测试版)
# ==========================================
class AdbSource(BaseSource):
    """
    专门针对 Adayo 车机优化的 Root 提权驱动
    集成了：密码注入、Disable Verity、SELinux 关闭、分区解锁 (上帝模式)
    """
    def __init__(self, device_id: Optional[str] = None):
        self.device_id = device_id
        self.root_pwd = "adayo@N51"
        self.is_root_verified = False

    def run_command(self, command: str, use_root: bool = False) -> str:
        """实现基类 BaseSource 的抽象方法，确保不报错"""
        prefix = f"adb -s {self.device_id} " if self.device_id else "adb "
        if use_root:
            full_cmd = prefix + f'shell "echo {self.root_pwd} | su -c {command}"'
        else:
            full_cmd = prefix + f'shell "{command}"'
        try:
            res = subprocess.run(full_cmd, shell=True, capture_output=True, text=True, timeout=10,encoding='utf-8', errors='replace')
            return res.stdout.strip()
        except Exception:
            return ""

    def run_raw(self, cmd: str) -> str:
        """
        核心修复：定义 run_raw 方法供 action_gain_root 调用
        执行底层原始 ADB 指令 (如 root, remount, disable-verity)
        """
        prefix = f"adb -s {self.device_id} " if self.device_id else "adb "
        try:
            # 合并 stdout 和 stderr 以便捕获所有输出信息
            res = subprocess.run(prefix + cmd, shell=True, capture_output=True, text=True, timeout=15,encoding='utf-8', errors='replace')
            return (res.stdout + res.stderr).strip()
        except Exception as e:
            return str(e)

    def request_full_power_access(self, progress_callback) -> Tuple[bool, str]:
        """上帝模式提权流水线"""
        try:
            # 1. 注入 Adayo 专用密码属性
            progress_callback(10, "正在注入 Adayo 认证密钥属性...")
            self.run_raw(f"shell setprop service.adb.root.password {self.root_pwd}")
            time.sleep(0.5)

            # 2. 切换 Root 模式
            progress_callback(30, "正在重启 ADB 守护进程至 ROOT 模式...")
            self.run_raw("root")
            time.sleep(3) # 等待服务重启

            # 3. 深度 Disable 安全策略
            progress_callback(60, "执行深度 Disable (SELinux / Verity)...")
            self.run_raw("shell setenforce 0")
            self.run_raw("shell setprop ro.boot.selinux disabled")
            # 这一步非常重要，如果提示需要 reboot，说明 Verity 之前是开启的
            verity_info = self.run_raw("disable-verity")

            # 4. 强制解锁分区 (Remount)
            progress_callback(85, "正在执行 Remount 并解锁系统分区读写...")
            remount_info = self.run_raw("remount")
            # 补充强制挂载指令，确保最大权限
            self.run_raw("shell mount -o remount,rw /")
            self.run_raw("shell mount -o remount,rw /system")
            self.run_raw("shell mount -o remount,rw /vendor")

            # 5. 最终权限验证
            progress_callback(100, "正在验证最终权限状态...")
            uid_info = self.run_command("id")

            # 检查是否涉及需要重启的情况
            needs_reboot = "reboot" in verity_info.lower() or "reboot" in remount_info.lower()

            if "uid=0" in uid_info:
                self.is_root_verified = True
                msg = "【上帝模式已激活】UID:0 | SELinux:Off | FS:RW"
                if needs_reboot:
                    msg += " (需重启车机后 Verity 禁用才彻底生效)"
                return True, msg

            return False, f"提权验证失败: {uid_info}"

        except Exception as e:
            return False, f"执行异常: {str(e)}"

# ==========================================
# 4. 核心模块: 日志引擎 (增强分析版+修复Platform)
# ==========================================
class LogcatAdvanced:
    def __init__(self, driver: AdbDriver, console: Console):
        self.driver = driver
        self.console = console
        self.filter_config = {"level": "V", "tag": "", "keyword": "", "exclude": ""}
        self.is_recording = False
        self.save_dir = os.path.join(os.getcwd(), "captured_logs")
        if not os.path.exists(self.save_dir): os.makedirs(self.save_dir)
        self.log_thread = None
        self.start_time = None
        self.current_file = ""

    def _build_cmd(self):
        cmd = "logcat -v threadtime"
        if self.filter_config["level"] != "V": cmd += f" *:{self.filter_config['level']}"
        if self.filter_config["tag"]: cmd += f" -s {self.filter_config['tag']}"
        if self.filter_config["keyword"]: cmd += f" | grep -i '{self.filter_config['keyword']}'"
        if self.filter_config["exclude"]: cmd += f" | grep -v '{self.filter_config['exclude']}'"
        return cmd

    def show_filter_menu(self):
        while True:
            self.console.clear()
            self.console.print(Panel("[bold cyan]🎛️ Logcat 过滤器[/bold cyan]", style="cyan"))

            grid = Table.grid(expand=True)
            grid.add_column(style="yellow"); grid.add_column(style="white")
            grid.add_row("Level:", self.filter_config['level'])
            grid.add_row("Tag:", self.filter_config['tag'] or "ALL")
            grid.add_row("Grep:", self.filter_config['keyword'] or "None")
            self.console.print(Panel(grid, title="当前配置", border_style="dim"))

            menu = Table.grid(padding=(0, 2))
            menu.add_row("1", "设置等级 (V/D/I/W/E)")
            menu.add_row("2", "设置 TAG")
            menu.add_row("3", "设置关键词")
            menu.add_row("4", "设置排除词")
            menu.add_row("5", "重置配置")
            menu.add_row("s", "[bold green]启动实时监控[/bold green]")
            menu.add_row("r", "[bold red]启动后台录制[/bold red]")
            menu.add_row("b", "返回")
            self.console.print(Panel(menu, border_style="cyan"))

            c = Prompt.ask("选项").lower()
            if c == '1': self.filter_config['level'] = Prompt.ask("等级", choices=['V','D','I','W','E'], default='V')
            elif c == '2': self.filter_config['tag'] = Prompt.ask("TAG")
            elif c == '3': self.filter_config['keyword'] = Prompt.ask("Keyword")
            elif c == '4': self.filter_config['exclude'] = Prompt.ask("排除词")
            elif c == '5': self.filter_config = {"level": "V", "tag": "", "keyword": "", "exclude": ""}
            elif c == 's': self.start_monitor()
            elif c == 'r': self.start_background()
            elif c == 'b': return

    def _analyze_session(self, logs: List[str], start_time: datetime):
        """停止后的智能日志分析"""
        duration = (datetime.now() - start_time).total_seconds()
        total_lines = len(logs)
        if total_lines == 0:
            self.console.print("[yellow]未捕获到任何日志数据。[/yellow]")
            return

        level_counts = {'E': 0, 'W': 0, 'F': 0}
        tag_counts = {}
        crash_snippets = []
        pattern = re.compile(r'\d+\s+\d+\s+([VDIWEF])\s+([^:]+):')

        for line in logs:
            line = line.strip()
            if " F " in line or "FATAL" in line: level_counts['F'] += 1
            elif " E " in line: level_counts['E'] += 1
            elif " W " in line: level_counts['W'] += 1

            if "FATAL EXCEPTION" in line:
                crash_snippets.append(line[:100] + "...")

            match = pattern.search(line)
            if match:
                tag = match.group(2).strip()
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

        self.console.clear()
        rate = total_lines / duration if duration > 0 else 0
        grid = Table.grid(expand=True, padding=(0, 2))
        grid.add_column(style="cyan", justify="right")
        grid.add_column(style="white")
        grid.add_row("⏱️ 监控时长:", f"{duration:.1f} 秒")
        grid.add_row("📝 捕获行数:", f"{total_lines} 行")
        grid.add_row("🚀 刷新速率:", f"{rate:.1f} 行/秒")

        health_color = "green"
        if level_counts['F'] > 0: health_color = "bold red"
        elif level_counts['E'] > 50: health_color = "yellow"

        grid.add_row("🚑 异常统计:", f"[{health_color}]Fatal: {level_counts['F']} | Error: {level_counts['E']} | Warn: {level_counts['W']}[/]")

        sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        top_tags_str = "\n".join([f"{i+1}. [yellow]{tag}[/]: {count}" for i, (tag, count) in enumerate(sorted_tags)])

        report_table = Table(box=box.ROUNDED, show_header=True, expand=True)
        report_table.add_column("📊 数据概览", ratio=1)
        report_table.add_column("🏆 噪音来源 (Top 5)", ratio=1)
        report_table.add_row(grid, top_tags_str or "[dim]无[/dim]")

        self.console.print(Panel(report_table, title="[bold magenta]Logcat 智能诊断报告[/bold magenta]", border_style="magenta"))

        if crash_snippets:
            self.console.print(Panel("\n".join(crash_snippets[:5]), title="[bold red]🚨 崩溃堆栈[/bold red]", border_style="red"))

        Prompt.ask("\n按回车键返回...")

    def start_monitor(self):
        """前台实时监控"""
        import platform  # <--- 核心修复：内部导入，防止NameError

        self.console.clear()
        cmd_str = self._build_cmd()
        self.console.print(f"[dim]CMD: {cmd_str}[/dim]")
        self.console.print("[cyan]监控中... (Ctrl+C 停止并分析)[/cyan]")

        self.driver.run("logcat -c")

        startupinfo = None
        if platform.system() == "Windows":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        session_logs = []
        start_time = datetime.now()

        try:
            proc = subprocess.Popen(f"adb -s {self.driver.device_id} {cmd_str}", shell=True, stdout=subprocess.PIPE, text=True, encoding='utf-8', errors='replace', startupinfo=startupinfo)
            while True:
                line = proc.stdout.readline()
                if not line: break

                session_logs.append(line)

                line = line.strip()
                style = "white"
                if " E " in line or "FATAL" in line: style = "red"
                elif " W " in line: style = "yellow"

                if "FATAL" in line: self.console.print(line, style="bold white on red", markup=False)
                else: self.console.print(line, style=style, markup=False)

        except KeyboardInterrupt:
            proc.terminate()
            self.console.print("\n[yellow]生成分析报告...[/yellow]")
            self._analyze_session(session_logs, start_time)

    def start_background(self):
        if self.is_recording: return
        self.is_recording = True
        self.start_time = datetime.now()
        self.driver.run("logcat -c")
        self.log_thread = threading.Thread(target=self._bg_worker, daemon=True)
        self.log_thread.start()
        self._show_dashboard()

    def stop_recording(self):
        if not self.is_recording:
            self.console.print("[yellow]未在录制[/yellow]"); time.sleep(1); return
        self.is_recording = False
        self.log_thread.join(timeout=2)
        self.console.print(Panel(f"[red]录制结束[/red]\n文件: {self.current_file}", border_style="red")); time.sleep(2)

    def _bg_worker(self):
        import platform # <--- 核心修复：内部导入

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.current_file = os.path.join(self.save_dir, f"log_{self.driver.device_id}_{ts}.txt")
        cmd_str = self._build_cmd()

        startupinfo = None
        if platform.system() == "Windows":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        try:
            with open(self.current_file, "w", encoding="utf-8") as f:
                proc = subprocess.Popen(f"adb -s {self.driver.device_id} {cmd_str}", shell=True, stdout=f, stderr=subprocess.PIPE, startupinfo=startupinfo)
                while self.is_recording:
                    if proc.poll() is not None: break
                    time.sleep(0.5)
                proc.terminate()
        except: pass

    def _show_dashboard(self):
        try:
            with Live(refresh_per_second=2) as live:
                while self.is_recording:
                    dur = str(datetime.now() - self.start_time).split('.')[0]
                    size = os.path.getsize(self.current_file) / (1024*1024) if os.path.exists(self.current_file) else 0
                    p = Panel(f"[bold green]🔴 REC[/bold green]\nTime: {dur}\nSize: {size:.2f} MB\nFile: {os.path.basename(self.current_file)}", title="后台录制", border_style="red")
                    live.update(Align.center(p))
                    time.sleep(0.5)
        except KeyboardInterrupt: pass

# ==========================================
# [修复] 核心模块: 离线日志管理 (一键清理/导出)
# ==========================================
class OfflineLogManager:
    def __init__(self, driver: AdbDriver, console: Console):
        self.driver = driver
        self.console = console

    def clean_logs(self):
        self.console.print(Panel("[bold red]🧹 进入深度清理模式[/bold red]", border_style="red"))

        # 1. 清理前的空间检查
        _, before_out = self.driver.run("shell df -h /mnt/sdcard")

        # 2. 扩充清理目标列表 (路径, 描述)
        clean_targets = [
            ("/mnt/sdcard/AdayoLog/*", "核心系统日志 (AdayoLog)"),
            ("/data/anr/*", "应用无响应日志 (ANR)"),
            ("/data/tombstones/*", "底层崩溃堆栈 (Tombstones)"),
            ("/mnt/sdcard/dvr_video/test/*.yuv", "YUV 预览临时文件"),
            ("/mnt/sdcard/ota/*.zip", "残留升级包 (OTA)"),
            ("/data/local/tmp/*", "ADB 临时传输目录")
        ]

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=None),
            TextColumn("[bold cyan]{task.fields[status]}"),
            console=self.console
        ) as p:
            task_id = p.add_task("正在清理...", total=len(clean_targets), status="准备中")

            for path, desc in clean_targets:
                p.update(task_id, description=f"正在处理: {desc}")

                # 使用 su -c 确保权限，并强制删除
                # 注意：有些车机 rm 不支持 -rf，增加判断
                success, _ = self.driver.run(f"shell \"su 0 rm -rf {path} || rm -rf {path}\"")

                if success:
                    p.update(task_id, status="[DONE]")
                else:
                    p.update(task_id, status="[SKIP]")

                time.sleep(0.2) # 视觉停留，增强交互感
                p.advance(task_id)

            # 3. 强制触发文件系统同步与缓存清理
            p.update(task_id, description="正在同步文件系统 (Sync)...", status="⌛")
            self.driver.run("shell sync")

        # 4. 清理后的空间检查与结果对比
        _, after_out = self.driver.run("shell df -h /mnt/sdcard")

        # 简单的字符串解析提取剩余空间（假设输出格式标准）
        self.console.print("\n[bold green]✅ 清理任务已完成！[/bold green]")

        # 5. 结果看板
        res_table = Table(box=box.MINIMAL_DOUBLE_HEAD, show_header=True, header_style="bold magenta")
        res_table.add_column("项目", justify="right")
        res_table.add_column("状态/数据", justify="left")
        res_table.add_row("清理目录总数", f"{len(clean_targets)} 个")
        res_table.add_row("系统状态", "已执行 Sync 强制同步")
        res_table.add_row("建议操作", "建议手动重启车机以刷新索引")

        self.console.print(res_table)
        Prompt.ask("\n[dim]按回车键返回主菜单...[/dim]")

    def pull_logs(self):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = os.path.join(os.getcwd(), "exported_logs", f"Log_{ts}")
        os.makedirs(dest, exist_ok=True)

        # 定义需要导出的目标及其描述
        targets = [
            ("/mnt/sdcard/AdayoLog", "车机核心日志 (AdayoLog)"),
            ("/data/vendor/wifi", "WiFi 调试日志"),
            ("/mnt/sdcard/ota/android", "OTA 升级日志"),
            ("/data/tombstones", "系统崩溃堆栈 (Tombstones)")
        ]

        self.console.print(Panel(f"[bold cyan]📥 开始全量导出[/bold cyan]\n[dim]目标路径: {dest}[/dim]", border_style="cyan"))

        # 使用 Progress 组件实现专业进度显示
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=None, finished_style="green"),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=self.console
        ) as progress:

            overall_task = progress.add_task("[bold white]整体进度[/bold white]", total=len(targets))

            for remote, label in targets:
                local_subdir = os.path.join(dest, os.path.basename(remote))
                progress.update(overall_task, description=f"[yellow]正在拉取: {label}[/yellow]")

                # 执行 ADB PULL
                success, output = self.driver.run(f"pull {remote} \"{local_subdir}\"")

                if not success:
                    self.console.print(f"[dim red]⚠ 跳过 {label}: 路径不存在或无权限[/dim red]")

                progress.advance(overall_task)

        self.console.print(f"\n[bold green]✅ 导出完成！[/bold green]")
        self.console.print(f"[cyan]📂 文件夹已保存至: [underline]{dest}[/underline][/cyan]")

        # 自动打开目录 (仅限 Windows)
        if os.name == 'nt':
            os.startfile(dest)

        Prompt.ask("\n[dim]按回车键返回...[/dim]")



# ==========================================
# [新增] 核心模块: OTA 配置管家
# ==========================================
# ==========================================
# [修复] 核心模块: OTA 配置管家 (支持 JSON)
# ==========================================
class OtaConfigManager:
    def __init__(self, driver: AdbDriver, console: Console):
        self.driver = driver
        self.console = console
        self.remote_path = "/mnt/sdcard/DeviceInfo.txt"
        self.local_temp = "temp_device_info.txt"
        self.is_json_format = True # 标记源文件格式

    def _validate_vin(self, vin: str) -> Tuple[bool, str]:
        """VIN 码校验逻辑"""
        if len(vin) != 17: return False, "长度必须为 17 位"
        if any(c in vin.upper() for c in ['I', 'O', 'Q']): return False, "包含非法字符 (I, O, Q)"
        if not re.match(r'^[A-Z0-9]+$', vin): return False, "包含特殊符号"
        return True, "验证通过"

    def _parse_config(self, content: str) -> Dict[str, str]:
        """智能解析 (优先 JSON，失败则尝试 Key=Value)"""
        content = content.strip()

        # 1. 尝试 JSON 解析 (针对你的车机情况)
        try:
            data = json.loads(content)
            self.is_json_format = True
            return data
        except json.JSONDecodeError:
            pass # 不是 JSON，尝试传统格式

        # 2. 尝试 Key=Value 解析 (兼容旧设备)
        self.is_json_format = False
        config = {}
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line: continue
            k, v = line.split('=', 1)
            # 去除可能存在的引号
            config[k.strip()] = v.strip().strip('"').strip("'")
        return config

    def run_wizard(self):
        """OTA 配置修改向导"""
        self.console.clear()
        self.console.print(Panel("[bold magenta]🔧 OTA 参数配置专家[/bold magenta]", style="magenta", box=box.HEAVY))

        # 1. 拉取配置
        with self.console.status("[bold cyan]正在从车机拉取配置文件..."):
            # 这里的 cat 比 pull 更快且不产生临时文件问题，但为了兼容中文编码，还是用 pull 稳妥
            if os.path.exists(self.local_temp): os.remove(self.local_temp)
            s, out = self.driver.run(f"pull {self.remote_path} {self.local_temp}")

        if not s or not os.path.exists(self.local_temp):
            self.console.print(Panel(f"[red]❌ 拉取失败: 找不到 {self.remote_path}[/red]", border_style="red"))
            Prompt.ask("按回车返回")
            return

        # 2. 读取并解析
        try:
            with open(self.local_temp, 'r', encoding='utf-8') as f:
                content = f.read()
            # 调试：打印原始内容的前50个字符
            # self.console.print(f"[dim]Raw: {content[:50]}...[/dim]")
            config_data = self._parse_config(content)
        except Exception as e:
            self.console.print(f"[red]解析失败: {e}[/red]")
            return

        # 3. 显示当前配置
        pno = config_data.get("ICC_PNO", "N/A")
        vin = config_data.get("VIN", "N/A")

        grid = Table.grid(expand=True, padding=(0,2))
        grid.add_column(style="cyan", justify="right")
        grid.add_column(style="bold white")
        grid.add_row("ICC_PNO:", pno)
        grid.add_row("VIN Code:", vin)

        # 显示其他可能的字段 (如 f1A1, 0525)
        extra_keys = [k for k in config_data.keys() if k not in ["ICC_PNO", "VIN"]]
        if extra_keys:
            grid.add_row("[dim]Other:[/dim]", f"[dim]{', '.join(extra_keys)}[/dim]")

        self.console.print(Panel(grid, title="[yellow]当前设备参数[/yellow]", border_style="yellow"))

        # 4. 交互修改
        if Prompt.ask("\n是否修改配置?", choices=["y", "n"], default="n") == "n":
            if os.path.exists(self.local_temp): os.remove(self.local_temp)
            return

        new_pno = Prompt.ask("请输入新 ICC_PNO", default=pno).strip()

        while True:
            new_vin = Prompt.ask("请输入新 VIN 码", default=vin).strip().upper()
            is_valid, msg = self._validate_vin(new_vin)
            if is_valid: break
            self.console.print(f"[red]❌ VIN 格式错误: {msg}[/red]")

        # 5. 生成并推送
        if new_pno != pno or new_vin != vin:
            config_data["ICC_PNO"] = new_pno
            config_data["VIN"] = new_vin

            try:
                with open(self.local_temp, 'w', encoding='utf-8') as f:
                    if self.is_json_format:
                        # 核心修复：按 JSON 格式写回
                        # separators=(',', ':') 去除空格，使其紧凑，与车机原格式保持一致
                        json.dump(config_data, f, separators=(',', ':'), ensure_ascii=False)
                    else:
                        # 按 Key=Value 写回
                        for k, v in config_data.items():
                            f.write(f"{k}={v}\n")

                with self.console.status("[bold green]正在推送新配置..."):
                    self.driver.run("root")
                    self.driver.run("remount")
                    s, out = self.driver.run(f"push {self.local_temp} {self.remote_path}")

                if s:
                    self.console.print(Panel(f"[bold green]✅ 更新成功！[/bold green]\n请重启车机生效", border_style="green"))
                else:
                    self.console.print(f"[red]推送失败: {out}[/red]")
            except Exception as e:
                self.console.print(f"[red]写入错误: {e}[/red]")
        else:
            self.console.print("[dim]配置未变化[/dim]")

        if os.path.exists(self.local_temp): os.remove(self.local_temp)
        Prompt.ask("\n按回车返回...")


# ==========================================
# [修复] 核心模块: 日志指挥中心 (入口)
# ==========================================
class LogCenter:
    def __init__(self, driver: AdbDriver, console: Console):
        self.driver = driver
        self.console = console
        self.live_log = LogcatAdvanced(driver, console)
        self.offline_mgr = OfflineLogManager(driver, console)

    def run_menu(self):
        while True:
            self.console.clear()
            rec_status = "[bold green]正在录制[/bold green]" if self.live_log.is_recording else "[dim]未启动[/dim]"
            self.console.print(Panel(f"[bold magenta]📊 车机日志中心[/bold magenta] (状态: {rec_status})", style="magenta"))
            menu = Table.grid(padding=(0, 2))
            menu.add_row("[yellow]1[/yellow]", "📺 [bold cyan]实时监控台[/bold cyan] (带过滤器)")
            menu.add_row("[yellow]2[/yellow]", "▶️ 启动后台录制 (带仪表盘)")
            menu.add_row("[yellow]3[/yellow]", "⏹️ 停止录制")
            menu.add_row("[yellow]4[/yellow]", "🧹 一键清理日志")
            menu.add_row("[yellow]5[/yellow]", "📥 全量导出日志")
            menu.add_row("[yellow]b[/yellow]", "返回主菜单")
            self.console.print(Panel(menu, border_style="magenta"))

            c = Prompt.ask("选择")
            if c == '1': self.live_log.show_filter_menu()
            elif c == '2': self.live_log.start_background()
            elif c == '3': self.live_log.stop_recording()
            elif c == '4': self.offline_mgr.clean_logs()
            elif c == '5': self.offline_mgr.pull_logs()
            elif c == 'b': return


# ==========================================
# 3. 核心模块: 专业 Logcat 分析工具
# ==========================================
class LogcatAnalyzer:
    """实时 Logcat 分析与过滤工具"""
    def __init__(self):
        self.console = Console()
        self.driver = AdbDriver()

        # --- 修复：初始化新的日志模块 ---
        self.log_center = LogCenter(self.driver, self.console)
        # (移除旧的 self.recorder 和 self.logcat_analyzer)

        self.screenshot_manager = ScreenshotManager(self.driver, self.console)
        self.version = "v3.3.0-ROOT-FIXED"
        self.ivi_source = None
        self.ivi_engine = None
        self.ivi_ui = None
        self.current_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.time_update_stop = False
        self.time_update_thread = None

    def _build_filter_command(self) -> str:
        """构建 logcat 过滤命令"""
        cmd = "logcat -v threadtime"

        # 日志级别过滤
        if self.filter_config["level"] != "V":
            cmd += f" *:{self.filter_config['level']}"

        # TAG 过滤
        if self.filter_config["tag"]:
            cmd += f" | grep '{self.filter_config['tag']}'"

        # 关键词过滤
        if self.filter_config["keyword"]:
            cmd += f" | grep '{self.filter_config['keyword']}'"

        # 排除关键词
        if self.filter_config["exclude"]:
            cmd += f" | grep -v '{self.filter_config['exclude']}'"

        return cmd

    def _parse_log_line(self, line: str) -> Dict[str, str]:
        """解析日志行，提取关键信息"""
        # 格式: 01-07 12:34:56.789  1234  5678 I TagName: message
        pattern = r'(\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3})\s+(\d+)\s+(\d+)\s+([VDIWEF])\s+([^:]+):\s*(.*)'
        match = re.match(pattern, line)

        if match:
            return {
                "time": match.group(1),
                "pid": match.group(2),
                "tid": match.group(3),
                "level": match.group(4),
                "tag": match.group(5).strip(),
                "message": match.group(6)
            }
        return None

    def _get_level_color(self, level: str) -> str:
        """根据日志级别返回颜色"""
        colors = {
            "V": "dim white",
            "D": "cyan",
            "I": "green",
            "W": "yellow",
            "E": "red",
            "F": "bold red"
        }
        return colors.get(level, "white")

    def _format_log_line(self, parsed: Dict[str, str]) -> str:
        """格式化日志输出"""
        level_color = self._get_level_color(parsed["level"])

        # 检测崩溃关键词
        is_crash = any(keyword in parsed["message"] for keyword in
                      ["FATAL EXCEPTION", "ANR in", "Native crash", "SIGSEGV", "SIGABRT"])

        if is_crash:
            return f"[bold red on white]🚨 CRASH[/] [{level_color}]{parsed['level']}[/] [dim]{parsed['time']}[/] [cyan]{parsed['tag']}[/cyan]: [bold red]{parsed['message']}[/]"

        return f"[{level_color}]{parsed['level']}[/] [dim]{parsed['time']}[/] [cyan]{parsed['tag']}[/cyan]: {parsed['message']}"

    def show_filter_menu(self):
        """显示过滤器配置菜单"""
        while True:  # 添加循环，避免返回主菜单
            self.console.clear()
            self.console.print(Panel("[bold cyan]🔍 Logcat 实时分析工具[/bold cyan]", style="cyan", box=box.DOUBLE))

            # 当前过滤配置
            config_table = Table(title="⚙️  当前过滤配置", box=box.ROUNDED, title_style="bold yellow")
            config_table.add_column("选项", style="yellow", width=15)
            config_table.add_column("值", style="green")

            level_desc = {"V": "详细", "D": "调试", "I": "信息", "W": "警告", "E": "错误", "F": "致命"}
            config_table.add_row("📊 日志级别", f"{self.filter_config['level']} ({level_desc.get(self.filter_config['level'], '')})")
            config_table.add_row("🏷️  TAG 过滤", self.filter_config["tag"] or "[dim]未设置[/dim]")
            config_table.add_row("🔎 关键词", self.filter_config["keyword"] or "[dim]未设置[/dim]")
            config_table.add_row("🚫 排除词", self.filter_config["exclude"] or "[dim]未设置[/dim]")

            self.console.print(config_table)
            self.console.print("\n[dim]" + "━" * self.console.width + "[/dim]")

            menu = Table.grid(padding=(0, 2))
            menu.add_row("[yellow]1[/yellow]", "设置日志级别 [dim](V-详细 / D-调试 / I-信息 / W-警告 / E-错误 / F-致命)[/dim]")
            menu.add_row("[yellow]2[/yellow]", "设置 TAG 过滤 [dim](只显示特定模块)[/dim]")
            menu.add_row("[yellow]3[/yellow]", "设置关键词过滤 [dim](搜索包含特定内容的日志)[/dim]")
            menu.add_row("[yellow]4[/yellow]", "设置排除关键词 [dim](屏蔽系统噪音)[/dim]")
            menu.add_row("[yellow]5[/yellow]", "清除所有过滤器 [dim](重置为默认)[/dim]")
            menu.add_row("[yellow]s[/yellow]", "[bold green]开始实时监控[/bold green] 🚀")
            menu.add_row("[yellow]b[/yellow]", "返回主菜单")

            self.console.print(Panel(menu, title="🎛️  过滤器配置", border_style="cyan"))

            choice = Prompt.ask("请输入").lower()

            if choice == "1":
                level = Prompt.ask("选择日志级别", choices=["V", "D", "I", "W", "E", "F"], default="V")
                self.filter_config["level"] = level
                self.console.print(f"[green]✓ 日志级别已设置为: {level}[/green]")
                time.sleep(1)

            elif choice == "2":
                tag = Prompt.ask("输入 TAG (留空取消)")
                self.filter_config["tag"] = tag
                self.console.print(f"[green]✓ TAG 过滤已设置: {tag if tag else '已清除'}[/green]")
                time.sleep(1)

            elif choice == "3":
                keyword = Prompt.ask("输入关键词 (留空取消)")
                self.filter_config["keyword"] = keyword
                self.console.print(f"[green]✓ 关键词过滤已设置: {keyword if keyword else '已清除'}[/green]")
                time.sleep(1)

            elif choice == "4":
                exclude = Prompt.ask("输入排除词 (留空取消)")
                self.filter_config["exclude"] = exclude
                self.console.print(f"[green]✓ 排除词已设置: {exclude if exclude else '已清除'}[/green]")
                time.sleep(1)

            elif choice == "5":
                self.filter_config = {"level": "V", "tag": "", "pid": "", "keyword": "", "exclude": ""}
                self.console.print("[green]✓ 过滤器已重置为默认配置[/green]")
                time.sleep(1)

            elif choice == "s":
                self.start_monitoring()

            elif choice == "b":
                return  # 返回主菜单

    def start_monitoring(self):
        """开始实时监控"""
        self.console.clear()
        self.console.print(Panel(
            "[bold cyan]📡 Logcat 实时监控中...[/bold cyan]\n[dim]按 Ctrl+C 停止监控[/dim]",
            style="green",
            box=box.DOUBLE
        ))

        # 显示当前过滤配置
        if any([self.filter_config["level"] != "V", self.filter_config["tag"],
                self.filter_config["keyword"], self.filter_config["exclude"]]):
            filter_info = []
            if self.filter_config["level"] != "V":
                filter_info.append(f"级别≥{self.filter_config['level']}")
            if self.filter_config["tag"]:
                filter_info.append(f"TAG={self.filter_config['tag']}")
            if self.filter_config["keyword"]:
                filter_info.append(f"含'{self.filter_config['keyword']}'")
            if self.filter_config["exclude"]:
                filter_info.append(f"排除'{self.filter_config['exclude']}'")

            self.console.print(f"[yellow]当前过滤: {' | '.join(filter_info)}[/yellow]\n")

        # 清除旧日志
        self.driver.run("logcat -c")

        prefix = f"adb -s {self.driver.device_id} " if self.driver.device_id else "adb "
        cmd = prefix + self._build_filter_command()

        crash_count = 0
        line_count = 0

        try:
            process = subprocess.Popen(
                cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,encoding='utf-8',       # 强制使用 UTF-8
                errors='replace'
            )

            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue

                parsed = self._parse_log_line(line)
                if parsed:
                    formatted = self._format_log_line(parsed)
                    self.console.print(formatted)

                    # 统计崩溃
                    if "CRASH" in formatted:
                        crash_count += 1

                    line_count += 1
                else:
                    # 无法解析的行直接输出
                    self.console.print(f"[dim]{line}[/dim]")

        except KeyboardInterrupt:
            process.terminate()
            self.console.print(f"\n[yellow]{'━' * self.console.width}[/yellow]")

            # 统计信息
            stats_table = Table(box=box.SIMPLE, show_header=False)
            stats_table.add_row("[green]✓ 监控已停止[/green]", "")
            stats_table.add_row("📊 捕获日志:", f"[cyan]{line_count}[/cyan] 行")
            stats_table.add_row("🚨 崩溃次数:", f"[red]{crash_count}[/red] 次" if crash_count > 0 else "[green]0[/green] 次")

            self.console.print(stats_table)
            Prompt.ask("\n[dim]按回车返回过滤器菜单...[/dim]")

# ==========================================
# 4. 数据处理引擎 - 健壮性与纠错能力
# ==========================================
class IVIMetricsEngine:
    def __init__(self, source: BaseSource, whitelist_path="whitelist.txt"):
        self.source = source
        self.whitelist = self._load_whitelist(whitelist_path)
        self.snapshot = {
            "sys": {"load": ("0.00", "0.00", "0.00"), "ram_pct": 0, "storage": "N/A"},
            "apps": []
        }

    def _load_whitelist(self, path) -> List[str]:
        """解析白名单并自动清洗干扰字符"""
        if not os.path.exists(path): return []
        try:
            with open(path, 'r', encoding='utf-8') as f:
                # 针对 等元数据进行正则清洗
                return [re.search(r'([a-zA-Z0-9._]+)$', l.strip()).group(1)
                        for l in f if re.search(r'([a-zA-Z0-9._]+)$', l.strip())]
        except Exception: return []

    def refresh(self):
        """高效数据采集序列"""
        # 1. 系统负载
        uptime = self.source.run_command("uptime", use_root=True)  # 使用root
        load = re.search(r"average:\s+([\d.]+),?\s+([\d.]+),?\s+([\d.]+)", uptime)
        if load: self.snapshot["sys"]["load"] = load.groups()
        # 2. 存储健康度 (针对视频中 0% 突跳 Bug 的监控)
        df = self.source.run_command("df -h /data", use_root=True)
        storage = re.search(r"(\d+)%", df)
        if storage: self.snapshot["sys"]["storage"] = f"{storage.group(1)}%"
        # 3. 进程监控 (使用 top 替代 dumpsys，速度提升 10 倍)
        top_raw = self.source.run_command("top -b -n 1", use_root=True)
        self._parse_top(top_raw)

    def _parse_top(self, raw_data: str):
        # 适配你发出来的 top 格式：Mem: 11382248K total, 10279672K used
        mem_match = re.search(r"Mem:\s+(\d+)K total,\s+(\d+)K used", raw_data)
        if mem_match:
            total = int(mem_match.group(1))
            used = int(mem_match.group(2))
            self.snapshot["sys"]["ram_pct"] = round((used / total) * 100, 1)

        app_list = []
        # 如果白名单为空，默认抓取 top 5 消耗最高的进程作为展示，防止界面留白
        search_list = self.whitelist if self.whitelist else []

        for pkg in search_list:
            # 兼容你的 top 输出格式
            pattern = fr"(\d+)\s+.*?\s+([\d,.]+[MGK]?)\s+.*?\s+(\d+[.]?\d*)\s+.*?\s+{re.escape(pkg)}"
            match = re.search(pattern, raw_data)
            if match:
                mem_val = self._normalize_mem(match.group(2))
                app_list.append({"name": pkg, "cpu": f"{match.group(3)}%", "mem": mem_val})

        self.snapshot["apps"] = sorted(app_list, key=lambda x: x['mem'], reverse=True)
    def _normalize_mem(self, val: str) -> float:
        """单位换算纠错 (G/M/K -> MB)"""
        try:
            val = val.replace(',', '')
            if 'G' in val: return float(val.replace('G', '')) * 1024
            if 'M' in val: return float(val.replace('M', ''))
            return float(val) / 1024
        except: return 0.0

class AdvancedSentinelUI:
    def __init__(self, engine: IVIMetricsEngine, console: Console):
        self.engine = engine
        self.console = console

    def _make_layout(self) -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="main", ratio=1),
            Layout(name="footer", size=3)
        )
        layout["main"].split_row(
            Layout(name="side", ratio=1),
            Layout(name="body", ratio=2)
        )
        return layout

    def _render_all(self, layout: Layout):
        """将渲染逻辑独立，确保首帧和循环使用同一套逻辑"""
        # Header
        layout["header"].update(Panel(
            Text.assemble(
                (" 🛰️ IVI SENTINEL PRO ", "bold white on blue"),
                (f" | DEVICE: {self.engine.source.device_id or 'Connecting...'} | ", "cyan"),
                (datetime.now().strftime("%H:%M:%S"), "yellow")
            ), border_style="blue"
        ))

        # Side: 系统概览 (增加 N/A 处理)
        sys = self.engine.snapshot["sys"]
        sys_grid = Table.grid(expand=True)
        sys_grid.add_row("🔥 [bold]CPU Load:[/]", f"[yellow]{' / '.join(sys.get('load', ['0.0','0.0','0.0']))}[/]")
        sys_grid.add_row("🧠 [bold]RAM Used:[/]", f"[bold cyan]{sys.get('ram_pct', 0)}%[/]")
        sys_grid.add_row("💾 [bold]Storage:[/]", f"[magenta]{sys.get('storage', 'N/A')}[/]")
        layout["side"].update(Panel(sys_grid, title="[bold]System Status", border_style="cyan"))

        # Body: 进程监控 (修复点：如果没有白名单数据，显示正在加载)
        app_table = Table(title="[bold green]Whitelisted Process Activity", expand=True)
        app_table.add_column("Package Name", style="white")
        app_table.add_column("CPU", justify="right", style="green")
        app_table.add_column("Memory (RES MB)", justify="right", style="magenta")

        apps = self.engine.snapshot.get("apps", [])
        if not apps:
            app_table.add_row("[dim]Waiting for data...[/]", "-", "-")
        else:
            for app in apps[:15]:
                app_table.add_row(app["name"], app["cpu"], f"{app['mem']:.1f}")

        layout["body"].update(app_table)

        # Footer
        layout["footer"].update(Panel(
            " [Q] 退出监控 | 实时刷新率: 2Hz | 权限: ROOT ",
            title="Quick Actions", border_style="dim"
        ))

    def start(self):
        layout = self._make_layout()

        # 🟢 关键修复：在进入 Live 模式前先打印提示，并执行一次同步刷新
        self.console.print("[bold yellow]🚀 正在连接设备并拉取首帧数据，请稍候...[/bold yellow]")
        self.engine.refresh()
        self._render_all(layout)

        with Live(layout, refresh_per_second=2, screen=True, console=self.console) as live:
            try:
                while True:
                    self.engine.refresh()
                    self._render_all(layout)
                    time.sleep(0.5)
            except KeyboardInterrupt:
                pass


# ==========================================
# [找回] 核心模块: 全能应用管理器 (App Manager)
# ==========================================
# ==========================================
# [修复版] 核心模块: 全能应用管理器 (交互增强)
# ==========================================
class AppManager:
    def __init__(self, driver: AdbDriver, console: Console):
        self.driver = driver
        self.console = console

    def _get_packages(self, mode="all") -> List[str]:
        """获取包名列表"""
        # mode: '3' (第三方), 's' (系统), 'all' (全部)
        flag = "-3" if mode == "3" else ("-s" if mode == "s" else "")
        s, out = self.driver.run(f"shell pm list packages {flag}")
        packages = []
        for line in out.splitlines():
            if "package:" in line:
                packages.append(line.split(":")[-1].strip())
        return sorted(packages)

    def run_menu(self):
        """交互式卸载向导"""
        while True:
            self.console.clear()
            self.console.print(Panel("[bold red]🗑️ 应用管理与卸载中心[/bold red]", style="red", box=box.HEAVY))

            # 二级菜单
            menu = Table.grid(padding=(0, 2))
            menu.add_row("[yellow]1[/yellow]", "🔍 [bold]关键词搜索卸载[/bold] (忽略大小写)")
            menu.add_row("[yellow]2[/yellow]", "📂 [bold]浏览第三方应用[/bold] (User Apps)")
            menu.add_row("[yellow]3[/yellow]", "⚠️ [bold]浏览系统应用[/bold] (System Apps)")
            menu.add_row("[yellow]b[/yellow]", "返回主菜单")

            self.console.print(Panel(menu, border_style="yellow"))
            choice = Prompt.ask("请选择浏览模式").lower()

            target_list = []
            title = ""

            if choice == "1":
                self.console.print("[dim]请输入包名关键词 (如: scene, map, launcher)[/dim]")
                keyword = Prompt.ask("🔍 搜索关键词").strip()

                # 修复：输入为空时的明确提示
                if not keyword:
                    self.console.print("[red]❌ 关键词不能为空[/red]")
                    time.sleep(1)
                    continue

                with self.console.status(f"正在搜索 '{keyword}'..."):
                    all_pkgs = self._get_packages("all")
                    # 逻辑确认：忽略大小写匹配
                    target_list = [p for p in all_pkgs if keyword.lower() in p.lower()]
                    title = f"搜索结果: '{keyword}'"

            elif choice == "2":
                with self.console.status("正在拉取第三方应用列表..."):
                    target_list = self._get_packages("3")
                    title = "所有第三方应用"

            elif choice == "3":
                with self.console.status("正在拉取系统应用列表..."):
                    target_list = self._get_packages("s")
                    title = "所有系统应用"

            elif choice == "b":
                return

            else:
                continue

            # 调用通用列表选择器
            self._show_list_and_act(target_list, title)

    def _show_list_and_act(self, packages: List[str], title: str):
        """通用列表展示与操作逻辑"""
        # 修复：如果没找到结果，暂停等待用户确认，而不是直接返回
        if not packages:
            self.console.print(Panel("[bold red]❌ 未找到匹配的应用[/bold red]", border_style="red"))
            Prompt.ask("按回车键返回...")
            return

        # 展示列表
        self.console.clear()
        table = Table(title=f"{title} (共 {len(packages)} 个)", box=box.ROUNDED, show_lines=True)
        table.add_column("ID", justify="center", style="cyan", width=4)
        table.add_column("包名 (Package Name)", style="white")

        # 分页逻辑（展示所有）
        for idx, pkg in enumerate(packages):
            table.add_row(str(idx + 1), pkg)

        self.console.print(table)
        self.console.print(f"[dim]提示: 输入 [cyan]ID[/cyan] 即可卸载，输入 [cyan]0[/cyan] 返回[/dim]")

        # 交互
        raw = Prompt.ask(f"\n[bold yellow]请输入 ID[/bold yellow]")

        if raw in ['0', 'b', '']: return

        try:
            idx = int(raw) - 1
            if 0 <= idx < len(packages):
                pkg_to_del = packages[idx]
                self._execute_uninstall(pkg_to_del)
            else:
                self.console.print("[red]ID 超出范围[/red]")
                time.sleep(1)
        except ValueError:
            self.console.print("[red]输入无效[/red]")
            time.sleep(1)

    def _execute_uninstall(self, package: str):
        self.console.print(f"\n[bold white on red] 警告 [/bold white on red] 即将卸载: [bold cyan]{package}[/bold cyan]")
        if Prompt.ask("确认执行？", choices=["y", "n"], default="n") == "y":
            with self.console.status("正在执行卸载指令..."):
                # 尝试普通卸载
                s, out = self.driver.run(f"uninstall {package}")

                # 如果失败，且包含 permission 错误，尝试 pm uninstall --user 0
                if not s:
                     s, out = self.driver.run(f"shell pm uninstall --user 0 {package}")

            if s and ("Success" in out or not out): # 部分shell命令成功无输出
                self.console.print("[bold green]✔ 卸载成功[/bold green]")
            else:
                self.console.print(f"[red]✘ 卸载失败: {out.strip()}[/red]")
                self.console.print("[dim]提示: 如果是只读系统应用，需先 Root 并 Remount 后使用 rm -rf 删除[/dim]")

            Prompt.ask("\n按回车继续...")


# ==========================================
# [新增] 核心模块: 智能提权专家 (封装版)
# ==========================================
# ==========================================
# [升级] 核心模块: 智能提权专家 (配置解耦版)
# ==========================================
class PrivilegeUnlocker:
    def __init__(self, driver: AdbDriver, console: Console, config: ConfigLoader):
        self.driver = driver
        self.console = console
        self.config = config
        # [修改点] 从配置加载器读取密码，如果配置文件里没有，则使用默认值
        self.root_pwd = self.config.get("root_password", "adayo@N51")

    def execute_unlock_sequence(self):
        """执行上帝模式提权流程"""
        self.console.clear()
        self.console.print(Panel("[bold red]☢️ 正在启动系统深度解锁协议 (上帝模式)[/bold red]", border_style="red", box=box.HEAVY))

        # 显示当前加载的密钥源 (脱敏显示)
        if len(self.root_pwd) > 4:
            masked_pwd = self.root_pwd[:2] + "****" + self.root_pwd[-2:]
        else:
            masked_pwd = "****"

        self.console.print(f"[dim]已加载认证密钥: {masked_pwd} (Source: config.json)[/dim]\n")

        # --- 阶段 1: 智能 Root (混合策略) ---
        with self.console.status("[bold cyan]正在尝试获取 Root 权限...[/bold cyan]") as status:
            # 策略 A: 尝试标准 Root (不带密码)
            self.driver.run("root")
            time.sleep(2)

            # 检查是否成功
            s, out = self.driver.run("shell id")
            if s and "uid=0" in out:
                self.console.print("[green]✔ 标准 ADB Root 成功[/green]")
            else:
                # 策略 B: 注入厂商密码 (Adayo)
                status.update(f"[yellow]标准提权失败，正在注入专用密钥...[/yellow]")
                self.driver.run(f"shell setprop service.adb.root.password {self.root_pwd}")
                self.driver.run("root")
                time.sleep(3)
                self.driver.run("wait-for-device")

                # 二次检查
                s, out = self.driver.run("shell id")
                if s and "uid=0" in out:
                    self.console.print("[green]✔ 密钥注入提权成功[/green]")
                else:
                    self.console.print("[bold red]❌ 提权失败，请检查 config.json 中的密码配置[/bold red]")
                    if Prompt.ask("是否继续后续步骤?", choices=["y", "n"], default="n") == "n":
                        return

        # --- 阶段 2: 深度解锁 (Verity & Remount) ---
        # 这里使用进度条展示复杂的解锁过程
        steps = [
            ("检查 Verity 状态...", "disable-verity"),
            ("解锁分区 (Remount)...", "remount"),
            ("强制挂载 / (RW)...", "shell mount -o rw,remount /"),
            ("强制挂载 /system (RW)...", "shell mount -o rw,remount /system"),
            ("禁用 SELinux...", "shell setenforce 0")
        ]

        verity_res = ""

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(complete_style="green"),
            TextColumn("[cyan]{task.fields[status]}"),
            console=self.console
        ) as p:
            task = p.add_task("系统解锁中...", total=len(steps), status="执行")

            for desc, cmd in steps:
                p.update(task, description=desc)
                s, out = self.driver.run(cmd)

                # 捕获 verity 输出用于判断重启
                if "disable-verity" in cmd: verity_res = out

                status_text = "[OK]" if s and "denied" not in out.lower() else "[SKIP]"
                p.update(task, status=status_text)
                time.sleep(0.5)
                p.advance(task)

        # --- 阶段 3: 结果判定与引导 ---
        # 很多车机 disable-verity 后需要重启
        if "reboot" in verity_res.lower() and "already" not in verity_res.lower():
            self.console.print(Panel("[bold yellow]⚠ 检测到 Verity 状态变更[/bold yellow]\n系统要求重启以生效解锁。", border_style="yellow"))
            if Prompt.ask("是否现在自动重启车机?", choices=["y", "n"], default="y") == "y":
                self.driver.run("reboot")
                self.console.print("[green]✔ 重启指令已发送，请等待车机重启后再次运行工具。[/green]")
                return

        # 最终验证
        s, uid = self.driver.run("shell id")
        s, mount_info = self.driver.run("shell mount")

        # 检查 /system 是否为 rw
        is_rw = any(" / " in line and "rw" in line for line in mount_info.splitlines())

        if "uid=0" in uid:
            status_msg = f"UID: 0 (Root) | Filesystem: {'RW (可写)' if is_rw else 'RO (只读)'}"
            self.console.print(Panel(f"[bold green]✅ 上帝模式已激活[/bold green]\n{status_msg}", border_style="green"))

        Prompt.ask("\n按回车返回菜单...")


# ==========================================
# [修复] 核心模块: 专业屏幕录制 (防Ctrl+C崩溃版)
# ==========================================
class ScreenRecorder:
    def __init__(self, driver: AdbDriver, console: Console):
        self.driver = driver
        self.console = console
        self.save_dir = os.path.join(os.getcwd(), "screen_records")
        if not os.path.exists(self.save_dir): os.makedirs(self.save_dir)
        self.remote_path = "/sdcard/screen_record.mp4"
        self.is_recording = False
        self.start_time = None

    def run_menu(self):
        import platform
        while True:
            self.console.clear()
            self.console.print(Panel("[bold magenta]🎥 专业屏幕录制工具[/bold magenta]", style="magenta"))
            menu = Table.grid(padding=(0, 2))
            menu.add_row("[yellow]1[/yellow]", "🔴 [bold]开始录制[/bold] (默认 180s 或 手动停止)")
            menu.add_row("[yellow]2[/yellow]", "⚙️ [bold]高级录制[/bold] (自定义比特率/尺寸)")
            menu.add_row("[yellow]3[/yellow]", "📂 打开视频文件夹")
            menu.add_row("[yellow]b[/yellow]", "返回")
            self.console.print(Panel(menu, border_style="yellow"))

            c = Prompt.ask("选择").lower()
            if c == '1': self.start_recording()
            elif c == '2': self.start_recording(advanced=True)
            elif c == '3':
                if platform.system() == "Windows": os.startfile(self.save_dir)
            elif c == 'b': return

    def start_recording(self, advanced=False):
        import platform

        # 1. 参数配置
        bit_rate = 12000000
        size = ""
        if advanced:
            br = Prompt.ask("比特率 (Mbps)", default="12")
            bit_rate = int(br) * 1000000
            sz = Prompt.ask("分辨率 (如 1280x720)", default="")
            if sz: size = f"--size {sz}"

        # 2. 启动进程
        cmd = f"adb -s {self.driver.device_id} shell screenrecord --bit-rate {bit_rate} {size} {self.remote_path}"

        try:
            startupinfo = None
            if platform.system() == "Windows":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, startupinfo=startupinfo)
            self.is_recording = True
            self.start_time = datetime.now()

            # 3. 录制界面
            self.console.clear()
            self.console.print(Panel("[bold red]🔴 正在录制...[/bold red]\n[yellow]按 Enter 或 Ctrl+C 停止并保存[/yellow]", border_style="red"))

            stop_event = threading.Event()

            def _timer():
                with Live(console=self.console, refresh_per_second=1) as live:
                    while not stop_event.is_set() and proc.poll() is None:
                        dur = str(datetime.now() - self.start_time).split('.')[0]
                        live.update(Panel(f"[bold red]● REC[/bold red]  {dur}\n目标: {self.remote_path}", style="red"))
                        time.sleep(0.5)

            t = threading.Thread(target=_timer, daemon=True)
            t.start()

            # --- 关键修复：捕获 Ctrl+C ---
            try:
                input() # 等待用户按回车
            except KeyboardInterrupt:
                # 捕获到 Ctrl+C 后，不抛出异常，而是打印提示并继续向下执行“封包逻辑”
                self.console.print("\n[yellow]检测到停止信号，正在处理视频...[/yellow]")
            # ---------------------------

            stop_event.set()

            # 4. 优雅封包 (发送 SIGINT)
            self.console.print("[cyan]正在封包 (请勿强制关闭)...[/cyan]")
            self.driver.run("shell pkill -2 screenrecord")

            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

            self.is_recording = False
            time.sleep(2) # 给足时间让车机写入 MP4 尾部信息

            # 5. 拉取文件
            ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            local_file = os.path.join(self.save_dir, f"video_{self.driver.device_id}_{ts}.mp4")

            self.console.print("[cyan]正在拉取视频...[/cyan]")
            s, o = self.driver.run(f"pull {self.remote_path} \"{local_file}\"")
            self.driver.run(f"shell rm {self.remote_path}")

            if s:
                self.console.print(f"[bold green]✅ 视频已保存: {os.path.basename(local_file)}[/bold green]")
                if platform.system() == "Windows": os.startfile(local_file)
            else:
                self.console.print(f"[red]❌ 拉取失败: {o}[/red]")

            Prompt.ask("\n按回车返回...")

        except Exception as e:
            self.console.print(f"[red]录制异常: {e}[/red]")
            if self.is_recording:
                self.driver.run("shell pkill -2 screenrecord")
            Prompt.ask("按回车返回")


# ==========================================
# 6. 新模块: 专业截屏工具 (ScreenshotManager) - 测试工程师专用
# ==========================================
# ==========================================
# 6. [修复] 新模块: 专业截屏工具 (修复 platform 报错)
# ==========================================
class ScreenshotManager:
    """专业截屏工具：支持单次/连续/定时截屏、水印添加、区域裁剪"""
    def __init__(self, driver: AdbDriver, console: Console):
        self.driver = driver
        self.console = console
        self.save_dir = os.path.join(os.getcwd(), "screenshots")
        if not os.path.exists(self.save_dir):
            os.makedirs(self.save_dir)
        self.remote_path = "/sdcard/screenshot.png"

    def _capture_screenshot(self) -> bool:
        success, output = self.driver.run(f"shell screencap -p {self.remote_path}")
        if not success:
            self.console.print(f"[red]✘ 截屏失败: {output}[/red]")
            return False
        return True

    def _pull_screenshot(self, local_path: str) -> bool:
        success, output = self.driver.run(f"pull {self.remote_path} \"{local_path}\"")
        if not success:
            self.console.print(f"[red]✘ 拉取失败: {output}[/red]")
            return False
        self.driver.run(f"shell rm {self.remote_path}")
        return True

    def _add_watermark(self, image_path: str, text: str):
        """添加水印"""
        try:
            from PIL import Image, ImageDraw, ImageFont

            img = Image.open(image_path)
            draw = ImageDraw.Draw(img)
            try:
                font = ImageFont.truetype("arial.ttf", 36)
            except:
                font = ImageFont.load_default()

            draw.text((20, 20), text, fill=(255, 0, 0), font=font)
            img.save(image_path)
        except ImportError:
            pass
        except Exception as e:
            self.console.print(f"[yellow]⚠ 水印添加失败: {e} (跳过)[/yellow]")

    def _crop_region(self, image_path: str, region: Tuple[int, int, int, int]):
        """区域裁剪"""
        try:
            from PIL import Image
            img = Image.open(image_path)
            cropped = img.crop(region)
            cropped.save(image_path)
        except Exception as e:
            self.console.print(f"[yellow]⚠ 裁剪失败: {e} (跳过)[/yellow]")

    def _process_image(self, file_path: str):
        """处理图片的交互逻辑"""
        # 1. 强制检查依赖
        try:
            from PIL import Image, ImageDraw, ImageFont
            import platform # <--- 关键修复：在此处显式导入 platform
        except ImportError:
            self.console.print(Panel("[bold red]❌ 功能不可用[/bold red]\n检测到 Python 环境未安装图像库\n请执行: [green]pip install pillow[/green]", border_style="red"))
            return

        try:
            img = Image.open(file_path)

            # 2. 水印流程
            self.console.print(f"\n[cyan]当前处理: {os.path.basename(file_path)} ({img.width}x{img.height})[/cyan]")
            watermark = Prompt.ask("🔹 输入水印文字 [dim](回车跳过)[/dim]").strip()
            if watermark:
                draw = ImageDraw.Draw(img)
                try:
                    font = ImageFont.truetype("arial.ttf", 40)
                except:
                    font = ImageFont.load_default()
                draw.text((30, 30), watermark, fill=(255, 0, 0), font=font)
                self.console.print("[green]✔ 水印已添加[/green]")

            # 3. 裁剪流程
            crop_input = Prompt.ask("🔹 输入裁剪区域 [dim](格式: left,top,right,bottom / 回车跳过)[/dim]").strip()
            if crop_input:
                try:
                    coords = tuple(map(int, crop_input.split(',')))
                    if len(coords) == 4:
                        img = img.crop(coords)
                        self.console.print("[green]✔ 图片已裁剪[/green]")
                    else:
                        self.console.print("[red]格式错误: 需要4个数字[/red]")
                except Exception as e:
                    self.console.print(f"[red]裁剪出错: {e}[/red]")

            # 4. 保存并打开
            img.save(file_path)
            self.console.print(f"[bold green]✨ 处理完成: {file_path}[/bold green]")

            if platform.system() == "Windows":
                os.startfile(file_path)

        except Exception as e:
            self.console.print(f"[red]图片处理异常: {e}[/red]")

    def single_screenshot(self):
        """单次截屏"""
        # 获取当前时间对象
        now = datetime.now()

        # 1. 文件名时间戳 (保持原样，Windows文件名不支持冒号)
        filename_ts = now.strftime("%Y%m%d_%H%M%S")

        # 2. [修改点] 水印时间戳 (修改为人类易读格式: 年-月-日 时:分:秒)
        readable_ts = now.strftime("%Y-%m-%d %H:%M:%S")

        local_path = os.path.join(self.save_dir, f"screenshot_{self.driver.device_id}_{filename_ts}.png")

        with self.console.status("[green]正在截屏..."):
            if self._capture_screenshot() and self._pull_screenshot(local_path):
                # [修改点] 使用易读格式的时间戳生成水印
                watermark_text = f"Device: {self.driver.device_id} | {readable_ts}"
                self._add_watermark(local_path, watermark_text)

                self.console.print(f"[green]✔ 已保存: {os.path.basename(local_path)}[/green]")
                return local_path
        return None

    def continuous_screenshots(self, count: int, interval: float):
        with Progress(SpinnerColumn(), BarColumn(), TextColumn("{task.description}"), console=self.console) as p:
            task = p.add_task("[cyan]连拍中...", total=count)
            for i in range(count):
                self.single_screenshot()
                p.advance(task)
                if i < count - 1: time.sleep(interval)
        self.console.print("[green]✔ 连拍完成[/green]")

    def timed_screenshot(self, duration: float):
        start = time.time()
        count = 0
        with Progress(SpinnerColumn(), BarColumn(), TextColumn("{task.description}"), console=self.console) as p:
            task = p.add_task("[cyan]定时截屏中...", total=duration)
            while time.time() - start < duration:
                self.single_screenshot()
                count += 1
                p.update(task, completed=time.time() - start)
                time.sleep(1)
        self.console.print(f"[green]✔ 定时结束，共 {count} 张[/green]")

    def show_menu(self):
        while True:
            self.console.clear()
            self.console.print(Panel("[bold cyan]📸 专业截屏工具[/bold cyan]", style="cyan"))

            menu = Table.grid(padding=(0, 2))
            menu.add_row("[yellow]1[/yellow]", "⚡ 单次截屏")
            menu.add_row("[yellow]2[/yellow]", "🎞️ 连续截屏 [dim](连拍)[/dim]")
            menu.add_row("[yellow]3[/yellow]", "⏱️ 定时截屏 [dim](持续时长)[/dim]")
            menu.add_row("[yellow]4[/yellow]", "🎨 [bold]自定义处理[/bold] [dim](对最新截图添加水印/裁剪)[/dim]")
            menu.add_row("[yellow]b[/yellow]", "返回")
            self.console.print(Panel(menu, title="配置", border_style="cyan"))

            c = Prompt.ask("输入").lower()
            if c == '1':
                path = self.single_screenshot()
                if path and Prompt.ask("是否立即编辑?", choices=['y','n'], default='n') == 'y':
                    self._process_image(path)
            elif c == '2':
                cnt = int(Prompt.ask("张数", default="5"))
                intv = float(Prompt.ask("间隔(秒)", default="1.0"))
                self.continuous_screenshots(cnt, intv)
                Prompt.ask("按回车继续")
            elif c == '3':
                sec = int(Prompt.ask("持续秒数", default="10"))
                self.timed_screenshot(sec)
                Prompt.ask("按回车继续")
            elif c == '4':
                files = [os.path.join(self.save_dir, f) for f in os.listdir(self.save_dir) if f.endswith('.png')]
                if not files:
                    self.console.print("[yellow]⚠ 文件夹为空[/yellow]")
                    time.sleep(1)
                else:
                    latest = max(files, key=os.path.getmtime)
                    self._process_image(latest)
                    Prompt.ask("按回车继续")
            elif c == 'b': return



# ==========================================
# [升级] 核心模块: 旗舰级图片工厂 (Image Factory Ultimate)
# ==========================================
class ImageConverter:
    """旗舰级图片处理工厂：全格式支持、PDF合并、高级编辑"""

    # 支持的导出格式映射
    FORMAT_MAP = {
        "1": ("JPG", "jpeg"),
        "2": ("PNG", "png"),
        "3": ("WEBP", "webp"),
        "4": ("BMP", "bmp"),
        "5": ("ICO", "ico"),
        "6": ("PDF", "pdf"),
        "7": ("TIFF", "tiff"),
        "8": ("PPM", "ppm") # 工业常用
    }

    def __init__(self, console: Console):
        self.console = console
        self.output_dir = os.path.join(os.getcwd(), "image_factory_output")
        if not os.path.exists(self.output_dir): os.makedirs(self.output_dir)

    def _check_dependency(self):
        try:
            from PIL import Image
            return True
        except ImportError:
            self.console.print(Panel("[bold red]❌ 核心组件缺失[/bold red]\n请执行: pip install pillow", border_style="red"))
            return False

    def run_menu(self):
        import platform
        if not self._check_dependency(): return

        while True:
            self.console.clear()
            self.console.print(Panel("[bold magenta]🎨 旗舰级图片工厂 (Image Factory Ultimate)[/bold magenta]", style="magenta", box=box.HEAVY))

            grid = Table.grid(padding=(0, 2))
            grid.add_column("Icon", justify="center")
            grid.add_column("Option", style="bold white")
            grid.add_column("Desc", style="dim")

            grid.add_row("🔄", "[1] 全能格式转换", "支持 JPG/PNG/WEBP/BMP/TIFF/ICO 等互转")
            grid.add_row("📉", "[2] 智能压缩瘦身", "自定义质量/尺寸缩放，批量减容")
            grid.add_row("📑", "[3] 合并为 PDF", "将多张图片按序合并为一个 PDF 文档")
            grid.add_row("🚀", "[4] WebP 极速转换", "Android 开发专用，一键最优配置")
            grid.add_row("🛠️", "[5] 高级图像处理", "旋转/翻转/灰度化/去EXIF信息")
            grid.add_row("📂", "[6] 打开输出目录", "")
            grid.add_row("🔙", "[b] 返回主菜单", "")

            self.console.print(Panel(grid, border_style="yellow"))

            c = Prompt.ask("请选择功能模块").lower()
            if c == '1': self._batch_processor(mode="convert")
            elif c == '2': self._batch_processor(mode="compress")
            elif c == '3': self._merge_to_pdf()
            elif c == '4': self._batch_processor(mode="webp_auto")
            elif c == '5': self._batch_processor(mode="edit")
            elif c == '6':
                if platform.system() == "Windows": os.startfile(self.output_dir)
            elif c == 'b': return

    def _get_files(self, path):
        # 扩展支持的输入格式
        valid_ext = {'.png', '.jpg', '.jpeg', '.bmp', '.webp', '.tiff', '.tif', '.ico', '.ppm'}
        if os.path.isfile(path): return [path]
        if os.path.isdir(path):
            # 按文件名排序，确保合并PDF时顺序正确
            files = [os.path.join(path, f) for f in os.listdir(path) if os.path.splitext(f)[1].lower() in valid_ext]
            return sorted(files)
        return []

    def _merge_to_pdf(self):
        """特有功能：合并PDF"""
        from PIL import Image
        import platform

        path = Prompt.ask("\n📂 拖入文件夹 (包含需合并的图片)").strip('"')
        files = self._get_files(path)
        if not files:
            self.console.print("[red]❌ 未找到图片文件[/red]"); time.sleep(1); return

        pdf_name = Prompt.ask("📄 输出 PDF 文件名", default="Merged_Images")
        if not pdf_name.endswith(".pdf"): pdf_name += ".pdf"

        output_path = os.path.join(self.output_dir, pdf_name)

        try:
            with self.console.status("[bold cyan]正在合成 PDF...[/bold cyan]"):
                image_list = []
                # 第一张图片作为基准
                first_img = Image.open(files[0]).convert("RGB")

                # 处理后续图片
                for f in files[1:]:
                    img = Image.open(f).convert("RGB")
                    image_list.append(img)

                first_img.save(output_path, save_all=True, append_images=image_list)

            self.console.print(f"[bold green]✅ PDF 生成成功: {output_path}[/bold green]")
            if platform.system() == "Windows": os.startfile(output_path)
        except Exception as e:
            self.console.print(f"[red]合成失败: {e}[/red]")

        Prompt.ask("按回车返回")

    def _batch_processor(self, mode="convert"):
        from PIL import Image, ImageOps
        import platform

        path = Prompt.ask("\n📂 拖入文件或文件夹").strip('"')
        files = self._get_files(path)
        if not files:
            self.console.print("[red]❌ 无效输入[/red]"); time.sleep(1); return

        # === 参数配置 ===
        params = {"fmt": "jpg", "quality": 90, "scale": 1.0, "ops": []}

        if mode == "convert":
            # 动态生成格式菜单
            fmt_menu = " / ".join([f"[{k}]{v[0]}" for k, v in self.FORMAT_MAP.items()])
            self.console.print(f"[cyan]可用格式: {fmt_menu}[/cyan]")
            choice = Prompt.ask("选择目标格式", choices=list(self.FORMAT_MAP.keys()), default="1")
            params["fmt"] = self.FORMAT_MAP[choice][1]

        elif mode == "compress":
            params["quality"] = int(Prompt.ask("压缩质量 (1-100)", default="75"))
            params["scale"] = float(Prompt.ask("缩放比例 (0.1-1.0)", default="0.8"))

        elif mode == "webp_auto":
            params["fmt"] = "webp"
            params["quality"] = 75
            params["method"] = 6 # 极致压缩

        elif mode == "edit":
            self.console.print("[dim]选择处理操作 (支持多选，如 1,3):[/dim]")
            self.console.print("1. [bold]灰度化[/bold] (Grayscale)\n2. [bold]旋转 90°[/bold]\n3. [bold]去除 EXIF[/bold]\n4. [bold]自动对比度[/bold]")
            ops_sel = Prompt.ask("输入操作序号", default="").split(',')
            if '1' in ops_sel: params["ops"].append("gray")
            if '2' in ops_sel: params["ops"].append("rotate90")
            if '3' in ops_sel: params["ops"].append("no_exif")
            if '4' in ops_sel: params["ops"].append("autocontrast")
            params["fmt"] = Prompt.ask("输出格式", choices=["jpg", "png"], default="jpg")

        # === 执行处理 ===
        batch_name = f"Batch_{datetime.now().strftime('%H%M%S')}_{mode}"
        save_dir = os.path.join(self.output_dir, batch_name)
        os.makedirs(save_dir, exist_ok=True)

        success, fail = 0, 0

        with Progress(SpinnerColumn(), BarColumn(), TextColumn("{task.description}"), console=self.console) as p:
            task = p.add_task("Processing...", total=len(files))

            for fpath in files:
                fname = os.path.basename(fpath)
                p.update(task, description=f"处理: {fname}")

                try:
                    with Image.open(fpath) as img:
                        # 1. 基础转换 RGB (处理透明通道问题)
                        target_ext = params["fmt"]
                        if target_ext in ['jpg', 'jpeg', 'bmp'] and img.mode in ('RGBA', 'LA'):
                            bg = Image.new('RGB', img.size, (255, 255, 255))
                            bg.paste(img, mask=img.split()[3])
                            img = bg
                        elif target_ext != 'ico': # ICO 保持原样或特定处理
                            if img.mode == 'P': img = img.convert('RGBA')

                        # 2. 高级编辑操作
                        for op in params["ops"]:
                            if op == "gray": img = ImageOps.grayscale(img)
                            if op == "rotate90": img = img.rotate(-90, expand=True)
                            if op == "autocontrast": img = ImageOps.autocontrast(img.convert("RGB"))
                            if op == "no_exif":
                                data = list(img.getdata())
                                img_without_exif = Image.new(img.mode, img.size)
                                img_without_exif.putdata(data)
                                img = img_without_exif

                        # 3. 尺寸缩放
                        if params["scale"] < 1.0:
                            w, h = img.size
                            img = img.resize((int(w*params["scale"]), int(h*params["scale"])), Image.LANCZOS)

                        # 4. 保存参数构建
                        save_args = {}
                        if target_ext in ['jpg', 'jpeg']:
                            save_args['quality'] = params['quality']
                            save_args['optimize'] = True
                        if target_ext == 'webp':
                            save_args['quality'] = params['quality']
                            if 'method' in params: save_args['method'] = params['method']
                        if target_ext == 'ico':
                            save_args['sizes'] = [(256, 256)] # 默认存大图标

                        out_name = os.path.splitext(fname)[0] + f".{target_ext}"
                        img.save(os.path.join(save_dir, out_name), **save_args)
                        success += 1

                except Exception as e:
                    fail += 1
                    # p.console.print(f"[red]Err: {fname} - {e}[/red]")

                p.advance(task)

        # 结果反馈
        self.console.print(Panel(
            f"[bold green]✔ 完成: {success}[/bold green]  [bold red]✘ 失败: {fail}[/bold red]\n"
            f"📂 路径: {save_dir}",
            title="任务报告", border_style="green"
        ))

        if platform.system() == "Windows": os.startfile(save_dir)
        Prompt.ask("按回车返回")

# ==========================================
# [新增] 核心模块: Monkey 压力测试专家
# ==========================================
# ==========================================
# [升级] 核心模块: Monkey 压力测试专家 (带日志持久化)
# ==========================================
class MonkeyTester:
    def __init__(self, driver: AdbDriver, console: Console):
        self.driver = driver
        self.console = console
        self.config = {
            "count": 10000,
            "throttle": 300,
            "seed": None,
            "packages": []
        }
        self.is_running = False
        # 初始化日志保存目录
        self.save_dir = os.path.join(os.getcwd(), "monkey_logs")
        if not os.path.exists(self.save_dir):
            os.makedirs(self.save_dir)

    def _get_packages(self, flag="-3"):
        s, out = self.driver.run(f"shell pm list packages {flag}")
        return [l.split(":")[-1].strip() for l in out.splitlines() if "package:" in l]

    def _kill_monkey(self):
        self.driver.run("shell killall com.android.commands.monkey")
        self.driver.run("shell pkill -f monkey")

    def config_menu(self):
        while True:
            self.console.clear()
            # 状态栏显示优化
            pkg_count = len(self.config['packages'])
            pkg_info = f"[green]{pkg_count} 个应用[/green]" if pkg_count > 0 else "[red bold]全系统 (无限制)[/red bold]"
            seed_info = self.config['seed'] if self.config['seed'] else "随机 (Random)"

            grid = Table.grid(expand=True)
            grid.add_column(style="cyan", justify="right")
            grid.add_column(style="white")
            grid.add_row("目标范围:", pkg_info)
            grid.add_row("事件总数:", f"[bold]{self.config['count']}[/bold]")
            grid.add_row("事件间隔:", f"{self.config['throttle']} ms")
            grid.add_row("Seed种子:", str(seed_info))

            self.console.print(Panel(grid, title="[bold magenta]🐒 Monkey 压测配置台[/bold magenta]", border_style="magenta"))

            menu = Table.grid(padding=(0, 2))
            menu.add_row("[yellow]1[/yellow]", "🎯 [bold]选择/添加目标应用[/bold] (Search)")
            menu.add_row("[yellow]2[/yellow]", "🔢 [bold]设置参数[/bold] (Count/Throttle)")
            menu.add_row("[yellow]3[/yellow]", "🌱 [bold]设置种子[/bold] (Seed)")
            menu.add_row("[yellow]4[/yellow]", "🧹 [bold]重置为全系统测试[/bold] (Clear All)")
            menu.add_row("[yellow]5[/yellow]", "👀 [bold cyan]查看已选应用列表[/bold cyan] (Review)") # <--- 新增
            menu.add_row("[yellow]s[/yellow]", "🚀 [bold green]开始压测[/bold green] (Start)")
            menu.add_row("[yellow]b[/yellow]", "返回")
            self.console.print(Panel(menu, border_style="yellow"))

            c = Prompt.ask("选项").lower()
            if c == '1': self._select_packages()
            elif c == '2':
                try:
                    cnt = Prompt.ask("事件总数", default=str(self.config['count']))
                    self.config['count'] = int(cnt)
                    thr = Prompt.ask("间隔(ms)", default=str(self.config['throttle']))
                    self.config['throttle'] = int(thr)
                except: pass
            elif c == '3':
                val = Prompt.ask("Seed值 (回车随机)", default="")
                self.config['seed'] = val if val else None
            elif c == '4':
                self.config['packages'] = []
                self.console.print("[green]已重置，当前为全系统压测模式[/green]")
                time.sleep(1)
            elif c == '5': # <--- 新增响应
                self._view_selected_packages()
            elif c == 's': self.run_test()
            elif c == 'b': return


    def _view_selected_packages(self):
        """查看当前待测应用清单"""
        self.console.clear()
        pkgs = self.config['packages']

        if not pkgs:
            self.console.print(Panel(
                "[bold red]☢️ 当前模式：全系统压测[/bold red]\n\n"
                "[dim]未指定任何包名，Monkey 将在整个系统中随机乱点。\n"
                "这可能会测试到 Launcher, Settings, SystemUI 等系统组件。[/dim]",
                title="已选列表", border_style="red"
            ))
        else:
            t = Table(
                title=f"📋 已选目标应用清单 ({len(pkgs)} 个)",
                box=box.ROUNDED,
                expand=True,
                border_style="cyan"
            )
            t.add_column("ID", justify="center", width=4, style="dim")
            t.add_column("Package Name", style="bold white")

            for i, p in enumerate(pkgs):
                t.add_row(str(i+1), p)

            self.console.print(t)

        Prompt.ask("\n按回车返回配置菜单...")

    def _select_packages(self):
        """应用选择逻辑 (UI 美化版)"""
        with self.console.status("[bold cyan]正在拉取设备全量应用列表...[/bold cyan]"):
            all_pkgs = self._get_packages("")

        # 使用 Panel 包裹统计信息
        self.console.print(Align.center(f"[dim]设备共安装 {len(all_pkgs)} 个应用[/dim]"))

        while True:
            self.console.print("\n[bold cyan]── 🔍 应用搜索 ──[/bold cyan]")
            keyword = Prompt.ask("请输入关键词 (如: set, navi) [dim]输入 0 返回[/dim]").strip()

            if keyword == '0': return
            if not keyword: continue

            filtered = [p for p in all_pkgs if keyword.lower() in p.lower()]
            if not filtered:
                self.console.print(Panel(f"[yellow]未找到包含 '{keyword}' 的应用[/yellow]", border_style="yellow", expand=False))
                continue

            self.console.clear()

            # --- [UI 升级] 专业表格渲染 ---
            t = Table(
                title=f"搜索结果: [bold green]'{keyword}'[/bold green] (命中 {len(filtered)} 个)",
                box=box.ROUNDED,           # 圆角边框
                header_style="bold yellow", # 表头高亮
                border_style="blue",       # 边框颜色
                expand=True,               # 撑满宽度
                highlight=True             # 自动高亮数字
            )
            t.add_column("ID", justify="center", style="bold cyan", width=6, no_wrap=True)
            t.add_column("Package Name", style="white")

            for i, p in enumerate(filtered[:50]):
                t.add_row(str(i+1), p)

            self.console.print(t)
            # ---------------------------

            if len(filtered) > 50:
                self.console.print(Align.center(f"[dim]... 还有 {len(filtered)-50} 个结果未显示，建议优化关键词 ...[/dim]"))

            # --- [UI 升级] 操作面板 ---
            tips = (
                "[bold white]操作指南[/bold white]\n"
                "• 输入 [cyan]ID[/cyan] 单选 (如: 1)\n"
                "• 输入 [cyan]1,2[/cyan] 多选\n"
                "• 输入 [cyan]all[/cyan] 全选\n"
                "• 输入 [cyan]r[/cyan] 重新搜索"
            )
            self.console.print(Panel(tips, border_style="dim", expand=False))

            sel = Prompt.ask("[bold yellow]请做出选择[/bold yellow]", default="").lower()

            if sel == 'r': continue
            elif sel == 'all':
                self.config['packages'] = filtered
                self.console.print(f"[green]✔ 已选中 {len(filtered)} 个应用[/green]"); time.sleep(1); return
            else:
                try:
                    idxs = [int(x.strip())-1 for x in sel.split(',') if x.strip().isdigit()]
                    selected = [filtered[i] for i in idxs if 0 <= i < len(filtered)]
                    if selected:
                        self.config['packages'] = selected
                        self.console.print(f"[green]✔ 已选中 {len(selected)} 个应用[/green]"); time.sleep(1); return
                except: pass

    def run_test(self):
        """执行压测引擎 (带日志保存 & 工业级参数)"""
        import platform # 强制导入防报错
        self.console.clear()

        # 1. 准备日志文件
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_filename = f"monkey_{self.driver.device_id}_{ts}.log"
        log_path = os.path.join(self.save_dir, log_filename)

        # 2. 构建命令
        cmd = "monkey"
        for p in self.config['packages']: cmd += f" -p {p}"
        cmd += f" --throttle {self.config['throttle']}"
        if self.config['seed']: cmd += f" -s {self.config['seed']}"

        # [核心优化] 工业级事件配比
        cmd += " --pct-touch 40 --pct-motion 25 --pct-appswitch 15 --pct-syskeys 5 --pct-anyevent 5"
        cmd += " --pct-trackball 0 --pct-nav 0 --pct-majornav 0"
        cmd += " --ignore-crashes --ignore-timeouts --ignore-security-exceptions --monitor-native-crashes"
        cmd += f" -v -v {self.config['count']}"

        full_cmd = f"adb -s {self.driver.device_id} shell {cmd}"

        self.is_running = True
        stats = {"crash": 0, "anr": 0, "progress": 0}

        startupinfo = None
        if platform.system() == "Windows":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        try:
            self.console.print(Panel(f"[dim]{cmd}[/dim]", title="正在执行工业级指令", border_style="dim"))
            self.console.print(f"[cyan]📝 完整日志将保存至: {log_filename}[/cyan]")

            # 使用 Popen 实时获取流
            proc = subprocess.Popen(full_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', startupinfo=startupinfo)

            # 打开文件，准备双工写入
            with open(log_path, 'w', encoding='utf-8') as log_file:
                # 写入头部元数据
                log_file.write(f"--- Monkey Test Start: {ts} ---\n")
                log_file.write(f"Packages: {self.config['packages']}\n")
                log_file.write(f"Command: {full_cmd}\n")
                log_file.write("-" * 50 + "\n")

                with Live(refresh_per_second=4) as live:
                    while self.is_running and proc.poll() is None:
                        line = proc.stdout.readline()
                        if not line: break

                        # [关键] 实时写入文件
                        log_file.write(line)

                        line = line.strip()
                        if not line: continue

                        # 实时分析
                        if "// CRASH" in line or "FATAL" in line: stats["crash"] += 1
                        if "// NOT RESPONDING" in line or "ANR" in line: stats["anr"] += 1
                        if "Events injected:" in line:
                            try: stats["progress"] = int(line.split()[-1])
                            except: pass

                        # 进度条模拟
                        pct = 0
                        if self.config['count'] > 0:
                            pct = min(100, int((stats['progress'] / self.config['count']) * 100))
                        bar = "█" * (pct // 5) + "░" * (20 - pct // 5)

                        # 构建实时面板
                        grid = Table.grid(expand=True)
                        grid.add_column(ratio=1)
                        grid.add_row(f"[bold green]🚀 Monkey 正在执行...[/bold green]")
                        grid.add_row(f"进度: [{bar}] {pct}% ({stats['progress']}/{self.config['count']})")
                        grid.add_row(f"状态: [bold red]Crash: {stats['crash']}[/bold red] | [bold yellow]ANR: {stats['anr']}[/bold yellow]")

                        # 显示最新日志 (截断)
                        display_line = line[:100] + "..." if len(line) > 100 else line
                        border_color = "cyan"
                        if "CRASH" in line: border_color = "red"
                        elif "ANR" in line: border_color = "yellow"

                        live.update(Panel(grid, subtitle=f"[dim]{display_line}[/dim]", border_style=border_color))

        except KeyboardInterrupt:
            self.console.print("\n[yellow]检测到用户停止，正在查杀 Monkey 进程...[/yellow]")
        finally:
            self.is_running = False
            self._kill_monkey()

            # 结果汇总
            self.console.print(f"\n[green]✔ 压测结束。[/green]")
            self.console.print(f"📊 统计: Crash: [red]{stats['crash']}[/red] | ANR: [yellow]{stats['anr']}[/yellow]")
            self.console.print(f"📂 日志已保存: [underline cyan]{log_path}[/underline cyan]")

            # Windows下自动打开文件夹
            if platform.system() == "Windows":
                try: os.startfile(self.save_dir)
                except: pass

            Prompt.ask("按回车返回")



# ==========================================
# [新增] 核心模块: 性能测速中心 (Performance Master)
# ==========================================
class PerformanceMaster:
    """工业级应用启动速度分析引擎"""
    def __init__(self, driver: AdbDriver, console: Console):
        self.driver = driver
        self.console = console

    def _get_packages(self, flag="-3"):
        """复用包名获取逻辑"""
        s, out = self.driver.run(f"shell pm list packages {flag}")
        return [l.split(":")[-1].strip() for l in out.splitlines() if "package:" in l]

    def _resolve_main_activity(self, package_name: str) -> Optional[str]:
        """
        [核心技术] 智能嗅探应用的启动 Activity
        无需用户手动输入 Component Name
        """
        with self.console.status(f"[cyan]正在解析 {package_name} 启动入口...[/cyan]"):
            # 方法 1: 使用 cmd package resolve-activity (Android 7+)
            cmd = f"shell cmd package resolve-activity --brief {package_name} | tail -n 1"
            s, out = self.driver.run(cmd)
            if s and "/" in out and "No activity found" not in out:
                return out.strip()

            # 方法 2: 降级方案，尝试通过 dumpsys (较慢但通用)
            cmd_dump = f"shell dumpsys package {package_name}"
            s, out = self.driver.run(cmd_dump)
            # 匹配: android.intent.action.MAIN: ... com.example/.MainActivity
            match = re.search(r'android.intent.action.MAIN:[\s\S]*?([a-zA-Z0-9._]+/[a-zA-Z0-9._]+)', out)
            if match:
                return match.group(1)

        return None

    def _measure_single_launch(self, component: str, mode: str) -> int:
        """执行单次启动测试 (增强稳定性版)"""
        pkg = component.split('/')[0]

        # --- 1. 环境重置 (关键修复: 加大等待时间，确保状态归零) ---
        if mode == "cold":
            # [冷启动策略]
            # 强制停止应用
            self.driver.run(f"shell am force-stop {pkg}")
            # 车机IO较慢，给足 3秒 让系统回收资源，否则可能杀不干净
            time.sleep(3)
        else:
            # [热启动策略]
            # 连续发送两次 Home 键，防止第一次被吃掉或响应不及时
            self.driver.run("shell input keyevent 3")
            time.sleep(0.5)
            self.driver.run("shell input keyevent 3")
            # 等待 2秒 让退后台动画完全执行完毕
            time.sleep(2)

        # --- 2. 执行启动并计时 ---
        # -W 等待启动完成
        # -S 启动前再次强杀 (仅冷启动用，双重保险)
        adb_cmd = f"shell am start -W -n {component}"
        if mode == "cold": adb_cmd += " -S"

        # 增加超时时间到 30s，防止车机卡顿导致获取不到输出
        s, out = self.driver.run(adb_cmd, timeout=30)

        # --- 3. 解析结果 ---
        # 优先抓取 TotalTime，如果没有则尝试抓取 WaitTime
        match = re.search(r"TotalTime:\s+(\d+)", out)
        if s and match:
            return int(match.group(1))

        # 如果没抓到，可能是因为应用已经是前台了（环境重置失败）
        return -1


    def _show_current_activity(self):
        """获取当前前台页面信息 (Focus/Resumed) - 修复Windows管道问题"""
        self.console.clear()

        info_pkg = "Unknown"
        info_act = "Unknown"
        raw_output = ""
        success = False

        with self.console.status("[bold cyan]正在侦测前台 Activity...[/bold cyan]"):
            # 1. 优先尝试 mCurrentFocus (最准)
            # [核心修复]：注意 'shell "..."' 的写法，强制管道在手机端执行
            s, out = self.driver.run('shell "dumpsys window | grep mCurrentFocus"')

            # 过滤无效行 (防止 grep 到其他无关信息)
            if s and "mCurrentFocus" in out:
                raw_output = out.strip()
                success = True

            # 2. 如果没获取到，尝试 mResumedActivity (兜底)
            if not success or "null" in raw_output:
                s, out = self.driver.run('shell "dumpsys activity | grep mResumedActivity"')
                if s and "mResumedActivity" in out:
                    raw_output = out.strip()
                    success = True

        # 3. 智能正则提取
        # 兼容格式1: mCurrentFocus=Window{2026e4 u0 com.pkg/.Activity}
        # 兼容格式2: mResumedActivity: ActivityRecord{... u0 com.pkg/com.pkg.Activity ...}
        # 正则逻辑：寻找 u0 后面紧跟的 包名/Activity 结构
        match = re.search(r'u0\s+([a-zA-Z0-9._]+)/([a-zA-Z0-9._]+)', raw_output)

        if match:
            info_pkg = match.group(1)
            info_act = match.group(2)

            # 补全简写 (如 .MainActivity -> com.adayo.setting.MainActivity)
            if info_act.startswith("."):
                info_act = info_pkg + info_act

            # 构建专业展示面板
            grid = Table.grid(expand=True, padding=(0, 2))
            grid.add_column(style="cyan", justify="right")
            grid.add_column(style="bold white")

            grid.add_row("Package:", info_pkg)
            grid.add_row("Activity:", info_act)

            # 区分 Short Component 和 Full Component
            short_comp = f"{info_pkg}/{match.group(2)}"

            self.console.print(Panel(
                grid,
                title=f"[bold green]📍 当前顶层页面[/bold green]",
                subtitle=f"[dim]{short_comp}[/dim]",
                border_style="green"
            ))

        else:
            # 匹配失败，显示原始内容供分析 (去除乱码干扰)
            clean_out = raw_output if raw_output else "[ADB 无返回数据]"
            self.console.print(Panel(
                f"[red]❌ 解析失败，未找到 Activity 信息[/red]\n\n[dim]原始数据:\n{clean_out}[/dim]\n\n[yellow]可能原因：\n1. 屏幕已锁屏\n2. 设备未授权\n3. 当前处于桌面或特殊窗口[/yellow]",
                border_style="red"
            ))

        Prompt.ask("\n按回车返回...")

    def run_menu(self):
        import platform # 强制导入，稳健性第一

        while True:
            self.console.clear()
            self.console.print(Panel("[bold magenta]⏱️ 性能测速中心 (Performance Master)[/bold magenta]", style="magenta", box=box.HEAVY))

            menu = Table.grid(padding=(0, 2))
            menu.add_row("[yellow]1[/yellow]", "❄️ [bold]冷启动测速[/bold] (Cold Start)")
            menu.add_row("[yellow]2[/yellow]", "🔥 [bold]热启动测速[/bold] (Hot Start)")
            menu.add_row("[yellow]3[/yellow]", "🕵️ [bold cyan]获取当前 Activity[/bold cyan] (Current Focus)")
            menu.add_row("[yellow]b[/yellow]", "返回主菜单")

            self.console.print(Panel(menu, border_style="yellow"))
            c = Prompt.ask("选择测试模式").lower()

            if c in ['1', '2']:
                mode = "cold" if c == '1' else "hot"
                self._run_benchmark_wizard(mode)
            elif c == '3':
                self._show_current_activity()
            elif c == 'b':
                return

    def _run_benchmark_wizard(self, mode: str):
        # 1. 预加载全量应用
        with self.console.status("[bold cyan]正在建立应用索引库 (User + System)...[/bold cyan]"):
            all_pkgs = self._get_packages("")

        target_pkg = None

        while True:
            self.console.print("\n[bold cyan]── 🔍 智能应用搜索 ──[/bold cyan]")
            self.console.print("[dim]支持模糊匹配，如输入 'set' 或 'music'，支持多关键词空格分隔[/dim]")

            raw_input = Prompt.ask("请输入关键词 [dim](0 返回)[/dim]").strip()

            if raw_input == '0': return
            if not raw_input: continue

            # --- [核心升级] 专业模糊搜索算法 ---
            keywords = raw_input.lower().split() # 支持 "google map" 这种多词搜索
            filtered = []

            # 策略 A: 精确/分词匹配 (优先级最高)
            exact_matches = []
            for p in all_pkgs:
                p_lower = p.lower()
                # 如果所有关键词都在包名里出现
                if all(k in p_lower for k in keywords):
                    exact_matches.append(p)

            # 策略 A 排序: 越短的包名通常越是核心应用 (如 com.android.settings vs com.android.settings.intelligence)
            exact_matches.sort(key=len)

            # 策略 B: 模糊匹配 (当策略A结果太少时启用)
            fuzzy_matches = []
            if len(exact_matches) < 5:
                # 使用 difflib 查找相似度 > 0.4 的包
                fuzzy_matches = difflib.get_close_matches(raw_input, all_pkgs, n=10, cutoff=0.4)
                # 剔除已经在精确匹配里的
                fuzzy_matches = [p for p in fuzzy_matches if p not in exact_matches]

            # 合并结果
            filtered = exact_matches + fuzzy_matches
            # --------------------------------

            if not filtered:
                self.console.print(Panel(f"[yellow]未找到与 '{raw_input}' 相似的应用[/yellow]", border_style="yellow"))
                continue

            # 展示结果 (美化表格)
            self.console.clear()
            t = Table(title=f"🔍 搜索结果: '{raw_input}' (命中 {len(filtered)} 个)", box=box.ROUNDED, expand=True)
            t.add_column("ID", justify="center", style="bold cyan", width=6)
            t.add_column("Package Name", style="white")
            t.add_column("匹配类型", justify="right", style="dim")

            # 分页显示前 20 个
            for i, p in enumerate(filtered[:20]):
                match_type = "精确" if p in exact_matches else "模糊"
                t.add_row(str(i+1), p, match_type)

            self.console.print(t)

            if len(filtered) > 20:
                self.console.print(Align.center(f"[dim]... 还有 {len(filtered)-20} 个结果未显示，请提供更精确的关键词 ...[/dim]"))

            # 选择应用
            sel = Prompt.ask("\n[bold yellow]请输入 ID[/bold yellow] (r 重搜, 0 返回)", default="").lower()
            if sel == 'r': continue
            if sel == '0': return

            try:
                idx = int(sel) - 1
                if 0 <= idx < len(filtered):
                    target_pkg = filtered[idx]
                    break
                else:
                    self.console.print("[red]ID 无效[/red]")
            except:
                self.console.print("[red]输入错误[/red]")

        # 3. 自动解析 Activity
        component = self._resolve_main_activity(target_pkg)
        if not component:
            self.console.print(Panel(f"[bold red]❌ 解析入口失败[/bold red]\n无法找到 {target_pkg} 的启动 Activity。\n可能原因：\n1. 这是一个后台服务/Provider\n2. 它是动态组件", border_style="red"))
            Prompt.ask("按回车返回"); return

        self.console.print(f"[green]✔ 锁定入口: {component}[/green]")

        # 4. 设置次数
        try:
            count = int(Prompt.ask("测试轮次", default="5"))
        except: count = 5

        # 5. 开始压测
        results = []
        self.console.clear()

        # 实时表格
        table = Table(title=f"🚀 测速进行中: {target_pkg}", box=box.SIMPLE_HEAD, show_header=True, expand=True)
        table.add_column("轮次", justify="center", style="dim")
        table.add_column("耗时 (TotalTime)", justify="right", style="bold yellow")
        table.add_column("状态", justify="center")

        with Live(table, refresh_per_second=4, console=self.console) as live:
            for i in range(1, count + 1):
                t_ms = self._measure_single_launch(component, mode)

                status = "[green]PASS[/green]" if t_ms > 0 else "[red]FAIL[/red]"
                val_str = f"{t_ms} ms" if t_ms > 0 else "N/A"

                if t_ms > 0: results.append(t_ms)

                table.add_row(f"#{i}", val_str, status)
                live.update(table)
                # 稍微冷却一下，避免系统过热导致降频影响数据
                time.sleep(1)

        # 6. 生成统计报告
        if results:
            avg_val = sum(results) / len(results)
            max_val = max(results)
            min_val = min(results)

            # 计算波动率 (标准差的简化参考)
            jitter = max_val - min_val

            summary = Table.grid(expand=True, padding=(0, 2))
            summary.add_column(style="cyan", justify="right")
            summary.add_column(style="bold white")

            summary.add_row("平均耗时 (Avg):", f"{avg_val:.0f} ms")
            summary.add_row("最慢 (Max):", f"{max_val} ms")
            summary.add_row("最快 (Min):", f"{min_val} ms")
            summary.add_row("波动幅度 (Jitter):", f"{jitter} ms")
            summary.add_row("成功率:", f"{len(results)}/{count}")

            self.console.print(Panel(summary, title="[bold green]📊 性能测试报告[/bold green]", border_style="green"))
        else:
            self.console.print(Panel("[red]测试全部失败[/red]", border_style="red"))

        Prompt.ask("\n按回车返回...")


# ==========================================
# [升级] 核心模块: 素材采集中心 (全量库版)
# ==========================================
class MaterialCenter:
    def __init__(self, console: Console, config: ConfigLoader):
        self.console = console
        self.config = config

        paths = self.config.get("paths", {})
        self.save_dir = os.path.join(os.getcwd(), paths.get("materials", "test_materials"))
        if not os.path.exists(self.save_dir): os.makedirs(self.save_dir)

        self.api_keys = self.config.get("unsplash_keys", [])
        if not self.api_keys:
             self.api_keys = ["BD0I4Br4tLY4WVyNFCNIzxB-IUn1uMkSP4Ebl8Bf4AY"]

        self.current_key_idx = 0
        self.headers = {"User-Agent": "IVI-Test-Tool/5.0"}

        # 加载全量目录
        self.catalog = self.config.get("unsplash_catalog", ConfigLoader.DEFAULT_CONFIG["unsplash_catalog"])

    def _get_key(self):
        return self.api_keys[self.current_key_idx]

    def _switch_key(self):
        self.current_key_idx = (self.current_key_idx + 1) % len(self.api_keys)
        self.console.print(f"[yellow]⚠ 切换 Key 索引至: {self.current_key_idx}[/yellow]")

    def run_menu(self):
        while True:
            self.console.clear()
            self.console.print(Panel("[bold magenta]📥 素材采集中心 (Material Center)[/bold magenta]", style="magenta"))

            grid = Table.grid(expand=True)
            grid.add_column(style="white")
            grid.add_row(f"存储: [dim]{self.save_dir}[/dim]")
            grid.add_row(f"Keys: [green]{len(self.api_keys)} 个可用[/green]")
            self.console.print(Panel(grid, border_style="dim"))

            menu = Table.grid(padding=(0, 2))
            menu.add_row("[yellow]1[/yellow]", "🏎️ [bold]快速下载：车载场景[/bold] (Car/Cockpit)")
            menu.add_row("[yellow]2[/yellow]", "🗺️ [bold]快速下载：地图纹理[/bold] (Map/City)")
            menu.add_row("[yellow]3[/yellow]", "🌐 [bold cyan]浏览全量主题库[/bold cyan] (Official Catalog)") # <--- 核心新增
            menu.add_row("[yellow]4[/yellow]", "🔍 [bold]自定义关键词搜索[/bold]")
            menu.add_row("[yellow]5[/yellow]", "🔑 [bold]配置 API Keys[/bold]")
            menu.add_row("[yellow]6[/yellow]", "📂 打开素材目录")
            menu.add_row("[yellow]b[/yellow]", "返回")

            self.console.print(Panel(menu, border_style="yellow"))
            c = Prompt.ask("选择任务").lower()

            if c == '1': self._start_task("Car,Supercar,Interior", 20)
            elif c == '2': self._start_task("City,Road,Map", 20)
            elif c == '3': self._select_from_catalog() # <--- 调用新功能
            elif c == '4':
                topic = Prompt.ask("输入关键词 (英文)", default="Technology")
                try: count = int(Prompt.ask("数量", default="10"))
                except: count = 10
                self._start_task(topic, count)
            elif c == '5': self._configure_keys()
            elif c == '6':
                if platform.system() == "Windows": os.startfile(self.save_dir)
            elif c == 'b': return

    def _select_from_catalog(self):
        """全量主题库选择器"""
        self.console.clear()

        # 1. 展示一级分类
        categories = list(self.catalog.keys())
        table = Table(title="📚 Unsplash 官方主题库", box=box.SIMPLE_HEAD)
        table.add_column("ID", justify="center", style="cyan")
        table.add_column("分类 (Category)", style="bold white")
        table.add_column("包含主题 (Topics)", style="dim")

        for i, cat in enumerate(categories):
            topics = ", ".join(self.catalog[cat])
            table.add_row(str(i+1), cat, topics)

        self.console.print(table)

        # 2. 选择分类
        cat_idx = Prompt.ask("\n选择分类 ID [dim](0 返回)[/dim]", default="0")
        if cat_idx == '0' or not cat_idx.isdigit(): return

        idx = int(cat_idx) - 1
        if 0 <= idx < len(categories):
            selected_cat = categories[idx]
            topics_list = self.catalog[selected_cat]

            # 3. 选择具体主题
            self.console.print(f"\n[cyan]您选择了: {selected_cat}[/cyan]")
            sub_menu = Table.grid(padding=(0, 2))
            for i, t in enumerate(topics_list):
                sub_menu.add_row(f"[yellow]{i+1}[/yellow]", t)

            self.console.print(Panel(sub_menu, title="可用主题", border_style="green"))

            t_idx = Prompt.ask("选择主题 ID [dim](all 下载该类全部)[/dim]", default="1")

            target_query = ""
            if t_idx == 'all':
                target_query = ",".join(topics_list)
            elif t_idx.isdigit() and 0 <= int(t_idx)-1 < len(topics_list):
                target_query = topics_list[int(t_idx)-1]
            else:
                return

            try:
                count = int(Prompt.ask("下载数量", default="20"))
            except: count = 20

            self._start_task(target_query, count)

    def _configure_keys(self):
        self.console.print("\n[dim]当前 Key 列表:[/dim]")
        for k in self.api_keys:
            masked = k[:6] + "******" + k[-4:] if len(k) > 10 else "******"
            self.console.print(f"- {masked}")

        new_key = Prompt.ask("\n输入新 Unsplash Access Key (回车跳过)").strip()
        if new_key:
            self.api_keys.append(new_key)
            self.config.set("unsplash_keys", self.api_keys)
            self.console.print("[green]✔ Key 已添加[/green]")
            time.sleep(1)

    def _start_task(self, query, total_count):
        # 自动建立分类文件夹
        topic_dir = os.path.join(self.save_dir, query.split(',')[0].replace(" ", "_"))
        if not os.path.exists(topic_dir): os.makedirs(topic_dir)

        self.console.print(f"\n[cyan]🚀 开始采集: {query} (目标: {total_count})[/cyan]")
        downloaded = 0

        with Progress(SpinnerColumn(), BarColumn(), TextColumn("[progress.percentage]{task.percentage:>3.0f}%"), TextColumn("{task.description}"), console=self.console) as p:
            task = p.add_task("Downloading...", total=total_count)
            while downloaded < total_count:
                batch_size = min(30, total_count - downloaded)
                url = "https://api.unsplash.com/photos/random"
                params = {"query": query, "count": batch_size, "client_id": self._get_key(), "orientation": "landscape"}

                try:
                    res = requests.get(url, params=params, headers=self.headers, timeout=10)
                    if res.status_code == 403:
                        self._switch_key(); time.sleep(1); continue
                    if res.status_code != 200:
                        p.console.print(f"[red]API Error: {res.status_code}[/red]"); break

                    data_list = res.json()
                    if not isinstance(data_list, list): data_list = [data_list]

                    for item in data_list:
                        if downloaded >= total_count: break
                        img_url = item['urls']['regular']
                        img_id = item['id']
                        fname = f"{query.split(',')[0]}_{img_id}.jpg"
                        fpath = os.path.join(topic_dir, fname)

                        p.update(task, description=f"GET: {fname}")
                        img_bytes = requests.get(img_url, timeout=15).content
                        with open(fpath, "wb") as f: f.write(img_bytes)
                        downloaded += 1
                        p.advance(task)

                except Exception as e:
                    p.console.print(f"[red]网络异常: {e}[/red]"); break

        self.console.print(f"[bold green]✅ 采集完成! 共下载 {downloaded} 张[/bold green]")
        if Prompt.ask("是否推送到车机相册测试?", choices=["y","n"], default="n") == "y":
            self._push_to_device(topic_dir)
        Prompt.ask("按回车返回")

    def _push_to_device(self, local_path):
        target = "/sdcard/Pictures/MaterialTest"
        self.console.print(f"[cyan]推送至 {target}...[/cyan]")
        subprocess.run(f"adb push \"{local_path}\" {target}", shell=True)
        self.console.print("[green]✔ 完成[/green]")


# ==========================================
# 展示层: CAR-HOUSE-KEEP v3.2.1
# ==========================================
class CarHouseKeepApp:
    def __init__(self):



        self.console = Console()
        self.driver = AdbDriver(device_id=None)
        self.config_loader = ConfigLoader()

        # 2. 注入配置到需要的模块
        self.unlocker = PrivilegeUnlocker(self.driver, self.console, self.config_loader) # 传入 config
        self.material_center = MaterialCenter(self.console, self.config_loader)     # 传入 config
        self.video_tool = ScreenRecorder(self.driver, self.console)
        self.monkey_tool = MonkeyTester(self.driver, self.console)
        self.img_converter = ImageConverter(self.console)
        self.perf_master = PerformanceMaster(self.driver, self.console)

        self.ota_mgr = OtaConfigManager(self.driver, self.console)


        # --- 核心修复：初始化日志中心 ---
        # LogCenter 会内部初始化 LogcatAdvanced 和 OfflineLogManager
        self.log_center = LogCenter(self.driver, self.console)
        self.app_mgr = AppManager(self.driver, self.console)



        # 兼容旧代码逻辑的录制器（如果 action_install_with_log 还在用它）
        self.recorder = LogRecorder(self.driver)

        # 初始化截屏管理
        self.screenshot_manager = ScreenshotManager(self.driver, self.console)

        self.version = "v3.3.0-ROOT-FULL"

        # 延迟初始化的组件（IVI Sentinel 相关）
        self.ivi_source = None
        self.ivi_engine = None
        self.ivi_ui = None

        # 时间更新线程变量
        self.current_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.time_update_stop = False
        self.time_update_thread = None

    def _start_time_update_thread(self):
        """启动后台时间更新线程"""
        def update_time():
            while not self.time_update_stop:
                self.current_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                time.sleep(1)

        self.time_update_stop = False
        self.time_update_thread = threading.Thread(target=update_time, daemon=True)
        self.time_update_thread.start()

    def _stop_time_update_thread(self):
        """停止时间更新线程"""
        self.time_update_stop = True
        if self.time_update_thread and self.time_update_thread.is_alive():
            self.time_update_thread.join(timeout=2)

    def _make_header(self):
        grid = Table.grid(expand=True)
        grid.add_column(justify="left", ratio=1)
        grid.add_column(justify="right", ratio=1)
        grid.add_row(
            "[bold cyan]CAR-HOUSE-KEEP PROFESSIONAL[/bold cyan]",
            f"[dim]Log Auto-Archive + Logcat Analyzer | {self.version}[/dim]"
        )
        return Panel(grid, style="bright_blue", box=box.HEAVY)

    def log_status(self, msg: str, level: str = "info"):
        colors = {"info": "cyan", "success": "green", "warn": "yellow", "error": "red"}
        icon = {"info": "ℹ", "success": "✓", "warn": "⚠", "error": "✘"}
        self.console.print(f"[{colors[level]}]{icon[level]}[/{colors[level]}] {msg}")

    def _get_permission_role(self):
        """获取当前权限角色 (user/root)"""
        success, output = self.driver.run("shell id")
        if success and "uid=0" in output:
            return "[bold green]ROOT[/bold green]"
        else:
            return "[bold yellow]USER[/bold yellow]"


    def action_ivi_sentinel(self):
        """
        功能：启动 IVI Sentinel 实时监控
        修复：统一类名引用，解决 NameError 和同事环境下的初始化问题
        """
        self.console.clear()
        self.console.print(Panel(
            "[bold cyan]🛰️ IVI Sentinel PRO 监控[/bold cyan]\n"
            "[dim]正在初始化遥测引擎... 按 Ctrl+C 退出监控[/dim]",
            style="cyan",
            box=box.DOUBLE
        ))

        try:
            # 1. 检查并初始化数据源 (注意：这里使用你代码中定义的 AdbSource)
            if not getattr(self, "ivi_source", None):
                # 传入当前 app 正在使用的 driver 实例
                self.ivi_source = AdbSource(device_id=self.driver.device_id)

            # 2. 检查并初始化计算引擎
            if not getattr(self, "ivi_engine", None):
                self.ivi_engine = IVIMetricsEngine(self.ivi_source)

            # 3. 检查并初始化 UI 界面 (确保类名与你定义的 AdvancedSentinelUI 一致)
            if not getattr(self, "ivi_ui", None):
                self.ivi_ui = AdvancedSentinelUI(self.ivi_engine, self.console)

            # 4. 启动前强制同步当前设备 ID (防止多设备干扰)
            self.ivi_source.device_id = self.driver.device_id

            # 🚀 启动监控
            self.ivi_ui.start()

        except KeyboardInterrupt:
            self.console.print("\n[yellow]👋 已安全停止监控，返回主菜单[/yellow]")
            time.sleep(0.5)
        except NameError as ne:
            # 针对类名定义错误的详细提示
            self.console.print(f"[bold red]❌ 脚本定义错误[/bold red]: {ne}")
            self.console.print("[yellow]请检查脚本中 AdbSource/IVIMetricsEngine 类名是否书写正确[/yellow]")
            Prompt.ask("\n按回车键返回")
        except Exception as e:
            self.console.print(Panel(f"[bold red]❌ 监控运行异常[/bold red]\n[white]{str(e)}[/white]", border_style="red"))
            Prompt.ask("\n按回车键返回")

    def action_install_with_log(self):
        """带日志监控的安装流程"""
        raw_input = Prompt.ask("\n[bold]请拖入 APK 文件[/bold]")
        # 路径清洗逻辑
        path = raw_input.strip().lstrip('&').strip().strip("'").strip('"')

        if not os.path.exists(path):
            self.log_status("文件不存在", "error")
            Prompt.ask("\n按回车返回...")
            return

        # 核心逻辑：开始安装前启动后台日志归档
        self.log_status("后台日志归档已启动...", "info")
        self.recorder.start()

        try:
            self.console.print(f"[yellow]正在执行安装策略...[/yellow]")
            success, err = self.driver.run(f'install -r -d -t "{path}"')

            if success:
                self.log_status("安装成功", "success")
            else:
                self.log_status(f"安装失败: {err}", "error")
                # 如果安装失败，日志将变得极其珍贵
                log_path = self.recorder.stop()
                self.log_status(f"错误详情已捕获至: {log_path}", "warn")
        finally:
            # 无论成功失败，给用户选择是否持续记录
            if self.recorder.is_recording:
                if Prompt.ask("是否停止后台日志记录？", choices=["y", "n"], default="y") == "y":
                    final_log = self.recorder.stop()
                    self.log_status(f"日志已保存: {final_log}", "success")

            Prompt.ask("\n按回车返回...")


    # def action_gain_root(self):
    #     """上帝模式：自动处理 Disable 和 Remount"""
    #     self.console.print(Panel("[bold red]☢️ 正在启动系统深度解锁协议 (上帝模式)[/bold red]", border_style="red"))

    #     if not self.ivi_source:
    #         self.ivi_source = AdbSource(device_id=self.driver.device_id)

    #     with Progress(
    #         SpinnerColumn(),
    #         TextColumn("[progress.description]{task.description}"),
    #         BarColumn(complete_style="red"),
    #         TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
    #         console=self.console, transient=True
    #     ) as progress:

    #         task = progress.add_task("[white]初始化...", total=100)

    #         # 1. 注入密码
    #         progress.update(task, completed=10, description="[yellow]注入 Adayo 认证密钥...")
    #         self.ivi_source.run_raw(f"shell setprop service.adb.root.password {self.ivi_source.root_pwd}")

    #         # 2. 尝试 Root
    #         progress.update(task, completed=30, description="[yellow]开启 ADB Root 服务...")
    #         self.ivi_source.run_raw("root")
    #         time.sleep(2)

    #         # 3. 核心 Disable 操作
    #         progress.update(task, completed=60, description="[red]执行深度 Disable (Verity/SELinux)...")
    #         self.ivi_source.run_raw("shell setenforce 0")
    #         verity_res = self.ivi_source.run_raw("disable-verity") # 关键步骤
    #         self.ivi_source.run_raw("shell setprop ro.boot.selinux disabled")

    #         # 4. 尝试 Remount
    #         progress.update(task, completed=85, description="[yellow]正在解锁全分区读写 (Remount)...")
    #         remount_res = self.ivi_source.run_raw("remount")
    #         self.ivi_source.run_raw("shell mount -o remount,rw /")

    #         progress.update(task, completed=100, description="[green]流程执行完毕")

    #     # --- 智能引导逻辑 ---
    #     if "reboot" in verity_res.lower() or "reboot" in remount_res.lower():
    #         self.log_status("检测到 dm-verity 锁定，必须重启车机后权限才能完全生效！", "warning")
    #         if Prompt.ask("是否现在重启车机以完成解锁？(y/n)", default="y") == "y":
    #             self.ivi_source.run_raw("reboot")
    #             self.console.print("[bold green]✔ 重启指令已发送，请等待车机重启后再次运行此工具即可获得上帝权限。[/bold green]")
    #             sys.exit(0)
    #     else:
    #         uid = self.ivi_source.run_command("id")
    #         if "uid=0" in uid:
    #             self.log_status("【上帝模式已激活】UID:0 | SELinux:Off | FS:RW", "success")
    #         else:
    #             self.log_status("提权验证失败，请检查 USB 连接或手动输入密码。", "error")

    #     Prompt.ask("\n按回车返回菜单...")

    def action_toggle_log_recording(self):
        """切换后台日志录制状态"""
        if not self.recorder.is_recording:
            self.recorder.start()
            self.log_status("后台监控已开启，正在归档...", "success")
        else:
            path = self.recorder.stop()
            self.log_status(f"监控已停止，日志已归档至: {path}", "info")

        Prompt.ask("\n按回车返回...")

    def action_reboot_device(self):
        """专业设备重启功能"""
        self.console.clear()
        self.console.print(Panel(
            "[bold yellow]⚠️ 设备重启管理[/bold yellow]\n[dim]此操作将重启连接的Android设备。请确保所有重要数据已保存。[/dim]",
            style="yellow",
            box=box.DOUBLE
        ))

        # 显示当前设备信息
        status_table = Table(title="设备状态", box=box.ROUNDED)
        status_table.add_column("属性", style="cyan")
        status_table.add_column("值", style="green")

        success, model = self.driver.run("shell getprop ro.product.model")
        status_table.add_row("型号", model if success else "未知")

        success, build = self.driver.run("shell getprop ro.build.version.release")
        status_table.add_row("Android版本", build if success else "未知")

        permission_role = self._get_permission_role()
        status_table.add_row("权限角色", permission_role)

        self.console.print(status_table)

        self.console.print("\n[yellow]警告: 重启将中断所有正在运行的进程和连接。[/yellow]")
        confirm = Prompt.ask("确认重启设备？", choices=["y", "n"], default="n")

        if confirm.lower() == "y":
            # 可选: Root模式下使用更安全的重启
            if "ROOT" in permission_role:
                cmd = "shell  'reboot'"
                self.console.print("[cyan]使用Root权限重启...[/cyan]")
            else:
                cmd = "reboot"
                self.console.print("[cyan]使用标准ADB重启...[/cyan]")

            success, output = self.driver.run(cmd)

            if success:
                self.console.print("[green]✓ 重启命令已发送。设备将在几秒内重启。[/green]")
                self.console.print("[dim]请等待设备重新连接...[/dim]")
                time.sleep(5)  # 短暂等待
                self.driver.run("wait-for-device")
                self.console.print("[green]✓ 设备已重新连接。[/green]")
            else:
                self.console.print(f"[red]✘ 重启失败: {output}[/red]")
                if "permission" in output.lower():
                    self.console.print("[yellow]建议: 尝试获取Root权限后重试。[/yellow]")
        else:
            self.console.print("[yellow]已取消重启操作。[/yellow]")

        Prompt.ask("\n按回车返回主菜单...")

    def action_screenshot_tool(self):
        """专业截屏工具入口"""
        self.screenshot_manager.show_menu()

    def main_menu(self):
        # 缓存变量，防止界面刷新时闪烁
        cached_model = None
        cached_android = None

        while True:
            self.console.clear()

            # --- 1. 设备连接检测 ---
            s, out = self.driver.run("devices")
            devs = [l.split()[0] for l in out.splitlines() if 'device' in l and 'List' not in l]

            if not devs:
                self.console.print(Panel(Align.center("[bold red]❌ 未检测到设备连接[/bold red]\n[dim]请检查 USB 线或 ADB 驱动[/dim]"), border_style="red"))
                if Prompt.ask("操作选择", choices=["Retry", "Quit"], default="Retry") == "Quit":
                    break
                continue

            # 更新当前操作的设备 ID
            self.driver.device_id = devs[0]

            # --- 2. 获取或使用缓存信息 (优化性能) ---
            if not cached_model:
                s_m, m = self.driver.run("shell getprop ro.product.model")
                cached_model = m.strip() if s_m else "Unknown"
                s_v, v = self.driver.run("shell getprop ro.build.version.release")
                cached_android = v.strip() if s_v else "Unknown"

            # --- 3. 实时状态遥测 ---
            s, uid_out = self.driver.run("shell id")
            is_root = "uid=0" in uid_out
            perm_text = "[bold green]ROOT (Unlocked)[/bold green]" if is_root else "[bold yellow]USER (Locked)[/bold yellow]"

            # 从 log_center 获取录制状态
            is_rec = self.log_center.live_log.is_recording
            rec_status = "[bold white on red] ● REC [/bold white on red]" if is_rec else "[dim] ○ IDLE [/dim]"

            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # --- 4. UI 渲染 (HUD + 仪表盘 + 菜单) ---

            # 4.1 顶部 HUD 抬头显示
            header_grid = Table.grid(expand=True)
            header_grid.add_column(justify="left", ratio=1)
            header_grid.add_column(justify="center", ratio=1)
            header_grid.add_column(justify="right", ratio=1)
            header_grid.add_row(
                f"[bold cyan]IVI TOOLBOX PRO[/bold cyan] [dim]{self.version}[/dim]",
                f"[bold yellow]{now_str}[/bold yellow]",
                f"[bold magenta]Jonas[/bold magenta] | [dim]dengzhu-hub[/dim]"
            )
            self.console.print(Panel(header_grid, style="blue", box=box.HEAVY))

            # 4.2 实时遥测仪表盘
            dash_table = Table(box=box.SIMPLE, show_header=False, expand=True, padding=(0, 1))
            dash_table.add_column("Key", style="cyan", justify="right", ratio=1)
            dash_table.add_column("Val", style="white", justify="left", ratio=2)
            dash_table.add_column("Key2", style="cyan", justify="right", ratio=1)
            dash_table.add_column("Val2", style="white", justify="left", ratio=2)

            dash_table.add_row("Device:", f"[bold white]{cached_model}[/bold white]", "Android:", cached_android)
            dash_table.add_row("Serial:", f"[dim]{self.driver.device_id}[/dim]", "Privilege:", perm_text)
            dash_table.add_row("Log Status:", rec_status, "", "")
            self.console.print(Panel(dash_table, title="[bold green]📡 实时遥测 (Telemetry)[/bold green]", border_style="green"))

            # 4.3 功能矩阵菜单
            menu_table = Table(box=box.ROUNDED, show_header=True, header_style="bold blue", expand=True, border_style="dim")
            menu_table.add_column("🛠️ 核心运维", ratio=1)
            menu_table.add_column("🧰 应用工具", ratio=1)

            menu_table.add_row(
                "[bold yellow]1[/bold yellow]  🚀 工程提权 [dim](Root/Remount)[/dim]",
                "[bold yellow]4[/bold yellow]  💿 智能安装 APK [dim](Auto-Grant)[/dim]"
            )
            menu_table.add_row(
                "[bold yellow]2[/bold yellow]  📊 系统监控 [dim](Top/Sentinel)[/dim]",
                "[bold yellow]5[/bold yellow]  🗑️ 应用卸载 [dim](App Manager)[/dim]"
            )
            menu_table.add_row(
                "[bold yellow]3[/bold yellow]  📺 [bold magenta]日志指挥中心[/bold magenta] [dim](Live/Pull)[/dim]",
                "[bold yellow]6[/bold yellow]  📸 专业截图 [dim](Burst/Delay)[/dim]"
            )

               # --- [插入] 新增视频录制入口 ---
            menu_table.add_row(

                "[bold yellow]9[/bold yellow]  🎥 [bold magenta]屏幕录制[/bold magenta] [dim](MP4/Record)[/dim]",
                 "[bold yellow]8[/bold yellow]  🔧 [bold cyan]OTA 参数配置[/bold cyan] [dim](PNO/VIN)[/dim]") # 新增



            menu_table.add_row(
                "[bold yellow]10[/bold yellow] 🐒 [bold red]Monkey 压测[/bold red] [dim](Stress Test)[/dim]", # 新增
                "[bold yellow]7[/bold yellow]  🔄 重启设备 [dim](Reboot)[/dim]"
            )
            menu_table.add_row(
                "[bold yellow]11[/bold yellow] 🎨 [bold magenta]图片工厂[/bold magenta] [dim](Convert/Resize)[/dim]" ,
                 "[bold yellow]12[/bold yellow] ⏱️ [bold cyan]性能测速[/bold cyan] [dim](Cold/Hot Start)[/dim]"

            )
            menu_table.add_row(
                 "[bold yellow]13[/bold yellow] 📥 [bold cyan]素材采集中心[/bold cyan] [dim](Download)[/dim]" # 新增,
                "[bold red]q[/bold red]   退出系统"
            )
            self.console.print(menu_table)

            # --- 5. 交互处理 (修复点：确保 self 后缀的方法/对象名正确) ---
            c = Prompt.ask("\n[bold cyan]请输入指令[/bold cyan]", default="").lower()

            if c == "1":
                # 修复：调用原有的 action_gain_root 或初始化后的 unlocker
                self.unlocker.execute_unlock_sequence()
            elif c == "2":
                # 修复：调用原有的 action_ivi_sentinel
                self.action_ivi_sentinel()
            elif c == "3":
                # 核心修复：调用 LogCenter 聚合菜单
                self.log_center.run_menu()
            elif c == "4":
                # 修复：调用类中定义的安装方法 action_install_with_log
                self.action_install_with_log()
            elif c == "5":
                # 注意：如果尚未实现 app_mgr，可暂时打印提示
                 self.app_mgr.run_menu()
            elif c == "6":
                # 修复：调用类中定义的 action_screenshot_tool
                self.action_screenshot_tool()
            elif c == "7":
                if Prompt.ask("确认重启设备?", choices=["y", "n"]) == "y":
                    self.driver.run("reboot")
                    cached_model = None # 重启后清除缓存

            elif c == "8":
                self.ota_mgr.run_wizard() # 调用 OTA 向导
            elif c == "9":
                self.video_tool.run_menu()
            elif c == "10":
                self.monkey_tool.config_menu()
            elif c == "11":
                self.img_converter.run_menu()
            elif c == "12":
                self.perf_master.run_menu()
            elif c == "13":
                self.material_center.run_menu()

            elif c == "q":
                # --- [新增] 1. 防误触二次确认 ---
                self.console.print("\n") # 空一行，呼吸感
                if Prompt.ask("[bold red]❓ 确定要退出系统吗?[/bold red]", choices=["y", "n"], default="n") == "n":
                    continue # 用户后悔了，回到循环

                # --- [新增] 2. 资源释放可视化 (仪式感) ---
                self.console.clear()
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[bold cyan]{task.description}"),
                    BarColumn(bar_width=40),
                    TextColumn("[green]{task.fields[status]}"),
                    console=self.console
                ) as p:
                    # 创建一个总任务
                    task_id = p.add_task("正在关闭系统服务...", total=3, status="准备就绪")

                    # 阶段 A: 检查并停止后台日志
                    p.update(task_id, description="正在检查后台录制任务...")
                    time.sleep(0.3) # 稍微停留展示过程
                    if self.log_center.live_log.is_recording:
                        self.log_center.live_log.stop_recording()
                        p.update(task_id, advance=1, status="[已保存并停止]")
                    else:
                        p.update(task_id, advance=1, status="[无后台任务]")

                    # 阶段 B: 停止 UI 刷新线程
                    p.update(task_id, description="正在终止 UI 刷新线程...")
                    self._stop_time_update_thread()
                    time.sleep(0.3)
                    p.update(task_id, advance=1, status="[线程已销毁]")

                    # 阶段 C: 断开 ADB 链接 (可选，这里仅做模拟清理)
                    p.update(task_id, description="正在清理临时缓存...")
                    time.sleep(0.2)
                    p.update(task_id, advance=1, status="[清理完成]")

                    # 完成
                    p.update(task_id, description="[bold green]系统安全关闭[/bold green]", status="✅ DONE")

                # --- [新增] 3. 专业的告别面板 ---
                farewell_text = (
                    f"[bold white]感谢使用 IVI TOOLBOX PRO[/bold white]\n"
                    f"[dim]Session Duration: {datetime.now().strftime('%H:%M:%S')}[/dim]\n\n"
                    f"[cyan]Keep Coding, Keep Testing![/cyan] 🚗💨"
                )

                self.console.print(Panel(
                    Align.center(farewell_text),
                    border_style="blue",
                    box=box.HEAVY,
                    padding=(1, 5)
                ))

                # 稍微暂停一下让用户看清告别语
                time.sleep(1)
                break

if __name__ == "__main__":
    app = CarHouseKeepApp()
    try:
        app.main_menu()
    except KeyboardInterrupt:
        app.console.print("\n[yellow]⚠ 检测到中断信号[/yellow]")
        if app.recorder.is_recording:
            app.recorder.stop()
        app._stop_time_update_thread()
        app.console.print("[green]✓ 系统已安全退出[/green]")