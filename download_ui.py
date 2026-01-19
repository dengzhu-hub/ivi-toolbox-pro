import os
import time
import requests
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.edge.service import Service
from selenium.webdriver.edge.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# 引入专业终端美化库
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, DownloadColumn, TransferSpeedColumn
from rich.panel import Panel
from rich.table import Table

console = Console()

class RedminePreviewDownloader:
    def __init__(self, start_url, download_dir="UI_Preview_Downloads"):
        self.start_url = start_url
        self.download_dir = download_dir
        self.username = "dengzhu"
        self.password = "2001asdf@@D"

        # 1. Edge 浏览器配置 (本地驱动模式)
        edge_options = Options()
        # 移除了 headless，方便你实时查看是否进入了预览页
        edge_options.add_experimental_option('excludeSwitches', ['enable-logging'])

        # 自动初始化驱动 (Selenium 4.x 会自动处理，若报错请手动指定 executable_path)
        self.driver = webdriver.Edge(options=edge_options)
        self.wait = WebDriverWait(self.driver, 20)
        self.session = requests.Session()

    def login(self):
        """专业登录逻辑"""
        with console.status("[bold green]正在执行身份认证...") as status:
            login_url = "http://redmine2.adayotsp.com:1889/login"
            self.driver.get(login_url)
            self.wait.until(EC.presence_of_element_located((By.ID, "username"))).send_keys(self.username)
            self.driver.find_element(By.ID, "password").send_keys(self.password)
            self.driver.find_element(By.NAME, "login").click()

            # 等待确认登录成功
            self.wait.until(EC.presence_of_element_located((By.ID, "loggedas")))

            # 同步 Cookies 到 requests 用于高速下载
            for cookie in self.driver.get_cookies():
                self.session.cookies.set(cookie['name'], cookie['value'])
            console.log("[bold blue]✓ 登录成功，Cookies 已同步。")

    def download_file(self, file_url, save_path, progress, task_id):
        """流式下载，带进度反馈"""
        try:
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            # Redmine URL 转换：show 转换成 raw 才是文件流
            raw_url = file_url.replace('/show/', '/raw/').replace('/changes/', '/raw/')

            response = self.session.get(raw_url, stream=True, timeout=30)
            response.raise_for_status()

            total_size = int(response.headers.get('content-length', 0))
            progress.update(task_id, total=total_size, visible=True)

            with open(save_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
                    progress.update(task_id, advance=len(chunk))
            return True
        except Exception as e:
            console.log(f"[red]✗ 下载异常: {os.path.basename(save_path)} - {e}")
            return False

    def crawl(self, url, local_path, progress):
        """递归爬取，强制锁定 preview 路径"""
        self.driver.get(url)
        try:
            # 兼容 Redmine 不同版本的表格 ID
            self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table.entries, #browser")))
        except:
            return

        # 解析当前页面条目
        rows = self.driver.find_elements(By.CSS_SELECTOR, "tr.dir, tr.file")
        items = []
        for row in rows:
            link = row.find_element(By.CSS_SELECTOR, "td.filename a")
            items.append({
                "name": link.text.strip(),
                "url": link.get_attribute("href"),
                "is_dir": "dir" in row.get_attribute("class")
            })

        for item in items:
            # 路径逻辑：只有在 preview 文件夹内，或者是 preview 文件夹本身才继续
            is_preview_dir = item['name'].lower() == 'preview'
            in_preview_context = 'preview' in local_path.lower()

            if item['is_dir']:
                # 关键：只有我们要找 preview 或者已经在里面了才深入
                if is_preview_dir or in_preview_context:
                    new_path = os.path.join(local_path, item['name'])
                    self.crawl(item['url'], new_path, progress)
                    # 递归回来后重置页面状态
                    self.driver.get(url)
            else:
                # 只有最终路径包含 preview 且是图片才下载
                if in_preview_context and item['name'].lower().endswith(('.png', '.jpg')):
                    save_path = os.path.join(self.download_dir, local_path, item['name'])
                    if not os.path.exists(save_path):
                        task_id = progress.add_task(f"[cyan]同步预览图: {item['name'][:25]}", total=None)
                        self.download_file(item['url'], save_path, progress, task_id)
                        progress.remove_task(task_id)

    def run(self):
        console.print(Panel.fit("座舱测试工程师专用 - UI Preview 下载器", style="bold green"))

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            transient=True,
        ) as progress:
            try:
                self.login()
                main_task = progress.add_task("[yellow]正在扫描仓库结构...", total=None)
                self.crawl(self.start_url, "", progress)
                console.print(Panel("[bold green]同步完成！所有 preview 图片已存至本地目录。"))
            except Exception as e:
                console.log(f"[bold red]程序运行崩溃: {e}")
            finally:
                self.driver.quit()

if __name__ == "__main__":
    # 使用你指定的起始 URL
    TARGET_URL = "http://redmine2.adayotsp.com:1889/projects/r3102038/repository/show/01_DocumentLib/07_SWE.1_SoftwareRequirementsAnalysis/08UI"

    downloader = RedminePreviewDownloader(TARGET_URL)
    downloader.run()