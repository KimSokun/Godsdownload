import os
import sys
import asyncio
import logging
import importlib
import importlib.metadata
from typing import Tuple, Optional, Dict, List
import yt_dlp
from bot.config import config

logger = logging.getLogger(__name__)

TRACKED_PACKAGES = [
    "yt-dlp",
    "python-telegram-bot",
    "aiohttp",
    "requests",
    "pip",
]


def get_installed_version(package_name: str) -> str:
    """Gets the installed version of a package using importlib.metadata."""
    try:
        return importlib.metadata.version(package_name)
    except Exception:
        # Fallback for yt_dlp reload
        if package_name == "yt-dlp":
            try:
                importlib.reload(yt_dlp)
                return getattr(yt_dlp.version, "__version__", "unknown")
            except Exception:
                return getattr(yt_dlp, "__version__", "unknown")
        return "unknown"


def get_current_version(pkg: str = "yt-dlp") -> str:
    """Gets the currently installed version of a package (default: yt-dlp)."""
    return get_installed_version(pkg)


def get_tracked_packages_versions() -> Dict[str, str]:
    """Returns a dictionary of tracked packages and their current installed versions."""
    return {pkg: get_installed_version(pkg) for pkg in TRACKED_PACKAGES}


async def run_package_update(package_name: Optional[str] = None) -> Tuple[bool, str]:
    """
    Asynchronously upgrades python packages.
    If package_name is specified (e.g. 'yt-dlp'), upgrades only that package.
    If package_name is None or 'all', upgrades pip, yt-dlp, and all packages in requirements.txt.
    Returns (success: bool, markdown_report: str)
    """
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    req_file = os.path.join(root_dir, "requirements.txt")

    old_versions = get_tracked_packages_versions()

    try:
        if package_name and package_name.lower() != "all":
            # Specific package upgrade
            old_ver = get_installed_version(package_name)
            logger.info(f"Checking for update for {package_name} (current: {old_ver})...")
            cmd = [sys.executable, "-m", "pip", "install", "--upgrade", package_name]
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            out_str = stdout.decode().strip()
            err_str = stderr.decode().strip()

            if process.returncode != 0:
                err_msg = f"❌ Failed to upgrade `{package_name}`: {err_str or out_str}"
                logger.error(err_msg)
                return False, err_msg

            new_ver = get_installed_version(package_name)
            if "Requirement already satisfied" in out_str and old_ver == new_ver:
                msg = f"✅ `{package_name}` is already up to date (version: `{new_ver}`)."
            else:
                msg = f"🚀 Successfully upgraded `{package_name}`: `{old_ver}` ➜ `{new_ver}`!"
            logger.info(msg)
            return True, msg

        else:
            # Full package upgrade: pip, yt-dlp, requirements.txt
            logger.info("Starting automatic upgrade of all packages (pip, yt-dlp, requirements.txt)...")

            # 1. Upgrade pip
            cmd_pip = [sys.executable, "-m", "pip", "install", "--upgrade", "pip", "-q"]
            p1 = await asyncio.create_subprocess_exec(*cmd_pip, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            await p1.communicate()

            # 2. Upgrade yt-dlp specifically for latest platform extractors
            cmd_ytdlp = [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp", "-q"]
            p2 = await asyncio.create_subprocess_exec(*cmd_ytdlp, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            await p2.communicate()

            # 3. Upgrade requirements.txt if present
            if os.path.exists(req_file):
                cmd_req = [sys.executable, "-m", "pip", "install", "--upgrade", "-r", req_file, "-q"]
                p3 = await asyncio.create_subprocess_exec(*cmd_req, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                stdout3, stderr3 = await p3.communicate()
                if p3.returncode != 0:
                    logger.warning(f"requirements.txt upgrade notice: {stderr3.decode().strip()}")

            new_versions = get_tracked_packages_versions()

            report_lines = ["📦 **Automatic Package Upgrade Report**\n"]
            any_changed = False
            for pkg in TRACKED_PACKAGES:
                old_v = old_versions.get(pkg, "unknown")
                new_v = new_versions.get(pkg, "unknown")
                if old_v != new_v:
                    any_changed = True
                    report_lines.append(f"• **{pkg}:** `{old_v}` ➜ `{new_v}` 🚀")
                else:
                    report_lines.append(f"• **{pkg}:** `{new_v}` ✅ (Latest)")

            if any_changed:
                report_lines.append("\n🎉 **Packages updated successfully!**")
            else:
                report_lines.append("\n✅ **All packages are already up to date!**")

            summary = "\n".join(report_lines)
            logger.info("Automatic package upgrade check finished successfully.")
            return True, summary

    except Exception as e:
        err_msg = f"❌ Exception during package upgrade: {e}"
        logger.error(err_msg, exc_info=True)
        return False, err_msg


async def run_update() -> Tuple[bool, str]:
    """Backward-compatible alias for run_package_update('yt-dlp')."""
    return await run_package_update("yt-dlp")


async def start_auto_updater(interval_hours: Optional[int] = None):
    """
    Background worker that runs periodically to automatically upgrade all packages.
    Ensures smooth downloads from YouTube, TikTok, Instagram, Bilibili, etc.
    """
    hours = interval_hours if interval_hours is not None else config.auto_update_interval_hours
    if hours <= 0:
        logger.info("Auto-updater disabled (interval <= 0).")
        return

    logger.info(f"🔄 Automatic Package Upgrader enabled: checking every {hours} hours.")

    # Run once at startup after a short delay (15 seconds) so bot is initialized
    await asyncio.sleep(15)
    try:
        success, report = await run_package_update()
        logger.info(f"Initial Startup Package Upgrade Check:\n{report}")
    except Exception as e:
        logger.warning(f"Startup package upgrade check failed: {e}")

    while True:
        try:
            await asyncio.sleep(hours * 3600)
            success, report = await run_package_update()
            logger.info(f"Periodic Package Upgrade Check:\n{report}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in automatic package upgrader loop: {e}")
            await asyncio.sleep(300)


# Alias for backward compatibility
auto_updater_task = start_auto_updater
