#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Unsplash Pro Downloader - 专业图片批量下载工具
# 支持多分类、断点续传、实时进度显示

import os
import json
import requests
import time
import threading
from pathlib import Path
from datetime import datetime
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog


class UnsplashDownloader:
    """Unsplash下载核心类"""

    # Unsplash完整分类列表
    CATEGORIES = {
        "nature": "自然风光",
        "landscape": "风景摄影",
        "city": "城市建筑",
        "architecture": "建筑艺术",
        "people": "人像摄影",
        "portrait": "肖像特写",
        "technology": "科技产品",
        "business": "商务办公",
        "food": "美食摄影",
        "drink": "饮品特写",
        "travel": "旅行探索",
        "animal": "动物世界",
        "wildlife": "野生动物",
        "pet": "宠物萌照",
        "sport": "体育运动",
        "fitness": "健身运动",
        "fashion": "时尚潮流",
        "art": "艺术创作",
        "abstract": "抽象艺术",
        "wallpaper": "精美壁纸",
        "minimal": "极简风格",
        "color": "色彩美学",
        "black-white": "黑白摄影",
        "texture": "纹理材质",
        "pattern": "图案花纹",
        "sea": "海洋波浪",
        "ocean": "深海探秘",
        "mountain": "高山巍峨",
        "forest": "森林密境",
        "sky": "天空云彩",
        "sunset": "日落黄昏",
        "flower": "花卉植物",
        "car": "汽车机械",
        "motorcycle": "摩托车",
        "airplane": "飞机航空",
        "space": "太空宇宙",
        "music": "音乐艺术",
        "book": "书籍阅读",
        "office": "办公场景",
        "home": "家居生活",
        "interior": "室内设计",
        "wedding": "婚礼纪实",
        "love": "爱情浪漫",
        "baby": "婴儿童真",
        "kids": "儿童成长",
        "street": "街头摄影",
        "night": "夜景灯光",
        "macro": "微距特写",
    }

    def __init__(self, api_key, save_dir, config):
        self.api_key = api_key
        self.save_dir = Path(save_dir)
        self.config = config
        self.is_running = False
        self.is_paused = False
        self.stats = {"total": 0, "success": 0, "failed": 0, "skipped": 0}
        self.progress_file = self.save_dir / ".download_progress.json"

    def load_progress(self, category):
        """加载下载进度（断点续传）"""
        if self.progress_file.exists():
            try:
                with open(self.progress_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return data.get(category, {"downloaded": 0, "ids": []})
            except:
                pass
        return {"downloaded": 0, "ids": []}

    def save_progress(self, category, progress):
        """保存下载进度"""
        data = {}
        if self.progress_file.exists():
            try:
                with open(self.progress_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except:
                pass
        data[category] = progress
        with open(self.progress_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def download_category(self, category, count, log_callback, progress_callback):
        """下载单个分类"""
        category_dir = self.save_dir / category
        category_dir.mkdir(parents=True, exist_ok=True)

        progress = self.load_progress(category)
        downloaded = progress["downloaded"]
        existing_ids = set(progress["ids"])

        if downloaded >= count:
            log_callback(f"[{category}] 已完成下载 ({downloaded}/{count})", "success")
            return True

        log_callback(f"[{category}] 开始下载 (已完成: {downloaded}/{count})", "info")

        url = "https://api.unsplash.com/photos/random"
        headers = {"User-Agent": "Mozilla/5.0"}

        while downloaded < count and self.is_running:
            while self.is_paused and self.is_running:
                time.sleep(0.5)

            if not self.is_running:
                break

            batch_size = min(self.config["batch_size"], count - downloaded)
            params = {
                "query": category,
                "count": batch_size,
                "client_id": self.api_key,
            }

            try:
                response = requests.get(url, params=params, headers=headers,
                                      timeout=self.config["timeout"])

                if response.status_code == 403:
                    log_callback("API配额已耗尽，请等待一小时或更换Key", "error")
                    return False

                if response.status_code == 429:
                    log_callback("触发速率限制，等待60秒...", "warning")
                    time.sleep(60)
                    continue

                response.raise_for_status()
                photos = response.json()

                log_callback(f"[{category}] 获取 {len(photos)} 张图片信息", "info")

                for photo in photos:
                    if not self.is_running:
                        break

                    while self.is_paused and self.is_running:
                        time.sleep(0.5)

                    photo_id = photo["id"]

                    if photo_id in existing_ids:
                        self.stats["skipped"] += 1
                        continue

                    try:
                        image_url = photo["urls"][self.config["resolution"]]
                        filename = f"{category}_{downloaded+1:04d}_{photo_id}.jpg"
                        save_path = category_dir / filename

                        img_response = requests.get(image_url, timeout=self.config["timeout"])
                        img_response.raise_for_status()

                        with open(save_path, "wb") as f:
                            f.write(img_response.content)

                        downloaded += 1
                        existing_ids.add(photo_id)
                        self.stats["success"] += 1
                        self.stats["total"] += 1

                        self.save_progress(category, {
                            "downloaded": downloaded,
                            "ids": list(existing_ids)
                        })

                        progress_callback(category, downloaded, count)

                        if downloaded % 5 == 0 or downloaded == count:
                            log_callback(f"[{category}] {downloaded}/{count} 张", "success")

                        time.sleep(self.config["delay"])

                    except Exception as e:
                        self.stats["failed"] += 1
                        self.stats["total"] += 1
                        log_callback(f"[{category}] 下载失败: {str(e)[:50]}", "error")
                        time.sleep(2)

            except requests.exceptions.RequestException as e:
                log_callback(f"[{category}] 请求失败: {str(e)[:50]}", "warning")
                time.sleep(10)
            except Exception as e:
                log_callback(f"[{category}] 错误: {str(e)[:50]}", "error")
                time.sleep(5)

        if downloaded >= count:
            log_callback(f"[{category}] 下载完成！", "success")
            return True

        return False

    def start(self, categories, log_callback, progress_callback, complete_callback):
        """开始下载任务"""
        self.is_running = True
        self.is_paused = False
        self.stats = {"total": 0, "success": 0, "failed": 0, "skipped": 0}

        def run():
            log_callback("开始批量下载任务", "info")

            for category, count in categories.items():
                if not self.is_running:
                    break
                self.download_category(category, count, log_callback, progress_callback)

            self.is_running = False
            log_callback(f"任务完成！成功: {self.stats['success']}, 失败: {self.stats['failed']}, 跳过: {self.stats['skipped']}", "success")
            complete_callback()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()

    def pause(self):
        """暂停下载"""
        self.is_paused = True

    def resume(self):
        """恢复下载"""
        self.is_paused = False

    def stop(self):
        """停止下载"""
        self.is_running = False
        self.is_paused = False


class UnsplashGUI:
    """图形界面类"""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Unsplash Pro Downloader - 专业图片批量下载工具")
        self.root.geometry("1200x800")
        self.root.configure(bg="#1a1a2e")

        self.config = {
            "api_key": "BD0I4Br4tLY4WVyNFCNIzxB-IUn1uMkSP4Ebl8Bf4AY",
            "save_dir": str(Path.home() / "Downloads" / "Unsplash"),
            "resolution": "full",
            "batch_size": 30,
            "delay": 1.5,
            "timeout": 10,
        }

        self.downloader = None
        self.category_vars = {}
        self.category_counts = {}
        self.progress_bars = {}
        self.progress_labels = {}

        self.create_ui()
        self.load_config()

    def create_ui(self):
        """创建界面"""
        style = ttk.Style()
        style.theme_use('clam')
        style.configure("TFrame", background="#1a1a2e")
        style.configure("TLabel", background="#1a1a2e", foreground="#ffffff", font=("Arial", 10))
        style.configure("Title.TLabel", font=("Arial", 14, "bold"), foreground="#00d4ff")
        style.configure("TButton", font=("Arial", 10, "bold"), padding=10)
        style.map("TButton", background=[("active", "#00d4ff")])

        main_frame = ttk.Frame(self.root, padding=20)
        main_frame.pack(fill=tk.BOTH, expand=True)

        title_frame = ttk.Frame(main_frame)
        title_frame.pack(fill=tk.X, pady=(0, 20))

        ttk.Label(title_frame, text="Unsplash Pro Downloader",
                 style="Title.TLabel").pack(side=tk.LEFT)

        config_frame = ttk.LabelFrame(main_frame, text="基础配置", padding=15)
        config_frame.pack(fill=tk.X, pady=(0, 15))

        ttk.Label(config_frame, text="API Key:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.api_key_entry = ttk.Entry(config_frame, width=50)
        self.api_key_entry.insert(0, self.config["api_key"])
        self.api_key_entry.grid(row=0, column=1, columnspan=2, sticky=tk.EW, padx=5, pady=5)

        ttk.Label(config_frame, text="保存路径:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        self.save_dir_entry = ttk.Entry(config_frame, width=40)
        self.save_dir_entry.insert(0, self.config["save_dir"])
        self.save_dir_entry.grid(row=1, column=1, sticky=tk.EW, padx=5, pady=5)

        ttk.Button(config_frame, text="浏览", command=self.browse_directory,
                  width=10).grid(row=1, column=2, padx=5, pady=5)

        config_frame.columnconfigure(1, weight=1)

        advanced_frame = ttk.LabelFrame(main_frame, text="高级设置", padding=15)
        advanced_frame.pack(fill=tk.X, pady=(0, 15))

        settings = [
            ("图片质量:", "resolution", ["raw", "full", "regular", "small", "thumb"]),
            ("批量大小:", "batch_size", None),
            ("下载延迟(秒):", "delay", None),
            ("超时时间(秒):", "timeout", None),
        ]

        for i, (label, key, values) in enumerate(settings):
            ttk.Label(advanced_frame, text=label).grid(row=i//4, column=(i%4)*2,
                                                       sticky=tk.W, padx=5, pady=5)
            if values:
                combo = ttk.Combobox(advanced_frame, values=values, width=15, state="readonly")
                combo.set(self.config[key])
                combo.grid(row=i//4, column=(i%4)*2+1, sticky=tk.W, padx=5, pady=5)
                setattr(self, f"{key}_combo", combo)
            else:
                entry = ttk.Entry(advanced_frame, width=15)
                entry.insert(0, str(self.config[key]))
                entry.grid(row=i//4, column=(i%4)*2+1, sticky=tk.W, padx=5, pady=5)
                setattr(self, f"{key}_entry", entry)

        content_frame = ttk.Frame(main_frame)
        content_frame.pack(fill=tk.BOTH, expand=True)

        left_frame = ttk.LabelFrame(content_frame, text="图片分类 (勾选要下载的类别)", padding=10)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))

        canvas = tk.Canvas(left_frame, bg="#1a1a2e", highlightthickness=0)
        scrollbar = ttk.Scrollbar(left_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        for i, (category, desc) in enumerate(UnsplashDownloader.CATEGORIES.items()):
            frame = ttk.Frame(scrollable_frame)
            frame.pack(fill=tk.X, pady=2)

            var = tk.BooleanVar(value=False)
            self.category_vars[category] = var

            check = ttk.Checkbutton(frame, text=f"{desc} ({category})",
                                   variable=var, width=30)
            check.pack(side=tk.LEFT, padx=5)

            count_entry = ttk.Entry(frame, width=8)
            count_entry.insert(0, "30")
            count_entry.pack(side=tk.LEFT, padx=5)
            self.category_counts[category] = count_entry

            progress = ttk.Progressbar(frame, length=100, mode='determinate')
            progress.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
            self.progress_bars[category] = progress

            label = ttk.Label(frame, text="0/0", width=10)
            label.pack(side=tk.LEFT, padx=5)
            self.progress_labels[category] = label

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        right_frame = ttk.Frame(content_frame)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        stats_frame = ttk.LabelFrame(right_frame, text="下载统计", padding=10)
        stats_frame.pack(fill=tk.X, pady=(0, 10))

        self.stats_labels = {}
        stats = ["总计", "成功", "失败", "跳过"]
        for i, stat in enumerate(stats):
            label = ttk.Label(stats_frame, text=f"{stat}: 0", font=("Arial", 11, "bold"))
            label.grid(row=0, column=i, padx=10)
            self.stats_labels[stat] = label

        control_frame = ttk.Frame(right_frame)
        control_frame.pack(fill=tk.X, pady=(0, 10))

        self.start_btn = ttk.Button(control_frame, text="开始下载",
                                    command=self.start_download)
        self.start_btn.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)

        self.pause_btn = ttk.Button(control_frame, text="暂停",
                                    command=self.pause_download, state=tk.DISABLED)
        self.pause_btn.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)

        self.stop_btn = ttk.Button(control_frame, text="停止",
                                   command=self.stop_download, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)

        log_frame = ttk.LabelFrame(right_frame, text="运行日志", padding=10)
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = scrolledtext.ScrolledText(log_frame, height=20, width=60,
                                                   bg="#0f0f23", fg="#00ff00",
                                                   font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)

        self.log_text.tag_config("info", foreground="#00d4ff")
        self.log_text.tag_config("success", foreground="#00ff88")
        self.log_text.tag_config("warning", foreground="#ffaa00")
        self.log_text.tag_config("error", foreground="#ff4444")

    def browse_directory(self):
        """选择保存目录"""
        directory = filedialog.askdirectory()
        if directory:
            self.save_dir_entry.delete(0, tk.END)
            self.save_dir_entry.insert(0, directory)

    def log(self, message, level="info"):
        """添加日志"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_message = f"[{timestamp}] {message}\n"
        self.log_text.insert(tk.END, log_message, level)
        self.log_text.see(tk.END)
        self.root.update_idletasks()

    def update_progress(self, category, current, total):
        """更新进度条"""
        if category in self.progress_bars:
            progress = (current / total) * 100
            self.progress_bars[category]["value"] = progress
            self.progress_labels[category].config(text=f"{current}/{total}")
            self.root.update_idletasks()

    def update_stats(self):
        """更新统计信息"""
        if self.downloader:
            stats = self.downloader.stats
            self.stats_labels["总计"].config(text=f"总计: {stats['total']}")
            self.stats_labels["成功"].config(text=f"成功: {stats['success']}")
            self.stats_labels["失败"].config(text=f"失败: {stats['failed']}")
            self.stats_labels["跳过"].config(text=f"跳过: {stats['skipped']}")
            self.root.after(1000, self.update_stats)

    def start_download(self):
        """开始下载"""
        selected = {}
        for category, var in self.category_vars.items():
            if var.get():
                try:
                    count = int(self.category_counts[category].get())
                    if count > 0:
                        selected[category] = count
                except:
                    pass

        if not selected:
            messagebox.showwarning("提示", "请至少选择一个分类！")
            return

        self.config["api_key"] = self.api_key_entry.get().strip()
        self.config["save_dir"] = self.save_dir_entry.get().strip()
        self.config["resolution"] = self.resolution_combo.get()

        try:
            self.config["batch_size"] = int(self.batch_size_entry.get())
            self.config["delay"] = float(self.delay_entry.get())
            self.config["timeout"] = int(self.timeout_entry.get())
        except:
            messagebox.showerror("错误", "请检查高级设置的数值格式！")
            return

        if not self.config["api_key"]:
            messagebox.showerror("错误", "请输入API Key！")
            return

        self.save_config()

        self.downloader = UnsplashDownloader(
            self.config["api_key"],
            self.config["save_dir"],
            self.config
        )

        for progress in self.progress_bars.values():
            progress["value"] = 0
        for label in self.progress_labels.values():
            label.config(text="0/0")

        self.start_btn.config(state=tk.DISABLED)
        self.pause_btn.config(state=tk.NORMAL, text="暂停")
        self.stop_btn.config(state=tk.NORMAL)

        self.log_text.delete(1.0, tk.END)

        self.downloader.start(selected, self.log, self.update_progress, self.download_complete)
        self.update_stats()

    def pause_download(self):
        """暂停/恢复下载"""
        if self.downloader:
            if self.downloader.is_paused:
                self.downloader.resume()
                self.pause_btn.config(text="暂停")
                self.log("下载已恢复", "info")
            else:
                self.downloader.pause()
                self.pause_btn.config(text="继续")
                self.log("下载已暂停", "warning")

    def stop_download(self):
        """停止下载"""
        if self.downloader:
            self.downloader.stop()
            self.log("下载已停止", "error")
            self.download_complete()

    def download_complete(self):
        """下载完成"""
        self.start_btn.config(state=tk.NORMAL)
        self.pause_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.DISABLED)

    def load_config(self):
        """加载配置"""
        config_file = Path.home() / ".unsplash_downloader.json"
        if config_file.exists():
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    saved_config = json.load(f)
                    self.config.update(saved_config)
                    self.api_key_entry.delete(0, tk.END)
                    self.api_key_entry.insert(0, self.config["api_key"])
                    self.save_dir_entry.delete(0, tk.END)
                    self.save_dir_entry.insert(0, self.config["save_dir"])
            except:
                pass

    def save_config(self):
        """保存配置"""
        config_file = Path.home() / ".unsplash_downloader.json"
        try:
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except:
            pass

    def run(self):
        """运行GUI"""
        self.root.mainloop()


if __name__ == "__main__":
    app = UnsplashGUI()
    app.run()