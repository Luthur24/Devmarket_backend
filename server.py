import os
import jwt
import bcrypt
import psycopg2
import cloudinary
import cloudinary.uploader
import stripe

from flask import Flask, request, jsonify, g
from flask_cors import CORS
from datetime import datetime, timedelta
from functools import wraps

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv
import os
port = int(os.environ.get("PORT", 10000))


load_dotenv()

app = Flask(__name__)
CORS(app, resources={
    r"/*": {
        "origins": [
            "https://luthur24.github.io",
            "https://luthur24.github.io/Devmarket_frontend",
            "https://devmarket-backend-2j8j.onrender.com"
        ],
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"]
    }
})

limiter = Limiter(get_remote_address, app=app, default_limits=["200 per day", "50 per hour"])

# ==================== CONFIG ====================

SECRET_KEY = os.getenv("SECRET_KEY", "change-this-secret-in-production")

# CORRECT (external - works from anywhere)
DATABASE_URL = "postgresql://trends_db_7j0m_user:pWD8LVVvyhlTWhNFVxArwz4wyBeUS25n@dpg-d6g84sdm5p6s739m65v0-a.frankfurt-postgres.render.com/trends_db_7j0m"

# Cloudinary Config
CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME", "ddusfl7pi")
CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY", "599965682593626")
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET", "pUcb90_1jtv-rDlHXRRsfDcBK5k")

cloudinary.config(
    cloud_name=CLOUDINARY_CLOUD_NAME,
    api_key=CLOUDINARY_API_KEY,
    api_secret=CLOUDINARY_API_SECRET
)

# Stripe Config
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "sk_test_your_key_here")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "whsec_your_webhook_secret")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5000")

stripe.api_key = STRIPE_SECRET_KEY

# ==================== DATABASE ====================

def get_db():
    if "db" not in g:
        g.db = psycopg2.connect(DATABASE_URL)
        g.cursor = g.db.cursor()
    return g.cursor


@app.teardown_appcontext
def close_db(error):
    db = g.pop("db", None)
    if db:
        db.close()


def init_tables():
    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor()

    tables = [
        # Users table - added stripe_account_id, total_earnings
        """
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username VARCHAR(50) UNIQUE NOT NULL,
            email VARCHAR(100) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            avatar_url TEXT,
            bio TEXT DEFAULT '',
            stripe_account_id VARCHAR(255),
            total_earnings DECIMAL(10,2) DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        
        # Posts table - added file_url, view_count, sales_count
        """
        CREATE TABLE IF NOT EXISTS posts (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            title VARCHAR(255) NOT NULL,
            description TEXT,
            price DECIMAL(10,2) DEFAULT 0,
            media_urls TEXT[] DEFAULT '{}',
            file_url TEXT,
            tags TEXT[] DEFAULT '{}',
            view_count INTEGER DEFAULT 0,
            sales_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        
        # Comments table with nested replies support
        """
        CREATE TABLE IF NOT EXISTS comments (
            id SERIAL PRIMARY KEY,
            post_id INTEGER REFERENCES posts(id) ON DELETE CASCADE,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            parent_id INTEGER REFERENCES comments(id) ON DELETE CASCADE,
            text TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        
        # Votes table
        """
        CREATE TABLE IF NOT EXISTS votes (
            id SERIAL PRIMARY KEY,
            post_id INTEGER REFERENCES posts(id) ON DELETE CASCADE,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            value INTEGER CHECK (value IN (1, -1)),
            UNIQUE(post_id, user_id)
        )
        """,
        
        # Messages table
        """
        CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY,
            sender_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            receiver_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            text TEXT NOT NULL,
            is_read BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        
        # Purchases table for tracking paid content
        """
        CREATE TABLE IF NOT EXISTS purchases (
            id SERIAL PRIMARY KEY,
            buyer_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            post_id INTEGER REFERENCES posts(id) ON DELETE CASCADE,
            amount DECIMAL(10,2) NOT NULL,
            stripe_payment_intent_id VARCHAR(255),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(buyer_id, post_id)
        )
        """,
        
        # Notifications table
        """
        CREATE TABLE IF NOT EXISTS notifications (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            type VARCHAR(50) NOT NULL,
            message TEXT NOT NULL,
            related_id INTEGER,
            is_read BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    ]

    for table in tables:
        cursor.execute(table)

    conn.commit()
    cursor.close()
    conn.close()


init_tables()

# ==================== AUTH HELPERS ====================

def hash_password(password):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def check_password(password, hashed):
    return bcrypt.checkpw(password.encode(), hashed.encode())


def encode_jwt(user_id):
    payload = {
        "user_id": user_id,
        "exp": datetime.utcnow() + timedelta(days=7)
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def decode_jwt(token):
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    except:
        return None


def require_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        token = auth_header.replace("Bearer ", "") if auth_header.startswith("Bearer ") else None
        
        if not token:
            return jsonify({"error": "Unauthorized - No token provided"}), 401

        payload = decode_jwt(token)
        if not payload:
            return jsonify({"error": "Unauthorized - Invalid token"}), 401

        g.user_id = payload["user_id"]
        return f(*args, **kwargs)
    return wrapper


def get_current_user():
    cursor = get_db()
    cursor.execute("SELECT id, username, email, bio, avatar_url, stripe_account_id, total_earnings FROM users WHERE id=%s", (g.user_id,))
    user = cursor.fetchone()
    if user:
        return {
            "id": user[0], "username": user[1], "email": user[2], "bio": user[3],
            "avatar_url": user[4], "stripe_account_id": user[5], "total_earnings": float(user[6] or 0)
        }
    return None

# ==================== CLOUDINARY ====================

def upload_media(file, resource_type="auto"):
    try:
        result = cloudinary.uploader.upload(file, resource_type=resource_type, timeout=120)
        return result["secure_url"]
    except Exception as e:
        print(f"Cloudinary upload error: {e}")
        raise Exception(f"File upload failed: {str(e)}")

# ==================== ROUTES ====================

@app.route("/")
def health():
    return jsonify({"status": "DevMarket API running", "version": "1.0.0"})

# ==================== AUTH ROUTES ====================

@app.route("/auth/register", methods=["POST"])
@limiter.limit("5 per minute")
def register():
    data = request.get_json()
    username = data.get("username", "").strip()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    if not username or not email or not password or len(password) < 6:
        return jsonify({"error": "Missing fields or password too short (min 6 chars)"}), 400

    cursor = get_db()
    password_hash = hash_password(password)

    try:
        cursor.execute(
            "INSERT INTO users (username, email, password_hash) VALUES (%s, %s, %s) RETURNING id",
            (username, email, password_hash)
        )
        user_id = cursor.fetchone()[0]
        g.db.commit()
        
        token = encode_jwt(user_id)
        return jsonify({
            "token": token, 
            "user_id": user_id,
            "username": username,
            "email": email
        }), 201

    except psycopg2.errors.UniqueViolation:
        g.db.rollback()
        return jsonify({"error": "Username or email already exists"}), 409
    except Exception as e:
        g.db.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/auth/login", methods=["POST"])
@limiter.limit("10 per minute")
def login():
    data = request.get_json()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    if not email or not password:
        return jsonify({"error": "Missing credentials"}), 400

    cursor = get_db()
    cursor.execute("SELECT id, username, password_hash FROM users WHERE email=%s", (email,))
    user = cursor.fetchone()

    if not user or not check_password(password, user[2]):
        return jsonify({"error": "Invalid credentials"}), 401

    token = encode_jwt(user[0])
    return jsonify({
        "token": token, 
        "user_id": user[0],
        "username": user[1]
    })


@app.route("/auth/me", methods=["GET"])
@require_auth
def get_current_user_route():
    user = get_current_user()
    if not user:
        return jsonify({"error": "User not found"}), 404
    return jsonify(user)

# ==================== POSTS ROUTES ====================

@app.route("/posts", methods=["GET"])
def get_posts():
    search = request.args.get("search", "").strip()
    cursor = get_db()
    
    # Get current user if authenticated (for vote status)
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.replace("Bearer ", "") if auth_header.startswith("Bearer ") else None
    current_user_id = None
    if token:
        payload = decode_jwt(token)
        if payload:
            current_user_id = payload["user_id"]

    # Build query with search
    if search:
        cursor.execute("""
            SELECT p.id, p.title, p.description, p.price, p.media_urls, p.tags, 
                   p.created_at, p.view_count, p.file_url,
                   u.id, u.username, u.avatar_url,
                   COALESCE(SUM(v.value), 0) as vote_count
            FROM posts p
            JOIN users u ON p.user_id = u.id
            LEFT JOIN votes v ON p.id = v.post_id
            WHERE p.title ILIKE %s OR p.description ILIKE %s OR %s = ANY(p.tags)
            GROUP BY p.id, u.id
            ORDER BY p.created_at DESC
        """, (f"%{search}%", f"%{search}%", search))
    else:
        cursor.execute("""
            SELECT p.id, p.title, p.description, p.price, p.media_urls, p.tags, 
                   p.created_at, p.view_count, p.file_url,
                   u.id, u.username, u.avatar_url,
                   COALESCE(SUM(v.value), 0) as vote_count
            FROM posts p
            JOIN users u ON p.user_id = u.id
            LEFT JOIN votes v ON p.id = v.post_id
            GROUP BY p.id, u.id
            ORDER BY p.created_at DESC
        """)

    rows = cursor.fetchall()
    result = []

    for r in rows:
        post_id = r[0]
        
        # Check if user voted on this post
        user_vote = 0
        if current_user_id:
            cursor.execute("SELECT value FROM votes WHERE post_id=%s AND user_id=%s", (post_id, current_user_id))
            vote_row = cursor.fetchone()
            if vote_row:
                user_vote = vote_row[0]
        
        # Check if user purchased this (for frontend download button)
        purchased = False
        if current_user_id:
            cursor.execute("SELECT id FROM purchases WHERE buyer_id=%s AND post_id=%s", (current_user_id, post_id))
            if cursor.fetchone():
                purchased = True

        # Get comment count
        cursor.execute("SELECT COUNT(*) FROM comments WHERE post_id=%s", (post_id,))
        comment_count = cursor.fetchone()[0]

        result.append({
            "id": post_id,
            "title": r[1],
            "description": r[2],
            "price": float(r[3]),
            "media_urls": r[4] or [],
            "tags": r[5] or [],
            "created_at": r[6].isoformat() if r[6] else None,
            "view_count": r[7] or 0,
            "file_url": r[8],
            "vote_count": int(r[12]),
            "user_vote": user_vote,
            "purchased": purchased,
            "comment_count": comment_count,
            "user": {
                "id": r[9],
                "username": r[10],
                "avatar_url": r[11]
            }
        })

    return jsonify(result)


@app.route("/posts/<int:post_id>", methods=["GET"])
def get_post(post_id):
    cursor = get_db()
    
    # Get post with user info
    cursor.execute("""
        SELECT p.id, p.title, p.description, p.price, p.media_urls, p.tags,
               p.created_at, p.view_count, p.file_url, p.user_id,
               u.username, u.avatar_url,
               COALESCE(SUM(v.value), 0) as vote_count
        FROM posts p
        JOIN users u ON p.user_id = u.id
        LEFT JOIN votes v ON p.id = v.post_id
        WHERE p.id = %s
        GROUP BY p.id, u.id
    """, (post_id,))
    
    row = cursor.fetchone()
    if not row:
        return jsonify({"error": "Post not found"}), 404

    # Increment view count
    cursor.execute("UPDATE posts SET view_count = view_count + 1 WHERE id = %s", (post_id,))
    g.db.commit()

    # Get comments with replies
    cursor.execute("""
        SELECT c.id, c.text, c.created_at, c.parent_id,
               u.id, u.username
        FROM comments c
        JOIN users u ON c.user_id = u.id
        WHERE c.post_id = %s
        ORDER BY c.created_at ASC
    """, (post_id,))
    
    comments_rows = cursor.fetchall()
    comments = []
    replies_map = {}
    
    for cr in comments_rows:
        comment_obj = {
            "id": cr[0],
            "text": cr[1],
            "created_at": cr[2].isoformat() if cr[2] else None,
            "parent_id": cr[3],
            "user": {"id": cr[4], "username": cr[5]}
        }
        if cr[3]:  # It's a reply
            if cr[3] not in replies_map:
                replies_map[cr[3]] = []
            replies_map[cr[3]].append(comment_obj)
        else:
            comments.append(comment_obj)
    
    # Attach replies to parent comments
    for comment in comments:
        comment["replies"] = replies_map.get(comment["id"], [])

    return jsonify({
        "id": row[0],
        "title": row[1],
        "description": row[2],
        "price": float(row[3]),
        "media_urls": row[4] or [],
        "tags": row[5] or [],
        "created_at": row[6].isoformat() if row[6] else None,
        "view_count": row[7] + 1,  # +1 because we just incremented
        "file_url": row[8],
        "vote_count": int(row[12]),
        "comments": comments,
        "user": {
            "id": row[9],
            "username": row[10],
            "avatar_url": row[11]
        }
    })


@app.route("/posts", methods=["POST"])
@require_auth
@limiter.limit("20 per minute")
def create_post():
    cursor = get_db()
    
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    price = request.form.get("price", "0")
    tags_str = request.form.get("tags", "")
    
    if not title:
        return jsonify({"error": "Title is required"}), 400

    try:
        price = float(price)
        if price < 0:
            price = 0
    except:
        price = 0

    tags = [t.strip().lower() for t in tags_str.split(",") if t.strip()]

    # Handle preview images (media)
    media_urls = []
    if "media" in request.files:
        files = request.files.getlist("media")
        for file in files:
            if file and file.filename:
                url = upload_media(file)
                if url:
                    media_urls.append(url)

    # Handle product file (the actual deliverable)
    file_url = None
    if "file" in request.files:
        product_file = request.files["file"]
        if product_file and product_file.filename:
            file_url = upload_media(product_file, resource_type="raw")

    cursor.execute("""
        INSERT INTO posts (user_id, title, description, price, media_urls, file_url, tags)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (g.user_id, title, description, price, media_urls, file_url, tags))
    
    post_id = cursor.fetchone()[0]
    g.db.commit()

    return jsonify({
        "id": post_id,
        "title": title,
        "price": price,
        "media_urls": media_urls,
        "file_url": file_url
    }), 201


@app.route("/posts/<int:post_id>", methods=["DELETE"])
@require_auth
def delete_post(post_id):
    cursor = get_db()
    
    # Verify ownership
    cursor.execute("SELECT user_id FROM posts WHERE id = %s", (post_id,))
    post = cursor.fetchone()
    
    if not post:
        return jsonify({"error": "Post not found"}), 404
    
    if post[0] != g.user_id:
        return jsonify({"error": "Not authorized to delete this post"}), 403
    
    cursor.execute("DELETE FROM posts WHERE id = %s", (post_id,))
    g.db.commit()
    
    return jsonify({"message": "Post deleted"}), 200

# ==================== VOTES ====================

@app.route("/posts/<int:post_id>/vote", methods=["POST"])
@require_auth
def vote_post(post_id):
    data = request.get_json()
    value = data.get("value")
    
    if value not in [1, -1]:
        return jsonify({"error": "Invalid vote value"}), 400

    cursor = get_db()
    
    # Check if post exists
    cursor.execute("SELECT id FROM posts WHERE id = %s", (post_id,))
    if not cursor.fetchone():
        return jsonify({"error": "Post not found"}), 404

    # Upsert vote
    cursor.execute("""
        INSERT INTO votes (post_id, user_id, value) 
        VALUES (%s, %s, %s)
        ON CONFLICT (post_id, user_id) 
        DO UPDATE SET value = EXCLUDED.value
    """, (post_id, g.user_id, value))
    
    g.db.commit()
    return jsonify({"message": "Vote recorded"}), 200

# ==================== COMMENTS ====================

@app.route("/posts/<int:post_id>/comment", methods=["POST"])
@require_auth
def add_comment(post_id):
    data = request.get_json()
    text = data.get("text", "").strip()
    parent_id = data.get("parent_id")
    
    if not text:
        return jsonify({"error": "Comment text is required"}), 400

    cursor = get_db()
    
    # Verify post exists
    cursor.execute("SELECT user_id FROM posts WHERE id = %s", (post_id,))
    post = cursor.fetchone()
    if not post:
        return jsonify({"error": "Post not found"}), 404
    
    # If parent_id provided, verify it exists and belongs to this post
    if parent_id:
        cursor.execute("SELECT id FROM comments WHERE id = %s AND post_id = %s", (parent_id, post_id))
        if not cursor.fetchone():
            return jsonify({"error": "Invalid parent comment"}), 400

    cursor.execute("""
        INSERT INTO comments (post_id, user_id, parent_id, text)
        VALUES (%s, %s, %s, %s)
        RETURNING id, created_at
    """, (post_id, g.user_id, parent_id, text))
    
    result = cursor.fetchone()
    g.db.commit()
    
    # Create notification for post owner (if not self)
    if post[0] != g.user_id:
        cursor.execute("""
            INSERT INTO notifications (user_id, type, message, related_id)
            VALUES (%s, %s, %s, %s)
        """, (post[0], "comment", f"Someone commented on your post", post_id))
        g.db.commit()

    return jsonify({
        "id": result[0],
        "text": text,
        "created_at": result[1].isoformat(),
        "user_id": g.user_id
    }), 201

# ==================== MESSAGES ====================

@app.route("/messages", methods=["GET"])
@require_auth
def get_conversations():
    cursor = get_db()
    
    # Get last message from each conversation partner
    cursor.execute("""
        WITH last_messages AS (
            SELECT DISTINCT ON (LEAST(sender_id, receiver_id), GREATEST(sender_id, receiver_id))
                id, sender_id, receiver_id, text, created_at, is_read,
                CASE WHEN sender_id = %s THEN receiver_id ELSE sender_id END as other_user_id
            FROM messages
            WHERE sender_id = %s OR receiver_id = %s
            ORDER BY LEAST(sender_id, receiver_id), GREATEST(sender_id, receiver_id), created_at DESC
        )
        SELECT lm.*, u.username
        FROM last_messages lm
        JOIN users u ON u.id = lm.other_user_id
        ORDER BY lm.created_at DESC
    """, (g.user_id, g.user_id, g.user_id))
    
    rows = cursor.fetchall()
    conversations = []
    
    for r in rows:
        other_user_id = r[5]
        
        # Count unread messages from this user
        cursor.execute("""
            SELECT COUNT(*) FROM messages 
            WHERE sender_id = %s AND receiver_id = %s AND is_read = FALSE
        """, (other_user_id, g.user_id))
        unread_count = cursor.fetchone()[0]
        
        conversations.append({
            "user_id": other_user_id,
            "username": r[6],
            "last_message": r[3],
            "last_time": r[4].isoformat() if r[4] else None,
            "unread_count": unread_count
        })
    
    return jsonify(conversations)


@app.route("/messages/<int:user_id>", methods=["GET"])
@require_auth
def get_messages_with_user(user_id):
    cursor = get_db()
    
    # Mark messages as read
    cursor.execute("""
        UPDATE messages SET is_read = TRUE 
        WHERE sender_id = %s AND receiver_id = %s AND is_read = FALSE
    """, (user_id, g.user_id))
    g.db.commit()
    
    # Get messages
    cursor.execute("""
        SELECT id, sender_id, receiver_id, text, created_at
        FROM messages
        WHERE (sender_id = %s AND receiver_id = %s) OR (sender_id = %s AND receiver_id = %s)
        ORDER BY created_at ASC
    """, (g.user_id, user_id, user_id, g.user_id))
    
    rows = cursor.fetchall()
    messages = []
    
    for r in rows:
        messages.append({
            "id": r[0],
            "is_me": r[1] == g.user_id,
            "text": r[3],
            "created_at": r[4].isoformat()
        })
    
    return jsonify(messages)


@app.route("/messages", methods=["POST"])
@require_auth
def send_message():
    data = request.get_json()
    receiver_id = data.get("receiver_id")
    text = data.get("text", "").strip()
    
    if not receiver_id or not text:
        return jsonify({"error": "Missing receiver or text"}), 400
    
    if receiver_id == g.user_id:
        return jsonify({"error": "Cannot message yourself"}), 400

    cursor = get_db()
    
    # Verify receiver exists
    cursor.execute("SELECT id FROM users WHERE id = %s", (receiver_id,))
    if not cursor.fetchone():
        return jsonify({"error": "User not found"}), 404
    
    cursor.execute("""
        INSERT INTO messages (sender_id, receiver_id, text)
        VALUES (%s, %s, %s)
        RETURNING id, created_at
    """, (g.user_id, receiver_id, text))
    
    result = cursor.fetchone()
    g.db.commit()
    
    # Create notification
    cursor.execute("""
        INSERT INTO notifications (user_id, type, message, related_id)
        VALUES (%s, %s, %s, %s)
    """, (receiver_id, "message", "You have a new message", g.user_id))
    g.db.commit()
    
    return jsonify({
        "id": result[0],
        "text": text,
        "created_at": result[1].isoformat()
    }), 201

# ==================== STRIPE / PAYMENTS ====================

@app.route("/stripe/onboard", methods=["POST"])
@require_auth
def stripe_onboard():
    cursor = get_db()
    
    # Get or create Stripe account
    cursor.execute("SELECT stripe_account_id FROM users WHERE id = %s", (g.user_id,))
    result = cursor.fetchone()
    stripe_account_id = result[0] if result else None
    
    if not stripe_account_id:
        try:
            account = stripe.Account.create(
                type="express",
                country="US",
                email=get_current_user()["email"],
                capabilities={
                    "card_payments": {"requested": True},
                    "transfers": {"requested": True}
                }
            )
            stripe_account_id = account.id
            cursor.execute("UPDATE users SET stripe_account_id = %s WHERE id = %s", 
                         (stripe_account_id, g.user_id))
            g.db.commit()
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    
    try:
        account_link = stripe.AccountLink.create(
            account=stripe_account_id,
            refresh_url=f"{FRONTEND_URL}/settings?stripe=refresh",
            return_url=f"{FRONTEND_URL}/settings?stripe=success",
            type="account_onboarding"
        )
        return jsonify({"url": account_link.url})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/posts/<int:post_id>/checkout", methods=["POST"])
@require_auth
def create_checkout(post_id):
    cursor = get_db()
    
    # Get post details
    cursor.execute("""
        SELECT p.id, p.title, p.price, p.user_id, u.stripe_account_id, p.file_url
        FROM posts p
        JOIN users u ON p.user_id = u.id
        WHERE p.id = %s
    """, (post_id,))
    
    post = cursor.fetchone()
    if not post:
        return jsonify({"error": "Post not found"}), 404
    
    price = float(post[2])
    seller_id = post[3]
    seller_stripe_id = post[4]
    file_url = post[5]
    
    # Check if already purchased
    cursor.execute("SELECT id FROM purchases WHERE buyer_id = %s AND post_id = %s", (g.user_id, post_id))
    if cursor.fetchone():
        return jsonify({"error": "Already purchased", "file_url": file_url}), 400
    
    # Can't buy own post
    if seller_id == g.user_id:
        return jsonify({"error": "Cannot purchase your own post"}), 400
    
    # Free post - just record purchase
    if price == 0:
        cursor.execute("""
            INSERT INTO purchases (buyer_id, post_id, amount, stripe_payment_intent_id)
            VALUES (%s, %s, 0, 'free')
        """, (g.user_id, post_id))
        g.db.commit()
        return jsonify({"free": True, "file_url": file_url})
    
    # Check seller has Stripe
    if not seller_stripe_id:
        return jsonify({"error": "Seller not set up for payments"}), 400
    
    try:
        # Calculate platform fee (10%)
        platform_fee = int(price * 10)  # in cents for Stripe
        
        checkout_session = stripe.checkout.Session.create(
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "product_data": {"name": post[1]},
                    "unit_amount": int(price * 100),  # Convert to cents
                },
                "quantity": 1,
            }],
            mode="payment",
            success_url=f"{FRONTEND_URL}?checkout=success&post_id={post_id}",
            cancel_url=f"{FRONTEND_URL}?checkout=cancel",
            payment_intent_data={
                "application_fee_amount": platform_fee,
                "transfer_data": {"destination": seller_stripe_id},
            },
            metadata={"post_id": post_id, "buyer_id": g.user_id}
        )
        
        return jsonify({"checkout_url": checkout_session.url})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/posts/<int:post_id>/download", methods=["GET"])
@require_auth
def download_file(post_id):
    cursor = get_db()
    
    # Check ownership or purchase
    cursor.execute("SELECT user_id, file_url FROM posts WHERE id = %s", (post_id,))
    post = cursor.fetchone()
    
    if not post:
        return jsonify({"error": "Post not found"}), 404
    
    is_owner = post[0] == g.user_id
    
    if not is_owner:
        cursor.execute("SELECT id FROM purchases WHERE buyer_id = %s AND post_id = %s", (g.user_id, post_id))
        if not cursor.fetchone():
            return jsonify({"error": "Purchase required"}), 403
    
    if not post[1]:
        return jsonify({"error": "No file available"}), 404
    
    return jsonify({"file_url": post[1]})


@app.route("/purchases/me", methods=["GET"])
@require_auth
def get_my_purchases():
    cursor = get_db()
    cursor.execute("SELECT post_id FROM purchases WHERE buyer_id = %s", (g.user_id,))
    rows = cursor.fetchall()
    return jsonify([r[0] for r in rows])

# ==================== WEBHOOK ====================

@app.route("/webhook", methods=["POST"])
def stripe_webhook():
    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature")
    
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except:
        return jsonify({"error": "Invalid signature"}), 400
    
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        post_id = int(session["metadata"]["post_id"])
        buyer_id = int(session["metadata"]["buyer_id"])
        amount = session["amount_total"] / 100  # Convert from cents
        
        cursor = get_db()
        
        # Record purchase
        cursor.execute("""
            INSERT INTO purchases (buyer_id, post_id, amount, stripe_payment_intent_id)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (buyer_id, post_id) DO NOTHING
        """, (buyer_id, post_id, amount, session["payment_intent"]))
        
        # Update seller earnings and sales count
        cursor.execute("""
            UPDATE users SET total_earnings = total_earnings + %s 
            WHERE id = (SELECT user_id FROM posts WHERE id = %s)
        """, (amount * 0.9, post_id))  # 90% to seller
        
        cursor.execute("""
            UPDATE posts SET sales_count = sales_count + 1 WHERE id = %s
        """, (post_id,))
        
        # Create notification
        cursor.execute("SELECT user_id FROM posts WHERE id = %s", (post_id,))
        seller_id = cursor.fetchone()[0]
        cursor.execute("""
            INSERT INTO notifications (user_id, type, message, related_id)
            VALUES (%s, %s, %s, %s)
        """, (seller_id, "sale", f"You made a sale! ${amount:.2f}", post_id))
        
        g.db.commit()
    
    return jsonify({"status": "success"})

# ==================== NOTIFICATIONS ====================

@app.route("/notifications", methods=["GET"])
@require_auth
def get_notifications():
    cursor = get_db()
    cursor.execute("""
        SELECT id, type, message, related_id, is_read, created_at
        FROM notifications
        WHERE user_id = %s
        ORDER BY created_at DESC
        LIMIT 50
    """, (g.user_id,))
    
    rows = cursor.fetchall()
    notifications = []
    
    for r in rows:
        notifications.append({
            "id": r[0],
            "type": r[1],
            "message": r[2],
            "related_id": r[3],
            "is_read": r[4],
            "created_at": r[5].isoformat()
        })
    
    return jsonify(notifications)


@app.route("/notifications/read", methods=["POST"])
@require_auth
def mark_notifications_read():
    cursor = get_db()
    cursor.execute("UPDATE notifications SET is_read = TRUE WHERE user_id = %s", (g.user_id,))
    g.db.commit()
    return jsonify({"message": "Marked as read"})

# ==================== USERS / PROFILES ====================

@app.route("/users/<int:user_id>", methods=["GET"])
def get_user_profile(user_id):
    cursor = get_db()
    
    # Get user info
    cursor.execute("""
        SELECT id, username, bio, avatar_url, total_earnings, created_at
        FROM users WHERE id = %s
    """, (user_id,))
    
    user = cursor.fetchone()
    if not user:
        return jsonify({"error": "User not found"}), 404
    
    # Get user's posts
    cursor.execute("""
        SELECT p.id, p.title, p.description, p.price, p.media_urls, p.tags,
               p.created_at, p.view_count, p.sales_count, p.file_url,
               COALESCE(SUM(v.value), 0) as vote_count
        FROM posts p
        LEFT JOIN votes v ON p.id = v.post_id
        WHERE p.user_id = %s
        GROUP BY p.id
        ORDER BY p.created_at DESC
    """, (user_id,))
    
    posts_rows = cursor.fetchall()
    posts = []
    
    for r in posts_rows:
        posts.append({
            "id": r[0],
            "title": r[1],
            "description": r[2],
            "price": float(r[3]),
            "media_urls": r[4] or [],
            "tags": r[5] or [],
            "created_at": r[6].isoformat() if r[6] else None,
            "view_count": r[7] or 0,
            "sales_count": r[8] or 0,
            "file_url": r[9],
            "vote_count": int(r[10]),
            "user": {"id": user[0], "username": user[1]}  # Embed user for frontend compatibility
        })
    
    return jsonify({
        "id": user[0],
        "username": user[1],
        "bio": user[2],
        "avatar_url": user[3],
        "total_earnings": float(user[4] or 0),
        "created_at": user[5].isoformat() if user[5] else None,
        "posts": posts
    })


@app.route("/users/me", methods=["PUT"])
@require_auth
def update_profile():
    data = request.get_json()
    username = data.get("username", "").strip()
    bio = data.get("bio", "").strip()
    
    if not username:
        return jsonify({"error": "Username is required"}), 400
    
    if len(username) < 3 or len(username) > 50:
        return jsonify({"error": "Username must be 3-50 characters"}), 400
    
    cursor = get_db()
    
    # Check username availability (if changed)
    cursor.execute("SELECT id FROM users WHERE username = %s AND id != %s", (username, g.user_id))
    if cursor.fetchone():
        return jsonify({"error": "Username already taken"}), 409
    
    try:
        cursor.execute("""
            UPDATE users SET username = %s, bio = %s WHERE id = %s
        """, (username, bio, g.user_id))
        g.db.commit()
        return jsonify({"message": "Profile updated", "username": username, "bio": bio})
    except Exception as e:
        g.db.rollback()
        return jsonify({"error": str(e)}), 500

# ==================== MAIN ====================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=port, debug=False)
