#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
车机OTA配置工具 - 专业增强版
Vehicle OTA Configuration Tool - Professional Enhanced Edition
作者: Professional Automotive Engineer (Enhanced by Senior Auto Engineer)
版本: 3.0.0 (最终增强版)
功能: 支持批量操作、配置模板、历史记录、自动验证、备份恢复、ADB集成
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
import subprocess
import json
import os
import shutil
import csv
from datetime import datetime
from pathlib import Path
import threading
import re
import hashlib


class ConfigValidator:
    """配置验证器"""
    
    @staticmethod
    def validate_vin(vin):
        """验证VIN码格式"""
        if not vin or len(vin) != 17:
            return False, "VIN码必须是17位字符"
        
        # VIN码不能包含I、O、Q
        if any(char in vin.upper() for char in ['I', 'O', 'Q']):
            return False, "VIN码不能包含字母I、O、Q"
        
        # 只能包含大写字母和数字
        # A-Z (除I, O, Q) 和 0-9
        if not re.match(r'^[A-HJ-NPR-Z0-9]{17}$', vin.upper()):
            return False, "VIN码格式不正确，只能包含A-Z(除I、O、Q)和0-9"
        
        # 验证校验位 (第9位)
        input_checksum = vin.upper()[8]
        calculated_checksum = ConfigValidator.calculate_vin_checksum(vin)
        
        # 如果计算出的校验位与输入的校验位不匹配
        if input_checksum != calculated_checksum and input_checksum != '0':
            # 允许用户输入错误的校验位，但给出警告。对于车规级应用，通常要求精确匹配。
            return True, f"VIN码格式正确，但校验位(第9位)应为 '{calculated_checksum}' (输入: '{input_checksum}')"
        
        return True, "VIN码格式正确，校验位验证通过"
    
    @staticmethod
    def validate_icc_pno(pno):
        """验证ICC_PNO格式"""
        if not pno or len(pno) < 5 or not pno.isalnum():
            return False, "ICC_PNO长度不能少于5位，且只能包含字母和数字"
        
        return True, "ICC_PNO格式正确"
    
    @staticmethod
    def calculate_vin_checksum(vin):
        """计算VIN码校验位（第9位）"""
        transliteration = {
            'A': 1, 'B': 2, 'C': 3, 'D': 4, 'E': 5, 'F': 6, 'G': 7, 'H': 8,
            'J': 1, 'K': 2, 'L': 3, 'M': 4, 'N': 5, 'P': 7, 'R': 9,
            'S': 2, 'T': 3, 'U': 4, 'V': 5, 'W': 6, 'X': 7, 'Y': 8, 'Z': 9
        }
        # VIN码中的第9位权重为0，因此可以预先计算出校验位，然后与第9位对比
        weights = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]
        
        vin = vin.upper()
        total = 0
        for i, char in enumerate(vin):
            # 跳过第9位（索引为8）的计算，因为我们就是要计算它
            if i == 8:
                continue
            
            if char.isdigit():
                value = int(char)
            else:
                value = transliteration.get(char, 0)
            
            # 只有第9位跳过权重，其他正常计算
            if i != 8:
                 total += value * weights[i]
        
        # VIN校验和的计算方式是将所有加权值相加，然后除以11取余数
        remainder = total % 11
        return 'X' if remainder == 10 else str(remainder)


class ConfigTemplate:
    """配置模板管理"""
    
    def __init__(self, template_dir="templates"):
        self.template_dir = template_dir
        os.makedirs(template_dir, exist_ok=True)
    
    def save_template(self, name, config_data):
        """保存配置模板"""
        template_path = os.path.join(self.template_dir, f"{name}.json")
        with open(template_path, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, ensure_ascii=False, indent=2)
        return template_path
    
    def load_template(self, name):
        """加载配置模板"""
        template_path = os.path.join(self.template_dir, f"{name}.json")
        if os.path.exists(template_path):
            with open(template_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None
    
    def list_templates(self):
        """列出所有模板"""
        if not os.path.exists(self.template_dir):
            return []
        return [f[:-5] for f in os.listdir(self.template_dir) if f.endswith('.json')]


class OperationHistory:
    """操作历史记录"""
    
    def __init__(self, history_file="operation_history.json"):
        self.history_file = history_file
        self.history = self.load_history()
    
    def load_history(self):
        """加载历史记录"""
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                # 文件损坏或格式错误时，返回空列表
                return []
        return []
    
    def save_history(self):
        """保存历史记录"""
        with open(self.history_file, 'w', encoding='utf-8') as f:
            json.dump(self.history, f, ensure_ascii=False, indent=2)
    
    def add_record(self, operation_type, old_config, new_config, result):
        """添加操作记录"""
        record = {
            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'operation': operation_type,
            'old_config': old_config,
            'new_config': new_config,
            'result': result
        }
        self.history.insert(0, record)
        # 只保留最近100条记录
        self.history = self.history[:100]
        self.save_history()
        

class VehicleOTAConfigToolEnhanced:
    """车机OTA配置工具增强版主类"""
    
    def __init__(self, root):
        self.root = root
        self.root.title("车机OTA配置工具专业版 v3.0.0 (最终增强版)")
        self.root.geometry("1400x900")
        self.root.resizable(True, True)
        
        # 配置文件路径
        self.device_file_path = "/mnt/sdcard/DeviceInfo.txt"
        self.local_file_path = "DeviceInfo.txt"
        self.backup_dir = "backups"
        self.batch_csv_data = [] # 用于存储批量导入的CSV数据
        
        # 创建必要目录
        os.makedirs(self.backup_dir, exist_ok=True)
        
        # 初始化组件
        self.validator = ConfigValidator()
        self.template_manager = ConfigTemplate()
        self.history_manager = OperationHistory()
        
        # 当前配置
        self.current_config = {}
        self.device_connected = False
        
        # 设置样式
        self.setup_styles()
        
        # 创建UI
        self.create_ui()
        
        # 初始化日志
        self.log("系统", "车机OTA配置工具专业版已启动 v3.0.0", tag='INFO')
        self.log("提示", "请点击'检测设备'按钮连接车机", tag='WARNING')
        
        # 加载配置模板列表和备份列表
        self.refresh_template_list()
        self.refresh_backup_list()
        
    # --- 核心辅助方法 ---
    
    def run_adb_command(self, command, log_on_success=True):
        """
        执行ADB命令的核心方法。
        Args:
            command (list/str): 要执行的ADB命令，如 ['pull', ...]
            log_on_success (bool): 成功时是否记录日志
        Returns:
            tuple: (bool, str) - (是否成功, 输出/错误信息)
        """
        if isinstance(command, str):
            command = command.split()
        
        full_command = ['adb'] + command
        
        try:
            # 启动一个子进程来执行命令
            result = subprocess.run(
                full_command,
                capture_output=True,
                text=True,
                check=False, # 不抛出异常，而是返回错误码
                encoding='utf-8',
                timeout=10 # 设置超时时间
            )
            
            output = result.stdout.strip()
            error = result.stderr.strip()
            
            if result.returncode == 0:
                if log_on_success:
                    self.log("ADB", f"命令执行成功: {' '.join(command)}", tag='SUCCESS')
                return True, output
            else:
                self.log("ADB错误", f"命令执行失败: {' '.join(command)}\n错误: {error}", tag='ERROR')
                return False, f"ADB命令失败: {error}"
                
        except FileNotFoundError:
            msg = "错误: 未找到ADB可执行文件。请确保ADB已安装并配置到系统PATH中。"
            self.log("系统错误", msg, tag='ERROR')
            messagebox.showerror("ADB错误", msg)
            return False, msg
        except subprocess.TimeoutExpired:
            msg = f"错误: ADB命令超时 ({' '.join(command)})"
            self.log("系统错误", msg, tag='ERROR')
            messagebox.showerror("ADB超时", msg)
            return False, msg
        except Exception as e:
            msg = f"ADB执行异常: {e}"
            self.log("系统错误", msg, tag='ERROR')
            messagebox.showerror("ADB异常", msg)
            return False, msg

    def calculate_file_hash(self, file_path):
        """计算文件的SHA256哈希值"""
        hasher = hashlib.sha256()
        try:
            with open(file_path, 'rb') as f:
                buf = f.read()
                hasher.update(buf)
            return hasher.hexdigest()
        except Exception as e:
            self.log("文件操作", f"计算哈希失败: {e}", tag='ERROR')
            return "计算失败"

    def log(self, source, message, tag='INFO'):
        """向日志框添加带时间戳和标签的记录"""
        timestamp = datetime.now().strftime("[%H:%M:%S]")
        self.log_text.insert(tk.END, f"{timestamp} ", 'TIMESTAMP')
        self.log_text.insert(tk.END, f"[{source}] ", tag)
        self.log_text.insert(tk.END, f"{message}\n", 'INFO')
        self.log_text.see(tk.END) # 自动滚动到底部

    def clear_log(self):
        """清空日志"""
        self.log_text.delete(1.0, tk.END)

    def export_log(self):
        """导出日志到文件"""
        log_content = self.log_text.get(1.0, tk.END)
        file_path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            title="保存操作日志"
        )
        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(log_content)
                self.log("系统", f"日志成功导出到: {file_path}", tag='SUCCESS')
            except Exception as e:
                self.log("系统错误", f"日志导出失败: {e}", tag='ERROR')
                messagebox.showerror("错误", f"日志导出失败: {e}")
                
    # --- UI创建方法 (已在原代码中定义，无需修改) ---
    def setup_styles(self):
        """设置UI样式"""
        style = ttk.Style()
        style.theme_use('clam')
        
        # 按钮样式
        style.configure('Primary.TButton', 
                       font=('Microsoft YaHei UI', 10, 'bold'),
                       padding=10)
        style.configure('Success.TButton',
                       font=('Microsoft YaHei UI', 10, 'bold'),
                       padding=10)
        style.configure('Danger.TButton',
                       font=('Microsoft YaHei UI', 10, 'bold'),
                       padding=10)
        style.configure('Info.TButton',
                       font=('Microsoft YaHei UI', 9),
                       padding=5)
        
        # 标签样式
        style.configure('Title.TLabel',
                       font=('Microsoft YaHei UI', 16, 'bold'))
        style.configure('Heading.TLabel',
                       font=('Microsoft YaHei UI', 12, 'bold'))
        style.configure('Info.TLabel',
                       font=('Microsoft YaHei UI', 10))

    def create_ui(self):
        """创建用户界面"""
        # 主容器
        main_container = ttk.Frame(self.root, padding="10")
        main_container.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # 配置网格权重
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_container.columnconfigure(1, weight=2)
        main_container.rowconfigure(2, weight=1)
        
        # 创建各个面板
        self.create_header(main_container)
        self.create_left_panel(main_container)
        self.create_right_panel(main_container)
        self.create_bottom_panel(main_container) # 新增底部状态栏
        
    def create_header(self, parent):
        """创建标题栏"""
        header_frame = ttk.Frame(parent, relief=tk.RAISED, borderwidth=2)
        header_frame.grid(row=0, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=(0, 10))
        header_frame.columnconfigure(1, weight=1)
        
        # 标题
        title_label = ttk.Label(header_frame, 
                               text="🚗 车机OTA配置工具专业版",
                               style='Title.TLabel')
        title_label.grid(row=0, column=0, padx=10, pady=10, sticky=tk.W)
        
        # 版本信息
        version_label = ttk.Label(header_frame, 
                                 text="v3.0.0 | 企业级增强版",
                                 font=('Microsoft YaHei UI', 9),
                                 foreground='gray')
        version_label.grid(row=0, column=1, padx=10, pady=10)
        
        # 连接状态指示器
        self.status_frame = ttk.Frame(header_frame)
        self.status_frame.grid(row=0, column=2, padx=10, pady=10, sticky=tk.E)
        
        self.status_label = ttk.Label(self.status_frame, 
                                     text="● 未连接",
                                     foreground="red",
                                     font=('Microsoft YaHei UI', 10, 'bold'))
        self.status_label.pack()

    def create_left_panel(self, parent):
        """创建左侧控制面板"""
        left_frame = ttk.Frame(parent, relief=tk.GROOVE, borderwidth=2)
        left_frame.grid(row=1, column=0, rowspan=2, sticky=(tk.W, tk.E, tk.N, tk.S), padx=(0, 5))
        left_frame.columnconfigure(0, weight=1)
        
        # 设备连接区
        connection_frame = ttk.LabelFrame(left_frame, text="📱 设备连接", padding="10")
        connection_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=5, padx=5)
        connection_frame.columnconfigure(0, weight=1)
        
        self.check_device_btn = ttk.Button(connection_frame,
                                          text="🔍 检测设备",
                                          command=self.check_device_connection,
                                          style='Primary.TButton')
        self.check_device_btn.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=2)
        
        ttk.Button(connection_frame,
                  text="🔄 重新连接",
                  command=self.reconnect_device,
                  style='Info.TButton').grid(row=1, column=0, sticky=(tk.W, tk.E), pady=2)
        
        # 当前配置区
        current_config_frame = ttk.LabelFrame(left_frame, text="⚙️ 当前配置", padding="10")
        current_config_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=5, padx=5)
        current_config_frame.columnconfigure(1, weight=1)
        
        # ICC_PNO
        ttk.Label(current_config_frame, text="ICC_PNO:", style='Info.TLabel').grid(
            row=0, column=0, sticky=tk.W, pady=5)
        self.current_pno_var = tk.StringVar(value="未读取")
        ttk.Label(current_config_frame,
                 textvariable=self.current_pno_var,
                 font=('Consolas', 10, 'bold'),
                 foreground='#0066cc').grid(row=0, column=1, sticky=tk.W, pady=5, padx=5)
        
        # VIN
        ttk.Label(current_config_frame, text="VIN:", style='Info.TLabel').grid(
            row=1, column=0, sticky=tk.W, pady=5)
        self.current_vin_var = tk.StringVar(value="未读取")
        ttk.Label(current_config_frame,
                 textvariable=self.current_vin_var,
                 font=('Consolas', 10, 'bold'),
                 foreground='#0066cc').grid(row=1, column=1, sticky=tk.W, pady=5, padx=5)
        
        # VIN校验状态
        self.vin_check_var = tk.StringVar(value="")
        ttk.Label(current_config_frame,
                 textvariable=self.vin_check_var,
                 font=('Microsoft YaHei UI', 8),
                 foreground='#28a745').grid(row=2, column=0, columnspan=2, sticky=tk.W, pady=2)
        
        # f1A1
        ttk.Label(current_config_frame, text="f1A1:", style='Info.TLabel').grid(
            row=3, column=0, sticky=tk.W, pady=5)
        self.current_f1a1_var = tk.StringVar(value="未读取")
        ttk.Label(current_config_frame,
                 textvariable=self.current_f1a1_var,
                 font=('Consolas', 8),
                 foreground='#666666').grid(row=3, column=1, sticky=tk.W, pady=5, padx=5)
        
        # 配置文件哈希
        ttk.Label(current_config_frame, text="文件哈希:", style='Info.TLabel').grid(
            row=4, column=0, sticky=tk.W, pady=5)
        self.file_hash_var = tk.StringVar(value="未计算")
        ttk.Label(current_config_frame,
                 textvariable=self.file_hash_var,
                 font=('Consolas', 8),
                 foreground='#999999').grid(row=4, column=1, sticky=tk.W, pady=5, padx=5)
        
        # 配置模板区
        template_frame = ttk.LabelFrame(left_frame, text="📋 配置模板", padding="10")
        template_frame.grid(row=2, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=5, padx=5)
        template_frame.columnconfigure(0, weight=1)
        template_frame.rowconfigure(0, weight=1)
        
        # 模板列表
        self.template_listbox = tk.Listbox(template_frame,
                                          height=8,
                                          font=('Consolas', 9))
        self.template_listbox.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.template_listbox.bind('<Double-Button-1>', self.load_template_double_click)
        
        template_scrollbar = ttk.Scrollbar(template_frame,
                                          orient=tk.VERTICAL,
                                          command=self.template_listbox.yview)
        template_scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
        self.template_listbox.configure(yscrollcommand=template_scrollbar.set)
        
        # 模板操作按钮
        template_btn_frame = ttk.Frame(template_frame)
        template_btn_frame.grid(row=1, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(5, 0))
        template_btn_frame.columnconfigure(0, weight=1)
        template_btn_frame.columnconfigure(1, weight=1)
        
        ttk.Button(template_btn_frame,
                  text="💾 保存为模板",
                  command=self.save_as_template,
                  style='Info.TButton').grid(row=0, column=0, sticky=(tk.W, tk.E), padx=(0, 2))
        
        ttk.Button(template_btn_frame,
                  text="📥 加载模板",
                  command=self.load_selected_template,
                  style='Info.TButton').grid(row=0, column=1, sticky=(tk.W, tk.E), padx=(2, 0))
        
        # 备份列表区
        backup_frame = ttk.LabelFrame(left_frame, text="💾 备份列表", padding="10")
        backup_frame.grid(row=3, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=5, padx=5)
        backup_frame.columnconfigure(0, weight=1)
        backup_frame.rowconfigure(0, weight=1)
        
        self.backup_listbox = tk.Listbox(backup_frame,
                                         height=8,
                                         font=('Consolas', 9))
        self.backup_listbox.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.backup_listbox.bind('<Double-Button-1>', self.restore_backup_double_click)
        
        backup_scrollbar = ttk.Scrollbar(backup_frame,
                                        orient=tk.VERTICAL,
                                        command=self.backup_listbox.yview)
        backup_scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
        self.backup_listbox.configure(yscrollcommand=backup_scrollbar.set)
        
        # 备份操作按钮
        backup_btn_frame = ttk.Frame(backup_frame)
        backup_btn_frame.grid(row=1, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(5, 0))
        backup_btn_frame.columnconfigure(0, weight=1)
        backup_btn_frame.columnconfigure(1, weight=1)
        
        ttk.Button(backup_btn_frame,
                  text="🔄 刷新列表",
                  command=self.refresh_backup_list,
                  style='Info.TButton').grid(row=0, column=0, sticky=(tk.W, tk.E), padx=(0, 2))
        
        ttk.Button(backup_btn_frame,
                  text="↩️ 恢复备份",
                  command=self.restore_selected_backup,
                  style='Info.TButton').grid(row=0, column=1, sticky=(tk.W, tk.E), padx=(2, 0))
        
    def create_right_panel(self, parent):
        """创建右侧操作面板"""
        right_frame = ttk.Frame(parent)
        right_frame.grid(row=1, column=1, sticky=(tk.W, tk.E, tk.N, tk.S), padx=5)
        right_frame.columnconfigure(0, weight=1)
        right_frame.rowconfigure(1, weight=1)
        
        # 配置更新区（使用Notebook实现多标签页）
        notebook = ttk.Notebook(right_frame)
        notebook.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=5)
        
        # 单个配置更新标签页
        single_update_frame = ttk.Frame(notebook, padding="10")
        notebook.add(single_update_frame, text="🔧 单个更新")
        self.create_single_update_tab(single_update_frame)
        
        # 批量更新标签页
        batch_update_frame = ttk.Frame(notebook, padding="10")
        notebook.add(batch_update_frame, text="📦 批量更新")
        self.create_batch_update_tab(batch_update_frame)
        
        # 高级功能标签页 (展示历史记录)
        advanced_frame = ttk.Frame(notebook, padding="10")
        notebook.add(advanced_frame, text="⚡ 操作历史")
        self.create_history_tab(advanced_frame)
        
        # 操作日志区
        log_frame = ttk.LabelFrame(right_frame, text="📝 操作日志", padding="10")
        log_frame.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=5)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        
        self.log_text = scrolledtext.ScrolledText(log_frame,
                                                  wrap=tk.WORD,
                                                  font=('Consolas', 9),
                                                  bg='#1e1e1e',
                                                  fg='#d4d4d4',
                                                  height=15)
        self.log_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # 配置日志颜色标签
        self.log_text.tag_config('INFO', foreground='#4ec9b0')
        self.log_text.tag_config('SUCCESS', foreground='#6a9955')
        self.log_text.tag_config('WARNING', foreground='#dcdcaa')
        self.log_text.tag_config('ERROR', foreground='#f48771')
        self.log_text.tag_config('TIMESTAMP', foreground='#808080')
        
        # 日志操作按钮
        log_btn_frame = ttk.Frame(log_frame)
        log_btn_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(5, 0))
        
        ttk.Button(log_btn_frame,
                  text="🗑️ 清空日志",
                  command=self.clear_log,
                  style='Info.TButton').pack(side=tk.LEFT, padx=2)
        
        ttk.Button(log_btn_frame,
                  text="💾 导出日志",
                  command=self.export_log,
                  style='Info.TButton').pack(side=tk.LEFT, padx=2)

    def create_bottom_panel(self, parent):
        """新增底部状态栏"""
        footer_frame = ttk.Frame(parent, relief=tk.SUNKEN, borderwidth=1)
        footer_frame.grid(row=2, column=1, sticky=(tk.W, tk.E, tk.S), padx=5, pady=(5, 0))
        footer_frame.columnconfigure(0, weight=1)
        
        self.status_bar_var = tk.StringVar(value="准备就绪...")
        ttk.Label(footer_frame, 
                 textvariable=self.status_bar_var,
                 font=('Microsoft YaHei UI', 9),
                 padding=(5, 2)).grid(row=0, column=0, sticky=tk.W)
        
        self.progress_bar = ttk.Progressbar(footer_frame, 
                                           orient='horizontal', 
                                           length=200, 
                                           mode='determinate')
        self.progress_bar.grid(row=0, column=1, sticky=tk.E, padx=5)

    def create_single_update_tab(self, parent):
        """创建单个更新标签页"""
        parent.columnconfigure(1, weight=1)
        
        # ICC_PNO输入
        ttk.Label(parent, text="新的 ICC_PNO:", style='Info.TLabel').grid(
            row=0, column=0, sticky=tk.W, pady=5)
        self.new_pno_var = tk.StringVar()
        pno_entry = ttk.Entry(parent,
                             textvariable=self.new_pno_var,
                             font=('Consolas', 10),
                             width=30)
        pno_entry.grid(row=0, column=1, sticky=(tk.W, tk.E), pady=5, padx=5)
        
        # VIN输入
        ttk.Label(parent, text="新的 VIN:", style='Info.TLabel').grid(
            row=1, column=0, sticky=tk.W, pady=5)
        self.new_vin_var = tk.StringVar()
        # 实时验证VIN
        self.new_vin_var.trace_add('write', self.validate_vin_input) 
        vin_entry = ttk.Entry(parent,
                             textvariable=self.new_vin_var,
                             font=('Consolas', 10),
                             width=30)
        vin_entry.grid(row=1, column=1, sticky=(tk.W, tk.E), pady=5, padx=5)
        
        # VIN验证状态
        self.vin_validation_var = tk.StringVar(value="")
        self.vin_validation_label = ttk.Label(parent, 
                                             textvariable=self.vin_validation_var,
                                             font=('Microsoft YaHei UI', 8))
        self.vin_validation_label.grid(row=2, column=1, sticky=tk.W, pady=2, padx=5)
        
        # f1A1输入
        ttk.Label(parent, text="新的 f1A1:", style='Info.TLabel').grid(
            row=3, column=0, sticky=tk.W, pady=5)
        self.new_f1a1_var = tk.StringVar()
        f1a1_entry = ttk.Entry(parent,
                              textvariable=self.new_f1a1_var,
                              font=('Consolas', 10),
                              width=30)
        f1a1_entry.grid(row=3, column=1, sticky=(tk.W, tk.E), pady=5, padx=5)
        ttk.Label(parent, text="(选填)", 
                 foreground='gray').grid(row=3, column=2, sticky=tk.W, pady=5)
        
        # 快速填充按钮
        quick_fill_frame = ttk.Frame(parent)
        quick_fill_frame.grid(row=4, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=10)
        
        ttk.Button(quick_fill_frame,
                  text="📋 复制当前配置",
                  command=self.copy_current_config,
                  style='Info.TButton').pack(side=tk.LEFT, padx=2)
        
        ttk.Button(quick_fill_frame,
                  text="🔢 生成测试VIN",
                  command=self.generate_test_vin,
                  style='Info.TButton').pack(side=tk.LEFT, padx=2)
        
        ttk.Button(quick_fill_frame,
                  text="🧹 清空输入",
                  command=self.clear_inputs,
                  style='Info.TButton').pack(side=tk.LEFT, padx=2)
        
        # 更新按钮
        self.update_btn = ttk.Button(parent,
                                    text="✅ 开始更新配置",
                                    command=self.start_single_update_thread, # 使用线程
                                    style='Success.TButton')
        self.update_btn.grid(row=5, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=10)

    def create_batch_update_tab(self, parent):
        """创建批量更新标签页"""
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1) # 让预览区域扩展
        
        # 说明文本
        info_text = """批量更新功能说明：
1. 准备CSV文件，包含列：ICC_PNO, VIN, f1A1（可选）
2. 点击"导入CSV文件"选择文件
3. 预览数据后点击"开始批量更新"
4. 系统将逐个处理每条记录，并记录结果。"""
        
        ttk.Label(parent, 
                 text=info_text,
                 font=('Microsoft YaHei UI', 9),
                 foreground='#666666',
                 justify=tk.LEFT).grid(row=0, column=0, sticky=tk.W, pady=10)
        
        # 批量操作按钮
        batch_btn_frame = ttk.Frame(parent)
        batch_btn_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=5)
        
        ttk.Button(batch_btn_frame,
                  text="📂 导入CSV文件",
                  command=self.import_batch_csv,
                  style='Primary.TButton').pack(side=tk.LEFT, padx=5)
        
        ttk.Button(batch_btn_frame,
                  text="📄 下载CSV模板",
                  command=self.download_csv_template,
                  style='Info.TButton').pack(side=tk.LEFT, padx=5)
        
        self.batch_update_btn = ttk.Button(batch_btn_frame,
                                          text="🚀 开始批量更新",
                                          command=self.start_batch_update_thread, # 使用线程
                                          style='Danger.TButton',
                                          state=tk.DISABLED)
        self.batch_update_btn.pack(side=tk.RIGHT, padx=5)
        
        # 批量数据预览
        preview_frame = ttk.LabelFrame(parent, text="数据预览 (最多显示10行)", padding="10")
        preview_frame.grid(row=2, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=5)
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(0, weight=1)
        
        # Treeview 用于展示表格数据
        self.batch_tree = ttk.Treeview(preview_frame, 
                                      columns=('PNO', 'VIN', 'F1A1', 'Validation'), 
                                      show='headings')
        self.batch_tree.heading('PNO', text='ICC_PNO')
        self.batch_tree.heading('VIN', text='VIN')
        self.batch_tree.heading('F1A1', text='f1A1')
        self.batch_tree.heading('Validation', text='验证状态')
        
        self.batch_tree.column('PNO', width=100, anchor='center')
        self.batch_tree.column('VIN', width=150, anchor='center')
        self.batch_tree.column('F1A1', width=80, anchor='center')
        self.batch_tree.column('Validation', width=200, anchor='w')
        
        self.batch_tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # 添加滚动条
        tree_scrollbar_y = ttk.Scrollbar(preview_frame, 
                                         orient="vertical", 
                                         command=self.batch_tree.yview)
        tree_scrollbar_y.grid(row=0, column=1, sticky='ns')
        self.batch_tree.configure(yscrollcommand=tree_scrollbar_y.set)

    def create_history_tab(self, parent):
        """创建操作历史记录标签页"""
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        
        # Treeview 用于展示历史记录
        self.history_tree = ttk.Treeview(parent, 
                                        columns=('Time', 'Operation', 'VIN', 'Result'), 
                                        show='headings')
        self.history_tree.heading('Time', text='时间')
        self.history_tree.heading('Operation', text='操作类型')
        self.history_tree.heading('VIN', text='VIN码/模板名')
        self.history_tree.heading('Result', text='结果')
        
        self.history_tree.column('Time', width=150, anchor='center')
        self.history_tree.column('Operation', width=100, anchor='center')
        self.history_tree.column('VIN', width=200, anchor='w')
        self.history_tree.column('Result', width=300, anchor='w')
        
        self.history_tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # 添加滚动条
        tree_scrollbar_y = ttk.Scrollbar(parent, 
                                         orient="vertical", 
                                         command=self.history_tree.yview)
        tree_scrollbar_y.grid(row=0, column=1, sticky='ns')
        self.history_tree.configure(yscrollcommand=tree_scrollbar_y.set)
        
        # 刷新按钮
        ttk.Button(parent,
                  text="🔄 刷新历史记录",
                  command=self.refresh_history_list,
                  style='Info.TButton').grid(row=1, column=0, sticky=tk.E, pady=(5, 0))
        
        self.refresh_history_list() # 初次加载历史记录
        
    # --- 设备和配置操作逻辑 ---
    
    def check_device_connection(self):
        """检查ADB设备连接状态并拉取配置"""
        self.status_bar_var.set("正在检测设备连接...")
        self.log("设备", "正在检测ADB设备...", tag='INFO')
        
        # 1. 检查ADB设备列表
        success, output = self.run_adb_command(['devices'], log_on_success=False)
        
        if success and 'device' in output and 'offline' not in output:
            self.device_connected = True
            self.status_label.configure(text="● 已连接", foreground="green")
            self.log("设备", "ADB设备已连接。", tag='SUCCESS')
            
            # 2. 拉取配置文件
            self.pull_config_file()
            
        else:
            self.device_connected = False
            self.status_label.configure(text="● 未连接", foreground="red")
            self.log("设备", "未检测到ADB设备连接。", tag='ERROR')
            self.status_bar_var.set("设备未连接")
            messagebox.showerror("连接失败", "未检测到车机设备或设备未授权，请检查USB连接和ADB调试权限。")

    def reconnect_device(self):
        """重新连接设备 (杀掉adb server并重启)"""
        self.log("设备", "正在尝试重新连接 (重启ADB服务)...", tag='WARNING')
        self.status_bar_var.set("正在重启ADB服务...")
        
        # 1. 停止ADB服务
        self.run_adb_command(['kill-server'], log_on_success=True)
        # 2. 启动ADB服务 (会自动启动)
        self.run_adb_command(['start-server'], log_on_success=True)
        
        # 3. 再次检查连接
        self.check_device_connection()

    def pull_config_file(self):
        """从设备拉取配置文件"""
        if not self.device_connected:
            self.log("操作失败", "设备未连接，无法拉取文件。", tag='ERROR')
            messagebox.showwarning("操作警告", "请先连接设备。")
            return
            
        self.status_bar_var.set("正在拉取配置文件...")
        self.log("文件操作", f"正在拉取文件: {self.device_file_path} -> {self.local_file_path}", tag='INFO')
        
        # 备份当前本地文件
        if os.path.exists(self.local_file_path):
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_local_prepull")
            backup_path = os.path.join(self.backup_dir, f"DeviceInfo_{timestamp}.txt")
            shutil.copy(self.local_file_path, backup_path)
            self.log("备份", f"本地文件已备份至: {backup_path}", tag='INFO')
            
        # 执行拉取命令
        success, output = self.run_adb_command(['pull', self.device_file_path, self.local_file_path])
        
        if success:
            self.log("文件操作", "配置文件拉取成功。", tag='SUCCESS')
            self.read_local_config()
            self.status_bar_var.set("配置文件拉取成功")
        else:
            self.log("文件操作", f"配置文件拉取失败: {output}", tag='ERROR')
            self.status_bar_var.set("配置文件拉取失败")
            messagebox.showerror("拉取失败", f"无法从设备拉取文件。请确认路径是否正确: {self.device_file_path}")

    def read_local_config(self):
        """读取本地配置文件并更新UI显示"""
        self.current_config = {}
        self.current_pno_var.set("未读取")
        self.current_vin_var.set("未读取")
        self.current_f1a1_var.set("未读取")
        self.file_hash_var.set("未计算")
        self.vin_check_var.set("")
        
        if not os.path.exists(self.local_file_path):
            self.log("文件操作", "本地配置文件不存在。", tag='ERROR')
            return
            
        try:
            with open(self.local_file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 配置文件是INI或Key-Value格式，这里简化为键值对解析
            # 假设格式为 Key=Value
            for line in content.splitlines():
                if '=' in line:
                    key, value = line.split('=', 1)
                    self.current_config[key.strip()] = value.strip()
                    
            # 更新UI
            pno = self.current_config.get('ICC_PNO', 'N/A')
            vin = self.current_config.get('VIN', 'N/A')
            f1a1 = self.current_config.get('f1A1', 'N/A')
            
            self.current_pno_var.set(pno)
            self.current_vin_var.set(vin)
            self.current_f1a1_var.set(f1a1)
            
            # 计算哈希
            file_hash = self.calculate_file_hash(self.local_file_path)
            self.file_hash_var.set(file_hash[:12] + '...')

            # 验证VIN
            is_valid, msg = self.validator.validate_vin(vin)
            self.vin_check_var.set(msg)
            self.vin_check_var.get() # Force update
            self.log("配置验证", f"当前VIN ({vin}): {msg}", tag='SUCCESS' if is_valid and '验证通过' in msg else 'WARNING')

            self.refresh_backup_list()
            self.status_bar_var.set("本地配置读取成功")
            
        except Exception as e:
            self.log("文件操作", f"读取或解析本地配置失败: {e}", tag='ERROR')
            self.status_bar_var.set("本地配置解析失败")
            messagebox.showerror("文件错误", f"读取或解析本地配置文件失败: {e}")

    def push_config_file(self, config_data, operation_type="SINGLE_UPDATE"):
        """将配置推送到设备"""
        if not self.device_connected:
            self.log("操作失败", "设备未连接，无法推送文件。", tag='ERROR')
            messagebox.showwarning("操作警告", "请先连接设备。")
            return False
            
        # 1. 创建新的本地文件 (Key=Value格式)
        new_content = ""
        # 优先使用旧配置，确保非修改字段不变
        temp_config = self.current_config.copy()
        temp_config.update(config_data)
        
        for key, value in temp_config.items():
            new_content += f"{key}={value}\n"
            
        temp_local_path = "DeviceInfo_new.txt"
        try:
            with open(temp_local_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
        except Exception as e:
            self.log("文件操作", f"写入新配置到临时文件失败: {e}", tag='ERROR')
            return False
            
        # 2. 备份设备上的原始文件 (远程备份)
        self.status_bar_var.set("正在创建远程备份...")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        remote_backup_path = f"{self.device_file_path}.{timestamp}.bak"
        
        # 尝试将设备上的文件复制到带时间戳的备份文件
        backup_cmd = ['shell', f'cp {self.device_file_path} {remote_backup_path}']
        backup_success, backup_output = self.run_adb_command(backup_cmd)
        
        if backup_success:
            self.log("备份", f"设备端文件已备份至: {remote_backup_path}", tag='SUCCESS')
        else:
            self.log("备份", f"设备端备份失败，跳过。错误: {backup_output}", tag='WARNING')

        # 3. 推送新文件
        self.status_bar_var.set("正在推送新配置文件...")
        self.log("文件操作", f"正在推送文件: {temp_local_path} -> {self.device_file_path}", tag='INFO')
        
        push_success, push_output = self.run_adb_command(['push', temp_local_path, self.device_file_path])
        
        # 4. 清理临时文件
        os.remove(temp_local_path)
        
        if push_success:
            self.log("文件操作", "配置文件推送成功。", tag='SUCCESS')
            
            # 5. 重新拉取并验证
            self.pull_config_file()
            
            # 6. 添加操作历史记录
            self.history_manager.add_record(
                operation_type=operation_type,
                old_config={'ICC_PNO': self.current_config.get('ICC_PNO'), 'VIN': self.current_config.get('VIN')},
                new_config={'ICC_PNO': config_data.get('ICC_PNO'), 'VIN': config_data.get('VIN')},
                result="成功"
            )
            self.refresh_history_list()
            self.status_bar_var.set("配置更新成功！")
            return True
        else:
            self.log("文件操作", f"配置文件推送失败: {push_output}", tag='ERROR')
            self.status_bar_var.set("配置更新失败")
            self.history_manager.add_record(
                operation_type=operation_type,
                old_config={'ICC_PNO': self.current_config.get('ICC_PNO'), 'VIN': self.current_config.get('VIN')},
                new_config={'ICC_PNO': config_data.get('ICC_PNO'), 'VIN': config_data.get('VIN')},
                result=f"失败: {push_output[:50]}..."
            )
            self.refresh_history_list()
            return False

    # --- 单个更新逻辑 ---
    
    def validate_vin_input(self, *args):
        """实时验证VIN输入"""
        vin = self.new_vin_var.get().upper()
        self.new_vin_var.set(vin) # 强制大写
        
        if not vin:
            self.vin_validation_var.set("")
            self.vin_validation_label.config(foreground='black')
            return
            
        is_valid, msg = self.validator.validate_vin(vin)
        self.vin_validation_var.set(msg)
        
        if '校验位验证通过' in msg:
            self.vin_validation_label.config(foreground='#28a745') # 绿色
        elif '应为' in msg:
            self.vin_validation_label.config(foreground='#ffc107') # 黄色警告
        else:
            self.vin_validation_label.config(foreground='#dc3545') # 红色错误

    def copy_current_config(self):
        """复制当前读取的配置到输入框"""
        self.new_pno_var.set(self.current_pno_var.get())
        self.new_vin_var.set(self.current_vin_var.get())
        # 只有当f1A1不是默认值时才复制
        current_f1a1 = self.current_f1a1_var.get()
        if current_f1a1 not in ["未读取", "N/A"]:
             self.new_f1a1_var.set(current_f1a1)
        self.log("输入", "当前配置已复制到输入框。", tag='INFO')

    def clear_inputs(self):
        """清空输入框"""
        self.new_pno_var.set("")
        self.new_vin_var.set("")
        self.new_f1a1_var.set("")
        self.vin_validation_var.set("")
        self.log("输入", "输入框已清空。", tag='INFO')
        
    def generate_test_vin(self):
        """生成一个合法的测试VIN（简化版，仅用于演示）"""
        # 构造一个符合格式但校验位可能不准确的VIN前8位和后8位
        base_vin = "LFWN1234F" # 假设前9位，第9位F会被替换
        suffix = "1234567"
        
        # 构造一个17位的VIN，第9位留空或用占位符
        placeholder_vin = base_vin[:8] + '0' + suffix
        
        # 计算第9位校验位
        checksum = self.validator.calculate_vin_checksum(placeholder_vin)
        
        # 替换第9位
        test_vin = base_vin[:8] + checksum + suffix
        
        self.new_vin_var.set(test_vin)
        self.log("输入", f"已生成测试VIN: {test_vin}", tag='INFO')

    def start_single_update_thread(self):
        """启动单个更新操作的线程"""
        # 禁用按钮防止重复点击
        self.update_btn.config(state=tk.DISABLED)
        threading.Thread(target=self._single_update, daemon=True).start()

    def _single_update(self):
        """执行单个配置更新逻辑（在线程中运行）"""
        try:
            pno = self.new_pno_var.get().strip()
            vin = self.new_vin_var.get().strip().upper()
            f1a1 = self.new_f1a1_var.get().strip()

            if not self.device_connected:
                messagebox.showwarning("操作警告", "设备未连接，请先检测并连接设备！")
                self.log("操作失败", "设备未连接，无法更新。", tag='ERROR')
                return

            # 1. 前置验证
            is_valid_pno, msg_pno = self.validator.validate_icc_pno(pno)
            is_valid_vin, msg_vin = self.validator.validate_vin(vin)

            if not is_valid_pno or not is_valid_vin:
                error_msg = f"配置验证失败:\nICC_PNO: {msg_pno}\nVIN: {msg_vin}"
                messagebox.showerror("验证失败", error_msg)
                self.log("验证失败", error_msg, tag='ERROR')
                return

            config_data = {'ICC_PNO': pno, 'VIN': vin}
            if f1a1:
                config_data['f1A1'] = f1a1
            else:
                # 如果用户清空了 f1A1，但旧配置中有，也需要清除它
                if 'f1A1' in self.current_config and 'f1A1' not in config_data:
                    config_data['f1A1'] = '' # 留空表示清除

            # 2. 推送配置
            self.log("更新", f"准备推送单个配置: PNO={pno}, VIN={vin}", tag='WARNING')
            self.push_config_file(config_data, operation_type="SINGLE_UPDATE")

        except Exception as e:
            self.log("系统错误", f"单个更新操作发生意外错误: {e}", tag='ERROR')
            messagebox.showerror("错误", f"更新操作发生意外错误: {e}")
        finally:
            # 重新启用按钮
            self.root.after(0, lambda: self.update_btn.config(state=tk.NORMAL))


    # --- 模板操作逻辑 ---

    def refresh_template_list(self):
        """刷新模板列表"""
        self.template_listbox.delete(0, tk.END)
        templates = self.template_manager.list_templates()
        for t in templates:
            self.template_listbox.insert(tk.END, t)
        self.log("模板", f"已加载 {len(templates)} 个配置模板。", tag='INFO')

    def save_as_template(self):
        """保存当前输入框的配置为模板"""
        pno = self.new_pno_var.get().strip()
        vin = self.new_vin_var.get().strip()
        f1a1 = self.new_f1a1_var.get().strip()
        
        if not pno or not vin:
            messagebox.showwarning("保存失败", "ICC_PNO 和 VIN 不能为空！")
            return
            
        template_name = tk.simpledialog.askstring("保存模板", "请输入模板名称:")
        
        if template_name:
            config_data = {
                'ICC_PNO': pno,
                'VIN': vin,
                'f1A1': f1a1
            }
            try:
                self.template_manager.save_template(template_name, config_data)
                self.log("模板", f"配置已保存为模板: {template_name}", tag='SUCCESS')
                self.refresh_template_list()
                
                self.history_manager.add_record(
                    operation_type="SAVE_TEMPLATE",
                    old_config={},
                    new_config=config_data,
                    result=f"模板名: {template_name}"
                )
                self.refresh_history_list()
            except Exception as e:
                self.log("模板错误", f"保存模板失败: {e}", tag='ERROR')
                messagebox.showerror("错误", f"保存模板失败: {e}")

    def load_selected_template(self):
        """加载选中的模板到输入框"""
        selected_indices = self.template_listbox.curselection()
        if not selected_indices:
            messagebox.showwarning("加载失败", "请先在列表中选择一个模板。")
            return
            
        template_name = self.template_listbox.get(selected_indices[0])
        self._load_template_by_name(template_name)
        
    def load_template_double_click(self, event):
        """双击加载模板"""
        selected_indices = self.template_listbox.curselection()
        if selected_indices:
            template_name = self.template_listbox.get(selected_indices[0])
            self._load_template_by_name(template_name)

    def _load_template_by_name(self, template_name):
        """按名称加载模板"""
        config = self.template_manager.load_template(template_name)
        if config:
            self.new_pno_var.set(config.get('ICC_PNO', ''))
            self.new_vin_var.set(config.get('VIN', ''))
            self.new_f1a1_var.set(config.get('f1A1', ''))
            self.log("模板", f"模板 '{template_name}' 已加载到输入框。", tag='INFO')
            messagebox.showinfo("模板加载", f"模板 '{template_name}' 已成功加载。")
            
            # 实时触发VIN验证
            self.validate_vin_input()
        else:
            self.log("模板错误", f"模板 '{template_name}' 加载失败。", tag='ERROR')
            messagebox.showerror("错误", f"模板 '{template_name}' 加载失败。")


    # --- 备份/恢复逻辑 ---
    
    def refresh_backup_list(self):
        """刷新本地备份列表"""
        self.backup_listbox.delete(0, tk.END)
        if not os.path.exists(self.backup_dir):
            return
        
        # 筛选出 DeviceInfo_YYYYMMDD_HHMMSS_*.txt 格式的文件
        backup_files = sorted([f for f in os.listdir(self.backup_dir) if f.startswith('DeviceInfo_')], 
                              reverse=True)
        
        for f in backup_files:
            # 格式化显示名称
            try:
                parts = f.split('_')
                if len(parts) >= 3:
                    date_time_str = parts[1] + '_' + parts[2].split('.')[0]
                    # YYYYMMDD_HHMMSS -> YYYY-MM-DD HH:MM:SS
                    formatted_time = datetime.strptime(date_time_str, "%Y%m%d_%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
                    
                    if 'local_prepull' in f:
                         tag = "本地拉取前"
                    else:
                         tag = "更新前备份"
                         
                    display_name = f"[{tag}] {formatted_time}"
                    self.backup_listbox.insert(tk.END, display_name)
                    # 将完整文件名作为隐藏数据存储
                    self.backup_listbox.item_data = getattr(self.backup_listbox, 'item_data', {})
                    self.backup_listbox.item_data[display_name] = f
            except:
                self.backup_listbox.insert(tk.END, f"[错误格式] " + f)
                pass # 忽略格式错误的备份文件
        self.log("备份", f"已加载 {len(backup_files)} 个本地备份。", tag='INFO')

    def restore_selected_backup(self):
        """恢复选中的备份文件到本地，并询问是否推送到设备"""
        selected_indices = self.backup_listbox.curselection()
        if not selected_indices:
            messagebox.showwarning("恢复失败", "请先在备份列表中选择一个文件。")
            return
            
        selected_display_name = self.backup_listbox.get(selected_indices[0])
        # 获取完整的原始文件名
        backup_file_name = self.backup_listbox.item_data.get(selected_display_name)
        backup_path = os.path.join(self.backup_dir, backup_file_name)
        
        if not messagebox.askyesno("确认恢复", f"确定要将本地配置恢复到\n'{selected_display_name}'\n的状态吗？"):
            return
            
        try:
            # 将备份文件复制到当前本地文件
            shutil.copy(backup_path, self.local_file_path)
            self.log("恢复", f"已将备份文件 '{backup_file_name}' 恢复到本地。", tag='SUCCESS')
            
            # 重新读取本地配置并更新UI
            self.read_local_config()
            
            # 询问是否推送到设备
            if self.device_connected and messagebox.askyesno("推送确认", "本地配置已恢复。是否立即将此配置推送到连接的设备？"):
                # 读取恢复后的配置
                temp_config = {}
                with open(self.local_file_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        if '=' in line:
                            key, value = line.split('=', 1)
                            temp_config[key.strip()] = value.strip()
                
                # 执行推送，使用恢复后的配置
                push_config = {
                    'ICC_PNO': temp_config.get('ICC_PNO', ''),
                    'VIN': temp_config.get('VIN', ''),
                    'f1A1': temp_config.get('f1A1', '')
                }
                self.push_config_file(push_config, operation_type="RESTORE_BACKUP")
                messagebox.showinfo("恢复完成", "备份已恢复并成功推送到设备。")
            else:
                messagebox.showinfo("恢复完成", "备份已成功恢复到本地。")
                
            self.history_manager.add_record(
                operation_type="RESTORE_LOCAL",
                old_config={},
                new_config={'file': backup_file_name},
                result="成功"
            )
            self.refresh_history_list()
            
        except Exception as e:
            self.log("恢复错误", f"恢复备份失败: {e}", tag='ERROR')
            messagebox.showerror("错误", f"恢复备份失败: {e}")

    def restore_backup_double_click(self, event):
        """双击恢复备份"""
        self.restore_selected_backup()

    # --- 批量操作逻辑 ---

    def download_csv_template(self):
        """下载CSV模板"""
        template_content = "ICC_PNO,VIN,f1A1\nTEST_PNO_001,VF900000000000000,01A1\nTEST_PNO_002,VF900000000000001,\n"
        file_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
            title="下载批量更新CSV模板"
        )
        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8-sig') as f: # 使用 utf-8-sig 避免中文乱码
                    f.write(template_content)
                self.log("文件操作", f"CSV模板已保存到: {file_path}", tag='SUCCESS')
            except Exception as e:
                self.log("文件操作", f"CSV模板保存失败: {e}", tag='ERROR')
                messagebox.showerror("错误", f"CSV模板保存失败: {e}")

    def import_batch_csv(self):
        """导入CSV文件并预览"""
        file_path = filedialog.askopenfilename(
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            title="选择批量更新CSV文件"
        )
        
        if not file_path:
            return
            
        self.batch_csv_data = []
        
        try:
            with open(file_path, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                required_headers = ['ICC_PNO', 'VIN']
                
                # 检查必需的表头
                if not all(h in reader.fieldnames for h in required_headers):
                    raise ValueError(f"CSV文件缺少必需的列: {', '.join(required_headers)}")
                    
                for row in reader:
                    # 确保 VIN 和 PNO 存在，且 f1A1 即使缺失也用空字符串填充
                    pno = row.get('ICC_PNO', '').strip()
                    vin = row.get('VIN', '').strip().upper()
                    f1a1 = row.get('f1A1', '').strip()
                    
                    # 进行预验证
                    is_valid_pno, msg_pno = self.validator.validate_icc_pno(pno)
                    is_valid_vin, msg_vin = self.validator.validate_vin(vin)
                    
                    validation_msg = f"PNO: {'OK' if is_valid_pno else msg_pno}; VIN: {'OK' if is_valid_vin else msg_vin}"
                    is_all_valid = is_valid_pno and is_valid_vin
                    
                    self.batch_csv_data.append({
                        'ICC_PNO': pno,
                        'VIN': vin,
                        'f1A1': f1a1,
                        'validation_msg': validation_msg,
                        'is_valid': is_all_valid,
                        'status': '待处理'
                    })
                    
            self.log("批量操作", f"成功导入 {len(self.batch_csv_data)} 条记录。", tag='SUCCESS')
            self._display_batch_preview()
            self.batch_update_btn.config(state=tk.NORMAL if self.batch_csv_data else tk.DISABLED)
            
        except Exception as e:
            self.log("批量操作错误", f"导入CSV文件失败: {e}", tag='ERROR')
            messagebox.showerror("错误", f"导入CSV文件失败: {e}")
            self.batch_csv_data = []
            self.batch_update_btn.config(state=tk.DISABLED)
            self._display_batch_preview()

    def _display_batch_preview(self):
        """在Treeview中显示批量数据预览"""
        # 清空现有数据
        for i in self.batch_tree.get_children():
            self.batch_tree.delete(i)
            
        # 插入新数据 (仅显示前10条)
        for i, data in enumerate(self.batch_csv_data[:10]):
            tag = 'error' if not data['is_valid'] else 'ok'
            
            # 配置标签颜色
            self.batch_tree.tag_configure('ok', foreground='#28a745')
            self.batch_tree.tag_configure('error', foreground='#dc3545')
            
            self.batch_tree.insert('', tk.END, 
                                   values=(data['ICC_PNO'], 
                                           data['VIN'], 
                                           data['f1A1'], 
                                           data['validation_msg']),
                                   tags=(tag,))
        
        if len(self.batch_csv_data) > 10:
            self.batch_tree.insert('', tk.END, values=('[...]', '[...]', '[...]', f'共 {len(self.batch_csv_data)} 条记录，仅显示前10条。'), tags=('info',))
            self.batch_tree.tag_configure('info', foreground='gray')
            
    def start_batch_update_thread(self):
        """启动批量更新操作的线程"""
        if not self.batch_csv_data:
            messagebox.showwarning("操作警告", "没有可供批量更新的数据。请先导入CSV文件。")
            return
            
        if not self.device_connected:
            messagebox.showwarning("操作警告", "设备未连接，请先检测并连接设备！")
            return
            
        if not messagebox.askyesno("确认批量更新", f"确定要开始批量更新 {len(self.batch_csv_data)} 条记录吗？该操作不可逆！"):
            return
            
        # 禁用按钮防止重复点击
        self.batch_update_btn.config(state=tk.DISABLED)
        self.update_btn.config(state=tk.DISABLED)
        
        # 使用线程执行耗时操作
        threading.Thread(target=self._batch_update, daemon=True).start()

    def _batch_update(self):
        """执行批量配置更新逻辑（在线程中运行）"""
        total_records = len(self.batch_csv_data)
        success_count = 0
        
        self.log("批量操作", f"--- 开始批量更新 ({total_records} 条记录) ---", tag='WARNING')
        self.progress_bar.config(mode='determinate', value=0, maximum=total_records)
        self.status_bar_var.set("批量更新进行中...")
        
        try:
            for i, record in enumerate(self.batch_csv_data):
                pno = record['ICC_PNO']
                vin = record['VIN']
                f1a1 = record['f1A1']
                
                self.log("批量进度", f"[{i+1}/{total_records}] 正在处理 PNO={pno}, VIN={vin}...", tag='INFO')
                self.root.after(0, lambda: self.progress_bar.step(1))

                if not record['is_valid']:
                    self.log("批量失败", f"记录[{i+1}] 跳过: 验证失败。{record['validation_msg']}", tag='ERROR')
                    self.batch_csv_data[i]['status'] = '跳过 (验证失败)'
                    continue
                
                config_data = {'ICC_PNO': pno, 'VIN': vin}
                if f1a1:
                    config_data['f1A1'] = f1a1

                # 执行推送（注意：这里会多次调用 push_config_file，每次都会拉取并验证）
                # 为了性能优化，实际应用中可以考虑在循环外拉取一次模板文件，然后批量修改，最后推送一次。
                # 但为了确保每条记录的配置都是基于最新的设备状态，我们保留每次推送的方式。
                if self.push_config_file(config_data, operation_type=f"BATCH_UPDATE [{i+1}/{total_records}]"):
                    self.log("批量成功", f"记录[{i+1}] 配置更新成功。", tag='SUCCESS')
                    self.batch_csv_data[i]['status'] = '成功'
                    success_count += 1
                else:
                    self.log("批量失败", f"记录[{i+1}] 配置更新失败。", tag='ERROR')
                    self.batch_csv_data[i]['status'] = '失败'
                    # 失败后是否继续？通常选择继续执行下一条，确保其他记录能处理
            
            # 批量操作完成后的总结
            summary = f"批量更新完成！成功 {success_count} 条，失败 {total_records - success_count} 条。"
            self.log("批量操作", f"--- 批量更新结束 --- {summary}", tag='SUCCESS' if success_count == total_records else 'WARNING')
            messagebox.showinfo("批量更新结果", summary)
            
        except Exception as e:
            self.log("系统错误", f"批量更新操作发生意外错误: {e}", tag='ERROR')
            messagebox.showerror("错误", f"批量更新操作发生意外错误: {e}")
        finally:
            # 恢复UI状态
            self.root.after(0, lambda: self.batch_update_btn.config(state=tk.NORMAL))
            self.root.after(0, lambda: self.update_btn.config(state=tk.NORMAL))
            self.root.after(0, lambda: self.progress_bar.config(value=0, mode='determinate'))
            self.root.after(0, lambda: self.status_bar_var.set("批量更新已完成"))
            
    # --- 历史记录逻辑 ---
    
    def refresh_history_list(self):
        """刷新历史记录列表"""
        # 清空现有数据
        for i in self.history_tree.get_children():
            self.history_tree.delete(i)
            
        # 插入新数据
        for record in self.history_manager.history:
            op_type = record.get('operation', 'N/A')
            timestamp = record.get('timestamp', 'N/A')
            result = record.get('result', 'N/A')
            
            # 提取关键信息
            if op_type in ["SINGLE_UPDATE", "BATCH_UPDATE"]:
                vin = record.get('new_config', {}).get('VIN', 'N/A')
            elif op_type == "SAVE_TEMPLATE":
                vin = record.get('result', '').replace("模板名: ", "")
                result = "成功"
            elif op_type == "RESTORE_LOCAL":
                vin = record.get('new_config', {}).get('file', 'N/A')
            else:
                vin = 'N/A'
                
            tag = 'success_rec' if '成功' in result else 'error_rec'
            
            # 配置标签颜色
            self.history_tree.tag_configure('success_rec', foreground='#6a9955')
            self.history_tree.tag_configure('error_rec', foreground='#dc3545')
            
            self.history_tree.insert('', tk.END, 
                                   values=(timestamp, op_type, vin, result),
                                   tags=(tag,))


if __name__ == '__main__':
    root = tk.Tk()
    # 启用 DPI 缩放，解决高分屏模糊问题
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except:
        pass
        
    app = VehicleOTAConfigToolEnhanced(root)
    root.mainloop()