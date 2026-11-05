import hashlib, json, os, time
import httpx
from dotenv import load_dotenv
import db
load_dotenv()
class CrustdataClient:
    def __init__(self,run_id=None,mode='live'):
        self.run_id=run_id; self.mode=mode
        st_token=None
        try:
            import streamlit as st
            if hasattr(st,'secrets'): st_token=st.secrets.get('CRUSTDATA_API_KEY') or st.secrets.get('CRUSTDATA_API_TOKEN')
        except Exception: pass
        self.token=st_token or os.getenv('CRUSTDATA_API_KEY') or os.getenv('CRUSTDATA_API_TOKEN')
    def post(self,endpoint,payload):
        body=db.dump(payload); key=hashlib.sha256(body.encode()).hexdigest(); namespace=endpoint if self.mode=='live' else 'demo:'+endpoint
        rows=db.query('SELECT response FROM cache WHERE endpoint=? AND payload_hash=?',(namespace,key))
        if rows:
            db.execute('INSERT INTO savings(run_id,source,endpoint,amount,created_at) VALUES(?,?,?,?,?)',(self.run_id,'cache',namespace,0,db.now())); return json.loads(rows[0]['response'])
        if self.mode=='demo':
            from demo_data import response
            data=response(endpoint,payload)
        else:
            try:
                if not self.token: raise RuntimeError('API key unavailable')
                r=httpx.post('https://api.crustdata.com'+endpoint,json=payload,headers={'Authorization':'Token '+self.token,'x-api-version':'2025-11-01'},timeout=5)
                r.raise_for_status(); data=r.json()
            except (httpx.HTTPError, ValueError, RuntimeError) as exc:
                db.audit(self.run_id,'RESILIENCE_FALLBACK_TRIGGERED',{'endpoint':endpoint,'reason':str(exc)[:200],'source':'synthetic fixture; not live Crustdata'})
                from demo_data import fallback_response
                return fallback_response(endpoint,payload)
        db.execute('INSERT OR IGNORE INTO cache VALUES(?,?,?,?,?)',(namespace,key,body,db.dump(data),db.now())); return data
