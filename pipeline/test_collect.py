"""Offline tests for the collector. No network access."""
import importlib.util
import pathlib
import unittest

spec = importlib.util.spec_from_file_location("collect", pathlib.Path(__file__).with_name("collect.py"))
collect = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collect)


class Classify(unittest.TestCase):
    def test_topics(self):
        self.assertIn("soil", collect.classify("Decreet tot wijziging van het Bodemdecreet van 27 oktober 2006"))
        self.assertIn("permitting", collect.classify("Besluit tot wijziging van titel II van het VLAREM"))
        self.assertIn("environmental-law", collect.classify("décret relatif au permis d'environnement"))
        self.assertEqual(collect.classify("President Costa's proposal to reach an agreement"), [])

    def test_strip_html(self):
        self.assertEqual(collect.strip_html(b"<p>Art. 1</p><script>x()</script><style>a{}</style>"), "Art. 1")

    def test_text_name(self):
        self.assertEqual(collect.text_name("eu:32026R2530"), "eu_32026R2530.txt")


INDEX = """<html><body><ul>
<li><a href="/nl/ordonnantie-van-21-december-2023_n2023048123.html">21 DECEMBER 2023. - bron brussels hoofdstedelijk gewest Ordonnantie tot wijziging van de ordonnantie betreffende het beheer en de sanering van verontreinigde bodems</a></li>
<li><a href="/nl/koninklijk-besluit-van-10-december-2023_n2023205123.html">bron federale overheidsdienst volksgezondheid Koninklijk besluit tot wijziging van het koninklijk besluit betreffende gevaarlijke stoffen</a></li>
<li><a href="/nl/besluit-van-de-vlaamse-regering_n2023048999.html">bron vlaamse overheid Besluit van de Vlaamse Regering tot wijziging van het VLAREM</a></li>
<li><a href="/nl/benoeming_n2023055555.html">bron federale overheidsdienst justitie Koninklijk besluit houdende benoeming van een rechter</a></li>
<li><a href="/nl/bericht_n2023066666.html">bron federale overheidsdienst financien Bericht over de rentevoet</a></li>
<li><a href="/nl/decreet_n2023077777.html">bron ministerie van de franse gemeenschap Decreet houdende diverse maatregelen inzake leefmilieu</a></li>
<li><a href="/nl/kb_n2023088888.html">bron federale overheidsdienst justitie Koninklijk besluit tot wijziging van het Gerechtelijk Wetboek</a></li>
<li><a href="/nl/p_n2023099999.html">bron federale overheidsdienst volksgezondheid Personeel. - Bevordering Bij koninklijk besluit wordt mevrouw X bevorderd</a></li>
<li><a href="/nl/b_n2023011111.html">bron brussels hoofdstedelijk gewest Besluit van de Brusselse Hoofdstedelijke Regering tot wijziging van het BWRO</a></li>
<li><a href="/nl/about.html">Over Etaamb</a></li>
</ul></body></html>"""


class Staatsblad(unittest.TestCase):
    def rows(self):
        parser = collect._EtaambIndexParser()
        parser.feed(INDEX)
        return parser.links

    def items(self):
        day = collect.dt.date(2024, 1, 2)
        out = [collect._sb_item(day, n, h, label) for n, h, label in self.rows()]
        return [i for i in out if i]

    def test_parser_reads_numac_links_only(self):
        self.assertEqual([n for n, _, _ in self.rows()],
                         ["2023048123", "2023205123", "2023048999", "2023055555", "2023066666", "2023077777", "2023088888", "2023099999", "2023011111"])

    def test_filter_and_levels(self):
        got = {i["ref"]: i for i in self.items()}
        self.assertEqual(sorted(got), ["2023011111", "2023048123", "2023205123"])
        self.assertEqual(got["2023011111"]["x"], 1)  # Brussels act without keyword: kept for the digest to judge
        self.assertEqual(got["2023048123"]["x"], 0)  # Flemish, community, appointment and plain notice dropped
        self.assertEqual(got["2023048123"]["level"], "BRU")
        self.assertEqual(got["2023205123"]["level"], "BE")
        self.assertIn("soil", got["2023048123"]["k"])
        self.assertTrue(got["2023048123"]["title"]["nl"].startswith("Ordonnantie"))
        self.assertEqual(got["2023048123"]["d"], "2024-01-02")
        self.assertIn("pd=2024-01-02", got["2023048123"]["url"])
        self.assertIn("numac=2023048123", got["2023048123"]["url"])

    def run_collect(self, fake_day, today, state):
        real_day, real_today, real_sleep = collect._sb_day, collect.TODAY, collect.time.sleep
        collect._sb_day, collect.TODAY, collect.time.sleep = fake_day, today, lambda s: None
        try:
            items = {}
            return collect.collect_staatsblad(state, items), items
        finally:
            collect._sb_day, collect.TODAY, collect.time.sleep = real_day, real_today, real_sleep

    def test_reads_weekdays_up_to_and_including_today(self):
        seen = []

        def fake(day, timeout=20, lang="nl"):
            if lang == "fr":
                return [("2023048123", "/fr/x_n2023048123.html", "source region de bruxelles-capitale Ordonnance modifiant l'ordonnance relative aux sols pollués")]
            seen.append(day.isoformat())
            return self.rows()
        today = collect.dt.date(2024, 1, 8)  # a Monday
        res, items = self.run_collect(fake, today, {"sb_next": "2024-01-04"})
        self.assertTrue(res["ok"])
        # backfill from the cursor, weekend skipped, today read, plus the refresh of the day before the cursor
        self.assertEqual(sorted(seen), ["2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"])  # each day once
        self.assertEqual(len(items), 3)
        self.assertTrue(items["sb:2023048123"]["title"]["fr"].startswith("Ordonnance modifiant"))
        self.assertNotIn("fr", items["sb:2023205123"]["title"])
        self.assertEqual(res["backfilled_to"], "2024-01-08")

    def test_failure_is_reported_and_cursor_stays(self):
        def boom(day, timeout=20, lang="nl"):
            raise OSError("connection reset")
        state = {"sb_next": "2024-01-04"}
        res, _ = self.run_collect(boom, collect.dt.date(2024, 1, 8), state)
        self.assertFalse(res["ok"])
        self.assertEqual(state["sb_next"], "2024-01-04")
        self.assertEqual(res["read"], 0)


if __name__ == "__main__":
    unittest.main()
