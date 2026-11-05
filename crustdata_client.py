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
        self.token=os.getenv('CRUSTDATA_API_KEY') or os.getenv('CRUSTDATA_API_TOKEN') or st_token
    def post(self,endpoint,payload):
        body=db.dump(payload); key=hashlib.sha256(body.encode()).hexdigest(); namespace=endpoint if self.mode=='live' else 'demo:'+endpoint
        rows=db.query('SELECT response FROM cache WHERE endpoint=? AND payload_hash=?',(namespace,key))
        if rows:
            db.execute('INSERT INTO savings(run_id,source,endpoint,amount,created_at) VALUES(?,?,?,?,?)',(self.run_id,'cache',namespace,0,db.now())); return json.loads(rows[0]['response'])
        if self.mode=='demo':
            from demo_data import response
            data=response(endpoint,payload)
        else:
            if not self.token: raise RuntimeError('Set CRUSTDATA_API_KEY in .env for live requests, or select the explicitly labeled demo dataset.')
            for attempt in range(4):
                try:
                    r=httpx.post('https://api.crustdata.com'+endpoint,json=payload,headers={'Authorization':'Token '+self.token,'x-api-version':'2025-11-01'},timeout=45)
                    if r.status_code==429 and attempt<3: time.sleep(min(2**attempt,8)); continue
                    r.raise_for_status(); data=r.json(); break
                except httpx.TransportError:
                    if attempt==3: raise
                    time.sleep(2**attempt)
        db.execute('INSERT OR IGNORE INTO cache VALUES(?,?,?,?,?)',(namespace,key,body,db.dump(data),db.now())); return data
