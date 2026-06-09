import sqlite3
import pandas as pd

conn = sqlite3.connect("data/railway.db")

query_1 = """
SELECT COUNT(*) AS n_schedules
FROM schedules;
"""
print("Number of schedules:")
print(pd.read_sql_query(query_1, conn))
print()

query_2 = """
SELECT COUNT(*) AS n_schedule_locations
FROM schedule_locations;
"""
print("Number of schedule locations:")
print(pd.read_sql_query(query_2, conn))
print()

query_3 = """
SELECT *
FROM schedules
LIMIT 5;
"""
print("First 5 schedule rows:")
print(pd.read_sql_query(query_3, conn))
print()

conn.close()