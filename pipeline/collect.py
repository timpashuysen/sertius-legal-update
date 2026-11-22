"""Nightly collector for the Sertius Legal Update.

Reads official, machine-readable sources, keeps what matches the four practice
areas, and writes data/feed.json (items), data/state.json (cursors) and
data/status.json (what happened per source). No AI is involved: titles are
taken as published and topics are assigned by keyword.

Sources that forbid automated access in robots.txt are not collected.
"""
import datetime as dt
import io
import json
import pathlib
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

UA = "SertiusLegalUpdate-collector/0.3 (+https://github.com/timpashuysen/sertius-legal-update)"
DATA = pathlib.Path("data")
RULES = 3
START = dt.date(2024, 1, 1)
TODAY = dt.datetime.now(dt.timezone.utc).date()

TOPICS = {
    "soil": r"\bbodem|grondverzet|grondwater|pollution des sols|assainissement des sols|gestion des sols|\bsoil|bodemsanering|pfas|brownfield|vlarebo|terres excav|bodemverontreinig|contaminated (land|site)|groundwater",
    "environmental-law": r"omgevingsvergunning|milieuvergunning|permis d.environnement|permis unique|permis d.urbanisme|environmental permit|ruimtelijke ordening|aménagement du territoire|urbanisme|stedenbouw|omgevingshandhaving|milieuhandhaving|\bvcro\b|\bcodt\b|\bcobat\b|spatial planning|ruimtelijk(e)? (uitvoeringsplan|beleidsplan|structuurplan)|développement territorial|omgevingsbesluit|access to justice in environmental",
    "environmental-safety": r"seveso|zware ongevallen|accidents majeurs|major[- ]accident|gevaarlijke stoffen|substances dangereuses|dangerous substances|hazardous substances|industrial emissions|industriële emissies|émissions industrielles|industrial hazard|regulation \(ec\) no 1907/2006|regulation \(ec\) no 1272/2008|classification, labelling and packaging|veiligheidsrapport|externe veiligheid|omgevingsveiligheid",
    "permitting": r"milieueffect|m\.e\.r\.|évaluation des incidences|environmental impact assessment|environmental assessment|waterbeleid|oppervlaktewater|afvalwater|eaux (usées|souterraines|de surface)|politique de l.eau|water (policy|pollution|protection|quality|framework)|waste ?water|luchtkwaliteit|air quality|qualité de l.air|geluidshinder|omgevingslawaai|environmental noise|natuurbehoud|natuurdecreet|natuurherstel|nature restoration|conservation de la nature|biodiversit|natural habitats|habitatrichtlijn|natura 2000|stikstof|\bazote\b|nitrogen deposition|nitrates|afvalstoffen|materialendecreet|\bvlarema\b|déchets|waste (framework|management|shipment)|landfill|vlarem|beste beschikbare technieken|best available techniques|\bbref\b|leefmilieu|milieubeleid|milieuhygiën|code de l.environnement|environmental (protection|liability|quality standards|crime|offences)|mestdecreet|mestbeleid|medium combustion",
}
TOPIC_RX = {k: re.compile(v, re.I) for k, v in TOPICS.items()}


def classify(text):
    return [k for k, rx in TOPIC_RX.items() if rx.search(text or "")]


def http(url, data=None, headers=None, timeout=60, method=None):
    req = urllib.request.Request(url, data=data, method=method, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def load(name, default):
    p = DATA / name
    if p.exists():
        try:
            return json.loads(p.read_text("utf-8"))
        except Exception:
            pass
    return default


# ---------------------------------------------------------------- EU: Cellar
CELLAR = "https://publications.europa.eu/webapi/rdf/sparql"
EUROVOC_RX = ("soil|pollution|environmental (impact|protection|policy|law|liability|monitoring|risk|standard)|waste|"
              "water (policy|pollution|protection|management)|groundwater|emission|dangerous substance|industrial hazard|"
              "major accident|chemical|biodiversity|protection of animal life|protected area|habitat|noise|air quality|"
              "industrial pollution|pollution control|environmental offence|nature restoration")
STRICT_LABEL = re.compile(r"soil (pollution|protection)|major accident|industrial hazard|industrial pollution|environmental impact|"
                          r"water pollution|groundwater|waste management|air quality|noise pollution|biodiversity|dangerous substance", re.I)
NOISE = re.compile(r"biocidal|active substance|plant protection|fishing|fisher|anti-dumping|tariff|feed additive|"
                   r"maximum residue|novel food|State aid|veterinary|cosmetic", re.I)


def cellar_query(a, b):
    lang = "http://publications.europa.eu/resource/authority/language/"
    opt = "".join(
        " OPTIONAL { ?e%s cdm:expression_belongs_to_work ?w ; cdm:expression_uses_language <%s%s> ; cdm:expression_title ?%s }" % (v, lang, c, v)
        for v, c in (("en", "ENG"), ("nl", "NLD"), ("fr", "FRA")))
    return """PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
SELECT ?celex ?date ?en ?nl ?fr (GROUP_CONCAT(DISTINCT ?label; separator="|") AS ?labels) WHERE {
 ?w cdm:resource_legal_id_celex ?celex ; cdm:work_date_document ?date ; cdm:work_is_about_concept_eurovoc ?c .
 ?c skos:prefLabel ?label . FILTER(lang(?label)="en")
 FILTER(?date >= "%s"^^xsd:date && ?date < "%s"^^xsd:date)
 FILTER(regex(?label, "%s", "i"))
 FILTER(regex(str(?celex), "^3[0-9]{4}[LRD]|^6[0-9]{4}(CJ|TJ)|^5[0-9]{4}PC"))
 %s
} GROUP BY ?celex ?date ?en ?nl ?fr LIMIT 800""" % (a, b, EUROVOC_RX, opt)


def collect_eu(state, items, months_per_run=12):
    cur = dt.date.fromisoformat(state.get("eu_next", START.isoformat()))
    n = 0
    done = 0
    windows = []
    while cur <= TODAY and done < months_per_run:
        nxt = (cur.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
        windows.append((cur, nxt))
        cur = nxt
        done += 1
    # always refresh the last 45 days so late-indexed documents are picked up
    windows.append((TODAY - dt.timedelta(days=45), TODAY + dt.timedelta(days=1)))
    for a, b in windows:
        body = urllib.parse.urlencode({"query": cellar_query(a.isoformat(), b.isoformat()),
                                       "format": "application/sparql-results+json"}).encode()
        _, raw = http(CELLAR, data=body, timeout=180)
        for r in json.loads(raw)["results"]["bindings"]:
            celex = r["celex"]["value"]
            title = {k: r[k]["value"].replace(" ", " ")[:400] for k in ("en", "nl", "fr") if k in r}
            if not title or NOISE.search(title.get("en", "")):
                continue
            labels = r.get("labels", {}).get("value", "")
            topics = classify(title.get("en") or " ".join(title.values()))
            if not topics and STRICT_LABEL.search(labels):
                topics = classify(labels) or ["permitting"]
            kind = "case" if celex[0] == "6" else "prop" if celex[0] == "5" else "law"
            items["eu:" + celex] = {"id": "eu:" + celex, "src": "eurlex", "level": "EU", "t": kind, "d": r["date"]["value"][:10],
                                    "title": title, "url": "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:" + celex,
                                    "ref": celex, "k": topics, "lab": labels[:300]}
            n += 1
        time.sleep(1)
    state["eu_next"] = min(cur, TODAY.replace(day=1)).isoformat()
    return {"ok": True, "matched": n, "backfilled_to": state["eu_next"]}


# ------------------------------------------------- Belgium: Constitutional Court
NL_MONTHS = {m: i + 1 for i, m in enumerate(
    "januari februari maart april mei juni juli augustus september oktober november december".split())}


def pdf_text(raw, pages=2):
    from pypdf import PdfReader
    rd = PdfReader(io.BytesIO(raw))
    return "\n".join((rd.pages[i].extract_text() or "") for i in range(min(pages, len(rd.pages))))


def collect_gwh(state, items, cap=150):
    nxt = state.setdefault("gwh_next", {})
    fetched = kept = 0
    for year in range(START.year, TODAY.year + 1):
        n = int(nxt.get(str(year), 1))
        miss = 0
        first_missing = None
        while fetched < cap and miss < 5:
            url = "https://nl.const-court.be/public/n/%d/%d-%03dn.pdf" % (year, year, n)
            try:
                _, raw = http(url, timeout=60)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    miss += 1
                    first_missing = first_missing or n
                    n += 1
                    continue
                raise
            miss = 0
            first_missing = None
            fetched += 1
            txt = re.sub(r"[ \t]+", " ", pdf_text(raw))
            m = re.search(r"In\s*zake\s*:\s*(.*?)(?:Het Grondwettelijk Hof,|$)", txt, re.S)
            subject = re.sub(r"\s+", " ", m.group(1)).strip()[:500] if m else ""
            dm = re.search(r"van (\d{1,2}) (\w+) (%d)" % year, txt)
            date = "%d-%02d-%02d" % (year, NL_MONTHS.get(dm.group(2).lower(), 1), int(dm.group(1))) if dm else "%d-01-01" % year
            topics = classify(subject)
            if True:
                title = {"nl": "Grondwettelijk Hof, arrest nr. %d/%d: %s" % (n, year, subject)}
                try:
                    if not topics:
                        raise LookupError("French title only for filed items")
                    _, rawf = http("https://fr.const-court.be/public/f/%d/%d-%03df.pdf" % (year, year, n), timeout=60)
                    tf = re.sub(r"[ \t]+", " ", pdf_text(rawf))
                    mf = re.search(r"En\s*cause\s*:\s*(.*?)(?:La Cour constitutionnelle,|$)", tf, re.S)
                    if mf:
                        title["fr"] = "Cour constitutionnelle, arrêt n° %d/%d : %s" % (n, year, re.sub(r"\s+", " ", mf.group(1)).strip()[:500])
                except Exception:
                    pass
                key = "gwh:%d/%d" % (n, year)
                items[key] = {"id": key, "src": "gwh", "level": "BE", "t": "case", "d": date, "title": title,
                              "url": url, "ref": "%d/%d" % (n, year), "k": topics}
                kept += 1
            n += 1
            time.sleep(0.3)
        nxt[str(year)] = first_missing or n
    return {"ok": True, "read": fetched, "matched": kept, "next": dict(nxt)}


# ----------------------------------------------------- Flanders: Vlaamse Codex
CODEX = "https://codex.opendata.api.vlaanderen.be/api/WetgevingDocument"


def collect_codex(state, items, pages=20, take=200):
    _, raw = http(CODEX + "?take=1")
    total = json.loads(raw)["TotaalAantal"]
    kept = seen = 0
    oldest = None
    skip = max(0, total - take)
    for _ in range(pages):
        _, raw = http(CODEX + "?skip=%d&take=%d" % (skip, take))
        rows = json.loads(raw).get("ResultatenLijst", [])
        if not rows:
            break
        for r in rows:
            seen += 1
            d = (r.get("Datum") or "")[:10]
            if not d or d < START.isoformat():
                continue
            oldest = min(oldest or d, d)
            typ = r.get("WetgevingDocumentType") or ""
            op = (r.get("Opschrift") or "").strip()
            topics = classify(op)
            key = "codex:%s" % r["Id"]
            items[key] = {"id": key, "src": "codex", "level": "VL", "t": "law", "d": d,
                          "title": {"nl": ("%s %s" % (typ, op))[:400]},
                          "url": "https://codex.vlaanderen.be/Zoeken/Document.aspx?DID=%s&param=inhoud" % r["Id"],
                          "ref": typ, "k": topics}
            kept += 1
        if skip == 0:
            break
        skip = max(0, skip - take)
        time.sleep(0.5)
    return {"ok": True, "total_in_codex": total, "read": seen, "matched": kept, "oldest_date_seen": oldest}


# ------------------------------------------------------------ Wallonia: Wallex
# Wallex has no list of new acts, but every act has a numeric id and a small
# public "listing" snippet with its date, title and ELI link. We walk the ids:
# forwards from the highest one seen (new acts) and backwards for the backfill.
FR_MONTHS = {m: i + 1 for i, m in enumerate(
    "janvier février mars avril mai juin juillet août septembre octobre novembre décembre".split())}
WALLEX = "https://wallex.wallonie.be"
WALLEX_SEED, WALLEX_FLOOR = 141700, 100000


def wallex_one(n):
    import html as _html
    url = "%s/fr/sites/wallex/contents/acts/%d/%d/1.listing.html.ajax" % (WALLEX, n // 1000, n)
    try:
        _, raw = http(url, timeout=15)
    except urllib.error.HTTPError as e:
        return (n, None) if e.code in (403, 404) else (n, "err")
    except Exception:
        return n, "err"
    t = re.sub(r"\s+", " ", raw.decode("utf-8", "replace"))
    a = re.search(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', t)
    dm = re.search(r"<time>\s*(\d{1,2})(?:er)? (\S+) (\d{4})", t)
    if not a or not dm or dm.group(2).lower() not in FR_MONTHS:
        return n, None
    date = "%s-%02d-%02d" % (dm.group(3), FR_MONTHS[dm.group(2).lower()], int(dm.group(1)))
    title = _html.unescape(re.sub(r"<[^>]+>", "", a.group(2))).strip()[:400]
    href = a.group(1)
    return n, {"id": "wallex:%d" % n, "src": "wallex", "level": "WAL", "t": "law", "d": date, "title": {"fr": title},
               "url": href if href.startswith("http") else WALLEX + href, "ref": "Wallex %d" % n, "k": classify(title)}


def collect_wallex(state, items, budget=420, chunk=90, ahead=180):
    """Time-boxed: Wallex answers slowly, so each run does what fits in `budget` seconds."""
    from concurrent.futures import ThreadPoolExecutor
    t0 = time.time()
    hi = int(state.get("wallex_hi", WALLEX_SEED))
    lo = int(state.get("wallex_lo", WALLEX_SEED))
    kept = probed = errs = 0
    top = hi

    def run(ids):
        nonlocal kept, probed, errs, top
        bad = 0
        with ThreadPoolExecutor(max_workers=3) as ex:
            for n, it in ex.map(wallex_one, ids):
                probed += 1
                if it == "err":
                    bad += 1
                elif it:
                    top = max(top, n + 1)
                    if it["d"] >= START.isoformat():
                        items[it["id"]] = it
                        kept += 1
        errs += bad
        return bad <= len(ids) * 2 // 5

    ok = True
    for start in range(hi, hi + ahead, chunk):  # new acts first
        if time.time() - t0 > budget / 3 or not ok:
            break
        ok = run(list(range(start, start + chunk)))
    if ok:
        state["wallex_hi"] = top
    ok = True  # a bad patch among the newest ids must not block the backfill
    while ok and lo > WALLEX_FLOOR and time.time() - t0 < budget:  # then the backfill
        nxt = max(WALLEX_FLOOR, lo - chunk)
        ok = run(list(range(nxt, lo)))
        if ok:
            lo = nxt
            state["wallex_lo"] = lo
    return {"ok": ok or kept > 0, "probed": probed, "matched": kept, "errors": errs,
            "next_new_id": state.get("wallex_hi", hi), "backfilled_down_to": state.get("wallex_lo", lo)}


# ------------------------------------------------------- EU: Council press RSS
def collect_council(state, items):
    _, raw = http("https://www.consilium.europa.eu/en/rss/pressreleases.ashx")
    root = ET.fromstring(raw.lstrip(b"\xef\xbb\xbf"))
    n = 0
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        desc = re.sub(r"<[^>]+>|&nbsp;", " ", it.findtext("description") or "")
        cats = " ".join(c.text or "" for c in it.findall("category"))
        topics = classify(title + " " + desc + " " + cats)
        if not topics:
            continue
        link = (it.findtext("link") or "").strip()
        upd = it.findtext("{http://www.w3.org/2005/Atom}updated") or ""
        key = "council:" + (it.findtext("guid") or link)
        items[key] = {"id": key, "src": "council", "level": "EU", "t": "guide", "d": upd[:10] or TODAY.isoformat(),
                      "title": {"en": title[:400]}, "url": link, "ref": "Press release", "k": topics}
        n += 1
    return {"ok": True, "matched": n}


# ------------------------------------------------- official texts for the digest
# The digest stage may run without web access, so the collector saves the opening
# part of the official text of each keyword-filed item under data/text/.
def text_name(item_id):
    return re.sub(r"[^A-Za-z0-9]+", "_", item_id) + ".txt"


def strip_html(raw):
    import html as _html
    t = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    t = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", t)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", _html.unescape(t)).strip()


def fetch_text(it):
    src = it["src"]
    if src == "gwh":
        _, raw = http(it["url"], timeout=60)
        from pypdf import PdfReader
        rd = PdfReader(io.BytesIO(raw))
        n = len(rd.pages)
        pages = list(range(min(5, n))) + ([n - 1] if n > 5 else [])
        return re.sub(r"[ \t]+", " ", "\n".join((rd.pages[i].extract_text() or "") for i in pages))
    if src == "wallex":
        _, raw = http(it["url"], timeout=40)
        return strip_html(raw)
    if src == "eurlex":
        _, raw = http("https://publications.europa.eu/resource/celex/" + urllib.parse.quote(it["ref"]),
                      headers={"Accept": "text/html, application/xhtml+xml;q=0.9", "Accept-Language": "eng"}, timeout=60)
        return strip_html(raw)
    if src == "codex":
        did = it["id"].split(":")[1]
        _, raw = http(CODEX + "/" + did + "/VolledigDocument", timeout=40)
        try:
            return strip_html(json.dumps(json.loads(raw), ensure_ascii=False))
        except Exception:
            return strip_html(raw)
    return ""


def save_texts(items, per_source=40, budget=240):
    tdir = DATA / "text"
    tdir.mkdir(exist_ok=True)
    t0 = time.time()
    res = {}
    todo = sorted((i for i in items.values() if i.get("k") and i["src"] in ("gwh", "wallex", "eurlex", "codex")),
                  key=lambda i: i["d"], reverse=True)
    for it in todo:
        r = res.setdefault(it["src"], {"saved": 0, "failed": 0})
        f = tdir / text_name(it["id"])
        if f.exists() or r["saved"] + r["failed"] >= per_source or r["failed"] >= 12 or time.time() - t0 > budget:
            continue
        try:
            txt = fetch_text(it)
            if len(txt) < 300:
                raise ValueError("text too short (%d chars)" % len(txt))
            f.write_text("SOURCE: %s\nTITLE: %s\n\n%s" % (it["url"], next(iter(it["title"].values())), txt[:9000]), "utf-8")
            r["saved"] += 1
        except Exception as e:
            r["failed"] += 1
            r["last_error"] = repr(e)[:200]
        time.sleep(0.4)
    return res


# ------------------------------------------------------------- explore next
def explore():
    out = {}
    for name, url in (("wallex_sitemap_lang", "https://wallex.wallonie.be/sitemap-lang.xml"),
                      ("vlpar_swagger", "https://ws.vlpar.be/e/opendata/api/swagger.json"),
                      ("codex_swagger", "https://codex.opendata.api.vlaanderen.be/docs/swagger.json")):
        try:
            s, raw = http(url, timeout=40)
            out[name] = {"status": s, "len": len(raw), "head": raw[:1500].decode("utf-8", "replace")}
        except Exception as e:
            out[name] = {"error": repr(e)[:200]}
    return out


def main():
    DATA.mkdir(exist_ok=True)
    state = load("state.json", {})
    feed = load("inbox.json", {"items": []})
    if state.get("rules") != RULES:  # filing rules changed: rebuild from the start
        state, feed = {"rules": RULES}, {"items": []}
    items = {i["id"]: i for i in feed.get("items", [])}
    before = len(items)
    status = {"ran": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "sources": {}}
    for name, fn in (("eurlex", collect_eu), ("gwh", collect_gwh), ("codex", collect_codex), ("wallex", collect_wallex), ("council", collect_council)):
        t0 = time.time()
        try:
            res = fn(state, items)
        except Exception as e:  # one failing source must not stop the others
            res = {"ok": False, "error": repr(e)[:400]}
        res["seconds"] = round(time.time() - t0, 1)
        status["sources"][name] = res
    status["not_collected"] = {
        "Belgisch Staatsblad / Moniteur belge": "robots.txt forbids automated access to the publication pages",
        "Raad van State / Conseil d'Etat judgments": "robots.txt forbids automated access to the judgment folders",
        "Raad voor Vergunningsbetwistingen (dbrc.be)": "refuses connections from this server; read by the digest stage instead",
        "Bruxelles Environnement": "refuses connections from this server; read by the digest stage instead",
        "JUPORTAL": "robots.txt forbids automated access",
    }
    try:
        status["texts"] = save_texts(items)
    except Exception as e:
        status["texts"] = {"error": repr(e)[:300]}
    status["texts_on_file"] = len(list((DATA / "text").glob("*.txt"))) if (DATA / "text").exists() else 0
    out = sorted(items.values(), key=lambda i: (i["d"], i["id"]), reverse=True)
    status["items_total"] = len(out)
    status["items_new"] = len(out) - before
    by = {}
    for i in out:
        by[i["src"]] = by.get(i["src"], 0) + 1
    status["items_by_source"] = by
    # inbox.json: everything collected, for the digest stage. feed.json: the keyword-filed subset, for the site.
    (DATA / "inbox.json").write_text(json.dumps({"updated": status["ran"], "items": out}, ensure_ascii=False, separators=(",", ":")), "utf-8")
    filed = [{k: v for k, v in i.items() if k != "lab"} for i in out if i.get("k")]
    status["items_filed_for_site"] = len(filed)
    (DATA / "feed.json").write_text(json.dumps({"updated": status["ran"], "items": filed}, ensure_ascii=False, separators=(",", ":")), "utf-8")
    (DATA / "state.json").write_text(json.dumps(state, indent=1), "utf-8")
    (DATA / "status.json").write_text(json.dumps(status, indent=1, ensure_ascii=False), "utf-8")
    print(json.dumps({k: v for k, v in status.items() if k != "explore"}, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
