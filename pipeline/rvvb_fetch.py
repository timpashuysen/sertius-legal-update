"""Backfill of rulings of the Raad voor Vergunningsbetwistingen and the Handhavingscollege (dbrc.be).

No AI. Runs where dbrc.be can be reached (it refuses GitHub's servers), in slices:
    python pipeline/rvvb_fetch.py --minutes 9
Phase 1 walks the public list (20 PDF links per page, sorted by name, descending) and records the
names of work years 2023-2024 and later. Phase 2 downloads each ruling, reads its date and text,
and keeps the text only when the ruling is dated from 1 January 2024, is not a mere withdrawal or
inadmissibility order, and touches environmental subjects. Progress is kept in data/rvvb-index.json.
One request every 10 seconds, as the site's robots.txt asks.
"""
import argparse, io, json, pathlib, re, time, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
INDEX = DATA / "rvvb-index.json"
BASE = "https://www.dbrc.be"
UA = "Mozilla/5.0 (compatible; SertiusLegalUpdate/1.0; +https://timpashuysen.github.io/sertius-legal-update/)"
DELAY = 10
SCOPE = "2026-01-01"   # rulings dated from here are all kept (unless a mere withdrawal); pipeline/assess.py qualifies them
LAST_PAGE_GUESS = 1000
NAME = re.compile(r"^(RVVB|HHC)\.(?:(UDN|S|A)\.)?(\d{2})(\d{2})\.(\d{4})(?:_\d+)?\.pdf$", re.I)
MONTHS = {m: i + 1 for i, m in enumerate(
    "januari februari maart april mei juni juli augustus september oktober november december".split())}
# looked for in the closing part only, where the decision is
TRIVIAL = re.compile(r"afstand van (het )?(beroep|geding|de vordering)|stelt de afstand vast|klaarblijkelijk onontvankelijk|"
                     r"rolrecht (werd |is )?niet (tijdig )?(betaald|gestort)", re.I)
ENV = re.compile(r"milieueffect|project-?m\.?e\.?r|mer-screening|passende beoordeling|stikstof|habitatrichtlijn|"
                 r"natuurtoets|watertoets|vlarem|ingedeelde inrichting|exploitatie van|bodemsanering|bodemdecreet|"
                 r"seveso|geluidshinder|geurhinder|luchtkwaliteit|bemaling|grondwater|afvalstoffen|windturbine|"
                 r"veehouderij|ammoniak|pfas|omgevingsveiligheid|milieuvergunning|bestuurlijke geldboete|"
                 r"milieuhandhaving|speciale beschermingszone", re.I)
_last = [0.0]


def get(url, timeout=40):
    wait = DELAY - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout) as r:
            return r.read()
    finally:
        _last[0] = time.time()


def recent(name):
    m = NAME.match(name)
    return bool(m and int(m.group(4)) == int(m.group(3)) + 1 and int(m.group(3)) >= 25)   # work year 2025-2026 on


def page_links(html):
    return re.findall(r'href="(https://www\.dbrc\.be/sites/default/files/[^"]+/([^"/]+\.pdf))"', html)


def walk_list(idx, deadline):
    """Record recent names. Old pages are skipped in jumps of 8; a jump that lands on recent names is retraced."""
    st = idx.setdefault("list", {"page": 0, "jump": 0, "done": False})
    while not st["done"] and time.time() < deadline:
        links = page_links(get("%s/legislation?page=%d" % (BASE, st["page"])).decode("utf-8", "replace"))
        names = [n for _, n in links if NAME.match(n) or re.match(r"^[A-Z]", n)]
        if not links or not names or st["page"] > LAST_PAGE_GUESS:
            st["done"] = True
            break
        hits = [(u, n) for u, n in links if recent(n)]
        if hits and st["jump"]:
            st["page"] -= 7          # we jumped over the start of a block: go back and walk it page by page
            st["jump"] = 0
            st["walk"] = 8
            continue
        for u, n in hits:
            idx["rulings"].setdefault(n, {"url": u})
        if hits or st.get("walk", 0) > 0:
            st["page"] += 1
            st["walk"] = max(0, st.get("walk", 0) - 1)
        else:
            st["page"] += 8
            st["jump"] = 1
    return st["done"]


def pdf_text(raw):
    import pypdf
    return "\n".join((p.extract_text() or "") for p in pypdf.PdfReader(io.BytesIO(raw)).pages)


def ruling_date(text):
    m = re.search(r"\bvan\s+(\d{1,2})\s+(%s)\s+(20\d\d)" % "|".join(MONTHS), text[:3000], re.I)
    return "%s-%02d-%02d" % (m.group(3), MONTHS[m.group(2).lower()], int(m.group(1))) if m else ""


def body(text):
    """The ruling from 'Voorwerp' on: the header with the parties' names and addresses is not stored."""
    m = re.search(r"\bI\.\s*Voorwerp|\bVoorwerp van (het beroep|de vordering)", text)
    t = re.sub(r"[ \t]+", " ", text[m.start():] if m else text)
    return t if len(t) <= 40000 else t[:8000] + "\n\n[... part left out ...]\n\n" + t[-32000:]


def subject(text):
    m = re.search(r"(vernietiging|schorsing)[^.]{0,60}van de beslissing van[^.]{10,260}\.", re.sub(r"\s+", " ", text[:6000]), re.I)
    return m.group(0)[:300] if m else ""


def fetch_rulings(idx, deadline):
    tdir = DATA / "text"
    tdir.mkdir(exist_ok=True)
    for n, r in idx["rulings"].items():
        if not recent(n) and "kept" not in r:
            r["kept"], r["why"] = 0, "before the scope date"
        elif str(r.get("why", "")).startswith("no environmental subject") and r.get("d", "") >= SCOPE:
            for k in ("kept", "why"):          # read under the old subject filter: read again, no subject filter now
                r.pop(k, None)
    todo = sorted((n for n, r in idx["rulings"].items() if "kept" not in r), reverse=True)
    for n in todo:
        if time.time() > deadline:
            break
        r = idx["rulings"][n]
        try:
            text = pdf_text(get(r["url"]))
        except Exception as e:
            r["err"] = r.get("err", 0) + 1
            if r["err"] >= 3:
                r["kept"] = 0
                r["why"] = "unreadable: %s" % str(e)[:60]
            continue
        r["d"] = ruling_date(text)
        flat = re.sub(r"\s+", " ", text)
        score = len(set(x.lower() for x in ENV.findall(flat)))
        if r["d"] and r["d"] < SCOPE:
            r["kept"], r["why"] = 0, "before the scope date"
        elif TRIVIAL.search(flat[-2500:]) or len(flat) < 6000:
            r["kept"], r["why"] = 0, "withdrawal, inadmissible or very short"
        else:
            r["kept"], r["score"], r["subject"] = 1, score, subject(text)
            key = re.sub(r"[^A-Za-z0-9]+", "_", "rvvb:" + n[:-4])
            (tdir / (key + ".txt")).write_text("SOURCE: %s\nTITLE: arrest %s van %s\n\n%s" % (r["url"], n[:-4], r["d"], body(text)), "utf-8")
    return not any("kept" not in r for r in idx["rulings"].values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=9)
    deadline = time.time() + ap.parse_args().minutes * 60 - 45
    idx = json.loads(INDEX.read_text("utf-8")) if INDEX.exists() else {"rulings": {}}
    # rulings from RVVB.2627.0166 on are read by the digest stage itself
    done = walk_list(idx, deadline)
    for n in list(idx["rulings"]):
        m = NAME.match(n)
        if m and m.group(1).upper() == "RVVB" and not m.group(2) and (m.group(3), m.group(5)) >= ("26", "0166"):
            idx["rulings"][n].setdefault("kept", 0)
            idx["rulings"][n].setdefault("why", "read by the digest stage")
    finished = done and fetch_rulings(idx, deadline)
    rs = idx["rulings"].values()
    idx["summary"] = {"names": len(idx["rulings"]), "read": sum("kept" in r for r in rs), "kept": sum(r.get("kept", 0) for r in rs),
                      "list_done": done, "finished": bool(finished), "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    INDEX.write_text(json.dumps(idx, ensure_ascii=False, indent=0), "utf-8")
    print(json.dumps(idx["summary"]))


if __name__ == "__main__":
    main()
