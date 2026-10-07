import os
import sys
import asyncio
import logging
from typing import Dict, Tuple, Optional, Set

logger = logging.getLogger(__name__)

# Extensions and filenames to monitor
WATCHED_EXTENSIONS = {".py"}
WATCHED_FILENAMES = {".env", "requirements.txt", "supervisor.conf"}

# Directory names to completely ignore
IGNORED_DIRS = {
    "temp_downloads",
    "data",
    "logs",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".git",
    ".idea",
    ".vscode",
    "node_modules",
}

# File extensions to ignore
IGNORED_EXTENSIONS = {".lock", ".log", ".tmp", ".swp", ".bak", ".db", ".json"}


def get_project_files_snapshot(root_dir: str) -> Dict[str, Tuple[float, int]]:
    """
    Scans the project directory and returns a dictionary of:
    { file_path: (mtime, size_bytes) }
    Only monitors source code and essential config files.
    """
    snapshot: Dict[str, Tuple[float, int]] = {}
    try:
        for dirpath, dirnames, filenames in os.walk(root_dir):
            # Modify dirnames in-place to avoid descending into ignored directories
            dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS and not d.startswith(".")]

            for fname in filenames:
                ext = os.path.splitext(fname)[1].lower()
                if ext in IGNORED_EXTENSIONS:
                    continue

                if ext in WATCHED_EXTENSIONS or fname in WATCHED_FILENAMES:
                    full_path = os.path.join(dirpath, fname)
                    try:
                        stat = os.stat(full_path)
                        snapshot[full_path] = (stat.st_mtime, stat.st_size)
                    except (OSError, PermissionError):
                        continue
    except Exception as e:
        logger.debug(f"Error scanning directory {root_dir}: {e}")

    return snapshot


def detect_file_changes(
    old_snapshot: Dict[str, Tuple[float, int]],
    new_snapshot: Dict[str, Tuple[float, int]]
) -> Set[str]:
    """
    Compares two file snapshots and returns a set of changed or newly created file paths.
    """
    changed_files = set()

    for path, (new_mtime, new_size) in new_snapshot.items():
        if path not in old_snapshot:
            changed_files.add(path)
        else:
            old_mtime, old_size = old_snapshot[path]
            # Detect change if modification time or file size changed
            if new_mtime != old_mtime or new_size != old_size:
                changed_files.add(path)

    for path in old_snapshot:
        if path not in new_snapshot:
            changed_files.add(path)

    return changed_files


async def restart_bot(application=None, lock=None):
    """
    Gracefully stops the Telegram bot instance and restarts the Python process.
    Releases single instance locks to prevent 409 Conflict upon restart.
    """
    logger.info("🔄 Releasing resources and restarting Godsdownload Bot...")

    # 1. Stop Telegram application cleanly
    if application:
        try:
            if hasattr(application, "updater") and application.updater and application.updater.running:
                await application.updater.stop()
            if hasattr(application, "stop"):
                await application.stop()
            if hasattr(application, "shutdown"):
                await application.shutdown()
        except Exception as e:
            logger.warning(f"Notice during application shutdown: {e}")

    # 2. Release single instance lock
    if lock:
        try:
            lock.release()
        except Exception as e:
            logger.debug(f"Notice during lock release: {e}")

    # 3. Flush system buffers
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass

    # 4. Restart process
    try:
        # Replaces current process with a fresh Python process
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as e:
        logger.warning(f"os.execv failed ({e}), terminating so Supervisor can restart cleanly.")
        sys.exit(0)


async def start_code_watcher(
    root_dir: Optional[str] = None,
    application=None,
    lock=None,
    check_interval: float = 2.0
):
    """
    Asynchronous background watcher that monitors code files for changes.
    When a new version of code is uploaded, it debounces and restarts the bot automatically.
    """
    if not root_dir:
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    logger.info(f"👀 Auto-Restart Watcher active: monitoring '{root_dir}' for code uploads every {check_interval}s.")

    # Initialize baseline snapshot
    current_snapshot = get_project_files_snapshot(root_dir)

    while True:
        try:
            await asyncio.sleep(check_interval)
            new_snapshot = get_project_files_snapshot(root_dir)
            changed = detect_file_changes(current_snapshot, new_snapshot)

            if changed:
                sample_file = os.path.basename(list(changed)[0])
                logger.info(f"📁 Code change detected in {len(changed)} file(s) (e.g. '{sample_file}'). Debouncing 2s for upload completion...")

                # Debounce window: wait for all files in a multi-file upload/extraction to finish writing
                debounce_delay = 2.0
                max_debounce_checks = 5
                checks = 0

                while checks < max_debounce_checks:
                    await asyncio.sleep(debounce_delay)
                    latest_snapshot = get_project_files_snapshot(root_dir)
                    subsequent_changes = detect_file_changes(new_snapshot, latest_snapshot)
                    if not subsequent_changes:
                        # Files have settled!
                        break
                    new_snapshot = latest_snapshot
                    checks += 1

                logger.info("🚀 New code version upload complete! Restarting bot now...")
                await restart_bot(application=application, lock=lock)
                return

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in code watcher loop: {e}")
            await asyncio.sleep(check_interval)
