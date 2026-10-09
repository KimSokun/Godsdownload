import os
import unittest
import asyncio
from bot.platforms.base import get_ffmpeg_path, ensure_ios_compatible_video_sync, ensure_ios_compatible_video
from bot.config import config

class TestIOSCompatibility(unittest.TestCase):
    def setUp(self):
        self.ffmpeg = get_ffmpeg_path()
        self.temp_files = []

    def tearDown(self):
        for f in self.temp_files:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception:
                    pass

    def test_ffmpeg_detected(self):
        self.assertIsNotNone(self.ffmpeg)
        self.assertTrue(os.path.exists(self.ffmpeg))

    def test_remux_ios_compatible_video_sync(self):
        if not self.ffmpeg:
            self.skipTest("FFmpeg not installed")

        test_file = os.path.join(config.temp_dir, "test_input.mp4")
        self.temp_files.append(test_file)

        # Generate a 1-second H.264 + AAC MP4
        import subprocess
        cmd = [
            self.ffmpeg, "-y",
            "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            test_file
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(res.returncode, 0)
        self.assertTrue(os.path.exists(test_file))

        # Run ensure_ios_compatible_video_sync
        out = ensure_ios_compatible_video_sync(test_file)
        self.assertTrue(os.path.exists(out))
        self.assertTrue(out.endswith(".mp4"))
        self.assertGreater(os.path.getsize(out), 0)

    def test_transcode_incompatible_video_async(self):
        if not self.ffmpeg:
            self.skipTest("FFmpeg not installed")

        test_file = os.path.join(config.temp_dir, "test_incomp.webm")
        self.temp_files.append(test_file)

        # Generate a 1-second VP8 + Opus WEBM
        import subprocess
        cmd = [
            self.ffmpeg, "-y",
            "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=1",
            "-c:v", "libvpx", "-c:a", "libopus",
            test_file
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(res.returncode, 0)

        # Run async ensure_ios_compatible_video
        async def run_test():
            return await ensure_ios_compatible_video(test_file)

        out = asyncio.run(run_test())
        self.temp_files.append(out)
        self.assertTrue(os.path.exists(out))
        self.assertTrue(out.endswith(".mp4"))
        self.assertGreater(os.path.getsize(out), 0)

if __name__ == "__main__":
    unittest.main()
