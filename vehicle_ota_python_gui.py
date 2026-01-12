#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
车机OTA配置工具
Vehicle OTA Configuration Tool
作者: Professional Python Developer
版本: 1.0.0
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import subprocess
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
import threading


class VehicleOTAConfigTool:
    """车机OTA配置工具主类"""
    
    def __init__(self, root):
        self.root = root
        self.root.title("车机OTA配置工具 v1.0.0")
        self.root.geometry("1200x800")
        self.root.resizable(True, True)
        
        # 配置文件路径
        self.device_file_path = "/mnt/sdcard/DeviceInfo.txt"
        self.local_file_path = "DeviceInfo.txt"
        self.backup_dir = "backups"
        
        # 创建备份目录
        os.makedirs(self.backup_dir, exist_ok=True)
        
        # 当前配置
        self.current_config = {}
        self.device_connected = False
        
        # 设置样式
        self.setup_styles()
        
        # 创建UI
        self.create_ui()
        
        # 初始化日志
        self.log("系统", "车机OTA配置工具已启动")
        self.log("提示", "请点击'检测设备'按钮连接车机")
    
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
                       padding=10,
                       background='#28a745')
        style.configure('Danger.TButton',
                       font=('Microsoft YaHei UI', 10, 'bold'),
                       padding=10,
                       background='#dc3545')
        
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
        main_container.columnconfigure(1, weight=1)
        main_container.rowconfigure(2, weight=1)
        
        # ============ 顶部标题栏 ============
        self.create_header(main_container)
        
        # ============ 左侧面板 ============
        self.create_left_panel(main_container)
        
        # ============ 右侧面板 ============
        self.create_right_panel(main_container)
        
    def create_header(self, parent):
        """创建标题栏"""
        header_frame = ttk.Frame(parent, relief=tk.RAISED, borderwidth=2)
        header_frame.grid(row=0, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))
        header_frame.columnconfigure(1, weight=1)
        
        # 标题
        title_label = ttk.Label(header_frame, 
                               text="🚗 车机OTA配置工具",
                               style='Title.TLabel')
        title_label.grid(row=0, column=0, padx=10, pady=10, sticky=tk.W)
        
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
        
        # ============ 设备连接区 ============
        connection_frame = ttk.LabelFrame(left_frame, text="设备连接", padding="10")
        connection_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=5, padx=5)
        connection_frame.columnconfigure(0, weight=1)
        
        self.check_device_btn = ttk.Button(connection_frame,
                                          text="🔍 检测设备",
                                          command=self.check_device_connection,
                                          style='Primary.TButton')
        self.check_device_btn.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=5)
        
        # ============ 当前配置区 ============
        current_config_frame = ttk.LabelFrame(left_frame, text="当前配置", padding="10")
        current_config_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=5, padx=5)
        current_config_frame.columnconfigure(1, weight=1)
        
        # ICC_PNO
        ttk.Label(current_config_frame, text="ICC_PNO:", style='Info.TLabel').grid(
            row=0, column=0, sticky=tk.W, pady=5)
        self.current_pno_var = tk.StringVar(value="未读取")
        current_pno_label = ttk.Label(current_config_frame,
                                     textvariable=self.current_pno_var,
                                     font=('Consolas', 10, 'bold'),
                                     foreground='#0066cc')
        current_pno_label.grid(row=0, column=1, sticky=tk.W, pady=5, padx=5)
        
        # VIN
        ttk.Label(current_config_frame, text="VIN:", style='Info.TLabel').grid(
            row=1, column=0, sticky=tk.W, pady=5)
        self.current_vin_var = tk.StringVar(value="未读取")
        current_vin_label = ttk.Label(current_config_frame,
                                     textvariable=self.current_vin_var,
                                     font=('Consolas', 10, 'bold'),
                                     foreground='#0066cc')
        current_vin_label.grid(row=1, column=1, sticky=tk.W, pady=5, padx=5)
        
        # f1A1
        ttk.Label(current_config_frame, text="f1A1:", style='Info.TLabel').grid(
            row=2, column=0, sticky=tk.W, pady=5)
        self.current_f1a1_var = tk.StringVar(value="未读取")
        current_f1a1_label = ttk.Label(current_config_frame,
                                      textvariable=self.current_f1a1_var,
                                      font=('Consolas', 8),
                                      foreground='#666666')
        current_f1a1_label.grid(row=2, column=1, sticky=tk.W, pady=5, padx=5)
        
        # ============ 备份列表区 ============
        backup_frame = ttk.LabelFrame(left_frame, text="备份列表", padding="10")
        backup_frame.grid(row=2, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=5, padx=5)
        backup_frame.columnconfigure(0, weight=1)
        backup_frame.rowconfigure(0, weight=1)
        
        # 备份列表
        self.backup_listbox = tk.Listbox(backup_frame,
                                         height=10,
                                         font=('Consolas', 9))
        self.backup_listbox.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        backup_scrollbar = ttk.Scrollbar(backup_frame,
                                        orient=tk.VERTICAL,
                                        command=self.backup_listbox.yview)
        backup_scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
        self.backup_listbox.configure(yscrollcommand=backup_scrollbar.set)
        
        # 刷新备份列表按钮
        ttk.Button(backup_frame,
                  text="🔄 刷新列表",
                  command=self.refresh_backup_list).grid(
            row=1, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(5, 0))
        
    def create_right_panel(self, parent):
        """创建右侧操作面板"""
        right_frame = ttk.Frame(parent)
        right_frame.grid(row=1, column=1, rowspan=2, sticky=(tk.W, tk.E, tk.N, tk.S), padx=(5, 0))
        right_frame.columnconfigure(0, weight=1)
        right_frame.rowconfigure(1, weight=1)
        
        # ============ 配置更新区 ============
        update_frame = ttk.LabelFrame(right_frame, text="配置更新", padding="10")
        update_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=5)
        update_frame.columnconfigure(1, weight=1)
        
        # ICC_PNO输入
        ttk.Label(update_frame, text="新的 ICC_PNO:", style='Info.TLabel').grid(
            row=0, column=0, sticky=tk.W, pady=5)
        self.new_pno_var = tk.StringVar()
        pno_entry = ttk.Entry(update_frame,
                             textvariable=self.new_pno_var,
                             font=('Consolas', 10),
                             width=30)
        pno_entry.grid(row=0, column=1, sticky=(tk.W, tk.E), pady=5, padx=5)
        
        # VIN输入
        ttk.Label(update_frame, text="新的 VIN:", style='Info.TLabel').grid(
            row=1, column=0, sticky=tk.W, pady=5)
        self.new_vin_var = tk.StringVar()
        vin_entry = ttk.Entry(update_frame,
                             textvariable=self.new_vin_var,
                             font=('Consolas', 10),
                             width=30)
        vin_entry.grid(row=1, column=1, sticky=(tk.W, tk.E), pady=5, padx=5)
        ttk.Label(update_frame, text="(17位标准VIN码)", 
                 foreground='gray').grid(row=1, column=2, sticky=tk.W, pady=5)
        
        # f1A1输入（可选）
        ttk.Label(update_frame, text="新的 f1A1:", style='Info.TLabel').grid(
            row=2, column=0, sticky=tk.W, pady=5)
        self.new_f1a1_var = tk.StringVar()
        f1a1_entry = ttk.Entry(update_frame,
                              textvariable=self.new_f1a1_var,
                              font=('Consolas', 10),
                              width=30)
        f1a1_entry.grid(row=2, column=1, sticky=(tk.W, tk.E), pady=5, padx=5)
        ttk.Label(update_frame, text="(选填)", 
                 foreground='gray').grid(row=2, column=2, sticky=tk.W, pady=5)
        
        # 更新按钮
        self.update_btn = ttk.Button(update_frame,
                                    text="✓ 开始更新",
                                    command=self.start_update,
                                    style='Success.TButton')
        self.update_btn.grid(row=3, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=10)
        
        # ============ 操作日志区 ============
        log_frame = ttk.LabelFrame(right_frame, text="操作日志", padding="10")
        log_frame.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=5)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        
        # 日志文本框
        self.log_text = scrolledtext.ScrolledText(log_frame,
                                                  wrap=tk.WORD,
                                                  font=('Consolas', 9),
                                                  bg='#1e1e1e',
                                                  fg='#d4d4d4',
                                                  height=20)
        self.log_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # 配置日志颜色标签
        self.log_text.tag_config('INFO', foreground='#4ec9b0')
        self.log_text.tag_config('SUCCESS', foreground='#6a9955')
        self.log_text.tag_config('WARNING', foreground='#dcdcaa')
        self.log_text.tag_config('ERROR', foreground='#f48771')
        self.log_text.tag_config('TIMESTAMP', foreground='#808080')
        
        # 清空日志按钮
        ttk.Button(log_frame,
                  text="🗑 清空日志",
                  command=self.clear_log).grid(
            row=1, column=0, sticky=(tk.W, tk.E), pady=(5, 0))
    
    def log(self, tag, message, level='INFO'):
        """添加日志"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_line = f"[{timestamp}] [{tag}] {message}\n"
        
        self.log_text.insert(tk.END, f"[{timestamp}] ", 'TIMESTAMP')
        self.log_text.insert(tk.END, f"[{tag}] {message}\n", level)
        self.log_text.see(tk.END)
        self.root.update_idletasks()
    
    def clear_log(self):
        """清空日志"""
        self.log_text.delete(1.0, tk.END)
        self.log("系统", "日志已清空")
    
    def run_adb_command(self, command):
        """执行ADB命令"""
        try:
            result = subprocess.run(command,
                                  shell=True,
                                  capture_output=True,
                                  text=True,
                                  timeout=10,
                                  encoding='utf-8')
            return result.returncode == 0, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return False, "", "命令执行超时"
        except Exception as e:
            return False, "", str(e)
    
    def check_device_connection(self):
        """检测设备连接"""
        self.log("检查", "正在检测ADB设备...")
        
        # 检查ADB是否存在
        success, stdout, stderr = self.run_adb_command("adb version")
        if not success:
            self.log("错误", "ADB未安装或不在PATH中", 'ERROR')
            messagebox.showerror("错误", "未找到ADB工具，请安装Android SDK Platform Tools")
            return
        
        # 检查设备连接
        success, stdout, stderr = self.run_adb_command("adb get-state")
        if not success or "device" not in stdout:
            self.log("错误", "未检测到设备，请检查USB连接和驱动", 'ERROR')
            self.device_connected = False
            self.status_label.config(text="● 未连接", foreground="red")
            messagebox.showerror("错误", "未检测到设备\n请确保：\n1. 设备已连接USB\n2. 已启用USB调试\n3. ADB驱动已安装")
            return
        
        self.log("成功", "设备已连接", 'SUCCESS')
        self.device_connected = True
        self.status_label.config(text="● 已连接", foreground="green")
        
        # 读取当前配置
        self.read_current_config()
    
    def read_current_config(self):
        """读取当前配置"""
        self.log("读取", "正在读取设备配置...")
        
        # 拉取配置文件
        success, stdout, stderr = self.run_adb_command(
            f"adb pull {self.device_file_path} {self.local_file_path}")
        
        if not success:
            self.log("错误", f"无法读取配置文件: {stderr}", 'ERROR')
            return
        
        # 解析JSON
        try:
            with open(self.local_file_path, 'r', encoding='utf-8') as f:
                self.current_config = json.load(f)
            
            self.current_pno_var.set(self.current_config.get('ICC_PNO', '未读取'))
            self.current_vin_var.set(self.current_config.get('VIN', '未读取'))
            self.current_f1a1_var.set(self.current_config.get('f1A1', '未读取')[:30] + '...')
            
            self.log("成功", f"配置读取完成 - ICC_PNO: {self.current_config.get('ICC_PNO')}", 'SUCCESS')
            self.log("成功", f"配置读取完成 - VIN: {self.current_config.get('VIN')}", 'SUCCESS')
            
        except json.JSONDecodeError as e:
            self.log("错误", f"JSON解析失败: {e}", 'ERROR')
        except Exception as e:
            self.log("错误", f"读取配置失败: {e}", 'ERROR')
    
    def start_update(self):
        """开始更新配置"""
        if not self.device_connected:
            messagebox.showwarning("警告", "请先连接设备")
            return
        
        new_pno = self.new_pno_var.get().strip()
        new_vin = self.new_vin_var.get().strip().upper()
        new_f1a1 = self.new_f1a1_var.get().strip()
        
        if not new_pno and not new_vin and not new_f1a1:
            messagebox.showwarning("警告", "请至少输入一个要更新的配置项")
            return
        
        # 验证VIN码格式
        if new_vin and len(new_vin) != 17:
            messagebox.showerror("错误", "VIN码必须是17位字符")
            return
        
        # 确认更新
        confirm_msg = "即将更新以下配置:\n\n"
        if new_pno:
            confirm_msg += f"ICC_PNO: {self.current_config.get('ICC_PNO')} → {new_pno}\n"
        if new_vin:
            confirm_msg += f"VIN: {self.current_config.get('VIN')} → {new_vin}\n"
        if new_f1a1:
            confirm_msg += f"f1A1: {new_f1a1}\n"
        confirm_msg += "\n是否继续？"
        
        if not messagebox.askyesno("确认更新", confirm_msg):
            return
        
        # 在新线程中执行更新
        update_thread = threading.Thread(target=self.perform_update,
                                        args=(new_pno, new_vin, new_f1a1))
        update_thread.daemon = True
        update_thread.start()
    
    def perform_update(self, new_pno, new_vin, new_f1a1):
        """执行更新操作"""
        try:
            self.update_btn.config(state='disabled')
            self.check_device_btn.config(state='disabled')
            
            self.log("开始", "========== 开始更新流程 ==========", 'INFO')
            
            # 步骤1: adb root
            self.log("执行", "[步骤 1/7] adb root", 'INFO')
            success, stdout, stderr = self.run_adb_command("adb root")
            if not success:
                self.log("警告", "root失败，尝试继续...", 'WARNING')
            
            # 步骤2: adb remount
            self.log("执行", "[步骤 2/7] adb remount", 'INFO')
            success, stdout, stderr = self.run_adb_command("adb remount")
            if not success:
                self.log("警告", "remount失败，尝试继续...", 'WARNING')
            
            # 步骤3: 拉取文件
            self.log("执行", "[步骤 3/7] 拉取 DeviceInfo.txt", 'INFO')
            success, stdout, stderr = self.run_adb_command(
                f"adb pull {self.device_file_path} {self.local_file_path}")
            if not success:
                raise Exception(f"拉取文件失败: {stderr}")
            
            # 步骤4: 备份文件
            backup_name = f"DeviceInfo_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            backup_path = os.path.join(self.backup_dir, backup_name)
            shutil.copy2(self.local_file_path, backup_path)
            self.log("备份", f"[步骤 4/7] 备份文件: {backup_name}", 'SUCCESS')
            
            # 步骤5: 更新配置
            self.log("更新", "[步骤 5/7] 更新配置字段", 'INFO')
            with open(self.local_file_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            if new_pno:
                old_pno = config.get('ICC_PNO')
                config['ICC_PNO'] = new_pno
                self.log("更新", f"  ICC_PNO: {old_pno} → {new_pno}", 'INFO')
            
            if new_vin:
                old_vin = config.get('VIN')
                config['VIN'] = new_vin
                self.log("更新", f"  VIN: {old_vin} → {new_vin}", 'INFO')
            
            if new_f1a1:
                config['f1A1'] = new_f1a1
                self.log("更新", f"  f1A1: {new_f1a1}", 'INFO')
            
            # 保存更新后的文件
            with open(self.local_file_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False)
            
            # 步骤6: 推送文件
            self.log("执行", "[步骤 6/7] 推送更新后的文件到设备", 'INFO')
            success, stdout, stderr = self.run_adb_command(
                f"adb push {self.local_file_path} {self.device_file_path}")
            if not success:
                raise Exception(f"推送文件失败: {stderr}")
            
            # 步骤7: 同步数据
            self.log("执行", "[步骤 7/7] 同步数据 (adb shell sync)", 'INFO')
            self.run_adb_command("adb shell sync")
            
            # 验证更新
            self.log("验证", "验证更新结果...", 'INFO')
            success, stdout, stderr = self.run_adb_command(
                f"adb shell cat {self.device_file_path}")
            if success:
                self.log("验证", f"设备文件内容: {stdout.strip()}", 'SUCCESS')
            
            self.log("完成", "========== 配置更新成功！ ==========", 'SUCCESS')
            
            # 清空输入框
            self.new_pno_var.set("")
            self.new_vin_var.set("")
            self.new_f1a1_var.set("")
            
            # 重新读取配置
            self.read_current_config()
            
            # 刷新备份列表
            self.refresh_backup_list()
            
            messagebox.showinfo("成功", "配置更新成功！")
            
        except Exception as e:
            self.log("错误", f"更新失败: {str(e)}", 'ERROR')
            messagebox.showerror("错误", f"更新失败:\n{str(e)}")
        
        finally:
            self.update_btn.config(state='normal')
            self.check_device_btn.config(state='normal')
    
    def refresh_backup_list(self):
        """刷新备份列表"""
        self.backup_listbox.delete(0, tk.END)
        
        if not os.path.exists(self.backup_dir):
            return
        
        backups = sorted([f for f in os.listdir(self.backup_dir) if f.endswith('.txt')],
                        reverse=True)
        
        for backup in backups:
            self.backup_listbox.insert(tk.END, backup)
        
        self.log("系统", f"备份列表已刷新，共 {len(backups)} 个备份文件")


def main():
    """主函数"""
    root = tk.Tk()
    app = VehicleOTAConfigTool(root)
    
    # 启动时刷新备份列表
    app.refresh_backup_list()
    
    root.mainloop()


if __name__ == "__main__":
    main()
