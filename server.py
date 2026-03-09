import os
import jwt
import bcrypt
import psycopg2
import cloudinary
import cloudinary.uploader
from flask import Flask, request, jsonify, g
from flask_cors import CORS
from datetime import datetime, timedelta
from functools import wraps

app = Flask(__name__)
CORS(app)

# Config
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-change-in-production')
app.config['DATABASE_URL'] = os.getenv('DATABASE_URL')
app.config['CLOUDINARY_CLOUD_NAME'] = os.getenv('CLOUDINARY_CLOUD_NAME')
app.config['CLOUDINARY_API_KEY'] = os.getenv('CLOUDINARY_API_KEY')
app.config['CLOUDINARY_API_SECRET'] = os.getenv('CLOUDINARY_API_SECRET')

cloudinary.config(
    cloud_name=app.config['CLOUDINARY_CLOUD_NAME'],
    api_key=app.config['CLOUDINARY_API_KEY'],
    api_secret=app.config['CLOUDINARY_API_SECRET']
)

# Database
class Database:
    def __init__(self):
        self.conn = psycopg2.connect(app.config['DATABASE_URL'])
        self.cursor = self.conn.cursor()
    
    def create_tables(self):
        commands = (
            """
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username VARCHAR(50) UNIQUE NOT NULL,
                email VARCHAR(100) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                avatar_url TEXT DEFAULT NULL,
                bio TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS posts (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                title VARCHAR(255) NOT NULL,
                description TEXT,
                price DECIMAL(10,2) DEFAULT 0,
                media_urls TEXT[] DEFAULT '{}',
                tags TEXT[] DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS comments (
                id SERIAL PRIMARY KEY,
                post_id INTEGER REFERENCES posts(id) ON DELETE CASCADE,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                parent_id INTEGER REFERENCES comments(id) ON DELETE CASCADE DEFAULT NULL,
                text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS votes (
                id SERIAL PRIMARY KEY,
                post_id INTEGER REFERENCES posts(id) ON DELETE CASCADE,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                value INTEGER CHECK (value IN (1, -1)),
                UNIQUE(post_id, user_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                sender_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                receiver_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                text TEXT,
                media_url TEXT,
                is_read BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        for command in commands:
            self.cursor.execute(command)
        self.conn.commit()
    
    def execute(self, query, params=None):
        self.cursor.execute(query, params)
        self.conn.commit()
        return self.cursor
    
    def fetchone(self):
        return self.cursor.fetchone()
    
    def fetchall(self):
        return self.cursor.fetchall()

db = Database()

# Auth Helpers
def hash_password(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def check_password(password, hashed):
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))

def encode_jwt(user_id):
    payload = {
        'user_id': user_id,
        'exp': datetime.utcnow() + timedelta(days=7)
    }
    return jwt.encode(payload, app.config['SECRET_KEY'], algorithm='HS256')

def decode_jwt(token):
    try:
        return jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
    except:
        return None

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        if not token:
            return jsonify({'error': 'No token'}), 401
        payload = decode_jwt(token)
        if not payload:
            return jsonify({'error': 'Invalid token'}), 401
        g.user_id = payload['user_id']
        return f(*args, **kwargs)
    return decorated

# Cloudinary
def upload_media(file):
    try:
        result = cloudinary.uploader.upload(file, resource_type="auto")
        return result['secure_url']
    except:
        return None

# Routes

@app.route('/')
def health():
    return jsonify({'status': 'DevMarket API running'})

# Auth Routes

@app.route('/auth/register', methods=['POST'])
def register():
    data = request.get_json()
    username = data.get('username')
    email = data.get('email')
    password = data.get('password')
    
    if not all([username, email, password]):
        return jsonify({'error': 'Missing fields'}), 400
    
    password_hash = hash_password(password)
    
    try:
        db.execute(
            "INSERT INTO users (username, email, password_hash) VALUES (%s, %s, %s) RETURNING id",
            (username, email, password_hash)
        )
        user_id = db.fetchone()[0]
        token = encode_jwt(user_id)
        return jsonify({'token': token, 'user_id': user_id}), 201
    except psycopg2.IntegrityError:
        return jsonify({'error': 'Username or email exists'}), 409

@app.route('/auth/login', methods=['POST'])
def login():
    data = request.get_json()
    email = data.get('email')
    password = data.get('password')
    
    db.execute("SELECT id, password_hash FROM users WHERE email = %s", (email,))
    result = db.fetchone()
    
    if not result or not check_password(password, result[1]):
        return jsonify({'error': 'Invalid credentials'}), 401
    
    token = encode_jwt(result[0])
    return jsonify({'token': token, 'user_id': result[0]})

@app.route('/auth/me', methods=['GET'])
@require_auth
def get_me():
    db.execute("SELECT id, username, email, avatar_url, bio, created_at FROM users WHERE id = %s", (g.user_id,))
    user = db.fetchone()
    return jsonify({
        'id': user[0],
        'username': user[1],
        'email': user[2],
        'avatar_url': user[3],
        'bio': user[4],
        'created_at': user[5].isoformat()
    })

# Post Routes

@app.route('/posts', methods=['GET'])
def get_posts():
    # Get current user id from token if provided
    user_id = None
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    if token:
        payload = decode_jwt(token)
        if payload:
            user_id = payload['user_id']
    
    db.execute("""
        SELECT p.id, p.title, p.description, p.price, p.media_urls, p.tags, p.created_at,
               u.id, u.username, u.avatar_url,
               COALESCE(SUM(v.value), 0) as vote_count,
               COUNT(DISTINCT c.id) as comment_count
        FROM posts p
        JOIN users u ON p.user_id = u.id
        LEFT JOIN votes v ON p.id = v.post_id
        LEFT JOIN comments c ON p.id = c.post_id
        GROUP BY p.id, u.id
        ORDER BY p.created_at DESC
    """)
    posts = db.fetchall()
    
    result = []
    for post in posts:
        # Check if current user voted
        user_vote = 0
        if user_id:
            db.execute("SELECT value FROM votes WHERE post_id = %s AND user_id = %s", (post[0], user_id))
            vote_result = db.fetchone()
            if vote_result:
                user_vote = vote_result[0]
        
        result.append({
            'id': post[0],
            'title': post[1],
            'description': post[2],
            'price': float(post[3]),
            'media_urls': post[4],
            'tags': post[5],
            'created_at': post[6].isoformat(),
            'user': {
                'id': post[7],
                'username': post[8],
                'avatar_url': post[9]
            },
            'vote_count': post[10],
            'comment_count': post[11],
            'user_vote': user_vote
        })
    return jsonify(result)

@app.route('/posts/<int:post_id>', methods=['GET'])
def get_post(post_id):
    db.execute("""
        SELECT p.id, p.title, p.description, p.price, p.media_urls, p.tags, p.created_at,
               u.id, u.username, u.avatar_url
        FROM posts p
        JOIN users u ON p.user_id = u.id
        WHERE p.id = %s
    """, (post_id,))
    post = db.fetchone()
    
    if not post:
        return jsonify({'error': 'Not found'}), 404
    
    # Get comments with replies
    db.execute("""
        SELECT c.id, c.text, c.created_at, c.parent_id,
               u.id, u.username, u.avatar_url
        FROM comments c
        JOIN users u ON c.user_id = u.id
        WHERE c.post_id = %s
        ORDER BY c.created_at ASC
    """, (post_id,))
    comments = db.fetchall()
    
    comment_list = []
    replies_map = {}
    
    for c in comments:
        comment_data = {
            'id': c[0],
            'text': c[1],
            'created_at': c[2].isoformat(),
            'parent_id': c[3],
            'user': {
                'id': c[4],
                'username': c[5],
                'avatar_url': c[6]
            },
            'replies': []
        }
        if c[3]:
            if c[3] in replies_map:
                replies_map[c[3]].append(comment_data)
        else:
            comment_list.append(comment_data)
            replies_map[c[0]] = comment_data['replies']
    
    return jsonify({
        'id': post[0],
        'title': post[1],
        'description': post[2],
        'price': float(post[3]),
        'media_urls': post[4],
        'tags': post[5],
        'created_at': post[6].isoformat(),
        'user': {
            'id': post[7],
            'username': post[8],
            'avatar_url': post[9]
        },
        'comments': comment_list
    })

@app.route('/posts', methods=['POST'])
@require_auth
def create_post():
    title = request.form.get('title')
    description = request.form.get('description')
    price = request.form.get('price', 0)
    tags = request.form.get('tags', '').split(',')
    tags = [t.strip() for t in tags if t.strip()]
    
    # Upload media files
    media_urls = []
    if 'media' in request.files:
        files = request.files.getlist('media')
        for file in files:
            url = upload_media(file)
            if url:
                media_urls.append(url)
    
    db.execute("""
        INSERT INTO posts (user_id, title, description, price, media_urls, tags)
        VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
    """, (g.user_id, title, description, price, media_urls, tags))
    
    post_id = db.fetchone()[0]
    return jsonify({'id': post_id}), 201

@app.route('/posts/<int:post_id>', methods=['DELETE'])
@require_auth
def delete_post(post_id):
    db.execute("DELETE FROM posts WHERE id = %s AND user_id = %s RETURNING id", (post_id, g.user_id))
    if not db.fetchone():
        return jsonify({'error': 'Not found or not authorized'}), 403
    return jsonify({'message': 'Deleted'})

# Vote Routes

@app.route('/posts/<int:post_id>/vote', methods=['POST'])
@require_auth
def vote_post(post_id):
    data = request.get_json()
    value = data.get('value')
    
    if value not in [1, -1]:
        return jsonify({'error': 'Invalid value'}), 400
    
    # Check existing vote
    db.execute("SELECT id, value FROM votes WHERE post_id = %s AND user_id = %s", (post_id, g.user_id))
    existing = db.fetchone()
    
    if existing:
        if existing[1] == value:
            # Remove vote (toggle off)
            db.execute("DELETE FROM votes WHERE id = %s", (existing[0],))
        else:
            # Change vote
            db.execute("UPDATE votes SET value = %s WHERE id = %s", (value, existing[0]))
    else:
        db.execute("INSERT INTO votes (post_id, user_id, value) VALUES (%s, %s, %s)", (post_id, g.user_id, value))
    
    return jsonify({'message': 'Voted'})

# Comment Routes

@app.route('/posts/<int:post_id>/comment', methods=['POST'])
@require_auth
def add_comment(post_id):
    data = request.get_json()
    text = data.get('text')
    parent_id = data.get('parent_id')
    
    if not text:
        return jsonify({'error': 'Text required'}), 400
    
    db.execute("""
        INSERT INTO comments (post_id, user_id, parent_id, text) VALUES (%s, %s, %s, %s) RETURNING id
    """, (post_id, g.user_id, parent_id, text))
    
    comment_id = db.fetchone()[0]
    return jsonify({'id': comment_id}), 201

# Message Routes

@app.route('/messages', methods=['GET'])
@require_auth
def get_conversations():
    db.execute("""
        SELECT DISTINCT ON (other_id)
            other_id,
            u.username,
            u.avatar_url,
            m.text as last_message,
            m.created_at as last_time,
            (SELECT COUNT(*) FROM messages WHERE sender_id = other_id AND receiver_id = %s AND is_read = FALSE) as unread_count
        FROM (
            SELECT sender_id as other_id, text, created_at
            FROM messages WHERE receiver_id = %s
            UNION
            SELECT receiver_id as other_id, text, created_at
            FROM messages WHERE sender_id = %s
        ) m
        JOIN users u ON m.other_id = u.id
        ORDER BY other_id, m.created_at DESC
    """, (g.user_id, g.user_id, g.user_id))
    
    conversations = db.fetchall()
    result = []
    for c in conversations:
        result.append({
            'user_id': c[0],
            'username': c[1],
            'avatar_url': c[2],
            'last_message': c[3],
            'last_time': c[4].isoformat() if c[4] else None,
            'unread_count': c[5]
        })
    return jsonify(result)

@app.route('/messages/<int:user_id>', methods=['GET'])
@require_auth
def get_messages(user_id):
    db.execute("""
        SELECT m.id, m.text, m.media_url, m.is_read, m.created_at,
               m.sender_id, u.username, u.avatar_url
        FROM messages m
        JOIN users u ON m.sender_id = u.id
        WHERE (m.sender_id = %s AND m.receiver_id = %s) OR (m.sender_id = %s AND m.receiver_id = %s)
        ORDER BY m.created_at ASC
    """, (g.user_id, user_id, user_id, g.user_id))
    
    messages = db.fetchall()
    result = []
    for m in messages:
        result.append({
            'id': m[0],
            'text': m[1],
            'media_url': m[2],
            'is_read': m[3],
            'created_at': m[4].isoformat(),
            'sender': {
                'id': m[5],
                'username': m[6],
                'avatar_url': m[7]
            },
            'is_me': m[5] == g.user_id
        })
    
    # Mark as read
    db.execute("UPDATE messages SET is_read = TRUE WHERE sender_id = %s AND receiver_id = %s", (user_id, g.user_id))
    
    return jsonify(result)

@app.route('/messages', methods=['POST'])
@require_auth
def send_message():
    data = request.get_json()
    receiver_id = data.get('receiver_id')
    text = data.get('text')
    
    if not receiver_id or not text:
        return jsonify({'error': 'Missing fields'}), 400
    
    db.execute("""
        INSERT INTO messages (sender_id, receiver_id, text) VALUES (%s, %s, %s) RETURNING id
    """, (g.user_id, receiver_id, text))
    
    msg_id = db.fetchone()[0]
    return jsonify({'id': msg_id}), 201

# User Routes

@app.route('/users/<int:user_id>', methods=['GET'])
def get_user(user_id):
    db.execute("SELECT id, username, avatar_url, bio, created_at FROM users WHERE id = %s", (user_id,))
    user = db.fetchone()
    
    if not user:
        return jsonify({'error': 'Not found'}), 404
    
    # Get user's posts
    db.execute("""
        SELECT p.id, p.title, p.description, p.price, p.media_urls, p.tags, p.created_at,
               COALESCE(SUM(v.value), 0) as vote_count
        FROM posts p
        LEFT JOIN votes v ON p.id = v.post_id
        WHERE p.user_id = %s
        GROUP BY p.id
        ORDER BY p.created_at DESC
    """, (user_id,))
    posts = db.fetchall()
    
    post_list = []
    for p in posts:
        post_list.append({
            'id': p[0],
            'title': p[1],
            'description': p[2],
            'price': float(p[3]),
            'media_urls': p[4],
            'tags': p[5],
            'created_at': p[6].isoformat(),
            'vote_count': p[7]
        })
    
    return jsonify({
        'id': user[0],
        'username': user[1],
        'avatar_url': user[2],
        'bio': user[3],
        'created_at': user[4].isoformat(),
        'posts': post_list
    })

@app.route('/users/me', methods=['PUT'])
@require_auth
def update_profile():
    data = request.get_json()
    bio = data.get('bio', '')
    
    db.execute("UPDATE users SET bio = %s WHERE id = %s", (bio, g.user_id))
    return jsonify({'message': 'Updated'})

# Init
if __name__ == '__main__':
    db.create_tables()
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
'''
DATABASE_URL=postgresql://user:pass@host:5432/dbname
SECRET_KEY=your-secret-key
CLOUDINARY_CLOUD_NAME=your-cloud
CLOUDINARY_API_KEY=your-key
CLOUDINARY_API_SECRET=your-secret
'''