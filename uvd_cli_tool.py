#!/usr/bin/env python3
"""
Universal Video Downloader (UVD) - Professional Edition
一个功能完整的专业级视频下载 CLI 工具

完整支持 yt-dlp 的所有核心功能:
- 网络选项 (代理、重试、超时等)
- 格式选择 (复杂的格式过滤和排序)
- 字幕选项 (多语言、自动字幕)
- 认证选项 (用户名、密码、cookies)
- 后处理选项 (音频提取、格式转换、元数据)
- SponsorBlock 集成
- 提取器参数
- 播放列表处理
"""

import sys
import os
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, field, asdict
from enum import Enum
import json
import logging
from datetime import datetime
import argparse
import re

try:
    import yt_dlp
    from rich.console import Console
    from rich.progress import (
        Progress,
        SpinnerColumn,
        TextColumn,
        BarColumn,
        TaskProgressColumn,
        TimeRemainingColumn,
    )
    from rich.table import Table
    from rich.panel import Panel
    from rich.prompt import Prompt, Confirm, IntPrompt
    from rich.logging import RichHandler
    from rich.tree import Tree
    from rich.syntax import Syntax
    from rich.columns import Columns
    from rich import print as rprint
    from rich.markup import escape
except ImportError as e:
    print(f"❌ 缺少依赖: {e}")
    print("请运行: pip install yt-dlp rich")
    sys.exit(1)


# ==================== 常量定义 ====================

VERSION = "2.0.0"
DEFAULT_OUTPUT_TEMPLATE = "%(title)s [%(id)s].%(ext)s"
CONFIG_DIR = Path.home() / ".uvd"
CONFIG_FILE = CONFIG_DIR / "config.json"
PRESETS_FILE = CONFIG_DIR / "presets.json"
LOG_FILE = CONFIG_DIR / "uvd.log"


# ==================== 枚举类型 ====================


class QualityPreset(Enum):
    """质量预设"""

    BEST = "bestvideo*+bestaudio/best"
    BEST_VIDEO = "bestvideo+bestaudio/best"
    HD_1080P = "bestvideo[height<=1080]+bestaudio/best[height<=1080]"
    HD_720P = "bestvideo[height<=720]+bestaudio/best[height<=720]"
    SD_480P = "bestvideo[height<=480]+bestaudio/best[height<=480]"
    SD_360P = "bestvideo[height<=360]+bestaudio/best[height<=360]"
    AUDIO_BEST = "bestaudio/best"
    AUDIO_MP3 = "bestaudio[ext=mp3]/bestaudio"
    AUDIO_M4A = "bestaudio[ext=m4a]/bestaudio"
    VIDEO_ONLY = "bestvideo"
    WORST = "worstvideo+worstaudio/worst"


class AudioFormat(Enum):
    """音频格式"""

    MP3 = "mp3"
    AAC = "aac"
    M4A = "m4a"
    OPUS = "opus"
    VORBIS = "vorbis"
    FLAC = "flac"
    WAV = "wav"
    BEST = "best"


class VideoFormat(Enum):
    """视频容器格式"""

    MP4 = "mp4"
    MKV = "mkv"
    WEBM = "webm"
    FLV = "flv"
    AVI = "avi"
    MOV = "mov"


class SponsorBlockCategories(Enum):
    """SponsorBlock 分类"""

    SPONSOR = "sponsor"
    INTRO = "intro"
    OUTRO = "outro"
    SELF_PROMO = "selfpromo"
    PREVIEW = "preview"
    FILLER = "filler"
    INTERACTION = "interaction"
    MUSIC_OFFTOPIC = "music_offtopic"
    ALL = "all"


# ==================== 数据类 ====================


@dataclass
class NetworkOptions:
    """网络选项"""

    proxy: Optional[str] = None
    socket_timeout: int = 30
    source_address: Optional[str] = None
    force_ipv4: bool = False
    force_ipv6: bool = False
    geo_verification_proxy: Optional[str] = None
    geo_bypass: bool = False
    geo_bypass_country: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        result = {}
        if self.proxy:
            result["proxy"] = self.proxy
        if self.socket_timeout != 30:
            result["socket_timeout"] = self.socket_timeout
        if self.source_address:
            result["source_address"] = self.source_address
        if self.force_ipv4:
            result["force_ipv4"] = True
        if self.force_ipv6:
            result["force_ipv6"] = True
        if self.geo_verification_proxy:
            result["geo_verification_proxy"] = self.geo_verification_proxy
        if self.geo_bypass:
            result["geo_bypass"] = True
        if self.geo_bypass_country:
            result["geo_bypass_country"] = self.geo_bypass_country
        return result


@dataclass
class DownloadOptions:
    """下载选项"""

    concurrent_fragments: int = 4
    limit_rate: Optional[str] = None  # e.g. "50K" or "4.2M"
    throttled_rate: Optional[str] = None
    retries: int = 10
    file_access_retries: int = 3
    fragment_retries: int = 10
    skip_unavailable_fragments: bool = True
    keep_fragments: bool = False
    buffer_size: int = 1024
    http_chunk_size: Optional[int] = None
    playlist_reverse: bool = False
    playlist_random: bool = False
    external_downloader: Optional[str] = None  # aria2c, axel, curl, wget

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "concurrent_fragment_downloads": self.concurrent_fragments,
            "retries": self.retries,
            "file_access_retries": self.file_access_retries,
            "fragment_retries": self.fragment_retries,
            "skip_unavailable_fragments": self.skip_unavailable_fragments,
            "keepfragments": self.keep_fragments,
            "buffersize": self.buffer_size,
        }

        if self.limit_rate:
            result["ratelimit"] = self._parse_rate(self.limit_rate)
        if self.throttled_rate:
            result["throttledratelimit"] = self._parse_rate(self.throttled_rate)
        if self.http_chunk_size:
            result["http_chunk_size"] = self.http_chunk_size
        if self.playlist_reverse:
            result["playlist_reverse"] = True
        if self.playlist_random:
            result["playlist_random"] = True
        if self.external_downloader:
            result["external_downloader"] = self.external_downloader

        return result

    @staticmethod
    def _parse_rate(rate: str) -> int:
        """解析速率字符串为字节数"""
        units = {"K": 1024, "M": 1024**2, "G": 1024**3}
        match = re.match(r"(\d+(?:\.\d+)?)(K|M|G)?", rate.upper())
        if match:
            value, unit = match.groups()
            multiplier = units.get(unit, 1)
            return int(float(value) * multiplier)
        return int(rate)


@dataclass
class FormatOptions:
    """格式选择选项"""

    format: str = "bestvideo*+bestaudio/best"
    format_sort: Optional[str] = None  # e.g. "res,ext:mp4:m4a"
    video_multistreams: bool = False
    audio_multistreams: bool = False
    prefer_free_formats: bool = False
    check_formats: bool = False
    merge_output_format: Optional[str] = None  # mp4, mkv, webm

    # 格式过滤
    max_filesize: Optional[str] = None
    min_filesize: Optional[str] = None
    max_downloads: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        result = {"format": self.format}

        if self.format_sort:
            result["format_sort"] = self.format_sort
        if self.video_multistreams:
            result["allow_multiple_video_streams"] = True
        if self.audio_multistreams:
            result["allow_multiple_audio_streams"] = True
        if self.prefer_free_formats:
            result["prefer_free_formats"] = True
        if self.check_formats:
            result["check_formats"] = True
        if self.merge_output_format:
            result["merge_output_format"] = self.merge_output_format
        if self.max_filesize:
            result["max_filesize"] = self._parse_size(self.max_filesize)
        if self.min_filesize:
            result["min_filesize"] = self._parse_size(self.min_filesize)
        if self.max_downloads:
            result["max_downloads"] = self.max_downloads

        return result

    @staticmethod
    def _parse_size(size: str) -> int:
        """解析大小字符串"""
        units = {"K": 1024, "M": 1024**2, "G": 1024**3}
        match = re.match(r"(\d+(?:\.\d+)?)(K|M|G)?", size.upper())
        if match:
            value, unit = match.groups()
            multiplier = units.get(unit, 1)
            return int(float(value) * multiplier)
        return int(size)


@dataclass
class SubtitleOptions:
    """字幕选项"""

    write_subs: bool = False
    write_auto_subs: bool = False
    list_subs: bool = False
    sub_format: str = "best"  # srt, ass, vtt, best
    sub_langs: List[str] = field(default_factory=lambda: ["en"])
    embed_subs: bool = False
    convert_subs: Optional[str] = None  # srt, ass, vtt, lrc

    def to_dict(self) -> Dict[str, Any]:
        result = {}

        if self.write_subs:
            result["writesubtitles"] = True
        if self.write_auto_subs:
            result["writeautomaticsub"] = True
        if self.list_subs:
            result["listsubtitles"] = True

        result["subtitlesformat"] = self.sub_format
        result["subtitleslangs"] = self.sub_langs

        if self.embed_subs:
            result["embedsubtitles"] = True
        if self.convert_subs:
            result["postprocessors"] = result.get("postprocessors", [])
            result["postprocessors"].append(
                {
                    "key": "FFmpegSubtitlesConvertor",
                    "format": self.convert_subs,
                }
            )

        return result


@dataclass
class AuthenticationOptions:
    """认证选项"""

    username: Optional[str] = None
    password: Optional[str] = None
    twofactor: Optional[str] = None
    netrc: bool = False
    video_password: Optional[str] = None
    cookies: Optional[str] = None  # path to cookies file
    cookies_from_browser: Optional[str] = None  # chrome, firefox, etc.

    def to_dict(self) -> Dict[str, Any]:
        result = {}

        if self.username:
            result["username"] = self.username
        if self.password:
            result["password"] = self.password
        if self.twofactor:
            result["twofactor"] = self.twofactor
        if self.netrc:
            result["usenetrc"] = True
        if self.video_password:
            result["videopassword"] = self.video_password
        if self.cookies:
            result["cookiefile"] = self.cookies
        if self.cookies_from_browser:
            result["cookiesfrombrowser"] = (self.cookies_from_browser, None, None, None)

        return result


@dataclass
class PostProcessingOptions:
    """后处理选项"""

    extract_audio: bool = False
    audio_format: AudioFormat = AudioFormat.MP3
    audio_quality: str = "192K"  # 0-10 or bitrate like 128K

    recode_video: Optional[VideoFormat] = None
    remux_video: Optional[str] = None

    embed_thumbnail: bool = False
    embed_metadata: bool = False
    embed_chapters: bool = False
    embed_info_json: bool = False

    write_description: bool = False
    write_info_json: bool = False
    write_annotations: bool = False
    write_thumbnail: bool = False
    write_all_thumbnails: bool = False

    add_metadata: bool = False
    parse_metadata: Optional[List[str]] = None

    # SponsorBlock
    sponsorblock_mark: Optional[List[str]] = None
    sponsorblock_remove: Optional[List[str]] = None
    sponsorblock_chapter_title: Optional[str] = None

    # 其他
    fixup: str = "detect_or_warn"  # never, warn, detect_or_warn, force
    prefer_ffmpeg: bool = True
    ffmpeg_location: Optional[str] = None
    exec_cmd: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        result = {"postprocessors": []}

        # 音频提取
        if self.extract_audio:
            result["postprocessors"].append(
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": self.audio_format.value,
                    "preferredquality": self.audio_quality,
                }
            )

        # 视频转码/重新封装
        if self.recode_video:
            result["postprocessors"].append(
                {
                    "key": "FFmpegVideoConvertor",
                    "preferedformat": self.recode_video.value,
                }
            )

        if self.remux_video:
            result["postprocessors"].append(
                {
                    "key": "FFmpegVideoRemuxer",
                    "preferedformat": self.remux_video,
                }
            )

        # 元数据嵌入
        if self.embed_metadata or self.add_metadata:
            result["postprocessors"].append(
                {
                    "key": "FFmpegMetadata",
                    "add_metadata": True,
                    "add_chapters": self.embed_chapters,
                    "add_infojson": (
                        self.embed_info_json if self.embed_info_json else None
                    ),
                }
            )

        # 缩略图嵌入
        if self.embed_thumbnail:
            result["writethumbnail"] = True
            result["postprocessors"].append(
                {
                    "key": "EmbedThumbnail",
                }
            )

        # SponsorBlock
        if self.sponsorblock_mark:
            result["postprocessors"].append(
                {
                    "key": "SponsorBlock",
                    "categories": self.sponsorblock_mark,
                }
            )

        if self.sponsorblock_remove:
            result["postprocessors"].append(
                {
                    "key": "ModifyChapters",
                    "remove_sponsor_segments": self.sponsorblock_remove,
                }
            )

        # 文件写入选项
        if self.write_description:
            result["writedescription"] = True
        if self.write_info_json:
            result["writeinfojson"] = True
        if self.write_annotations:
            result["writeannotations"] = True
        if self.write_thumbnail:
            result["writethumbnail"] = True
        if self.write_all_thumbnails:
            result["writeall_thumbnails"] = True

        # 元数据解析
        if self.parse_metadata:
            result["parse_metadata"] = self.parse_metadata

        # 修复策略
        result["fixup"] = self.fixup

        # FFmpeg
        if self.prefer_ffmpeg:
            result["prefer_ffmpeg"] = True
        if self.ffmpeg_location:
            result["ffmpeg_location"] = self.ffmpeg_location

        # 执行命令
        if self.exec_cmd:
            result["exec_cmd"] = self.exec_cmd

        return result


@dataclass
class FilesystemOptions:
    """文件系统选项"""

    output_path: Path = field(default_factory=Path.cwd)
    output_template: str = DEFAULT_OUTPUT_TEMPLATE
    output_na_placeholder: str = "NA"

    restrict_filenames: bool = False
    windows_filenames: bool = False
    trim_filenames: Optional[int] = None

    no_overwrites: bool = True
    force_overwrites: bool = False
    continue_dl: bool = True
    no_part: bool = False

    no_mtime: bool = False
    write_description: bool = False
    write_info_json: bool = False
    write_annotations: bool = False

    cookies: Optional[str] = None
    cache_dir: Optional[str] = None
    no_cache_dir: bool = False
    rm_cache_dir: bool = False

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "outtmpl": str(self.output_path / self.output_template),
            "output_na_placeholder": self.output_na_placeholder,
            "nooverwrites": self.no_overwrites,
            "continue": self.continue_dl,
            "noprogress": False,
        }

        if self.restrict_filenames:
            result["restrictfilenames"] = True
        if self.windows_filenames:
            result["windowsfilenames"] = True
        if self.trim_filenames:
            result["trim_file_name"] = self.trim_filenames
        if self.force_overwrites:
            result["overwrites"] = True
        if self.no_part:
            result["nopart"] = True
        if not self.no_mtime:
            result["updatetime"] = True

        if self.cookies:
            result["cookiefile"] = self.cookies
        if self.cache_dir:
            result["cachedir"] = self.cache_dir
        if self.no_cache_dir:
            result["cachedir"] = False
        if self.rm_cache_dir:
            result["rm_cachedir"] = True

        return result


@dataclass
class PlaylistOptions:
    """播放列表选项"""

    yes_playlist: bool = True
    playlist_items: Optional[str] = None  # e.g. "1-5,7,10-20"
    playlist_start: int = 1
    playlist_end: Optional[int] = None
    playlist_reverse: bool = False
    playlist_random: bool = False

    age_limit: Optional[int] = None
    download_archive: Optional[str] = None
    break_on_existing: bool = False
    break_on_reject: bool = False
    skip_playlist_after_errors: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        result = {}

        if not self.yes_playlist:
            result["noplaylist"] = True

        if self.playlist_items:
            result["playlist_items"] = self.playlist_items
        if self.playlist_start != 1:
            result["playliststart"] = self.playlist_start
        if self.playlist_end:
            result["playlistend"] = self.playlist_end
        if self.playlist_reverse:
            result["playlist_reverse"] = True
        if self.playlist_random:
            result["playlistrandom"] = True

        if self.age_limit:
            result["age_limit"] = self.age_limit
        if self.download_archive:
            result["download_archive"] = self.download_archive
        if self.break_on_existing:
            result["break_on_existing"] = True
        if self.break_on_reject:
            result["break_on_reject"] = True
        if self.skip_playlist_after_errors:
            result["skip_playlist_after_errors"] = self.skip_playlist_after_errors

        return result


@dataclass
class VerbosityOptions:
    """详细度选项"""

    quiet: bool = False
    no_warnings: bool = False
    verbose: bool = False
    print_traffic: bool = False
    dump_intermediate_pages: bool = False
    write_pages: bool = False

    simulate: bool = False
    skip_download: bool = False

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "quiet": self.quiet,
            "no_warnings": self.no_warnings,
            "verbose": self.verbose,
        }

        if self.print_traffic:
            result["dump_single_json"] = True
        if self.dump_intermediate_pages:
            result["dump_intermediate_pages"] = True
        if self.write_pages:
            result["writepages"] = True
        if self.simulate:
            result["simulate"] = True
        if self.skip_download:
            result["skip_download"] = True

        return result


@dataclass
class DownloadConfig:
    """完整下载配置"""

    urls: List[str] = field(default_factory=list)

    # 各类选项组
    network: NetworkOptions = field(default_factory=NetworkOptions)
    download: DownloadOptions = field(default_factory=DownloadOptions)
    format: FormatOptions = field(default_factory=FormatOptions)
    subtitle: SubtitleOptions = field(default_factory=SubtitleOptions)
    auth: AuthenticationOptions = field(default_factory=AuthenticationOptions)
    postprocess: PostProcessingOptions = field(default_factory=PostProcessingOptions)
    filesystem: FilesystemOptions = field(default_factory=FilesystemOptions)
    playlist: PlaylistOptions = field(default_factory=PlaylistOptions)
    verbosity: VerbosityOptions = field(default_factory=VerbosityOptions)

    # 提取器参数
    extractor_args: Dict[str, str] = field(default_factory=dict)

    def to_ydl_opts(self) -> Dict[str, Any]:
        """转换为 yt-dlp 选项字典"""
        opts = {}

        # 合并所有选项组
        opts.update(self.network.to_dict())
        opts.update(self.download.to_dict())
        opts.update(self.format.to_dict())
        opts.update(self.subtitle.to_dict())
        opts.update(self.auth.to_dict())
        opts.update(self.filesystem.to_dict())
        opts.update(self.playlist.to_dict())
        opts.update(self.verbosity.to_dict())

        # 后处理需要特殊处理（合并 postprocessors）
        pp_opts = self.postprocess.to_dict()
        if "postprocessors" in pp_opts:
            opts["postprocessors"] = (
                opts.get("postprocessors", []) + pp_opts["postprocessors"]
            )
            del pp_opts["postprocessors"]
        opts.update(pp_opts)

        # 提取器参数
        if self.extractor_args:
            opts["extractor_args"] = self.extractor_args

        return opts

    def save_preset(self, name: str) -> None:
        """保存配置预设"""
        PRESETS_FILE.parent.mkdir(parents=True, exist_ok=True)

        presets = {}
        if PRESETS_FILE.exists():
            presets = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))

        # 序列化配置（排除 URLs）
        config_dict = asdict(self)
        config_dict.pop("urls", None)
        presets[name] = config_dict

        PRESETS_FILE.write_text(
            json.dumps(presets, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    @classmethod
    def load_preset(cls, name: str) -> Optional["DownloadConfig"]:
        """加载配置预设"""
        if not PRESETS_FILE.exists():
            return None

        presets = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
        if name not in presets:
            return None

        config_dict = presets[name]
        # 重建嵌套对象
        return cls(**config_dict)


@dataclass
class DownloadStats:
    """下载统计"""

    total_videos: int = 0
    successful: int = 0
    failed: int = 0
    skipped: int = 0
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None

    def duration(self) -> str:
        if self.start_time and self.end_time:
            delta = self.end_time - self.start_time
            return str(delta).split(".")[0]
        return "N/A"


# ==================== 核心下载器 ====================


class UniversalVideoDownloader:
    """通用视频下载器"""

    def __init__(self, config: DownloadConfig, console: Console):
        self.config = config
        self.console = console
        self.stats = DownloadStats()
        self.logger = self._setup_logger()
        self.current_info = {}

    def _setup_logger(self) -> logging.Logger:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)

        logger = logging.getLogger("UVD")
        logger.setLevel(logging.DEBUG)

        # 清除现有处理器
        logger.handlers.clear()

        # 文件处理器
        fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )

        # Rich 处理器
        if not self.config.verbosity.quiet:
            rh = RichHandler(
                console=self.console, rich_tracebacks=True, show_time=False
            )
            rh.setLevel(
                logging.INFO if self.config.verbosity.verbose else logging.WARNING
            )
            logger.addHandler(rh)

        logger.addHandler(fh)
        return logger

    def _progress_hook(self, d: Dict[str, Any]) -> None:
        """进度回调"""
        if d["status"] == "downloading":
            if not self.config.verbosity.quiet:
                filename = Path(d.get("filename", "unknown")).name
                downloaded = d.get("downloaded_bytes", 0)
                total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
                speed = d.get("speed", 0)
                eta = d.get("eta", 0)

                if total > 0:
                    percent = (downloaded / total) * 100
                    speed_str = f"{speed / 1024 / 1024:.2f} MB/s" if speed else "N/A"
                    eta_str = f"{eta}s" if eta else "N/A"

                    self.console.print(
                        f"  ⬇  {filename[:40]:<40} {percent:5.1f}% | {speed_str} | ETA: {eta_str}",
                        end="\r",
                    )

        elif d["status"] == "finished":
            filename = Path(d.get("filename", "unknown")).name
            if not self.config.verbosity.quiet:
                self.console.print(
                    f"  ✅ 下载完成: {filename}                                    "
                )
            self.stats.successful += 1
            self.logger.info(f"成功下载: {filename}")

        elif d["status"] == "error":
            self.stats.failed += 1
            self.logger.error(f"下载错误: {d.get('error', 'Unknown error')}")

    def download(self) -> bool:
        """执行下载"""
        self.stats.start_time = datetime.now()
        self.stats.total_videos = len(self.config.urls)

        # 准备 yt-dlp 选项
        ydl_opts = self.config.to_ydl_opts()
        ydl_opts["progress_hooks"] = [self._progress_hook]
        ydl_opts["logger"] = self.logger

        # 显示配置摘要
        if not self.config.verbosity.quiet:
            self._show_config_summary()

        # 创建输出目录
        self.config.filesystem.output_path.mkdir(parents=True, exist_ok=True)

        # 下载过程
        for idx, url in enumerate(self.config.urls, 1):
            if not self.config.verbosity.quiet:
                self.console.print(
                    f"\n[bold cyan]═══ ({idx}/{self.stats.total_videos}) 处理: {url[:80]}...[/]"
                )

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    self.logger.info(f"开始下载: {url}")

                    # 提取信息
                    if not self.config.verbosity.skip_download:
                        info = ydl.extract_info(url, download=True)
                        self.current_info = info
                    else:
                        info = ydl.extract_info(url, download=False)
                        self.current_info = info
                        if not self.config.verbosity.quiet:
                            self._show_video_info(info)

            except yt_dlp.utils.DownloadError as e:
                if not self.config.verbosity.quiet:
                    self.console.print(f"  ❌ 下载失败: {str(e)}", style="red")
                self.logger.error(f"下载失败 {url}: {e}")
                self.stats.failed += 1

            except KeyboardInterrupt:
                self.console.print("\n⚠️  用户中断下载", style="yellow")
                raise

            except Exception as e:
                if not self.config.verbosity.quiet:
                    self.console.print(f"  ❌ 未知错误: {str(e)}", style="red")
                self.logger.exception(f"未知错误 {url}")
                self.stats.failed += 1

        self.stats.end_time = datetime.now()

        if not self.config.verbosity.quiet:
            self._show_summary()

        return self.stats.failed == 0

    def _show_config_summary(self) -> None:
        """显示配置摘要"""
        table = Table(
            title="📋 下载配置摘要", show_header=True, header_style="bold cyan"
        )
        table.add_column("类别", style="cyan", width=20)
        table.add_column("配置项", style="yellow", width=25)
        table.add_column("值", style="green")

        # 基础信息
        table.add_row("基础", "视频数量", str(self.stats.total_videos))
        table.add_row("", "输出路径", str(self.config.filesystem.output_path))
        table.add_row("", "输出模板", self.config.filesystem.output_template)

        # 格式选项
        table.add_row("格式", "格式选择器", self.config.format.format)
        if self.config.format.format_sort:
            table.add_row("", "格式排序", self.config.format.format_sort)
        if self.config.format.merge_output_format:
            table.add_row("", "合并格式", self.config.format.merge_output_format)

        # 网络选项
        if self.config.network.proxy:
            table.add_row("网络", "代理", self.config.network.proxy)
        table.add_row("", "超时时间", f"{self.config.network.socket_timeout}s")

        # 下载选项
        table.add_row(
            "下载", "并发片段", str(self.config.download.concurrent_fragments)
        )
        table.add_row("", "重试次数", str(self.config.download.retries))
        if self.config.download.limit_rate:
            table.add_row("", "速率限制", self.config.download.limit_rate)
        if self.config.download.external_downloader:
            table.add_row("", "外部下载器", self.config.download.external_downloader)

        # 字幕选项
        if self.config.subtitle.write_subs or self.config.subtitle.write_auto_subs:
            subs = "手动" if self.config.subtitle.write_subs else ""
            subs += "+自动" if self.config.subtitle.write_auto_subs else ""
            table.add_row("字幕", "下载字幕", subs)
            table.add_row("", "语言", ", ".join(self.config.subtitle.sub_langs))
            if self.config.subtitle.embed_subs:
                table.add_row("", "嵌入", "✓")

        # 后处理
        pp_items = []
        if self.config.postprocess.extract_audio:
            pp_items.append(f"音频提取({self.config.postprocess.audio_format.value})")
        if self.config.postprocess.embed_metadata:
            pp_items.append("元数据嵌入")
        if self.config.postprocess.embed_thumbnail:
            pp_items.append("缩略图嵌入")
        if self.config.postprocess.sponsorblock_mark:
            pp_items.append("SponsorBlock标记")
        if self.config.postprocess.sponsorblock_remove:
            pp_items.append("SponsorBlock移除")

        if pp_items:
            table.add_row("后处理", "启用项", "\n".join(pp_items))

        # 认证
        if self.config.auth.username:
            table.add_row("认证", "用户名", self.config.auth.username)
        if self.config.auth.cookies:
            table.add_row("", "Cookies文件", self.config.auth.cookies)
        if self.config.auth.cookies_from_browser:
            table.add_row("", "浏览器Cookie", self.config.auth.cookies_from_browser)

        # 播放列表
        if not self.config.playlist.yes_playlist:
            table.add_row("播放列表", "模式", "仅单个视频")
        elif self.config.playlist.playlist_items:
            table.add_row("播放列表", "项目范围", self.config.playlist.playlist_items)

        self.console.print(table)
        self.console.print()

    def _show_video_info(self, info: Dict[str, Any]) -> None:
        """显示视频信息"""
        if not info:
            return

        # 处理播放列表
        if "_type" in info and info["_type"] == "playlist":
            self.console.print(f"  📋 播放列表: {info.get('title', 'Unknown')}")
            self.console.print(
                f"  📊 包含 {info.get('playlist_count', len(info.get('entries', [])))} 个视频"
            )
            return

        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("Key", style="cyan")
        table.add_column("Value", style="white")

        table.add_row("标题", info.get("title", "N/A"))
        table.add_row("ID", info.get("id", "N/A"))
        table.add_row("上传者", info.get("uploader", "N/A"))

        if "duration" in info:
            duration = info["duration"]
            duration_str = f"{duration // 3600:02d}:{(duration % 3600) // 60:02d}:{duration % 60:02d}"
            table.add_row("时长", duration_str)

        if "view_count" in info:
            table.add_row("观看数", f"{info['view_count']:,}")

        if "like_count" in info:
            table.add_row("点赞数", f"{info['like_count']:,}")

        # 格式信息
        if "formats" in info:
            best_format = max(info["formats"], key=lambda f: f.get("height", 0) or 0)
            if best_format.get("height"):
                table.add_row(
                    "最佳分辨率",
                    f"{best_format.get('width')}x{best_format.get('height')}",
                )

        self.console.print(table)

    def _show_summary(self) -> None:
        """显示下载摘要"""
        self.console.print("\n" + "=" * 70)

        # 构建统计文本
        stats_parts = []
        stats_parts.append(f"[bold cyan]总计:[/] {self.stats.total_videos} 个视频")
        stats_parts.append(f"[bold green]成功:[/] {self.stats.successful}")

        if self.stats.failed > 0:
            stats_parts.append(f"[bold red]失败:[/] {self.stats.failed}")

        if self.stats.skipped > 0:
            stats_parts.append(f"[bold yellow]跳过:[/] {self.stats.skipped}")

        stats_parts.append(f"[bold blue]用时:[/] {self.stats.duration()}")

        stats_text = "\n".join(stats_parts)

        panel = Panel(
            stats_text,
            title="📊 下载统计",
            border_style="cyan" if self.stats.failed == 0 else "red",
        )

        self.console.print(panel)

        # 显示日志位置
        self.console.print(f"\n📝 详细日志: {LOG_FILE}", style="dim")


# ==================== 交互式界面 ====================


class InteractiveUI:
    """交互式配置向导"""

    def __init__(self, console: Console):
        self.console = console
        self.config = DownloadConfig()

    def show_banner(self) -> None:
        banner = f"""
[bold cyan]
╔════════════════════════════════════════════════════════════════╗
║                                                                ║
║   Universal Video Downloader v{VERSION} - Professional Edition   ║
║   功能完整的专业级视频下载工具                                    ║
║   完整支持 yt-dlp 的所有核心参数                                  ║
║                                                                ║
╚════════════════════════════════════════════════════════════════╝
[/bold cyan]
        """
        self.console.print(banner)

    def select_preset(self) -> bool:
        """选择预设配置"""
        if not PRESETS_FILE.exists():
            return False

        presets = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
        if not presets:
            return False

        if Confirm.ask("\n是否使用已保存的预设配置?", default=False):
            preset_names = list(presets.keys())

            self.console.print("\n[bold]可用预设:[/]")
            for idx, name in enumerate(preset_names, 1):
                self.console.print(f"  {idx}. {name}")

            choice = IntPrompt.ask(
                "选择预设", choices=[str(i) for i in range(1, len(preset_names) + 1)]
            )

            preset_name = preset_names[int(choice) - 1]
            loaded_config = DownloadConfig.load_preset(preset_name)
            if loaded_config:
                self.config = loaded_config
                self.console.print(f"✓ 已加载预设: {preset_name}", style="green")
                return True

        return False

    def input_urls(self) -> List[str]:
        """输入 URL"""
        self.console.print("\n[bold cyan]═══ 步骤 1/8: 输入视频 URL ═══[/]")
        self.console.print("  • 单个 URL: 直接粘贴")
        self.console.print("  • 多个 URL: 每行一个，输入空行结束")
        self.console.print("  • 从文件: 输入 'file:/path/to/urls.txt'\n")

        urls = []
        first_input = Prompt.ask("URL", default="").strip()

        if first_input.startswith("file:"):
            filepath = Path(first_input[5:].strip())
            if filepath.exists():
                urls = filepath.read_text(encoding="utf-8").strip().split("\n")
                urls = [u.strip() for u in urls if u.strip() and not u.startswith("#")]
                self.console.print(f"✓ 从文件加载了 {len(urls)} 个 URL", style="green")
            else:
                self.console.print(f"❌ 文件不存在: {filepath}", style="red")
                return []
        elif first_input:
            urls.append(first_input)
            self.console.print("\n[dim]继续输入 URL，输入空行结束:[/]")
            while True:
                url = Prompt.ask("URL", default="").strip()
                if not url:
                    break
                urls.append(url)

        return urls

    def configure_format(self) -> None:
        """配置格式选项"""
        self.console.print("\n[bold cyan]═══ 步骤 2/8: 格式与质量 ═══[/]")

        # 质量预设
        self.console.print("\n[bold]1. 选择质量预设:[/]")
        presets = [
            ("最佳质量（视频+音频，智能合并）", QualityPreset.BEST),
            ("最佳视频 + 最佳音频", QualityPreset.BEST_VIDEO),
            ("1080p 高清", QualityPreset.HD_1080P),
            ("720p 高清", QualityPreset.HD_720P),
            ("480p 标清", QualityPreset.SD_480P),
            ("360p 低清", QualityPreset.SD_360P),
            ("仅音频（最佳）", QualityPreset.AUDIO_BEST),
            ("仅视频", QualityPreset.VIDEO_ONLY),
            ("自定义格式字符串", None),
        ]

        for idx, (desc, _) in enumerate(presets, 1):
            self.console.print(f"  {idx}. {desc}")

        choice = IntPrompt.ask(
            "选择质量",
            choices=[str(i) for i in range(1, len(presets) + 1)],
            default="2",
        )

        if choice == len(presets):
            custom_format = Prompt.ask(
                "输入自定义格式", default="bestvideo[height<=1080]+bestaudio/best"
            )
            self.config.format.format = custom_format
        else:
            self.config.format.format = presets[choice - 1][1].value

        # 高级格式选项
        if Confirm.ask("\n是否配置高级格式选项?", default=False):
            # 格式排序
            if Confirm.ask("  启用自定义格式排序?", default=False):
                self.console.print("  示例: res,ext:mp4:m4a 或 +size,+br,+res,+fps")
                self.config.format.format_sort = Prompt.ask("  格式排序规则")

            # 多流支持
            self.config.format.video_multistreams = Confirm.ask(
                "  允许多视频流?", default=False
            )
            self.config.format.audio_multistreams = Confirm.ask(
                "  允许多音频流?", default=False
            )

            # 文件大小限制
            if Confirm.ask("  设置文件大小限制?", default=False):
                self.config.format.max_filesize = Prompt.ask(
                    "  最大文件大小 (如: 500M, 2G)", default=""
                )
                self.config.format.min_filesize = Prompt.ask(
                    "  最小文件大小 (如: 10M)", default=""
                )

            # 合并格式
            merge_formats = ["mp4", "mkv", "webm", "无"]
            self.console.print(f"\n  合并输出格式: {', '.join(merge_formats[:-1])}")
            merge_choice = Prompt.ask(
                "  选择合并格式", choices=merge_formats, default="无"
            )
            if merge_choice != "无":
                self.config.format.merge_output_format = merge_choice

    def configure_network(self) -> None:
        """配置网络选项"""
        if not Confirm.ask(
            "\n[bold cyan]═══ 步骤 3/8: 网络选项 ═══[/]\n是否配置网络选项?",
            default=False,
        ):
            return

        # 代理
        if Confirm.ask("  使用代理?", default=False):
            self.console.print("  格式: http://proxy:port 或 socks5://proxy:port")
            self.config.network.proxy = Prompt.ask("  代理地址")

        # 超时
        self.config.network.socket_timeout = IntPrompt.ask(
            "  Socket 超时时间(秒)", default=30
        )

        # IP版本
        if Confirm.ask("  强制使用特定IP版本?", default=False):
            ip_choice = Prompt.ask("  选择", choices=["ipv4", "ipv6"], default="ipv4")
            if ip_choice == "ipv4":
                self.config.network.force_ipv4 = True
            else:
                self.config.network.force_ipv6 = True

        # 地理绕过
        if Confirm.ask("  启用地理限制绕过?", default=False):
            self.config.network.geo_bypass = True
            if Confirm.ask("    指定国家代码? (如: US, JP)", default=False):
                self.config.network.geo_bypass_country = Prompt.ask(
                    "    国家代码"
                ).upper()

    def configure_download(self) -> None:
        """配置下载选项"""
        self.console.print("\n[bold cyan]═══ 步骤 4/8: 下载选项 ═══[/]")

        # 并发片段
        self.config.download.concurrent_fragments = IntPrompt.ask(
            "并发下载片段数 (1-16, 推荐4-8)", default=4
        )

        # 重试次数
        if Confirm.ask("自定义重试次数?", default=False):
            self.config.download.retries = IntPrompt.ask("  下载重试次数", default=10)
            self.config.download.fragment_retries = IntPrompt.ask(
                "  片段重试次数", default=10
            )

        # 速率限制
        if Confirm.ask("设置下载速率限制?", default=False):
            self.console.print("  格式: 50K, 4.2M, 1G")
            self.config.download.limit_rate = Prompt.ask("  最大速率")

        # 外部下载器
        if Confirm.ask("使用外部下载器?", default=False):
            downloaders = ["aria2c", "axel", "curl", "wget", "ffmpeg"]
            self.console.print(f"  可用: {', '.join(downloaders)}")
            downloader = Prompt.ask("  选择下载器", choices=downloaders)
            self.config.download.external_downloader = downloader

        # 播放列表选项
        if Confirm.ask("配置播放列表选项?", default=False):
            self.config.download.playlist_reverse = Confirm.ask(
                "  反向下载播放列表?", default=False
            )
            self.config.download.playlist_random = Confirm.ask(
                "  随机下载播放列表?", default=False
            )

    def configure_subtitles(self) -> None:
        """配置字幕选项"""
        if not Confirm.ask(
            "\n[bold cyan]═══ 步骤 5/8: 字幕选项 ═══[/]\n是否下载字幕?", default=False
        ):
            return

        self.config.subtitle.write_subs = Confirm.ask("  下载手动字幕?", default=True)
        self.config.subtitle.write_auto_subs = Confirm.ask(
            "  下载自动生成字幕?", default=True
        )

        # 字幕语言
        self.console.print("\n  字幕语言代码 (逗号分隔):")
        self.console.print("  常用: en, zh-Hans, zh-Hant, ja, ko, es, fr, de")
        self.console.print("  使用 'all' 下载所有可用语言")

        langs_input = Prompt.ask("  语言代码", default="en")
        if langs_input == "all":
            self.config.subtitle.sub_langs = ["all"]
        else:
            self.config.subtitle.sub_langs = [
                lang.strip() for lang in langs_input.split(",")
            ]

        # 字幕格式
        sub_formats = ["best", "srt", "ass", "vtt", "lrc"]
        self.config.subtitle.sub_format = Prompt.ask(
            "  字幕格式", choices=sub_formats, default="best"
        )

        # 嵌入字幕
        self.config.subtitle.embed_subs = Confirm.ask(
            "  嵌入字幕到视频?", default=False
        )

        # 转换格式
        if Confirm.ask("  转换字幕格式?", default=False):
            convert_formats = ["srt", "ass", "vtt", "lrc"]
            self.config.subtitle.convert_subs = Prompt.ask(
                "  目标格式", choices=convert_formats
            )

    def configure_auth(self) -> None:
        """配置认证选项"""
        if not Confirm.ask(
            "\n[bold cyan]═══ 步骤 6/8: 认证选项 ═══[/]\n需要认证?", default=False
        ):
            return

        auth_methods = [
            "用户名密码",
            "Cookies 文件",
            "从浏览器导入 Cookies",
            ".netrc 文件",
        ]

        self.console.print("\n认证方式:")
        for idx, method in enumerate(auth_methods, 1):
            self.console.print(f"  {idx}. {method}")

        choice = IntPrompt.ask("选择认证方式", choices=[str(i) for i in range(1, 5)])

        if choice == 1:
            self.config.auth.username = Prompt.ask("  用户名")
            self.config.auth.password = Prompt.ask("  密码", password=True)
            if Confirm.ask("  需要两步验证?", default=False):
                self.config.auth.twofactor = Prompt.ask("  两步验证码")

        elif choice == 2:
            self.config.auth.cookies = Prompt.ask("  Cookies 文件路径")

        elif choice == 3:
            browsers = ["chrome", "firefox", "edge", "safari", "opera", "brave"]
            self.console.print(f"  支持浏览器: {', '.join(browsers)}")
            browser = Prompt.ask("  选择浏览器", choices=browsers)
            self.config.auth.cookies_from_browser = browser

        elif choice == 4:
            self.config.auth.netrc = True
            self.console.print("  ✓ 将使用 ~/.netrc 文件", style="green")

        # 视频密码
        if Confirm.ask("\n  视频需要密码?", default=False):
            self.config.auth.video_password = Prompt.ask("  视频密码", password=True)

    def configure_postprocessing(self) -> None:
        """配置后处理选项"""
        self.console.print("\n[bold cyan]═══ 步骤 7/8: 后处理选项 ═══[/]")

        # 音频提取
        if Confirm.ask("提取音频?", default=False):
            self.config.postprocess.extract_audio = True

            formats = [fmt.value for fmt in AudioFormat]
            self.console.print(f"  音频格式: {', '.join(formats)}")
            audio_format = Prompt.ask("  选择格式", choices=formats, default="mp3")
            self.config.postprocess.audio_format = AudioFormat(audio_format)

            self.console.print("  音质: 0-10 (VBR) 或比特率如 128K, 192K, 320K")
            self.config.postprocess.audio_quality = Prompt.ask(
                "  音频质量", default="192K"
            )

        # 视频转换
        if Confirm.ask("重新编码/封装视频?", default=False):
            operations = ["重新封装（快速）", "重新编码（慢，质量损失）"]
            op_choice = Prompt.ask("  选择操作", choices=["1", "2"], default="1")

            video_formats = [fmt.value for fmt in VideoFormat]
            self.console.print(f"  格式: {', '.join(video_formats)}")
            fmt = Prompt.ask("  目标格式", choices=video_formats, default="mp4")

            if op_choice == "1":
                self.config.postprocess.remux_video = fmt
            else:
                self.config.postprocess.recode_video = VideoFormat(fmt)

        # 元数据
        metadata_options = {
            "嵌入元数据": "embed_metadata",
            "嵌入缩略图": "embed_thumbnail",
            "嵌入章节": "embed_chapters",
            "写入描述文件": "write_description",
            "写入 info.json": "write_info_json",
        }

        self.console.print("\n元数据选项:")
        for option in metadata_options:
            self.console.print(f"  • {option}")

        if Confirm.ask("启用元数据处理?", default=True):
            for desc, attr in metadata_options.items():
                enabled = Confirm.ask(f"  {desc}?", default=attr.startswith("embed"))
                setattr(self.config.postprocess, attr, enabled)

        # SponsorBlock
        if Confirm.ask("\nSponsorBlock 集成（YouTube）?", default=False):
            self.console.print(
                "  可用分类: sponsor, intro, outro, selfpromo, preview, filler, interaction, music_offtopic"
            )

            if Confirm.ask("  标记赞助片段?", default=False):
                categories = Prompt.ask(
                    "  分类(逗号分隔)", default="sponsor,selfpromo"
                ).split(",")
                self.config.postprocess.sponsorblock_mark = [
                    c.strip() for c in categories
                ]

            if Confirm.ask("  移除赞助片段?", default=False):
                categories = Prompt.ask("  分类(逗号分隔)", default="sponsor").split(
                    ","
                )
                self.config.postprocess.sponsorblock_remove = [
                    c.strip() for c in categories
                ]

        # FFmpeg 位置
        if Confirm.ask("\n自定义 FFmpeg 路径?", default=False):
            self.config.postprocess.ffmpeg_location = Prompt.ask("  FFmpeg 路径或目录")

    def configure_filesystem(self) -> None:
        """配置文件系统选项"""
        self.console.print("\n[bold cyan]═══ 步骤 8/8: 文件系统选项 ═══[/]")

        # 输出路径
        default_path = Path.cwd() / "downloads"
        path_str = Prompt.ask("输出目录", default=str(default_path))
        self.config.filesystem.output_path = Path(path_str)

        # 输出模板
        if Confirm.ask("自定义文件命名模板?", default=False):
            self.console.print("\n  可用字段示例:")
            self.console.print("    %(title)s - 标题")
            self.console.print("    %(id)s - 视频ID")
            self.console.print("    %(uploader)s - 上传者")
            self.console.print("    %(upload_date)s - 上传日期")
            self.console.print("    %(ext)s - 扩展名")
            self.console.print("\n  示例: %(uploader)s/%(title)s [%(id)s].%(ext)s")

            template = Prompt.ask("  文件命名模板", default=DEFAULT_OUTPUT_TEMPLATE)
            self.config.filesystem.output_template = template

        # 文件名选项
        if Confirm.ask("限制文件名（仅ASCII）?", default=False):
            self.config.filesystem.restrict_filenames = True

        if Confirm.ask("强制Windows兼容文件名?", default=False):
            self.config.filesystem.windows_filenames = True

        # 覆盖行为
        overwrite_options = ["跳过已存在", "强制覆盖", "继续未完成"]
        self.console.print(f"\n  文件已存在时: {', '.join(overwrite_options)}")
        ow_choice = Prompt.ask("  选择行为", choices=["1", "2", "3"], default="1")

        if ow_choice == "1":
            self.config.filesystem.no_overwrites = True
        elif ow_choice == "2":
            self.config.filesystem.force_overwrites = True
        else:
            self.config.filesystem.continue_dl = True

        # 下载归档
        if Confirm.ask("\n使用下载归档（避免重复）?", default=False):
            archive_path = Prompt.ask("  归档文件路径", default="download_archive.txt")
            self.config.playlist.download_archive = archive_path

    def save_preset_prompt(self) -> None:
        """询问是否保存预设"""
        if Confirm.ask("\n是否保存此配置为预设?", default=False):
            preset_name = Prompt.ask("预设名称")
            self.config.save_preset(preset_name)
            self.console.print(f"✓ 预设已保存: {preset_name}", style="green")

    def run(self) -> Optional[DownloadConfig]:
        """运行交互流程"""
        self.show_banner()

        # 尝试加载预设
        # 逻辑：如果有预设，直接加载；如果没有或用户选择不加载，则进入完整配置流程
        used_preset = self.select_preset()

        if used_preset:
            # 即使使用了预设，通常也需要用户输入本次要下载的 URL
            # 因为 save_preset 方法中特意排除了 'urls' 字段
            if not self.config.urls:
                self.config.urls = self.input_urls()
        else:
            # === 如果不使用预设，走完整的 8 步配置流程 ===

            # 1. URL 输入
            self.config.urls = self.input_urls()
            if not self.config.urls:
                self.console.print("❌ 未提供有效 URL，程序退出。", style="red")
                return None

            # 2-8. 逐步配置
            self.configure_format()
            self.configure_network()
            self.configure_download()
            self.configure_subtitles()
            self.configure_auth()
            self.configure_postprocessing()
            self.configure_filesystem()

            # 配置完成后询问是否保存为新预设
            self.save_preset_prompt()

        # 如果此时没有 URL (比如预设加载后用户取消了 URL 输入)，则退出
        if not self.config.urls:
            self.console.print("❌ URL 列表为空，取消下载。", style="red")
            return None

        # 最终确认
        if self.confirm_start():
            return self.config
        else:
            self.console.print("⚠️  用户取消操作。", style="yellow")
            return None

    def confirm_start(self) -> bool:
        """最终确认开始下载"""
        self.console.print("\n" + "=" * 50)
        self.console.print(f"[bold cyan]准备就绪![/]")
        self.console.print(f"  • 待下载视频数: [bold green]{len(self.config.urls)}[/]")
        self.console.print(
            f"  • 输出目录: [bold yellow]{self.config.filesystem.output_path}[/]"
        )
        self.console.print(
            f"  • 格式策略: [bold magenta]{self.config.format.format}[/]"
        )

        return Confirm.ask("\n立即开始下载?", default=True)


# ==================== 主程序入口 ====================


def main():
    # 设置控制台
    console = Console()

    # 检查 FFmpeg 环境 (可选，但在专业工具中建议检查)
    # 这里简单通过 where/which 命令检查，不阻断程序，仅提示
    import shutil

    if not shutil.which("ffmpeg"):
        console.print(
            Panel(
                "[bold red]警告: 未检测到 FFmpeg![/]\n"
                "视频合并、格式转换和音频提取功能将无法使用。\n"
                "请确保 ffmpeg 已添加到系统环境变量 PATH 中。",
                title="环境检查",
                border_style="red",
            )
        )
        if not Confirm.ask("是否继续?", default=False):
            sys.exit(1)

    try:
        # 初始化 UI 并获取配置
        ui = InteractiveUI(console)
        config = ui.run()

        if config:
            # 初始化下载器并执行
            downloader = UniversalVideoDownloader(config, console)
            success = downloader.download()

            if not success:
                sys.exit(1)

    except KeyboardInterrupt:
        console.print("\n\n⚠️  程序被用户强制中断", style="bold red")
        sys.exit(130)
    except Exception as e:
        console.print_exception()
        sys.exit(1)


if __name__ == "__main__":
    main()
