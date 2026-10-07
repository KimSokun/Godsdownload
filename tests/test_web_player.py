import os
import unittest
import asyncio
from aiohttp.test_utils import TestClient, TestServer
from bot.web_player import create_web_player_app, register_stream
from bot.platforms.youtube import extract_youtube_id, clean_youtube_url, YouTubePlatform

class TestWebPlayerAndYouTube(unittest.TestCase):
    def test_youtube_url_parsing(self):
        self.assertEqual(extract_youtube_id('https://www.youtube.com/watch?v=aqz-KE-bpKQ'), 'aqz-KE-bpKQ')
        self.assertEqual(extract_youtube_id('https://www.youtube.com/shorts/aqz-KE-bpKQ'), 'aqz-KE-bpKQ')
        self.assertEqual(extract_youtube_id('https://youtube.com/shorts/aqz-KE-bpKQ?feature=share'), 'aqz-KE-bpKQ')
        self.assertEqual(extract_youtube_id('https://youtu.be/aqz-KE-bpKQ'), 'aqz-KE-bpKQ')
        self.assertEqual(clean_youtube_url('https://www.youtube.com/shorts/aqz-KE-bpKQ?feature=share'), 'https://www.youtube.com/watch?v=aqz-KE-bpKQ')

    def test_platform_can_handle(self):
        p = YouTubePlatform()
        self.assertTrue(p.can_handle('https://www.youtube.com/watch?v=jNQXAC9IVRw'))
        self.assertTrue(p.can_handle('https://www.youtube.com/shorts/aqz-KE-bpKQ'))
        self.assertTrue(p.can_handle('https://youtu.be/jNQXAC9IVRw'))

    def test_web_player_endpoints(self):
        async def run_async():
            from bot.config import config
            app = create_web_player_app()
            os.makedirs(config.temp_dir, exist_ok=True)
            test_file = os.path.join(config.temp_dir, 'unit_test_sample.mp4')
            with open(test_file, 'wb') as f:
                f.write(b'TEST_MP4_PAYLOAD_12345')
            try:
                token = register_stream(
                    filepath=test_file,
                    title='My Awesome Video',
                    uploader='Top Creator',
                    filesize_mb=5.5
                )
                async with TestClient(TestServer(app)) as client:
                    resp = await client.get(f'/watch?v={token}')
                    self.assertEqual(resp.status, 200)
                    body = await resp.text()
                    self.assertIn('My Awesome Video', body)
                    self.assertIn('Top Creator', body)
                    self.assertIn('Verified Safe Media', body)
                    self.assertIn('/download?v=', body)
                    self.assertIn('<video', body)

                    s_resp = await client.get(f'/stream?v={token}')
                    self.assertEqual(s_resp.status, 200)
                    s_bytes = await s_resp.read()
                    self.assertEqual(s_bytes, b'TEST_MP4_PAYLOAD_12345')

                    d_resp = await client.get(f'/download?v={token}')
                    self.assertEqual(d_resp.status, 200)
                    self.assertIn('attachment', d_resp.headers.get('Content-Disposition', ''))
                    self.assertIn('My Awesome Video.mp4', d_resp.headers.get('Content-Disposition', ''))
                    d_bytes = await d_resp.read()
                    self.assertEqual(d_bytes, b'TEST_MP4_PAYLOAD_12345')

                    exp_resp = await client.get('/watch?v=nonexistent_token')
                    self.assertEqual(exp_resp.status, 404)
                    exp_text = await exp_resp.text()
                    self.assertIn('Stream Expired', exp_text)
            finally:
                if os.path.exists(test_file):
                    os.remove(test_file)
        asyncio.run(run_async())

if __name__ == '__main__':
    unittest.main()
