"""Writes digest entries through the Claude API for items assessed as relevant.

For each relevant item (data/assessments.json, current version, dated from the scope start) that
has a stored official text and no entry yet, one call to the writing model returns an entry in
English, Dutch and French. The entry is validated and appended to data/digest.json.
    python pipeline/write_entries.py     (needs ANTHROPIC_API_KEY)
"""
import datetime as dt, json, pathlib, re, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import llm, assess

DATA = assess.DATA
STATUSES = ["In force", "Adopted", "Proposal", "Draft decree", "Preliminary draft", "Ruling", "Pending"]
PER_RUN = 20

SYSTEM = """You write entries for the Sertius Legal Update, a digest of legal changes for clients of a Belgian environmental consultancy. You receive one official publication: its metadata and its official text (long texts are shortened). Write ONE entry based ONLY on that text.

Return a JSON object only, with these fields:
"relevant": true, or false if on reading the text it does not change or clarify obligations on environmental matters for companies, project developers or authorities in Belgium (then also give "why" in at most 20 words and nothing else)
"h": English headline, at most 110 characters, saying what changed
"a": English short summary, 1-2 sentences
"b": English long summary, 3-6 sentences: who is affected and what falls in or out of scope first, then what changes, then dates (entry into force, deadlines, transitional rules) where the text gives them
"nl": [headline, short, long] in Dutch, faithful to the English, with the established Belgian legal terms
"fr": [headline, short, long] in French, faithful to the English, with the established Belgian legal terms (for Flemish instruments use the usual French names, e.g. richtwaarden = valeurs guides)
"s": status, one of: In force, Adopted, Proposal, Draft decree, Preliminary draft, Ruling, Pending
"scope": one line naming the instrument and the provisions concerned, in the language of the source
"dates": {"adopted": "YYYY-MM-DD" or "", "published": "YYYY-MM-DD" or "", "in_force": "YYYY-MM-DD" or ""} taken from the text; leave empty what the text does not state

Rules: plain and factual; no advice and no speculation; your own wording, no long quotations; never name private persons; do not mention the text, the excerpt or what was left out of it; no generic closing sentence; if the text does not state something the reader would expect (such as the entry into force), say that the act does not state it only when it matters."""


def text_name(item_id):
    return re.sub(r"[^A-Za-z0-9]+", "_", item_id) + ".txt"


def make_entry(item, a, out, today):
    eid = "d-" + re.sub(r"[^a-z0-9]+", "-", item["id"].lower()).strip("-")
    for k in ("h", "a", "b"):
        if not isinstance(out.get(k), str) or len(out[k]) < 20:
            raise ValueError("missing " + k)
    for lang in ("nl", "fr"):
        v = out.get(lang)
        if not (isinstance(v, list) and len(v) == 3 and all(isinstance(x, str) and len(x) > 15 for x in v)):
            raise ValueError("missing " + lang)
    status = out.get("s") if out.get("s") in STATUSES else ("Ruling" if item.get("t") == "case" else "Adopted")
    dates = {k: v for k, v in (out.get("dates") or {}).items() if k in ("adopted", "published", "in_force") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(v))}
    title = item.get("title", {})
    label = (title.get("en") or title.get("nl") or title.get("fr") or item["id"])[:140]
    level = item.get("level") if item.get("level") in ("EU", "BE", "VL", "BRU", "WAL") else "BE"
    return {"id": eid, "d": item["d"], "j": [level], "t": item.get("t", "law") if item.get("t") in ("law", "prop", "case", "guide") else "law",
            "s": status, "k": a.get("topics") or ["environmental-law"], "h": out["h"][:160], "a": out["a"], "b": out["b"],
            "nl": out["nl"], "fr": out["fr"], "scope": str(out.get("scope", ""))[:300], "dates": dates,
            "u": [[label, item["url"]]], "inbox": [item["id"]], "by": llm.MODELS["writer"], "written": today}


def run(inbox, log, digest, client, today, per_run=PER_RUN):
    used = {x for e in digest["items"] for x in e.get("inbox", [])}
    ids = {e["id"] for e in digest["items"]}
    todo = [i for i in inbox if i["d"] >= assess.SCOPE_START and i["id"] not in used
            and log["items"].get(i["id"], {}).get("v") == assess.VERSION and log["items"][i["id"]]["verdict"] == "relevant"
            and not log["items"][i["id"]].get("written") and (DATA / "text" / text_name(i["id"])).exists()]
    todo.sort(key=lambda i: i["d"], reverse=True)
    made = dropped = failed = 0
    for it in todo[:per_run]:
        a = log["items"][it["id"]]
        text = (DATA / "text" / text_name(it["id"])).read_text("utf-8")[:60000]
        meta = json.dumps({k: it.get(k) for k in ("id", "src", "level", "t", "d", "title", "url", "ref")}, ensure_ascii=False)
        try:
            out = llm.json_block(client.ask(llm.MODELS["writer"], SYSTEM, "METADATA\n%s\n\nOFFICIAL TEXT\n%s" % (meta, text), max_tokens=5000))
        except llm.CapReached:
            break
        except Exception as e:
            a["write_error"] = str(e)[:160]
            failed += 1
            continue
        if out.get("relevant") is False:
            a["written"] = "no"
            a["second_look"] = str(out.get("why", ""))[:200]     # the writer read the text and found it not relevant
            dropped += 1
            continue
        try:
            entry = make_entry(it, a, out, today)
        except ValueError as e:
            a["write_error"] = str(e)
            failed += 1
            continue
        if entry["id"] in ids:
            entry["id"] += "-2"
        digest["items"].append(entry)
        ids.add(entry["id"])
        a["written"] = entry["id"]
        made += 1
    waiting = sum(1 for i in inbox if i["d"] >= assess.SCOPE_START and i["id"] not in used
                  and log["items"].get(i["id"], {}).get("verdict") == "relevant" and not log["items"][i["id"]].get("written"))
    return made, dropped, failed, waiting


def main():
    inbox = assess.load("inbox.json", [])
    inbox = inbox if isinstance(inbox, list) else inbox.get("items", [])
    log = assess.load("assessments.json", {"version": assess.VERSION, "items": {}})
    digest = assess.load("digest.json", {"items": []})
    client = llm.Client(cap_usd=6.0)
    if not client.available():
        print(json.dumps({"skipped": "no API key"}))
        return
    today = dt.date.today().isoformat()
    made, dropped, failed, waiting = run(inbox, log, digest, client, today)
    digest["updated"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    log["writer"] = {"this_run": made, "found_not_relevant_on_reading": dropped, "failed": failed, "waiting": waiting,
                     "api": client.report(), "updated": today}
    (DATA / "digest.json").write_text(json.dumps(digest, ensure_ascii=False, indent=1), "utf-8")
    (DATA / "assessments.json").write_text(json.dumps(log, ensure_ascii=False, indent=0), "utf-8")
    print(json.dumps(log["writer"]))


if __name__ == "__main__":
    main()
