"""
DevMarket — Flask Backend
server.py
"""

import os
import uuid
import json
import hashlib
import hmac
import secrets
import re
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, request, jsonify, session, send_from_directory
from flask_cors import CORS
import psycopg2
from psycopg2.extras import RealDictCursor
import cloudinary
import cloudinary.uploader

# ──────────────────────────────────────────────
# APP CONFIG
# ──────────────────────────────────────────────
app = Flask(__name__, static_folder=".", static_url_path="")
app.secret_key = os.getenv("SECRET_KEY", secrets.token_hex(32))
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("FLASK_ENV") == "production",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)

CORS(app, supports_credentials=True, origins=os.getenv("ALLOWED_ORIGINS", "*").split(","))

# Cloudinary
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME", "ddusfl7pi"),
    api_key=os.getenv("CLOUDINARY_API_KEY", ""),
    api_secret=os.getenv("CLOUDINARY_API_SECRET", ""),
)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost/devmarket")

# ──────────────────────────────────────────────
# DB
# ──────────────────────────────────────────────
def get_db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    return conn


def query(sql, params=None, fetch="all"):
    conn = get_db()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, params or [])
                if fetch == "one":
                    return cur.fetchone()
                elif fetch == "all":
                    return cur.fetchall()
                elif fetch == "none":
                    return None
                elif fetch == "lastrow":
                    return cur.fetchone()
    finally:
        conn.close()


def init_db():
    conn = get_db()
    with conn:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE EXTENSION IF NOT EXISTS "pgcrypto";

            CREATE TABLE IF NOT EXISTS users (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                first_name VARCHAR(80) NOT NULL,
                last_name VARCHAR(80) NOT NULL,
                username VARCHAR(40) UNIQUE NOT NULL,
                email VARCHAR(255) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                bio TEXT,
                avatar_url TEXT,
                website VARCHAR(255),
                github VARCHAR(100),
                twitter VARCHAR(100),
                location VARCHAR(100),
                total_sales INTEGER DEFAULT 0,
                avg_rating FLOAT DEFAULT 0,
                stripe_account_id VARCHAR(255),
                reset_token VARCHAR(255),
                reset_token_expires TIMESTAMPTZ,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS listings (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                seller_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title VARCHAR(200) NOT NULL,
                short_description VARCHAR(200),
                description TEXT NOT NULL,
                category VARCHAR(100) NOT NULL,
                price NUMERIC(10,2) DEFAULT 0,
                license VARCHAR(50) DEFAULT 'personal',
                demo_url VARCHAR(500),
                tags JSONB DEFAULT '[]',
                features JSONB DEFAULT '[]',
                images JSONB DEFAULT '[]',
                file_url TEXT,
                file_key TEXT,
                status VARCHAR(20) DEFAULT 'active',
                total_sales INTEGER DEFAULT 0,
                review_count INTEGER DEFAULT 0,
                avg_rating FLOAT DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS orders (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                listing_id UUID NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
                buyer_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                seller_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                amount NUMERIC(10,2) NOT NULL,
                currency VARCHAR(10) DEFAULT 'USD',
                status VARCHAR(30) DEFAULT 'completed',
                stripe_payment_id VARCHAR(255),
                download_count INTEGER DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS reviews (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                listing_id UUID NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
                reviewer_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                order_id UUID REFERENCES orders(id) ON DELETE SET NULL,
                rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
                title VARCHAR(200),
                body TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(listing_id, reviewer_id)
            );

            CREATE TABLE IF NOT EXISTS conversations (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_a UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                user_b UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                last_message TEXT,
                last_message_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(user_a, user_b)
            );

            CREATE TABLE IF NOT EXISTS messages (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                sender_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                body TEXT NOT NULL,
                is_read BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS notifications (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                type VARCHAR(50) NOT NULL,
                message TEXT NOT NULL,
                related_id UUID,
                is_read BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS idx_listings_seller ON listings(seller_id);
            CREATE INDEX IF NOT EXISTS idx_listings_category ON listings(category);
            CREATE INDEX IF NOT EXISTS idx_listings_status ON listings(status);
            CREATE INDEX IF NOT EXISTS idx_orders_buyer ON orders(buyer_id);
            CREATE INDEX IF NOT EXISTS idx_orders_seller ON orders(seller_id);
            CREATE INDEX IF NOT EXISTS idx_messages_convo ON messages(conversation_id);
            CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id);
            """)
    conn.close()
    print("✅ Database initialized")


# ──────────────────────────────────────────────
# AUTH HELPERS
# ──────────────────────────────────────────────
def hash_password(pw):
    salt = secrets.token_hex(16)
    h = hashlib.sha256((salt + pw).encode()).hexdigest()
    return f"{salt}:{h}"


def verify_password(pw, stored):
    try:
        salt, h = stored.split(":", 1)
        return hmac.compare_digest(h, hashlib.sha256((salt + pw).encode()).hexdigest())
    except Exception:
        return False


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "Authentication required"}), 401
        return f(*args, **kwargs)
    return decorated


def safe_user(u):
    if not u:
        return None
    return {
        "id": str(u["id"]),
        "first_name": u["first_name"],
        "last_name": u["last_name"],
        "username": u["username"],
        "email": u["email"],
        "bio": u["bio"],
        "avatar_url": u["avatar_url"],
        "website": u["website"],
        "github": u["github"],
        "twitter": u["twitter"],
        "location": u["location"],
        "total_sales": u["total_sales"],
        "avg_rating": float(u["avg_rating"] or 0),
        "created_at": u["created_at"].isoformat() if u["created_at"] else None,
    }


def format_listing(l, buyer_id=None):
    if not l:
        return None
    d = dict(l)
    d["id"] = str(d["id"])
    d["seller_id"] = str(d["seller_id"])
    d["price"] = float(d["price"] or 0)
    d["avg_rating"] = float(d["avg_rating"] or 0)
    for f in ["created_at", "updated_at"]:
        if d.get(f):
            d[f] = d[f].isoformat()
    for f in ["tags", "features", "images"]:
        if isinstance(d.get(f), str):
            try:
                d[f] = json.loads(d[f])
            except Exception:
                d[f] = []
    if buyer_id:
        order = query(
            "SELECT id FROM orders WHERE listing_id=%s AND buyer_id=%s",
            [d["id"], buyer_id],
            fetch="one",
        )
        d["has_purchased"] = order is not None
    else:
        d["has_purchased"] = False
    return d


def notify(user_id, type_, message, related_id=None):
    try:
        query(
            "INSERT INTO notifications (user_id, type, message, related_id) VALUES (%s,%s,%s,%s)",
            [str(user_id), type_, message, str(related_id) if related_id else None],
            fetch="none",
        )
    except Exception as e:
        print("Notify error:", e)


# ──────────────────────────────────────────────
# SERVE INDEX
# ──────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(".", "index.html")


# ──────────────────────────────────────────────
# AUTH ROUTES
# ──────────────────────────────────────────────
@app.route("/api/auth/signup", methods=["POST"])
def signup():
    d = request.get_json() or {}
    first_name = d.get("first_name", "").strip()
    last_name = d.get("last_name", "").strip()
    username = d.get("username", "").strip().lower()
    email = d.get("email", "").strip().lower()
    password = d.get("password", "")

    if not all([first_name, last_name, username, email, password]):
        return jsonify({"error": "All fields are required"}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400
    if not re.match(r"^[a-z0-9_]{3,20}$", username):
        return jsonify({"error": "Username: 3-20 chars, letters/numbers/underscores only"}), 400
    if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
        return jsonify({"error": "Invalid email address"}), 400

    existing = query("SELECT id FROM users WHERE email=%s OR username=%s", [email, username], fetch="one")
    if existing:
        return jsonify({"error": "Email or username already taken"}), 409

    pw_hash = hash_password(password)
    user = query(
        """INSERT INTO users (first_name, last_name, username, email, password_hash)
           VALUES (%s,%s,%s,%s,%s) RETURNING *""",
        [first_name, last_name, username, email, pw_hash],
        fetch="one",
    )
    session.permanent = True
    session["user_id"] = str(user["id"])
    return jsonify({"user": safe_user(user)}), 201


@app.route("/api/auth/signin", methods=["POST"])
def signin():
    d = request.get_json() or {}
    email = d.get("email", "").strip().lower()
    password = d.get("password", "")
    if not email or not password:
        return jsonify({"error": "Email and password required"}), 400

    user = query("SELECT * FROM users WHERE email=%s AND is_active=TRUE", [email], fetch="one")
    if not user or not verify_password(password, user["password_hash"]):
        return jsonify({"error": "Invalid email or password"}), 401

    session.permanent = True
    session["user_id"] = str(user["id"])
    return jsonify({"user": safe_user(user)})


@app.route("/api/auth/me")
def me():
    if "user_id" not in session:
        return jsonify({"error": "Not authenticated"}), 401
    user = query("SELECT * FROM users WHERE id=%s", [session["user_id"]], fetch="one")
    if not user:
        session.clear()
        return jsonify({"error": "User not found"}), 404
    return jsonify({"user": safe_user(user)})


@app.route("/api/auth/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"message": "Logged out"})


@app.route("/api/auth/change-password", methods=["POST"])
@login_required
def change_password():
    d = request.get_json() or {}
    current = d.get("current_password", "")
    new_pw = d.get("new_password", "")
    if not current or not new_pw:
        return jsonify({"error": "Both passwords required"}), 400
    if len(new_pw) < 8:
        return jsonify({"error": "New password must be at least 8 characters"}), 400
    user = query("SELECT * FROM users WHERE id=%s", [session["user_id"]], fetch="one")
    if not verify_password(current, user["password_hash"]):
        return jsonify({"error": "Current password is incorrect"}), 401
    query(
        "UPDATE users SET password_hash=%s, updated_at=NOW() WHERE id=%s",
        [hash_password(new_pw), session["user_id"]],
        fetch="none",
    )
    return jsonify({"message": "Password updated"})


@app.route("/api/auth/forgot-password", methods=["POST"])
def forgot_password():
    d = request.get_json() or {}
    email = d.get("email", "").strip().lower()
    if not email:
        return jsonify({"error": "Email required"}), 400
    user = query("SELECT id FROM users WHERE email=%s", [email], fetch="one")
    if user:
        token = secrets.token_urlsafe(32)
        expires = datetime.utcnow() + timedelta(hours=1)
        query(
            "UPDATE users SET reset_token=%s, reset_token_expires=%s WHERE id=%s",
            [token, expires, str(user["id"])],
            fetch="none",
        )
        # TODO: Send email with reset link
        print(f"Password reset token for {email}: {token}")
    return jsonify({"message": "If that email exists, a reset link has been sent."})


# ──────────────────────────────────────────────
# LISTINGS
# ──────────────────────────────────────────────
@app.route("/api/listings", methods=["GET"])
def get_listings():
    sort = request.args.get("sort", "newest")
    category = request.args.get("category", "")
    q = request.args.get("q", "").strip()
    min_price = request.args.get("min_price")
    max_price = request.args.get("max_price")
    page = max(1, int(request.args.get("page", 1)))
    limit = min(50, int(request.args.get("limit", 9)))
    offset = (page - 1) * limit

    sorts = {
        "newest": "l.created_at DESC",
        "popular": "l.total_sales DESC",
        "rating": "l.avg_rating DESC",
        "price-asc": "l.price ASC",
        "price-desc": "l.price DESC",
    }
    order_by = sorts.get(sort, "l.created_at DESC")

    conditions = ["l.status='active'"]
    params = []

    if category:
        conditions.append("l.category=%s")
        params.append(category)
    if q:
        conditions.append("(l.title ILIKE %s OR l.short_description ILIKE %s OR l.description ILIKE %s)")
        like = f"%{q}%"
        params.extend([like, like, like])
    if min_price is not None:
        conditions.append("l.price>=%s")
        params.append(float(min_price))
    if max_price is not None:
        conditions.append("l.price<=%s")
        params.append(float(max_price))

    where = " AND ".join(conditions)

    count_row = query(
        f"""SELECT COUNT(*) as cnt FROM listings l WHERE {where}""",
        params, fetch="one"
    )
    total = count_row["cnt"] if count_row else 0

    rows = query(
        f"""SELECT l.*,
               u.first_name || ' ' || u.last_name AS seller_name,
               u.avatar_url AS seller_avatar,
               u.avg_rating AS seller_rating,
               u.total_sales AS seller_sales
            FROM listings l
            JOIN users u ON u.id=l.seller_id
            WHERE {where}
            ORDER BY {order_by}
            LIMIT %s OFFSET %s""",
        params + [limit, offset],
    )
    buyer_id = session.get("user_id")
    listings = [format_listing(r, buyer_id) for r in rows]
    return jsonify({"listings": listings, "total": total, "page": page, "per_page": limit})


@app.route("/api/listings", methods=["POST"])
@login_required
def create_listing():
    seller_id = session["user_id"]
    title = request.form.get("title", "").strip()
    short_desc = request.form.get("short_description", "").strip()
    description = request.form.get("description", "").strip()
    category = request.form.get("category", "").strip()
    price = float(request.form.get("price", 0) or 0)
    license_ = request.form.get("license", "personal")
    demo_url = request.form.get("demo_url", "").strip()
    tags = json.loads(request.form.get("tags", "[]"))
    features = json.loads(request.form.get("features", "[]"))

    if not title or not description or not category:
        return jsonify({"error": "Title, description, and category are required"}), 400

    # Upload images
    image_urls = []
    files = request.files.getlist("images")
    for f in files[:6]:
        if f and f.filename:
            result = cloudinary.uploader.upload(
                f, folder="devmarket/listings", resource_type="image"
            )
            image_urls.append(result["secure_url"])

    # Upload product file
    file_url = None
    file_key = None
    pf = request.files.get("product_file")
    if pf and pf.filename:
        result = cloudinary.uploader.upload(
            pf, folder="devmarket/files", resource_type="raw"
        )
        file_url = result["secure_url"]
        file_key = result["public_id"]

    listing = query(
        """INSERT INTO listings
           (seller_id, title, short_description, description, category, price,
            license, demo_url, tags, features, images, file_url, file_key)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
        [
            seller_id, title, short_desc, description, category, price,
            license_, demo_url,
            json.dumps(tags), json.dumps(features), json.dumps(image_urls),
            file_url, file_key,
        ],
        fetch="one",
    )
    return jsonify({"listing": format_listing(listing)}), 201


@app.route("/api/listings/mine")
@login_required
def my_listings():
    rows = query(
        """SELECT l.*,
               u.first_name || ' ' || u.last_name AS seller_name,
               u.avatar_url AS seller_avatar,
               u.avg_rating AS seller_rating,
               u.total_sales AS seller_sales
            FROM listings l JOIN users u ON u.id=l.seller_id
            WHERE l.seller_id=%s ORDER BY l.created_at DESC""",
        [session["user_id"]],
    )
    return jsonify({"listings": [format_listing(r) for r in rows]})


@app.route("/api/listings/<listing_id>", methods=["GET"])
def get_listing(listing_id):
    row = query(
        """SELECT l.*,
               u.first_name || ' ' || u.last_name AS seller_name,
               u.avatar_url AS seller_avatar,
               u.avg_rating AS seller_rating,
               u.total_sales AS seller_sales
            FROM listings l JOIN users u ON u.id=l.seller_id
            WHERE l.id=%s""",
        [listing_id], fetch="one"
    )
    if not row:
        return jsonify({"error": "Listing not found"}), 404
    listing = format_listing(row, session.get("user_id"))

    # Reviews
    reviews = query(
        """SELECT r.*, u.first_name || ' ' || u.last_name AS reviewer_name, u.avatar_url AS reviewer_avatar
           FROM reviews r JOIN users u ON u.id=r.reviewer_id
           WHERE r.listing_id=%s ORDER BY r.created_at DESC""",
        [listing_id]
    )
    listing["reviews"] = [
        {**dict(rv), "id": str(rv["id"]), "listing_id": str(rv["listing_id"]),
         "reviewer_id": str(rv["reviewer_id"]),
         "created_at": rv["created_at"].isoformat() if rv["created_at"] else None}
        for rv in reviews
    ]
    return jsonify({"listing": listing})


@app.route("/api/listings/<listing_id>", methods=["DELETE"])
@login_required
def delete_listing(listing_id):
    row = query("SELECT seller_id FROM listings WHERE id=%s", [listing_id], fetch="one")
    if not row:
        return jsonify({"error": "Not found"}), 404
    if str(row["seller_id"]) != session["user_id"]:
        return jsonify({"error": "Not authorized"}), 403
    query("DELETE FROM listings WHERE id=%s", [listing_id], fetch="none")
    return jsonify({"message": "Deleted"})


@app.route("/api/listings/<listing_id>/download")
@login_required
def download_listing(listing_id):
    buyer_id = session["user_id"]
    order = query(
        "SELECT id FROM orders WHERE listing_id=%s AND buyer_id=%s",
        [listing_id, buyer_id], fetch="one"
    )
    listing = query("SELECT file_url, seller_id FROM listings WHERE id=%s", [listing_id], fetch="one")
    if not listing:
        return jsonify({"error": "Listing not found"}), 404
    if not order and str(listing["seller_id"]) != buyer_id:
        return jsonify({"error": "Purchase required to download"}), 403
    if not listing["file_url"]:
        return jsonify({"error": "No file available for this listing"}), 404
    # Increment download count
    query("UPDATE orders SET download_count=download_count+1 WHERE listing_id=%s AND buyer_id=%s",
          [listing_id, buyer_id], fetch="none")
    return jsonify({"download_url": listing["file_url"]})


# ──────────────────────────────────────────────
# PAYMENTS
# ──────────────────────────────────────────────
@app.route("/api/payments/checkout", methods=["POST"])
@login_required
def checkout():
    d = request.get_json() or {}
    listing_id = d.get("listing_id")
    buyer_id = session["user_id"]

    listing = query("SELECT * FROM listings WHERE id=%s AND status='active'", [listing_id], fetch="one")
    if not listing:
        return jsonify({"error": "Listing not found"}), 404
    if str(listing["seller_id"]) == buyer_id:
        return jsonify({"error": "You cannot purchase your own listing"}), 400

    # Check already purchased
    existing = query(
        "SELECT id FROM orders WHERE listing_id=%s AND buyer_id=%s", [listing_id, buyer_id], fetch="one"
    )
    if existing:
        return jsonify({"error": "You already own this product"}), 400

    price = float(listing["price"])
    if price <= 0:
        return jsonify({"error": "Use the free claim endpoint for free products"}), 400

    # ── Stripe Integration Point ──
    # In production, create a PaymentIntent here:
    # import stripe
    # stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
    # pi = stripe.PaymentIntent.create(amount=int(price*100), currency='usd', ...)
    # Then confirm with card details via stripe.js on frontend
    # For now we simulate a successful charge:
    stripe_payment_id = "pi_simulated_" + secrets.token_hex(8)

    order = query(
        """INSERT INTO orders (listing_id, buyer_id, seller_id, amount, stripe_payment_id)
           VALUES (%s,%s,%s,%s,%s) RETURNING *""",
        [listing_id, buyer_id, str(listing["seller_id"]), price, stripe_payment_id],
        fetch="one"
    )

    # Update listing stats
    query(
        "UPDATE listings SET total_sales=total_sales+1 WHERE id=%s",
        [listing_id], fetch="none"
    )
    # Update seller stats
    query(
        "UPDATE users SET total_sales=total_sales+1 WHERE id=%s",
        [str(listing["seller_id"])], fetch="none"
    )

    # Notify seller
    buyer = query("SELECT first_name, last_name FROM users WHERE id=%s", [buyer_id], fetch="one")
    buyer_name = f"{buyer['first_name']} {buyer['last_name']}" if buyer else "A buyer"
    notify(
        listing["seller_id"], "sale",
        f"{buyer_name} purchased '{listing['title']}' for ${price:.2f}",
        listing_id
    )
    # Notify buyer
    notify(
        buyer_id, "purchase",
        f"You successfully purchased '{listing['title']}'. Go to dashboard to download.",
        listing_id
    )

    return jsonify({"order_id": str(order["id"]), "message": "Purchase successful"})


@app.route("/api/payments/free", methods=["POST"])
@login_required
def claim_free():
    d = request.get_json() or {}
    listing_id = d.get("listing_id")
    buyer_id = session["user_id"]

    listing = query("SELECT * FROM listings WHERE id=%s AND status='active'", [listing_id], fetch="one")
    if not listing:
        return jsonify({"error": "Listing not found"}), 404
    if float(listing["price"]) > 0:
        return jsonify({"error": "This product is not free"}), 400
    if str(listing["seller_id"]) == buyer_id:
        return jsonify({"error": "You cannot claim your own listing"}), 400

    existing = query(
        "SELECT id FROM orders WHERE listing_id=%s AND buyer_id=%s", [listing_id, buyer_id], fetch="one"
    )
    if existing:
        return jsonify({"error": "Already in your library"}), 400

    query(
        "INSERT INTO orders (listing_id, buyer_id, seller_id, amount) VALUES (%s,%s,%s,0)",
        [listing_id, buyer_id, str(listing["seller_id"])], fetch="none"
    )
    query("UPDATE listings SET total_sales=total_sales+1 WHERE id=%s", [listing_id], fetch="none")
    return jsonify({"message": "Added to your library"})


# ──────────────────────────────────────────────
# ORDERS
# ──────────────────────────────────────────────
@app.route("/api/orders/mine")
@login_required
def my_purchases():
    rows = query(
        """SELECT o.*, l.title AS listing_title,
               u.first_name || ' ' || u.last_name AS seller_name,
               (SELECT 1 FROM reviews r WHERE r.listing_id=o.listing_id AND r.reviewer_id=o.buyer_id) IS NOT NULL AS reviewed
            FROM orders o
            JOIN listings l ON l.id=o.listing_id
            JOIN users u ON u.id=o.seller_id
            WHERE o.buyer_id=%s ORDER BY o.created_at DESC""",
        [session["user_id"]]
    )
    orders = []
    for r in rows:
        d = dict(r)
        for k in ["id", "listing_id", "buyer_id", "seller_id"]:
            if d.get(k):
                d[k] = str(d[k])
        d["amount"] = float(d["amount"])
        if d.get("created_at"):
            d["created_at"] = d["created_at"].isoformat()
        orders.append(d)
    return jsonify({"orders": orders})


@app.route("/api/orders/received")
@login_required
def received_orders():
    rows = query(
        """SELECT o.*, l.title AS listing_title,
               u.first_name || ' ' || u.last_name AS buyer_name
            FROM orders o
            JOIN listings l ON l.id=o.listing_id
            JOIN users u ON u.id=o.buyer_id
            WHERE o.seller_id=%s ORDER BY o.created_at DESC""",
        [session["user_id"]]
    )
    orders = []
    for r in rows:
        d = dict(r)
        for k in ["id", "listing_id", "buyer_id", "seller_id"]:
            if d.get(k):
                d[k] = str(d[k])
        d["amount"] = float(d["amount"])
        if d.get("created_at"):
            d["created_at"] = d["created_at"].isoformat()
        orders.append(d)
    return jsonify({"orders": orders})


# ──────────────────────────────────────────────
# REVIEWS
# ──────────────────────────────────────────────
@app.route("/api/reviews", methods=["POST"])
@login_required
def post_review():
    d = request.get_json() or {}
    listing_id = d.get("listing_id")
    rating = int(d.get("rating", 0))
    title = d.get("title", "").strip()
    body = d.get("body", "").strip()

    if not listing_id or not rating or not body:
        return jsonify({"error": "listing_id, rating, and body are required"}), 400
    if not 1 <= rating <= 5:
        return jsonify({"error": "Rating must be 1–5"}), 400

    # Verify purchase
    order = query(
        "SELECT id FROM orders WHERE listing_id=%s AND buyer_id=%s",
        [listing_id, session["user_id"]], fetch="one"
    )
    if not order:
        return jsonify({"error": "Purchase required to review"}), 403

    try:
        review = query(
            """INSERT INTO reviews (listing_id, reviewer_id, order_id, rating, title, body)
               VALUES (%s,%s,%s,%s,%s,%s) RETURNING *""",
            [listing_id, session["user_id"], str(order["id"]), rating, title, body],
            fetch="one"
        )
    except Exception as e:
        if "unique" in str(e).lower():
            # Update existing
            review = query(
                "UPDATE reviews SET rating=%s, title=%s, body=%s WHERE listing_id=%s AND reviewer_id=%s RETURNING *",
                [rating, title, body, listing_id, session["user_id"]], fetch="one"
            )
        else:
            return jsonify({"error": str(e)}), 500

    # Recalculate avg rating
    agg = query(
        "SELECT AVG(rating) as avg, COUNT(*) as cnt FROM reviews WHERE listing_id=%s",
        [listing_id], fetch="one"
    )
    query(
        "UPDATE listings SET avg_rating=%s, review_count=%s WHERE id=%s",
        [float(agg["avg"] or 0), agg["cnt"], listing_id], fetch="none"
    )

    listing = query("SELECT seller_id, title FROM listings WHERE id=%s", [listing_id], fetch="one")
    if listing:
        reviewer = query("SELECT first_name, last_name FROM users WHERE id=%s", [session["user_id"]], fetch="one")
        name = f"{reviewer['first_name']} {reviewer['last_name']}" if reviewer else "A user"
        notify(listing["seller_id"], "review",
               f"{name} left a {rating}★ review on '{listing['title']}'", listing_id)

    return jsonify({"message": "Review submitted"}), 201


# ──────────────────────────────────────────────
# MESSAGES
# ──────────────────────────────────────────────
@app.route("/api/messages/start", methods=["POST"])
@login_required
def start_conversation():
    d = request.get_json() or {}
    other_id = d.get("other_user_id")
    if not other_id:
        return jsonify({"error": "other_user_id required"}), 400
    me = session["user_id"]
    if me == other_id:
        return jsonify({"error": "Cannot message yourself"}), 400

    a, b = sorted([me, other_id])
    convo = query(
        "SELECT id FROM conversations WHERE user_a=%s AND user_b=%s", [a, b], fetch="one"
    )
    if not convo:
        convo = query(
            "INSERT INTO conversations (user_a, user_b) VALUES (%s,%s) RETURNING id",
            [a, b], fetch="one"
        )
    return jsonify({"conversation_id": str(convo["id"])})


@app.route("/api/messages/conversations")
@login_required
def get_conversations():
    me = session["user_id"]
    rows = query(
        """SELECT c.*,
               CASE WHEN c.user_a=%s THEN c.user_b ELSE c.user_a END AS other_user_id,
               CASE WHEN c.user_a=%s THEN ub.first_name || ' ' || ub.last_name
                    ELSE ua.first_name || ' ' || ua.last_name END AS other_user_name,
               CASE WHEN c.user_a=%s THEN ub.avatar_url ELSE ua.avatar_url END AS other_user_avatar,
               (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id AND m.sender_id!=cast(%s as uuid) AND m.is_read=FALSE) AS unread_count
            FROM conversations c
            JOIN users ua ON ua.id=c.user_a
            JOIN users ub ON ub.id=c.user_b
            WHERE c.user_a=%s OR c.user_b=%s
            ORDER BY c.last_message_at DESC NULLS LAST""",
        [me, me, me, me, me, me]
    )
    convos = []
    for r in rows:
        d = dict(r)
        for k in ["id", "user_a", "user_b", "other_user_id"]:
            if d.get(k):
                d[k] = str(d[k])
        if d.get("last_message_at"):
            d["last_message_at"] = d["last_message_at"].isoformat()
        convos.append(d)
    return jsonify({"conversations": convos})


@app.route("/api/messages/<convo_id>", methods=["GET"])
@login_required
def get_messages(convo_id):
    me = session["user_id"]
    convo = query(
        "SELECT * FROM conversations WHERE id=%s AND (user_a=%s OR user_b=%s)",
        [convo_id, me, me], fetch="one"
    )
    if not convo:
        return jsonify({"error": "Conversation not found"}), 404
    # Mark as read
    query(
        "UPDATE messages SET is_read=TRUE WHERE conversation_id=%s AND sender_id!=%s",
        [convo_id, me], fetch="none"
    )
    rows = query(
        """SELECT m.*, u.first_name || ' ' || u.last_name AS sender_name
           FROM messages m JOIN users u ON u.id=m.sender_id
           WHERE m.conversation_id=%s ORDER BY m.created_at ASC""",
        [convo_id]
    )
    msgs = []
    for r in rows:
        d = dict(r)
        for k in ["id", "conversation_id", "sender_id"]:
            if d.get(k):
                d[k] = str(d[k])
        if d.get("created_at"):
            d["created_at"] = d["created_at"].isoformat()
        msgs.append(d)
    return jsonify({"messages": msgs})


@app.route("/api/messages/<convo_id>", methods=["POST"])
@login_required
def send_message(convo_id):
    me = session["user_id"]
    d = request.get_json() or {}
    body = d.get("body", "").strip()
    if not body:
        return jsonify({"error": "Message body required"}), 400

    convo = query(
        "SELECT * FROM conversations WHERE id=%s AND (user_a=%s OR user_b=%s)",
        [convo_id, me, me], fetch="one"
    )
    if not convo:
        return jsonify({"error": "Conversation not found"}), 404

    msg = query(
        "INSERT INTO messages (conversation_id, sender_id, body) VALUES (%s,%s,%s) RETURNING *",
        [convo_id, me, body], fetch="one"
    )
    query(
        "UPDATE conversations SET last_message=%s, last_message_at=NOW() WHERE id=%s",
        [body[:100], convo_id], fetch="none"
    )

    # Notify recipient
    other_id = str(convo["user_b"]) if str(convo["user_a"]) == me else str(convo["user_a"])
    sender = query("SELECT first_name FROM users WHERE id=%s", [me], fetch="one")
    sender_name = sender["first_name"] if sender else "Someone"
    notify(other_id, "msg", f"{sender_name} sent you a message: {body[:60]}", convo_id)

    return jsonify({"message": "Sent"})


# ──────────────────────────────────────────────
# USERS / PROFILE
# ──────────────────────────────────────────────
@app.route("/api/users/<user_id>")
def get_user_profile(user_id):
    user = query("SELECT * FROM users WHERE id=%s AND is_active=TRUE", [user_id], fetch="one")
    if not user:
        return jsonify({"error": "User not found"}), 404
    listings = query(
        """SELECT l.*,
               u.first_name || ' ' || u.last_name AS seller_name,
               u.avatar_url AS seller_avatar,
               u.avg_rating AS seller_rating,
               u.total_sales AS seller_sales
            FROM listings l JOIN users u ON u.id=l.seller_id
            WHERE l.seller_id=%s AND l.status='active' ORDER BY l.created_at DESC""",
        [user_id]
    )
    return jsonify({
        "user": safe_user(user),
        "listings": [format_listing(r) for r in listings]
    })


@app.route("/api/users/me", methods=["PUT"])
@login_required
def update_profile():
    d = request.get_json() or {}
    allowed = ["first_name", "last_name", "username", "bio", "website", "github", "twitter", "location"]
    updates = {k: v for k, v in d.items() if k in allowed}
    if not updates:
        return jsonify({"error": "Nothing to update"}), 400

    if "username" in updates:
        ex = query(
            "SELECT id FROM users WHERE username=%s AND id!=%s",
            [updates["username"], session["user_id"]], fetch="one"
        )
        if ex:
            return jsonify({"error": "Username already taken"}), 409

    set_clause = ", ".join(f"{k}=%s" for k in updates)
    values = list(updates.values()) + [session["user_id"]]
    user = query(
        f"UPDATE users SET {set_clause}, updated_at=NOW() WHERE id=%s RETURNING *",
        values, fetch="one"
    )
    return jsonify({"user": safe_user(user)})


@app.route("/api/users/me", methods=["DELETE"])
@login_required
def delete_account():
    query("UPDATE users SET is_active=FALSE WHERE id=%s", [session["user_id"]], fetch="none")
    session.clear()
    return jsonify({"message": "Account deleted"})


# ──────────────────────────────────────────────
# NOTIFICATIONS
# ──────────────────────────────────────────────
@app.route("/api/notifications")
@login_required
def get_notifications():
    rows = query(
        "SELECT * FROM notifications WHERE user_id=%s ORDER BY created_at DESC LIMIT 50",
        [session["user_id"]]
    )
    notifs = []
    for r in rows:
        d = dict(r)
        d["id"] = str(d["id"])
        d["user_id"] = str(d["user_id"])
        if d.get("related_id"):
            d["related_id"] = str(d["related_id"])
        if d.get("created_at"):
            d["created_at"] = d["created_at"].isoformat()
        notifs.append(d)
    return jsonify({"notifications": notifs})


@app.route("/api/notifications/unread-count")
@login_required
def unread_count():
    row = query(
        "SELECT COUNT(*) as cnt FROM notifications WHERE user_id=%s AND is_read=FALSE",
        [session["user_id"]], fetch="one"
    )
    return jsonify({"count": row["cnt"] if row else 0})


@app.route("/api/notifications/<notif_id>/read", methods=["POST"])
@login_required
def mark_read(notif_id):
    query(
        "UPDATE notifications SET is_read=TRUE WHERE id=%s AND user_id=%s",
        [notif_id, session["user_id"]], fetch="none"
    )
    return jsonify({"message": "Marked as read"})


@app.route("/api/notifications/read-all", methods=["POST"])
@login_required
def mark_all_read():
    query(
        "UPDATE notifications SET is_read=TRUE WHERE user_id=%s",
        [session["user_id"]], fetch="none"
    )
    return jsonify({"message": "All marked as read"})


# ──────────────────────────────────────────────
# DASHBOARD STATS
# ──────────────────────────────────────────────
@app.route("/api/dashboard/stats")
@login_required
def dashboard_stats():
    me = session["user_id"]
    revenue = query(
        "SELECT COALESCE(SUM(amount),0) as total FROM orders WHERE seller_id=%s", [me], fetch="one"
    )
    sales = query(
        "SELECT COUNT(*) as cnt FROM orders WHERE seller_id=%s", [me], fetch="one"
    )
    listings = query(
        "SELECT COUNT(*) as cnt FROM listings WHERE seller_id=%s AND status='active'", [me], fetch="one"
    )
    rating = query(
        "SELECT COALESCE(AVG(avg_rating),0) as avg FROM listings WHERE seller_id=%s AND review_count>0",
        [me], fetch="one"
    )
    return jsonify({
        "stats": {
            "revenue": float(revenue["total"] or 0),
            "sales": sales["cnt"] or 0,
            "listings": listings["cnt"] or 0,
            "avg_rating": float(rating["avg"] or 0),
        }
    })


# ──────────────────────────────────────────────
# HEALTH
# ──────────────────────────────────────────────
@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "service": "DevMarket API"})


# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_ENV") != "production"
    print(f"🚀 DevMarket running on http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=debug)
