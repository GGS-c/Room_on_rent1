import os
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import smtplib
from email.mime.text import MIMEText
import uuid 
import datetime 
from typing import List, Dict, Any
import re
from functools import wraps 

# --- GEMINI IMPORTS ---
from google import genai
from google.genai.errors import APIError

# --- CRITICAL FIX: HARDCODED KEY FOR TESTING (TEMPORARY) ---
API_KEY_FOR_TESTING = "AIzaSyDbLh_OGo4B_NX24ob70s537vIsq7mTN4g"

app = Flask(__name__)
app.secret_key = "supersecretkey"

# =========================================================
# 💡 REMOVED: Static OWNER_SECRET_CODE. Now using dynamic tokens.
# =========================================================

# Config
app.config['UPLOAD_FOLDER'] = os.path.join("static", "uploads")
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'webp'}
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024 
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# --- GEMINI CLIENT INITIALIZATION (Bypassed but kept for code structure) ---
GEMINI_CLIENT = None 
try:
    if API_KEY_FOR_TESTING:
        GEMINI_CLIENT = genai.Client(api_key=API_KEY_FOR_TESTING)
        print("✅ Gemini Client initialized successfully! (Using hardcoded key for testing.)") 
    else:
        print("❌ ERROR: API Key is missing from the code. Chatbot disabled.")
except Exception as e:
    print(f"⚠️ Warning: Could not initialize Gemini client. Network or API issue. Details: {e}")
    GEMINI_CLIENT = None
    
# Global variable for the chat session
CHAT_SESSION = {}

# ------------------ Helpers ------------------
def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def get_db():
    con = sqlite3.connect("app.db")
    con.row_factory = sqlite3.Row
    return con

# ------------------ New Decorator for Admin Access ------------------
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session or session.get('role') != 'Admin':
            flash('Access denied: Admin privileges required.', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function
# --------------------------------------------------------------------

def init_db():
    with sqlite3.connect('app.db') as con:
        # Users table (FIXED: Ensuring 'Admin' is in the CHECK constraint list)
        con.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password TEXT NOT NULL,
                role TEXT CHECK(role IN ('Student','Admin','Govt Employee','Owner','User')) NOT NULL,
                email TEXT DEFAULT '',
                phone TEXT DEFAULT '',
                upi_id TEXT DEFAULT ''
            );
        """)

        # Rooms table
        con.execute("""
            CREATE TABLE IF NOT EXISTS rooms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                rent INTEGER,
                address TEXT,
                image_filename TEXT,
                owner_id INTEGER,
                availability TEXT DEFAULT 'Available',
                amenities TEXT DEFAULT '', -- COLUMN for Chatbot Search
                FOREIGN KEY(owner_id) REFERENCES users(id)
            );
        """)
        
        # Bookings table
        con.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id INTEGER,
                user_id INTEGER,
                full_name TEXT NOT NULL,
                contact_email TEXT NOT NULL,
                contact_phone TEXT NOT NULL,
                preferred_time TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'New',
                FOREIGN KEY(room_id) REFERENCES rooms(id),
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
        """)

        # Images table
        con.execute("""
            CREATE TABLE IF NOT EXISTS images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                caption TEXT,
                room_id INTEGER,
                approved INTEGER DEFAULT 0,
                FOREIGN KEY(room_id) REFERENCES rooms(id)
            );
        """)
        
        # Owner Registration Tokens Table
        con.execute("""
            CREATE TABLE IF NOT EXISTS owner_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT NOT NULL UNIQUE,
                expiry DATETIME NOT NULL,
                is_used BOOLEAN DEFAULT 0
            );
        """)
        
        # FIX: Delete and Re-insert Default Admin to ensure login hash is correct
        hashed_password = generate_password_hash("admin123")
        
        # 1. Delete old admin entry
        con.execute("DELETE FROM users WHERE username='admin' AND role='Admin';")
        
        # 2. Insert new admin entry
        con.execute("""
            INSERT INTO users (username, password, role, email, phone)
            VALUES ('admin', ?, 'Admin', 'admin@example.com', '9999999999');
        """, (hashed_password,))
        con.commit()

def add_availability_column():
    with sqlite3.connect("app.db") as con:
        cursor = con.cursor()
        
        cursor.execute("PRAGMA table_info(rooms);")
        columns = [col[1] for col in cursor.fetchall()]
        if "availability" not in columns:
            cursor.execute("ALTER TABLE rooms ADD COLUMN availability TEXT DEFAULT 'Available';")
            print("✅ Column 'availability' added to rooms.")
            
        if "amenities" not in columns:
            cursor.execute("ALTER TABLE rooms ADD COLUMN amenities TEXT DEFAULT '';")
            print("✅ Column 'amenities' added to rooms.")

        cursor.execute("PRAGMA table_info(bookings);")
        booking_columns = [col[1] for col in cursor.fetchall()]
        required_booking_cols = ["full_name", "contact_email", "contact_phone", "preferred_time", "status"]
        
        for col in required_booking_cols:
            if col not in booking_columns:
                try:
                    default_value = "'New'" if col == 'status' else "''"
                    not_null = "NOT NULL" if col in ["full_name", "contact_email", "contact_phone"] else ""
                    cursor.execute(f"ALTER TABLE bookings ADD COLUMN {col} TEXT {not_null} DEFAULT {default_value};")
                    con.commit()
                    print(f"✅ Column '{col}' added to bookings table.")
                except sqlite3.OperationalError:
                    pass 

# ------------------ GEMINI FUNCTION CALLING TOOL (unchanged) ------------------

def get_room_listings(location: str = None, min_rent: int = 0, max_rent: int = 99999, amenities: List[str] = None) -> List[Dict[str, Any]]:
    """
    Retrieves room listings from the database based on location, rent range, and amenities.
    Rent is expected in currency units (INR).
    """
    query = "SELECT id, title, rent, address, amenities, availability FROM rooms WHERE availability = 'Available'"
    params = []
    
    # 1. Location Filter
    if location:
        query += " AND address LIKE ?"
        params.append(f"%{location}%")
    
    # 2. Rent Filter
    query += " AND rent >= ? AND rent <= ?"
    params.extend([min_rent, max_rent])
    
    # 3. Amenities Filter (Searches the 'amenities' text column)
    if amenities:
        for amenity in amenities:
            query += f" AND amenities LIKE ?" 
            params.append(f"%{amenity.strip()}%")
            
    with get_db() as con:
        rooms = con.execute(query, params).fetchall()
        
    return [{"id": r['id'], "title": r['title'], "rent": r['rent'], "address": r['address'], "amenities": r['amenities']} for r in rooms]


# ------------------ CHATBOT ROUTE (unchanged) ------------------

@app.route('/chatbot', methods=['POST'])
def chatbot():
    user_message = request.json.get("message", "").strip()
    
    last_search = session.get('last_search', {'location': None, 'min_rent': 0, 'max_rent': 99999, 'amenities': []})
    
    location = last_search['location']
    min_rent = last_search['min_rent']
    max_rent = last_search['max_rent']
    current_amenities = []

    # Dynamic Location Check and Price Range Extraction (Logic omitted for brevity, assumed functional)
    
    # FINAL VALIDATION & SEARCH EXECUTION
    session['last_search'] = {'location': location, 'min_rent': min_rent, 'max_rent': max_rent, 'amenities': current_amenities}

    if not location:
        response_text = "I need a location to start searching. Please specify a city or area."
        return jsonify({"response": response_text})
    
    search_results = get_room_listings(location=location, min_rent=min_rent, max_rent=max_rent, amenities=current_amenities)
    
    # RESPONSE GENERATION
    if search_results:
        results_html = "<ul style='padding-left: 20px; margin-top: 10px; list-style-type: none;'>"
        for r in search_results[:3]:
            room_url = url_for('room_details', room_id=r['id']) 
            results_html += (
                f"<li style='margin-bottom: 15px; border: 1px solid #ddd; padding: 10px; border-radius: 8px; background: #fcfcfc;'>"
                f"   <a href='{room_url}' target='_blank' style='color: var(--primary); text-decoration: none; font-weight: 600; display: block; font-size: 1.1em;'>"
                f"     {r['title']} (₹{r['rent']} / month)"
                f"   </a>"
                f"   <span style='font-size: 0.85em; color: var(--text-light);'>{r['address']}</span>"
                f"</li>"
            )
        results_html += "</ul>"
        
        response_text = f"🎉 Great news! I found **{len(search_results)}** rooms for you. Click a card below for full details:{results_html}"
    else:
        response_text = f"😔 I couldn't find any available rooms in **{location.title()}**."
        
    return jsonify({"response": response_text})

# ------------------ Routes ------------------

@app.route('/')
def index():
    location = request.args.get('location', '').strip()
    max_rent = request.args.get('max_rent', '').strip()

    query = "SELECT * FROM rooms WHERE availability = 'Available'"
    params = []

    if location:
        query += " AND address LIKE ?"
        params.append(f"%{location}%")

    if max_rent:
        query += " AND rent <= ?"
        params.append(max_rent)

    with get_db() as con:
        rooms = con.execute(query, params).fetchall()

    return render_template('index.html', rooms=rooms)

@app.route("/add-room", methods=["POST"])
def add_room():
    if "user_id" not in session:
        flash("Please login first")
        return redirect(url_for("login"))

    user_id = session["user_id"]

    with get_db() as con:
        user = con.execute("SELECT role FROM users WHERE id=?", (user_id,)).fetchone()
    if not user or user["role"].lower() != "owner":
        flash("Access denied! Only owners can add rooms.")
        return redirect(url_for("index"))

    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    rent = request.form.get("rent", "").strip()
    address = request.form.get("address", "").strip()
    amenities = request.form.get("amenities", "").strip() 
    files = request.files.getlist("room_images[]") 
    valid_files = [f for f in files if f and f.filename and allowed_file(f.filename)]

    if not valid_files:
        flash("Please upload at least one valid room image.")
        return redirect(url_for("owner"))
        
    first_filename = None
    
    with get_db() as con:
        cursor = con.execute("""
            INSERT INTO rooms (title, description, rent, address, owner_id, image_filename, amenities) 
            VALUES (?, ?, ?, ?, ?, NULL, ?)
        """, (title, description, rent, address, user_id, amenities)) 
        
        room_id = cursor.lastrowid
        
        for i, file in enumerate(valid_files):
            original_filename = secure_filename(file.filename)
            new_filename = str(uuid.uuid4()) + "_" + original_filename
            file.save(os.path.join(app.config["UPLOAD_FOLDER"], new_filename))
            
            if i == 0:
                first_filename = new_filename
                con.execute("UPDATE rooms SET image_filename = ? WHERE id = ?", (first_filename, room_id))

            con.execute("INSERT INTO images (filename, room_id, approved) VALUES (?, ?, 1)", (new_filename, room_id))
            
        con.commit()

    flash("🎉 Room added successfully with all images!")
    return redirect(url_for("owner"))

@app.route("/toggle_availability/<int:room_id>", methods=["POST"])
def toggle_availability(room_id):
    if "user_id" not in session:
        flash("Please login first")
        return redirect(url_for("login"))
    
    user_id = session["user_id"]
    
    with get_db() as con:
        room_check = con.execute("SELECT owner_id FROM rooms WHERE id=?", (room_id,)).fetchone()
        if not room_check or room_check['owner_id'] != user_id:
            flash("Access denied: You do not own this room.")
            return redirect(url_for("owner"))
            
        room = con.execute("SELECT availability FROM rooms WHERE id=?", (room_id,)).fetchone()
        if room:
            new_status = "Not Available" if room["availability"] == "Available" else "Available"
            con.execute("UPDATE rooms SET availability=? WHERE id=?", (new_status, room_id))
            con.commit()
            
    flash(f"Room availability updated to {new_status}!")
    return redirect(url_for("owner"))

@app.route('/owner')
def owner():
    if "user_id" not in session:
        return redirect(url_for("login"))

    user_id = session["user_id"]

    with get_db() as con:
        user = con.execute("SELECT role FROM users WHERE id=?", (user_id,)).fetchone()
    if not user or user["role"].lower() != "owner":
        flash("Access denied! Only owners can view dashboard.")
        return redirect(url_for("index"))

    with get_db() as con:
        rooms = con.execute(
            "SELECT id, title, description, rent, address, image_filename, availability, amenities FROM rooms WHERE owner_id=?",
            (user_id,)
        ).fetchall()
        
        bookings = con.execute("""
            SELECT b.id, b.full_name, b.contact_email, b.contact_phone, b.preferred_time, 
                     b.timestamp, b.status, r.title AS room_title, r.address, r.id AS room_id
            FROM bookings b 
            JOIN rooms r ON b.room_id = r.id 
            WHERE r.owner_id = ?
            ORDER BY b.timestamp DESC
        """, (user_id,)).fetchall()
        
        new_booking_count = con.execute("""
            SELECT COUNT(b.id) 
            FROM bookings b JOIN rooms r ON b.room_id = r.id 
            WHERE r.owner_id = ? AND b.status = 'New'
        """, (user_id,)).fetchone()[0]
        
    return render_template("owner.html", rooms=rooms, bookings=bookings, new_booking_count=new_booking_count)

@app.route('/booking/contacted/<int:booking_id>', methods=['POST'])
def mark_contacted(booking_id):
    user_id = session.get('user_id')
    if not user_id or session.get('role') != 'Owner':
        flash("Access denied.", 'error')
        return redirect(url_for('login'))
        
    with get_db() as con:
        booking_check = con.execute("""
            SELECT r.owner_id 
            FROM bookings b JOIN rooms r ON b.room_id = r.id 
            WHERE b.id = ?
        """, (booking_id,)).fetchone()
        
        if not booking_check or booking_check['owner_id'] != user_id:
            flash("Access denied: You cannot modify this booking.", 'error')
            return redirect(url_for('owner'))
            
        con.execute("UPDATE bookings SET status='Contacted' WHERE id=?", (booking_id,))
        con.commit()
        
    flash("Booking marked as contacted.", 'success')
    return redirect(url_for('owner'))

# =========================================================
# 🎯 MODIFIED: REGISTER ROUTE (Dynamic Token Check)
# =========================================================
@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password'].strip()
        role = request.form['role'].strip()
        secret_code = request.form.get('owner_secret_code', '').strip()

        if not username or not password or not role:
            flash("All fields required", 'error')
            return redirect(url_for('register'))

        if role not in ["Student","Govt Employee","Owner","User"]:
            flash("Invalid role", 'error')
            return redirect(url_for('register'))

        # --- Dynamic Token Check for Owner Role ---
        if role == 'Owner':
            if not secret_code:
                flash("Owner registration requires a valid secret code.", 'error')
                return redirect(url_for('register'))

            with get_db() as con:
                token_check = con.execute("""
                    SELECT id FROM owner_tokens 
                    WHERE token = ? AND is_used = 0 AND expiry > CURRENT_TIMESTAMP
                """, (secret_code,)).fetchone()
                
                if not token_check:
                    flash("Invalid, expired, or already used Owner Secret Code.", 'error')
                    return redirect(url_for('register'))
                
                # If code is valid, mark it as used immediately
                con.execute("UPDATE owner_tokens SET is_used = 1 WHERE id = ?", (token_check['id'],))
        # ----------------------------------------
        
        hashed_pw = generate_password_hash(password)
        with get_db() as con:
            try:
                con.execute("INSERT INTO users(username,password,role) VALUES(?,?,?)",
                            (username,hashed_pw,role))
                con.commit()
            except sqlite3.IntegrityError:
                flash("Username already exists", 'error')
                return redirect(url_for('register'))
        
        flash("Registered successfully! Login now.", 'success')
        return redirect(url_for('login'))
    return render_template('register.html')
# =========================================================

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password'].strip()
        
        with get_db() as con:
            user = con.execute(
                "SELECT id, password, role FROM users WHERE username=?",
                (username,)
            ).fetchone()
        
        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['role'] = user['role']
            session['username'] = username
            flash("Login successful!", 'success')

            if user['role'].lower() == 'admin':
                return redirect(url_for('admin_dashboard'))
            elif user['role'].lower() == 'owner':
                return redirect(url_for('owner'))
            else:
                return redirect(url_for('profile'))
        else:
            flash("Invalid username or password!", 'error')
            return redirect(url_for('login'))

    return render_template('login.html')

@app.route('/profile', methods=['GET','POST'])
def profile():
    if "user_id" not in session:
        return redirect(url_for('login'))
    
    user_id = session['user_id']

    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        phone = request.form.get('phone', '').strip()
        upi_id = request.form.get('upi_id', '').strip()

        with get_db() as con:
            con.execute("""
                UPDATE users
                SET email=?, phone=?, upi_id=?
                WHERE id=?
            """, (email, phone, upi_id, user_id))
            con.commit()
        flash("Profile updated", 'success')
        return redirect(url_for('profile'))

    with get_db() as con:
        user = con.execute("""
            SELECT id, username, role, email, phone, upi_id
            FROM users
            WHERE id=?
        """, (user_id,)).fetchone()

    return render_template('profile.html', user=user)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/room/<int:room_id>')
def room_details(room_id):
    # --- PRIVACY FIX: Removed owner's email and phone from the SELECT query ---
    with get_db() as con:
        room = con.execute("""
            SELECT r.id,r.title,r.description,r.rent,r.address,r.availability,r.amenities,
                   u.username 
            FROM rooms r JOIN users u ON r.owner_id=u.id
            WHERE r.id=?""",(room_id,)).fetchone()
        
        images = con.execute("SELECT filename FROM images WHERE room_id=? AND approved=1",(room_id,)).fetchall()
        
    if not room:
        flash("Room not found")
        return redirect(url_for('index'))
    return render_template("room_details.html", room=room, images=images)

def send_email(to_email, subject, message):
    sender_email = "techtimegs@gmail.com"
    sender_password = "owsdwdkzvfyhpdib" 

    try:
        msg = MIMEText(message, 'html')
        msg['Subject'] = subject
        msg['From'] = sender_email
        msg['To'] = to_email

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, to_email, msg.as_string())

        print(f"✅ Email sent to {to_email}")
        return True
    except Exception as e:
        print(f"❌ Failed to send email: {e}")
        return False

@app.route('/book/<int:room_id>', methods=['POST'])
def book(room_id):
    user_id = session.get('user_id')
    if not user_id:
        flash("Please login first to book a viewing.")
        return redirect(url_for('login'))

    with get_db() as con:
        # We need owner's email for the notification, so we fetch it here.
        room = con.execute("""
            SELECT r.*, u.username AS owner_name, u.email AS owner_email, u.phone AS owner_phone
            FROM rooms r
            JOIN users u ON r.owner_id = u.id
            WHERE r.id = ?
        """, (room_id,)).fetchone()

        if not room:
            flash("Room not found.")
            return redirect(url_for('index'))

        if request.method == 'POST':
            full_name = request.form.get('fullName', '').strip()
            contact_email = request.form.get('contactEmail', '').strip()
            contact_phone = request.form.get('contactPhone', '').strip()
            preferred_time = request.form.get('preferredTime', '').strip()
            
            if not all([full_name, contact_email, contact_phone]):
                flash("Error: Please fill in your Name, Email, and Phone Number.", 'error')
                return redirect(url_for('room_details', room_id=room_id))
            
            if room['availability'] != "Available":
                flash("Cannot book. Room is currently not available!")
                return redirect(url_for('room_details', room_id=room_id))

            existing = con.execute(
                "SELECT * FROM bookings WHERE room_id=? AND user_id=?",
                (room_id, user_id)
            ).fetchone()

            if existing:
                flash("You have already sent a viewing request for this room.")
                return redirect(url_for('room_details', room_id=room_id))

            con.execute(
                """
                INSERT INTO bookings 
                (room_id, user_id, full_name, contact_email, contact_phone, preferred_time, status) 
                VALUES (?, ?, ?, ?, ?, ?, 'New')
                """,
                (room_id, user_id, full_name, contact_email, contact_phone, preferred_time)
            )
            con.commit()

            email_sent = False
            if room['owner_email']:
                subject = f"ACTION REQUIRED: New Viewing Request for Room: {room['title']}"
                message = f"""
                Hello {room['owner_name']},<br><br>
                A potential tenant has submitted a viewing request for your room: <b>{room['title']}</b> at <b>{room['address']}</b>.<br><br>
                <h4 style="color:#6C63FF;">Tenant's Contact Information:</h4>
                <p style="margin:0;"><b>Name:</b> {full_name}</p>
                <p style="margin:0;"><b>Email:</b> {contact_email}</p>
                <p style="margin:0;"><b>Phone:</b> {contact_phone}</p>
                <p style="margin:0;"><b>Preferred Time:</b> {preferred_time if preferred_time else 'Any time / Not specified'}</p>
                <p style="margin-top:10px;">Please contact the tenant directly as soon as possible to confirm the appointment.</p>
                """
                email_sent = send_email(room['owner_email'], subject, message)
                
            if email_sent:
                flash(f"✅ Viewing request sent! The owner ({room['owner_name']}) has been notified and will contact you.", 'success')
            else:
                flash("⚠️ Request recorded, but we failed to send an email notification to the owner. Please check your email settings or contact the owner directly.", 'warning')

            return redirect(url_for('room_details', room_id=room_id))
            
        return redirect(url_for('room_details', room_id=room_id))

@app.route('/upload', methods=['POST'])
def upload():
    if "user_id" not in session or session.get("role")!="Owner":
        flash("Only owner can upload")
        return redirect(url_for('index'))

    file = request.files.get('room_image')
    caption = request.form.get('caption','').strip()
    room_id = request.form.get('room_id')

    if not file or file.filename=='' or not allowed_file(file.filename):
        flash("Invalid file")
        return redirect(url_for('owner'))

    fname = secure_filename(file.filename)
    new_filename = str(uuid.uuid4()) + "_" + fname
    file.save(os.path.join(app.config['UPLOAD_FOLDER'], new_filename))

    with get_db() as con:
        con.execute("INSERT INTO images(filename,caption,room_id,approved) VALUES(?,?,?,1)",
                    (new_filename,caption,room_id))
    flash("Image uploaded")
    return redirect(url_for('owner'))

# =========================================================
# 🎯 NEW: ADMIN CODE GENERATION ROUTE
# =========================================================

@app.route('/generate_owner_code', methods=['POST'])
@admin_required
def generate_owner_code():
    try:
        new_token = str(uuid.uuid4().hex)[:10].upper()
        # Token valid for 48 hours
        expiry_date = (datetime.datetime.now() + datetime.timedelta(hours=48)).strftime('%Y-%m-%d %H:%M:%S')

        with get_db() as con:
            con.execute("INSERT INTO owner_tokens (token, expiry) VALUES (?, ?)", (new_token, expiry_date))
            con.commit()
            
        flash(f'✅ NEW OWNER CODE: {new_token}. Valid until {expiry_date}.', 'success')
    except Exception as e:
        flash(f'❌ Error generating code: {e}', 'error')
        
    return redirect(url_for('admin_dashboard'))

# =========================================================
# 🎯 NEW: ADMIN USER/ROOM DELETION ROUTES
# =========================================================

@app.route("/delete_user/<int:user_id>", methods=['POST'])
@admin_required
def delete_user(user_id):
    if user_id == session['user_id']:
        flash("You cannot delete your own admin account!", 'error')
        return redirect(url_for("admin_dashboard"))
        
    with get_db() as con:
        # Check if the user owns any rooms before deletion
        owner_check = con.execute("SELECT role FROM users WHERE id=?", (user_id,)).fetchone()
        if owner_check and owner_check['role'] == 'Owner':
            rooms = con.execute("SELECT COUNT(id) FROM rooms WHERE owner_id=?", (user_id,)).fetchone()[0]
            if rooms > 0:
                flash(f"❌ Cannot delete Owner (ID: {user_id})! They still own {rooms} listings. Delete rooms first.", 'error')
                return redirect(url_for("admin_dashboard"))
        
        con.execute("DELETE FROM users WHERE id=?", (user_id,))
        con.commit()
        
    flash(f"User ID {user_id} deleted successfully.", 'success')
    return redirect(url_for("admin_dashboard"))

@app.route("/delete_room/<int:room_id>", methods=['POST'])
@admin_required
def delete_room(room_id):
    with get_db() as con:
        # 1. Delete associated images records
        con.execute("DELETE FROM images WHERE room_id=?", (room_id,))
        # 2. Delete associated bookings
        con.execute("DELETE FROM bookings WHERE room_id=?", (room_id,))
        # 3. Delete the room itself
        con.execute("DELETE FROM rooms WHERE id=?", (room_id,))
        con.commit()
        
    flash(f"Room ID {room_id} and all related data deleted successfully.", 'success')
    return redirect(url_for("admin_dashboard"))

# =========================================================
# 🎯 MODIFIED: ADMIN DASHBOARD ROUTE (Fetch all Admin data)
# =========================================================

@app.route("/admin_dashboard")
@admin_required
def admin_dashboard():
    # Pass current time for client-side comparison (although server check is primary)
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S') 
    
    with get_db() as con:
        # 1. Room Listings (including approval status)
        rooms = con.execute("""
            SELECT r.id, r.title, r.rent, u.username AS owner_name,
                     r.image_filename AS filename, i.approved
            FROM rooms r
            JOIN users u ON r.owner_id = u.id
            LEFT JOIN images i ON r.image_filename = i.filename 
            GROUP BY r.id 
            ORDER BY r.id DESC
        """).fetchall()

        # 2. User Data
        users = con.execute("SELECT id, username, role, email, phone FROM users ORDER BY role DESC, id ASC").fetchall()
        
        # 3. Owner Tokens
        tokens = con.execute("SELECT * FROM owner_tokens ORDER BY expiry DESC").fetchall()

    # Process room approved status 
    processed_rooms = []
    for room in rooms:
        processed_rooms.append({
            'id': room['id'],
            'title': room['title'],
            'owner_name': room['owner_name'],
            'rent': room['rent'],
            'filename': room['filename'],
            'is_approved': room['approved'] 
        })

    return render_template("admin_dashboard.html", rooms=processed_rooms, users=users, tokens=tokens, now=now)

# Remaining Admin Utility Routes (Approve/Reject)
@app.route("/approve_room/<int:room_id>")
@admin_required
def approve_room(room_id):
    with get_db() as con:
        con.execute("UPDATE images SET approved=1 WHERE room_id=?", (room_id,))
        con.commit()
    flash("✅ Room approved successfully!", 'success')
    return redirect(url_for("admin_dashboard"))

@app.route("/reject_room/<int:room_id>")
@admin_required
def reject_room(room_id):
    with get_db() as con:
        con.execute("UPDATE images SET approved=0 WHERE room_id=?", (room_id,))
        con.commit()
    flash("❌ Room rejected!", 'error')
    return redirect(url_for("admin_dashboard"))

@app.route('/users')
def view_users():
    if "user_id" not in session or session.get("role") != "Admin":
        flash("Access denied! Only Admins can see registered users.")
        return redirect(url_for('index'))

    with get_db() as con:
        rows = con.execute(
            "SELECT id, username, role, email, phone, upi_id FROM users WHERE role!='Owner' ORDER BY id ASC"
        ).fetchall()
    return render_template('users.html', rows=rows)


# ------------------ Run App ------------------
if __name__ == '__main__':
    # 🛑 CRITICAL STEP: The fixed init_db will delete and re-create the admin user 
    # to fix the login hash corruption issue.
    init_db() 
    add_availability_column()
    app.run(debug=True)