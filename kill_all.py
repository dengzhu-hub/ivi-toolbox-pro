"""
进程清理与关机助手 v2.0
功能：安全地终止指定进程并提供关机选项
作者：System Administrator
最后更新：2025-01-19
"""

import os
import sys
import psutil
import tkinter as tk
from tkinter import messagebox, scrolledtext
from datetime import datetime
import logging
from pathlib import Path
import json
from typing import List, Dict, Set
import time


class ProcessCleaner:
    """专业的进程清理管理器"""

    def __init__(self, config_file: str = "process_cleaner_config.json"):
        """初始化清理器"""
        self.config_file = config_file
        self.log_dir = Path("logs")
        self.log_dir.mkdir(exist_ok=True)

        # 初始化日志
        self._setup_logging()

        # 加载配置
        self.config = self._load_config()

        # 统计信息
        self.stats = {
            'attempted': 0,
            'succeeded': 0,
            'failed': 0,
            'access_denied': 0,
            'not_found': 0
        }

    def _setup_logging(self):
        """配置日志系统"""
        log_file = self.log_dir / f"cleaner_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file, encoding='utf-8'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        self.logger.info("=" * 60)
        self.logger.info("进程清理助手启动")
        self.logger.info("=" * 60)

    def _load_config(self) -> Dict:
        """加载配置文件"""
        default_config = {
            "target_processes": [
                # 截图与效率工具
                "Snipaste.exe",
                "ShareX.exe",
                "Listary.exe",
                "PicGo.exe",
                "Everything.exe",

                # 工业与开发软件
                "TSMaster64.exe",
                "Code.exe",

                # 办公软件
                "WINWORD.EXE",
                "EXCEL.EXE",
                "POWERPNT.EXE",
                "Outlook.exe",

                # 浏览器与通讯
                "chrome.exe",
                "msedge.exe",
                "WXWork.exe",
                "Clash for Windows.exe",

                # 其他驻留进程
                "lghub.exe",
                "Vantage.exe",
                "Eraser.exe"
            ],
            "protected_processes": [
                # 系统关键进程（永不终止）
                "explorer.exe",
                "winlogon.exe",
                "csrss.exe",
                "smss.exe",
                "services.exe",
                "lsass.exe",
                "svchost.exe",
                "python.exe",  # 保护自己
                "pythonw.exe"
            ],
            "retry_count": 3,
            "retry_delay": 0.5,
            "force_kill_timeout": 5,
            "enable_backup_cleanup": True
        }

        try:
            if Path(self.config_file).exists():
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    loaded_config = json.load(f)
                    default_config.update(loaded_config)
                    self.logger.info(f"配置文件加载成功: {self.config_file}")
            else:
                # 创建默认配置文件
                with open(self.config_file, 'w', encoding='utf-8') as f:
                    json.dump(default_config, f, indent=4, ensure_ascii=False)
                self.logger.info(f"创建默认配置文件: {self.config_file}")
        except Exception as e:
            self.logger.error(f"配置文件加载失败: {e}，使用默认配置")

        return default_config

    def _is_protected_process(self, process_name: str) -> bool:
        """检查是否为受保护进程"""
        protected = self.config.get('protected_processes', [])
        return process_name.lower() in [p.lower() for p in protected]

    def _kill_process_safe(self, proc: psutil.Process) -> tuple[bool, str]:
        """安全终止进程"""
        try:
            proc_name = proc.name()
            proc_pid = proc.pid

            # 双重保护检查
            if self._is_protected_process(proc_name):
                msg = f"跳过受保护进程: {proc_name} (PID: {proc_pid})"
                self.logger.warning(msg)
                return False, msg

            # 尝试优雅终止
            try:
                proc.terminate()
                proc.wait(timeout=self.config.get('force_kill_timeout', 5))
                msg = f"✓ 优雅终止: {proc_name} (PID: {proc_pid})"
                self.logger.info(msg)
                return True, msg
            except psutil.TimeoutExpired:
                # 强制终止
                proc.kill()
                msg = f"⚡ 强制终止: {proc_name} (PID: {proc_pid})"
                self.logger.warning(msg)
                return True, msg

        except psutil.NoSuchProcess:
            msg = f"进程已不存在: {proc_name}"
            self.logger.debug(msg)
            self.stats['not_found'] += 1
            return False, msg

        except psutil.AccessDenied:
            msg = f"✗ 权限不足: {proc_name} (PID: {proc_pid})"
            self.logger.error(msg)
            self.stats['access_denied'] += 1
            return False, msg

        except Exception as e:
            msg = f"✗ 终止失败: {proc_name} - {str(e)}"
            self.logger.error(msg)
            return False, msg

    def clean_processes(self) -> Dict[str, List[str]]:
        """执行进程清理"""
        self.logger.info("开始扫描目标进程...")

        target_list = self.config.get('target_processes', [])
        target_lower = [name.lower() for name in target_list]

        results = {
            'killed': [],
            'failed': [],
            'skipped': []
        }

        # 收集所有匹配的进程
        matched_processes = []
        for proc in psutil.process_iter(['name', 'pid']):
            try:
                proc_name = proc.info['name']
                if proc_name.lower() in target_lower:
                    matched_processes.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        self.logger.info(f"发现 {len(matched_processes)} 个目标进程")

        # 终止进程
        for proc in matched_processes:
            self.stats['attempted'] += 1
            success, msg = self._kill_process_safe(proc)

            try:
                proc_name = proc.name()
                if success:
                    results['killed'].append(f"{proc_name} (PID: {proc.pid})")
                    self.stats['succeeded'] += 1
                else:
                    results['failed'].append(f"{proc_name} (PID: {proc.pid})")
                    self.stats['failed'] += 1
            except:
                pass

        # 记录统计
        self.logger.info("=" * 60)
        self.logger.info("清理完成统计:")
        self.logger.info(f"  尝试终止: {self.stats['attempted']}")
        self.logger.info(f"  成功终止: {self.stats['succeeded']}")
        self.logger.info(f"  终止失败: {self.stats['failed']}")
        self.logger.info(f"  权限不足: {self.stats['access_denied']}")
        self.logger.info("=" * 60)

        return results


class CleanerGUI:
    """图形用户界面"""

    def __init__(self, cleaner: ProcessCleaner):
        self.cleaner = cleaner
        self.root = tk.Tk()
        self.root.title("🔧 进程清理与关机助手 v2.0")
        self.root.geometry("600x500")
        self.root.resizable(False, False)

        # 置顶窗口
        self.root.attributes("-topmost", True)

        # 居中显示
        self._center_window()

        # 创建界面
        self._create_widgets()

    def _center_window(self):
        """窗口居中"""
        self.root.update_idletasks()
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        x = (self.root.winfo_screenwidth() // 2) - (width // 2)
        y = (self.root.winfo_screenheight() // 2) - (height // 2)
        self.root.geometry(f'{width}x{height}+{x}+{y}')

    def _create_widgets(self):
        """创建界面组件"""
        # 标题
        title_frame = tk.Frame(self.root, bg="#2c3e50", height=60)
        title_frame.pack(fill=tk.X)
        title_frame.pack_propagate(False)

        title_label = tk.Label(
            title_frame,
            text="🔧 进程清理与关机助手",
            font=("Microsoft YaHei UI", 16, "bold"),
            bg="#2c3e50",
            fg="white"
        )
        title_label.pack(pady=15)

        # 日志区域
        log_frame = tk.LabelFrame(
            self.root,
            text="📋 执行日志",
            font=("Microsoft YaHei UI", 10),
            padx=10,
            pady=10
        )
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            font=("Consolas", 9),
            bg="#f8f9fa",
            fg="#2c3e50",
            wrap=tk.WORD,
            state=tk.DISABLED
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # 按钮区域
        button_frame = tk.Frame(self.root)
        button_frame.pack(fill=tk.X, padx=10, pady=10)

        self.clean_btn = tk.Button(
            button_frame,
            text="🚀 开始清理",
            font=("Microsoft YaHei UI", 11, "bold"),
            bg="#3498db",
            fg="white",
            padx=20,
            pady=10,
            command=self._execute_clean,
            cursor="hand2"
        )
        self.clean_btn.pack(side=tk.LEFT, padx=5)

        self.shutdown_btn = tk.Button(
            button_frame,
            text="⚡ 清理并关机",
            font=("Microsoft YaHei UI", 11, "bold"),
            bg="#e74c3c",
            fg="white",
            padx=20,
            pady=10,
            command=self._clean_and_shutdown,
            cursor="hand2"
        )
        self.shutdown_btn.pack(side=tk.LEFT, padx=5)

        self.exit_btn = tk.Button(
            button_frame,
            text="❌ 退出",
            font=("Microsoft YaHei UI", 11),
            bg="#95a5a6",
            fg="white",
            padx=20,
            pady=10,
            command=self.root.quit,
            cursor="hand2"
        )
        self.exit_btn.pack(side=tk.RIGHT, padx=5)

    def _log(self, message: str):
        """添加日志"""
        self.log_text.config(state=tk.NORMAL)
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)
        self.root.update()

    def _execute_clean(self):
        """执行清理"""
        self._log("=" * 50)
        self._log("开始执行清理任务...")

        # 禁用按钮
        self.clean_btn.config(state=tk.DISABLED)
        self.shutdown_btn.config(state=tk.DISABLED)

        try:
            results = self.cleaner.clean_processes()

            # 显示结果
            if results['killed']:
                self._log(f"\n✅ 成功终止 {len(results['killed'])} 个进程:")
                for app in results['killed']:
                    self._log(f"   • {app}")

            if results['failed']:
                self._log(f"\n⚠️ 终止失败 {len(results['failed'])} 个进程:")
                for app in results['failed']:
                    self._log(f"   • {app}")

            if not results['killed'] and not results['failed']:
                self._log("\n✅ 系统环境纯净，未发现目标进程")

            self._log("\n清理任务完成！")

        except Exception as e:
            self._log(f"\n❌ 清理过程出错: {str(e)}")
            self.cleaner.logger.exception("清理过程异常")

        finally:
            # 重新启用按钮
            self.clean_btn.config(state=tk.NORMAL)
            self.shutdown_btn.config(state=tk.NORMAL)
            self._log("=" * 50)

    def _clean_and_shutdown(self):
        """清理并关机"""
        # 确认对话框
        response = messagebox.askyesno(
            "⚠️ 关机确认",
            "即将执行以下操作：\n\n"
            "1. 清理所有目标进程\n"
            "2. 强制关闭系统\n\n"
            "确定要继续吗？\n\n"
            "【此操作不可撤销】",
            icon='warning'
        )

        if not response:
            self._log("用户取消了关机操作")
            return

        # 执行清理
        self._execute_clean()

        # 倒计时
        self._log("\n⏰ 系统将在 5 秒后关机...")
        for i in range(5, 0, -1):
            self._log(f"   {i}...")
            time.sleep(1)

        self._log("\n🔌 正在关机...")

        try:
            # 强制关机
            os.system("shutdown /s /f /t 0")
        except Exception as e:
            self._log(f"❌ 关机命令执行失败: {str(e)}")
            messagebox.showerror("错误", f"关机失败：{str(e)}")

    def run(self):
        """运行界面"""
        self._log("系统就绪，请选择操作...")
        self._log(f"配置文件: {self.cleaner.config_file}")
        self._log(f"日志目录: {self.cleaner.log_dir}")
        self.root.mainloop()


def check_admin_privileges() -> bool:
    """检查管理员权限"""
    try:
        return os.getuid() == 0
    except AttributeError:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin() != 0


def main():
    """主函数"""
    # 检查权限
    if not check_admin_privileges():
        print("⚠️ 警告: 建议以管理员权限运行以获得最佳效果")
        print("某些系统进程可能需要管理员权限才能终止\n")

    try:
        # 创建清理器
        cleaner = ProcessCleaner()

        # 创建并运行GUI
        gui = CleanerGUI(cleaner)
        gui.run()

    except KeyboardInterrupt:
        print("\n\n用户中断操作")
        sys.exit(0)
    except Exception as e:
        logging.exception("程序运行异常")
        messagebox.showerror("严重错误", f"程序遇到未处理的异常：\n\n{str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()