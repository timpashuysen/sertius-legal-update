"""Nightly collector for the Sertius Legal Update.

Reads official, machine-readable sources, keeps what matches the four practice
areas, and writes data/feed.json (items), data/state.json (cursors) and
data/status.json (what happened per source). No AI is involved: titles are
taken as published and topics are assigned by keyword.

Sources that forbid automated access in robots.txt are not collected.
"""
import datetime as dt
import html.parser
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
            if state.get("text_v", 0) < 4 or (state.get("text_v", 0) < 5 and f.name.startswith("codex_")) or f.name.startswith("gwh_"):
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
    failed = set()

    def run(ids):
        nonlocal kept, probed, errs, top
        bad = 0
        with ThreadPoolExecutor(max_workers=3) as ex:
            for n, it in ex.map(wallex_one, ids):
                probed += 1
                if it == "err":
                    bad += 1
                    failed.add(n)
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
    # Backfill: data/wallex-ids.json lists act ids known to exist (found once by a full walk).
    known = load("wallex-ids.json", {"ids": []}).get("ids", [])
    todo = [n for n in sorted(known, reverse=True) if "wallex:%d" % n not in items and n not in state.get("wallex_old", [])]
    old = set(state.get("wallex_old", []))
    while todo and time.time() - t0 < budget:
        part, todo = todo[:30], todo[30:]
        if not run(part):
            break
        for n in part:  # acts dated before the start date are remembered so they are not fetched again
            if "wallex:%d" % n not in items and n not in failed:
                old.add(n)
    state["wallex_old"] = sorted(old)
    state["wallex_known_left"] = len(todo)
    lo = len(todo)
    return {"ok": ok or kept > 0, "probed": probed, "matched": kept, "errors": errs,
            "next_new_id": state.get("wallex_hi", hi), "known_ids_left": state.get("wallex_known_left")}



# --------------------------------------------------------- Belgium: Staatsblad
# Apify fallback remains disabled; this adapter uses Etaamb's daily indexes.
ETAAMB = "https://etaamb.openjustice.be"
SB_KEEP = re.compile(
    r"\b(wet|loi|decreet|décret|ordonnantie|ordonnance|koninklijk besluit|"
    r"arrêté royal|ministerieel besluit|arrêté ministériel|besluit van de regering|"
    r"besluit van de [a-zé -]{0,40}regering|besluit van het verenigd college|arrêté du collège réuni|"
    r"arrêté du gouvernement|circulaire|omzendbrief|avis|bericht|mededeling|notice)\b", re.I)
SB_DROP = re.compile(
    r"\b(benoeming|benoemingen|aanstelling|aanstellingen|appointment|nomination|"
    r"désignation|vacature|vacatures|vacancy|vacancies|eervol|ereteken|eretekens|"
    r"honou?r|onderscheiding|decoration|rechtbank|tribunal|court notice|selectienummer|"
    r"solliciteren|faillissement|bankruptcy|ondernemingsnummer|company notice|ontslag|"
    r"oprustestelling|oprustestellingen|in ruste gesteld|pensionering|démission|mise à la retraite)\b", re.I)
# Council of State rulings that annul or suspend a regulation are published by extract in the gazette,
# as are the notices of pending annulment actions (article 3quater). Kept at every level.
SB_RVS = re.compile(r"(raad van state|conseil d'[eé]tat)\W{0,8}(vernietiging|schorsing|annulation|suspension)|"
                    r"art(ikel|icle|\.)\s*3\s*quater", re.I)
SB_TITLE_START = re.compile(
    r"\b(wet|loi|decreet|décret|ordonnantie|ordonnance|koninklijk besluit|"
    r"arrêté royal|ministerieel besluit|arrêté ministériel|besluit van de "
    r"(?:vlaamse|waalse|brusselse|regering|franse gemeenschap)|arrêté du gouvernement|"
    r"circulaire|omzendbrief|avis|bericht|mededeling|notice)\b", re.I)


SB_RULES = 3  # bump to rebuild the Staatsblad part of the inbox
SB_NOISE = re.compile(
    r"^(personeel|personnel)\b|collectieve arbeidsovereenkomst|convention collective|officieuze coördinatie in het duits|"
    r"duitse vertaling|traduction allemande|subsidie|subvention|toelage|\blening\b|\bemprunt\b|rechterlijke orde|"
    r"ordre judiciaire|nationale orden|ordres nationaux|erkenning|agrément|benoeming|ontslag|bevordering|pensioen|"
    r"inrustestelling|mise à la retraite|begroting|\bbudget|erratum|errata|machtiging|mandaat|vergelijkende selectie", re.I)
SB_AUTH = re.compile(r"leefmilieu|environnement|volksgezondheid|santé publique|werkgelegenheid|emploi, travail|"
                     r"economie|économie|binnenlandse zaken|intérieur|nucleaire|nucléaire|mobiliteit|mobilité", re.I)


class _EtaambIndexParser(html.parser.HTMLParser):
    """Extract NUMAC links and their visible text from one Etaamb day index."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self._href, self._text = [], None, []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            self._href = dict(attrs).get("href", "")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._href is not None:
            href = self._href
            label = re.sub(r"\s+", " ", " ".join(self._text)).strip()
            match = re.search(r"(?:_n|/)(\d{10})(?:\.html)?/?$", href)
            if match:
                self.links.append((match.group(1), href, label))
            self._href, self._text = None, []


def _sb_title(raw, href, numac):
    text = re.sub(r"\s+", " ", raw or "").strip()
    tail = re.split(r"\b(?:bron|source)\b", text, maxsplit=1, flags=re.I)
    candidate = tail[-1] if len(tail) > 1 else text
    # The index label places the issuing authority before the act title. Etaamb
    # lowercases the authority and starts the title with a capitalized word.
    match = re.search(r"\b[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’\-]*", candidate)
    if match:
        return candidate[match.start():].strip(" .-")[:500]
    match = SB_TITLE_START.search(candidate)
    if match:
        return candidate[match.start():].strip(" .-")[:500]
    slug = href.rsplit("/", 1)[-1]
    slug = re.sub(r"(?:_n|/)" + re.escape(numac) + r"(?:\.html)?$", "", slug, flags=re.I)
    return slug.replace("-", " ").replace("_", " ").strip()[:500] or text[:500]


def _sb_authority(raw):
    text = re.sub(r"\s+", " ", raw or "").strip()
    tail = re.split(r"\b(?:bron|source)\b", text, maxsplit=1, flags=re.I)
    candidate = tail[-1] if len(tail) > 1 else text
    title = re.search(r"\b[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’\-]*", candidate)
    return candidate[:title.start()] if title else candidate


def _sb_level(text):
    if re.search(r"franse gemeenschap|communauté française|fédération wallonie-bruxelles|federatie wallonië-brussel|"
                 r"duitstalige gemeenschap|communauté germanophone|deutschsprachige gemeinschaft", text, re.I):
        return "COM"  # French and German-speaking Communities: outside the digest's scope
    if re.search(r"brussels hoofdstad|brussels[- ]capital|région de bruxelles|"
                 r"brussels hoofdstedelijk gewest|cocof|cocom|commission communautaire française|"
                 r"gemeenschappelijke gemeenschapscommissie|vlaamse gemeenschapscommissie", text, re.I):
        return "BRU"
    if re.search(r"vlaamse overheid|vlaams(e| gewest|e gemeenschap| parlement)|"
                 r"vlaanderen|flemish (government|region|community)", text, re.I):
        return "VL"
    if re.search(r"waalse overheid|waals(e| gewest)|wallonië|wallonie|walloon (government|region)", text, re.I):
        return "WAL"
    return "BE"


def _sb_day(day, timeout=20, lang="nl"):
    url = "%s/%s/pub/%04d/%02d/%02d.html" % (ETAAMB, lang, day.year, day.month, day.day)
    try:
        _, raw = http(url, timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []
        raise
    parser = _EtaambIndexParser()
    parser.feed(raw.decode("utf-8", "replace"))
    return parser.links


def _sb_official_url(day, numac):
    """The gazette's permanent-link form: publication date plus NUMAC."""
    query = urllib.parse.urlencode({"lg": "nl", "pd": day.isoformat(), "numac": numac})
    return "https://www.ejustice.just.fgov.be/cgi/api2.pl?" + query


def _sb_item(day, numac, href, raw):
    title = _sb_title(raw, href, numac)
    searchable = (raw or "") + " " + title
    etaamb_href = href if href.startswith("http") else ETAAMB + (href if href.startswith("/") else "/" + href)
    if SB_RVS.search(title):
        level = _sb_level(_sb_authority(raw))
        return {
            "id": "sb:" + numac, "src": "staatsblad", "level": "BE" if level == "COM" else level,
            "t": "case", "d": day.isoformat(), "title": {"nl": title}, "url": _sb_official_url(day, numac),
            "ref": numac, "k": classify(title), "text_url": etaamb_href, "auth": "Raad van State", "x": 1,
        }
    if not SB_KEEP.search(searchable) or SB_DROP.search(searchable):
        return None
    kind = "guide" if re.search(r"\b(circulaire|omzendbrief|avis|bericht|mededeling|notice)\b", title, re.I) else "law"
    level = _sb_level(_sb_authority(raw))
    topics = classify(title)
    if level in ("VL", "WAL", "COM"):
        return None  # regions come from the Vlaamse Codex and Wallex; communities are out of scope
    if kind == "guide" and not topics:
        return None  # notices are only worth keeping when they touch a practice area
    auth = _sb_authority(raw).strip()[:120]
    wide = False
    if not topics:
        # No keyword hit: keep only what the digest should still look at, i.e. Brussels acts and
        # federal acts from authorities that regulate environment, safety, work or industry.
        if SB_NOISE.search(title) or not (level == "BRU" or SB_AUTH.search(auth)):
            return None
        wide = True
    key = "sb:" + numac
    return {
        "id": key, "src": "staatsblad", "level": level, "t": kind,
        "d": day.isoformat(), "title": {"nl": title}, "url": _sb_official_url(day, numac),
        "ref": numac, "k": topics, "text_url": etaamb_href, "auth": auth, "x": int(wide),
    }


def collect_staatsblad(state, items, budget=220):
    """Backfill Etaamb's daily publication indexes and refresh the last five days."""
    started = time.time()
    cursor = dt.date.fromisoformat(state.get("sb_next", START.isoformat()))
    matched = read = 0
    error = None
    done = set()  # days already read in this run

    def read_day(day, timeout=20):
        nonlocal matched, read
        done.add(day)
        rows = _sb_day(day, timeout=timeout)
        read += len(rows)
        kept = []
        for numac, href, label in rows:
            item = _sb_item(day, numac, href, label)
            if item:
                items[item["id"]] = item
                kept.append(item)
                matched += 1
        time.sleep(1)  # no more than one request per second
        if kept:  # French titles are a bonus: a failure here must not fail the day
            try:
                fr = {n: _sb_title(label, href, n) for n, href, label in _sb_day(day, timeout=timeout, lang="fr")}
                for item in kept:
                    if fr.get(item["ref"]):
                        item["title"]["fr"] = fr[item["ref"]]
            except Exception:
                pass
            time.sleep(1)

    try:
        while cursor <= TODAY and time.time() - started < budget - 80:
            if cursor.weekday() >= 5:
                cursor += dt.timedelta(days=1)
                state["sb_next"] = cursor.isoformat()
                continue
            try:
                read_day(cursor)
            except Exception as e:
                error = "backfill %s: %s" % (cursor.isoformat(), repr(e)[:250])
                break
            if cursor >= TODAY:
                break
            cursor += dt.timedelta(days=1)
            state["sb_next"] = cursor.isoformat()

        recent = max(START, TODAY - dt.timedelta(days=5))
        while recent <= TODAY:
            if recent.weekday() < 5 and recent not in done:
                try:
                    read_day(recent, timeout=15)
                except Exception as e:
                    error = error or "refresh %s: %s" % (recent.isoformat(), repr(e)[:250])
            recent += dt.timedelta(days=1)
        result = {"ok": error is None, "matched": matched, "read": read,
                  "backfilled_to": state.get("sb_next", START.isoformat())}
        if error:
            result["error"] = error
        return result
    except Exception as e:
        return {"ok": False, "matched": matched, "read": read,
                "backfilled_to": state.get("sb_next", START.isoformat()),
                "error": repr(e)[:400]}


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
    if src == "staatsblad":
        _, raw = http(it.get("text_url") or (ETAAMB + "/nl/" + it["ref"] + ".html"), timeout=40)
        return strip_html(raw)
    if src == "gwh":
        _, raw = http(it["url"], timeout=60)
        from pypdf import PdfReader
        rd = PdfReader(io.BytesIO(raw))
        # every page: the Court's reasoning and decision are in the second half
        return re.sub(r"[ \t]+", " ", "\n".join((pg.extract_text() or "") for pg in rd.pages))
    if src == "cass":
        _, raw = http(it["url"], timeout=60)
        from pypdf import PdfReader
        return re.sub(r"[ \t]+", " ", "\n".join((pg.extract_text() or "") for pg in PdfReader(io.BytesIO(raw)).pages))
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
            return _codex_text(json.loads(raw))
        except Exception:
            return strip_html(raw)
    return ""


def _codex_text(doc):
    """The article text of a Vlaamse Codex document, without the JSON envelope and its metadata."""
    out, meta = [], []

    def walk(v):
        if isinstance(v, dict):
            for k, x in v.items():
                # dates of publication and entry into force live in the metadata, not in the articles
                if isinstance(x, (str, int)) and re.search(r"datum|staatsblad|inwerking|publicat", k, re.I) and str(x).strip():
                    line = "%s: %s" % (k, str(x).strip()[:60])
                    if line not in meta and len(meta) < 12:
                        meta.append(line)
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, str):
            t = strip_html(v) if "<" in v else v.strip()
            if len(t) > 30 and " " in t and not t.startswith("http"):
                out.append(t)
    walk(doc)
    return "\n".join(meta + [""] + out) if meta else "\n".join(out)


def excerpt(txt, src="", cap=40000):
    """At most `cap` characters of an official text, keeping the part that carries the rules.

    Most acts fit whole. For longer ones: rulings keep their opening and a long closing part (the
    court's reasoning and decision are at the end); EU acts keep the title and the articles, which
    follow the recitals; other acts keep the opening part, where the articles start.
    """
    if len(txt) <= cap:
        return txt
    gap = "\n\n[... part left out ...]\n\n"
    if src == "gwh":
        return txt[:6000] + gap + txt[-(cap - 6000):]
    m = re.search(r"HA(S|VE) ADOPTED THIS (REGULATION|DIRECTIVE|DECISION)", txt)
    if m and m.start() > 2500:
        return txt[:2000] + gap + txt[m.start():m.start() + cap - 4000] + gap + txt[-2000:]
    return txt[:cap - 3000] + gap + txt[-3000:]


TEXT_V = 6  # bump to store the texts of rulings, EU acts and Codex documents again


CASS_PAGE = "https://www.cass.be/nl/Arr%C3%AAts.html"
NL_MONTHS = {m: i + 1 for i, m in enumerate(
    "januari februari maart april mei juni juli augustus september oktober november december".split())}


def parse_cass(html):
    """Entries of the Court of Cassation's own list of recent judgments: date, number, summary, PDF."""
    out = []
    for li in re.findall(r"<li\b.*?</li>", html, re.S | re.I):
        m = re.search(r'href="([^"]*?pdf/arresten-arrets/[^"]+\.pdf)"', li, re.I)
        if not m:
            continue
        text = strip_html(li)
        text = re.sub(r"[\u200b\xa0]", " ", text)
        d = re.search(r"(\d{1,2})\s+(%s)\s+(20\d\d)\s*-\s*([A-Z0-9./]+)" % "|".join(NL_MONTHS), text, re.I)
        if not d:
            continue
        summary = re.sub(r"\s+", " ", text[d.end():]).strip()
        out.append({"d": "%s-%02d-%02d" % (d.group(3), NL_MONTHS[d.group(2).lower()], int(d.group(1))),
                    "ref": d.group(4), "url": urllib.parse.urljoin(CASS_PAGE, m.group(1)), "summary": summary})
    return out


def collect_cass(state, items):
    """The decisions the Court of Cassation publishes on its own website (a selection, not all rulings)."""
    _, raw = http(CASS_PAGE, timeout=40)
    rows = parse_cass(raw.decode("utf-8", "replace"))
    n = 0
    for r in rows:
        if r["d"] < START.isoformat():
            continue
        key = "cass:" + r["ref"]
        if key not in items:
            n += 1
        title = "Hof van Cassatie, arrest %s. %s" % (r["ref"], r["summary"])
        items[key] = {"id": key, "src": "cass", "level": "BE", "t": "case", "d": r["d"], "title": {"nl": title.strip()},
                      "url": r["url"], "ref": r["ref"], "k": classify(r["summary"])}
    return {"ok": bool(rows), "listed": len(rows), "new": n}


def collect_rvvb(state, items):
    """Rulings of the Raad voor Vergunningsbetwistingen and the Handhavingscollege.

    dbrc.be refuses GitHub's servers, so the rulings are downloaded elsewhere by rvvb_fetch.py,
    which keeps data/rvvb-index.json. This only turns the kept rulings into inbox items.
    """
    idx = load("rvvb-index.json", {"rulings": {}})
    n = 0
    for name, r in idx.get("rulings", {}).items():
        if not r.get("kept") or not r.get("d"):
            continue
        ref = name[:-4]
        court = "Handhavingscollege" if ref.upper().startswith("HHC") else "Raad voor Vergunningsbetwistingen"
        title = "%s, arrest %s. %s" % (court, ref, r.get("subject", ""))
        key = "rvvb:" + ref
        if key not in items:
            n += 1
        items[key] = {"id": key, "src": "rvvb", "level": "VL", "t": "case", "d": r["d"], "title": {"nl": title.strip()},
                      "url": r["url"], "ref": ref, "k": classify(title), "x": 1}
    return {"ok": True, "new": n, "kept": sum(1 for r in idx.get("rulings", {}).values() if r.get("kept")),
            "summary": idx.get("summary", {})}


def _feed_items(raw):
    """(title, link, date) from an RSS or Atom feed."""
    out = []
    root = ET.fromstring(raw)
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        title = link = date = ""
        for c in el:
            t = c.tag.split("}")[-1]
            if t == "title":
                import html as _h
                title = strip_html(_h.unescape(c.text or ""))
            elif t == "link":
                link = (c.get("href") or c.text or "").strip() or link
            elif t in ("pubDate", "published", "updated", "date") and not date:
                date = (c.text or "").strip()[:40]
        if title and link:
            out.append((title, link, date))
    return out


def index_source(url, timeout=25):
    """Recent article titles and links of one monitored site: its feed if it has one, else its page links."""
    _, raw = http(url, timeout=timeout)
    page = raw.decode("utf-8", "replace")
    if page.lstrip()[:200].lower().startswith(("<?xml", "<rss", "<feed")):
        return _feed_items(raw), "feed"
    m = re.search(r'<link[^>]+type="application/(?:rss|atom)\+xml"[^>]*>', page, re.I)
    if m:
        h = re.search(r'href="([^"]+)"', m.group(0))
        if h:
            try:
                _, fraw = http(urllib.parse.urljoin(url, h.group(1)), timeout=timeout)
                items = _feed_items(fraw)
                if items:
                    return items, "feed"
            except Exception:
                pass
    host = urllib.parse.urlsplit(url).netloc.lower().removeprefix("www.")
    out, seen = [], set()
    for href, inner in re.findall(r'<a\b[^>]*href="([^"#]+)"[^>]*>(.*?)</a>', page, re.S | re.I):
        title = strip_html(inner)
        link = urllib.parse.urljoin(url, href)
        lhost = urllib.parse.urlsplit(link).netloc.lower().removeprefix("www.")
        if lhost != host or link in seen or not (35 <= len(title) <= 220) or title.count(" ") < 4:
            continue
        seen.add(link)
        out.append((title, link, ""))
    return out, "page"


def collect_editorial(state, items):
    """Index of the monitored editorial sites in data/sources.json (law firms, legal publishers).

    Only titles and links are kept, and only of articles whose title matches a practice area. They
    are editorial context: nothing is written from them. Result in data/editorial.json.
    """
    sources = load("sources.json", {"sources": []}).get("sources", [])
    old = load("editorial.json", {"articles": [], "sources": {}})
    known = {a["url"]: a for a in old.get("articles", [])}
    report, today = {}, TODAY.isoformat()
    extra = load("editorial-claude.json", {"sources": {}, "articles": []})   # sites that refuse this server
    for src in sources:
        if src.get("via") == "claude":
            if src["url"] in extra.get("sources", {}):
                report[src["url"]] = extra["sources"][src["url"]]
            continue
        r = {"checked": today, "ok": False, "listed": 0, "matched": 0}
        try:
            found, how = index_source(src["url"])
            r.update(ok=True, how=how, listed=len(found))
            for title, link, date in found[:80]:
                k = classify(title)
                if not k:
                    continue
                r["matched"] += 1
                if link not in known:
                    known[link] = {"source": src["name"], "title": title[:220], "url": link, "date": date, "seen": today, "k": k}
        except Exception as e:
            r["error"] = repr(e)[:160]
            r["last_ok"] = old.get("sources", {}).get(src["url"], {}).get("last_ok", "")
        if r["ok"]:
            r["last_ok"] = today
        report[src["url"]] = r
        time.sleep(1)
    for art in extra.get("articles", []):
        known.setdefault(art["url"], art)
    arts = sorted(known.values(), key=lambda a: a["seen"], reverse=True)[:600]
    (DATA / "editorial.json").write_text(json.dumps({"updated": today, "sources": report, "articles": arts}, ensure_ascii=False, indent=0), "utf-8")
    return {"ok": True, "sites": len(sources), "reachable": sum(1 for r in report.values() if r["ok"]),
            "articles": len(arts), "new": sum(1 for a in arts if a["seen"] == today and a["url"] not in {x["url"] for x in old.get("articles", [])})}


def save_texts(items, state, per_source=40, budget=240):
    tdir = DATA / "text"
    tdir.mkdir(exist_ok=True)
    if state.get("text_v") != TEXT_V:
        for f in tdir.glob("*.txt"):
            if True:
                f.unlink()
        state["text_v"] = TEXT_V
    t0 = time.time()
    res = {}
    gave_up = set(state.get("text_failed", []))
    todo = sorted((i for i in items.values() if (i.get("k") or i.get("x")) and i["src"] in ("gwh", "wallex", "eurlex", "codex", "staatsblad", "cass")),
                  key=lambda i: i["d"], reverse=True)
    for it in todo:
        r = res.setdefault(it["src"], {"saved": 0, "failed": 0})
        f = tdir / text_name(it["id"])
        if it["id"] in gave_up or f.exists() or r["saved"] + r["failed"] >= per_source or r["failed"] >= 12 or time.time() - t0 > budget:
            continue
        try:
            txt = fetch_text(it)
            if len(txt) < 300:
                raise ValueError("text too short (%d chars)" % len(txt))
            f.write_text("SOURCE: %s\nTITLE: %s\n\n%s" % (it["url"], next(iter(it["title"].values())), excerpt(txt, it["src"])), "utf-8")
            r["saved"] += 1
        except Exception as e:
            r["failed"] += 1
            r["last_error"] = repr(e)[:200]
            if "404" in repr(e) or "too short" in repr(e):
                gave_up.add(it["id"])  # no text at this address: do not retry every run
        time.sleep(1 if it["src"] == "staatsblad" else 0.4)
    state["text_failed"] = sorted(gave_up)
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
    if state.get("sb_rules") != SB_RULES:  # Staatsblad filing rules changed: rebuild that source only
        items = {k: v for k, v in items.items() if v.get("src") != "staatsblad"}
        state.pop("sb_next", None)
        state["sb_rules"] = SB_RULES
    before = len(items)
    status = {"ran": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "sources": {}}
    for name, fn in (("eurlex", collect_eu), ("gwh", collect_gwh), ("codex", collect_codex), ("wallex", collect_wallex), ("staatsblad", collect_staatsblad), ("council", collect_council), ("rvvb", collect_rvvb), ("cass", collect_cass), ("editorial", collect_editorial)):
        t0 = time.time()
        try:
            res = fn(state, items)
        except Exception as e:  # one failing source must not stop the others
            res = {"ok": False, "error": repr(e)[:400]}
        res["seconds"] = round(time.time() - t0, 1)
        status["sources"][name] = res
    status["not_collected"] = {
        "Raad van State / Conseil d'Etat judgments": "robots.txt forbids automated access to the judgment folders",
        "Raad voor Vergunningsbetwistingen (dbrc.be)": "refuses connections from this server; read by the digest stage instead",
        "Bruxelles Environnement": "refuses connections from this server; read by the digest stage instead",
        "JUPORTAL": "robots.txt forbids automated access",
    }
    try:
        status["texts"] = save_texts(items, state)
    except Exception as e:
        status["texts"] = {"error": repr(e)[:300]}
    status["texts_on_file"] = len(list((DATA / "text").glob("*.txt"))) if (DATA / "text").exists() else 0
    out = sorted(items.values(), key=lambda i: (i["d"], i["id"]), reverse=True)
    w = status["sources"].get("wallex", {})
    g = status["sources"].get("gwh", {})
    t = status.get("texts", {}) if isinstance(status.get("texts"), dict) else {}
    sb = status["sources"].get("staatsblad", {})
    sb_next = sb.get("backfilled_to")
    sb_backlog = bool(sb.get("ok") and sb.get("read") and sb_next and sb_next < TODAY.isoformat())
    status["backlog"] = bool(sb_backlog or (w.get("known_ids_left") or 0) > 0 or (g.get("read") or 0) >= 150
                             or any(isinstance(v, dict) and v.get("saved", 0) >= 30 for v in t.values()))
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
