# Digest stage

The collector (`collect.py`, GitHub Actions, no AI) writes everything it finds to `data/inbox.json`.
The digest stage (a scheduled Claude task) reads the inbox, picks what matters for Sertius, reads the
official text, writes summaries in English, Dutch and French, and appends them to `data/digest.json`.
The site loads `data/digest.json` on top of its built-in items.

## data/digest.json item format
{
 "id": "d-<source>-<reference>",            unique, lowercase letters, digits and hyphens only
 "d": "YYYY-MM-DD",                         date of the act or judgment
 "j": ["EU"|"BE"|"VL"|"BRU"|"WAL"],
 "t": "law"|"prop"|"case"|"guide",
 "s": one of: "In force","Adopted","Proposal","Draft decree","Preliminary draft","Ruling","Pending",
      "Consultation","Council position","Provisional agreement","Deadline","Policy","In Parliament",
 "k": any of "soil","environmental-law","environmental-safety","permitting",
 "h": headline, "a": short summary (1-2 sentences), "b": long summary (3-6 sentences),   English
 "nl": [h, a, b], "fr": [h, a, b],
 "u": [["Label","https://official-url"], ...]   official source first; commentary only as extra context
 "inbox": ["<inbox id>", ...]               the inbox items this entry is based on
}

## data/digest-state.json
{"processed": [inbox ids already triaged], "dbrc_seen": [RvVb PDF names already triaged], "notes": "..."}

## Sources read by the digest stage itself
GitHub's runners are refused by dbrc.be, so two sources are read by the digest task with its web tool:
- Raad voor Vergunningsbetwistingen: https://www.dbrc.be/legislation?page=854 onward lists the newest
  rulings (RVVB.<work year>.<number>.pdf, sorted by name, descending). `dbrc_seen` (names) and
  `dbrc_todo` (PDF URLs still to read) in `data/digest-state.json` track progress.
- Bruxelles Environnement: its list of new regulations is used as a check on the Brussels acts
  collected from the Belgisch Staatsblad (`bxl_seen`, `bxl_missing`).
Council of State: rulings that annul or suspend a regulation reach the inbox through their notice in
the Belgisch Staatsblad (`src` staatsblad, `t` case). The Council's own judgment folders are not read.

## Replacing the hand-built items
`data/builtin.json` lists the 49 items that were written by hand into the site before the pipeline
existed (`official` = number of official links; 0 means the item rests on commentary only). The digest
stage replaces them one by one: it finds the inbox item for the same act or ruling, writes a normal
entry from the official text, and adds `"replaces": ["<built-in id>"]` to that entry. The site hides a
built-in item as soon as an entry replaces it. A built-in item for which the inbox has no official
text goes to `builtin_nomatch` in `data/digest-state.json` with a short reason. Commentary links of
the old item may be kept after the official link in `u`.

## Reading the web sources directly (preferred over the web tool)
Since 22 November 2026 the sites www.dbrc.be and environnement.brussels are on the account's list of
allowed websites, so the digest stage can download from them with the shell. For steps 4 and 5 of
the task prompt, try this first and use the WebFetch tool only if a download fails:
- list page: `curl -sS -m 30 "https://www.dbrc.be/legislation?page=854"` and take the PDF links
  with a regular expression (`sites/default/files/YYYY-MM/RVVB.<year>.<number>.pdf`);
- ruling: download the PDF and read its text with `pdftotext` or the Python package `pypdf`
  (install it if missing). Read the whole ruling, in particular the assessment and the decision;
- Brussels list: `curl` the page and take the rows and NUMAC numbers from the HTML.
Keep about 10 seconds between requests to dbrc.be. All limits in the task prompt stay as written.

## Limits and waiting for the backfill
`pipeline/limits.json` holds the numbers for a run: `entries` (entries per run), `triage` (titles
triaged), `rvvb_read` and `rvvb_entries` (rulings read and published), `builtin` (hand-built items
handled). Tim changes that file to speed up or slow down the digest.
While `data/status.json` shows `"backlog": true` the collector is still filling the inbox and the
text files. In that state: do not add hand-built items to `builtin_nomatch` and do not add NUMACs to
`bxl_seen` or `bxl_missing` unless the matching inbox item was found; try them again in a later run.
Text files hold up to 40,000 characters of the official text. If a file still lacks the part you
need, leave the item unprocessed and note it.
