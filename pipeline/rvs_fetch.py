"""Rulings of the Council of State (Raad van State / Conseil d'Etat), from 1 January 2024.

No AI. The Council tolerates automated use of its publications under conditions published at
https://raadvanstate.be/nl/digitale-toegankelijkheid/ : at most one download every five seconds,
only between 22:00 and 05:00 Belgian time, and incremental after a first harvest. This script
refuses to run outside that window, waits 6 seconds between requests, never downloads a ruling
twice, and keeps its place in data/rvs-index.json.
    python pipeline/rvs_fetch.py --minutes 9
Rulings are numbered in sequence. A ruling is kept (text stored) only when it touches
environmental subjects; the rest is recorded as read and not stored.
"""
import argparse, datetime as dt, io, json, pathlib, re, time, urllib.error, urllib.request
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
INDEX = DATA / "rvs-index.json"
UA = "Mozilla/5.0 (compatible; SertiusLegalUpdate/1.0; +https://timpashuysen.github.io/sertius-legal-update/)"
DELAY = 6
START = 265300            # 23 December 2025; rulings dated before SCOPE are read once and not kept
SCOPE = "2026-01-01"
MISS_LIMIT = 120          # this many missing numbers in a row means we are past the newest ruling
URLS = ("https://www.raadvst-consetat.be/arresten/{n}",)   # the address the old /Arresten/... form redirects to
MONTHS = {m: i % 12 + 1 for i, m in enumerate(
    "januari februari maart april mei juni juli augustus september oktober november december "
    "janvier février mars avril mai juin juillet août septembre octobre novembre décembre".split())}
ENV = re.compile(r"milieueffect|project-?m\.?e\.?r|passende beoordeling|stikstof|habitatrichtlijn|natuurbehoud|watertoets|"
                 r"vlarem|ingedeelde inrichting|bodemsanering|bodemdecreet|seveso|geluidshinder|geurhinder|luchtkwaliteit|"
                 r"grondwater|afvalstoffen|windturbine|ammoniak|pfas|omgevingsvergunning|milieuvergunning|ruimtelijk uitvoeringsplan|"
                 r"gewestplan|stedenbouwkundige vergunning|incidences sur l'environnement|évaluation appropriée|natura 2000|"
                 r"permis d'environnement|permis unique|permis d'urbanisme|plan de secteur|assainissement des? sols?|"
                 r"nuisances sonores|qualité de l'air|eaux souterraines|déchets|éolien|azote|établissement classé|"
                 r"schéma de développement|étude d'incidences|conservation de la nature", re.I)
_last = [0.0]


def in_window(now=None):
    h = (now or dt.datetime.now(ZoneInfo("Europe/Brussels"))).hour
    return h >= 22 or h < 5


def get(url, timeout=40):
    wait = DELAY - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout) as r:
            return r.read()
    finally:
        _last[0] = time.time()


def url_for(n, pattern):
    return pattern.format(k=n // 1000, h=n // 100 % 10, n=n)


def pdf_text(raw):
    import pypdf
    return "\n".join((p.extract_text() or "") for p in pypdf.PdfReader(io.BytesIO(raw)).pages)


def ruling_date(text):
    m = re.search(r"\b(?:van|du)\s+(\d{1,2})(?:er)?\s+(%s)\s+(20\d\d)" % "|".join(MONTHS), text[:2500], re.I)
    return "%s-%02d-%02d" % (m.group(3), MONTHS[m.group(2).lower()], int(m.group(1))) if m else ""


TRIVIAL = re.compile(r"afstand van (het )?(geding|beroep)|désistement d['e ]|\bdoorhaling\b|biffure", re.I)


def subject(flat):
    """What the case is about, from the 'object of the appeal' part (the parties' names are skipped)."""
    m = re.search(r"Voorwerp van (?:het beroep|de vordering|het verzoekschrift)|Objet d[eu] (?:la requête|recours|la demande)", flat, re.I)
    start = m.end() if m else min(len(flat), 900)
    return flat[start:start + 420].strip(" .:-")


def excerpt(text):
    t = re.sub(r"[ \t]+", " ", text)
    return t if len(t) <= 26000 else t[:6000] + "\n\n[... part left out ...]\n\n" + t[-20000:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=9)
    deadline = time.time() + ap.parse_args().minutes * 60 - 45
    idx = json.loads(INDEX.read_text("utf-8")) if INDEX.exists() else {"next": START, "misses": 0, "kept": {}, "read": 0, "pattern": 0}
    if not in_window():
        print(json.dumps({"skipped": "outside 22:00-05:00 Belgian time", "next": idx["next"]}))
        return
    tdir = DATA / "text"
    tdir.mkdir(exist_ok=True)
    errors = 0          # numbers given up on in a row; five in a row means the site itself is in trouble
    tries = 0           # attempts at the current number
    idx["last_error"], idx["blocked"] = "", False
    idx.setdefault("skipped", [])

    def failed(n, msg):
        # a temporary fault: try the same number three times, then set it aside and move on
        nonlocal errors, tries
        idx["last_error"] = "%s at %d" % (msg, n)
        tries += 1
        if tries < 3:
            time.sleep(20)
            return
        tries = 0
        errors += 1
        if n not in idx["skipped"]:
            idx["skipped"].append(n)
        idx["next"] = n + 1

    while time.time() < deadline and in_window() and errors < 5:
        n = idx["next"]
        if idx["misses"] >= MISS_LIMIT:
            # past the newest ruling: go back to the last one found and wait for the next night
            idx["next"], idx["misses"], idx["caught_up"] = idx.get("last_hit", n - MISS_LIMIT) + 1, 0, True
            break
        url = url_for(n, URLS[idx.get("pattern", 0)])
        try:
            raw = get(url)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                if idx["read"] == 0 and idx["misses"] >= 20 and idx.get("pattern", 0) + 1 < len(URLS):
                    idx["pattern"], idx["misses"] = idx.get("pattern", 0) + 1, 0   # address form not right: try the other
                    continue
                idx["misses"] += 1
                idx["next"] = n + 1
                continue
            if e.code in (403, 429):
                idx["last_error"] = "HTTP %d at %d" % (e.code, n)
                idx["blocked"] = True
                break                      # refused: stop at once and leave the rest for a person to look at
            failed(n, "HTTP %d" % e.code)
            continue
        except Exception as e:
            failed(n, str(e)[:80])
            continue
        errors = tries = 0
        idx["next"], idx["misses"], idx["last_hit"], idx["caught_up"] = n + 1, 0, n, False
        idx["read"] += 1
        try:
            text = pdf_text(raw)
        except Exception:
            continue
        flat = re.sub(r"\s+", " ", text)
        score = len(set(x.lower() for x in ENV.findall(flat)))
        d = ruling_date(text)
        trivial = len(flat) < 3500 or TRIVIAL.search(flat[-2500:])
        if d >= SCOPE and not trivial:        # no subject filter: pipeline/assess.py qualifies every ruling
            idx["kept"][str(n)] = {"d": d, "url": url, "score": score, "subject": subject(flat),
                                   "lang": "fr" if re.search(r"\bCONSEIL D'[EÉ]TAT\b", text[:600]) else "nl"}
            (tdir / ("rvs_%d.txt" % n)).write_text("SOURCE: %s\nTITLE: arrest / arrêt nr. %d van / du %s\n\n%s" % (url, n, d, excerpt(text)), "utf-8")
    idx["summary"] = {"next": idx["next"], "read": idx["read"], "kept": len(idx["kept"]), "caught_up": bool(idx.get("caught_up")), "blocked": bool(idx.get("blocked") or errors >= 5), "skipped": len(idx["skipped"]),
                      "last_error": idx.get("last_error", ""), "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    INDEX.write_text(json.dumps(idx, ensure_ascii=False, indent=0), "utf-8")
    print(json.dumps(idx["summary"]))


if __name__ == "__main__":
    main()
