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
