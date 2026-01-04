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

# --- ✅ RAZORPAY IMPORT (For ₹11 Fee) ---
try:
    import razorpay
    RAZORPAY_AVAILABLE = True
    print("✅ Razorpay imported successfully")
except ImportError:
    razorpay = None
    RAZORPAY_AVAILABLE = False
    print("⚠️ Warning: razorpay not installed. Install with: pip install razorpay")

# --- ✅ DATABASE PATH CONFIG ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "app.db")

# Flask App Initialization
app = Flask(__name__)
app.secret_key = "super_secret_business_key"

# Config
app.config['UPLOAD_FOLDER'] = os.path.join(BASE_DIR, "static", "uploads")
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'webp'}
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024 
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# --- ✅ RAZORPAY CONFIG (Replace with your actual keys from Razorpay Dashboard) ---
RAZORPAY_KEY_ID = "rzp_test_RzPY4LtSGAOkHI"
RAZORPAY_KEY_SECRET = "C1M3mE1f191Q6u092TGMw8QE"

# Initialize Razorpay client with error handling
razor_client = None
if RAZORPAY_AVAILABLE and RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET:
    try:
        razor_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
        print("✅ Razorpay client initialized successfully")
    except Exception as e:
        print(f"❌ Failed to initialize Razorpay client: {e}")
        razor_client = None
else:
    print("⚠️ Razorpay not available - check installation and API keys")

# ------------------ Helpers ------------------
def get_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session or session.get('role') != 'Admin':
            flash('Access denied: Admin privileges required.', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- ✅ IMPROVED EMAIL HELPER ---
def send_email(to_email, subject, message):
    sender_email = "techtimegs@gmail.com"
    # ⚠️ महत्त्वाचे: हा तुमचा साधा पासवर्ड नसावा. 
    # Google Account > Security > 2-Step Verification > App Passwords मधून तयार केलेला 16 अंकी कोड वापरा.
    sender_password = "owsdwdkzvfyhpdib" 

    try:
        msg = MIMEText(message, 'html')
        msg['Subject'] = subject
        msg['From'] = sender_email
        msg['To'] = to_email

        # Port 465 for SSL
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, to_email, msg.as_string())
        print(f"✅ Email successfully sent to {to_email}")
        return True
    except Exception as e:
        print(f"❌ Email Failed: {str(e)}")
        return False


def get_room_listings(location=None, min_rent=0, max_rent=99999, amenities=None):
    """Helper function for chatbot to search rooms"""
    query = "SELECT * FROM rooms WHERE availability = 'Available'"
    params = []
    
    if location:
        query += " AND address LIKE ?"
        params.append(f"%{location}%")
    
    if min_rent > 0:
        query += " AND rent >= ?"
        params.append(min_rent)
    
    if max_rent < 99999:
        query += " AND rent <= ?"
        params.append(max_rent)
    
    if amenities and len(amenities) > 0:
        for amenity in amenities:
            query += f" AND amenities LIKE ?"
            params.append(f"%{amenity}%")
    
    with get_db() as con:
        rooms = con.execute(query, params).fetchall()
    
    return [dict(room) for room in rooms]

# ------------------ Database Initialization ------------------
def init_db():
    with sqlite3.connect(DB_PATH) as con:
        # Users Table
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

        # Rooms Table
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
                amenities TEXT DEFAULT '',
                FOREIGN KEY(owner_id) REFERENCES users(id)
            );
        """)
        
        # Bookings Table (Updated with Payment Status)
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
                payment_status TEXT DEFAULT 'Pending',
                razorpay_order_id TEXT,
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
        
        # Owner Tokens Table
        con.execute("""
            CREATE TABLE IF NOT EXISTS owner_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT NOT NULL UNIQUE,
                expiry DATETIME NOT NULL,
                is_used BOOLEAN DEFAULT 0
            );
        """)
        
        # Create admin user
        hashed_password = generate_password_hash("admin123")
        con.execute("DELETE FROM users WHERE username = 'admin' AND role = 'Admin'")
        con.execute("INSERT OR IGNORE INTO users (username, password, role) VALUES ('admin', ?, 'Admin')", (hashed_password,))
        con.commit()

def add_missing_columns():
    """Add any missing columns to existing tables"""
    with sqlite3.connect(DB_PATH) as con:
        cursor = con.cursor()
        
        # Check and add columns to rooms table
        cursor.execute("PRAGMA table_info(rooms);")
        room_columns = [col[1] for col in cursor.fetchall()]
        
        if "availability" not in room_columns:
            cursor.execute("ALTER TABLE rooms ADD COLUMN availability TEXT DEFAULT 'Available';")
            print("✅ Column 'availability' added to rooms.")
            
        if "amenities" not in room_columns:
            cursor.execute("ALTER TABLE rooms ADD COLUMN amenities TEXT DEFAULT '';")
            print("✅ Column 'amenities' added to rooms.")
        
        # Check and add columns to bookings table
        cursor.execute("PRAGMA table_info(bookings);")
        booking_columns = [col[1] for col in cursor.fetchall()]
        
        columns_to_add = {
            "full_name": "TEXT NOT NULL DEFAULT ''",
            "contact_email": "TEXT NOT NULL DEFAULT ''", 
            "contact_phone": "TEXT NOT NULL DEFAULT ''",
            "preferred_time": "TEXT DEFAULT ''",
            "status": "TEXT DEFAULT 'New'",
            "payment_status": "TEXT DEFAULT 'Pending'",
            "razorpay_order_id": "TEXT"
        }
        
        for col_name, col_def in columns_to_add.items():
            if col_name not in booking_columns:
                try:
                    cursor.execute(f"ALTER TABLE bookings ADD COLUMN {col_name} {col_def};")
                    print(f"✅ Column '{col_name}' added to bookings table.")
                except sqlite3.OperationalError as e:
                    print(f"⚠️ Could not add column '{col_name}': {e}")
        
        con.commit()

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

@app.route('/chatbot', methods=['POST'])
def chatbot():
    user_message = request.json.get("message", "").strip()
    return jsonify({"response": "Chatbot feature is currently under maintenance. Please use the search filters on the homepage."})

@app.route('/create-booking-order', methods=['POST'])
def create_booking_order():
    if 'user_id' not in session:
        return jsonify({
            "error": "Authentication Required", 
            "message": "Please login first to book a room visit."
        }), 401
    elif not razor_client:
        return jsonify({
            "error": "Payment system is currently unavailable.",
            "details": "Please contact administrator or try again later."
        }), 503
    
    data = request.json
    if not data:
        return jsonify({"error": "No data provided"}), 400
    
    # ₹11 = 1100 paise
    amount = 1100 
    try:
        order = razor_client.order.create(data={
            "amount": amount,
            "currency": "INR",
            "payment_capture": "1"
        })
        print(f"✅ Razorpay order created: {order['id']}")
    except Exception as e:
        print(f"❌ Razorpay error: {e}")
        return jsonify({
            "error": "Payment gateway error",
            "details": str(e)
        }), 500
    
    # Save booking to database
    with get_db() as con:
        con.execute("""
            INSERT INTO bookings (room_id, user_id, full_name, contact_email, contact_phone, status, payment_status, razorpay_order_id)
            VALUES (?, ?, ?, ?, ?, 'Pending Payment', 'Pending', ?)
        """, (
            data.get('room_id'), 
            session.get('user_id'), 
            data.get('name'), 
            data.get('email'), 
            data.get('phone'), 
            order['id']
        ))
        con.commit()
        
    return jsonify({
        "success": True,
        "order": order,
        "key_id": RAZORPAY_KEY_ID,
        "amount": amount
    })

# --- ✅ FIXED CONFIRM PAYMENT ROUTE ---
# --- १. पेमेंट कन्फर्मेशन आणि रूम लॉक करणे ---
@app.route('/confirm-payment', methods=['POST'])
def confirm_payment():
    data = request.json
    if not data:
        return jsonify({"error": "No data received"}), 400
    
    order_id = data.get('order_id')
    
    try:
        with get_db() as con:
            # १. बुकिंग आणि पेमेंट स्टेटस अपडेट करा
            con.execute("UPDATE bookings SET status='New', payment_status='Paid' WHERE razorpay_order_id=?", (order_id,))
            
            # २. सर्व आवश्यक माहिती (Tenant, Owner, Room) एकाच वेळी मिळवा
            query = """
                SELECT b.full_name, b.contact_email, b.contact_phone, 
                       r.id as room_id, r.title, r.address,
                       u.email as owner_email, u.username as owner_name
                FROM bookings b
                JOIN rooms r ON b.room_id = r.id
                JOIN users u ON r.owner_id = u.id
                WHERE b.razorpay_order_id = ?
            """
            details = con.execute(query, (order_id,)).fetchone()
            
            if details:
                room_id = details['room_id']
                
                # ३. रूम लॉक करा (Main list मधून लपवण्यासाठी)
                con.execute("UPDATE rooms SET availability='In Booking Process' WHERE id=?", (room_id,))
                
                # ४. टेनंटला पेमेंट रिसीप्ट ई-मेल पाठवा
                tenant_subject = f"Payment Receipt: Visit for {details['title']}"
                tenant_message = f"""
                <div style="font-family: Arial, sans-serif; border: 1px solid #eee; padding: 20px; border-radius: 10px;">
                    <h2 style="color: #4F46E5;">Payment Received!</h2>
                    <p>Hello <b>{details['full_name']}</b>,</p>
                    <p>तुमचे <b>₹११</b> पेमेंट यशस्वी झाले आहे. तुमची या रूमसाठीची भेट (Visit) निश्चित झाली आहे.</p>
                    <hr>
                    <p><b>Property:</b> {details['title']}</p>
                    <p><b>Address:</b> {details['address']}</p>
                    <p><b>Order ID:</b> {order_id}</p>
                    <hr>
                    <p>मालक (<b>{details['owner_name']}</b>) तुम्हाला लवकरच संपर्क करतील.</p>
                </div>
                """
                send_email(details['contact_email'], tenant_subject, tenant_message)

                # ५. ओनरला माहिती मिळवण्यासाठी ई-मेल पाठवा
                owner_subject = f"✅ PAID VISIT: {details['title']} is now Locked"
                owner_message = f"""
                <h2>Hello {details['owner_name']},</h2>
                <p>टेनंट <b>{details['full_name']}</b> ने ₹११ फी भरली आहे.</p>
                <p><b>Status:</b> तुमची रूम आता 'In Booking Process' म्हणून लॉक केली आहे.</p>
                <p><b>Tenant Phone:</b> {details['contact_phone']}</p>
                <hr>
                <p>कृपया टेनंटला संपर्क करून भेटीची वेळ ठरवा.</p>
                """
                send_email(details['owner_email'], owner_subject, owner_message)

            con.commit()
            return jsonify({"status": "success", "message": "Receipts sent and room locked"})
            
    except Exception as e:
        print(f"❌ Error in confirm_payment: {str(e)}")
        return jsonify({"error": "Internal Server Error"}), 500
# --- २. ओनरसाठी पुन्हा उपलब्ध करण्याची सोय ---

@app.route('/book/<int:room_id>', methods=['GET', 'POST'])
def book_room(room_id):
    if "user_id" not in session:
        flash("Please login first")
        return redirect(url_for('login'))

    with get_db() as con:
        room = con.execute("""
            SELECT r.*, u.username AS owner_name, u.email AS owner_email
            FROM rooms r
            JOIN users u ON r.owner_id = u.id
            WHERE r.id = ?
        """, (room_id,)).fetchone()

        if not room:
            flash("Room not found.")
            return redirect(url_for('index'))

    if request.method == 'GET':
        return render_template('book_room.html', 
                             room=room, 
                             razorpay_available=bool(razor_client),
                             razorpay_key_id=RAZORPAY_KEY_ID if razor_client else None)
    
    # POST method - process booking
    full_name = request.form.get('fullName', '').strip()
    contact_email = request.form.get('contactEmail', '').strip()
    contact_phone = request.form.get('contactPhone', '').strip()
    preferred_time = request.form.get('preferredTime', '').strip()

    if not all([full_name, contact_email, contact_phone]):
        flash("Error: Please fill in your Name, Email, and Phone Number.", 'error')
        return redirect(url_for('book_room', room_id=room_id))
    
    if room['availability'] != "Available":
        flash("Cannot book. Room is currently not available!", 'error')
        return redirect(url_for('room_details', room_id=room_id))

    # Check for existing booking
    with get_db() as con:
        existing = con.execute(
            "SELECT * FROM bookings WHERE room_id=? AND user_id=?",
            (room_id, session['user_id'])
        ).fetchone()

        if existing:
            flash("You have already sent a viewing request for this room.")
            return redirect(url_for('room_details', room_id=room_id))

        # Insert booking
        con.execute("""
            INSERT INTO bookings (room_id, user_id, full_name, contact_email, contact_phone, preferred_time, status) 
            VALUES (?, ?, ?, ?, ?, ?, 'New')
        """, (room_id, session['user_id'], full_name, contact_email, contact_phone, preferred_time))
        con.commit()

    # Send email to owner
    if room['owner_email']:
        subject = f"New Booking Request for Room: {room['title']}"
        message = f"""
        Hello {room['owner_name']},<br><br>
        A potential tenant has submitted a booking request for your room: <b>{room['title']}</b> at <b>{room['address']}</b>.<br><br>
        <h4>Tenant's Contact Information:</h4>
        <p><b>Name:</b> {full_name}</p>
        <p><b>Email:</b> {contact_email}</p>
        <p><b>Phone:</b> {contact_phone}</p>
        <p><b>Preferred Time:</b> {preferred_time if preferred_time else 'Any time / Not specified'}</p>
        """
        send_email(room['owner_email'], subject, message)
        
    flash(f"✅ Booking request sent! The owner ({room['owner_name']}) has been notified and will contact you.", 'success')
    return redirect(url_for('room_details', room_id=room_id))

@app.route('/payment-success', methods=['POST'])
def payment_success():
    if not request.is_json:
        return jsonify({"error": "Invalid request"}), 400
        
    data = request.json
    order_id = data.get('razorpay_order_id')
    
    if not order_id:
        return jsonify({"error": "Missing order ID"}), 400
    
    with get_db() as con:
        con.execute("UPDATE bookings SET payment_status='Paid', status='Confirmed' WHERE razorpay_order_id=?", (order_id,))
        con.commit()
        
    return jsonify({"status": "success"})

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
    # १. युजर लॉगिन आहे का ते तपासा
    if "user_id" not in session:
        flash("Please login first")
        return redirect(url_for("login"))
    
    user_id = session["user_id"]
    
    with get_db() as con:
        # २. ओनर स्वतःच्याच रूमचा स्टेटस बदलतोय का याची खात्री करा
        room_check = con.execute("SELECT owner_id, availability FROM rooms WHERE id=?", (room_id,)).fetchone()
        
        if not room_check or room_check['owner_id'] != user_id:
            flash("Access denied: You do not own this room.")
            return redirect(url_for("owner"))
        
        current_status = room_check['availability']
        
        # ३. नवीन लॉजिक: जर स्टेटस 'Available' नसेल, तर तो 'Available' करा. 
        # जर 'Available' असेल, तर तो 'Occupied' (किंवा Not Available) करा.
        if current_status == "Available":
            new_status = "Occupied"
        else:
            # स्टेटस 'In Booking Process' किंवा 'Occupied' असल्यास पुन्हा 'Available' होईल
            new_status = "Available"
            
        con.execute("UPDATE rooms SET availability=? WHERE id=?", (new_status, room_id))
        con.commit()
            
    flash(f"Room status updated to {new_status}!")
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

        # Dynamic Token Check for Owner Role
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
                
                con.execute("UPDATE owner_tokens SET is_used = 1 WHERE id = ?", (token_check['id'],))
        
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

            # ✅ बदल: Admin आणि Owner ला मेसेज न दाखवता थेट डॅशबोर्डवर पाठवा
            if user['role'].lower() == 'admin':
                return redirect(url_for('admin_dashboard'))
            elif user['role'].lower() == 'owner':
                return redirect(url_for('owner'))
            else:
                # फक्त सामान्य युजरला 'Login successful' मेसेज दाखवा
                flash("Login successful!", 'success')
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
    
    # Debug print to check Razorpay key
    print(f"Debug: RAZORPAY_KEY_ID = {RAZORPAY_KEY_ID}")
    print(f"Debug: Key starts with 'rzp_': {RAZORPAY_KEY_ID.startswith('rzp_') if RAZORPAY_KEY_ID else False}")
    
    return render_template("room_details.html", 
                         room=room, 
                         images=images,
                         razorpay_key_id=RAZORPAY_KEY_ID,
                         razorpay_available=bool(razor_client))

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

@app.route('/generate_owner_code', methods=['POST'])
@admin_required
def generate_owner_code():
    try:
        new_token = str(uuid.uuid4().hex)[:10].upper()
        expiry_date = (datetime.datetime.now() + datetime.timedelta(hours=48)).strftime('%Y-%m-%d %H:%M:%S')

        with get_db() as con:
            con.execute("INSERT INTO owner_tokens (token, expiry) VALUES (?, ?)", (new_token, expiry_date))
            con.commit()
            
        flash(f'✅ NEW OWNER CODE: {new_token}. Valid until {expiry_date}.', 'success')
    except Exception as e:
        flash(f'❌ Error generating code: {e}', 'error')
        
    return redirect(url_for('admin_dashboard'))

@app.route("/delete_user/<int:user_id>", methods=['POST'])
@admin_required
def delete_user(user_id):
    if user_id == session['user_id']:
        flash("You cannot delete your own admin account!", 'error')
        return redirect(url_for("admin_dashboard"))
        
    with get_db() as con:
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
        con.execute("DELETE FROM images WHERE room_id=?", (room_id,))
        con.execute("DELETE FROM bookings WHERE room_id=?", (room_id,))
        con.execute("DELETE FROM rooms WHERE id=?", (room_id,))
        con.commit()
        
    flash(f"Room ID {room_id} and all related data deleted successfully.", 'success')
    return redirect(url_for("admin_dashboard"))

@app.route("/admin_dashboard")
@admin_required
def admin_dashboard():
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S') 
    
    with get_db() as con:
        rooms = con.execute("""
            SELECT r.id, r.title, r.rent, u.username AS owner_name,
                     r.image_filename AS filename, i.approved
            FROM rooms r
            JOIN users u ON r.owner_id = u.id
            LEFT JOIN images i ON r.image_filename = i.filename 
            GROUP BY r.id 
            ORDER BY r.id DESC
        """).fetchall()

        users = con.execute("SELECT id, username, role, email, phone FROM users ORDER BY role DESC, id ASC").fetchall()
        
        tokens = con.execute("SELECT * FROM owner_tokens ORDER BY expiry DESC").fetchall()

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

# ------------------ Razorpay Test Route ------------------
@app.route('/test-razorpay')
def test_razorpay():
    """Test Razorpay connection"""
    if not RAZORPAY_AVAILABLE:
        return "❌ Razorpay not installed. Run: pip install razorpay"
    
    if not razor_client:
        return "❌ Razorpay client not initialized. Check API keys."
    
    try:
        # Test with a minimal amount
        order = razor_client.order.create({
            "amount": 100,  # 1 rupee
            "currency": "INR",
            "payment_capture": "1"
        })
        return f"✅ Razorpay working! Test order created: {order['id']}"
    except Exception as e:
        return f"❌ Razorpay error: {str(e)}"

# ------------------ Alternative Payment (No Razorpay) ------------------
@app.route('/free-booking/<int:room_id>', methods=['POST'])
def free_booking(room_id):
    """Alternative booking without payment (for testing)"""
    if "user_id" not in session:
        flash("Please login first")
        return redirect(url_for('login'))

    with get_db() as con:
        room = con.execute("""
            SELECT r.*, u.username AS owner_name, u.email AS owner_email
            FROM rooms r
            JOIN users u ON r.owner_id = u.id
            WHERE r.id = ?
        """, (room_id,)).fetchone()

        if not room:
            flash("Room not found.")
            return redirect(url_for('index'))

    full_name = request.form.get('fullName', '').strip()
    contact_email = request.form.get('contactEmail', '').strip()
    contact_phone = request.form.get('contactPhone', '').strip()
    preferred_time = request.form.get('preferredTime', '').strip()

    if not all([full_name, contact_email, contact_phone]):
        flash("Error: Please fill in your Name, Email, and Phone Number.", 'error')
        return redirect(url_for('book_room', room_id=room_id))
    
    if room['availability'] != "Available":
        flash("Cannot book. Room is currently not available!", 'error')
        return redirect(url_for('room_details', room_id=room_id))

    with get_db() as con:
        existing = con.execute(
            "SELECT * FROM bookings WHERE room_id=? AND user_id=?",
            (room_id, session['user_id'])
        ).fetchone()

        if existing:
            flash("You have already sent a viewing request for this room.")
            return redirect(url_for('room_details', room_id=room_id))

        con.execute("""
            INSERT INTO bookings (room_id, user_id, full_name, contact_email, contact_phone, preferred_time, status, payment_status) 
            VALUES (?, ?, ?, ?, ?, ?, 'New', 'Free')
        """, (room_id, session['user_id'], full_name, contact_email, contact_phone, preferred_time))
        con.commit()

    # Send email to owner
    if room['owner_email']:
        subject = f"New FREE Booking Request for Room: {room['title']}"
        message = f"""
        Hello {room['owner_name']},<br><br>
        A potential tenant has submitted a booking request for your room: <b>{room['title']}</b> at <b>{room['address']}</b>.<br><br>
        <h4>Tenant's Contact Information:</h4>
        <p><b>Name:</b> {full_name}</p>
        <p><b>Email:</b> {contact_email}</p>
        <p><b>Phone:</b> {contact_phone}</p>
        <p><b>Preferred Time:</b> {preferred_time if preferred_time else 'Any time / Not specified'}</p>
        <p><b>Note:</b> This booking was created without payment (free mode).</p>
        """
        send_email(room['owner_email'], subject, message)
        
    flash(f"✅ FREE Booking request sent! The owner ({room['owner_name']}) has been notified.", 'success')
    return redirect(url_for('room_details', room_id=room_id))

# ------------------ Run App ------------------
if __name__ == '__main__':
    print("🔄 Initializing database...")
    init_db()
    add_missing_columns()
    print("✅ Database initialized successfully!")
    
    if razor_client:
        print("✅ Razorpay integration is ready!")
    else:
        print("⚠️ Razorpay not available. Free booking mode enabled.")
        print("   To enable payments:")
        print("   1. pip install razorpay")
        print("   2. Get API keys from: https://dashboard.razorpay.com")
        print("   3. Update RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET in the code")
    
    app.run(debug=True, host='0.0.0.0', port=5000)
