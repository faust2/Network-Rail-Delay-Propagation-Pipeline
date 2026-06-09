#1. Get valid schedules
#2. Find long ones among them
#3. Pick a cif_train_uid
#4. Plug into query_valid_timetable.py
#5. Inspect full journey


from datetime import date
import sqlite3
import pandas as pd

today = date.today().strftime("%Y-%m-%d")

conn = sqlite3.connect("data/railway.db")

query = f"""
SELECT
    s.schedule_id,
    s.cif_train_uid,
    s.atoc_code,
    COUNT(*) AS n_locations
FROM schedules s
JOIN schedule_locations l
  ON l.schedule_id = s.schedule_id
WHERE s.schedule_start_date <= '{today}'
  AND s.schedule_end_date >= '{today}'
GROUP BY s.schedule_id, s.cif_train_uid, s.atoc_code
ORDER BY n_locations DESC
LIMIT 20;
"""

df = pd.read_sql_query(query, conn)

print(f"Using date: {today}")
print(df)

conn.close()