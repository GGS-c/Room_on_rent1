import sqlite3

# Connect to existing database
con = sqlite3.connect("app.db")
cur = con.cursor()

# Try to add the column if not already present
try:
    cur.execute("ALTER TABLE rooms ADD COLUMN availability TEXT DEFAULT 'Available'")
    con.commit()
    print("✅ Column 'availability' added successfully!")
except sqlite3.OperationalError as e:
    print("⚠️ Column might already exist or another issue:", e)

con.close()
