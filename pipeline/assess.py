"""Qualification of collected publications: one logged verdict per item, the same way for all.

Scope: items dated from SCOPE_START. Two kinds of verdict, both written to data/assessments.json:
  - "excluded": by a rule on the kind of document (appointments, honours, staff, withdrawals,
    duplicates of a regional act). The rule is named. No AI.
  - "relevant" / "not relevant": by the model, on the title, authority, type and, for rulings,
    the subject line, with topics, a one-line reason and a confidence.
Every verdict carries VERSION. Raising VERSION re-assesses everything, so the published set never
mixes two ways of qualifying.
    python pipeline/assess.py            (needs ANTHROPIC_API_KEY; rule exclusions run without it)
"""
import datetime as dt, json, pathlib, re, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import llm

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SCOPE_START = "2026-01-01"
VERSION = 3
BATCH = 20
TOPICS = ["soil", "environmental-law", "environmental-safety", "permitting", "energy-climate", "circular-products"]

RULES = [
    ("staff and appointments", re.compile(r"\b(benoeming|aanstelling|ontslag|oprustestelling|in ruste gesteld|pensionering|bevordering|"
     r"personeel|vacature|vergelijkende selectie|nomination|désignation|démission|mise à la retraite|promotion|personnel|emploi vacant|mobiliteit)\b", re.I)),
    ("honours", re.compile(r"\b(eretekens?|nationale orden|ordres nationaux|décoration|onderscheiding)\b", re.I)),
    ("courts and notaries administration", re.compile(r"\b(rechterlijke orde|ordre judiciaire|notariaat|notariat|gerechtsdeurwaarder|huissier de justice)\b", re.I)),
    ("collective labour agreement", re.compile(r"collectieve arbeidsovereenkomst|convention collective de travail", re.I)),
    ("translation or erratum only", re.compile(r"\b(duitse vertaling|traduction allemande|officieuze coördinatie in het duits)\b", re.I)),
]

SYSTEM = """You screen official publications for Sertius, a Belgian environmental consultancy. For each numbered publication decide whether it is RELEVANT to environmental compliance in Belgium.

RELEVANT: it creates, changes, repeals, interprets or announces rules that companies, project developers or authorities in Belgium (EU, federal, Flemish, Brussels or Walloon level) must take into account in one of these topics:
- soil: soil and groundwater quality, remediation, excavated earth, PFAS in soil
- environmental-law: environmental and planning permits, spatial planning, enforcement, access to justice, environmental procedure
- environmental-safety: Seveso and major accidents, dangerous substances, chemicals (REACH, CLP), industrial emissions, external safety
- permitting: environmental impact assessment, water, air, noise, nature and biodiversity, nitrogen, waste, best available techniques, manure
- energy-climate: energy efficiency and renewable energy obligations, emissions trading, climate rules for installations and buildings
- circular-products: extended producer responsibility, packaging, batteries, ecodesign, product and material rules with environmental purpose, critical raw materials
Court rulings are relevant when they annul, suspend or interpret such rules, or decide a point of law or procedure on them that matters beyond the case (for rulings an opening extract of the text is given after "LEAD:"). A ruling that only settles the facts of one permit file, or that concerns another field of law, is not relevant. Corrigenda to relevant acts are relevant.

NOT RELEVANT: everything else, for example tax, social security, justice, health care, education, defence, road traffic and vehicle rules, transport safety, animal health, food and feed rules, agriculture market measures, fisheries quotas, trade and anti-dumping, customs, financial services, individual subsidies, individual product or active-substance authorisations, and decisions about one named person, company, plot or site (such as the designation or change of one specific soil site, or one individual permit) without general interest.

Answer with a JSON array only, one object per publication, in the same order:
{"n": <number>, "relevant": true|false, "topics": [zero or more topic ids from the list], "reason": "<at most 15 words, plain>", "confidence": "high"|"medium"|"low"}
Use "low" confidence when the title is too vague to tell; then set relevant to true so that a person or a later step looks at it."""


def load(name, default):
    p = DATA / name
    return json.loads(p.read_text("utf-8")) if p.exists() else default


def rule_exclusion(item):
    title = " ".join(item.get("title", {}).values())
    if item.get("rule"):                       # set by the collector, e.g. a regional act also in the regional database
        return item["rule"]
    if item["src"] in ("staatsblad",):
        for name, rx in RULES:
            if rx.search(title):
                return name
    return ""


RULING_SYSTEM = """You screen one court ruling (Belgian Council of State, or the Flemish Raad voor Vergunningsbetwistingen or Handhavingscollege) for Sertius, a Belgian environmental consultancy. Long rulings are shortened in the middle.

RELEVANT only if both hold:
1. it concerns environmental, planning or permit law (permits, spatial plans, environmental impact assessment, nature, water, soil, waste, noise, air, emissions, energy installations, environmental enforcement); and
2. it annuls or suspends a regulation or plan, or it decides a point of law or procedure that permit applicants, operators or authorities elsewhere should know (how a rule is to be read or applied, what an assessment or a statement of reasons must contain, who may appeal and when).
NOT relevant: other fields (aliens, civil service, public procurement, education, tax, local government staff), withdrawals and purely procedural endings, and rulings that only weigh the facts of one file without a statement of wider use.

Most rulings are NOT relevant under this test: a dispute about one building, one permit or one neighbour is not relevant unless the ruling states a rule of wider use.

Do not explain or think aloud. Your whole reply is one JSON object, starting with {"relevant":
{"relevant": true|false, "topics": [any of "soil","environmental-law","environmental-safety","permitting","energy-climate","circular-products"], "reason": "<at most 25 words: the point of law, or why not relevant>", "confidence": "high"|"medium"|"low"}"""


def text_path(item):
    return DATA / "text" / (re.sub(r"[^A-Za-z0-9]+", "_", item["id"]) + ".txt")


def lead(item):
    """For rulings: the part of the stored text that says what the case is about (their titles are only numbers)."""
    if item.get("t") != "case" or item["src"] == "eurlex":
        return ""
    p = DATA / "text" / (re.sub(r"[^A-Za-z0-9]+", "_", item["id"]) + ".txt")
    if not p.exists():
        return ""
    flat = re.sub(r"\s+", " ", p.read_text("utf-8")[:9000])
    m = re.search(r"Voorwerp van (?:het beroep|de vordering|het verzoekschrift)|Objet d[eu] (?:la requête|recours|la demande)|Onderwerp van de (?:beroepen|vordering)", flat, re.I)
    start = m.end() if m else min(len(flat), 700)
    return flat[start:start + 650].strip(" .:-")


def describe(n, item):
    t = item.get("title", {})
    title = t.get("en") or t.get("nl") or t.get("fr") or ""
    extra = "; ".join(x for x in (item.get("auth", ""), item.get("lab", "")[:160]) if x)
    ld = lead(item)
    return "%d. [%s, %s, %s] %s%s%s" % (n, item["src"], item.get("level", ""), item.get("t", ""), title[:420],
                                        (" (" + extra + ")") if extra else "", (" LEAD: " + ld) if ld else "")


def parse_rows(reply):
    """The verdict objects in a reply; if the array is cut off, the complete objects before the cut."""
    try:
        return [r for r in llm.json_block(reply) if isinstance(r, dict) and "n" in r]
    except ValueError:
        rows = []
        for m in re.finditer(r"\{[^{}]*\}", reply):
            try:
                r = json.loads(m.group(0))
            except ValueError:
                continue
            if isinstance(r, dict) and "n" in r:
                rows.append(r)
        if not rows:
            raise
        return rows


def assess(items, log, client, today, limit=1000, rulings=260):
    done = 0
    log.pop("last_error", None)
    todo = [i for i in items if i["d"] >= SCOPE_START and log["items"].get(i["id"], {}).get("v") != VERSION]
    rest = []
    for it in todo:
        rule = rule_exclusion(it)
        if rule:
            log["items"][it["id"]] = {"v": VERSION, "verdict": "excluded", "by": "rule", "reason": rule, "at": today}
            done += 1
        else:
            rest.append(it)
    if not client or not client.available():
        return done, len(rest)
    rest.sort(key=lambda i: i["d"], reverse=True)
    # rulings of the Council of State and the permit tribunal are read in full, one by one
    full = [i for i in rest if i["src"] in ("rvvb", "rvs") and text_path(i).exists()]
    rest = [i for i in rest if not (i["src"] in ("rvvb", "rvs") and text_path(i).exists())]
    bad = 0
    for it in full[:rulings]:
        try:
            reply = client.ask(llm.MODELS["small"], RULING_SYSTEM,
                               "RULING\n" + text_path(it).read_text("utf-8")[:34000] + "\n\nNow give the JSON object and nothing else.", max_tokens=1500)
            r = llm.json_block(reply[reply.rfind('{"relevant"'):] if '{"relevant"' in reply else reply)
            bad = 0
        except llm.CapReached:
            break
        except Exception as e:
            log["last_error"] = str(e)[:300] + " | reply: " + (reply[:300] if "reply" in dir() else "")
            bad += 1
            if bad >= 5:          # something is wrong with the replies: stop spending and leave the rest for a look
                break
            continue
        log["items"][it["id"]] = {"v": VERSION, "verdict": "relevant" if r.get("relevant") else "not relevant", "by": llm.MODELS["small"],
                                  "basis": "full text", "topics": [t for t in r.get("topics", []) if t in TOPICS],
                                  "reason": str(r.get("reason", ""))[:200],
                                  "confidence": r.get("confidence") if r.get("confidence") in ("high", "medium", "low") else "medium", "at": today}
        done += 1
    for a in range(0, min(len(rest), limit), BATCH):
        batch = rest[a:a + BATCH]
        try:
            reply = client.ask(llm.MODELS["small"], SYSTEM, "\n".join(describe(n + 1, it) for n, it in enumerate(batch)), max_tokens=6000)
            rows = {int(r["n"]): r for r in parse_rows(reply)}
        except llm.CapReached:
            break
        except Exception as e:
            log["last_error"] = str(e)[:900]
            break
        for n, it in enumerate(batch):
            r = rows.get(n + 1)
            if not r:
                continue
            topics = [t for t in r.get("topics", []) if t in TOPICS]
            log["items"][it["id"]] = {"v": VERSION, "verdict": "relevant" if r.get("relevant") else "not relevant", "by": llm.MODELS["small"],
                                      "topics": topics, "reason": str(r.get("reason", ""))[:160],
                                      "confidence": r.get("confidence", "medium") if r.get("confidence") in ("high", "medium", "low") else "medium", "at": today}
            done += 1
    left = sum(1 for i in rest + full if log["items"].get(i["id"], {}).get("v") != VERSION)
    return done, left


def main():
    inbox = load("inbox.json", [])
    inbox = inbox if isinstance(inbox, list) else inbox.get("items", [])
    log = load("assessments.json", {"version": VERSION, "items": {}})
    log["version"] = VERSION
    client = llm.Client(cap_usd=3.0)
    today = dt.date.today().isoformat()
    done, left = assess(inbox, log, client, today)
    scope = [i for i in inbox if i["d"] >= SCOPE_START]
    cur = [log["items"][i["id"]] for i in scope if log["items"].get(i["id"], {}).get("v") == VERSION]
    log["summary"] = {"scope_from": SCOPE_START, "in_scope": len(scope), "assessed": len(cur), "waiting": left,
                      "relevant": sum(1 for a in cur if a["verdict"] == "relevant"),
                      "not_relevant": sum(1 for a in cur if a["verdict"] == "not relevant"),
                      "excluded_by_rule": sum(1 for a in cur if a["verdict"] == "excluded"),
                      "this_run": done, "api": client.report(), "key_present": bool(client.key), "updated": today}
    (DATA / "assessments.json").write_text(json.dumps(log, ensure_ascii=False, indent=0), "utf-8")
    # what the site needs: the ids found relevant, and whether the whole scope has a verdict
    by_model = sum(1 for a in cur if a["by"] != "rule")
    (DATA / "qualified.json").write_text(json.dumps({
        "version": VERSION, "scope_from": SCOPE_START, "active": bool(by_model and left == 0), "summary": log["summary"],
        "relevant": sorted(i["id"] for i in scope if log["items"].get(i["id"], {}).get("verdict") == "relevant"
                           and log["items"][i["id"]].get("v") == VERSION)}, ensure_ascii=False), "utf-8")
    print(json.dumps(log["summary"]))


if __name__ == "__main__":
    main()
