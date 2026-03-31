"""
Devmarket Backend - Flask + PostgreSQL
Single file backend with all necessary features
"""

import os
import re
import json
import jwt
import bcrypt
import psycopg2
import cloudinary
import cloudinary.uploader
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, request, jsonify, g
from flask_cors import CORS
from werkzeug.utils import secure_filename
from psycopg2.extras import RealDictCursor

# Configuration
class Config:
    SECRET_KEY = os.environ.get('JWT_SECRET', 'your-secret-key-change-in-production')
    DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://user:password@localhost/devmarket')
    CLOUDINARY_CLOUD_NAME = os.environ.get('CLOUDINARY_CLOUD_NAME', '')
    CLOUDINARY_API_KEY = os.environ.get('CLOUDINARY_API_KEY', '')
    CLOUDINARY_API_SECRET = os.environ.get('CLOUDINARY_API_SECRET', '')
    ALLOWED_EXTENSIONS = {'zip', 'json', 'txt', 'md'}
    MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

# Initialize Flask
app = Flask(__name__)
app.config.from_object(Config)
CORS(app, origins=["*"])  # Allow GitHub Pages

# Initialize Cloudinary
if Config.CLOUDINARY_CLOUD_NAME:
    cloudinary.config(
        cloud_name=Config.CLOUDINARY_CLOUD_NAME,
        api_key=Config.CLOUDINARY_API_KEY,
        api_secret=Config.CLOUDINARY_API_SECRET
    )

# Database Connection
def get_db():
    if 'db' not in g:
        g.db = psycopg2.connect(Config.DATABASE_URL, cursor_factory=RealDictCursor)
    return g.db

@app.teardown_appcontext
def close_db(error):
    if 'db' in g:
        g.db.close()

# Database Initialization
def init_db():
    """Create tables if they don't exist"""
    db = get_db()
    cursor = db.cursor()

    # Users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username VARCHAR(50) UNIQUE NOT NULL,
            email VARCHAR(100) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            role VARCHAR(20) DEFAULT 'user',
            avatar_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Products table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id SERIAL PRIMARY KEY,
            title VARCHAR(200) NOT NULL,
            description TEXT,
            price DECIMAL(10,2) NOT NULL,
            category VARCHAR(50) NOT NULL,
            seller_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            image_url TEXT,
            file_url TEXT,
            file_public_id TEXT,
            rating DECIMAL(2,1) DEFAULT 0,
            sales_count INTEGER DEFAULT 0,
            views_count INTEGER DEFAULT 0,
            tags TEXT[],
            status VARCHAR(20) DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Orders table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id SERIAL PRIMARY KEY,
            buyer_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            product_id INTEGER REFERENCES products(id) ON DELETE CASCADE,
            price_paid DECIMAL(10,2) NOT NULL,
            status VARCHAR(20) DEFAULT 'completed',
            download_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Cart items table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cart_items (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            product_id INTEGER REFERENCES products(id) ON DELETE CASCADE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, product_id)
        )
    """)

    # Favorites table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS favorites (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            product_id INTEGER REFERENCES products(id) ON DELETE CASCADE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, product_id)
        )
    """)

    # Messages table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY,
            sender_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            receiver_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
            content TEXT NOT NULL,
            is_read BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Reviews table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reviews (
            id SERIAL PRIMARY KEY,
            product_id INTEGER REFERENCES products(id) ON DELETE CASCADE,
            reviewer_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            rating INTEGER CHECK (rating >= 1 AND rating <= 5),
            comment TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(product_id, reviewer_id)
        )
    """)

    # Activity log table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS activity_log (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            type VARCHAR(50) NOT NULL,
            description TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    db.commit()
    cursor.close()
    print("Database initialized successfully!")

# JWT Authentication
def generate_token(user_id):
    payload = {
        'user_id': user_id,
        'exp': datetime.utcnow() + timedelta(days=7),
        'iat': datetime.utcnow()
    }
    return jwt.encode(payload, app.config['SECRET_KEY'], algorithm='HS256')

def verify_token(token):
    try:
        payload = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
        return payload['user_id']
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        if 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            try:
                token = auth_header.split(" ")[1]
            except IndexError:
                return jsonify({'error': 'Invalid token format'}), 401

        if not token:
            return jsonify({'error': 'Token is missing'}), 401

        user_id = verify_token(token)
        if not user_id:
            return jsonify({'error': 'Token is invalid or expired'}), 401

        g.user_id = user_id
        return f(*args, **kwargs)
    return decorated

# Helper Functions
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def log_activity(user_id, activity_type, description):
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "INSERT INTO activity_log (user_id, type, description) VALUES (%s, %s, %s)",
        (user_id, activity_type, description)
    )
    db.commit()
    cursor.close()

def update_product_rating(product_id):
    """Recalculate product rating based on reviews"""
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "SELECT AVG(rating) as avg_rating, COUNT(*) as count FROM reviews WHERE product_id = %s",
        (product_id,)
    )
    result = cursor.fetchone()
    if result and result['count'] > 0:
        cursor.execute(
            "UPDATE products SET rating = %s WHERE id = %s",
            (round(result['avg_rating'], 1), product_id)
        )
        db.commit()
    cursor.close()

# ==================== AUTH ROUTES ====================

@app.route('/api/auth/register', methods=['POST'])
def register():
    data = request.get_json()

    # Validation
    if not data or not data.get('username') or not data.get('email') or not data.get('password'):
        return jsonify({'error': 'Missing required fields'}), 400

    username = data['username'].strip()
    email = data['email'].strip().lower()
    password = data['password']

    if len(password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400

    if not re.match(r'^[a-zA-Z0-9_]+$', username):
        return jsonify({'error': 'Username can only contain letters, numbers, and underscores'}), 400

    # Hash password
    password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute(
            "INSERT INTO users (username, email, password_hash, role) VALUES (%s, %s, %s, %s) RETURNING id",
            (username, email, password_hash, 'user')
        )
        user_id = cursor.fetchone()['id']
        db.commit()

        token = generate_token(user_id)
        log_activity(user_id, 'register', 'User registered successfully')

        return jsonify({
            'message': 'User registered successfully',
            'token': token,
            'user': {
                'id': user_id,
                'username': username,
                'email': email,
                'role': 'user'
            }
        }), 201

    except psycopg2.IntegrityError:
        db.rollback()
        return jsonify({'error': 'Username or email already exists'}), 409
    finally:
        cursor.close()

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.get_json()

    if not data or not data.get('email') or not data.get('password'):
        return jsonify({'error': 'Missing email or password'}), 400

    email = data['email'].strip().lower()
    password = data['password']

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "SELECT id, username, email, password_hash, role FROM users WHERE email = %s",
        (email,)
    )
    user = cursor.fetchone()
    cursor.close()

    if not user:
        return jsonify({'error': 'Invalid credentials'}), 401

    if not bcrypt.checkpw(password.encode('utf-8'), user['password_hash'].encode('utf-8')):
        return jsonify({'error': 'Invalid credentials'}), 401

    token = generate_token(user['id'])
    log_activity(user['id'], 'login', 'User logged in')

    return jsonify({
        'message': 'Login successful',
        'token': token,
        'user': {
            'id': user['id'],
            'username': user['username'],
            'email': user['email'],
            'role': user['role']
        }
    })

@app.route('/api/auth/me', methods=['GET'])
@login_required
def get_current_user():
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "SELECT id, username, email, role, avatar_url, created_at FROM users WHERE id = %s",
        (g.user_id,)
    )
    user = cursor.fetchone()
    cursor.close()

    if not user:
        return jsonify({'error': 'User not found'}), 404

    return jsonify({'user': dict(user)})

# ==================== PRODUCT ROUTES ====================

@app.route('/api/products', methods=['GET'])
def get_products():
    """Get all products with optional filtering"""
    search = request.args.get('search', '').strip()
    category = request.args.get('category', '').strip()
    sort_by = request.args.get('sort', 'created_at')
    order = request.args.get('order', 'desc')
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))

    db = get_db()
    cursor = db.cursor()

    query = """
        SELECT p.*, u.username as seller_name
        FROM products p
        JOIN users u ON p.seller_id = u.id
        WHERE p.status = 'active'
    """
    params = []

    if search:
        query += " AND (p.title ILIKE %s OR p.description ILIKE %s OR %s = ANY(p.tags))"
        params.extend([f'%{search}%', f'%{search}%', search])

    if category and category != 'all':
        query += " AND p.category = %s"
        params.append(category)

    # Sorting
    sort_column = {
        'price': 'p.price',
        'rating': 'p.rating',
        'sales': 'p.sales_count',
        'created_at': 'p.created_at'
    }.get(sort_by, 'p.created_at')

    query += f" ORDER BY {sort_column} {order.upper()}"

    # Pagination
    offset = (page - 1) * per_page
    query += " LIMIT %s OFFSET %s"
    params.extend([per_page, offset])

    cursor.execute(query, params)
    products = cursor.fetchall()

    # Get total count for pagination
    count_query = """
        SELECT COUNT(*) as total
        FROM products p
        WHERE p.status = 'active'
    """
    if search:
        count_query += " AND (p.title ILIKE %s OR p.description ILIKE %s)"
    if category and category != 'all':
        count_query += " AND p.category = %s"

    cursor.execute(count_query, params[:-2] if params else ())
    total = cursor.fetchone()['total']
    cursor.close()

    return jsonify({
        'products': [dict(p) for p in products],
        'pagination': {
            'page': page,
            'per_page': per_page,
            'total': total,
            'pages': (total + per_page - 1) // per_page
        }
    })

@app.route('/api/products/<int:product_id>', methods=['GET'])
def get_product(product_id):
    """Get single product details"""
    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
        SELECT p.*, u.username as seller_name, u.avatar_url as seller_avatar
        FROM products p
        JOIN users u ON p.seller_id = u.id
        WHERE p.id = %s AND p.status = 'active'
    """, (product_id,))

    product = cursor.fetchone()

    if not product:
        cursor.close()
        return jsonify({'error': 'Product not found'}), 404

    # Increment views
    cursor.execute("UPDATE products SET views_count = views_count + 1 WHERE id = %s", (product_id,))
    db.commit()

    # Get reviews
    cursor.execute("""
        SELECT r.*, u.username as reviewer_name
        FROM reviews r
        JOIN users u ON r.reviewer_id = u.id
        WHERE r.product_id = %s
        ORDER BY r.created_at DESC
    """, (product_id,))
    reviews = cursor.fetchall()

    cursor.close()

    product_dict = dict(product)
    product_dict['reviews'] = [dict(r) for r in reviews]

    return jsonify({'product': product_dict})

@app.route('/api/products', methods=['POST'])
@login_required
def create_product():
    """Create new product (seller only)"""
    data = request.get_json()

    if not data or not data.get('title') or not data.get('price'):
        return jsonify({'error': 'Missing required fields'}), 400

    title = data['title'].strip()
    description = data.get('description', '').strip()
    price = float(data['price'])
    category = data.get('category', 'other').strip()
    tags = data.get('tags', [])
    image_url = data.get('image_url', '')
    file_url = data.get('file_url', '')

    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        INSERT INTO products (title, description, price, category, seller_id, image_url, file_url, tags)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (title, description, price, category, g.user_id, image_url, file_url, tags))

    product_id = cursor.fetchone()['id']
    db.commit()
    cursor.close()

    log_activity(g.user_id, 'product_created', f'Created product: {title}')

    return jsonify({
        'message': 'Product created successfully',
        'product_id': product_id
    }), 201

@app.route('/api/products/<int:product_id>', methods=['PUT'])
@login_required
def update_product(product_id):
    """Update product (seller only)"""
    data = request.get_json()

    db = get_db()
    cursor = db.cursor()

    # Check ownership
    cursor.execute("SELECT seller_id FROM products WHERE id = %s", (product_id,))
    product = cursor.fetchone()

    if not product:
        cursor.close()
        return jsonify({'error': 'Product not found'}), 404

    if product['seller_id'] != g.user_id:
        cursor.close()
        return jsonify({'error': 'Unauthorized'}), 403

    # Update fields
    allowed_fields = ['title', 'description', 'price', 'category', 'tags', 'image_url', 'status']
    updates = []
    values = []

    for field in allowed_fields:
        if field in data:
            updates.append(f"{field} = %s")
            values.append(data[field])

    if not updates:
        cursor.close()
        return jsonify({'error': 'No fields to update'}), 400

    values.append(product_id)
    query = f"UPDATE products SET {', '.join(updates)}, updated_at = CURRENT_TIMESTAMP WHERE id = %s"
    cursor.execute(query, values)
    db.commit()
    cursor.close()

    return jsonify({'message': 'Product updated successfully'})

@app.route('/api/products/my', methods=['GET'])
@login_required
def get_my_products():
    """Get current user's products (for seller dashboard)"""
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        SELECT * FROM products
        WHERE seller_id = %s
        ORDER BY created_at DESC
    """, (g.user_id,))
    products = cursor.fetchall()
    cursor.close()
    return jsonify({'products': [dict(p) for p in products]})

# ==================== FILE UPLOAD ====================

@app.route('/api/upload', methods=['POST'])
@login_required
def upload_file():
    """Upload file to Cloudinary"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    upload_type = request.form.get('type', 'file')  # 'file' or 'image'

    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    if not allowed_file(file.filename) and upload_type == 'file':
        return jsonify({'error': 'File type not allowed'}), 400

    try:
        # Upload to Cloudinary
        if upload_type == 'image':
            result = cloudinary.uploader.upload(file, folder='devmarket/products')
        else:
            result = cloudinary.uploader.upload(
                file,
                folder='devmarket/files',
                resource_type='raw'
            )

        return jsonify({
            'url': result['secure_url'],
            'public_id': result['public_id']
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ==================== CART ROUTES ====================

@app.route('/api/cart', methods=['GET'])
@login_required
def get_cart():
    """Get user's cart"""
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        SELECT p.*, c.id as cart_item_id
        FROM cart_items c
        JOIN products p ON c.product_id = p.id
        WHERE c.user_id = %s
    """, (g.user_id,))
    items = cursor.fetchall()
    cursor.close()
    return jsonify({'cart': [dict(i) for i in items]})

@app.route('/api/cart', methods=['POST'])
@login_required
def add_to_cart():
    """Add item to cart"""
    data = request.get_json()
    product_id = data.get('product_id')

    if not product_id:
        return jsonify({'error': 'Product ID required'}), 400

    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute(
            "INSERT INTO cart_items (user_id, product_id) VALUES (%s, %s)",
            (g.user_id, product_id)
        )
        db.commit()
        return jsonify({'message': 'Added to cart'}), 201
    except psycopg2.IntegrityError:
        db.rollback()
        return jsonify({'error': 'Item already in cart'}), 409
    finally:
        cursor.close()

@app.route('/api/cart/<int:item_id>', methods=['DELETE'])
@login_required
def remove_from_cart(item_id):
    """Remove item from cart"""
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "DELETE FROM cart_items WHERE id = %s AND user_id = %s",
        (item_id, g.user_id)
    )
    db.commit()
    cursor.close()
    return jsonify({'message': 'Removed from cart'})

# ==================== ORDER ROUTES ====================

@app.route('/api/orders', methods=['POST'])
@login_required
def create_order():
    """Create order from cart (checkout)"""
    data = request.get_json()
    items = data.get('items', [])  # Array of product_ids

    if not items:
        return jsonify({'error': 'No items to purchase'}), 400

    db = get_db()
    cursor = db.cursor()

    try:
        orders = []
        for product_id in items:
            # Get product price
            cursor.execute("SELECT price, seller_id, title FROM products WHERE id = %s", (product_id,))
            product = cursor.fetchone()

            if not product:
                continue

            # Create order
            cursor.execute("""
                INSERT INTO orders (buyer_id, product_id, price_paid, status)
                VALUES (%s, %s, %s, 'completed')
                RETURNING id
            """, (g.user_id, product_id, product['price']))
            order_id = cursor.fetchone()['id']
            orders.append(order_id)

            # Update product sales count
            cursor.execute(
                "UPDATE products SET sales_count = sales_count + 1 WHERE id = %s",
                (product_id,)
            )

            # Log activity
            log_activity(g.user_id, 'purchase', f'Purchased {product["title"]}')
            log_activity(product['seller_id'], 'sale', f'Sold {product["title"]}')

        # Clear cart
        cursor.execute("DELETE FROM cart_items WHERE user_id = %s", (g.user_id,))

        db.commit()
        return jsonify({'message': 'Order completed', 'orders': orders})

    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()

@app.route('/api/orders/my', methods=['GET'])
@login_required
def get_my_orders():
    """Get user's purchase history (library)"""
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        SELECT o.*, p.title, p.description, p.image_url, p.file_url, u.username as seller_name
        FROM orders o
        JOIN products p ON o.product_id = p.id
        JOIN users u ON p.seller_id = u.id
        WHERE o.buyer_id = %s
        ORDER BY o.created_at DESC
    """, (g.user_id,))
    orders = cursor.fetchall()
    cursor.close()
    return jsonify({'orders': [dict(o) for o in orders]})

@app.route('/api/orders/<int:order_id>/download', methods=['GET'])
@login_required
def download_order(order_id):
    """Download purchased product"""
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        SELECT o.*, p.file_url, p.title
        FROM orders o
        JOIN products p ON o.product_id = p.id
        WHERE o.id = %s AND o.buyer_id = %s
    """, (order_id, g.user_id))
    order = cursor.fetchone()

    if not order:
        cursor.close()
        return jsonify({'error': 'Order not found or unauthorized'}), 404

    # Update download count
    cursor.execute(
        "UPDATE orders SET download_count = download_count + 1 WHERE id = %s",
        (order_id,)
    )
    db.commit()
    cursor.close()

    return jsonify({
        'download_url': order['file_url'],
        'product_title': order['title']
    })

# ==================== SELLER DASHBOARD ====================

@app.route('/api/seller/stats', methods=['GET'])
@login_required
def get_seller_stats():
    """Get seller dashboard statistics"""
    db = get_db()
    cursor = db.cursor()

    # Total revenue
    cursor.execute("""
        SELECT COALESCE(SUM(price_paid), 0) as revenue, COUNT(*) as sales
        FROM orders o
        JOIN products p ON o.product_id = p.id
        WHERE p.seller_id = %s AND o.status = 'completed'
    """, (g.user_id,))
    stats = cursor.fetchone()

    # Total views across all products
    cursor.execute("""
        SELECT COALESCE(SUM(views_count), 0) as views, COUNT(*) as products
        FROM products WHERE seller_id = %s
    """, (g.user_id,))
    product_stats = cursor.fetchone()

    # Monthly revenue for chart
    cursor.execute("""
        SELECT DATE_TRUNC('month', o.created_at) as month, SUM(o.price_paid) as revenue
        FROM orders o
        JOIN products p ON o.product_id = p.id
        WHERE p.seller_id = %s AND o.created_at > NOW() - INTERVAL '6 months'
        GROUP BY month
        ORDER BY month
    """, (g.user_id,))
    monthly = cursor.fetchall()

    cursor.close()

    return jsonify({
        'revenue': float(stats['revenue']),
        'sales': stats['sales'],
        'views': product_stats['views'],
        'products': product_stats['products'],
        'monthly_revenue': [dict(m) for m in monthly]
    })

@app.route('/api/seller/sales', methods=['GET'])
@login_required
def get_recent_sales():
    """Get recent sales for seller"""
    limit = int(request.args.get('limit', 10))

    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        SELECT o.*, p.title as product_title, u.username as buyer_name
        FROM orders o
        JOIN products p ON o.product_id = p.id
        JOIN users u ON o.buyer_id = u.id
        WHERE p.seller_id = %s AND o.status = 'completed'
        ORDER BY o.created_at DESC
        LIMIT %s
    """, (g.user_id, limit))
    sales = cursor.fetchall()
    cursor.close()
    return jsonify({'sales': [dict(s) for s in sales]})

@app.route('/api/seller/top-products', methods=['GET'])
@login_required
def get_top_products():
    """Get top selling products for seller"""
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        SELECT p.id, p.title, p.price, p.sales_count, p.rating,
               (p.price * p.sales_count) as total_revenue
        FROM products p
        WHERE p.seller_id = %s AND p.status = 'active'
        ORDER BY p.sales_count DESC
        LIMIT 5
    """, (g.user_id,))
    products = cursor.fetchall()
    cursor.close()
    return jsonify({'products': [dict(p) for p in products]})

# ==================== FAVORITES ====================

@app.route('/api/favorites', methods=['GET'])
@login_required
def get_favorites():
    """Get user's favorite products"""
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        SELECT p.*, u.username as seller_name
        FROM favorites f
        JOIN products p ON f.product_id = p.id
        JOIN users u ON p.seller_id = u.id
        WHERE f.user_id = %s
        ORDER BY f.created_at DESC
    """, (g.user_id,))
    favorites = cursor.fetchall()
    cursor.close()
    return jsonify({'favorites': [dict(f) for f in favorites]})

@app.route('/api/favorites', methods=['POST'])
@login_required
def add_favorite():
    """Add product to favorites"""
    data = request.get_json()
    product_id = data.get('product_id')

    if not product_id:
        return jsonify({'error': 'Product ID required'}), 400

    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute(
            "INSERT INTO favorites (user_id, product_id) VALUES (%s, %s)",
            (g.user_id, product_id)
        )
        db.commit()
        return jsonify({'message': 'Added to favorites'}), 201
    except psycopg2.IntegrityError:
        db.rollback()
        return jsonify({'error': 'Already in favorites'}), 409
    finally:
        cursor.close()

@app.route('/api/favorites/<int:product_id>', methods=['DELETE'])
@login_required
def remove_favorite(product_id):
    """Remove from favorites"""
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "DELETE FROM favorites WHERE user_id = %s AND product_id = %s",
        (g.user_id, product_id)
    )
    db.commit()
    cursor.close()
    return jsonify({'message': 'Removed from favorites'})

# ==================== MESSAGES ====================

@app.route('/api/messages', methods=['GET'])
@login_required
def get_messages():
    """Get user's messages"""
    db = get_db()
    cursor = db.cursor()

    # Get conversations (latest message per user)
    cursor.execute("""
        SELECT DISTINCT ON (other_user)
            m.*,
            CASE 
                WHEN m.sender_id = %s THEN u2.username 
                ELSE u1.username 
            END as other_username,
            CASE 
                WHEN m.sender_id = %s THEN u2.id 
                ELSE u1.id 
            END as other_user,
            p.title as product_title
        FROM messages m
        JOIN users u1 ON m.sender_id = u1.id
        JOIN users u2 ON m.receiver_id = u2.id
        LEFT JOIN products p ON m.product_id = p.id
        WHERE m.sender_id = %s OR m.receiver_id = %s
        ORDER BY other_user, m.created_at DESC
    """, (g.user_id, g.user_id, g.user_id, g.user_id))

    messages = cursor.fetchall()
    cursor.close()
    return jsonify({'messages': [dict(m) for m in messages]})

@app.route('/api/messages/<int:user_id>', methods=['GET'])
@login_required
def get_conversation(user_id):
    """Get conversation with specific user"""
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        SELECT m.*, u.username as sender_name
        FROM messages m
        JOIN users u ON m.sender_id = u.id
        WHERE (m.sender_id = %s AND m.receiver_id = %s)
           OR (m.sender_id = %s AND m.receiver_id = %s)
        ORDER BY m.created_at ASC
    """, (g.user_id, user_id, user_id, g.user_id))
    messages = cursor.fetchall()

    # Mark as read
    cursor.execute("""
        UPDATE messages SET is_read = TRUE
        WHERE receiver_id = %s AND sender_id = %s
    """, (g.user_id, user_id))
    db.commit()
    cursor.close()

    return jsonify({'messages': [dict(m) for m in messages]})

@app.route('/api/messages', methods=['POST'])
@login_required
def send_message():
    """Send message to user"""
    data = request.get_json()
    receiver_id = data.get('receiver_id')
    content = data.get('content', '').strip()
    product_id = data.get('product_id')

    if not receiver_id or not content:
        return jsonify({'error': 'Receiver and content required'}), 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        INSERT INTO messages (sender_id, receiver_id, product_id, content)
        VALUES (%s, %s, %s, %s)
        RETURNING id
    """, (g.user_id, receiver_id, product_id, content))
    message_id = cursor.fetchone()['id']
    db.commit()
    cursor.close()

    return jsonify({'message': 'Message sent', 'id': message_id}), 201

# ==================== REVIEWS ====================

@app.route('/api/products/<int:product_id>/reviews', methods=['POST'])
@login_required
def create_review(product_id):
    """Create review for product (buyers only)"""
    data = request.get_json()
    rating = data.get('rating')
    comment = data.get('comment', '').strip()

    if not rating or not (1 <= rating <= 5):
        return jsonify({'error': 'Rating must be 1-5'}), 400

    db = get_db()
    cursor = db.cursor()

    # Verify user purchased this product
    cursor.execute("""
        SELECT id FROM orders 
        WHERE buyer_id = %s AND product_id = %s AND status = 'completed'
    """, (g.user_id, product_id))
    if not cursor.fetchone():
        cursor.close()
        return jsonify({'error': 'Must purchase product to review'}), 403

    try:
        cursor.execute("""
            INSERT INTO reviews (product_id, reviewer_id, rating, comment)
            VALUES (%s, %s, %s, %s)
        """, (product_id, g.user_id, rating, comment))
        db.commit()

        # Update product rating
        update_product_rating(product_id)

        return jsonify({'message': 'Review submitted'}), 201
    except psycopg2.IntegrityError:
        db.rollback()
        return jsonify({'error': 'Already reviewed this product'}), 409
    finally:
        cursor.close()

# ==================== ACTIVITY LOG ====================

@app.route('/api/activity', methods=['GET'])
@login_required
def get_activity():
    """Get recent activity for user"""
    limit = int(request.args.get('limit', 20))

    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        SELECT * FROM activity_log
        WHERE user_id = %s
        ORDER BY created_at DESC
        LIMIT %s
    """, (g.user_id, limit))
    activities = cursor.fetchall()
    cursor.close()
    return jsonify({'activities': [dict(a) for a in activities]})

# ==================== MAIN ====================

if __name__ == '__main__':
    with app.app_context():
        init_db()

    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)