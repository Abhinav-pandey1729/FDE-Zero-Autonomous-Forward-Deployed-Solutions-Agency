import hashlib, json, os, time
from datetime import datetime, timezone
import httpx
from dotenv import load_dotenv
import db

load_dotenv()
class CrustdataError(Exception): pass
class CrustdataClient:
    def __init__(self, run_id=None):
        self.token=os.getenv('CRUSTDATA_API_KEY') or os.getenv('CRUSTDATA_API_TOKEN') or os.getenv('CRUSTDATA_TOKEN')
        self.events=[]; self.run_id=run_id
    def post(self, endpoint, payload):
        body=db.dump(payload); digest=hashlib.sha256(body.encode()).hexdigest()
        cached=db.query('SELECT response FROM cache WHERE endpoint=? AND payload_hash=?',(endpoint,digest))
        if cached:
            data=json.loads(cached[0]['response']); count=len(data) if isinstance(data,list) else len(data.get('profiles',data.get('job_listings',data.get('companies',[]))))
            rates={'/company/identify':0,'/company/enrich':2,'/person/search':0.33,'/job/search':0.03,'/company/search':0.03,'/web/search/live':1}
            estimate=rates.get(endpoint,0)*(count if endpoint not in ('/web/search/live',) else 1)
            db.execute('INSERT INTO savings(run_id,source,endpoint,amount,basis,created_at) VALUES(?,?,?,?,?,?)',(self.run_id,'cache',endpoint,estimate,'estimate based on returned rows and requested groups',db.now()))
            self.events.append({'endpoint':endpoint,'cached':True,'credits':0,'latency':0})
            return data
        if not self.token: raise CrustdataError('Set CRUSTDATA_API_KEY in .env to run live. No synthetic data is used.')
        headers={'Authorization':f'Token {self.token}','x-api-version':'2025-11-01','Content-Type':'application/json'}
        start=time.monotonic()
        for attempt in range(4):
            try: r=httpx.post('https://api.crustdata.com'+endpoint,json=payload,headers=headers,timeout=45)
            except httpx.HTTPError as e:
                if attempt==3: raise CrustdataError(str(e)) from e
                time.sleep(2**attempt); continue
            if r.status_code==429 and attempt<3:
                raw=r.headers.get('x-ratelimit-reset','')
                try:
                    n=float(raw); delay=max(0,n-time.time()) if n>1000000000 else max(0,n)
                except ValueError:
                    try: delay=max(0,(datetime.fromisoformat(raw.replace('Z','+00:00'))-datetime.now(timezone.utc)).total_seconds())
                    except ValueError: delay=2**attempt
                time.sleep(min(delay+0.1,60)); continue
            if r.status_code>=400: raise CrustdataError(f'{endpoint}: HTTP {r.status_code}: {r.text[:700]}')
            try: data=r.json()
            except ValueError as e: raise CrustdataError(f'{endpoint}: invalid JSON') from e
            db.execute('INSERT OR REPLACE INTO cache VALUES(?,?,?,?,?)',(endpoint,digest,body,db.dump(data),db.now()))
            self.events.append({'endpoint':endpoint,'cached':False,'credits':float(r.headers.get('x-credits-used',0) or 0),'latency':round(time.monotonic()-start,3),'credits_header_present':'x-credits-used' in r.headers})
            return data
        raise CrustdataError('Retry limit exceeded')
