import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import stream_embed as se

UC = "UC" + "a" * 22


class EmbedTests(unittest.TestCase):
    def test_kick(self):
        t = se.build_target("kick", "https://kick.com/Some_Streamer")
        self.assertEqual(t["mode"], "url")
        self.assertEqual(t["src"], "https://player.kick.com/some-streamer")
        self.assertEqual(t["external"], "https://kick.com/some-streamer")

    def test_kick_bad_url(self):
        self.assertEqual(se.build_target("kick", "https://kick.com/videos")["mode"], "external")

    def test_youtube_video_from_stream_url(self):
        t = se.build_target("youtube", "https://www.youtube.com/@abc", stream_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertEqual(t["mode"], "html")
        self.assertIn("embed/dQw4w9WgXcQ", t["src"])
        self.assertIn("<iframe", t["html"])

    def test_youtube_session_id_is_video(self):
        self.assertIn("embed/dQw4w9WgXcQ", se.build_target("youtube", "https://www.youtube.com/@abc", session_id="dQw4w9WgXcQ")["src"])

    def test_youtube_channel_live(self):
        t = se.build_target("youtube", "https://www.youtube.com/@abc", channel_id=UC)
        self.assertIn("live_stream?channel=" + UC, t["src"])
        t = se.build_target("youtube", f"https://www.youtube.com/channel/{UC}")
        self.assertIn(UC, t["src"])

    def test_youtube_handle_only_is_external(self):
        t = se.build_target("youtube", "https://www.youtube.com/@abc")
        self.assertEqual(t["mode"], "external")
        self.assertTrue(t["external"].endswith("/@abc/live"))

    def test_demo_session_not_a_video(self):
        self.assertEqual(se.build_target("youtube", "https://www.youtube.com/@abc", session_id="demo-1")["mode"], "external")

    def test_video_id(self):
        self.assertEqual(se.youtube_video_id("https://youtu.be/dQw4w9WgXcQ"), "dQw4w9WgXcQ")
        self.assertEqual(se.youtube_video_id("nonsense"), "")


if __name__ == "__main__":
    unittest.main()
