import os
import re
import time
import subprocess
from concurrent.futures import ThreadPoolExecutor
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.console import Console

class IVIIndustrialMonitor:
    def __init__(self, whitelist_path="whitelist.txt"):
        self.console = Console()
        self.device_id = self._get_device()
        self.whitelist = self._load_whitelist(whitelist_path)
        # 初始化数据模型
        self.metrics = {
            "system": {"load": ("0.00", "0.00", "0.00"), "mem_pct": 0, "mem_raw": "0/0", "storage": "N/A"},
            "apps": []
        }

    def _load_whitelist(self, path):
        """专业解析：清洗包名，确保不含源标记"""
        if not os.path.exists(path): return []
        with open(path, 'r', encoding='utf-8') as f:
            # 这里的正则专门过滤 这种杂质
            return [re.search(r'([a-zA-Z0-9._]+)$', line.strip()).group(1)
                    for line in f if re.search(r'([a-zA-Z0-9._]+)$', line.strip())]

    def _get_device(self):
        res = subprocess.run("adb devices", shell=True, capture_output=True, text=True)
        devices = re.findall(r'^(\S+)\tdevice', res.stdout, re.MULTILINE)
        return devices[0] if devices else None

    def _adb_shell(self, cmd):
        """通过 ADB 执行命令，支持 Root 自动检测"""
        if not self.device_id: return ""
        # 尝试使用 su -c 以获得最高权限输出
        full_cmd = f"adb -s {self.device_id} shell \"{cmd}\""
        try:
            res = subprocess.run(full_cmd, shell=True, capture_output=True, text=True, timeout=4)
            return res.stdout
        except:
            return ""

    def collect_all_data(self):
        """工业级一键采集：聚合 top 命令以提高性能"""
        # 1. 采集系统负载
        uptime = self._adb_shell("uptime")
        load = re.search(r"average:\s+([\d.]+),?\s+([\d.]+),?\s+([\d.]+)", uptime)
        if load: self.metrics["system"]["load"] = load.groups()

        # 2. 采集存储
        df = self._adb_shell("df -h /data")
        storage = re.search(r"(\d+)%", df)
        if storage: self.metrics["system"]["storage"] = f"{storage.group(1)}%"

        # 3. 核心：通过 top 一次性抓取所有进程数据 (非常快)
        # -b: 批处理模式, -n 1: 刷新一次, -o: 自定义输出字段 (RES是物理内存, CPU是占用率)
        top_data = self._adb_shell("top -b -n 1")

        # 4. 解析系统总内存 (从 top 头部获取)
        mem_line = re.search(r"Mem:\s+([\d,]+)K total,\s+([\d,]+)K used", top_data)
        if mem_line:
            total_k = int(mem_line.group(1).replace(',', ''))
            used_k = int(mem_line.group(2).replace(',', ''))
            self.metrics["system"]["mem_pct"] = round((used_k / total_k) * 100, 1)
            self.metrics["system"]["mem_raw"] = f"{used_k//1024}/{total_k//1024} MB"

        # 5. 匹配白名单进程
        app_list = []
        for pkg in self.whitelist:
            # 寻找匹配包名的行 (top 输出通常包含包名或进程名)
            # 修正正则以适应 8155 的输出格式
            pattern = fr"\s*(\d+)\s+.*?\s+([\d,.]+[MG]?)\s+.*?\s+(\d+[.]?\d*)\s+.*?\s+{re.escape(pkg)}"
            match = re.search(pattern, top_data)
            if match:
                res_mem = match.group(2)
                cpu_val = match.group(3)
                # 转换内存单位 (如果是 'G' 或 'M')
                mem_mb = self._parse_mem_to_mb(res_mem)
                app_list.append({"pkg": pkg, "cpu": f"{cpu_val}%", "mem": f"{mem_mb} MB"})

        self.metrics["apps"] = sorted(app_list, key=lambda x: float(x['mem'].split()[0]), reverse=True)

    def _parse_mem_to_mb(self, mem_str):
        """将 top 的内存字符串 (如 1.2G, 500M, 123456) 统一转换为 MB"""
        try:
            mem_str = mem_str.replace(',', '')
            if 'G' in mem_str: return round(float(mem_str.replace('G', '')) * 1024, 1)
            if 'M' in mem_str: return round(float(mem_str.replace('M', '')), 1)
            return round(float(mem_str) / 1024, 1) # 默认是 KB
        except:
            return 0.0

    def generate_dashboard(self):
        layout = Layout()
        layout.split_column(Layout(name="header", size=3), Layout(name="main", ratio=1))
        layout["main"].split_row(Layout(name="sys", ratio=1), Layout(name="app", ratio=2.5))

        # Header
        layout["header"].update(Panel(f"[bold cyan]IVI INDUSTRIAL MONITOR[/] | Device: [green]{self.device_id}[/] | {time.strftime('%H:%M:%S')}", border_style="cyan"))

        # Sys Info
        sys = self.metrics["system"]
        l1, l5, l15 = sys["load"]
        sys_table = Table(show_header=False, box=None)
        sys_table.add_row("CPU Load:", f"[bold yellow]{l1}[/] [dim]/ {l5} / {l15}[/]")
        sys_table.add_row("RAM Usage:", f"[bold]{sys['mem_pct']}%[/] ({sys['mem_raw']})")
        sys_table.add_row("Storage:", f"[bold magenta]{sys['storage']}[/]")
        layout["sys"].update(Panel(sys_table, title="System Health", border_style="blue"))

        # App Info
        app_table = Table(title=f"Monitoring {len(self.metrics['apps'])} Active Processes (from Whitelist)", expand=True)
        app_table.add_column("Package Name", style="cyan", ratio=3)
        app_table.add_column("CPU %", style="green", justify="right", ratio=1)
        app_table.add_column("Memory (RES)", style="magenta", justify="right", ratio=1.5)

        for app in self.metrics["apps"][:18]:
            app_table.add_row(app["pkg"], app["cpu"], app["mem"])

        # 如果列表为空，显示提示
        if not self.metrics["apps"]:
            app_table.add_row("[yellow]Searching for whitelisted apps...[/]", "-", "-")

        layout["app"].update(Panel(app_table, border_style="green"))
        return layout

    def run(self):
        with Live(self.generate_dashboard(), refresh_per_second=1, screen=True) as live:
            while True:
                self.collect_all_data()
                live.update(self.generate_dashboard())
                time.sleep(1)

if __name__ == "__main__":
    IVIIndustrialMonitor().run()