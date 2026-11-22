import json, datetime, pathlib
pathlib.Path("data").mkdir(exist_ok=True)
json.dump({"ran": datetime.datetime.utcnow().isoformat()+"Z", "sources": {}}, open("data/status.json","w"), indent=1)
