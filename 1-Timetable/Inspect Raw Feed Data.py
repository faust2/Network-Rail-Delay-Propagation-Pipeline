import gzip
import json

path = "data/raw/schedule_full.json.gz"

with gzip.open(path, "rt", encoding="utf-8") as f:
    for i, line in enumerate(f):
        record = json.loads(line)
        print(record)
        if i == 5:
            break