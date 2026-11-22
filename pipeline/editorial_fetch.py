"""Index of the monitored editorial sites that refuse GitHub's servers.

No AI. Run where those sites can be reached (a scheduled Claude task): python3 pipeline/editorial_fetch.py
Reads the sources marked "via": "claude" in data/sources.json, keeps titles and links of articles
whose title matches a practice area, and writes data/editorial-claude.json. The collector merges
that file into data/editorial.json.
"""
import json, time
import collect

OUT = collect.DATA / "editorial-claude.json"


def main():
    sources = [s for s in collect.load("sources.json", {"sources": []})["sources"] if s.get("via") == "claude"]
    old = collect.load("editorial-claude.json", {"sources": {}, "articles": []})
    known = {a["url"]: a for a in old.get("articles", [])}
    today, report = collect.TODAY.isoformat(), {}
    for src in sources:
        r = {"checked": today, "ok": False, "listed": 0, "matched": 0}
        try:
            found, how = collect.index_source(src["url"])
            r.update(ok=True, how=how, listed=len(found), last_ok=today)
            for title, link, date in found[:80]:
                k = collect.classify(title)
                if k:
                    r["matched"] += 1
                    known.setdefault(link, {"source": src["name"], "title": title[:220], "url": link, "date": date, "seen": today, "k": k})
        except Exception as e:
            r["error"] = repr(e)[:160]
            r["last_ok"] = old.get("sources", {}).get(src["url"], {}).get("last_ok", "")
        report[src["url"]] = r
        time.sleep(5)
    arts = sorted(known.values(), key=lambda a: a["seen"], reverse=True)[:300]
    OUT.write_text(json.dumps({"updated": today, "sources": report, "articles": arts}, ensure_ascii=False, indent=0), "utf-8")
    print(json.dumps({u: {k: v for k, v in r.items() if k in ("ok", "listed", "matched", "error")} for u, r in report.items()}))


if __name__ == "__main__":
    main()
