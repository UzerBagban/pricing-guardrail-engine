from src.ingestion import db
with db.get_cursor(commit=False) as cur:
    cur.execute("SELECT id, status, suppression_reason FROM suppression_queue")
    print("rows:", cur.fetchall())
