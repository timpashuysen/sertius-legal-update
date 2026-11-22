"""Add an editorial source proposed through a GitHub issue to data/sources.json.

Run by .github/workflows/add-source.yml. The issue title starts with "Add source:" and the body
holds lines "Name: ...", "URL: ...", "Note: ...". Values arrive in environment variables.
Prints "added", "duplicate" or "invalid: <why>".
"""
import datetime as dt, json, os, pathlib, re, urllib.parse

FILE = pathlib.Path(__file__).resolve().parent.parent / "data" / "sources.json"


def field(body, name):
    m = re.search(r"(?im)^\s*%s\s*:\s*(.+?)\s*$" % name, body)
    return m.group(1).strip() if m else ""


def main():
    body = os.environ.get("ISSUE_BODY", "")
    title = os.environ.get("ISSUE_TITLE", "")
    url = field(body, "URL")
    name = field(body, "Name") or re.sub(r"(?i)^add source:\s*", "", title).strip()
    note = field(body, "Note")[:200]
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https" or "." not in u.netloc or re.search(r"[\s<>\"']", url) or len(url) > 300:
        print("invalid: the URL must be a plain https address")
        return
    name = re.sub(r"[<>\"]", "", name)[:80] or u.netloc
    data = json.loads(FILE.read_text("utf-8")) if FILE.exists() else {"sources": []}
    host = u.netloc.lower().removeprefix("www.")
    for s in data["sources"]:
        if urllib.parse.urlsplit(s["url"]).netloc.lower().removeprefix("www.") == host and urllib.parse.urlsplit(s["url"]).path.rstrip("/") == u.path.rstrip("/"):
            print("duplicate")
            return
    today = dt.date.today().isoformat()
    data["sources"].append({"name": name, "url": url, "added": today, "by": "Legal", "note": re.sub(r"[<>]", "", note)})
    data["updated"] = today
    FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    print("added")


if __name__ == "__main__":
    main()
