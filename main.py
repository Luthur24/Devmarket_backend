"""
DevMarket Backend — Flask + PostgreSQL
Shared database (trends_db2) with namespaced tables (devmarket_*)
"""

import os
import re
import jwt
import bcrypt
import psycopg2
import cloudinary
import cloudinary.uploader
from decimal import Decimal
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, request, jsonify, g
from flask_cors import CORS
from psycopg2.extras import RealDictCursor

# ── CONFIG ───────────────────────────────────────────────────────
class Config:
    SECRET_KEY          = os.environ.get('DM_JWT_SECRET', 'devmarket-secret-change-in-prod')
    DB_HOST             = os.environ.get('DB_HOST',     'dpg-d70himndiees73dlbeig-a.frankfurt-postgres.render.com')
    DB_NAME             = os.environ.get('DB_NAME',     'trends_db2')
    DB_USER             = os.environ.get('DB_USER',     'trends_db2_user')
    DB_PASSWORD         = os.environ.get('DB_PASSWORD', 'h5NO8WY8nxLF64WSM7jwYZ7b8B7dCOiR')
    DB_PORT             = int(os.environ.get('DB_PORT', 5432))

    CLOUDINARY_CLOUD_NAME = os.environ.get('CLOUDINARY_CLOUD_NAME', 'ddusfl7pi')
    CLOUDINARY_API_KEY    = os.environ.get('CLOUDINARY_API_KEY',    '599965682593626')
    CLOUDINARY_API_SECRET = os.environ.get('CLOUDINARY_API_SECRET', 'pUcb90_1jtv-rDlHXRRsfDcBK5k')

    # ── FINANCIAL API (replace values when ready) ──────────────
    PAYMENT_API_BASE    = 'https://api.yourpaymentprovider.com/v1'
    PAYMENT_API_KEY     = 'YOUR_PAYMENT_API_KEY_HERE'
    PAYMENT_WEBHOOK_SECRET = 'YOUR_WEBHOOK_SECRET_HERE'

    COMMISSION_RATE     = Decimal('0.05')   # 5%
    ALLOWED_EXTENSIONS  = {'zip', 'rar', 'tar', 'gz', 'json', 'txt', 'md', 'pdf'}
    MAX_FILE_SIZE       = 50 * 1024 * 1024  # 50 MB

# ── APP INIT ─────────────────────────────────────────────────────
app = Flask(__name__)
app.config.from_object(Config)
CORS(app, origins=['*'])

cloudinary.config(
    cloud_name = Config.CLOUDINARY_CLOUD_NAME,
    api_key    = Config.CLOUDINARY_API_KEY,
    api_secret = Config.CLOUDINARY_API_SECRET,
)

# ── DATABASE ──────────────────────────────────────────────────────
def get_db():
    if 'db' not in g:
        g.db = psycopg2.connect(
            host     = Config.DB_HOST,
            dbname   = Config.DB_NAME,
            user     = Config.DB_USER,
            password = Config.DB_PASSWORD,
            port     = Config.DB_PORT,
            cursor_factory = RealDictCursor,
        )
    return g.db

@app.teardown_appcontext
def close_db(error):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    db = get_db()
    cur = db.cursor()

    # ── Users ────────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS devmarket_users (
            id              SERIAL PRIMARY KEY,
            username        VARCHAR(50)  UNIQUE NOT NULL,
            email           VARCHAR(120) UNIQUE NOT NULL,
            password_hash   VARCHAR(255) NOT NULL,
            display_name    VARCHAR(80),
            bio             TEXT,
            avatar_url      TEXT,
            avatar_public_id TEXT,
            is_seller       BOOLEAN DEFAULT FALSE,
            is_verified     BOOLEAN DEFAULT FALSE,
            wallet_balance  NUMERIC(14,2) DEFAULT 0.00,
            created_at      TIMESTAMPTZ DEFAULT NOW(),
            updated_at      TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # ── Products ─────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS devmarket_products (
            id              SERIAL PRIMARY KEY,
            seller_id       INTEGER REFERENCES devmarket_users(id) ON DELETE CASCADE,
            title           VARCHAR(200) NOT NULL,
            description     TEXT,
            price           NUMERIC(10,2) NOT NULL CHECK (price >= 0),
            category        VARCHAR(60) NOT NULL,
            tags            TEXT[],
            image_url       TEXT,
            image_public_id TEXT,
            file_url        TEXT,
            file_public_id  TEXT,
            rating          NUMERIC(3,2) DEFAULT 0.00,
            sales_count     INTEGER DEFAULT 0,
            views_count     INTEGER DEFAULT 0,
            status          VARCHAR(20) DEFAULT 'active',
            created_at      TIMESTAMPTZ DEFAULT NOW(),
            updated_at      TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # ── Orders ───────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS devmarket_orders (
            id              SERIAL PRIMARY KEY,
            buyer_id        INTEGER REFERENCES devmarket_users(id) ON DELETE CASCADE,
            product_id      INTEGER REFERENCES devmarket_products(id) ON DELETE CASCADE,
            price_paid      NUMERIC(10,2) NOT NULL,
            commission_amt  NUMERIC(10,2) NOT NULL,
            seller_payout   NUMERIC(10,2) NOT NULL,
            status          VARCHAR(20) DEFAULT 'completed',
            download_count  INTEGER DEFAULT 0,
            created_at      TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # ── Cart ─────────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS devmarket_cart_items (
            id          SERIAL PRIMARY KEY,
            user_id     INTEGER REFERENCES devmarket_users(id) ON DELETE CASCADE,
            product_id  INTEGER REFERENCES devmarket_products(id) ON DELETE CASCADE,
            created_at  TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE (user_id, product_id)
        )
    """)

    # ── Favorites ────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS devmarket_favorites (
            id          SERIAL PRIMARY KEY,
            user_id     INTEGER REFERENCES devmarket_users(id) ON DELETE CASCADE,
            product_id  INTEGER REFERENCES devmarket_products(id) ON DELETE CASCADE,
            created_at  TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE (user_id, product_id)
        )
    """)

    # ── Messages ─────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS devmarket_messages (
            id          SERIAL PRIMARY KEY,
            sender_id   INTEGER REFERENCES devmarket_users(id) ON DELETE CASCADE,
            receiver_id INTEGER REFERENCES devmarket_users(id) ON DELETE CASCADE,
            product_id  INTEGER REFERENCES devmarket_products(id) ON DELETE SET NULL,
            content     TEXT NOT NULL,
            is_read     BOOLEAN DEFAULT FALSE,
            created_at  TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # ── Reviews ──────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS devmarket_reviews (
            id          SERIAL PRIMARY KEY,
            product_id  INTEGER REFERENCES devmarket_products(id) ON DELETE CASCADE,
            reviewer_id INTEGER REFERENCES devmarket_users(id) ON DELETE CASCADE,
            rating      SMALLINT CHECK (rating BETWEEN 1 AND 5),
            comment     TEXT,
            created_at  TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE (product_id, reviewer_id)
        )
    """)

    # ── Wallet Transactions ───────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS devmarket_wallet_transactions (
            id              SERIAL PRIMARY KEY,
            user_id         INTEGER REFERENCES devmarket_users(id) ON DELETE CASCADE,
            type            VARCHAR(30) NOT NULL,
            amount          NUMERIC(14,2) NOT NULL,
            commission_amt  NUMERIC(14,2) DEFAULT 0.00,
            net_amount      NUMERIC(14,2) NOT NULL,
            reference       VARCHAR(100),
            external_tx_id  VARCHAR(200),
            status          VARCHAR(20) DEFAULT 'completed',
            note            TEXT,
            created_at      TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # ── Payouts ──────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS devmarket_payouts (
            id              SERIAL PRIMARY KEY,
            user_id         INTEGER REFERENCES devmarket_users(id) ON DELETE CASCADE,
            amount          NUMERIC(14,2) NOT NULL,
            commission_amt  NUMERIC(14,2) NOT NULL,
            net_amount      NUMERIC(14,2) NOT NULL,
            bank_name       VARCHAR(100),
            account_number  VARCHAR(50),
            account_name    VARCHAR(100),
            status          VARCHAR(20) DEFAULT 'pending',
            external_tx_id  VARCHAR(200),
            failure_reason  TEXT,
            requested_at    TIMESTAMPTZ DEFAULT NOW(),
            processed_at    TIMESTAMPTZ
        )
    """)

    # ── Activity Log ─────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS devmarket_activity_log (
            id          SERIAL PRIMARY KEY,
            user_id     INTEGER REFERENCES devmarket_users(id) ON DELETE CASCADE,
            type        VARCHAR(50) NOT NULL,
            description TEXT NOT NULL,
            created_at  TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    db.commit()
    cur.close()
    print('[DevMarket] Database initialized.')

# ── AUTH HELPERS ─────────────────────────────────────────────────
def generate_token(user_id):
    return jwt.encode(
        {'user_id': user_id,
         'iat': datetime.utcnow(),
         'exp': datetime.utcnow() + timedelta(days=7)},
        Config.SECRET_KEY, algorithm='HS256'
    )

def verify_token(token):
    try:
        return jwt.decode(token, Config.SECRET_KEY, algorithms=['HS256'])['user_id']
    except Exception:
        return None

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get('Authorization', '')
        parts = auth.split(' ')
        if len(parts) != 2 or parts[0] != 'Bearer':
            return jsonify({'error': 'Authorization header missing or malformed'}), 401
        user_id = verify_token(parts[1])
        if not user_id:
            return jsonify({'error': 'Token invalid or expired'}), 401
        g.user_id = user_id
        return f(*args, **kwargs)
    return decorated

# ── UTILS ─────────────────────────────────────────────────────────
def log_activity(user_id, kind, desc):
    try:
        db  = get_db()
        cur = db.cursor()
        cur.execute(
            "INSERT INTO devmarket_activity_log (user_id, type, description) VALUES (%s,%s,%s)",
            (user_id, kind, desc)
        )
        db.commit()
        cur.close()
    except Exception:
        pass

def recalc_rating(product_id):
    db  = get_db()
    cur = db.cursor()
    cur.execute(
        "SELECT ROUND(AVG(rating)::numeric, 2) AS avg FROM devmarket_reviews WHERE product_id = %s",
        (product_id,)
    )
    row = cur.fetchone()
    if row and row['avg']:
        cur.execute(
            "UPDATE devmarket_products SET rating = %s WHERE id = %s",
            (row['avg'], product_id)
        )
        db.commit()
    cur.close()

def credit_wallet(user_id, amount, kind, note='', reference='', ext_id=''):
    """Add funds to a user's wallet and record the transaction."""
    db  = get_db()
    cur = db.cursor()
    cur.execute(
        "UPDATE devmarket_users SET wallet_balance = wallet_balance + %s WHERE id = %s",
        (amount, user_id)
    )
    cur.execute("""
        INSERT INTO devmarket_wallet_transactions
            (user_id, type, amount, commission_amt, net_amount, reference, external_tx_id, note)
        VALUES (%s, %s, %s, 0, %s, %s, %s, %s)
    """, (user_id, kind, amount, amount, reference, ext_id, note))
    db.commit()
    cur.close()

def debit_wallet(user_id, amount, kind, note='', reference='', ext_id=''):
    """Remove funds from a user's wallet; raises on insufficient balance."""
    db  = get_db()
    cur = db.cursor()
    cur.execute("SELECT wallet_balance FROM devmarket_users WHERE id = %s FOR UPDATE", (user_id,))
    row = cur.fetchone()
    if not row or Decimal(str(row['wallet_balance'])) < Decimal(str(amount)):
        cur.close()
        raise ValueError('Insufficient wallet balance')
    cur.execute(
        "UPDATE devmarket_users SET wallet_balance = wallet_balance - %s WHERE id = %s",
        (amount, user_id)
    )
    cur.execute("""
        INSERT INTO devmarket_wallet_transactions
            (user_id, type, amount, commission_amt, net_amount, reference, external_tx_id, note)
        VALUES (%s, %s, %s, 0, %s, %s, %s, %s)
    """, (user_id, kind, amount, amount, reference, ext_id, note))
    db.commit()
    cur.close()

# ── AUTH ──────────────────────────────────────────────────────────
@app.route('/api/auth/register', methods=['POST'])
def register():
    data = request.get_json() or {}
    username = (data.get('username') or '').strip()
    email    = (data.get('email')    or '').strip().lower()
    password = (data.get('password') or '')

    if not username or not email or not password:
        return jsonify({'error': 'username, email and password are required'}), 400
    if len(password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400
    if not re.match(r'^[a-zA-Z0-9_]+$', username):
        return jsonify({'error': 'Username: letters, numbers and underscores only'}), 400

    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    db  = get_db()
    cur = db.cursor()
    try:
        cur.execute("""
            INSERT INTO devmarket_users (username, email, password_hash)
            VALUES (%s, %s, %s) RETURNING id
        """, (username, email, pw_hash))
        user_id = cur.fetchone()['id']
        db.commit()
    except psycopg2.IntegrityError:
        db.rollback()
        return jsonify({'error': 'Username or email already taken'}), 409
    finally:
        cur.close()

    log_activity(user_id, 'register', 'Account created')
    return jsonify({'token': generate_token(user_id),
                    'user': {'id': user_id, 'username': username, 'email': email}}), 201

@app.route('/api/auth/login', methods=['POST'])
def login():
    data  = request.get_json() or {}
    email = (data.get('email') or '').strip().lower()
    pw    = (data.get('password') or '')

    if not email or not pw:
        return jsonify({'error': 'email and password required'}), 400

    db  = get_db()
    cur = db.cursor()
    cur.execute(
        "SELECT id, username, email, password_hash, is_seller FROM devmarket_users WHERE email = %s",
        (email,)
    )
    user = cur.fetchone()
    cur.close()

    if not user or not bcrypt.checkpw(pw.encode(), user['password_hash'].encode()):
        return jsonify({'error': 'Invalid credentials'}), 401

    log_activity(user['id'], 'login', 'Logged in')
    return jsonify({'token': generate_token(user['id']),
                    'user': {'id': user['id'], 'username': user['username'],
                             'email': user['email'], 'is_seller': user['is_seller']}})

@app.route('/api/auth/me', methods=['GET'])
@login_required
def get_me():
    db  = get_db()
    cur = db.cursor()
    cur.execute("""
        SELECT id, username, email, display_name, bio, avatar_url,
               is_seller, is_verified, wallet_balance, created_at
        FROM devmarket_users WHERE id = %s
    """, (g.user_id,))
    user = cur.fetchone()
    cur.close()
    if not user:
        return jsonify({'error': 'User not found'}), 404
    return jsonify({'user': dict(user)})

@app.route('/api/auth/profile', methods=['PUT'])
@login_required
def update_profile():
    data = request.get_json() or {}
    fields, vals = [], []

    for col in ('display_name', 'bio'):
        if col in data:
            fields.append(f'{col} = %s')
            vals.append(data[col])
    if not fields:
        return jsonify({'error': 'Nothing to update'}), 400

    fields.append("updated_at = NOW()")
    vals.append(g.user_id)

    db  = get_db()
    cur = db.cursor()
    cur.execute(f"UPDATE devmarket_users SET {', '.join(fields)} WHERE id = %s", vals)
    db.commit()
    cur.close()
    return jsonify({'message': 'Profile updated'})

# ── UPLOAD ────────────────────────────────────────────────────────
@app.route('/api/upload', methods=['POST'])
@login_required
def upload_file():
    kind = request.form.get('type', 'file')   # 'image' | 'file' | 'avatar'
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    f = request.files['file']
    if not f.filename:
        return jsonify({'error': 'Empty filename'}), 400

    try:
        if kind == 'image':
            result = cloudinary.uploader.upload(f, folder='devmarket/products/images')
        elif kind == 'avatar':
            result = cloudinary.uploader.upload(
                f, folder='devmarket/avatars',
                transformation={'width': 200, 'height': 200, 'crop': 'fill', 'gravity': 'face'}
            )
            # persist avatar on user
            db  = get_db()
            cur = db.cursor()
            cur.execute(
                "UPDATE devmarket_users SET avatar_url = %s, avatar_public_id = %s, updated_at = NOW() WHERE id = %s",
                (result['secure_url'], result['public_id'], g.user_id)
            )
            db.commit()
            cur.close()
        else:
            ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
            if ext not in Config.ALLOWED_EXTENSIONS:
                return jsonify({'error': f'Extension .{ext} not allowed'}), 400
            result = cloudinary.uploader.upload(f, folder='devmarket/products/files', resource_type='raw')

        return jsonify({'url': result['secure_url'], 'public_id': result['public_id']})

    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── PRODUCTS ──────────────────────────────────────────────────────
@app.route('/api/products', methods=['GET'])
def list_products():
    search   = request.args.get('search', '').strip()
    category = request.args.get('category', '').strip()
    sort_by  = request.args.get('sort', 'created_at')
    order    = 'DESC' if request.args.get('order', 'desc').lower() == 'desc' else 'ASC'
    page     = max(1, int(request.args.get('page', 1)))
    per_page = min(50, max(1, int(request.args.get('per_page', 20))))

    safe_sort = {
        'price': 'p.price', 'rating': 'p.rating',
        'sales': 'p.sales_count', 'created_at': 'p.created_at'
    }.get(sort_by, 'p.created_at')

    query  = "SELECT p.*, u.username AS seller_name, u.avatar_url AS seller_avatar FROM devmarket_products p JOIN devmarket_users u ON p.seller_id = u.id WHERE p.status = 'active'"
    params = []

    if search:
        query += " AND (p.title ILIKE %s OR p.description ILIKE %s OR %s = ANY(p.tags))"
        params += [f'%{search}%', f'%{search}%', search]
    if category and category != 'all':
        query += " AND p.category = %s"
        params.append(category)

    query += f" ORDER BY {safe_sort} {order} LIMIT %s OFFSET %s"
    params += [per_page, (page - 1) * per_page]

    db  = get_db()
    cur = db.cursor()
    cur.execute(query, params)
    products = [dict(r) for r in cur.fetchall()]

    count_q = "SELECT COUNT(*) AS total FROM devmarket_products WHERE status = 'active'"
    count_p = []
    if search:
        count_q += " AND (title ILIKE %s OR description ILIKE %s)"
        count_p += [f'%{search}%', f'%{search}%']
    if category and category != 'all':
        count_q += " AND category = %s"
        count_p.append(category)

    cur.execute(count_q, count_p)
    total = cur.fetchone()['total']
    cur.close()

    return jsonify({'products': products,
                    'pagination': {'page': page, 'per_page': per_page,
                                   'total': total, 'pages': -(-total // per_page)}})

@app.route('/api/products/<int:pid>', methods=['GET'])
def get_product(pid):
    db  = get_db()
    cur = db.cursor()
    cur.execute("""
        SELECT p.*, u.username AS seller_name, u.avatar_url AS seller_avatar,
               u.bio AS seller_bio, u.is_verified AS seller_verified
        FROM devmarket_products p
        JOIN devmarket_users u ON p.seller_id = u.id
        WHERE p.id = %s AND p.status = 'active'
    """, (pid,))
    product = cur.fetchone()
    if not product:
        cur.close()
        return jsonify({'error': 'Product not found'}), 404

    cur.execute("UPDATE devmarket_products SET views_count = views_count + 1 WHERE id = %s", (pid,))

    cur.execute("""
        SELECT r.*, u.username AS reviewer_name, u.avatar_url AS reviewer_avatar
        FROM devmarket_reviews r
        JOIN devmarket_users u ON r.reviewer_id = u.id
        WHERE r.product_id = %s ORDER BY r.created_at DESC
    """, (pid,))
    reviews = [dict(r) for r in cur.fetchall()]
    db.commit()
    cur.close()

    out = dict(product)
    out['reviews'] = reviews
    return jsonify({'product': out})

@app.route('/api/products', methods=['POST'])
@login_required
def create_product():
    data = request.get_json() or {}
    title    = (data.get('title') or '').strip()
    price    = data.get('price')
    category = (data.get('category') or '').strip()

    if not title or price is None or not category:
        return jsonify({'error': 'title, price and category are required'}), 400

    db  = get_db()
    cur = db.cursor()
    cur.execute("""
        INSERT INTO devmarket_products
            (seller_id, title, description, price, category, tags, image_url, image_public_id, file_url, file_public_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
    """, (
        g.user_id, title,
        data.get('description', ''),
        float(price), category,
        data.get('tags', []),
        data.get('image_url', ''),
        data.get('image_public_id', ''),
        data.get('file_url', ''),
        data.get('file_public_id', ''),
    ))
    pid = cur.fetchone()['id']
    # mark user as seller if not yet
    cur.execute("UPDATE devmarket_users SET is_seller = TRUE WHERE id = %s", (g.user_id,))
    db.commit()
    cur.close()
    log_activity(g.user_id, 'product_created', f'Listed: {title}')
    return jsonify({'message': 'Product listed', 'product_id': pid}), 201

@app.route('/api/products/<int:pid>', methods=['PUT'])
@login_required
def update_product(pid):
    db  = get_db()
    cur = db.cursor()
    cur.execute("SELECT seller_id FROM devmarket_products WHERE id = %s", (pid,))
    row = cur.fetchone()
    if not row:
        cur.close()
        return jsonify({'error': 'Product not found'}), 404
    if row['seller_id'] != g.user_id:
        cur.close()
        return jsonify({'error': 'Forbidden'}), 403

    data   = request.get_json() or {}
    fields, vals = [], []
    for col in ('title', 'description', 'price', 'category', 'tags',
                'image_url', 'image_public_id', 'file_url', 'file_public_id', 'status'):
        if col in data:
            fields.append(f'{col} = %s')
            vals.append(data[col])
    if not fields:
        cur.close()
        return jsonify({'error': 'Nothing to update'}), 400

    fields.append('updated_at = NOW()')
    vals.append(pid)
    cur.execute(f"UPDATE devmarket_products SET {', '.join(fields)} WHERE id = %s", vals)
    db.commit()
    cur.close()
    return jsonify({'message': 'Product updated'})

@app.route('/api/products/<int:pid>', methods=['DELETE'])
@login_required
def delete_product(pid):
    db  = get_db()
    cur = db.cursor()
    cur.execute("SELECT seller_id FROM devmarket_products WHERE id = %s", (pid,))
    row = cur.fetchone()
    if not row or row['seller_id'] != g.user_id:
        cur.close()
        return jsonify({'error': 'Not found or forbidden'}), 404
    cur.execute("UPDATE devmarket_products SET status = 'removed' WHERE id = %s", (pid,))
    db.commit()
    cur.close()
    return jsonify({'message': 'Product removed'})

@app.route('/api/products/mine', methods=['GET'])
@login_required
def my_products():
    db  = get_db()
    cur = db.cursor()
    cur.execute(
        "SELECT * FROM devmarket_products WHERE seller_id = %s ORDER BY created_at DESC",
        (g.user_id,)
    )
    products = [dict(r) for r in cur.fetchall()]
    cur.close()
    return jsonify({'products': products})

# ── CART ──────────────────────────────────────────────────────────
@app.route('/api/cart', methods=['GET'])
@login_required
def get_cart():
    db  = get_db()
    cur = db.cursor()
    cur.execute("""
        SELECT p.*, c.id AS cart_item_id
        FROM devmarket_cart_items c
        JOIN devmarket_products p ON c.product_id = p.id
        WHERE c.user_id = %s
    """, (g.user_id,))
    items = [dict(r) for r in cur.fetchall()]
    cur.close()
    return jsonify({'cart': items})

@app.route('/api/cart', methods=['POST'])
@login_required
def add_to_cart():
    pid = (request.get_json() or {}).get('product_id')
    if not pid:
        return jsonify({'error': 'product_id required'}), 400
    db  = get_db()
    cur = db.cursor()
    try:
        cur.execute(
            "INSERT INTO devmarket_cart_items (user_id, product_id) VALUES (%s,%s)",
            (g.user_id, pid)
        )
        db.commit()
        return jsonify({'message': 'Added to cart'}), 201
    except psycopg2.IntegrityError:
        db.rollback()
        return jsonify({'error': 'Already in cart'}), 409
    finally:
        cur.close()

@app.route('/api/cart/<int:item_id>', methods=['DELETE'])
@login_required
def remove_from_cart(item_id):
    db  = get_db()
    cur = db.cursor()
    cur.execute(
        "DELETE FROM devmarket_cart_items WHERE id = %s AND user_id = %s",
        (item_id, g.user_id)
    )
    db.commit()
    cur.close()
    return jsonify({'message': 'Removed from cart'})

# ── ORDERS / CHECKOUT ─────────────────────────────────────────────
@app.route('/api/orders', methods=['POST'])
@login_required
def checkout():
    """
    Checkout using wallet balance.
    Applies 5% commission: seller receives 95% of price.
    """
    data  = request.get_json() or {}
    items = data.get('items', [])   # list of product_ids
    if not items:
        return jsonify({'error': 'No items to purchase'}), 400

    db  = get_db()
    cur = db.cursor()

    # fetch wallet balance with lock
    cur.execute(
        "SELECT wallet_balance FROM devmarket_users WHERE id = %s FOR UPDATE",
        (g.user_id,)
    )
    buyer_row = cur.fetchone()
    if not buyer_row:
        cur.close()
        return jsonify({'error': 'User not found'}), 404

    balance = Decimal(str(buyer_row['wallet_balance']))

    # calculate total & verify products
    cart = []
    for pid in items:
        cur.execute(
            "SELECT id, title, price, seller_id FROM devmarket_products WHERE id = %s AND status = 'active'",
            (pid,)
        )
        p = cur.fetchone()
        if not p:
            continue
        price      = Decimal(str(p['price']))
        commission = (price * Config.COMMISSION_RATE).quantize(Decimal('0.01'))
        payout     = price - commission
        cart.append({'product': dict(p), 'price': price,
                     'commission': commission, 'payout': payout})

    total = sum(i['price'] for i in cart)
    if balance < total:
        cur.close()
        return jsonify({'error': 'Insufficient wallet balance',
                        'required': str(total), 'balance': str(balance)}), 402

    try:
        order_ids = []
        for item in cart:
            p = item['product']

            # check buyer hasn't already bought this
            cur.execute(
                "SELECT id FROM devmarket_orders WHERE buyer_id=%s AND product_id=%s AND status='completed'",
                (g.user_id, p['id'])
            )
            if cur.fetchone():
                continue

            # create order
            cur.execute("""
                INSERT INTO devmarket_orders
                    (buyer_id, product_id, price_paid, commission_amt, seller_payout)
                VALUES (%s,%s,%s,%s,%s) RETURNING id
            """, (g.user_id, p['id'], item['price'], item['commission'], item['payout']))
            oid = cur.fetchone()['id']
            order_ids.append(oid)

            # increment sales
            cur.execute(
                "UPDATE devmarket_products SET sales_count = sales_count + 1 WHERE id = %s",
                (p['id'],)
            )

            # credit seller wallet
            cur.execute(
                "UPDATE devmarket_users SET wallet_balance = wallet_balance + %s WHERE id = %s",
                (item['payout'], p['seller_id'])
            )
            cur.execute("""
                INSERT INTO devmarket_wallet_transactions
                    (user_id, type, amount, commission_amt, net_amount, reference, note)
                VALUES (%s, 'sale_credit', %s, %s, %s, %s, %s)
            """, (p['seller_id'], item['price'], item['commission'], item['payout'],
                  f'order_{oid}', f"Sale of: {p['title']}"))

            log_activity(g.user_id,   'purchase', f"Bought: {p['title']}")
            log_activity(p['seller_id'], 'sale',  f"Sold: {p['title']}")

        # debit buyer wallet
        cur.execute(
            "UPDATE devmarket_users SET wallet_balance = wallet_balance - %s WHERE id = %s",
            (total, g.user_id)
        )
        cur.execute("""
            INSERT INTO devmarket_wallet_transactions
                (user_id, type, amount, commission_amt, net_amount, note)
            VALUES (%s, 'purchase_debit', %s, 0, %s, %s)
        """, (g.user_id, total, -total, f'Checkout — {len(order_ids)} item(s)'))

        # clear cart
        cur.execute(
            "DELETE FROM devmarket_cart_items WHERE user_id = %s AND product_id = ANY(%s)",
            (g.user_id, items)
        )

        db.commit()
        return jsonify({'message': 'Purchase complete', 'orders': order_ids,
                        'total_paid': str(total)}), 201

    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()

@app.route('/api/orders/library', methods=['GET'])
@login_required
def get_library():
    """All products the user has purchased."""
    db  = get_db()
    cur = db.cursor()
    cur.execute("""
        SELECT o.id AS order_id, o.price_paid, o.download_count, o.created_at AS purchased_at,
               p.id AS product_id, p.title, p.description, p.image_url, p.file_url,
               u.username AS seller_name
        FROM devmarket_orders o
        JOIN devmarket_products p ON o.product_id = p.id
        JOIN devmarket_users u ON p.seller_id = u.id
        WHERE o.buyer_id = %s AND o.status = 'completed'
        ORDER BY o.created_at DESC
    """, (g.user_id,))
    orders = [dict(r) for r in cur.fetchall()]
    cur.close()
    return jsonify({'library': orders})

@app.route('/api/orders/<int:oid>/download', methods=['GET'])
@login_required
def download(oid):
    db  = get_db()
    cur = db.cursor()
    cur.execute("""
        SELECT o.*, p.file_url, p.title
        FROM devmarket_orders o
        JOIN devmarket_products p ON o.product_id = p.id
        WHERE o.id = %s AND o.buyer_id = %s AND o.status = 'completed'
    """, (oid, g.user_id))
    order = cur.fetchone()
    if not order:
        cur.close()
        return jsonify({'error': 'Order not found'}), 404
    cur.execute(
        "UPDATE devmarket_orders SET download_count = download_count + 1 WHERE id = %s", (oid,)
    )
    db.commit()
    cur.close()
    return jsonify({'download_url': order['file_url'], 'title': order['title']})

# ── FAVORITES ─────────────────────────────────────────────────────
@app.route('/api/favorites', methods=['GET'])
@login_required
def get_favorites():
    db  = get_db()
    cur = db.cursor()
    cur.execute("""
        SELECT p.*, u.username AS seller_name
        FROM devmarket_favorites f
        JOIN devmarket_products p ON f.product_id = p.id
        JOIN devmarket_users u ON p.seller_id = u.id
        WHERE f.user_id = %s ORDER BY f.created_at DESC
    """, (g.user_id,))
    favs = [dict(r) for r in cur.fetchall()]
    cur.close()
    return jsonify({'favorites': favs})

@app.route('/api/favorites', methods=['POST'])
@login_required
def add_favorite():
    pid = (request.get_json() or {}).get('product_id')
    if not pid:
        return jsonify({'error': 'product_id required'}), 400
    db  = get_db()
    cur = db.cursor()
    try:
        cur.execute(
            "INSERT INTO devmarket_favorites (user_id, product_id) VALUES (%s,%s)",
            (g.user_id, pid)
        )
        db.commit()
        return jsonify({'message': 'Added to favorites'}), 201
    except psycopg2.IntegrityError:
        db.rollback()
        return jsonify({'error': 'Already saved'}), 409
    finally:
        cur.close()

@app.route('/api/favorites/<int:pid>', methods=['DELETE'])
@login_required
def remove_favorite(pid):
    db  = get_db()
    cur = db.cursor()
    cur.execute(
        "DELETE FROM devmarket_favorites WHERE user_id = %s AND product_id = %s",
        (g.user_id, pid)
    )
    db.commit()
    cur.close()
    return jsonify({'message': 'Removed from favorites'})

# ── MESSAGES ──────────────────────────────────────────────────────
@app.route('/api/messages', methods=['GET'])
@login_required
def get_conversations():
    """
    Returns the latest message per conversation thread.
    Buyers use this to contact sellers before purchasing.
    """
    db  = get_db()
    cur = db.cursor()
    cur.execute("""
        SELECT DISTINCT ON (
            LEAST(m.sender_id, m.receiver_id),
            GREATEST(m.sender_id, m.receiver_id)
        )
            m.id, m.content, m.is_read, m.created_at,
            CASE WHEN m.sender_id = %s THEN m.receiver_id ELSE m.sender_id END AS other_id,
            CASE WHEN m.sender_id = %s THEN u2.username ELSE u1.username END AS other_username,
            CASE WHEN m.sender_id = %s THEN u2.avatar_url ELSE u1.avatar_url END AS other_avatar,
            p.id AS product_id, p.title AS product_title,
            (SELECT COUNT(*) FROM devmarket_messages
             WHERE receiver_id = %s AND sender_id =
               CASE WHEN m.sender_id = %s THEN m.sender_id ELSE m.receiver_id END
             AND is_read = FALSE) AS unread_count
        FROM devmarket_messages m
        JOIN devmarket_users u1 ON m.sender_id   = u1.id
        JOIN devmarket_users u2 ON m.receiver_id = u2.id
        LEFT JOIN devmarket_products p ON m.product_id = p.id
        WHERE m.sender_id = %s OR m.receiver_id = %s
        ORDER BY LEAST(m.sender_id, m.receiver_id),
                 GREATEST(m.sender_id, m.receiver_id),
                 m.created_at DESC
    """, (g.user_id,)*7)
    convs = [dict(r) for r in cur.fetchall()]
    cur.close()
    return jsonify({'conversations': convs})

@app.route('/api/messages/<int:other_id>', methods=['GET'])
@login_required
def get_conversation(other_id):
    db  = get_db()
    cur = db.cursor()
    cur.execute("""
        SELECT m.*, u.username AS sender_name, u.avatar_url AS sender_avatar
        FROM devmarket_messages m
        JOIN devmarket_users u ON m.sender_id = u.id
        WHERE (m.sender_id = %s AND m.receiver_id = %s)
           OR (m.sender_id = %s AND m.receiver_id = %s)
        ORDER BY m.created_at ASC
    """, (g.user_id, other_id, other_id, g.user_id))
    messages = [dict(r) for r in cur.fetchall()]

    # mark as read
    cur.execute("""
        UPDATE devmarket_messages SET is_read = TRUE
        WHERE receiver_id = %s AND sender_id = %s AND is_read = FALSE
    """, (g.user_id, other_id))
    db.commit()
    cur.close()
    return jsonify({'messages': messages})

@app.route('/api/messages', methods=['POST'])
@login_required
def send_message():
    data        = request.get_json() or {}
    receiver_id = data.get('receiver_id')
    content     = (data.get('content') or '').strip()
    product_id  = data.get('product_id')

    if not receiver_id or not content:
        return jsonify({'error': 'receiver_id and content required'}), 400
    if receiver_id == g.user_id:
        return jsonify({'error': 'Cannot message yourself'}), 400

    db  = get_db()
    cur = db.cursor()
    cur.execute("""
        INSERT INTO devmarket_messages (sender_id, receiver_id, product_id, content)
        VALUES (%s,%s,%s,%s) RETURNING id, created_at
    """, (g.user_id, receiver_id, product_id, content))
    row = cur.fetchone()
    db.commit()
    cur.close()
    return jsonify({'message': 'Sent', 'id': row['id'],
                    'created_at': row['created_at'].isoformat()}), 201

@app.route('/api/messages/<int:msg_id>', methods=['DELETE'])
@login_required
def delete_message(msg_id):
    db  = get_db()
    cur = db.cursor()
    cur.execute(
        "DELETE FROM devmarket_messages WHERE id = %s AND sender_id = %s",
        (msg_id, g.user_id)
    )
    db.commit()
    cur.close()
    return jsonify({'message': 'Deleted'})

# ── REVIEWS ───────────────────────────────────────────────────────
@app.route('/api/products/<int:pid>/reviews', methods=['POST'])
@login_required
def post_review(pid):
    data    = request.get_json() or {}
    rating  = data.get('rating')
    comment = (data.get('comment') or '').strip()

    if not rating or not (1 <= int(rating) <= 5):
        return jsonify({'error': 'Rating must be 1–5'}), 400

    db  = get_db()
    cur = db.cursor()
    # must have bought it
    cur.execute(
        "SELECT id FROM devmarket_orders WHERE buyer_id=%s AND product_id=%s AND status='completed'",
        (g.user_id, pid)
    )
    if not cur.fetchone():
        cur.close()
        return jsonify({'error': 'Purchase required to review'}), 403

    try:
        cur.execute("""
            INSERT INTO devmarket_reviews (product_id, reviewer_id, rating, comment)
            VALUES (%s,%s,%s,%s)
        """, (pid, g.user_id, rating, comment))
        db.commit()
        recalc_rating(pid)
        return jsonify({'message': 'Review submitted'}), 201
    except psycopg2.IntegrityError:
        db.rollback()
        return jsonify({'error': 'Already reviewed'}), 409
    finally:
        cur.close()

# ── WALLET ────────────────────────────────────────────────────────
@app.route('/api/wallet', methods=['GET'])
@login_required
def wallet_overview():
    db  = get_db()
    cur = db.cursor()
    cur.execute(
        "SELECT wallet_balance FROM devmarket_users WHERE id = %s",
        (g.user_id,)
    )
    bal = cur.fetchone()['wallet_balance']

    cur.execute("""
        SELECT * FROM devmarket_wallet_transactions
        WHERE user_id = %s ORDER BY created_at DESC LIMIT 30
    """, (g.user_id,))
    txns = [dict(r) for r in cur.fetchall()]
    cur.close()
    return jsonify({'balance': str(bal), 'transactions': txns})

@app.route('/api/wallet/fund', methods=['POST'])
@login_required
def fund_wallet():
    """
    Initiate a wallet top-up via the financial API.
    Replace the stub below with the real payment provider call.
    """
    data   = request.get_json() or {}
    amount = data.get('amount')
    if not amount or float(amount) <= 0:
        return jsonify({'error': 'Valid amount required'}), 400

    amount = Decimal(str(amount)).quantize(Decimal('0.01'))

    # ── FINANCIAL API STUB ────────────────────────────────────────
    # Replace this block with your actual payment provider SDK call.
    # Example shape (Flutterwave / Paystack / etc.):
    #
    # import requests as http
    # resp = http.post(f"{Config.PAYMENT_API_BASE}/charge", json={
    #     "amount": str(amount),
    #     "currency": "USD",
    #     "customer_email": <buyer email>,
    #     "tx_ref": f"dm_fund_{g.user_id}_{int(datetime.utcnow().timestamp())}",
    # }, headers={"Authorization": f"Bearer {Config.PAYMENT_API_KEY}"})
    # payment_data = resp.json()
    # if payment_data.get("status") != "success":
    #     return jsonify({"error": "Payment initiation failed"}), 502
    # external_tx_id = payment_data["data"]["id"]
    #
    # For now we credit immediately (sandbox behaviour):
    external_tx_id = f'sandbox_{g.user_id}_{int(datetime.utcnow().timestamp())}'
    credit_wallet(g.user_id, amount, 'wallet_fund',
                  note='Wallet top-up', ext_id=external_tx_id)
    # ─────────────────────────────────────────────────────────────

    return jsonify({'message': 'Wallet funded', 'amount': str(amount),
                    'reference': external_tx_id}), 201

@app.route('/api/wallet/withdraw', methods=['POST'])
@login_required
def withdraw():
    """
    Request a payout. 5% commission is deducted from the withdrawal amount.
    Replace the financial API stub with the real call.
    """
    data           = request.get_json() or {}
    gross_amount   = data.get('amount')
    bank_name      = (data.get('bank_name')      or '').strip()
    account_number = (data.get('account_number') or '').strip()
    account_name   = (data.get('account_name')   or '').strip()

    if not gross_amount or float(gross_amount) <= 0:
        return jsonify({'error': 'Valid amount required'}), 400
    if not bank_name or not account_number or not account_name:
        return jsonify({'error': 'Bank details required (bank_name, account_number, account_name)'}), 400

    gross      = Decimal(str(gross_amount)).quantize(Decimal('0.01'))
    commission = (gross * Config.COMMISSION_RATE).quantize(Decimal('0.01'))
    net        = gross - commission

    db  = get_db()
    cur = db.cursor()

    # lock & check balance
    cur.execute(
        "SELECT wallet_balance FROM devmarket_users WHERE id = %s FOR UPDATE",
        (g.user_id,)
    )
    row = cur.fetchone()
    if not row or Decimal(str(row['wallet_balance'])) < gross:
        cur.close()
        return jsonify({'error': 'Insufficient balance'}), 402

    try:
        # debit wallet
        cur.execute(
            "UPDATE devmarket_users SET wallet_balance = wallet_balance - %s WHERE id = %s",
            (gross, g.user_id)
        )

        # record payout request
        cur.execute("""
            INSERT INTO devmarket_payouts
                (user_id, amount, commission_amt, net_amount,
                 bank_name, account_number, account_name, status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,'pending') RETURNING id
        """, (g.user_id, gross, commission, net, bank_name, account_number, account_name))
        payout_id = cur.fetchone()['id']

        # ── FINANCIAL API STUB ────────────────────────────────────
        # Replace with real transfer call, e.g.:
        # resp = http.post(f"{Config.PAYMENT_API_BASE}/transfers", json={
        #     "amount": str(net),
        #     "currency": "USD",
        #     "account_bank": bank_name,
        #     "account_number": account_number,
        #     "narration": f"DevMarket payout #{payout_id}",
        # }, headers={"Authorization": f"Bearer {Config.PAYMENT_API_KEY}"})
        # transfer = resp.json()
        # ext_id = transfer["data"]["id"]
        ext_id = f'payout_stub_{payout_id}'
        cur.execute("""
            UPDATE devmarket_payouts SET external_tx_id = %s, status = 'processing'
            WHERE id = %s
        """, (ext_id, payout_id))
        # ─────────────────────────────────────────────────────────

        cur.execute("""
            INSERT INTO devmarket_wallet_transactions
                (user_id, type, amount, commission_amt, net_amount, reference, external_tx_id, note)
            VALUES (%s,'withdrawal', %s, %s, %s, %s, %s, %s)
        """, (g.user_id, gross, commission, net,
              f'payout_{payout_id}', ext_id,
              f'Withdrawal to {bank_name} — {account_number}'))

        db.commit()
        return jsonify({'message': 'Withdrawal initiated',
                        'gross': str(gross), 'commission': str(commission),
                        'net': str(net), 'payout_id': payout_id}), 201

    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()

@app.route('/api/wallet/payouts', methods=['GET'])
@login_required
def payout_history():
    db  = get_db()
    cur = db.cursor()
    cur.execute("""
        SELECT * FROM devmarket_payouts
        WHERE user_id = %s ORDER BY requested_at DESC
    """, (g.user_id,))
    payouts = [dict(r) for r in cur.fetchall()]
    cur.close()
    return jsonify({'payouts': payouts})

# ── SELLER DASHBOARD ──────────────────────────────────────────────
@app.route('/api/seller/stats', methods=['GET'])
@login_required
def seller_stats():
    db  = get_db()
    cur = db.cursor()

    cur.execute("""
        SELECT COALESCE(SUM(price_paid), 0)    AS gross_revenue,
               COALESCE(SUM(seller_payout), 0) AS net_revenue,
               COALESCE(SUM(commission_amt), 0) AS total_commission,
               COUNT(*)                         AS total_sales
        FROM devmarket_orders o
        JOIN devmarket_products p ON o.product_id = p.id
        WHERE p.seller_id = %s AND o.status = 'completed'
    """, (g.user_id,))
    rev = cur.fetchone()

    cur.execute("""
        SELECT COALESCE(SUM(views_count),0) AS views,
               COUNT(*) AS product_count
        FROM devmarket_products WHERE seller_id = %s
    """, (g.user_id,))
    pr = cur.fetchone()

    cur.execute("""
        SELECT DATE_TRUNC('month', o.created_at) AS month,
               SUM(o.seller_payout) AS revenue, COUNT(*) AS sales
        FROM devmarket_orders o
        JOIN devmarket_products p ON o.product_id = p.id
        WHERE p.seller_id = %s AND o.created_at > NOW() - INTERVAL '6 months'
        GROUP BY month ORDER BY month
    """, (g.user_id,))
    monthly = [dict(r) for r in cur.fetchall()]

    cur.close()
    return jsonify({
        'gross_revenue':    float(rev['gross_revenue']),
        'net_revenue':      float(rev['net_revenue']),
        'total_commission': float(rev['total_commission']),
        'total_sales':      rev['total_sales'],
        'views':            pr['views'],
        'product_count':    pr['product_count'],
        'monthly':          monthly,
    })

@app.route('/api/seller/sales', methods=['GET'])
@login_required
def seller_sales():
    limit = min(50, int(request.args.get('limit', 20)))
    db  = get_db()
    cur = db.cursor()
    cur.execute("""
        SELECT o.id, o.price_paid, o.seller_payout, o.commission_amt, o.created_at,
               p.title AS product_title,
               u.username AS buyer_name
        FROM devmarket_orders o
        JOIN devmarket_products p ON o.product_id = p.id
        JOIN devmarket_users u ON o.buyer_id = u.id
        WHERE p.seller_id = %s AND o.status = 'completed'
        ORDER BY o.created_at DESC LIMIT %s
    """, (g.user_id, limit))
    sales = [dict(r) for r in cur.fetchall()]
    cur.close()
    return jsonify({'sales': sales})

@app.route('/api/seller/top-products', methods=['GET'])
@login_required
def top_products():
    db  = get_db()
    cur = db.cursor()
    cur.execute("""
        SELECT id, title, price, sales_count, rating,
               (price * sales_count) AS gross_revenue
        FROM devmarket_products
        WHERE seller_id = %s AND status = 'active'
        ORDER BY sales_count DESC LIMIT 5
    """, (g.user_id,))
    products = [dict(r) for r in cur.fetchall()]
    cur.close()
    return jsonify({'products': products})

# ── ACTIVITY ──────────────────────────────────────────────────────
@app.route('/api/activity', methods=['GET'])
@login_required
def get_activity():
    limit = min(50, int(request.args.get('limit', 20)))
    db  = get_db()
    cur = db.cursor()
    cur.execute("""
        SELECT * FROM devmarket_activity_log
        WHERE user_id = %s ORDER BY created_at DESC LIMIT %s
    """, (g.user_id, limit))
    acts = [dict(r) for r in cur.fetchall()]
    cur.close()
    return jsonify({'activities': acts})

# ── SEARCH / DISCOVER ─────────────────────────────────────────────
@app.route('/api/discover', methods=['GET'])
def discover():
    """Featured / trending products."""
    db  = get_db()
    cur = db.cursor()

    cur.execute("""
        SELECT p.*, u.username AS seller_name
        FROM devmarket_products p
        JOIN devmarket_users u ON p.seller_id = u.id
        WHERE p.status = 'active'
        ORDER BY (p.sales_count * 0.6 + p.views_count * 0.2 + p.rating * 20) DESC
        LIMIT 20
    """)
    trending = [dict(r) for r in cur.fetchall()]

    cur.execute("""
        SELECT p.*, u.username AS seller_name
        FROM devmarket_products p
        JOIN devmarket_users u ON p.seller_id = u.id
        WHERE p.status = 'active'
        ORDER BY p.created_at DESC LIMIT 10
    """)
    newest = [dict(r) for r in cur.fetchall()]

    cur.close()
    return jsonify({'trending': trending, 'newest': newest})

# ── HEALTH ────────────────────────────────────────────────────────
@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'platform': 'DevMarket'})

# ── MAIN ──────────────────────────────────────────────────────────
if __name__ == '__main__':
    with app.app_context():
        init_db()
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False)
