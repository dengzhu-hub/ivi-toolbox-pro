import asyncio
import aiohttp
import argparse
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import time

from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    BarColumn,
    TextColumn,
    DownloadColumn,
    TransferSpeedColumn,
    TimeRemainingColumn,
)
from rich.panel import Panel
from rich.table import Table

# ==============================
# 🎨 日志配置
# ==============================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('download.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
console = Console()

# ==============================
# 🔑 API Key 管理器
# ==============================
class APIKeyManager:
    """管理多个 API Key 的轮询和状态"""

    def __init__(self, keys: List[str]):
        self.keys = keys
        self.current_index = 0
        self.key_status = {key: {"active": True, "quota_reset": None} for key in keys}

    def get_active_key(self) -> Optional[str]:
        """获取当前可用的 API Key"""
        for _ in range(len(self.keys)):
            key = self.keys[self.current_index]
            if self.key_status[key]["active"]:
                return key
            self.current_index = (self.current_index + 1) % len(self.keys)
        return None

    def mark_key_exhausted(self, key: str):
        """标记 Key 已耗尽配额"""
        self.key_status[key]["active"] = False
        self.key_status[key]["quota_reset"] = datetime.now()
        logger.warning(f"API Key {key[:10]}... 配额耗尽，已切换")
        self.current_index = (self.current_index + 1) % len(self.keys)

# ==============================
# 📡 链接提供者
# ==============================
class LinkProvider:
    """专门负责和 Unsplash 通讯，管理 API Key 轮询"""

    def __init__(self, api_keys: List[str], timeout: int = 10):
        self.key_manager = APIKeyManager(api_keys)
        self.timeout = timeout
        self.session: Optional[aiohttp.ClientSession] = None

    async def initialize(self):
        """初始化异步 session"""
        self.session = aiohttp.ClientSession(
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )

    async def close(self):
        """关闭 session"""
        if self.session:
            await self.session.close()

    async def fetch_image_links(self, topic: str, count: int) -> List[Dict]:
        """批量获取图片链接"""
        url = "https://api.unsplash.com/photos/random"
        all_images = []

        while len(all_images) < count:
            api_key = self.key_manager.get_active_key()
            if not api_key:
                logger.error("所有 API Key 均已耗尽配额")
                break

            batch_size = min(30, count - len(all_images))
            params = {
                "query": topic,
                "count": batch_size,
                "client_id": api_key,
            }

            try:
                async with self.session.get(url, params=params, timeout=self.timeout) as response:
                    if response.status == 403:
                        self.key_manager.mark_key_exhausted(api_key)
                        continue

                    response.raise_for_status()
                    data_list = await response.json()
                    all_images.extend(data_list)

                    await asyncio.sleep(0.5)  # 避免触发限流

            except Exception as e:
                logger.error(f"获取链接失败: {e}")
                await asyncio.sleep(2)

        return all_images[:count]

# ==============================
# 💾 文件下载器
# ==============================
class FileDownloader:
    """专门负责文件下载、重试和校验"""

    def __init__(self, save_dir: Path, resolution: str = "full", max_retries: int = 3):
        self.save_dir = save_dir
        self.resolution = resolution
        self.max_retries = max_retries
        self.session: Optional[aiohttp.ClientSession] = None

    async def initialize(self):
        """初始化下载 session"""
        self.session = aiohttp.ClientSession()

    async def close(self):
        """关闭 session"""
        if self.session:
            await self.session.close()

    def _check_existing_file(self, file_path: Path) -> bool:
        """检查文件是否已存在且有效"""
        if file_path.exists():
            size = file_path.stat().st_size
            if size > 10000:  # 大于 10KB 认为有效
                return True
        return False

    async def download_image(self, image_data: Dict, topic: str, index: int) -> Optional[Dict]:
        """下载单张图片，支持断点续传和重试"""
        image_url = image_data["urls"][self.resolution]
        filename = f"{topic}_{index:03d}_{image_data['id']}.jpg"
        save_path = self.save_dir / filename

        # 断点续传检查
        if self._check_existing_file(save_path):
            logger.info(f"⏭️ 文件已存在，跳过: {filename}")
            return {
                "filename": filename,
                "status": "skipped",
                "size": save_path.stat().st_size
            }

        # 重试机制
        for attempt in range(self.max_retries):
            try:
                async with self.session.get(image_url, timeout=aiohttp.ClientTimeout(total=30)) as response:
                    response.raise_for_status()
                    content = await response.read()

                    # 保存文件
                    save_path.write_bytes(content)

                    # 返回元数据
                    return {
                        "id": image_data["id"],
                        "filename": filename,
                        "photographer": image_data["user"]["name"],
                        "width": image_data["width"],
                        "height": image_data["height"],
                        "color": image_data.get("color", "#000000"),
                        "download_url": image_url,
                        "size": len(content),
                        "status": "success"
                    }

            except Exception as e:
                if attempt < self.max_retries - 1:
                    wait_time = 2 ** attempt  # 指数退避
                    logger.warning(f"下载失败 (重试 {attempt + 1}/{self.max_retries}): {filename}, 等待 {wait_time}s")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"下载最终失败: {filename}, 错误: {e}")
                    return {
                        "filename": filename,
                        "status": "failed",
                        "error": str(e)
                    }

        return None

# ==============================
# 🎯 主下载引擎
# ==============================
class WallpaperDownloadEngine:
    """核心下载引擎，协调所有组件"""

    def __init__(self, config: Dict):
        self.config = config
        self.link_provider = LinkProvider(config["api_keys"], config["timeout"])
        self.file_downloader = FileDownloader(
            Path(config["save_dir"]),
            config["resolution"],
            config["max_retries"]
        )
        self.metadata_store = []

    async def download_topic(self, topic: str, count: int, progress: Progress, task_id):
        """下载指定主题的所有图片"""
        topic_dir = Path(self.config["save_dir"]) / topic
        topic_dir.mkdir(parents=True, exist_ok=True)
        self.file_downloader.save_dir = topic_dir

        # 1. 获取所有图片链接
        progress.update(task_id, description=f"[cyan]🔍 [{topic}] 获取链接...")
        image_links = await self.link_provider.fetch_image_links(topic, count)

        if not image_links:
            logger.error(f"未能获取到 {topic} 的图片链接")
            return

        progress.update(task_id, total=len(image_links), completed=0)
        progress.update(task_id, description=f"[yellow]⬇️ [{topic}] 下载中...")

        # 2. 并发下载
        semaphore = asyncio.Semaphore(self.config["concurrent_downloads"])

        async def download_with_limit(img_data, idx):
            async with semaphore:
                result = await self.file_downloader.download_image(img_data, topic, idx + 1)
                progress.advance(task_id, 1)
                return result

        tasks = [download_with_limit(img, i) for i, img in enumerate(image_links)]
        results = await asyncio.gather(*tasks)

        # 3. 保存元数据
        topic_metadata = {
            "topic": topic,
            "total": count,
            "downloaded": len([r for r in results if r and r.get("status") == "success"]),
            "skipped": len([r for r in results if r and r.get("status") == "skipped"]),
            "failed": len([r for r in results if r and r.get("status") == "failed"]),
            "timestamp": datetime.now().isoformat(),
            "images": [r for r in results if r]
        }

        manifest_path = topic_dir / "manifest.json"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(topic_metadata, f, indent=2, ensure_ascii=False)

        self.metadata_store.append(topic_metadata)

        progress.update(task_id, description=f"[green]✅ [{topic}] 完成")

    async def run(self, topics: Dict[str, int]):
        """运行下载任务"""
        await self.link_provider.initialize()
        await self.file_downloader.initialize()

        try:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                DownloadColumn(),
                TransferSpeedColumn(),
                TimeRemainingColumn(),
                console=console
            ) as progress:

                tasks = []
                for topic, count in topics.items():
                    task_id = progress.add_task(f"[cyan]{topic}", total=count)
                    tasks.append(self.download_topic(topic, count, progress, task_id))

                await asyncio.gather(*tasks)

        finally:
            await self.link_provider.close()
            await self.file_downloader.close()

            # 生成总报告
            self._generate_summary_report()

    def _generate_summary_report(self):
        """生成下载总结报告"""
        table = Table(title="📊 下载总结报告", show_header=True, header_style="bold magenta")
        table.add_column("主题", style="cyan")
        table.add_column("成功", style="green")
        table.add_column("跳过", style="yellow")
        table.add_column("失败", style="red")

        for meta in self.metadata_store:
            table.add_row(
                meta["topic"],
                str(meta["downloaded"]),
                str(meta["skipped"]),
                str(meta["failed"])
            )

        console.print("\n")
        console.print(table)
        console.print(f"\n📁 所有数据已保存至: {self.config['save_dir']}")

# ==============================
# 🚀 命令行入口
# ==============================
def parse_arguments():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description="企业级壁纸下载工具 v2.0")
    parser.add_argument("--config", type=str, default="config_claude.json",help="配置文件路径 (JSON)")
    parser.add_argument("--topic", type=str, help="单个主题名称")
    parser.add_argument("--count", type=int, default=100, help="下载数量")
    parser.add_argument("--resolution", type=str, default="full",
                       choices=["raw", "full", "regular", "small", "thumb"],
                       help="图片分辨率")
    parser.add_argument("--save-dir", type=str, default="./wallpapers", help="保存目录")
    parser.add_argument("--concurrent", type=int, default=5, help="并发下载数")

    return parser.parse_args()

async def main():
    """主程序入口"""
    args = parse_arguments()

    # 默认配置
    config = {
        "api_keys": ["BD0I4Br4tLY4WVyNFCNIzxB-IUn1uMkSP4Ebl8Bf4AY"],  # 支持多个 Key
        "save_dir": args.save_dir,
        "resolution": args.resolution,
        "timeout": 10,
        "max_retries": 3,
        "concurrent_downloads": args.concurrent
    }

    # 如果提供了配置文件，覆盖默认配置
    if args.config and Path(args.config).exists():
        try:
            with open(args.config, "r", encoding="utf-8") as f:
                user_config = json.load(f)
                config.update(user_config)
                console.print(f"[green]已成功加载外部配置: {args.config}[/green]")
        except json.JSONDecodeError:
            console.print(f"[red]错误: 配置文件 {args.config} 格式不正确，请检查 JSON 语法！[/red]")
            return # 或者退出

    # 确定下载主题
    topics = {}
    if args.topic:
        topics[args.topic] = args.count
    else:
        # 默认主题
        topics = {
            "Travel": 100,
            "Nature": 50,
            "Technology": 50
        }

    # 显示启动信息
    console.print(Panel.fit(
        f"[bold cyan]🚀 企业级壁纸下载工具 v2.0[/bold cyan]\n"
        f"📁 保存目录: {config['save_dir']}\n"
        f"🎯 下载主题: {', '.join(topics.keys())}\n"
        f"⚡ 并发数: {config['concurrent_downloads']}\n"
        f"📊 分辨率: {config['resolution']}",
        title="启动信息"
    ))

    # 创建下载引擎并运行
    engine = WallpaperDownloadEngine(config)

    try:
        await engine.run(topics)
        console.print("\n[bold green]🎉 所有任务完成！[/bold green]")
    except KeyboardInterrupt:
        console.print("\n[bold red]🛑 用户中断下载[/bold red]")
    except Exception as e:
        logger.error(f"下载过程出错: {e}", exc_info=True)
        console.print(f"\n[bold red]❌ 错误: {e}[/bold red]")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n\n[bold yellow]👋 已安全退出，感谢使用！[/bold yellow]")