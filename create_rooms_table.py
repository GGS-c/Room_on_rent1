import sqlite3

con = sqlite3.connect("app.db")
cur = con.cursor()

# Create rooms table
cur.execute("""
CREATE TABLE IF NOT EXISTS rooms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER,
    title TEXT NOT NULL,
    description TEXT,
    rent INTEGER,
    address TEXT,
    FOREIGN KEY(owner_id) REFERENCES users(id)
)
""")
print("✅ Rooms table created")

# Create images table linked to rooms
cur.execute("""
CREATE TABLE IF NOT EXISTS images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id INTEGER,
    filename TEXT NOT NULL,
    caption TEXT,
    approved INTEGER DEFAULT 1,
    FOREIGN KEY(room_id) REFERENCES rooms(id)
)
""")
print("✅ Images table created")

con.commit()
con.close()
print("🎉 Database setup complete!")
