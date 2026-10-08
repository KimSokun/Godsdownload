import os
import sys
import time
import json
import asyncio
import logging

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from datetime import datetime
from typing import Dict, Any, List, Tuple, Optional
import aiohttp

from bot.config import config
from bot.tracker import tracker
from bot.media_cache import media_cache
from bot.auto_updater import get_tracked_packages_versions

logger = logging.getLogger(__name__)

MAINTENANCE_REPORT_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "monthly_maintenance_report.json"
)

# Known upstream endpoints to probe
MIRROR_ENDPOINTS = [
    {"name": "Cobalt API Primary", "url": "https://api.cobalt.tools", "type": "post", "payload": {"url": "https://www.youtube.com/watch?v=jNQXAC9IVRw"}},
    {"name": "Cobalt API Mirror 2", "url": "https://cobalt.api.scot", "type": "post", "payload": {"url": "https://www.youtube.com/watch?v=jNQXAC9IVRw"}},
    {"name": "TikWM API", "url": "https://www.tikwm.com/api/", "type": "post", "payload": {"url": "https://www.tiktok.com"}},
    {"name": "Bilibili HTML5 API", "url": "https://api.bilibili.com/x/player/pagelist?bvid=BV1xx411c7mD", "type": "get"},
    {"name": "Invidious Mirror", "url": "https://inv.tux.pizza/api/v1/stats", "type": "get"},
]


async def probe_endpoint(session: aiohttp.ClientSession, endpoint: Dict[str, Any], timeout: float = 4.0) -> Dict[str, Any]:
    """Pings an upstream mirror and records latency and HTTP status."""
    url = endpoint["url"]
    name = endpoint["name"]
    req_type = endpoint.get("type", "get")
    headers = {"User-Agent": "Godsdownload/1.0 HealthMonitor"}

    start = time.time()
    try:
        if req_type == "post":
            async with session.post(url, json=endpoint.get("payload", {}), headers=headers, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                elapsed_ms = round((time.time() - start) * 1000, 1)
                status = "ONLINE" if resp.status in (200, 201, 400) else f"HTTP {resp.status}"
                return {"name": name, "url": url, "status": status, "latency_ms": elapsed_ms, "ok": resp.status < 500}
        else:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                elapsed_ms = round((time.time() - start) * 1000, 1)
                status = "ONLINE" if resp.status == 200 else f"HTTP {resp.status}"
                return {"name": name, "url": url, "status": status, "latency_ms": elapsed_ms, "ok": resp.status == 200}
    except asyncio.TimeoutError:
        return {"name": name, "url": url, "status": "TIMEOUT", "latency_ms": timeout * 1000, "ok": False}
    except Exception as e:
        return {"name": name, "url": url, "status": f"ERROR ({type(e).__name__})", "latency_ms": 0, "ok": False}


async def audit_mirrors() -> List[Dict[str, Any]]:
    """Audits all external streaming and download mirrors."""
    results = []
    async with aiohttp.ClientSession() as session:
        tasks = [probe_endpoint(session, ep) for ep in MIRROR_ENDPOINTS]
        results = await asyncio.gather(*tasks, return_exceptions=False)
    return results


async def check_pypi_latest(package_name: str, timeout: float = 3.0) -> Optional[str]:
    """Fetches the latest published version of a package on PyPI."""
    url = f"https://pypi.org/pypi/{package_name}/json"
    headers = {"User-Agent": "Godsdownload/1.0"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("info", {}).get("version")
    except Exception:
        pass
    return None


async def audit_dependencies() -> List[Dict[str, Any]]:
    """Checks installed package versions against PyPI latest releases."""
    installed = get_tracked_packages_versions()
    results = []
    for pkg, current_ver in installed.items():
        if pkg in ("pip",):
            results.append({"package": pkg, "current": current_ver, "latest": current_ver, "up_to_date": True})
            continue
        latest = await check_pypi_latest(pkg)
        up_to_date = (latest is None or current_ver == latest)
        results.append({
            "package": pkg,
            "current": current_ver,
            "latest": latest or "Unknown",
            "up_to_date": up_to_date,
        })
    return results


def audit_storage_and_cleanup(max_age_hours: float = 2.0) -> Dict[str, Any]:
    """
    Cleans up temporary downloads older than max_age_hours and returns disk usage stats.
    """
    temp_dir = config.temp_dir
    cleaned_files = 0
    cleaned_bytes = 0
    now = time.time()
    cutoff = now - (max_age_hours * 3600)

    if os.path.exists(temp_dir):
        for fname in os.listdir(temp_dir):
            fpath = os.path.join(temp_dir, fname)
            if os.path.isfile(fpath):
                try:
                    mtime = os.path.getmtime(fpath)
                    if mtime < cutoff:
                        fsize = os.path.getsize(fpath)
                        os.remove(fpath)
                        cleaned_files += 1
                        cleaned_bytes += fsize
                except Exception as e:
                    logger.debug(f"Could not remove temp file {fpath}: {e}")

    # Clean expired media cache
    cleaned_cache = media_cache.clean_expired()

    # Calculate remaining temp disk usage
    total_temp_size = 0
    remaining_temp_files = 0
    if os.path.exists(temp_dir):
        for fname in os.listdir(temp_dir):
            p = os.path.join(temp_dir, fname)
            if os.path.isfile(p):
                total_temp_size += os.path.getsize(p)
                remaining_temp_files += 1

    return {
        "cleaned_files": cleaned_files,
        "cleaned_mb": round(cleaned_bytes / (1024 * 1024), 2),
        "cleaned_cache_entries": cleaned_cache,
        "remaining_temp_files": remaining_temp_files,
        "temp_usage_mb": round(total_temp_size / (1024 * 1024), 2),
    }


async def run_monthly_maintenance() -> Tuple[Dict[str, Any], str]:
    """
    Executes full monthly maintenance:
    1. Upstream mirror latency & health check
    2. Dependency freshness audit against PyPI
    3. Storage cleanup & cache eviction
    4. Database & community report statistics
    Returns (report_data_dict, markdown_summary_text)
    """
    logger.info("🔧 Starting Godsdownload Monthly Maintenance & Health Audit...")
    run_timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    # 1. Audit Mirrors
    mirrors_data = await audit_mirrors()

    # 2. Audit Dependencies
    deps_data = await audit_dependencies()

    # 3. Storage & Temp Cleanup
    storage_data = audit_storage_and_cleanup(max_age_hours=2.0)

    # 4. Community Reports & User Stats
    stats = tracker.get_stats()
    cache_stats = media_cache.get_stats()
    reports = getattr(tracker, "reports", [])
    open_reports = len([r for r in reports if not r.get("resolved")])

    report_data = {
        "timestamp": run_timestamp,
        "mirrors": mirrors_data,
        "dependencies": deps_data,
        "storage": storage_data,
        "stats": stats,
        "cache": cache_stats,
        "open_reports": open_reports,
    }

    # Save to disk
    try:
        os.makedirs(os.path.dirname(MAINTENANCE_REPORT_FILE), exist_ok=True)
        with open(MAINTENANCE_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2, ensure_ascii=False)
        logger.info(f"Saved monthly maintenance report to {MAINTENANCE_REPORT_FILE}")
    except Exception as e:
        logger.error(f"Failed to write maintenance report file: {e}")

    # Build Markdown Summary Report
    lines = [
        "🛠️ **Godsdownload Monthly Maintenance & Health Report**",
        f"📅 **Date:** `{run_timestamp}`\n",
        "🌐 **Upstream Mirror Status:**"
    ]
    for m in mirrors_data:
        status_icon = "🟢" if m["ok"] else "🔴"
        lines.append(f"• {status_icon} **{m['name']}:** `{m['status']}` ({m['latency_ms']} ms)")

    lines.append("\n📦 **Dependency Up-to-Date Status:**")
    for d in deps_data:
        icon = "✅" if d["up_to_date"] else "⚠️"
        lines.append(f"• {icon} **{d['package']}:** `{d['current']}`" + (f" (Latest: `{d['latest']}`)" if not d["up_to_date"] else ""))

    lines.append("\n🧹 **Storage & Cache Maintenance:**")
    lines.append(f"• Cleaned Temp Files: `{storage_data['cleaned_files']}` files ({storage_data['cleaned_mb']} MB freed)")
    lines.append(f"• Cleaned Cache Entries: `{storage_data['cleaned_cache_entries']}` expired items")
    lines.append(f"• Active Cached Media: `{cache_stats['total_cached']}` file_ids stored")
    lines.append(f"• Current Temp Disk Usage: `{storage_data['temp_usage_mb']} MB` ({storage_data['remaining_temp_files']} files)")

    lines.append("\n👥 **Community & Member Overview:**")
    lines.append(f"• Total Members: `{stats.get('total_users', 0):,}`")
    lines.append(f"• Active VIP Members: `{stats.get('total_vips', 0):,}`")
    lines.append(f"• Total Processed Downloads: `{stats.get('total_downloads', 0):,}`")
    lines.append(f"• Pending Community Reports: `{open_reports}`")

    summary_text = "\n".join(lines)
    return report_data, summary_text


async def cli_main():
    """CLI Entry point for python -m bot.maintenance"""
    report, text = await run_monthly_maintenance()
    print("\n" + "=" * 60)
    print(text)
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(cli_main())
