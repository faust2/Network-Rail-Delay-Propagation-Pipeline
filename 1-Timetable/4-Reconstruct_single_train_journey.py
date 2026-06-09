import sqlite3
import pandas as pd

conn = sqlite3.connect("data/railway.db")

query = """
SELECT
    s.cif_train_uid,
    l.location_index,
    l.tiploc_code,
    t.tps_description,
    l.arrival,
    l.departure,
    l.pass,
    l.platform
FROM schedules s
JOIN schedule_locations l
  ON s.schedule_id = l.schedule_id
LEFT JOIN tiploc t
  ON l.tiploc_code = t.tiploc_code
WHERE s.schedule_id = 1
ORDER BY l.location_index;
"""

df = pd.read_sql_query(query, conn)
print(df)

conn.close()