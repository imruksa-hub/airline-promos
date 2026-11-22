import sys, unittest
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
import collect as c

PAGE = """<html><head><title>Promotions</title></head><body>
<a href="/th/promotion/summer-sale">Summer Sale ลด 50%</a>
<a href="/th/promotion/lucky-draw-2026"><img alt="ลุ้นชิงโชค ตั๋วฟรี"></a>
<a href="/th/about">About us</a>
<a href="https://other.com/promo">Other site promo</a>
<a href="/login?next=/promo">Login</a>
<script>var promo = 1;</script></body></html>"""

class T(unittest.TestCase):
    def test_links(self):
        p = c.parse(PAGE)
        links = c.candidate_links("https://www.air.example/th/promotion", p.links)
        urls = [u for u, _ in links]
        self.assertEqual(urls, ["https://www.air.example/th/promotion/summer-sale",
                                "https://www.air.example/th/promotion/lucky-draw-2026"])
        self.assertEqual(c.classify(" ".join(links[1])), "draw")
        self.assertEqual(c.classify(" ".join(links[0])), "fare")

    def test_dates(self):
        today = date(2026, 11, 22)
        self.assertEqual(c.guess_deadline("จองได้ถึง 30 พ.ย. 69", today), "2026-11-30")
        self.assertEqual(c.guess_deadline("ร่วมสนุกได้ตั้งแต่ 1 ตุลาคม 2569 - 15 ธันวาคม 2569", today), "2026-12-15")
        self.assertEqual(c.guess_deadline("Book by 5 Dec 2026, travel until March 31, 2027", today), "2027-03-31")
        self.assertIsNone(c.guess_deadline("ended 1 Jan 2025", today))

    def test_merge(self):
        prev = [{"id": "a", "airline": "x", "source": "auto", "first_seen": "2026-11-01T00:00:00+07:00",
                 "last_seen": "2026-11-01T00:00:00+07:00"},
                {"id": "b", "airline": "y", "source": "auto", "first_seen": "2026-11-01T00:00:00+07:00",
                 "last_seen": "2026-11-01T00:00:00+07:00"}]
        fresh = [{"id": "a", "airline": "x", "source": "auto"}]
        now = "2026-11-22T10:00:00+07:00"
        out = {i["id"]: i for i in c.merge(prev, fresh, {"x"}, now, date(2026, 11, 22))}
        self.assertEqual(out["a"]["first_seen"], "2026-11-01T00:00:00+07:00")
        self.assertIn("b", out)  # airline y failed this run, keep old items
        out = {i["id"]: i for i in c.merge(prev, fresh, {"x", "y"}, now, date(2026, 11, 22))}
        self.assertNotIn("b", out)  # y ran fine and item b is gone

class Normalize(unittest.TestCase):
    def test_same_page(self):
        self.assertEqual(c.normalize("http://Content.Nokair.com/th/a.aspx "), "https://content.nokair.com/th/a.aspx")
        self.assertEqual(c.normalize("https://x.com/a#top"), "https://x.com/a")

if __name__ == "__main__":
    unittest.main()
