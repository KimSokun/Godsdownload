import sys, os, io, shutil, asyncio

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.downloader import download_video
from bot.platforms.router import detect_platform


test_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'test_downloads')
os.makedirs(test_dir, exist_ok=True)

test_targets = [
    ('Bilibili', 'https://www.bilibili.com/video/BV1citH6TE9n'),
    ('YouTube', 'https://www.youtube.com/watch?v=jNQXAC9IVRw'),
    ('Vimeo_Universal', 'https://vimeo.com/76979871'),
]


async def main():
    print('[*] Starting End-to-End Platform Download Verification...')
    print(f'[*] Target Directory: {test_dir}\n')
    results = []
    for platform_name, url in test_targets:
        print(f"--- [Testing {platform_name}] ---")
        print(f"URL: {url}")
        platform = detect_platform(url)
        print(f"Engine Platform: {platform.platform_id} ({platform.display_name})")
        try:
            res = await download_video(url, max_size_mb=200.0)
            if res.success and res.filepath and os.path.exists(res.filepath):
                size_mb = round(os.path.getsize(res.filepath) / (1024 * 1024), 2)
                dest = os.path.join(test_dir, f"{platform_name}.mp4")
                shutil.copy2(res.filepath, dest)
                print(f"SUCCESS! Downloaded and saved: {dest} ({size_mb} MB)")
                results.append({'platform': platform_name, 'status': 'PASS', 'size_mb': size_mb, 'filepath': dest})
            else:
                err = res.error or 'Unknown failure'
                print(f"FAILED: {err}")
                results.append({'platform': platform_name, 'status': 'FAIL', 'size_mb': 0.0, 'error': err})
        except Exception as e:
            print(f"EXCEPTION: {e}")
            results.append({'platform': platform_name, 'status': 'ERROR', 'size_mb': 0.0, 'error': str(e)})
        print()

    print("=================================================================")
    print("                    PLATFORM E2E TEST SUMMARY                    ")
    print("==================================================================")
    for r in results:
        print(f"{'PASS' if r['status'] == 'PASS' else 'FAIL':<6} {r['platform']:18} | Size: {r['size_mb']} MB | Path: {r.get('filepath', 'None')}")
    print("=================================================================")

if __name__ == '__main__':
    asyncio.run(main())
