"""Probe candidate official endpoints and record what answers. Temporary first step."""
import json, datetime, pathlib, urllib.request, urllib.parse, ssl
UA = "SertiusLegalUpdate-collector/0.1 (+https://github.com/timpashuysen/sertius-legal-update)"
def get(url, method="GET", n=1500, headers=None):
    req = urllib.request.Request(url, method=method, headers={"User-Agent": UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            body = r.read(200000) if method == "GET" else b""
            return {"status": r.status, "type": r.headers.get("Content-Type"), "len": len(body), "head": body[:n].decode("utf-8", "replace")}
    except Exception as e:
        return {"error": repr(e)[:300]}
SPARQL = """PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
SELECT DISTINCT ?celex ?date ?title ?label WHERE {
 ?w cdm:resource_legal_id_celex ?celex ; cdm:work_date_document ?date ; cdm:work_is_about_concept_eurovoc ?c .
 ?c skos:prefLabel ?label . FILTER(lang(?label)="en")
 FILTER(?date >= "2026-10-01"^^<http://www.w3.org/2001/XMLSchema#date>)
 FILTER(regex(?label, "soil|pollut|environmental|waste|water|emission|chemical|biodiversity|nature", "i"))
 ?e cdm:expression_belongs_to_work ?w ; cdm:expression_uses_language <http://publications.europa.eu/resource/authority/language/ENG> ; cdm:expression_title ?title .
} ORDER BY DESC(?date) LIMIT 20"""
P = {
 "cellar_sparql": ("https://publications.europa.eu/webapi/rdf/sparql?" + urllib.parse.urlencode({"query": SPARQL, "format": "application/sparql-results+json"}), "GET", 3000),
 "constcourt_pdf_092": ("https://nl.const-court.be/public/n/2026/2026-092n.pdf", "HEAD", 0),
 "constcourt_pdf_140": ("https://nl.const-court.be/public/n/2026/2026-140n.pdf", "HEAD", 0),
 "constcourt_pdf_190": ("https://nl.const-court.be/public/n/2026/2026-190n.pdf", "HEAD", 0),
 "constcourt_robots": ("https://nl.const-court.be/robots.txt", "GET", 600),
 "codex_api": ("https://codex.opendata.api.vlaanderen.be/api/WetgevingDocument?take=3", "GET", 2500),
 "codex_docs": ("https://codex.opendata.api.vlaanderen.be/docs/", "GET", 1200),
 "themis_sparql": ("https://themis.vlaanderen.be/sparql?" + urllib.parse.urlencode({"query": "SELECT DISTINCT ?t WHERE { ?s a ?t } LIMIT 40", "format": "application/sparql-results+json"}), "GET", 3000),
 "vlpar_api": ("https://ws.vlpar.be/e/opendata/api", "GET", 1500),
 "wallex_robots": ("https://wallex.wallonie.be/robots.txt", "GET", 800),
 "wallex_sitemap": ("https://wallex.wallonie.be/sitemap.xml", "GET", 1500),
 "wallex_home": ("https://wallex.wallonie.be/home.html", "GET", 2500),
 "ejustice_robots": ("https://www.ejustice.just.fgov.be/robots.txt", "GET", 800),
 "dbrc_robots": ("https://www.dbrc.be/robots.txt", "GET", 600),
 "dbrc_rvvb": ("https://www.dbrc.be/raad-voor-vergunningsbetwistingen", "GET", 1500),
 "rvs_robots": ("http://www.raadvst-consetat.be/robots.txt", "GET", 600),
 "consilium_rss": ("https://www.consilium.europa.eu/en/rss/pressreleases.ashx", "GET", 1500),
 "ec_env_rss": ("https://environment.ec.europa.eu/node/3/rss_en", "GET", 1200),
 "bxl_env_newregs": ("https://environnement.brussels/pro/reglementation-et-inspection/obligations-et-autorisations/decouvrez-les-nouvelles-reglementations-en-rapport-avec-lenvironnement-lurbanisme-et-lenergie", "GET", 800),
 "bxl_env_robots": ("https://environnement.brussels/robots.txt", "GET", 600),
 "vl_decisions": ("https://www.vlaanderen.be/vlaamse-regering/beslissingen-van-de-vlaamse-regering", "GET", 800),
 "vl_robots": ("https://www.vlaanderen.be/robots.txt", "GET", 800),
 "ovam_rss": ("https://ovam.vlaanderen.be/rss", "GET", 600),
 "curia_rss": ("https://curia.europa.eu/jcms/jcms/Jo2_7052/en/", "GET", 600),
}
out = {k: get(u, m, n) for k, (u, m, n) in P.items()}
pathlib.Path("data").mkdir(exist_ok=True)
json.dump({"ran": datetime.datetime.now(datetime.timezone.utc).isoformat(), "probe": out}, open("data/probe.json", "w"), indent=1, ensure_ascii=False)
