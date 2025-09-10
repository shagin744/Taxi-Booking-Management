from flask import Flask, render_template, request, redirect, url_for, session, flash
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
from bson.objectid import ObjectId
from datetime import datetime
from twilio.rest import Client
import random
import requests
from geopy.distance import geodesic
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask_mail import Mail, Message 
from werkzeug.security import generate_password_hash, check_password_hash




app = Flask(__name__)
app.secret_key = "supersecretkey"

app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'Kingslinsamuval@gmail.com'
app.config['MAIL_PASSWORD'] = 'ifed nsdo zfgw kiiq'   # Gmail App Password
app.config['MAIL_DEFAULT_SENDER'] = 'King@gmail.com'

mail = Mail(app)
# -------------------------
# MongoDB
# -------------------------
client = MongoClient("mongodb://localhost:27017/")
db = client["ride_booking"]
users_collection = db["users"]
drivers_collection = db["drivers"]
bookings_collection = db["bookings"]
payments_collection = db["payments"]
notifications_collection = db["notifications"]
fleet_collection = db["fleet"]

# -------------------------
# Email / SMS Config
# -------------------------
SENDER_EMAIL = "Kingslinsamuval@gmail.com"
SENDER_PASSWORD = "ifed nsdo zfgw kiiq"

TWILIO_ACCOUNT_SID = 'ACd8725ddcd2b887e9a0e348b1729ad17d'
TWILIO_AUTH_TOKEN = '4b9ec778b8d347edd19e46732aa91c75'
TWILIO_PHONE_NUMBER = '+19592073844'
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# -------------------------
# Helper Functions
# -------------------------
def send_sms_via_twilio(to, body):
    try:
        # Clean & normalize number
        to = to.replace(" ", "").strip()
        if not to.startswith("+"):
            to = f"+{to}"

        message = twilio_client.messages.create(
            body=body,
            from_='+19592073844',  # ✅ your Twilio number from config
            to=to                     # ✅ dynamic user number
        )

        # print SID and status only if message was created
        print("✅ SMS sent:", message.sid, "Status:", message.status)
        return True

    except Exception as e:
        # if something fails, don't reference message (it doesn't exist here)
       
        print("❌ SMS failed:", str(e))
        return False



def geocode_area(area):
    """Geocode restricted to Madurai"""
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "q": f"{area}, Madurai, Tamil Nadu, India",
                "format": "json",
                "limit": 1,
                "countrycodes": "in"
            },
            headers={"User-Agent": "RideBookingApp/1.0"}
        )
        data = resp.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception as e:
        print("Geocode error:", e)
    return None


def calc_distance_km(coord1, coord2):
    try:
        if not coord1 or not coord2:
            print("⚠️ Coordinates missing, using fallback 5 km")
            return 5.0   # only fallback if geocoding failed
        dist = round(geodesic(coord1, coord2).km, 2)
        if dist <= 0 or dist > 200:  # unrealistic inside Madurai
            print("⚠️ Unrealistic distance, using fallback 5 km")
            return 5.0
        return dist
    except Exception as e:
        print("Distance error:", e)
        return 5.0


def fare_from_distance_km(distance_km):
    if distance_km <= 0:
        return 0.0
    return round(distance_km * 50.0, 2)  # 50 Rs per km


def apply_promo(original_fare, promo_code):
    """Return (discount_percent, discount_amount, final_fare)."""
    PROMO_CODES = {"SAVE10": 10, "RIDE50": 50, "WELCOME25": 25, "4567": 15}
    pct = PROMO_CODES.get((promo_code or "").upper().strip(), 0)
    disc = round(original_fare * (pct / 100), 2)
    final = round(original_fare - disc, 2)
    return pct, disc, final

# -------------------------
# User Routes
# -------------------------
drivers_list = ["driver1@example.com"]

@app.route('/')
def index():
    return render_template('index.html')

from flask import session

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        mobile = request.form['mobile']
        emergency_contact = request.form['emergency_contact']
        password = request.form['password']

        # ✅ Save user to DB
        users_collection.insert_one({
            "name": name,
            "email": email,
            "mobile": mobile,
            "emergency_contact": emergency_contact,
            "password": password
        })

        # ✅ Store email & name in session
        session['user_name'] = name
        session['user_email'] = email
        session['user_phone'] = mobile

        flash("Signup successful!", "success")

        # ✅ Redirect to booking (with user email available in session)
        return redirect(url_for('booking'))

    return render_template('signup.html')



@app.route('/booking', methods=['GET', 'POST'])
def booking():

    if not session.get("user_email"):
        flash("Please login or signup before booking.", "warning")
        return redirect(url_for("signup"))
    user_name = session.get('user_name', '')
    user_email = session.get('user_email', '')
    user_phone = session.get('user_phone', '')
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        pickup = request.form['pickup'].strip()
        drop = request.form['drop'].strip()
        promo_code = (request.form.get('promo_code', '').strip() or "").upper()
        date = request.form.get('date') or datetime.now().strftime('%Y-%m-%d')
        time = request.form.get('time') or datetime.now().strftime('%H:%M')
        booking_type = request.form.get('type', 'now')

        # --- Distance & Fare ---
        pc = geocode_area(pickup) 
        dc = geocode_area(drop) 
        distance = calc_distance_km(pc, dc)
        original_fare = fare_from_distance_km(distance)

        # --- Apply promo code (once) ---
        discount_percent, discount_amount, final_fare = apply_promo(original_fare, promo_code)

        # --- Assign driver ---
        driver_email = random.choice(drivers_list)
        status = "Pending" if booking_type == "schedule" else "Confirmed"
        user_email = session.get('user_email', 'Guest')
        vehicle_type = request.form.get('vehicle_type', 'Sedan')


        # --- Save booking ---
        booking_doc = {
            "name": name,
            "user": user_email,
            "phone":user_phone,
            "driver": driver_email,
            "pickup": pickup,
            "drop": drop,
            "vehicle_type": vehicle_type,
            "distance": distance,
            "original_fare": original_fare,
            "discount_amount": discount_amount,
            "discount_percent": discount_percent,
            "final_fare": final_fare,   # ✅ always charge this
            "promo_code": promo_code,
            "date": date,
            "time": time,
            "status": status,
            "payment_method": None,
            "created_at": datetime.utcnow()
        }

        try:
            result = bookings_collection.insert_one(booking_doc)
            booking_id = str(result.inserted_id)
        except DuplicateKeyError:
            existing = bookings_collection.find_one({
               "name": user_name, "user": user_email, "phone":user_phone, "pickup": pickup,
                "drop": drop, "date": date, "time": time
            })
            booking_doc = existing
            booking_id = str(existing["_id"])

        # ✅ Pass full booking object to fare.html
        return render_template(
            'fare.html',
            booking=booking_doc,
            booking_id=booking_id,
            rate_per_km=50 
        )

    return render_template('booking.html', user_email=user_email, user_name=user_name, user_phone=user_phone)
@app.route('/user/bookings')
def user_bookings():
    name = session.get('user_name')
    user_email = session.get('user_email')
    phone = session.get('user_phone')

    if not user_email:
        flash("Please login first.", "warning")
        return redirect(url_for('index'))

    bookings = list(bookings_collection.find().sort("created_at", -1))
    for b in bookings:
        b["_id"] = str(b["_id"])

    return render_template("user_bookings.html", bookings=bookings, name=name)

@app.route('/user/bookings/cancel/<booking_id>', methods=['POST'])
def user_cancel_booking(booking_id):
    user_email = session.get('user_email')
    user_phone = session.get('user_phone')

    if not user_email:
        flash("Please login first to cancel booking.", "warning")
        return redirect(url_for('index'))

    booking = bookings_collection.find_one(
        {"_id": ObjectId(booking_id), "$or": [{"user": user_email}, {"phone": user_phone}] }
    )
    if not booking:
        flash("Booking not found or not allowed.", "danger")
        return redirect(url_for('user_bookings'))

    # ✅ Get cancel reason
    cancel_reason = request.form.get("cancel_reason")
    other_reason = request.form.get("other_reason", "").strip()
    if cancel_reason == "Other" and other_reason:
        reason = other_reason
    else:
        reason = cancel_reason or "No reason provided"

    # ✅ Update booking with reason
    bookings_collection.update_one(
        {"_id": ObjectId(booking_id)},
        {"$set": {"status": "Cancelled", "cancel_reason": reason, "cancelled_at": datetime.now()}}
    )
        
    users_collection.delete_one({"email": user_email})

    payments_collection.update_one(
        {"booking_id": str(booking_id)},
        {"$set": {"status": "Refunded"}}
    )

    cancel_reason = reason  

    # ✅ Fix user lookup
    user = users_collection.find_one({ "$or": [{"email": user_email}, {"mobile": user_phone}] })
    name = user.get("name", "Customer") if user else "Customer"
    phone = user.get("mobile") if user else None
    email = user.get("email") if user else None

    # ✅ Use correct cancel reason
    

    # --- SMS ---
    if phone:
        send_sms_via_twilio(
            f"+91{phone}",
            f"Dear {name}, your ride from {booking.get('pickup','-')} to {booking.get('drop','-')} "
            f"on {booking.get('date','-')} at {booking.get('time','-')} has been CANCELLED.\n"
            f"Reason: {cancel_reason}"
        )

    # --- Email ---
    if email:
        refund_text = ""
        if booking.get("final_fare", 0) > 0:
            refund_text = f"Refund: ✅ Your amount ₹{booking.get('final_fare', 0)} has been refunded successfully.\n"

        msg = Message(
            subject="Booking Cancelled - Invoice",
            recipients=[email],
            body=f"""
Dear {name},

Your booking has been CANCELLED.

Ride Details:
Pickup: {booking.get('pickup', '-') }
Drop: {booking.get('drop', '-') }
Distance: {booking.get('distance', 0)} km
Vehicle Type: {booking.get('vehicle_type', '-') }
Date: {booking.get('date', '-') }
Time: {booking.get('time', '-') }
Fare: ₹{booking.get('final_fare', 0)}

❌ Cancel Reason: {cancel_reason}
Status:User CANCELLED ❌
{refund_text}
Thank you,
Ride Booking Team
"""
        )
        mail.send(msg)

    flash("Booking cancelled successfully. SMS & Email sent.", "success")
    return redirect(url_for('user_bookings'))

@app.route('/cancelled_invoice/<booking_id>')
def cancelled_invoice(booking_id):
    booking = bookings_collection.find_one({"_id": ObjectId(booking_id)})
    if not booking:
        return "Booking not found", 404

    payment = payments_collection.find_one({"email": booking['user'], "pickup": booking['pickup'], "drop": booking['drop']})
    return render_template(
        "invoice.html",
        booking={
            'name': booking.get('name', '-'),
            'email': booking.get('user', '-'),
            'pickup': booking.get('pickup', '-'),
            'drop': booking.get('drop', '-'),
            'distance': booking.get('distance', 0),
            'vehicle_type': booking.get('vehicle_type', '-'),
            'original_fare': f"{booking.get('original_fare', 0):.2f}",
            'discount_amount': f"{booking.get('discount_amount', 0):.2f}",
            'discount_percent': booking.get('discount_percent', 0),
            'final_fare': f"{booking.get('final_fare', 0):.2f}",
            'promo_code': booking.get('promo_code', ''),
            'date': booking.get('date', '-'),
            'time': booking.get('time', '-'),
            'status': "Cancelled",
            'payment_method': payment.get('method', '-') if payment else '-'
        }
    )
@app.route("/payment/<booking_id>", methods=["GET"])
def payment_page(booking_id):
    booking = bookings_collection.find_one({"_id": ObjectId(booking_id)})
    if not booking:
        return "Booking not found", 404

    return render_template(
        "payment.html",
        booking_id=str(booking["_id"]),
        name=booking.get("name", ""),
        email=booking.get("user", ""),
        pickup=booking.get("pickup", ""),
        drop=booking.get("drop", ""),
        distance=booking.get("distance", 0),
        vehicle_type=booking.get("vehicle_type", "Not Selected"),
        fare=f"{booking.get('original_fare', 0):.2f}",
        promo_code=booking.get("promo_code", ""),
        discount_amount=f"{booking.get('discount_amount', 0):.2f}",
        final_fare=f"{booking.get('final_fare', 0):.2f}",
        date=booking.get("date", ""),
        time=booking.get("time", "")
    )

@app.route('/payment', methods=['POST'])
def process_payment():
    booking_id = request.form.get("booking_id")
    email = request.form.get("email")
    pickup = request.form.get("pickup")
    drop = request.form.get("drop")
    vehicle_type = request.form.get("vehicle_type")
    amount_paid = float(request.form.get("final_fare", 0))
    date = request.form.get("date")
    time = request.form.get("time")
    method = request.form.get("method")

    card_number = request.form.get("card_number")
    crypto_address = request.form.get("crypto_address")

    # ✅ FIXED: upsert instead of insert_one
    payments_collection.update_one(
        {"booking_id": ObjectId(booking_id)},
        {"$set": {
            "booking_id": ObjectId(booking_id),
            "email": email,
            "pickup": pickup,
            "drop": drop,
            "vehicle_type": vehicle_type,
            "amount_paid": amount_paid,
            "method": method,
            "card_number": card_number if method == "card" else None,
            "crypto_address": crypto_address if method == "crypto" else None,
            "date": date,
            "time": time,
            "created_at": datetime.utcnow()
        }},
        upsert=True
    )


    notifications_collection.insert_one({
        "message": f"Your {vehicle_type} ride from {pickup} to {drop} on {date} at {time} has been booked.",
        "timestamp": datetime.utcnow()
    })

    flash("Payment successful!", "success")
    return redirect(url_for("communication"))

@app.route('/communication')
def communication():
    user = users_collection.find_one(sort=[("_id", -1)])
    booking = bookings_collection.find_one(sort=[("_id", -1)])
    if not user:
        return "No user found."

    name = user.get("name", "")
    phone = user.get("mobile", "").strip()
    if phone:
        send_sms_via_twilio(f"+91{phone}", f"Dear {name}, your ride from {booking['pickup']} to {booking['drop']} on {booking['date']} at {booking['time']} has been booked SUCCESSFULLY.")

        return render_template("communication.html", name=name, phone=phone)

    return render_template(
        "communication.html",
        name=name,
        phone=phone,
        whatsapp_link=f"https://wa.me/91{phone}?text=Hi%20{name},%20your%20ride%20has%20been%20booked!",
        sms_link=f"sms:91{phone}?body=Hi%20{name},%20your%20ride%20is%20confirmed.",
        call_link=f"tel:+91{phone}"
    )

@app.route('/notifications')
def view_notifications():
    notifications = list(notifications_collection.find().sort("timestamp", -1))
    for n in notifications:
        n['_id'] = str(n['_id'])
        ts = n.get('timestamp')
        if isinstance(ts, datetime):
            n['timestamp'] = ts.strftime("%d %b %Y, %I:%M %p")
    return render_template("notifications.html", notifications=notifications)

@app.route('/invoice')
def invoice():
    """Build invoice from last user, booking and payment (for demo)."""
    user = users_collection.find_one(sort=[("_id", -1)])
    booking = bookings_collection.find_one(sort=[("_id", -1)])
    payment = payments_collection.find_one(sort=[("_id", -1)])

    if not (user and booking and payment):
        return "Missing data."

    # Use stored booking values (no re-discount)
    name = user.get('name', '-')
    email = user.get('email', '-')
    pickup = booking.get('pickup', '-')
    drop = booking.get('drop', '-')
    distance = booking.get('distance', 0)
    vehicle_type = booking.get('vehicle_type', '-')

    original_fare = float(booking.get('original_fare', 0))
    discount_amount = float(booking.get('discount_amount', 0))
    discount_percent = int(booking.get('discount_percent', 0))
    final_fare = float(booking.get('final_fare', 0))
    promo_code = booking.get('promo_code', '').strip()

    body = f"""
Dear {name},

Thank you for using our ride booking service!

Pickup: {pickup}
Drop: {drop}
Distance: {distance} km
Vehicle Type: {vehicle_type} 
Date: {booking.get('date', '-')}
Time: {booking.get('time', '-')}
Original Fare: ₹{original_fare:.2f}
Discount: ₹{discount_amount:.2f} ({discount_percent}%)
Final Fare Paid: ₹{final_fare:.2f}
Payment Method: {payment.get('method', '-')}

Regards,
Ride Booking Team
"""

    # Email sending (optional)
    status = "Invoice email skipped"
    try:
        if email and SENDER_EMAIL and SENDER_PASSWORD:
            msg = MIMEMultipart()
            msg['From'] = SENDER_EMAIL
            msg['To'] = email
            msg['Subject'] = "Ride Booking Invoice"
            msg.attach(MIMEText(body, 'plain'))

            server = smtplib.SMTP('smtp.gmail.com', 587)
            server.starttls()
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.send_message(msg)
            server.quit()
            status = "Invoice sent successfully"
    except Exception as e:
        status = f"Failed to send invoice: {e}"

    return render_template(
        "invoice.html",
        booking={
            'name': name,
            'email': email,
            'pickup': pickup,
            'drop': drop,
            'distance': distance,
            'vehicle_type': vehicle_type,
            'original_fare': f"{original_fare:.2f}",
            'discount_amount': f"{discount_amount:.2f}",
            'discount_percent': discount_percent,
            'final_fare': f"{final_fare:.2f}",
            'promo_code': promo_code,
            'date': booking.get('date', '-'),
            'time': booking.get('time', '-'),
            'status': status,
            'payment_method': payment.get('method', '-')
        }
    )

# -------------------------
# Admin
# -------------------------
@app.route('/admin/dashboard')
def admin_dashboard():
    total_users = users_collection.count_documents({})
    total_drivers = drivers_collection.count_documents({})
   
    # Only count rides that are NOT cancelled
    total_rides = bookings_collection.count_documents({"status": {"$ne": "Cancelled"}})

    # Revenue should only include rides that are not cancelled
    valid_bookings = bookings_collection.find({"status": {"$ne": "Cancelled"}})
    valid_booking_ids = [b["_id"] for b in valid_bookings]   # ✅ keep ObjectId, not str

    payments = payments_collection.find({"booking_id": {"$in": valid_booking_ids}})
    total_revenue = round(sum(float(p.get("amount_paid", 0) or 0) for p in payments), 2)

    return render_template(
        "dashboard.html",
        users=total_users,
        drivers=total_drivers,
        rides=total_rides,
        revenue=total_revenue
    )


@app.route('/admin/users')
def admin_users():
    status_filter = request.args.get('status')  # Active / Inactive
    query = {}
    if status_filter:
        query['status'] = status_filter
    users = list(users_collection.find(query))
    for u in users:
        u["_id"] = str(u["_id"])  # Convert ObjectId to string
    return render_template("admin_users.html", users=users, status_filter=status_filter)

# --- Activate User ---
@app.route('/admin/user/activate/<user_id>')
def activate_user(user_id):
    users_collection.update_one({"_id": ObjectId(user_id)}, {"$set": {"status": "Active"}})
    flash("✅ User activated successfully!", "success")
    return redirect(url_for('admin_users'))

# --- Deactivate User ---
@app.route('/admin/user/inactive/<user_id>')
def inactive_user(user_id):
    users_collection.update_one({"_id": ObjectId(user_id)}, {"$set": {"status": "Inactive"}})
    flash("✅ User marked as Inactive", "success")
    return redirect(url_for('admin_users'))

# --- Delete User (Permanent) ---
@app.route('/admin/user/delete/<user_id>')
def delete_user(user_id):   # 👈 function name and template call should match
    users_collection.delete_one({"_id": ObjectId(user_id)})
    flash("🗑️ User deleted permanently!", "danger")
    return redirect(url_for('admin_users'))

# --- Edit User ---
@app.route('/admin/users/edit/<user_id>', methods=['GET', 'POST'])
def edit_user(user_id):
    user = users_collection.find_one({"_id": ObjectId(user_id)})
    if request.method == 'POST':
        users_collection.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {
                "name": request.form.get('name', ''),
                "email": request.form.get('email', ''),
                "mobile": request.form.get('mobile', ''),
                "emergency_contact": request.form.get('emergency_contact', ''),
                "password": request.form.get('password', '')
            }}
        )
        flash("✅ User details updated successfully!", "success")
        return redirect(url_for('admin_users'))
    return render_template("edit_user.html", user=user)

@app.route('/admin/drivers')
def admin_drivers():
    all_drivers = drivers_collection.find()
    return render_template("admin_drivers.html", drivers=all_drivers)

@app.route('/driver/signup', methods=['GET', 'POST'])
def driver_signup():
    if request.method == 'POST':
        driver = {
            "name": request.form['name'],
            "email": request.form['email'],
            "mobile": request.form['mobile'],
            "vehicle_type": request.form['vehicle_type'],
            "license": request.form['license'],
            "status": "Active"  # default status
        }
        drivers_collection.insert_one(driver)
        return redirect(url_for('admin_drivers'))  # go back to driver list
    return render_template("driver_signup.html")




@app.route('/admin/drivers/edit/<driver_id>', methods=['GET', 'POST'])
def edit_driver(driver_id):
    driver = drivers_collection.find_one({"_id": ObjectId(driver_id)})
    if request.method == 'POST':
        drivers_collection.update_one({"_id": ObjectId(driver_id)}, {"$set": {
            "name": request.form['name'],
            "email": request.form['email'],
            "license": request.form['license'],
            "vehicle": request.form['vehicle']
        }})
        return redirect(url_for('admin_drivers'))
    return render_template("edit_driver.html", driver=driver)

@app.route('/admin/drivers/delete/<driver_id>')
def delete_driver(driver_id):
    drivers_collection.delete_one({"_id": ObjectId(driver_id)})
    return redirect(url_for('admin_drivers'))

@app.route('/admin/bookings')
def admin_bookings():
    search_query = request.args.get("search", "").strip().lower()
    bookings_cursor = bookings_collection.find().sort("created_at", -1)

    bookings = []
    for b in bookings_cursor:
        b["_id"] = str(b["_id"])
        
        # Attach user name from users collection
        user = users_collection.find_one({"email": b.get("user")})
        b["user_name"] = user.get("name", "Unknown") if user else "Unknown"

        # Ensure cancel_reason is present
        if "cancel_reason" not in b:
            b["cancel_reason"] = "No reason provided"

        # --- ✅ Filter here ---
        if search_query:
            if search_query in b["user_name"].lower() or search_query in b["user"].lower():
                bookings.append(b)
        else:
            bookings.append(b)

    return render_template("admin_bookings.html", bookings=bookings, search=search_query)

@app.route('/admin/bookings/cancel/<booking_id>', methods=['POST'])
def cancel_booking(booking_id):
    # Read selected reason
    cancel_reason = request.form.get("cancel_reason")
    other_reason = request.form.get("other_reason", "").strip()

    # If "Other" selected, override with custom reason
    if cancel_reason == "Other" and other_reason:
        reason = other_reason
    else:
        reason = cancel_reason or "No reason provided"

    booking = bookings_collection.find_one({"_id": ObjectId(booking_id)})
    if not booking:
        flash("Booking not found.", "danger")
        return redirect(url_for('admin_bookings'))

    # ✅ Mark booking as Cancelled with reason
    bookings_collection.update_one(
        {"_id": ObjectId(booking_id)},
        {"$set": {"status": "Cancelled", "cancel_reason": reason}}
    )

    # ✅ Update related payment safely
    payments_collection.update_one(
        {"booking_id": booking["_id"]},
        {"$set": {"status": "Refunded"}},
        upsert=False
    )

    # ✅ Find user details
    user = users_collection.find_one({"email": booking["user"]})
    if user:
        name = user.get("name", "Customer")
        phone = user.get("mobile")
        email = user.get("email")

        # --- SMS (Twilio) ---
        if phone:
            send_sms_via_twilio(
                f"+91{phone}",
                f"Dear {name}, your ride from {booking.get('pickup','-')} "
                f"to {booking.get('drop','-')} on {booking.get('date','-')} at {booking.get('time','-')} "
                f"has been CANCELLED.\nReason: {reason}"
            )

        # --- Email (Flask-Mail) ---
        # --- Email (Flask-Mail) ---
    if email:
     refund_text = ""
    if booking.get("final_fare", 0) > 0:
        refund_text = f"Refund: ✅ Your amount ₹{booking.get('final_fare', 0):.2f} has been refunded.\n"

    msg = Message(
        subject="Booking Cancelled - Invoice",
        recipients=[email],
        body=f"""
Dear {name},

Your ride has been CANCELLED by Admin.

Pickup: {booking.get('pickup', '-') }
Drop: {booking.get('drop', '-') }
Distance: {booking.get('distance', 0)} km
Vehicle Type: {booking.get('vehicle_type', '-') }
Date: {booking.get('date', '-') }
Time: {booking.get('time', '-') }

Fare Details:
Original Fare: ₹{booking.get('original_fare', 0):.2f}
Discount: ₹{booking.get('discount_amount', 0):.2f} ({booking.get('discount_percent', 0)}%)
Final Fare Paid: ₹{booking.get('final_fare', 0):.2f}
Promo Code: {booking.get('promo_code', '-')}

❌ Status: ADMIN CANCELLED
📝 Reason: {reason}
{refund_text}

Thank you,
Ride Booking Team
"""


    )
    mail.send(msg)


    flash("Booking cancelled successfully with reason sent to user.", "success")
    return redirect(url_for('admin_bookings'))
    
@app.route('/admin/bookings/invoice/<booking_id>')
def view_invoice(booking_id):
    try:
        # Fetch booking
        booking = bookings_collection.find_one({"_id": ObjectId(booking_id)})
        if not booking:
            flash("Booking not found.", "danger")
            return redirect(url_for('admin_bookings'))

        # Ensure numeric fields are floats
        booking['original_fare'] = float(booking.get('original_fare', 0))
        booking['discount_amount'] = float(booking.get('discount_amount', 0))
        booking['final_fare'] = float(booking.get('final_fare', 0))
        booking['discount_percent'] = int(booking.get('discount_percent', 0))

        # Fetch user info
        user = users_collection.find_one({"email": booking.get("user")})
        booking_email = user.get("email", "-") if user else booking.get("user", "-")
        user_name = user.get("name", booking.get("name", "-")) if user else booking.get("name", "-")

        # Fetch payment info
        payment = payments_collection.find_one({"booking_id": booking["_id"]})
        payment_method = payment.get("method", "-") if payment else "-"

        
        
        

        # Render invoice
        return render_template("invoice.html", booking={
            "name": user_name,
            "email": booking_email,
            "pickup": booking.get("pickup", "-"),
            "drop": booking.get("drop", "-"),
            "distance": booking.get("distance", 0),
            "vehicle_type": booking.get("vehicle_type", "-"),
            "original_fare": booking['original_fare'],
            "discount_amount": booking['discount_amount'],
            "discount_percent": booking['discount_percent'],
            "final_fare": booking['final_fare'],
            "promo_code": booking.get("promo_code", "-"),
            "payment_method": payment_method,
            "date": booking.get("date", "-"),
            "time": booking.get("time", "-"),
            "cancel_reason": booking.get("cancel_reason", "-"),
            "status": booking.get("status", "-")
          
        })

    except Exception as e:
        flash(f"Error fetching invoice: {e}", "danger")
        return redirect(url_for('admin_bookings'))


@app.route('/admin/ride-monitoring')
def ride_monitoring():
    # Only include active rides (exclude cancelled)
    rides = list(bookings_collection.find({"status": {"$ne": "Cancelled"}}).sort("created_at", -1))
    
    for r in rides:
        r["_id"] = str(r["_id"])  # JSON friendly
        # optional: ensure a location exists
        r.setdefault("driver_location", {"lat": 9.939093, "lng": 78.121719})
    
    return render_template(
        "ride_monitoring.html",
        rides=rides,
        google_api_key="AIzaSyBHHQPcdfSJBAbc0XUayV81ybnopaWWRBE"
    )


@app.route("/admin/fleet")
def admin_fleet():
    status = request.args.get("status")
    query = {}
    if status:
        query["status"] = status
    vehicles = list(fleet_collection.find(query))
    return render_template("admin_fleet.html", vehicles=vehicles)

# --- Add Vehicle ---
@app.route("/admin/add_vehicle", methods=["GET", "POST"])
def add_vehicle():
    if request.method == "POST":
        data = {
            "vehicle_no": request.form["vehicle_no"],
            "model": request.form["model"],
            "type": request.form["type"],
            "capacity": int(request.form["capacity"]),
            "insurance_expiry": request.form["insurance_expiry"],
            "fitness_expiry": request.form["fitness_expiry"],
            "driver_name": request.form.get("driver_name", "Unassigned"),
            "status": "Active"
        }
        fleet_collection.insert_one(data)
        return redirect(url_for("admin_fleet"))

    drivers = list(drivers_collection.find())
    return render_template("add_vehicle.html", drivers=drivers)


@app.route('/admin/fleet/edit/<vehicle_id>', methods=['GET', 'POST'])
def edit_vehicle(vehicle_id):
    vehicle = fleet_collection.find_one({"_id": ObjectId(vehicle_id)})
    if not vehicle:
        flash("❌ Vehicle not found.", "danger")
        return redirect(url_for('admin_fleet'))

    if request.method == 'POST':
        update_data = {
            "registration_no": request.form['registration_no'],
            "vehicle_type": request.form['vehicle_type'],
            "capacity": request.form['capacity'],
            "status": request.form['status'],
            "insurance_expiry": request.form['insurance_expiry'],
            "fitness_expiry": request.form['fitness_expiry'],
            "assigned_driver": request.form.get('assigned_driver', None),
        }
        fleet_collection.update_one({"_id": ObjectId(vehicle_id)}, {"$set": update_data})
        flash("✅ Vehicle updated successfully.", "success")
        return redirect(url_for('admin_fleet'))

    drivers = list(drivers_collection.find({"status": "Active"}))
    return render_template("edit_vehicle.html", vehicle=vehicle, drivers=drivers)

@app.route('/admin/fleet/delete/<vehicle_id>', methods=['POST'])
def delete_vehicle(vehicle_id):
    fleet_collection.delete_one({"_id": ObjectId(vehicle_id)})
    flash("✅ Vehicle deleted successfully.", "success")
    return redirect(url_for('admin_fleet'))

# -------------------------
# Run
# -------------------------
if __name__ == '__main__':
    app.run(debug=True)
