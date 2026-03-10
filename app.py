"""
NestWork — Backend v4
Run:  pip install flask flask-cors flask-jwt-extended
      python app.py
Open: http://localhost:5000
"""
import os, sqlite3, hashlib, secrets
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import Flask, request, jsonify, g, render_template

try:
    from flask_cors import CORS; HAS_CORS = True
except ImportError:
    HAS_CORS = False

try:
    from flask_jwt_extended import (
        JWTManager, create_access_token,
        verify_jwt_in_request, get_jwt_identity
    )
    HAS_JWT = True
except ImportError:
    HAS_JWT = False

BASE = os.path.dirname(os.path.abspath(__file__))
DB   = os.environ.get('DB_PATH', os.path.join(BASE, 'nestwork.db'))

app = Flask(__name__, template_folder=os.path.join(BASE, 'templates'))
app.config['SECRET_KEY']               = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['JWT_SECRET_KEY']           = os.environ.get('JWT_SECRET_KEY', secrets.token_hex(32))
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = False

if HAS_CORS: CORS(app, resources={r'/api/*': {'origins': '*'}})
if HAS_JWT:  jwt = JWTManager(app)

COLORS = ['#4f8ef7','#34d399','#fb923c','#a78bfa','#f87171','#fbbf24','#38bdf8','#e879f9']

# ── DATABASE ──────────────────────────────────────────────────────────────────
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB, detect_types=sqlite3.PARSE_DECLTYPES)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA journal_mode=WAL')
        g.db.execute('PRAGMA foreign_keys=ON')
    return g.db

@app.teardown_appcontext
def close_db(e):
    db = g.pop('db', None)
    if db: db.close()

def q(sql, params=(), one=False, commit=False):
    db  = get_db()
    cur = db.execute(sql, params)
    if commit:
        db.commit(); return cur.lastrowid
    r = cur.fetchone() if one else cur.fetchall()
    return (dict(r) if r else None) if one else [dict(x) for x in r]

def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id         TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            email      TEXT UNIQUE NOT NULL,
            password   TEXT NOT NULL,
            initials   TEXT NOT NULL,
            color      TEXT NOT NULL DEFAULT '#4f8ef7',
            role       TEXT NOT NULL DEFAULT 'editor'
                       CHECK(role IN ('admin','editor','viewer')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS join_requests (
            id         TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            email      TEXT NOT NULL,
            password   TEXT NOT NULL,
            initials   TEXT NOT NULL,
            status     TEXT NOT NULL DEFAULT 'pending'
                       CHECK(status IN ('pending','approved','rejected')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS rooms (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            emoji       TEXT NOT NULL DEFAULT '🗂️',
            color       TEXT NOT NULL DEFAULT '#4f8ef7',
            created_by  TEXT NOT NULL REFERENCES users(id),
            created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS room_docs (
            id          TEXT PRIMARY KEY,
            room_id     TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
            name        TEXT NOT NULL,
            file_type   TEXT NOT NULL DEFAULT 'text',
            content     TEXT NOT NULL,
            uploaded_by TEXT NOT NULL REFERENCES users(id),
            created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS snippets (
            id          TEXT PRIMARY KEY,
            title       TEXT NOT NULL,
            type        TEXT NOT NULL DEFAULT 'code'
                        CHECK(type IN ('code','template','sop','note')),
            lang        TEXT NOT NULL DEFAULT 'text',
            author_id   TEXT NOT NULL REFERENCES users(id),
            status      TEXT NOT NULL DEFAULT 'draft'
                        CHECK(status IN ('draft','approved')),
            content     TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS proposals (
            id         TEXT PRIMARY KEY,
            snippet_id TEXT NOT NULL REFERENCES snippets(id) ON DELETE CASCADE,
            author_id  TEXT NOT NULL REFERENCES users(id),
            content    TEXT NOT NULL,
            note       TEXT NOT NULL DEFAULT '',
            status     TEXT NOT NULL DEFAULT 'pending'
                       CHECK(status IN ('pending','approved','rejected')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        -- Group chat messages (auto-expire after 8h)
        CREATE TABLE IF NOT EXISTS chat_messages (
            id         TEXT PRIMARY KEY,
            author_id  TEXT NOT NULL REFERENCES users(id),
            message    TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expires_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_docs_room   ON room_docs(room_id);
        CREATE INDEX IF NOT EXISTS idx_snip_author ON snippets(author_id);
        CREATE INDEX IF NOT EXISTS idx_prop_snip   ON proposals(snippet_id);
        CREATE INDEX IF NOT EXISTS idx_chat_exp    ON chat_messages(expires_at);
    """)
    db.commit()
    _seed(db)
    print('✅ Database ready.')

def _seed(db):
    if db.execute('SELECT COUNT(*) FROM users').fetchone()[0] > 0:
        return
    pw = _hash('password123')
    for uid_, name, email, role, ini, color in [
        ('usr_alex',  'Alex Johnson', 'alex@nestwork.io',  'admin',  'AJ', COLORS[0]),
        ('usr_sara',  'Sara Chen',    'sara@nestwork.io',  'editor', 'SC', COLORS[1]),
        ('usr_james', 'James Okafor', 'james@nestwork.io', 'editor', 'JO', COLORS[2]),
    ]:
        db.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?,CURRENT_TIMESTAMP)',
                   (uid_, name, email, pw, ini, color, role))

    for rid, name, desc, emoji, color in [
        ('room_be', 'Backend API',   'Server architecture and API standards', '🔧', COLORS[0]),
        ('room_ds', 'Design System', 'UI components and design guidelines',   '🎨', COLORS[3]),
        ('room_hr', 'HR Handbook',   'Company policies and onboarding docs',  '📋', COLORS[1]),
    ]:
        db.execute('INSERT INTO rooms VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)',
                   (rid, name, desc, emoji, color, 'usr_alex'))

    docs = [
        ('doc_1','room_be','API Standards','text',
         'REST API STANDARDS\n==================\nAll endpoints must:\n- Use kebab-case URLs\n- Return JSON with {success, data} envelope\n- Use HTTP status codes correctly\n- Require JWT authentication (except /auth)\n\nError format: {success: false, error: "message"}\nPagination: ?page=1&limit=20 returns {data, total, page, pages}',
         'usr_alex'),
        ('doc_2','room_be','Database Guidelines','text',
         'DATABASE GUIDELINES\n===================\nNaming: snake_case tables, TEXT ids with prefix (usr_, room_, snp_)\nPerformance:\n- Index all foreign keys\n- Use WAL mode for SQLite\n- Avoid SELECT * in production queries\nMigrations: never delete columns, mark as deprecated\nAlways test rollback before deploying to production',
         'usr_alex'),
        ('doc_3','room_ds','Color Tokens','text',
         'COLOR SYSTEM\n============\nPrimary: #4f8ef7  Success: #34d399  Warning: #fbbf24  Danger: #f87171\nBackground: --bg #080808, --bg2 #101010, --bg3 #161616\nBorders: --border #242424, --border2 #2e2e2e\nText: --text #efefef, --text2 #808080\nRule: always use CSS tokens, never raw hex values in components',
         'usr_sara'),
        ('doc_4','room_hr','Onboarding Checklist','text',
         'NEW MEMBER ONBOARDING\n=====================\nDay 1: Account setup, read handbook, meet team lead\nWeek 1: Security training, first PR, 1:1 with manager\nMonth 1: First project done, 30-day review\nTools required: GitHub, Linear, Figma, NestWork\nAll access requests go through IT helpdesk',
         'usr_james'),
    ]
    for d in docs:
        db.execute('INSERT INTO room_docs(id,room_id,name,file_type,content,uploaded_by) VALUES(?,?,?,?,?,?)', d)

    for sid, title, stype, lang, author, status, content, desc in [
        ('snp_jwt','JWT Auth Middleware','code','javascript','usr_alex','approved',
         "const auth = (req, res, next) => {\n  const token = req.headers.authorization?.split(' ')[1];\n  if (!token) return res.status(401).json({ error: 'No token' });\n  try { req.user = jwt.verify(token, process.env.JWT_SECRET); next(); }\n  catch { res.status(401).json({ error: 'Invalid token' }); }\n};",
         'Reusable JWT auth middleware for Express routes.'),
        ('snp_err','Global Error Handler','code','javascript','usr_james','draft',
         "const errorHandler = (err, req, res, next) => {\n  const status = err.statusCode || 500;\n  res.status(status).json({ success: false, error: err.message || 'Server error' });\n};",
         'Express global error handling middleware.'),
        ('snp_email','Welcome Email Template','template','text','usr_sara','approved',
         "Hi {{name}},\n\nWelcome to {{company}}!\n\n1. Complete your profile\n2. Join your first room\n3. Post your first snippet\n\nBest,\nThe Team",
         'Standard new member welcome email template.'),
    ]:
        db.execute('INSERT INTO snippets VALUES(?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)',
                   (sid, title, stype, lang, author, status, content, desc))

    db.execute("INSERT INTO proposals VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)",
               ('prp_1','snp_jwt','usr_sara',
                "const auth = (req, res, next) => {\n  const token = req.headers.authorization?.split(' ')[1];\n  if (!token) return res.status(401).json({ error: 'No token', code: 'NO_TOKEN' });\n  try { req.user = jwt.verify(token, process.env.JWT_SECRET); next(); }\n  catch(e) {\n    const msg = e.name === 'TokenExpiredError' ? 'Token expired' : 'Invalid token';\n    res.status(401).json({ error: msg });\n  }\n};",
                'Added distinct error codes for expired vs invalid tokens.','pending'))

    db.execute("INSERT INTO join_requests VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)",
               ('req_demo','Demo User','demo@example.com',_hash('demo123'),'DU','pending'))
    db.commit()
    print('✅ Seed data inserted.')

# ── HELPERS ───────────────────────────────────────────────────────────────────
def uid():    return secrets.token_hex(8)
def now():    return datetime.now(timezone.utc).isoformat()
def _hash(p): return hashlib.sha256(p.encode()).hexdigest()
def ok(data=None): return jsonify({'success': True, 'data': data})
def err(msg, code=400): return jsonify({'success': False, 'error': msg}), code

def expires_at_8h():
    return (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()

def enrich_snippet(s):
    if not s: return s
    author = q('SELECT id,name,initials,color FROM users WHERE id=?', (s['author_id'],), one=True)
    props  = q("""SELECT p.*,u.name as author_name,u.initials,u.color
                  FROM proposals p JOIN users u ON p.author_id=u.id
                  WHERE p.snippet_id=? ORDER BY p.created_at DESC""", (s['id'],))
    return {**s, 'author': author, 'proposals': props}

def require_auth(f):
    @wraps(f)
    def wrap(*a, **kw):
        if HAS_JWT:
            try:
                verify_jwt_in_request()
                user = q('SELECT * FROM users WHERE id=?', (get_jwt_identity(),), one=True)
            except Exception as e:
                return err(str(e), 401)
        else:
            user = q('SELECT * FROM users WHERE id=?',
                     (request.headers.get('X-User-Id','usr_alex'),), one=True)
        if not user: return err('User not found', 401)
        g.user = user
        return f(*a, **kw)
    return wrap

def admin_only(f):
    @wraps(f)
    def wrap(*a, **kw):
        if g.user['role'] != 'admin': return err('Admin only', 403)
        return f(*a, **kw)
    return wrap

def editor_up(f):
    @wraps(f)
    def wrap(*a, **kw):
        if g.user['role'] not in ('admin','editor'): return err('Editor access required', 403)
        return f(*a, **kw)
    return wrap

# ── FRONTEND ──────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')

# ── AUTH ──────────────────────────────────────────────────────────────────────
@app.route('/api/auth/request-join', methods=['POST'])
def request_join():
    b     = request.get_json(silent=True) or {}
    name  = b.get('name','').strip()
    email = b.get('email','').strip().lower()
    pw    = b.get('password','')
    if not all([name, email, pw]):  return err('All fields required')
    if len(pw) < 6:                 return err('Password must be at least 6 characters')
    if q('SELECT id FROM users WHERE email=?', (email,), one=True):
        return err('Email already has an account')
    if q("SELECT id FROM join_requests WHERE email=? AND status='pending'", (email,), one=True):
        return err('A request from this email is already pending')
    ini = ''.join(p[0].upper() for p in name.split()[:2])
    q('INSERT INTO join_requests VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)',
      ('req_'+uid(), name, email, _hash(pw), ini, 'pending'), commit=True)
    return ok({'message': 'Request submitted. An admin will review it shortly.'})

@app.route('/api/auth/login', methods=['POST'])
def login():
    b     = request.get_json(silent=True) or {}
    email = b.get('email','').strip().lower()
    pw    = b.get('password','')
    if not email or not pw: return err('Email and password required')
    user = q('SELECT * FROM users WHERE email=?', (email,), one=True)
    if not user or user['password'] != _hash(pw):
        return err('Invalid email or password')
    token = create_access_token(identity=user['id']) if HAS_JWT else 'dev-token'
    return ok({'token': token, 'user': {k: user[k] for k in ('id','name','email','initials','color','role')}})

@app.route('/api/auth/me', methods=['GET'])
@require_auth
def me():
    u = g.user
    return ok({k: u[k] for k in ('id','name','email','initials','color','role')})

# ── JOIN REQUESTS ─────────────────────────────────────────────────────────────
@app.route('/api/join-requests', methods=['GET'])
@require_auth
@admin_only
def list_requests():
    return ok(q("SELECT * FROM join_requests WHERE status='pending' ORDER BY created_at DESC"))

@app.route('/api/join-requests/<rid>/approve', methods=['POST'])
@require_auth
@admin_only
def approve_request(rid):
    r = q('SELECT * FROM join_requests WHERE id=?', (rid,), one=True)
    if not r:                    return err('Request not found', 404)
    if r['status'] != 'pending': return err(f"Already {r['status']}")
    if q('SELECT id FROM users WHERE email=?', (r['email'],), one=True):
        return err('Email already registered')
    b      = request.get_json(silent=True) or {}
    role   = b.get('role', 'editor')
    if role not in ('admin','editor','viewer'): role = 'editor'
    color  = COLORS[q('SELECT COUNT(*) as c FROM users', one=True)['c'] % len(COLORS)]
    new_id = 'usr_' + uid()
    q('INSERT INTO users VALUES(?,?,?,?,?,?,?,CURRENT_TIMESTAMP)',
      (new_id, r['name'], r['email'], r['password'], r['initials'], color, role), commit=True)
    q("UPDATE join_requests SET status='approved' WHERE id=?", (rid,), commit=True)
    return ok({'user_id': new_id, 'name': r['name'], 'role': role})

@app.route('/api/join-requests/<rid>/reject', methods=['POST'])
@require_auth
@admin_only
def reject_request(rid):
    r = q('SELECT * FROM join_requests WHERE id=?', (rid,), one=True)
    if not r:                    return err('Request not found', 404)
    if r['status'] != 'pending': return err(f"Already {r['status']}")
    q("UPDATE join_requests SET status='rejected' WHERE id=?", (rid,), commit=True)
    return ok({'message': 'Rejected'})

# ── MEMBERS ───────────────────────────────────────────────────────────────────
@app.route('/api/members', methods=['GET'])
@require_auth
def list_members():
    ms = q('SELECT id,name,email,initials,color,role,created_at FROM users ORDER BY name')
    for m in ms:
        m['snippet_count'] = q('SELECT COUNT(*) as c FROM snippets WHERE author_id=?',
                               (m['id'],), one=True)['c']
    return ok(ms)

@app.route('/api/members/<mid>', methods=['PATCH'])
@require_auth
@admin_only
def update_member(mid):
    if mid == g.user['id']: return err('Cannot change your own role')
    role = (request.get_json(silent=True) or {}).get('role')
    if role not in ('admin','editor','viewer'): return err('Invalid role')
    q('UPDATE users SET role=? WHERE id=?', (role, mid), commit=True)
    return ok({'id': mid, 'role': role})

@app.route('/api/members/<mid>', methods=['DELETE'])
@require_auth
@admin_only
def delete_member(mid):
    if mid == g.user['id']: return err('Cannot delete yourself')
    if not q('SELECT id FROM users WHERE id=?', (mid,), one=True):
        return err('Member not found', 404)
    q('DELETE FROM users WHERE id=?', (mid,), commit=True)
    return ok({'deleted': mid})

# ── ROOMS ─────────────────────────────────────────────────────────────────────
@app.route('/api/rooms', methods=['GET'])
@require_auth
def list_rooms():
    rooms = q('SELECT * FROM rooms ORDER BY name')
    for r in rooms:
        r['doc_count'] = q('SELECT COUNT(*) as c FROM room_docs WHERE room_id=?',
                           (r['id'],), one=True)['c']
    return ok(rooms)

@app.route('/api/rooms', methods=['POST'])
@require_auth
@editor_up
def create_room():
    b    = request.get_json(silent=True) or {}
    name = b.get('name','').strip()
    if not name: return err('Room name required')
    rid  = 'room_' + uid()
    col  = COLORS[q('SELECT COUNT(*) as c FROM rooms', one=True)['c'] % len(COLORS)]
    q('INSERT INTO rooms VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)',
      (rid, name, b.get('description',''), b.get('emoji','🗂️'), col, g.user['id']), commit=True)
    return ok(q('SELECT * FROM rooms WHERE id=?', (rid,), one=True)), 201

@app.route('/api/rooms/<rid>', methods=['GET'])
@require_auth
def get_room(rid):
    r = q('SELECT * FROM rooms WHERE id=?', (rid,), one=True)
    if not r: return err('Room not found', 404)
    r['docs'] = q("""SELECT d.*,u.name as uploader_name,u.initials,u.color
                     FROM room_docs d JOIN users u ON d.uploaded_by=u.id
                     WHERE d.room_id=? ORDER BY d.created_at DESC""", (rid,))
    return ok(r)

@app.route('/api/rooms/<rid>', methods=['DELETE'])
@require_auth
@admin_only
def delete_room(rid):
    if not q('SELECT id FROM rooms WHERE id=?', (rid,), one=True):
        return err('Room not found', 404)
    q('DELETE FROM rooms WHERE id=?', (rid,), commit=True)
    return ok({'deleted': rid})

# ── ROOM DOCS ─────────────────────────────────────────────────────────────────
@app.route('/api/rooms/<rid>/docs', methods=['POST'])
@require_auth
@editor_up
def upload_doc(rid):
    """Upload doc — only room creator or admin"""
    room = q('SELECT * FROM rooms WHERE id=?', (rid,), one=True)
    if not room: return err('Room not found', 404)
    # Only room creator or admin can upload
    if room['created_by'] != g.user['id'] and g.user['role'] != 'admin':
        return err('Only the room creator or admin can upload documents', 403)
    b         = request.get_json(silent=True) or {}
    name      = b.get('name','').strip()
    content   = b.get('content','').strip()
    file_type = b.get('file_type','text')
    if not name:    return err('Document name required')
    if not content: return err('Content required')
    did = 'doc_' + uid()
    q('INSERT INTO room_docs(id,room_id,name,file_type,content,uploaded_by,updated_at) VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)',
      (did, rid, name, file_type, content, g.user['id']), commit=True)
    return ok(q("""SELECT d.*,u.name as uploader_name,u.initials,u.color
                   FROM room_docs d JOIN users u ON d.uploaded_by=u.id
                   WHERE d.id=?""", (did,), one=True)), 201

@app.route('/api/rooms/<rid>/docs/<did>', methods=['PATCH'])
@require_auth
def edit_doc(rid, did):
    """Edit doc — only uploader or admin"""
    doc = q('SELECT * FROM room_docs WHERE id=? AND room_id=?', (did, rid), one=True)
    if not doc: return err('Document not found', 404)
    if doc['uploaded_by'] != g.user['id'] and g.user['role'] != 'admin':
        return err('Only the uploader or admin can edit this document', 403)
    b       = request.get_json(silent=True) or {}
    fields  = {k: b[k] for k in ('name','content','file_type') if k in b}
    if not fields: return err('Nothing to update')
    fields['updated_at'] = now()
    clause  = ', '.join(f'{k}=?' for k in fields)
    q(f'UPDATE room_docs SET {clause} WHERE id=?', (*fields.values(), did), commit=True)
    return ok(q("""SELECT d.*,u.name as uploader_name,u.initials,u.color
                   FROM room_docs d JOIN users u ON d.uploaded_by=u.id
                   WHERE d.id=?""", (did,), one=True))

@app.route('/api/rooms/<rid>/docs/<did>', methods=['DELETE'])
@require_auth
def delete_doc(rid, did):
    doc = q('SELECT * FROM room_docs WHERE id=? AND room_id=?', (did, rid), one=True)
    if not doc: return err('Document not found', 404)
    room = q('SELECT * FROM rooms WHERE id=?', (rid,), one=True)
    # uploader, room creator, or admin
    if doc['uploaded_by'] != g.user['id'] and room['created_by'] != g.user['id'] and g.user['role'] != 'admin':
        return err('Only the uploader, room creator or admin can delete', 403)
    q('DELETE FROM room_docs WHERE id=?', (did,), commit=True)
    return ok({'deleted': did})

@app.route('/api/rooms/<rid>/docs/<did>/summarize', methods=['POST'])
@require_auth
def summarize_doc(rid, did):
    """Return doc content for AI to summarize on frontend"""
    doc = q('SELECT * FROM room_docs WHERE id=? AND room_id=?', (did, rid), one=True)
    if not doc: return err('Document not found', 404)
    return ok({'name': doc['name'], 'content': doc['content'], 'file_type': doc['file_type']})

@app.route('/api/rooms/<rid>/ask', methods=['POST'])
@require_auth
def ask_room(rid):
    r = q('SELECT * FROM rooms WHERE id=?', (rid,), one=True)
    if not r: return err('Room not found', 404)
    question = (request.get_json(silent=True) or {}).get('question','').strip()
    if not question: return err('Question required')
    docs  = q('SELECT name,content FROM room_docs WHERE room_id=?', (rid,))
    if not docs:
        return ok({'context': '', 'docs': [], 'room': r['name']})
    words  = set(w for w in question.lower().split() if len(w) > 2)
    scored = []
    for d in docs:
        text  = (d['content'] or '').lower()
        score = sum(1 for w in words if w in text)
        scored.append({**d, 'score': score})
    scored.sort(key=lambda x: x['score'], reverse=True)
    top     = [d for d in scored if d['score'] > 0] or scored[:2]
    context = '\n\n---\n\n'.join(f"[{d['name']}]\n{d['content']}" for d in top[:3])
    return ok({'context': context, 'room': r['name'], 'docs': [d['name'] for d in top[:3]]})

# ── SNIPPETS ──────────────────────────────────────────────────────────────────
@app.route('/api/snippets', methods=['GET'])
@require_auth
def list_snippets():
    sql, p = 'SELECT * FROM snippets WHERE 1=1', []
    for k in ('type','status','author_id'):
        v = request.args.get(k)
        if v: sql += f' AND {k}=?'; p.append(v)
    sql += ' ORDER BY created_at DESC'
    return ok([enrich_snippet(s) for s in q(sql, p)])

@app.route('/api/snippets', methods=['POST'])
@require_auth
@editor_up
def create_snippet():
    b       = request.get_json(silent=True) or {}
    title   = b.get('title','').strip()
    content = b.get('content','').strip()
    if not title:   return err('Title required')
    if not content: return err('Content required')
    sid = 'snp_' + uid()
    q('INSERT INTO snippets VALUES(?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)',
      (sid, title, b.get('type','code'), b.get('lang','text'), g.user['id'],
       'draft', content, b.get('description', title)), commit=True)
    return ok(enrich_snippet(q('SELECT * FROM snippets WHERE id=?', (sid,), one=True))), 201

@app.route('/api/snippets/<sid>', methods=['GET'])
@require_auth
def get_snippet(sid):
    s = q('SELECT * FROM snippets WHERE id=?', (sid,), one=True)
    if not s: return err('Snippet not found', 404)
    return ok(enrich_snippet(s))

@app.route('/api/snippets/<sid>', methods=['PATCH'])
@require_auth
def update_snippet(sid):
    """Edit snippet — only the author can edit their own snippet"""
    s = q('SELECT * FROM snippets WHERE id=?', (sid,), one=True)
    if not s: return err('Snippet not found', 404)
    if s['author_id'] != g.user['id']:
        return err('Only the snippet author can edit it', 403)
    b      = request.get_json(silent=True) or {}
    fields = {k: b[k] for k in ('title','type','lang','content','description','status') if k in b}
    if not fields: return err('Nothing to update')
    fields['updated_at'] = now()
    clause = ', '.join(f'{k}=?' for k in fields)
    q(f'UPDATE snippets SET {clause} WHERE id=?', (*fields.values(), sid), commit=True)
    return ok(enrich_snippet(q('SELECT * FROM snippets WHERE id=?', (sid,), one=True)))

@app.route('/api/snippets/<sid>', methods=['DELETE'])
@require_auth
def delete_snippet(sid):
    s = q('SELECT * FROM snippets WHERE id=?', (sid,), one=True)
    if not s: return err('Snippet not found', 404)
    if s['author_id'] != g.user['id'] and g.user['role'] != 'admin':
        return err('Only the owner or admin can delete', 403)
    q('DELETE FROM snippets WHERE id=?', (sid,), commit=True)
    return ok({'deleted': sid})

# ── PROPOSALS ─────────────────────────────────────────────────────────────────
@app.route('/api/snippets/<sid>/proposals', methods=['POST'])
@require_auth
@editor_up
def create_proposal(sid):
    s = q('SELECT * FROM snippets WHERE id=?', (sid,), one=True)
    if not s: return err('Snippet not found', 404)
    if s['author_id'] == g.user['id']:
        return err('You cannot propose changes to your own snippet')
    b       = request.get_json(silent=True) or {}
    content = b.get('content','').strip()
    note    = b.get('note','').strip()
    if not content: return err('Proposed content required')
    pid = 'prp_' + uid()
    q('INSERT INTO proposals VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)',
      (pid, sid, g.user['id'], content, note or 'Proposed improvement', 'pending'), commit=True)
    return ok(q("""SELECT p.*,u.name as author_name,u.initials,u.color
                   FROM proposals p JOIN users u ON p.author_id=u.id
                   WHERE p.id=?""", (pid,), one=True)), 201

@app.route('/api/proposals/<pid>/approve', methods=['POST'])
@require_auth
def approve_proposal(pid):
    p = q('SELECT * FROM proposals WHERE id=?', (pid,), one=True)
    if not p:                    return err('Proposal not found', 404)
    if p['status'] != 'pending': return err(f"Already {p['status']}")
    s = q('SELECT * FROM snippets WHERE id=?', (p['snippet_id'],), one=True)
    if s['author_id'] != g.user['id']:
        return err('Only the snippet creator can approve proposals', 403)
    q('UPDATE snippets SET content=?,status=\'approved\',updated_at=? WHERE id=?',
      (p['content'], now(), s['id']), commit=True)
    q("UPDATE proposals SET status='approved' WHERE id=?", (pid,), commit=True)
    return ok(enrich_snippet(q('SELECT * FROM snippets WHERE id=?', (s['id'],), one=True)))

@app.route('/api/proposals/<pid>/reject', methods=['POST'])
@require_auth
def reject_proposal(pid):
    p = q('SELECT * FROM proposals WHERE id=?', (pid,), one=True)
    if not p:                    return err('Proposal not found', 404)
    if p['status'] != 'pending': return err(f"Already {p['status']}")
    s = q('SELECT * FROM snippets WHERE id=?', (p['snippet_id'],), one=True)
    if s['author_id'] != g.user['id']:
        return err('Only the snippet creator can reject proposals', 403)
    q("UPDATE proposals SET status='rejected' WHERE id=?", (pid,), commit=True)
    return ok({'proposal_id': pid, 'status': 'rejected'})

# ── GROUP CHAT ────────────────────────────────────────────────────────────────
@app.route('/api/chat/messages', methods=['GET'])
@require_auth
def get_chat_messages():
    """Return active (non-expired) messages"""
    n = now()
    # Auto-delete expired messages
    q("DELETE FROM chat_messages WHERE expires_at < ?", (n,), commit=True)
    msgs = q("""SELECT m.*,u.name,u.initials,u.color
                FROM chat_messages m JOIN users u ON m.author_id=u.id
                WHERE m.expires_at > ?
                ORDER BY m.created_at ASC""", (n,))
    return ok(msgs)

@app.route('/api/chat/messages', methods=['POST'])
@require_auth
def send_chat_message():
    b   = request.get_json(silent=True) or {}
    msg = b.get('message','').strip()
    if not msg:       return err('Message required')
    if len(msg) > 1000: return err('Message too long (max 1000 characters)')
    mid = 'msg_' + uid()
    exp = expires_at_8h()
    q('INSERT INTO chat_messages VALUES(?,?,?,CURRENT_TIMESTAMP,?)',
      (mid, g.user['id'], msg, exp), commit=True)
    result = q("""SELECT m.*,u.name,u.initials,u.color
                  FROM chat_messages m JOIN users u ON m.author_id=u.id
                  WHERE m.id=?""", (mid,), one=True)
    return ok(result), 201

@app.route('/api/chat/messages/<mid>', methods=['DELETE'])
@require_auth
def delete_chat_message(mid):
    m = q('SELECT * FROM chat_messages WHERE id=?', (mid,), one=True)
    if not m: return err('Message not found', 404)
    if m['author_id'] != g.user['id'] and g.user['role'] != 'admin':
        return err('Can only delete your own messages', 403)
    q('DELETE FROM chat_messages WHERE id=?', (mid,), commit=True)
    return ok({'deleted': mid})

# ── SEARCH ────────────────────────────────────────────────────────────────────
@app.route('/api/search', methods=['GET'])
@require_auth
def search():
    qs = request.args.get('q','').strip()
    if len(qs) < 2: return err('Query too short')
    like  = f'%{qs}%'
    snips = q("""SELECT s.*,u.name as author_name,u.initials,u.color
                 FROM snippets s JOIN users u ON s.author_id=u.id
                 WHERE s.title LIKE ? OR s.description LIKE ? OR s.content LIKE ?
                 ORDER BY s.updated_at DESC LIMIT 20""", (like,like,like))
    rooms = q('SELECT * FROM rooms WHERE name LIKE ? OR description LIKE ? LIMIT 10', (like,like))
    docs  = q("""SELECT d.*,r.name as room_name,r.emoji,r.id as room_id
                 FROM room_docs d JOIN rooms r ON d.room_id=r.id
                 WHERE d.name LIKE ? OR d.content LIKE ? LIMIT 10""", (like,like))
    return ok({'query': qs, 'snippets': snips, 'rooms': rooms, 'docs': docs})

# ── HEALTH ────────────────────────────────────────────────────────────────────
@app.route('/api/health')
def health():
    return jsonify({'status':'ok','jwt':HAS_JWT,'cors':HAS_CORS})

@app.errorhandler(404)
def nf(e):  return jsonify({'success':False,'error':'Not found'}), 404
@app.errorhandler(500)
def se(e):  return jsonify({'success':False,'error':'Server error'}), 500

# ── MAIN ──────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print('\n🪺  NestWork v4')
    print('════════════════════════')
    with app.app_context():
        init_db()
    print(f'📦  DB   : {DB}')
    print(f'🔐  JWT  : {"on" if HAS_JWT else "off"}')
    print(f'🌐  CORS : {"on" if HAS_CORS else "off"}')
    print('\n🚀  http://localhost:5000')
    print('\nDemo logins:')
    print('  alex@nestwork.io  / password123  (admin)')
    print('  sara@nestwork.io  / password123  (editor)')
    print('  james@nestwork.io / password123  (editor)\n')
    app.run(host='0.0.0.0',
            port=int(os.environ.get('PORT',5000)),
            debug=os.environ.get('DEBUG','true').lower()=='true')
