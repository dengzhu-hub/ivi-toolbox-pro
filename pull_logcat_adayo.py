import argparse
import subprocess
import datetime
import sys
import shutil
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.rule import Rule
import time

# ========================================
# 1. 配置和元信息 (定制化区域)
# ========================================

TOOL_NAME = "Adayo 车载日志拉取工具"
VERSION = "20.1.0 (增强 ANR/BT 拉取)" # 版本号更新：增加 ANR 和 Bluetooth 日志拉取功能
AUTHOR = "Jonas (深圳海冰科技 测试工程师)"
GITHUB_LINK = "dengzhu-hub"

# --- AdayoLog 配置 (/mnt/sdcard) ---
LOG_TYPES = [
    "logcat",  "setting", "systemproperty", "config", "kernel",
     "tombstones", "dropbox", "resource", "mcu", "aee", "ael", "upgrade"
]
REMOTE_LOG_PATH = "/mnt/sdcard/AdayoLog"

# --- WLAN Log 配置 (/data/vendor/wifi) ---
WLAN_LOG_TYPE = "wlan_logs"
WLAN_LOG_PATH = "/data/vendor/wifi/wlan_logs"

# --- 新增 ANR Log 配置 (/data/anr) ---
ANR_LOG_TYPE = "anr" # 导出后的本地目录名
ANR_LOG_PATH = "/data/anr"

# --- 新增 Bluetooth Log 配置 (/data/misc/bluetooth/logs) ---
# 注意：此处的命名将覆盖配置区的 btsnoop 命名，导出目录为 btsnoop
BTSNOOP_LOG_TYPE = "btsnoop"
BTSNOOP_LOG_PATH = "/data/misc/bluetooth/logs"


# 初始化 Rich Console
console = Console()

# ========================================
# 2. 核心功能函数
# ========================================

def run_adb_command(command: list, serial: str = None, check_output: bool = False):
    """
    执行 ADB 命令，返回结果或检查命令是否成功。
    """
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
        console.print("[bold red]ERROR:[/bold red] ADB tool not found. Please ensure ADB is in your system PATH.", file=sys.stderr)
        sys.exit(1)
    except subprocess.TimeoutExpired:
        console.print(f"[bold red]ERROR:[/bold red] Command timed out after 120 seconds: {' '.join(command)}", file=sys.stderr)
        return False

def print_step_title(step_num: str, title: str):
    """打印增强型步骤标题。"""
    # 步骤总数：1, 1.5, 2, 3, 4, 5
    console.print(f"\n[bold white on blue] STEP {step_num}/5: {title} [/bold white on blue]")

def check_and_get_device():
    """检查设备连接状态。"""
    print_step_title("1", "检查设备连接...")
    output = run_adb_command(["devices"], check_output=True)
    devices = []
    if output:
        lines = output.split('\n')
        for line in lines[1:]:
            if line.strip() and "device" in line and "unauthorized" not in line:
                serial = line.split('\t')[0]
                devices.append(serial)

    if len(devices) != 1:
        console.print(f"[bold red]错误:[/bold red] 找到 {len(devices)} 个设备。请连接且只连接一个设备。")
        sys.exit(1)

    serial = devices[0]
    # 修复 Rich MarkupError
    console.print(f"[bold green]成功:[/bold green] 设备已连接。序列号: [bold cyan]{serial}[/bold cyan]")
    return serial

def root_device(serial: str):
    """尝试以 root 权限重启 adbd，并执行 remount。"""
    print_step_title("1.5", "尝试 Root 权限和 Remount...")

    # 1. 尝试 ADB Root
    output_root = run_adb_command(["root"], serial=serial, check_output=True)

    if "restarting" in output_root.lower():
        time.sleep(3)
        try:
            # 重新检查连接
            check_and_get_device()
            console.print("[bold green]成功:[/bold green] 设备已重启并获得 Root 权限。")
        except SystemExit:
             console.print(f"[bold red]失败:[/bold red] Root 尝试后连接丢失。")
             sys.exit(1)
    elif "adbd cannot run as root" in output_root.lower():
        console.print(f"[bold yellow]警告:[/bold yellow] ADB root 被禁用，将使用现有权限继续。")
    else:
        console.print(f"[bold green]成功:[/bold green] ADB 已确认以 Root 运行。")

    # 2. 尝试 ADB Remount (关键权限增强)
    console.print("[dim]-> 尝试 ADB Remount 以确保文件系统可读/写...[/dim]")
    output_remount = run_adb_command(["remount"], serial=serial, check_output=True)

    if "remount succeeded" in output_remount.lower():
        console.print("[bold green]成功:[/bold green] ADB Remount successful.")
    else:
        console.print(f"[bold yellow]警告:[/bold yellow] ADB Remount 失败或跳过。错误: {output_remount.splitlines()[-1]}")

def get_timestamp_and_path(serial: str):
    """获取时间戳和导出路径。"""
    print_step_title("2", "获取时间戳并设置导出路径...")

    device_time_str = run_adb_command(
        ["shell", "date +%Y%m%d_%H%M%S"],
        serial=serial,
        check_output=True
    )

    time_source = "PC 本地时间"
    if device_time_str and len(device_time_str) >= 15 and "error" not in device_time_str.lower():
        timestamp = device_time_str.replace('\n', '').replace('\r', '').strip()[:15]
        time_source = "设备时间"
    else:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    local_base_path = Path.cwd() / "CarLogs"
    # 更新路径名以包含 ANR/BT
    export_path = local_base_path / f"AdayoLog_WLAN_ANR_BT_{timestamp}"
    export_path.mkdir(parents=True, exist_ok=True)

    console.print(f"[bold green]成功:[/bold green] 导出路径: [bold magenta]{export_path}[/bold magenta]")
    console.print(f"[bold green]成功:[/bold green] 时间戳: [bold cyan]{timestamp}[/bold cyan] ({time_source})")

    return timestamp, time_source, export_path

# 保持 pull_logs 不变
def pull_logs(serial: str, export_path: Path):
    """
    循环拉取 /mnt/sdcard/AdayoLog 目录，使用标准的 ADB Pull 模式。
    """
    print_step_title("3", f"拉取主日志 ({REMOTE_LOG_PATH})...")
    console.print(f"[dim]尝试使用标准 ADB Pull 从 {REMOTE_LOG_PATH} 拉取目录...[/dim]")

    files_pulled_count = 0
    empty_pulled_count = 0
    fail_count = 0
    total_count = len(LOG_TYPES)
    results = []

    for i, log_type in enumerate(LOG_TYPES):
        # 路径兼容修复 V12.0.2：强制使用字符串拼接和正斜杠。
        remote_path_str = f"{REMOTE_LOG_PATH}/{log_type}"
        local_target_dir = export_path / log_type

        console.print(f"\n[{i+1}/{total_count}] 处理: [bold]{log_type}[/bold]...", end="")

        pull_cmd = ["pull", remote_path_str, str(local_target_dir)]

        result = subprocess.run(
            ["adb", "-s", serial] + pull_cmd,
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

        if is_success:
            if local_target_dir.exists():
                file_count = sum(1 for item in local_target_dir.rglob('*') if item.is_file())

                if file_count > 0:
                    console.print(f"[bold green] -> 成功[/bold green] ([dim]{file_count} 文件[/dim])")
                    files_pulled_count += 1
                    results.append((log_type, f"OK ({file_count} files)", "FILES"))
                else:
                    console.print("[bold yellow] -> OK (空目录)[/bold yellow]")
                    if local_target_dir.is_dir():
                        try:
                            shutil.rmtree(local_target_dir)
                        except OSError:
                            pass
                    empty_pulled_count += 1
                    results.append((log_type, "OK (Empty Dir)", "EMPTY"))
            else:
                console.print("[bold red] -> FAIL[/bold red] (本地文件夹未创建)")
                fail_count += 1
                results.append((log_type, "FAIL (I/O Error)", "HARD_FAIL"))
        else:
            console.print("[bold red] -> FAIL[/bold red] (ADB Pull 错误)")
            diag_message = "Pull failed."
            if "no such file or directory" in error.lower() or "0 files pulled" in output.lower():
                diag_message = "FAIL (Missing Dir)"
            elif "permission denied" in error.lower():
                diag_message = "FAIL (Perm. Denied)"

            if error:
                console.print(f"[dim]ADB 错误: {error.splitlines()[-1]}[/dim]")

            fail_count += 1
            results.append((log_type, diag_message, "HARD_FAIL"))

    return files_pulled_count, empty_pulled_count, fail_count, results

# 保持 pull_wlan_logs 不变
def pull_wlan_logs(serial: str, export_path: Path):
    """
    拉取 /data/vendor/wifi/wlan_logs 整个目录。
    返回: log_type, log_status, status_type, files_pulled_count, fail_count
    """
    print_step_title("4", f"拉取 WLAN 日志 ({WLAN_LOG_PATH})...")
    console.print(f"[dim]尝试使用标准 ADB Pull 目录 ({WLAN_LOG_PATH}) ...[/dim]")

    log_type = WLAN_LOG_TYPE
    remote_path_str = WLAN_LOG_PATH
    local_target_dir = export_path / log_type

    # 核心：使用 adb pull 命令拉取整个目录到 export_path
    pull_cmd = ["pull", remote_path_str, str(export_path)]

    result = subprocess.run(
        ["adb", "-s", serial] + pull_cmd,
        capture_output=True,
        text=True,
        check=False,
        encoding='utf-8',
        timeout=60
    )

    output = result.stdout.strip()
    error = result.stderr.strip()

    is_success = (result.returncode == 0 and
                  "pull failed" not in error.lower() and
                  "no such file" not in error.lower() and
                  "0 files pulled" not in output.lower())

    if is_success:
        if local_target_dir.exists():
            file_count = sum(1 for item in local_target_dir.rglob('*') if item.is_file())

            if file_count > 0:
                console.print(f"[bold green] -> 成功[/bold green] ([dim]目录包含 {file_count} 文件[/dim])")
                return log_type, f"OK ({file_count} files)", "FILES", file_count, 0
            else:
                console.print("[bold yellow] -> OK (空目录)[/bold yellow]")
                if local_target_dir.is_dir():
                    try:
                        shutil.rmtree(local_target_dir) # 清理空目录
                    except OSError:
                        pass
                return log_type, "OK (Empty Dir)", "EMPTY", 0, 0
        else:
            console.print("[bold red] -> FAIL[/bold red] (本地文件夹未创建)")
            return log_type, "FAIL (I/O Error)", "HARD_FAIL", 0, 1
    else:
        console.print("[bold red] -> FAIL[/bold red] (ADB Pull 错误)")
        diag_message = "Pull failed."
        if "no such file or directory" in error.lower() or "0 files pulled" in output.lower():
             diag_message = "FAIL (Missing Dir)"
        elif "permission denied" in error.lower():
             diag_message = "FAIL (Perm. Denied)"

        if error:
             console.print(f"[dim]ADB 错误: {error.splitlines()[-1]}[/dim]")

        return log_type, diag_message, "HARD_FAIL", 0, 1


# 新增：通用的特殊路径拉取函数，用于 ANR 和 BTSNOOP
def pull_special_logs(serial: str, export_path: Path, log_type: str, remote_path: str, step_num: str):
    """
    拉取指定的特殊路径的整个目录。
    参数: log_type (本地目录名), remote_path (远程路径), step_num (步骤编号)
    返回: log_type, log_status, status_type, files_pulled_count, fail_count
    """
    print_step_title(step_num, f"拉取特殊日志 ({log_type}: {remote_path})...")
    console.print(f"[dim]尝试使用标准 ADB Pull 目录 ({remote_path}) ...[/dim]")

    local_target_dir = export_path / log_type

    # 核心：使用 adb pull 命令拉取整个目录到 export_path
    # 注意：如果远程路径是目录，pull 到本地目录的父目录 (export_path)，本地会自动创建同名目录
    pull_cmd = ["pull", remote_path, str(export_path)]

    result = subprocess.run(
        ["adb", "-s", serial] + pull_cmd,
        capture_output=True,
        text=True,
        check=False,
        encoding='utf-8',
        timeout=60
    )

    output = result.stdout.strip()
    error = result.stderr.strip()

    is_success = (result.returncode == 0 and
                  "pull failed" not in error.lower() and
                  "no such file" not in error.lower() and
                  "0 files pulled" not in output.lower())

    if is_success:
        # ADB Pull 成功后，检查本地目录是否被创建
        if local_target_dir.exists():
            # 使用 rglob 统计文件数量
            file_count = sum(1 for item in local_target_dir.rglob('*') if item.is_file())

            if file_count > 0:
                console.print(f"[bold green] -> 成功[/bold green] ([dim]目录包含 {file_count} 文件[/dim])")
                return log_type, f"OK ({file_count} files)", "FILES", file_count, 0
            else:
                console.print("[bold yellow] -> OK (空目录)[/bold yellow]")
                if local_target_dir.is_dir():
                    try:
                        shutil.rmtree(local_target_dir) # 清理空目录
                    except OSError:
                        pass
                return log_type, "OK (Empty Dir)", "EMPTY", 0, 0
        else:
            # 这种情况可能是远程目录为空，ADB pull 自动跳过，本地目录未创建。
            console.print("[bold yellow] -> OK (本地文件夹未创建，可能远程为空) [/bold yellow]")
            return log_type, "OK (Empty Dir)", "EMPTY", 0, 0

    else:
        console.print("[bold red] -> FAIL[/bold red] (ADB Pull 错误)")
        diag_message = "Pull failed."
        if "no such file or directory" in error.lower() or "0 files pulled" in output.lower():
             diag_message = "FAIL (Missing Dir)"
        elif "permission denied" in error.lower():
             diag_message = "FAIL (Perm. Denied)"

        if error:
             console.print(f"[dim]ADB 错误: {error.splitlines()[-1]}[/dim]")

        return log_type, diag_message, "HARD_FAIL", 0, 1


def generate_report_and_summary(timestamp: str, time_source: str, export_path: Path,
                                 serial: str, total_files_pulled: int, total_empty_pulled: int, total_fail: int,
                                 all_results: list):
    """生成 CLI 总结报告和 Report.txt 文件。"""
    print_step_title("5", "生成报告与最终总结...")

    summary_table = Table(title="日志导出总结 (Dengzhu-Hub)", show_header=True, header_style="bold magenta", border_style="dim cyan")
    summary_table.add_column("日志类型", style="cyan", justify="left")
    summary_table.add_column("状态", style="bold", justify="left")

    for log_type, status, status_type in all_results:
        if status_type == "FILES":
            status_style = "green"
        elif status_type == "EMPTY":
            status_style = "yellow"
        else: # HARD_FAIL
            status_style = "red"
        summary_table.add_row(log_type, f"[{status_style}]{status}[/]")

    console.print(summary_table)

    # UI/UX 优化：突出最终统计
    console.print("\n")
    console.print(Rule("[bold white on blue]========== 任务状态报告 ==========[/bold white on blue]", style="bold blue"))
    # 更新总类型数 (主日志: 12 + WLAN: 1 + ANR: 1 + BTSNOOP: 1 = 15)
    total_log_types = len(LOG_TYPES) + 2
    console.print(f"[bold]总类型数:[/bold] {total_log_types} 项 (含 WLAN/ANR/BT)")
    console.print(f"[bold green]成功拉取文件:[/bold green] {total_files_pulled} 项 :white_check_mark:")
    console.print(f"[bold yellow]成功拉取空目录:[/bold yellow] {total_empty_pulled} 项 :warning:")
    console.print(f"[bold red]硬失败:[/bold red] {total_fail} 项 :x:")
    console.print(f"[bold]输出位置:[/bold] [bold magenta]{export_path}[/bold magenta]")

    # 生成 Report.txt
    report_content = [
        f"{TOOL_NAME} 导出报告 (v{VERSION})",
        f"工程师: {AUTHOR}",
        f"Github/Gitee: {GITHUB_LINK}",
        "=" * 40,
        f"系统时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"设备序列号: {serial}",
        f"导出路径: {export_path}",
        "",
        "统计:",
        f"  总数: {total_log_types} 项",
        f"  成功拉取文件: {total_files_pulled} 项",
        f"  成功拉取空目录: {total_empty_pulled} 项",
        f"  硬失败: {total_fail} 项",
        "",
        "详细结果:",
    ]
    for log_type, status, _ in all_results:
        report_content.append(f"  - {log_type}: {status}")

    report_file = export_path / "Report.txt"
    report_file.write_text('\n'.join(report_content), encoding='utf-8')
    console.print(f"\n[bold green]报告已保存至:[/bold green] {report_file.name}")

    # 不再在此处打开文件夹，移到 main 函数最后一步执行


def count_remote_files(serial: str, remote_path: str) -> int:
    """统计远程目录下的文件数量。返回 -1 表示权限问题或路径不存在。"""
    # 使用 find 结合 wc -l，排除目录和本身
    # find /path -type f | wc -l
    count_cmd = ["shell", f"find {remote_path} -type f | wc -l"]
    output = run_adb_command(count_cmd, serial=serial, check_output=True)
    try:
        # 清理输出中的 /r 字符，并尝试转换为整数
        return int(output.strip().split()[-1])
    except (ValueError, IndexError):
        return -1 # 表示权限问题、路径不存在或命令执行失败

def prompt_and_clear_logcat(serial: str):
    """在日志收集完成后，询问用户是否清除 logcat 日志，并增强确认机制。"""

    # logcat 的远程路径：/mnt/sdcard/AdayoLog/logcat
    logcat_path = Path(REMOTE_LOG_PATH) / "logcat"

    # 修复 TypeError：将字符串和 Rule 分开打印
    console.print("\n")
    console.print(Rule("[bold white on red]===== 日志清理操作 (Logcat) =====[/bold white on red]", style="bold red"))

    # 1. 统计清理前的数量
    files_before = count_remote_files(serial, str(logcat_path))

    if files_before > 0:
        console.print(f"[bold yellow]当前状态:[/bold yellow] 目标目录 [cyan]{logcat_path}[/cyan] 包含 [bold]{files_before}[/bold] 个文件。")
    elif files_before == 0:
        console.print(f"[bold green]当前状态:[/bold green] 目标目录 [cyan]{logcat_path}[/cyan] 为空，无需清理。")
        console.print(Rule(style="bold red"))
        return
    else:
        console.print(f"[bold red]错误:[/bold red] 无法访问或统计 {logcat_path} 目录，可能存在权限问题。")
        console.print(Rule(style="bold red"))
        return

    # 2. 询问并执行清理
    while True:
        choice = console.input(
            f"[bold yellow]❓ 警告:[/bold yellow] 日志已拉取完成。是否需要清除设备上的 {logcat_path} 日志内容？ (y/n): "
        ).strip().lower()

        if choice == 'y':
            console.print("[bold cyan]-> 确认清除操作，正在执行 ADB Shell 命令...[/bold cyan]")
            # 核心清除命令：只清除内容，保留目录本身
            clear_cmd = ["shell", f"rm -rf {logcat_path}/*"]

            # 尝试执行清除命令 (同步操作)
            success = run_adb_command(clear_cmd, serial=serial)

            if success:
                # 3. 统计清理后的数量
                files_after = count_remote_files(serial, str(logcat_path))

                if files_after == 0:
                     console.print(f"[bold green]✅ 清除成功:[/bold green] 设备上的 {logcat_path} 内容已清空。([dim]原 {files_before} 个文件，现 0 个[/dim])")
                elif files_after > 0:
                    console.print(f"[bold yellow]⚠️ 清除警告:[/bold yellow] 清除命令执行成功，但目录中仍有 [bold]{files_after}[/bold] 个文件残留。")
                elif files_after == -1:
                    console.print(f"[bold red]❌ 清除失败:[/bold red] 无法再次访问目录确认清理结果。")

            else:
                console.print(f"[bold red]❌ 清除失败:[/bold red] 无法执行清理命令。请检查 Root 权限。")
            break

        elif choice == 'n':
            console.print("[bold green]👍 操作跳过:[/bold green] 已保留设备上的 logcat 日志。")
            break
        else:
            console.print("[bold red]输入错误。[/bold red] 请输入 'y' 或 'n'。")

    console.print(Rule(style="bold red"))

def open_export_folder(export_path: Path, total_files_pulled: int):
    """在 Windows 系统中打开日志导出文件夹。"""

    if total_files_pulled > 0 and sys.platform == "win32":
        console.print("\n[bold cyan]正在自动打开日志导出文件夹...[/bold cyan]")
        try:
             subprocess.Popen(['explorer', str(export_path)])
        except FileNotFoundError:
             console.print("[bold yellow]警告:[/bold yellow] 无法打开资源管理器。请手动检查路径。")


# ========================================
# 3. 主程序入口
# ========================================

def main():
    """主函数"""

    # V20.0.0 UI/UX 增强版
    console.print(Rule(f"[bold magenta on white] {TOOL_NAME} [/] [dim]v{VERSION}[/]", style="bold magenta"))
    console.print(Panel(
        f"[bold green]工程师:[/bold green] [cyan]{AUTHOR}[/cyan]\n[italic yellow]GitHub/Gitee Feature: {GITHUB_LINK}[/italic yellow]",
        title="[bold blue]定制化标识[/bold blue]",
        border_style="cyan"
    ))

    # 初始化结果列表
    all_results = []

    # 1. 检查设备
    serial = check_and_get_device()

    # 1.5. 尝试 Root 和 Remount (ANR 和 Bluetooth 文件都在 /data 分区，强烈需要 Root 权限)
    root_device(serial)

    # 2. 获取时间戳和路径
    timestamp, time_source, export_path = get_timestamp_and_path(serial)

    # --- 任务 1: 拉取 Adayo Log ---
    files_pulled_count_adayo, empty_pulled_count_adayo, fail_count_adayo, adayo_results = pull_logs(serial, export_path)
    all_results.extend(adayo_results)

    # --- 任务 2: 拉取 WLAN Log (/data/vendor/wifi/wlan_logs) ---
    wlan_log_type, wlan_log_status, status_type_wlan, wlan_files_pulled, wlan_fail = pull_wlan_logs(serial, export_path)
    all_results.append((wlan_log_type, wlan_log_status, status_type_wlan))

    # --- 任务 3: 拉取 ANR Log (/data/anr) ---
    anr_log_type, anr_log_status, status_type_anr, anr_files_pulled, anr_fail = pull_special_logs(
        serial, export_path, ANR_LOG_TYPE, ANR_LOG_PATH, "4.1" # 使用 4.1 作为子步骤
    )
    all_results.append((anr_log_type, anr_log_status, status_type_anr))

    # --- 任务 4: 拉取 Bluetooth Log (/data/misc/bluetooth/logs) ---
    bt_log_type, bt_log_status, status_type_bt, bt_files_pulled, bt_fail = pull_special_logs(
        serial, export_path, BTSNOOP_LOG_TYPE, BTSNOOP_LOG_PATH, "4.2" # 使用 4.2 作为子步骤
    )
    all_results.append((bt_log_type, bt_log_status, status_type_bt))


    # 整合总计数
    total_files_pulled = files_pulled_count_adayo + wlan_files_pulled + anr_files_pulled + bt_files_pulled
    total_fail = fail_count_adayo + wlan_fail + anr_fail + bt_fail

    # 计算空目录数
    total_empty_pulled = empty_pulled_count_adayo
    if status_type_wlan == "EMPTY":
        total_empty_pulled += 1
    if status_type_anr == "EMPTY":
        total_empty_pulled += 1
    if status_type_bt == "EMPTY":
        total_empty_pulled += 1

    # 5. 生成报告和总结
    generate_report_and_summary(timestamp, time_source, export_path, serial, total_files_pulled, total_empty_pulled, total_fail, all_results)

    # 6. 提示用户是否清除 logcat 日志
    if "logcat" in LOG_TYPES:
        prompt_and_clear_logcat(serial)

    # 7. 弹出文件夹（最终步骤）
    open_export_folder(export_path, total_files_pulled)

    console.print("\n")
    console.print(Rule(style="bold blue"))

    if total_files_pulled == 0:
        if total_empty_pulled > 0 and total_fail == 0:
            console.print("\n[bold yellow]警告:[/bold yellow] 脚本运行成功，所有目标目录均已访问，但**全部为空**。")
        else:
            console.print("\n[bold red]任务失败:[/bold red] 未拉取到任何日志文件。")
        sys.exit(1)
    else:
        sys.exit(0)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[bold red]操作被用户取消。[/bold red]")
        sys.exit(1)