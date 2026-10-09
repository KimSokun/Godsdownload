import os
import unittest
from unittest.mock import patch, MagicMock, AsyncMock
from bot.cloud_storage import upload_to_litterbox, upload_to_gofile, upload_large_file


class TestCloudStorage(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.test_file = "test_cloud_unit.mp4"
        with open(self.test_file, "wb") as f:
            f.write(b"SAMPLE_VIDEO_DATA" * 100)

    def tearDown(self):
        if os.path.exists(self.test_file):
            os.remove(self.test_file)

    async def test_upload_nonexistent_file(self):
        res = await upload_to_litterbox("non_existent_file.mp4")
        self.assertIsNone(res)

        g_res = await upload_to_gofile("non_existent_file.mp4")
        self.assertIsNone(g_res)

        large_res = await upload_large_file("non_existent_file.mp4")
        self.assertIsNone(large_res)

    @patch("aiohttp.ClientSession.post")
    async def test_litterbox_success(self, mock_post):
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text.return_value = "https://litter.catbox.moe/abc1234.mp4\n"
        mock_post.return_value.__aenter__.return_value = mock_resp

        link = await upload_to_litterbox(self.test_file)
        self.assertEqual(link, "https://litter.catbox.moe/abc1234.mp4")

    @patch("aiohttp.ClientSession.post")
    async def test_litterbox_failure_non_200(self, mock_post):
        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.text.return_value = "Internal Server Error"
        mock_post.return_value.__aenter__.return_value = mock_resp

        link = await upload_to_litterbox(self.test_file)
        self.assertIsNone(link)

    @patch("aiohttp.ClientSession.post")
    @patch("aiohttp.ClientSession.get")
    async def test_gofile_success(self, mock_get, mock_post):
        # Mock servers response
        mock_s_resp = AsyncMock()
        mock_s_resp.status = 200
        mock_s_resp.json.return_value = {"status": "ok", "data": {"servers": [{"name": "store5"}]}}
        mock_get.return_value.__aenter__.return_value = mock_s_resp

        # Mock upload response
        mock_up_resp = AsyncMock()
        mock_up_resp.status = 200
        mock_up_resp.json.return_value = {
            "status": "ok",
            "data": {"downloadPage": "https://gofile.io/d/TEST1234"}
        }
        mock_post.return_value.__aenter__.return_value = mock_up_resp

        link = await upload_to_gofile(self.test_file)
        self.assertEqual(link, "https://gofile.io/d/TEST1234")

    @patch("bot.cloud_storage.upload_to_litterbox", new_callable=AsyncMock)
    async def test_auto_switch_litterbox_primary(self, mock_litterbox):
        mock_litterbox.return_value = "https://litter.catbox.moe/primary.mp4"
        link = await upload_large_file(self.test_file)
        self.assertEqual(link, "https://litter.catbox.moe/primary.mp4")

    @patch("bot.cloud_storage.upload_to_gofile", new_callable=AsyncMock)
    @patch("bot.cloud_storage.upload_to_litterbox", new_callable=AsyncMock)
    async def test_auto_switch_fallback_to_gofile(self, mock_litterbox, mock_gofile):
        # Litterbox fails (None) -> auto switches to Gofile
        mock_litterbox.return_value = None
        mock_gofile.return_value = "https://gofile.io/d/FALLBACK"

        link = await upload_large_file(self.test_file)
        self.assertEqual(link, "https://gofile.io/d/FALLBACK")
        mock_litterbox.assert_called_once()
        mock_gofile.assert_called_once()

    @patch("bot.cloud_storage.upload_to_gofile", new_callable=AsyncMock)
    @patch("bot.cloud_storage.upload_to_litterbox", new_callable=AsyncMock)
    async def test_auto_switch_both_fail(self, mock_litterbox, mock_gofile):
        mock_litterbox.return_value = None
        mock_gofile.return_value = None

        link = await upload_large_file(self.test_file)
        self.assertIsNone(link)


if __name__ == "__main__":
    unittest.main()
