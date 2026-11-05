import hashlib, json, sqlite3
from datetime import datetime, timezone

DB = 'fde_zero.db'
def now(): return datetime.now(timezone.utc).isoformat()
def dump(v): return json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)
class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc, tb):
        try: return super().__exit__(exc_type, exc, tb)
        finally: self.close()
def connect():
    c = sqlite3.connect(DB, timeout=30, factory=ClosingConnection)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA journal_mode=WAL')
    return c
def init_db():
    with connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, request TEXT, status TEXT, created_at TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS companies(run_id TEXT, company_id INTEGER, name TEXT, domain TEXT, confidence REAL, PRIMARY KEY(run_id,company_id));
        CREATE TABLE IF NOT EXISTS inputs(run_id TEXT, identifier TEXT, company_id INTEGER, PRIMARY KEY(run_id,identifier));
        CREATE TABLE IF NOT EXISTS checkpoints(run_id TEXT, company_id INTEGER, stage TEXT, data TEXT, latency REAL, credits REAL, created_at TEXT, PRIMARY KEY(run_id,company_id,stage));
        CREATE TABLE IF NOT EXISTS cache(endpoint TEXT, payload_hash TEXT, payload TEXT, response TEXT, created_at TEXT, PRIMARY KEY(endpoint,payload_hash));
        CREATE TABLE IF NOT EXISTS citations(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, company_id INTEGER, fact_label TEXT, fact_value TEXT, endpoint TEXT, crustdata_id TEXT, verified_at_utc TEXT, raw_json_excerpt TEXT);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, company_id INTEGER, event TEXT, detail TEXT, created_at TEXT);
        CREATE TABLE IF NOT EXISTS exception_queue(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, company_id INTEGER, reasons TEXT, status TEXT DEFAULT 'pending', created_at TEXT, UNIQUE(run_id,company_id));
        CREATE TABLE IF NOT EXISTS sandbox_outbox(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, company_id INTEGER, idempotency_key TEXT UNIQUE, markdown TEXT, json TEXT, created_at TEXT);
        ''')
        for table in ('citations','audit'):
            cols={r['name'] for r in c.execute(f'PRAGMA table_info({table})')}
            for col in ('prev_hash','record_hash'):
                if col not in cols: c.execute(f'ALTER TABLE {table} ADD COLUMN {col} TEXT')
            prev='GENESIS'
            for row in c.execute(f'SELECT * FROM {table} ORDER BY id').fetchall():
                if row['record_hash'] is None:
                    digest=record_digest(table,prev,dict(row))
                    c.execute(f'UPDATE {table} SET prev_hash=?,record_hash=? WHERE id=?',(prev,digest,row['id']))
                    prev=digest
                else: prev=row['record_hash']
        c.execute('CREATE TABLE IF NOT EXISTS savings(id INTEGER PRIMARY KEY, run_id TEXT, source TEXT, endpoint TEXT, amount REAL, basis TEXT, created_at TEXT)')
        c.execute('CREATE TABLE IF NOT EXISTS watcher_configs(run_id TEXT, company_id INTEGER, kind TEXT, endpoint TEXT, payload TEXT, status TEXT, PRIMARY KEY(run_id,company_id,kind))')
def query(sql, args=()):
    with connect() as c: return [dict(x) for x in c.execute(sql,args).fetchall()]
def execute(sql,args=()):
    with connect() as c: c.execute(sql,args)
def record_digest(table,prev,row):
    if table=='citations': parts=[prev,row['endpoint'],row['crustdata_id'],row['fact_label'],row['fact_value'],row['verified_at_utc'],hashlib.sha256(row['raw_json_excerpt'].encode()).hexdigest()]
    else: parts=[prev,'AUDIT',str(row['company_id']),row['event'],row['detail'],row['created_at'],hashlib.sha256(str(row['run_id']).encode()).hexdigest()]
    return hashlib.sha256('|'.join(str(x) for x in parts).encode()).hexdigest()
def append_chain(table,fields):
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        last=c.execute(f'SELECT record_hash FROM {table} ORDER BY id DESC LIMIT 1').fetchone(); prev=last[0] if last else 'GENESIS'
        fields['prev_hash']=prev; fields['record_hash']=record_digest(table,prev,fields)
        cols=list(fields); row=c.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",tuple(fields[k] for k in cols))
        return row.lastrowid
def audit(run, cid, event, detail): return append_chain('audit',{'run_id':run,'company_id':cid,'event':event,'detail':dump(detail),'created_at':now()})
def cite(run,cid,label,value,endpoint,raw):
    return append_chain('citations',{'run_id':run,'company_id':cid,'fact_label':label,'fact_value':dump(value),'endpoint':endpoint,'crustdata_id':str(cid),'verified_at_utc':now(),'raw_json_excerpt':dump(raw)})
def verify_ledger_integrity(table='citations'):
    if table not in ('citations','audit'): raise ValueError('invalid ledger')
    rows=query(f'SELECT * FROM {table} ORDER BY id'); prev='GENESIS'; intact=0
    for row in rows:
        if row['prev_hash'] != prev or row['record_hash'] != record_digest(table,prev,row): return {'ok':False,'intact':intact,'total':len(rows),'head':prev,'failed_id':row['id']}
        prev=row['record_hash']; intact+=1
    return {'ok':True,'intact':intact,'total':len(rows),'head':prev}
def checkpoint(run,cid,stage,data,latency=0,credits=0): execute('INSERT OR REPLACE INTO checkpoints VALUES(?,?,?,?,?,?,?)',(run,cid,stage,dump(data),latency,credits,now()))
def load(run,cid,stage):
    rows=query('SELECT data FROM checkpoints WHERE run_id=? AND company_id=? AND stage=?',(run,cid,stage))
    return json.loads(rows[0]['data']) if rows else None
def key(run,cid): return hashlib.sha256(f'{run}:{cid}'.encode()).hexdigest()
