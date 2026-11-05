import gzip, hashlib, json, os, sqlite3, tempfile
from pathlib import Path
from datetime import datetime, timezone
DB = 'foundry_zero.db'
def now(): return datetime.now(timezone.utc).isoformat()
def dump(v): return json.dumps(v, sort_keys=True, ensure_ascii=False, default=str)
class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc, tb):
        try: return super().__exit__(exc_type, exc, tb)
        finally: self.close()
def connect():
    c=sqlite3.connect(DB, timeout=30, factory=ClosingConnection); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); return c
def query(sql,args=()):
    with connect() as c: return [dict(r) for r in c.execute(sql,args)]
def execute(sql,args=()):
    with connect() as c: return c.execute(sql,args).lastrowid
def init_db():
    with connect() as c: c.executescript('''
    CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, identifiers TEXT, mode TEXT, status TEXT, created_at TEXT, error TEXT);
    CREATE TABLE IF NOT EXISTS cache(endpoint TEXT, payload_hash TEXT, payload TEXT, response TEXT, created_at TEXT, PRIMARY KEY(endpoint,payload_hash));
    CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, run_id TEXT, event TEXT, detail TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS savings(id INTEGER PRIMARY KEY, run_id TEXT, source TEXT, endpoint TEXT, amount REAL, created_at TEXT);
    CREATE TABLE IF NOT EXISTS companies(run_id TEXT, company_id TEXT, name TEXT, domain TEXT, data TEXT, PRIMARY KEY(run_id,company_id));
    CREATE TABLE IF NOT EXISTS aliases(run_id TEXT, alias TEXT, company_id TEXT, PRIMARY KEY(run_id,alias));
    CREATE TABLE IF NOT EXISTS checkpoints(run_id TEXT, scope TEXT, stage TEXT, data TEXT, created_at TEXT, PRIMARY KEY(run_id,scope,stage));
    CREATE TABLE IF NOT EXISTS citations(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, entity_id TEXT, endpoint TEXT, field TEXT, value TEXT, raw_json TEXT, created_at TEXT, prev_hash TEXT, record_hash TEXT);
    CREATE TABLE IF NOT EXISTS candidates(run_id TEXT, person_id TEXT, company_id TEXT, name TEXT, pool TEXT, profile TEXT, citations TEXT, PRIMARY KEY(run_id,person_id));
    CREATE TABLE IF NOT EXISTS matches(id TEXT PRIMARY KEY, run_id TEXT, founder_a TEXT, founder_b TEXT, score INTEGER, breakdown TEXT, pathway TEXT, thesis TEXT, safe TEXT);
    CREATE TABLE IF NOT EXISTS exception_queue(id INTEGER PRIMARY KEY, run_id TEXT, person_id TEXT, reasons TEXT, status TEXT DEFAULT 'pending', created_at TEXT, UNIQUE(run_id,person_id));
    CREATE TABLE IF NOT EXISTS meetings(id TEXT PRIMARY KEY, run_id TEXT, kind TEXT, subject_id TEXT, title TEXT, starts_at TEXT, ends_at TEXT, participants TEXT, agenda TEXT, status TEXT, version INTEGER, ics TEXT, UNIQUE(run_id,kind,subject_id));
    CREATE TABLE IF NOT EXISTS work_party_cohorts(id TEXT PRIMARY KEY, run_id TEXT, city TEXT, theme TEXT, attendees TEXT, plan TEXT, meeting_id TEXT, UNIQUE(run_id,city,theme));
    CREATE TABLE IF NOT EXISTS notifications_outbox(id INTEGER PRIMARY KEY, run_id TEXT, meeting_id TEXT, recipient TEXT, kind TEXT, idempotency_key TEXT UNIQUE, payload TEXT, created_at TEXT);
    ''')
def digest(prev,row):
    return hashlib.sha256(dump([prev,row['run_id'],row['entity_id'],row['endpoint'],row['field'],row['value'],row['raw_json'],row['created_at']]).encode()).hexdigest()
def cite(run,entity,endpoint,field,value,raw):
    row=dict(run_id=run,entity_id=str(entity),endpoint=endpoint,field=field,value=dump(value),raw_json=dump(raw),created_at=now())
    with connect() as c:
        c.execute('BEGIN IMMEDIATE'); last=c.execute('SELECT record_hash FROM citations ORDER BY id DESC LIMIT 1').fetchone(); prev=last[0] if last else 'GENESIS'; h=digest(prev,row)
        cur=c.execute('INSERT INTO citations(run_id,entity_id,endpoint,field,value,raw_json,created_at,prev_hash,record_hash) VALUES(?,?,?,?,?,?,?,?,?)',(*row.values(),prev,h))
        return {'id':cur.lastrowid,'field':field,'record_hash':h}
def verify_ledger():
    prev='GENESIS'; rows=query('SELECT * FROM citations ORDER BY id')
    for row in rows:
        if row['prev_hash']!=prev or row['record_hash']!=digest(prev,row): return {'ok':False,'failed_id':row['id'],'head':prev,'count':len(rows)}
        prev=row['record_hash']
    return {'ok':True,'head':prev,'count':len(rows)}
def checkpoint(run,scope,stage,data): execute('INSERT OR REPLACE INTO checkpoints VALUES(?,?,?,?,?)',(run,str(scope),stage,dump(data),now()))
def load(run,scope,stage):
    rows=query('SELECT data FROM checkpoints WHERE run_id=? AND scope=? AND stage=?',(run,str(scope),stage)); return json.loads(rows[0]['data']) if rows else None


def audit(run,event,detail): execute("INSERT INTO audit(run_id,event,detail,created_at) VALUES(?,?,?,?)",(run,event,dump(detail),now()))

def hydrate_flagship_if_missing():
    """Atomically publish a bundled synthetic seed for a fresh container."""
    if os.path.exists(DB): return False
    source=Path(__file__).with_name('flagship_seed.sqlite.gz')
    directory=os.path.dirname(os.path.abspath(DB))
    fd,tmp=tempfile.mkstemp(prefix='.foundry-seed-',dir=directory)
    try:
        with os.fdopen(fd,'wb') as out, gzip.open(source,'rb') as inp: out.write(inp.read())
        try: os.link(tmp,DB); return True
        except FileExistsError: return False
    finally: os.unlink(tmp)
