"""Offline tests for the assessment and writing steps, with a stand-in for the API."""
import json, pathlib, sys, tempfile, unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import assess, llm, write_entries


def fake(payload):
    if payload["model"] == llm.MODELS["small"]:
        lines = [l for l in payload["messages"][0]["content"].split("\n") if l.strip()]
        rows = [{"n": i + 1, "relevant": "bodem" in l, "topics": ["soil", "bogus"] if "bodem" in l else [], "reason": "x", "confidence": "high"}
                for i, l in enumerate(lines)]
        return {"content": [{"type": "text", "text": "Result: " + json.dumps(rows)}], "usage": {"input_tokens": 900, "output_tokens": 150}}
    e = {"relevant": True, "h": "Flanders amends the soil decree on guide values", "a": "A decree changes the soil rules for operators.",
         "b": "Operators of risk sites are affected. The decree changes the rules. It enters into force in March.",
         "nl": ["Vlaanderen wijzigt het Bodemdecreet", "Een decreet wijzigt de bodemregels.", "Exploitanten van risicogronden zijn betrokken."],
         "fr": ["La Flandre modifie le décret sol", "Un décret modifie les règles relatives au sol.", "Les exploitants de terrains à risque sont concernés."],
         "s": "In force", "scope": "Bodemdecreet, art. 5", "dates": {"adopted": "2026-02-27", "published": "", "in_force": "junk"}}
    return {"content": [{"type": "text", "text": json.dumps(e)}], "usage": {"input_tokens": 9000, "output_tokens": 900}}


ITEMS = [
    {"id": "codex:1", "src": "codex", "level": "VL", "t": "law", "d": "2026-02-27", "title": {"nl": "Decreet tot wijziging van het bodemdecreet"}, "url": "https://codex.vlaanderen.be/x"},
    {"id": "sb:2", "src": "staatsblad", "level": "BE", "t": "law", "d": "2026-03-01", "title": {"nl": "Personeel. - Benoeming"}, "url": "https://www.ejustice.just.fgov.be/x"},
    {"id": "eu:3", "src": "eurlex", "level": "EU", "t": "law", "d": "2026-03-02", "title": {"en": "Regulation imposing an anti-dumping duty"}, "url": "https://eur-lex.europa.eu/x"},
    {"id": "codex:4", "src": "codex", "level": "VL", "t": "law", "d": "2025-05-05", "title": {"nl": "Ouder bodembesluit"}, "url": "https://codex.vlaanderen.be/y"},
]


class Qualification(unittest.TestCase):
    def test_one_verdict_per_item_in_scope(self):
        log = {"items": {}}
        done, left = assess.assess(ITEMS, log, llm.Client(1, transport=fake), "2026-11-22")
        self.assertEqual((done, left), (3, 0))
        self.assertEqual(log["items"]["codex:1"]["verdict"], "relevant")
        self.assertEqual(log["items"]["codex:1"]["topics"], ["soil"])
        self.assertEqual(log["items"]["sb:2"], {"v": assess.VERSION, "verdict": "excluded", "by": "rule", "reason": "staff and appointments", "at": "2026-11-22"})
        self.assertEqual(log["items"]["eu:3"]["verdict"], "not relevant")
        self.assertNotIn("codex:4", log["items"])          # before the scope start

    def test_without_key_only_rules_run(self):
        log = {"items": {}}
        done, left = assess.assess(ITEMS, log, llm.Client(1, key=""), "2026-11-22")
        self.assertEqual((done, left), (1, 2))

    def test_cut_off_reply_keeps_complete_verdicts(self):
        rows = assess.parse_rows('[{"n": 1, "relevant": true, "topics": [], "reason": "a", "confidence": "high"},\n{"n": 2, "relevant": fal')
        self.assertEqual([r["n"] for r in rows], [1])

    def test_spending_cap_stops_the_run(self):
        c = llm.Client(0.0, transport=fake)
        with self.assertRaises(llm.CapReached):
            c.ask(llm.MODELS["small"], "s", "u")


class Writing(unittest.TestCase):
    def test_entry_is_written_and_validated(self):
        with tempfile.TemporaryDirectory() as d:
            write_entries.DATA = pathlib.Path(d)
            (pathlib.Path(d) / "text").mkdir()
            (pathlib.Path(d) / "text" / "codex_1.txt").write_text("Artikel 1. ...", "utf-8")
            log = {"items": {}}
            assess.assess(ITEMS, log, llm.Client(1, transport=fake), "2026-11-22")
            digest = {"items": []}
            made, dropped, failed, waiting = write_entries.run(ITEMS, log, digest, llm.Client(1, transport=fake), "2026-11-22")
        self.assertEqual((made, dropped, failed, waiting), (1, 0, 0, 0))
        e = digest["items"][0]
        self.assertEqual(e["id"], "d-codex-1")
        self.assertEqual(e["k"], ["soil"])
        self.assertEqual(e["dates"], {"adopted": "2026-02-27"})
        self.assertEqual(e["inbox"], ["codex:1"])
        self.assertEqual(log["items"]["codex:1"]["written"], "d-codex-1")


if __name__ == "__main__":
    unittest.main()
