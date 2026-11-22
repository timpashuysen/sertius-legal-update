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


if __name__ == "__main__":
    unittest.main()
