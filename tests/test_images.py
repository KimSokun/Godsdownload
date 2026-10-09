import os
import sys
import unittest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure mocks for telegram if needed
try:
    import telegram
    import telegram.ext
except ImportError:
    if "telegram" not in sys.modules:
        sys.modules["telegram"] = MagicMock()
    if "telegram.ext" not in sys.modules:
        sys.modules["telegram.ext"] = MagicMock()

from bot.downloader import scan_url, download_images, DownloadResult, cleanup_files
from bot.platforms.tiktok import TikTokPlatform
from bot.platforms.instagram import InstagramPlatform
from bot.platforms.reddit import RedditPlatform
from bot.platforms.pinterest import PinterestPlatform
from bot.platforms.twitter import TwitterPlatform
from bot.platforms.generic import GenericPlatform
from bot.handlers.user_handlers import send_image_gallery, handle_video_message
from bot.config import config


class TestMultiPlatformImageExtraction(unittest.TestCase):
    def setUp(self):
        self.created_test_files = []

    def tearDown(self):
        # Strict requirement: "when done delete test image"
        for f in self.created_test_files:
            cleanup_files(f)
        self.created_test_files.clear()

    def test_tiktok_10_image_slideshow_scan_and_download(self):
        """Test TikTok 10-image slideshow detection and extraction."""
        platform = TikTokPlatform()
        mock_tikwm_data = {
            "success": True,
            "title": "Beautiful Travel Slideshow",
            "uploader": "traveler_joe",
            "duration": 0,
            "video_url": None,
            "audio_url": "https://cdn.tikwm.com/music/bgm.mp3",
            "thumbnail_url": "https://cdn.tikwm.com/thumb.jpg",
            "image_urls": [f"https://p16-sign.tiktokcdn.com/image_{i}.jpeg" for i in range(1, 11)],
        }

        with patch.object(platform, "_fetch_tikwm", new=AsyncMock(return_value=mock_tikwm_data)):
            # 1. Fast Scan First
            scan = asyncio.run(platform.fast_scan("https://www.tiktok.com/@user/video/71234567890"))
            self.assertIsNotNone(scan)
            self.assertEqual(scan["type"], "images")
            self.assertEqual(len(scan["image_urls"]), 10)
            self.assertIn("image_1.jpeg", scan["image_urls"][0])
            self.assertIn("image_10.jpeg", scan["image_urls"][9])
            self.assertEqual(scan["audio_url"], "https://cdn.tikwm.com/music/bgm.mp3")

            # 2. Download Images
            res = asyncio.run(platform.download_images("https://www.tiktok.com/@user/video/71234567890"))
            self.assertTrue(res.success)
            self.assertTrue(res.is_image)
            self.assertEqual(len(res.image_urls), 10)

    def test_instagram_multi_image_carousel_scan(self):
        """Test Instagram carousel with Cobalt picker."""
        platform = InstagramPlatform()
        mock_cobalt = {
            "success": True,
            "title": "Instagram Carousel",
            "uploader": "photographer",
            "video_url": None,
            "audio_url": None,
            "image_urls": [
                "https://scontent.cdninstagram.com/photo1.jpg",
                "https://scontent.cdninstagram.com/photo2.jpg",
                "https://scontent.cdninstagram.com/photo3.jpg"
            ]
        }
        with patch("bot.platforms.instagram.fetch_cobalt_mirror", new=AsyncMock(return_value=mock_cobalt)):
            scan = asyncio.run(platform.fast_scan("https://www.instagram.com/p/Cx12345/"))
            self.assertIsNotNone(scan)
            self.assertEqual(scan["type"], "images")
            self.assertEqual(len(scan["image_urls"]), 3)

            res = asyncio.run(platform.download_images("https://www.instagram.com/p/Cx12345/"))
            self.assertTrue(res.success)
            self.assertEqual(len(res.image_urls), 3)

    def test_twitter_multi_image_scan(self):
        """Test X / Twitter 4-photo tweet."""
        platform = TwitterPlatform()
        mock_cobalt = {
            "success": True,
            "title": "X Tweet",
            "uploader": "artist",
            "video_url": None,
            "audio_url": None,
            "image_urls": [f"https://pbs.twimg.com/media/pic_{i}.jpg" for i in range(1, 5)]
        }
        with patch("bot.platforms.twitter.fetch_cobalt_mirror", new=AsyncMock(return_value=mock_cobalt)):
            scan = asyncio.run(platform.fast_scan("https://x.com/artist/status/123456"))
            self.assertIsNotNone(scan)
            self.assertEqual(scan["type"], "images")
            self.assertEqual(len(scan["image_urls"]), 4)

    def test_reddit_and_pinterest_image_scan(self):
        """Test direct and gallery image scans for Reddit and Pinterest."""
        reddit = RedditPlatform()
        scan_r = asyncio.run(reddit.fast_scan("https://i.redd.it/example123.jpg"))
        self.assertIsNotNone(scan_r)
        self.assertEqual(scan_r["type"], "images")
        self.assertEqual(scan_r["image_urls"][0], "https://i.redd.it/example123.jpg")

        pin = PinterestPlatform()
        scan_p = asyncio.run(pin.fast_scan("https://i.pinimg.com/originals/ab/cd/ef.png"))
        self.assertIsNotNone(scan_p)
        self.assertEqual(scan_p["type"], "images")
        self.assertEqual(scan_p["image_urls"][0], "https://i.pinimg.com/originals/ab/cd/ef.png")

    def test_generic_direct_image_url(self):
        """Test direct image link ending with .webp, .jpg, .png."""
        generic = GenericPlatform()
        scan = asyncio.run(generic.fast_scan("https://images.unsplash.com/photo-12345.webp?auto=format"))
        self.assertIsNotNone(scan)
        self.assertEqual(scan["type"], "images")
        self.assertIn(".webp", scan["image_urls"][0])


class TestSendImageGalleryAndCleanup(unittest.TestCase):
    def setUp(self):
        self.created_test_files = []

    def tearDown(self):
        for f in self.created_test_files:
            cleanup_files(f)
        self.created_test_files.clear()

    def test_send_image_gallery_chunks_up_to_10_photos(self):
        """Verify images are chunked into albums of up to 10 photos per group."""
        mock_context = MagicMock()
        mock_context.bot.send_media_group = AsyncMock()

        # 15 images should result in 2 media groups (10 + 5)
        fifteen_images = [f"https://cdn.example.com/photo_{i}.jpg" for i in range(1, 16)]
        ok = asyncio.run(send_image_gallery(mock_context, chat_id=12345, image_urls=fifteen_images, title="15 Photos"))
        self.assertTrue(ok)
        self.assertEqual(mock_context.bot.send_media_group.call_count, 2)

        first_call = mock_context.bot.send_media_group.call_args_list[0][1]["media"]
        second_call = mock_context.bot.send_media_group.call_args_list[1][1]["media"]
        self.assertEqual(len(first_call), 10)
        self.assertEqual(len(second_call), 5)

    def test_end_to_end_fast_scan_and_auto_send_images(self):
        """Test user pasting a 10-image link: scans first and automatically sends the 10 images back."""
        mock_update = MagicMock()
        mock_msg = MagicMock()
        mock_msg.text = "https://www.tiktok.com/@user/video/78910"
        mock_status = MagicMock()
        mock_status.delete = AsyncMock()
        mock_msg.reply_text = AsyncMock(return_value=mock_status)
        mock_update.message = mock_msg
        mock_update.effective_message = mock_msg
        mock_update.effective_chat.id = 998877

        mock_user = MagicMock()
        mock_user.id = 998877
        mock_user.username = "photo_lover"
        mock_update.effective_user = mock_user

        mock_context = MagicMock()
        mock_context.bot.send_media_group = AsyncMock()

        # Mock scan_url returning 10 images
        ten_images = [f"https://cdn.tiktok.com/img_{i}.jpg" for i in range(1, 11)]
        mock_scan = {
            "type": "images",
            "platform": "🎵 TikTok / Douyin",
            "platform_id": "tiktok",
            "image_urls": ten_images,
            "title": "10 TikTok Photos",
            "uploader": "Creator",
            "audio_url": "https://cdn.tiktok.com/audio.mp3",
        }

        with patch("bot.handlers.user_handlers.tracker.can_download", return_value=(True, 0, 10, True)), \
             patch("bot.handlers.user_handlers.scan_url", new=AsyncMock(return_value=mock_scan)):
            asyncio.run(handle_video_message(mock_update, mock_context))

            # Verify send_media_group was called with all 10 images
            self.assertTrue(mock_context.bot.send_media_group.called)
            media_sent = mock_context.bot.send_media_group.call_args[1]["media"]
            self.assertEqual(len(media_sent), 10)

            # Verify audio download button and confirmation reply were provided
            self.assertTrue(mock_msg.reply_text.called)

    def test_test_image_cleanup_policy(self):
        """Strict verification: temporary image files must be deleted immediately."""
        test_img_path = os.path.join(config.temp_dir, "test_temp_verify_delete.jpg")
        with open(test_img_path, "wb") as f:
            f.write(b"\xFF\xD8\xFF\xE0" + b"\x00" * 50)
        self.created_test_files.append(test_img_path)
        self.assertTrue(os.path.exists(test_img_path))

        # Perform cleanup
        cleanup_files(test_img_path)
        self.assertFalse(os.path.exists(test_img_path))


if __name__ == "__main__":
    unittest.main()
