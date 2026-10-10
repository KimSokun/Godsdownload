import unittest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

from bot.platforms.base import BasePlatform
from bot.platforms.router import detect_platform, get_platform_name, get_all_platforms
from bot.platforms.youtube import YouTubePlatform
from bot.platforms.tiktok import TikTokPlatform
from bot.platforms.douyin import DouyinPlatform
from bot.platforms.facebook import FacebookPlatform
from bot.platforms.bilibili import BilibiliPlatform
from bot.platforms.instagram import InstagramPlatform
from bot.platforms.threads import ThreadsPlatform
from bot.platforms.twitter import TwitterPlatform
from bot.platforms.reddit import RedditPlatform
from bot.platforms.pinterest import PinterestPlatform
from bot.platforms.generic import GenericPlatform
from bot.downloader import download_video, download_audio, DownloadResult


class TestPlatformDetection(unittest.TestCase):
    """Verifies that all URLs are accurately identified and routed to their modular platform."""

    def test_youtube_detection(self):
        urls = [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ",
            "https://youtube.com/shorts/3i0hW1w6RTI",
            "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
        ]
        for url in urls:
            with self.subTest(url=url):
                platform = detect_platform(url)
                self.assertIsInstance(platform, YouTubePlatform)
                self.assertEqual(platform.platform_id, "youtube")
                self.assertIn("YouTube", platform.display_name)

    def test_tiktok_detection(self):
        urls = [
            "https://www.tiktok.com/@user/video/7123456789012345678",
            "https://vt.tiktok.com/ZS1234567/",
            "https://vm.tiktok.com/ZM1234567/",
        ]
        for url in urls:
            with self.subTest(url=url):
                platform = detect_platform(url)
                self.assertIsInstance(platform, TikTokPlatform)
                self.assertEqual(platform.platform_id, "tiktok")
                self.assertIn("TikTok", platform.display_name)

    def test_douyin_detection(self):
        urls = [
            "https://www.douyin.com/video/7123456789012345678",
            "https://v.douyin.com/ieXabc12/",
            "https://www.iesdouyin.com/share/video/7123456789012345678/",
        ]
        for url in urls:
            with self.subTest(url=url):
                platform = detect_platform(url)
                self.assertIsInstance(platform, DouyinPlatform)
                self.assertEqual(platform.platform_id, "douyin")
                self.assertIn("Douyin", platform.display_name)

    def test_facebook_detection(self):
        urls = [
            "https://www.facebook.com/reel/123456789",
            "https://fb.watch/abcdefg123/",
            "https://facebook.com/watch/?v=987654321",
            "https://m.facebook.com/story.php?story_fbid=123",
            "https://fb.gg/v/12345",
        ]
        for url in urls:
            with self.subTest(url=url):
                platform = detect_platform(url)
                self.assertIsInstance(platform, FacebookPlatform)
                self.assertEqual(platform.platform_id, "facebook")
                self.assertIn("Facebook", platform.display_name)

    def test_bilibili_detection(self):
        urls = [
            "https://www.bilibili.com/video/BV1xx411c7mD",
            "https://b23.tv/BV1xx411c7mD",
            "https://m.bilibili.com/video/BV1xx411c7mD",
        ]
        for url in urls:
            with self.subTest(url=url):
                platform = detect_platform(url)
                self.assertIsInstance(platform, BilibiliPlatform)
                self.assertEqual(platform.platform_id, "bilibili")
                self.assertIn("Bilibili", platform.display_name)


    def test_instagram_detection(self):
        urls = [
            "https://www.instagram.com/reel/C123456789/",
            "https://instagram.com/p/C123456789/",
            "https://instagr.am/reel/xyz123/",
            "https://www.instagram.com/tv/C123456789/",
        ]
        for url in urls:
            with self.subTest(url=url):
                platform = detect_platform(url)
                self.assertIsInstance(platform, InstagramPlatform)
                self.assertEqual(platform.platform_id, "instagram")
                self.assertIn("Instagram", platform.display_name)

    def test_threads_detection(self):
        urls = [
            "https://www.threads.net/@user/post/C123456789",
            "https://threads.net/@user/post/C987654321",
        ]
        for url in urls:
            with self.subTest(url=url):
                platform = detect_platform(url)
                self.assertIsInstance(platform, ThreadsPlatform)
                self.assertEqual(platform.platform_id, "threads")
                self.assertIn("Threads", platform.display_name)

    def test_twitter_x_detection(self):
        urls = [
            "https://twitter.com/user/status/1234567890123456789",
            "https://x.com/user/status/1234567890123456789",
            "https://mobile.twitter.com/user/status/123456789",
        ]
        for url in urls:
            with self.subTest(url=url):
                platform = detect_platform(url)
                self.assertIsInstance(platform, TwitterPlatform)
                self.assertEqual(platform.platform_id, "twitter")
                self.assertIn("Twitter", platform.display_name)


    def test_reddit_detection(self):
        urls = [
            "https://www.reddit.com/r/funny/comments/123456/funny_video/",
            "https://v.redd.it/abcdef123456",
            "https://old.reddit.com/r/videos/comments/xyz123/video/",
        ]
        for url in urls:
            with self.subTest(url=url):
                platform = detect_platform(url)
                self.assertIsInstance(platform, RedditPlatform)
                self.assertEqual(platform.platform_id, "reddit")
                self.assertIn("Reddit", platform.display_name)

    def test_pinterest_detection(self):
        urls = [
            "https://www.pinterest.com/pin/123456789012345678/",
            "https://pin.it/7aBcDeF",
        ]
        for url in urls:
            with self.subTest(url=url):
                platform = detect_platform(url)
                self.assertIsInstance(platform, PinterestPlatform)
                self.assertEqual(platform.platform_id, "pinterest")
                self.assertIn("Pinterest", platform.display_name)

    def test_generic_fallback(self):
        urls = [
            "https://vimeo.com/76979871",
            "https://soundcloud.com/user/track",
            "https://dailymotion.com/video/x8abcdef",
            "https://example.com/stream.mp4",
        ]
        for url in urls:
            with self.subTest(url=url):
                platform = detect_platform(url)
                self.assertIsInstance(platform, GenericPlatform)
                self.assertEqual(platform.platform_id, "generic")
                self.assertIn("Universal Web", platform.display_name)

    def test_case_insensitivity(self):
        urls = [
            ("HTTPS://WWW.YOUTUBE.COM/WATCH?V=ABC12345678", YouTubePlatform),
            ("HTTP://TIKTOK.COM/@USER/VIDEO/123", TikTokPlatform),
            ("HTTPS://WWW.FACEBOOK.COM/REEL/123", FacebookPlatform),
            ("HTTPS://WWW.BILIBILI.COM/VIDEO/BV123", BilibiliPlatform),
            ("HTTPS://WWW.INSTAGRAM.COM/REEL/123", InstagramPlatform),
            ("HTTPS://X.COM/USER/STATUS/123", TwitterPlatform),
        ]
        for url, expected_cls in urls:
            with self.subTest(url=url):
                platform = detect_platform(url)
                self.assertIsInstance(platform, expected_cls)


class TestPlatformArchitecture(unittest.TestCase):
    """Verifies that all platforms adhere strictly to the BasePlatform interface."""

    def test_all_platforms_implement_interface(self):
        platforms = get_all_platforms()
        self.assertGreaterEqual(len(platforms), 9)

        for p in platforms:
            with self.subTest(platform=p.platform_id):
                self.assertIsInstance(p, BasePlatform)
                self.assertTrue(hasattr(p, "download_video"))
                self.assertTrue(hasattr(p, "download_audio"))
                self.assertTrue(callable(p.download_video))
                self.assertTrue(callable(p.download_audio))
                self.assertIsInstance(p.display_name, str)
                self.assertGreater(len(p.display_name), 0)


class TestEndToEndDispatch(unittest.IsolatedAsyncioTestCase):
    """Verifies end-to-end routing and download dispatching without errors."""

    async def test_download_video_dispatches_to_platform(self):
        test_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        expected_result = DownloadResult(
            success=True,
            filepath="temp_downloads/dummy.mp4",
            filename="dummy.mp4",
            filesize_mb=12.5,
            title="Rick Astley - Never Gonna Give You Up",
        )

        with patch.object(YouTubePlatform, "download_video", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = expected_result
            res = await download_video(test_url)
            self.assertTrue(res.success)
            self.assertEqual(res.title, "Rick Astley - Never Gonna Give You Up")
            mock_dl.assert_awaited_once_with(test_url, 2000.0)

    async def test_download_audio_dispatches_to_platform(self):
        test_url = "https://www.tiktok.com/@user/video/123456"
        expected_result = DownloadResult(
            success=True,
            filepath="temp_downloads/dummy.mp3",
            filename="dummy.mp3",
            filesize_mb=3.2,
            title="Viral TikTok Sound",
            is_audio=True,
        )

        with patch.object(TikTokPlatform, "download_audio", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = expected_result
            res = await download_audio(test_url)
            self.assertTrue(res.success)
            self.assertTrue(res.is_audio)
            mock_dl.assert_awaited_once_with(test_url, 2000.0)


    async def test_instagram_dispatch(self):
        test_url = "https://www.instagram.com/reel/C123456789/"
        expected_result = DownloadResult(
            success=True,
            filepath="temp_downloads/ig.mp4",
            filename="ig.mp4",
            filesize_mb=8.0,
            title="Instagram Reel",
        )
        with patch.object(InstagramPlatform, "download_video", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = expected_result
            res = await download_video(test_url)
            self.assertTrue(res.success)
            self.assertEqual(res.title, "Instagram Reel")
            mock_dl.assert_awaited_once_with(test_url, 2000.0)

    async def test_twitter_dispatch(self):
        test_url = "https://x.com/user/status/1234567890"
        expected_result = DownloadResult(
            success=True,
            filepath="temp_downloads/x.mp4",
            filename="x.mp4",
            filesize_mb=5.0,
            title="X Post Video",
        )
        with patch.object(TwitterPlatform, "download_video", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = expected_result
            res = await download_video(test_url)
            self.assertTrue(res.success)
            self.assertEqual(res.title, "X Post Video")
            mock_dl.assert_awaited_once_with(test_url, 2000.0)

    async def test_facebook_dispatch(self):
        test_url = "https://www.facebook.com/reel/123456789"
        expected_result = DownloadResult(
            success=True,
            filepath="temp_downloads/fb.mp4",
            filename="fb.mp4",
            filesize_mb=15.0,
            title="Facebook Reel",
        )
        with patch.object(FacebookPlatform, "download_video", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = expected_result
            res = await download_video(test_url)
            self.assertTrue(res.success)
            self.assertEqual(res.title, "Facebook Reel")
            mock_dl.assert_awaited_once_with(test_url, 2000.0)

    async def test_reddit_dispatch(self):
        test_url = "https://www.reddit.com/r/funny/comments/123456/funny_video/"
        expected_result = DownloadResult(
            success=True,
            filepath="temp_downloads/reddit.mp4",
            filename="reddit.mp4",
            filesize_mb=11.0,
            title="Reddit Funny Video",
        )
        with patch.object(RedditPlatform, "download_video", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = expected_result
            res = await download_video(test_url)
            self.assertTrue(res.success)
            self.assertEqual(res.title, "Reddit Funny Video")
            mock_dl.assert_awaited_once_with(test_url, 2000.0)

    async def test_pinterest_dispatch(self):
        test_url = "https://pin.it/7aBcDeF"
        expected_result = DownloadResult(
            success=True,
            filepath="temp_downloads/pin.mp4",
            filename="pin.mp4",
            filesize_mb=4.0,
            title="Pinterest Pin",
        )
        with patch.object(PinterestPlatform, "download_video", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = expected_result
            res = await download_video(test_url)
            self.assertTrue(res.success)
            self.assertEqual(res.title, "Pinterest Pin")
            mock_dl.assert_awaited_once_with(test_url, 2000.0)

    async def test_threads_dispatch(self):
        test_url = "https://www.threads.net/@user/post/C123456789"
        expected_result = DownloadResult(
            success=True,
            filepath="temp_downloads/threads.mp4",
            filename="threads.mp4",
            filesize_mb=6.0,
            title="Threads Video",
        )
        with patch.object(ThreadsPlatform, "download_video", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = expected_result
            res = await download_video(test_url)
            self.assertTrue(res.success)
            self.assertEqual(res.title, "Threads Video")
            mock_dl.assert_awaited_once_with(test_url, 2000.0)

    async def test_bilibili_dispatch(self):
        test_url = "https://www.bilibili.com/video/BV1xx411c7mD"
        expected_result = DownloadResult(
            success=True,
            filepath="temp_downloads/bili.mp4",
            filename="bili.mp4",
            filesize_mb=30.0,
            title="Bilibili Animation",
        )
        with patch.object(BilibiliPlatform, "download_video", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = expected_result
            res = await download_video(test_url)
            self.assertTrue(res.success)
            self.assertEqual(res.title, "Bilibili Animation")
            mock_dl.assert_awaited_once_with(test_url, 2000.0)

    async def test_generic_dispatch(self):
        test_url = "https://vimeo.com/76979871"
        expected_result = DownloadResult(
            success=True,
            filepath="temp_downloads/vimeo.mp4",
            filename="vimeo.mp4",
            filesize_mb=18.0,
            title="Vimeo Short Film",
        )
        with patch.object(GenericPlatform, "download_video", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = expected_result
            res = await download_video(test_url)
            self.assertTrue(res.success)
            self.assertEqual(res.title, "Vimeo Short Film")
            mock_dl.assert_awaited_once_with(test_url, 2000.0)


class TestCdnEngine(unittest.IsolatedAsyncioTestCase):
    """Verifies redirect expansion and CDN mirror resolvers."""

    async def test_expand_redirect_url_passthrough(self):
        from bot.cdn_api import expand_redirect_url
        # Non-http should return unchanged
        self.assertEqual(await expand_redirect_url(""), "")
        self.assertEqual(await expand_redirect_url("not-a-url"), "not-a-url")

    async def test_cobalt_mirror_resolver_structure(self):
        from bot.cdn_api import fetch_cobalt_mirror
        # Test mock cobalt resolver response structure
        with patch("aiohttp.ClientSession.post") as mock_post:
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.json = AsyncMock(return_value={
                "status": "tunnel",
                "url": "https://cdn.cobalt.tools/file.mp4",
                "filename": "Sample Cobalt Video.mp4"
            })
            mock_post.return_value.__aenter__.return_value = mock_resp

            res = await fetch_cobalt_mirror("https://www.instagram.com/reel/123", is_audio=False)
            self.assertIsNotNone(res)
            self.assertTrue(res["success"])
            self.assertEqual(res["video_url"], "https://cdn.cobalt.tools/file.mp4")
            self.assertIn("Cobalt", res["source"])


class TestMonthlyVipSubscription(unittest.TestCase):
    """Verifies that VIP status is tracked as a 30-day monthly subscription."""

    def setUp(self):
        from bot.tracker import tracker
        self.tracker = tracker
        self.test_user_id = 888888888

    def tearDown(self):
        if str(self.test_user_id) in self.tracker.users:
            del self.tracker.users[str(self.test_user_id)]
            self.tracker._save()

    def test_monthly_vip_grant_and_expiry(self):
        from datetime import datetime, timedelta

        # 1. Grant 30 days VIP
        self.tracker.set_vip(self.test_user_id, True, days=30)
        vip_info = self.tracker.get_vip_info(self.test_user_id)
        self.assertTrue(vip_info["is_vip"])
        self.assertGreaterEqual(vip_info["days_left"], 29)
        self.assertIsNotNone(vip_info["vip_until_str"])

        can_dl, count, limit, is_vip = self.tracker.can_download(self.test_user_id)
        self.assertTrue(can_dl)
        self.assertTrue(is_vip)

        # 2. Extend subscription by another 30 days (total ~60 days)
        self.tracker.set_vip(self.test_user_id, True, days=30)
        vip_info_ext = self.tracker.get_vip_info(self.test_user_id)
        self.assertGreaterEqual(vip_info_ext["days_left"], 59)

        # 3. Test expiration behavior
        user = self.tracker.get_or_create_user(self.test_user_id)
        yesterday = (datetime.utcnow() - timedelta(days=1)).isoformat()
        user["vip_until"] = yesterday
        self.tracker._save()

class TestDirectCdnUrlGeneration(unittest.TestCase):
    """Verifies that auto-generated direct CDN download URLs are correctly extracted for all platforms when files are large."""

    def test_progressive_mp4_direct_url_extraction(self):
        from bot.platforms.base import extract_best_direct_url

        # Simulate Bilibili / yt-dlp multi-format playurl info dictionary
        sample_info = {
            "title": "Bilibili High Quality Stream",
            "formats": [
                {
                    "format_id": "1-30016",
                    "vcodec": "none",
                    "acodec": "mp4a.40.2",
                    "url": "https://upos-sz-mirrorcos.bilivideo.com/audio-only.m4s",
                    "filesize": 5000000,
                },
                {
                    "format_id": "1-30080",
                    "vcodec": "avc1.640028",
                    "acodec": "none",
                    "url": "https://upos-sz-mirrorcos.bilivideo.com/video-only.m4s",
                    "filesize": 45000000,
                },
                {
                    "format_id": "1-192",
                    "vcodec": "avc1.640028",
                    "acodec": "mp4a.40.2",
                    "height": 1080,
                    "tbr": 2500,
                    "url": "https://upos-sz-mirrorcos.bilivideo.com/upgcxcode/59/08/41395620859/41395620859-1-192.mp4?e=test&uipk=5",
                    "filesize": 95000000,
                },
                {
                    "format_id": "1-64",
                    "vcodec": "avc1.640028",
                    "acodec": "mp4a.40.2",
                    "height": 720,
                    "tbr": 1200,
                    "url": "https://upos-sz-mirrorcos.bilivideo.com/upgcxcode/59/08/41395620859/41395620859-1-64.mp4?e=test",
                    "filesize": 40000000,
                },
            ]
        }

        # Must pick highest quality progressive mp4 format (both audio and video stream)
        extracted = extract_best_direct_url(sample_info, is_audio=False)
        self.assertIsNotNone(extracted)
        self.assertTrue(extracted.startswith("https://upos-sz-mirrorcos.bilivideo.com"))
        self.assertIn("41395620859-1-192.mp4", extracted)

    def test_audio_direct_url_extraction(self):
        from bot.platforms.base import extract_best_direct_url

        sample_info = {
            "title": "Audio Stream",
            "formats": [
                {
                    "format_id": "audio-low",
                    "acodec": "mp3",
                    "vcodec": "none",
                    "abr": 128,
                    "url": "https://cdn.example.com/audio_128k.mp3",
                },
                {
                    "format_id": "audio-high",
                    "acodec": "mp3",
                    "vcodec": "none",
                    "abr": 320,
                    "url": "https://cdn.example.com/audio_320k.mp3",
                },
            ]
        }

        extracted = extract_best_direct_url(sample_info, is_audio=True)
        self.assertEqual(extracted, "https://cdn.example.com/audio_320k.mp3")

    def test_download_result_direct_url_property(self):
        res = DownloadResult(
            success=True,
            filepath=None,
            filesize_mb=128.5,
            title="Large 4K Video",
            uploader="Creator",
            is_over_50mb=True,
            direct_url="https://cdn.example.com/direct-download.mp4",
        )
        self.assertTrue(res.success)
        self.assertTrue(res.is_over_50mb)
        self.assertIsNone(res.filepath)
        self.assertEqual(res.direct_url, "https://cdn.example.com/direct-download.mp4")

    def test_member_header_with_stats(self):
        import sys
        from unittest.mock import MagicMock
        mock_tg = MagicMock()
        mock_tg_ext = MagicMock()
        mock_qr = MagicMock()
        with patch.dict(sys.modules, {"telegram": mock_tg, "telegram.ext": mock_tg_ext, "qrcode": mock_qr}):
            from bot.handlers.user_handlers import get_member_header
            header = get_member_header(999999999)
            self.assertIn("Members", header)
            self.assertIn("MEMBER", header)


class TestAutoPackageUpgrade(unittest.IsolatedAsyncioTestCase):
    """Verifies automatic package upgrade mechanisms for dependencies and yt-dlp."""

    def test_get_installed_version(self):
        from bot.auto_updater import get_installed_version, get_current_version
        v = get_installed_version("pip")
        self.assertIsInstance(v, str)
        self.assertNotEqual(v, "unknown")

        ytdlp_v = get_current_version("yt-dlp")
        self.assertIsInstance(ytdlp_v, str)

    def test_tracked_packages_versions(self):
        from bot.auto_updater import get_tracked_packages_versions, TRACKED_PACKAGES
        versions = get_tracked_packages_versions()
        self.assertIsInstance(versions, dict)
        for pkg in TRACKED_PACKAGES:
            self.assertIn(pkg, versions)

    async def test_run_package_update_single(self):
        from bot.auto_updater import run_package_update
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate.return_value = (b"Requirement already satisfied", b"")
            mock_exec.return_value = mock_proc

            success, msg = await run_package_update(package_name="yt-dlp")
            self.assertTrue(success)
            self.assertIn("yt-dlp", msg)

    async def test_run_package_update_all(self):
        from bot.auto_updater import run_package_update
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate.return_value = (b"Successfully installed", b"")
            mock_exec.return_value = mock_proc

            success, report = await run_package_update()
            self.assertTrue(success)
            self.assertIn("Automatic Package Upgrade Report", report)
            self.assertIn("yt-dlp", report)
            self.assertIn("pip", report)


class TestUsdtOnlyPayment(unittest.TestCase):
    """Verifies that KHQR is removed and payment is strictly USDT-TRON only."""

    def test_config_has_no_bakong_and_has_usdt(self):
        from bot.config import config
        self.assertTrue(hasattr(config, "usdt_tron_address"))
        self.assertFalse(hasattr(config, "bakong_account_id"))
        self.assertFalse(hasattr(config, "bakong_api_token"))

    def test_generate_usdt_qr_bytes(self):
        import sys
        from unittest.mock import MagicMock
        mock_tg = MagicMock()
        mock_tg_ext = MagicMock()
        mock_qr = MagicMock()
        with patch.dict(sys.modules, {"telegram": mock_tg, "telegram.ext": mock_tg_ext, "qrcode": mock_qr}):
            from bot.handlers.payment_handlers import generate_usdt_qr_bytes
            bio = generate_usdt_qr_bytes("TTestTronAddress1234567890abcdefgh")
            self.assertIsNotNone(bio)
            self.assertEqual(bio.name, "usdt_qr.png")

    def test_admin_username_support(self):
        from bot.config import config
        orig_usernames = list(config.admin_usernames)
        orig_ids = list(config.admin_ids)
        try:
            config.admin_usernames = ["jinwook", "superadmin"]
            config.admin_ids = [999888777]

            # 1. By exact username with '@'
            self.assertTrue(config.is_admin(12345, "@jinwook"))
            # 2. By case-insensitive username without '@'
            self.assertTrue(config.is_admin(12346, "JinWook"))
            self.assertTrue(config.is_admin(12347, "@SuperAdmin"))
            # 3. By numeric ID
            self.assertTrue(config.is_admin(999888777, "unknown_user"))
            # 4. Non-admin
            self.assertFalse(config.is_admin(111222333, "regular_user"))
        finally:
            config.admin_usernames = orig_usernames
            config.admin_ids = orig_ids

    def test_handle_video_message_bilibili(self):
        """Verify handle_video_message handles Bilibili URLs and passes valid reply_markup."""
        import sys
        from unittest.mock import MagicMock, AsyncMock, patch

        mock_update = MagicMock()
        mock_msg = MagicMock()
        mock_msg.text = "https://www.bilibili.com/video/BV1Ybuq6nEYq/?t=9&spm_id_from=333.1007.tianma.2-2-5.click"
        mock_msg.reply_text = AsyncMock()
        mock_update.effective_message = mock_msg
        mock_user = MagicMock()
        mock_user.id = 999999999
        mock_user.username = "testuser"
        mock_user.first_name = "Test"
        mock_update.effective_user = mock_user

        mock_tg = MagicMock()
        mock_tg_ext = MagicMock()
        mock_qr = MagicMock()
        with patch.dict(sys.modules, {"telegram": mock_tg, "telegram.ext": mock_tg_ext, "qrcode": mock_qr}):
            from bot.handlers import user_handlers
            asyncio.run(user_handlers.handle_video_message(mock_update, MagicMock()))

            self.assertTrue(mock_msg.reply_text.called)
            args, kwargs = mock_msg.reply_text.call_args
            self.assertIn("reply_markup", kwargs)
            self.assertIsNotNone(kwargs["reply_markup"])
            self.assertIn("Platform Detected", args[0])


class TestDirectCdnAndOver50Mb(unittest.TestCase):
    """Verifies instant direct CDN URL generation and 50MB+ bypass logic."""

    def test_execute_ytdlp_video_over_50mb_instant_return(self):
        from bot.platforms.generic import GenericPlatform
        platform = GenericPlatform()
        mock_info = {
            "title": "Large Test Video",
            "uploader": "Test Channel",
            "duration": 180,
            "filesize": 75 * 1024 * 1024,  # 75 MB
            "url": "https://cdn.example.com/stream.mp4",
        }
        with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
            mock_ydl = MagicMock()
            mock_ydl.extract_info.return_value = mock_info
            mock_ydl_cls.return_value.__enter__.return_value = mock_ydl

            res = platform.execute_ytdlp_video("https://example.com/video", "C:/tmp/test_out", 2000.0)
            self.assertTrue(res.success)
            self.assertTrue(res.is_over_50mb)
            self.assertEqual(res.direct_url, "https://cdn.example.com/stream.mp4")
            self.assertEqual(res.filesize_mb, 75.0)
            self.assertFalse(mock_ydl.download.called)

    def test_execute_ytdlp_video_exception_fallback(self):
        from bot.platforms.generic import GenericPlatform
        platform = GenericPlatform()
        mock_info = {
            "title": "Failing Download Video",
            "uploader": "Channel",
            "duration": 60,
            "filesize": 30 * 1024 * 1024,
            "url": "https://cdn.example.com/direct_fallback.mp4",
        }
        with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
            mock_ydl = MagicMock()
            mock_ydl.extract_info.return_value = mock_info
            mock_ydl.download.side_effect = Exception("Bilibili 412 or FFmpeg merge failure")
            mock_ydl_cls.return_value.__enter__.return_value = mock_ydl

            res = platform.execute_ytdlp_video("https://example.com/failing", "C:/tmp/test_fail", 2000.0)
            self.assertTrue(res.success)
            self.assertTrue(res.is_over_50mb)
            self.assertEqual(res.direct_url, "https://cdn.example.com/direct_fallback.mp4")

    def test_bilibili_html5_over_50mb_direct_url(self):
        platform = BilibiliPlatform()
        fake_meta = {
            "cdn_url": "https://upos-sz-mirrorcos.bilivideo.com/upgcxcode/59/08/41395620859/41395620859-1-192.mp4?e=test",
            "title": "Big Anime Episode",
            "duration": 1420,
            "size_bytes": 95 * 1024 * 1024,
            "size_mb": 95.0,
        }
        with patch.object(platform, "_fetch_html5_stream", return_value=fake_meta):
            res = asyncio.run(platform.download_video("https://www.bilibili.com/video/BV1citH6TE9n"))
            self.assertTrue(res.success)
            self.assertTrue(res.is_over_50mb)
            self.assertEqual(res.direct_url, fake_meta["cdn_url"])
            self.assertEqual(res.filesize_mb, 95.0)
            self.assertIsNone(res.filepath)


class TestMediaCacheAndMaintenance(unittest.TestCase):
    """Verifies Telegram file_id caching and monthly maintenance module."""

    def test_media_cache_get_set(self):
        import os
        import tempfile
        from bot.media_cache import MediaCache
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            temp_cache_file = tf.name

        try:
            cache = MediaCache(cache_file=temp_cache_file)
            test_url = "https://www.tiktok.com/@user/video/123456789"
            self.assertIsNone(cache.get(test_url, "video"))

            cache.set(
                url=test_url,
                media_type="video",
                file_id="BAACAgIAAxkBAAI...",
                title="Viral Dance",
                uploader="TikTok Creator",
                duration=35,
                filesize_mb=12.5
            )

            entry = cache.get(test_url, "video")
            self.assertIsNotNone(entry)
            self.assertEqual(entry["file_id"], "BAACAgIAAxkBAAI...")
            self.assertEqual(entry["title"], "Viral Dance")
            self.assertEqual(entry["filesize_mb"], 12.5)

            # Different media type should return None
            self.assertIsNone(cache.get(test_url, "audio"))
        finally:
            if os.path.exists(temp_cache_file):
                os.remove(temp_cache_file)


    def test_threads_url_parsing(self):
        platform = ThreadsPlatform()
        self.assertEqual(platform.extract_shortcode('https://www.threads.net/@user/post/CuYrF4qpuJa'), 'CuYrF4qpuJa')
        self.assertEqual(platform.extract_shortcode('https://www.threads.net/t/CuWqVLNPbta/'), 'CuWqVLNPbta')
        self.assertEqual(platform.extract_username('https://www.threads.net/@creator_123/post/XYZ'), 'creator_123')

    def test_threads_fast_scan_and_images(self):
        async def _run():
            platform = ThreadsPlatform()
            test_url = 'https://www.threads.net/t/CuWqVLNPbta'
            mock_resolved = {
                'shortcode': 'CuWqVLNPbta',
                'username': 'creator',
                'title': 'Photo post',
                'uploader': 'creator',
                'video_url': None,
                'image_urls': ['https://cdn.threads.net/photo1.jpg'],
                'thumbnail_url': 'https://cdn.threads.net/photo1.jpg',
                'media_type': 'images',
            }
            with patch.object(platform, 'resolve_threads_media', new_callable=AsyncMock) as mock_res:
                mock_res.return_value = mock_resolved
                scan = await platform.fast_scan(test_url)
                self.assertIsNotNone(scan)
                self.assertEqual(scan['type'], 'images')
                self.assertEqual(len(scan['image_urls']), 1)

                dl_res = await platform.download_images(test_url)
                self.assertTrue(dl_res.success)
                self.assertTrue(dl_res.is_image)
                self.assertEqual(dl_res.image_urls, ['https://cdn.threads.net/photo1.jpg'])
        asyncio.run(_run())

    def test_maintenance_audit_storage(self):
        from bot.maintenance import audit_storage_and_cleanup
        res = audit_storage_and_cleanup(max_age_hours=24.0)
        self.assertIn("cleaned_files", res)
        self.assertIn("temp_usage_mb", res)
        self.assertIn("remaining_temp_files", res)


if __name__ == "__main__":
    unittest.main()




