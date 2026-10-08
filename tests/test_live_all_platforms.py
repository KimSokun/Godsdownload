import os
import sys
import io
import asyncio
import logging

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.downloader import download_video, download_images, scan_url, cleanup_files
from bot.platforms.router import detect_platform, get_all_platforms

logging.basicConfig(level=logging.WARNING)

# Test URLs for each platform
TEST_CASES = [
    {
        "platform_id": "youtube",
        "video_url": "https://www.youtube.com/watch?v=jNQXAC9IVRw",
        "image_url": None,
    },
    {
        "platform_id": "bilibili",
        "video_url": "https://www.bilibili.com/video/BV1citH6TE9n",
        "image_url": None,
    },
    {
        "platform_id": "generic",
        "video_url": "https://vimeo.com/76979871",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/b/b6/Image_created_with_a_mobile_phone.png/640px-Image_created_with_a_mobile_phone.png",
    },
    {
        "platform_id": "tiktok",
        "video_url": "https://www.tiktok.com/@tiktok/video/6805867805452668165",
        "image_url": "https://www.tiktok.com/@tiktok/video/6805867805452668165",
    },
    {
        "platform_id": "reddit",
        "video_url": "https://www.reddit.com/r/NatureIsFuckingLit/comments/1f8e123/majestic_eagle/",
        "image_url": "https://i.redd.it/snoo.png",
    },
    {
        "platform_id": "pinterest",
        "video_url": "https://www.pinterest.com/pin/123456789/",
        "image_url": "https://i.pinimg.com/originals/4f/9e/8a/4f9e8a7b6c5d4e3f2a1b0c9d8e7f6a5b.jpg",
    },
    {
        "platform_id": "twitter",
        "video_url": "https://x.com/Twitter/status/1750596078779679163",
        "image_url": "https://x.com/Twitter/status/1750596078779679163",
    },
    {
        "platform_id": "instagram",
        "video_url": "https://www.instagram.com/reel/C123456789/",
        "image_url": "https://www.instagram.com/p/C123456789/",
    },
    {
        "platform_id": "facebook",
        "video_url": "https://www.facebook.com/reel/123456789",
        "image_url": None,
    },
    {
        "platform_id": "threads",
        "video_url": "https://www.threads.net/@zuck/post/CuU59hPy9-a",
        "image_url": None,
    },
    {
        "platform_id": "xiaohongshu",
        "video_url": "https://www.xiaohongshu.com/explore/648000000000000000000001",
        "image_url": "https://www.xiaohongshu.com/explore/648000000000000000000001",
    },
    {
        "platform_id": "kuaishou",
        "video_url": "https://v.kuaishou.com/xyz123",
        "image_url": None,
    },
    {
        "platform_id": "wetv",
        "video_url": "https://wetv.vip/en/play/u3344abc/v0044def",
        "image_url": None,
    },
]


async def run_platform_probe():
    print("=" * 70)
    print("      LIVE PLATFORM DOWNLOAD VERIFICATION (VIDEO & IMAGE)      ")
    print("=" * 70)
    
    results = []
    
    for case in TEST_CASES:
        p_id = case["platform_id"]
        v_url = case["video_url"]
        i_url = case["image_url"]
        
        print(f"\n[Testing Platform: {p_id.upper()}]")
        p_engine = detect_platform(v_url) if v_url else None
        engine_name = p_engine.display_name if p_engine else "N/A"
        print(f"-> Detected Engine: {engine_name} ({p_engine.platform_id if p_engine else 'None'})")

        v_status = "SKIPPED"
        v_size = 0.0
        v_err = ""
        v_file = None

        if v_url:
            print(f"-> Testing Video Download ({v_url[:45]}...): ", end="", flush=True)
            try:
                # 15s timeout for probing
                res = await asyncio.wait_for(download_video(v_url, max_size_mb=100.0), timeout=25.0)
                if res and res.success and (res.filepath or res.direct_url or res.is_over_50mb):
                    v_status = "PASS"
                    v_size = res.filesize_mb or 0.0
                    v_file = res.filepath
                    print(f"SUCCESS ({v_size} MB, is_over_50mb={res.is_over_50mb})")
                else:
                    v_status = "FAIL"
                    v_err = (res.error if res else "No result") or "Failed"
                    print(f"FAIL: {v_err[:60]}")
            except asyncio.TimeoutError:
                v_status = "TIMEOUT"
                v_err = "Request timed out (>25s)"
                print(f"TIMEOUT")
            except Exception as e:
                v_status = "ERROR"
                v_err = str(e)
                print(f"ERROR: {e}")
            finally:
                if v_file:
                    cleanup_files(v_file)

        i_status = "N/A"
        i_count = 0
        i_err = ""
        if i_url:
            print(f"-> Testing Image Download ({i_url[:45]}...): ", end="", flush=True)
            try:
                res_img = await asyncio.wait_for(download_images(i_url), timeout=15.0)
                if res_img and res_img.success and res_img.image_urls:
                    i_status = "PASS"
                    i_count = len(res_img.image_urls)
                    print(f"SUCCESS ({i_count} images found)")
                else:
                    i_status = "FAIL"
                    i_err = (res_img.error if res_img else "No result") or "No images found"
                    print(f"FAIL: {i_err[:60]}")
            except asyncio.TimeoutError:
                i_status = "TIMEOUT"
                i_err = "Request timed out (>15s)"
                print(f"TIMEOUT")
            except Exception as e:
                i_status = "ERROR"
                i_err = str(e)
                print(f"ERROR: {e}")

        results.append({
            "platform": p_id,
            "engine": engine_name,
            "video_status": v_status,
            "video_size": v_size,
            "video_err": v_err,
            "image_status": i_status,
            "image_count": i_count,
            "image_err": i_err,
        })

    print("\n" + "=" * 70)
    print("                   PLATFORM AUDIT SUMMARY                    ")
    print("=" * 70)
    print(f"{'Platform':<14} | {'Video Download':<16} | {'Image Download':<16} | {'Verdict'}")
    print("-" * 70)
    
    for r in results:
        v_str = f"{r['video_status']} ({r['video_size']}MB)" if r['video_status'] == 'PASS' else r['video_status']
        i_str = f"{r['image_status']} ({r['image_count']} imgs)" if r['image_status'] == 'PASS' else r['image_status']
        
        can_download = (r['video_status'] == 'PASS') or (r['image_status'] == 'PASS')
        verdict = "KEEP" if can_download else "CHECK / PRUNE"
        print(f"{r['platform']:<14} | {v_str:<16} | {i_str:<16} | {verdict}")

    print("=" * 70)
    return results


if __name__ == "__main__":
    asyncio.run(run_platform_probe())
