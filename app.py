import json
import streamlit as st
import db
from service_engine import pipeline, review, STAGES

st.set_page_config(page_title='FDE-Zero | ContextCon 04',page_icon='◈',layout='wide')
db.init_db()
st.markdown('''<style>
.stApp {background:#090e19;color:#edf3ff} [data-testid="stSidebar"] {background:#101828}
h1,h2,h3 {letter-spacing:-.03em} .hero {border:1px solid #263a59;background:linear-gradient(110deg,#14233b,#0a1425);padding:28px;border-radius:16px;margin-bottom:20px}
.eyebrow {color:#62e8c2;font-size:12px;font-weight:700;letter-spacing:.18em} .muted {color:#a9bbd3}
[data-testid="stMetric"] {background:#111e30;border:1px solid #263a50;padding:12px;border-radius:10px}
</style>''',unsafe_allow_html=True)
with st.sidebar:
    st.markdown('### ◈ FDE-ZERO')
    st.caption('CONTEXTCON 04  /  MISSION CONTROL')
    st.divider()
    st.markdown('#### Live telemetry')
    metrics=[('Canonical companies resolved','SELECT count(*) n FROM companies'),('Stale "Ex-" profiles caught',"SELECT count(*) n FROM audit WHERE event='STALE_PROFILE_CAUGHT'"),('Duplicates merged',"SELECT count(*) n FROM audit WHERE event='DUPLICATE_MERGED'"),('Citation records','SELECT count(*) n FROM citations'),('Cached responses','SELECT count(*) n FROM cache')]
    for label,sql in metrics: st.metric(label,db.query(sql)[0]['n'])
    st.metric('Estimated credits saved',db.query('SELECT round(coalesce(sum(amount),0),2) n FROM savings')[0]['n'])
    st.caption('Cache hits and checkpoints avoid repeat calls. Credits saved are not estimated when the original charge is unknown.')
    st.divider()
    preset=st.selectbox('Service request',['Live FinTech & SaaS Engineering Spike (Razorpay + Freshworks + Duplicate Domain Test)','Trust & Compliance Vendor Audit (Cross-Check Web Claims vs Crustdata Graph)','Vendor Discrepancy Case','Custom Plain-English Service Request'])
    custom=st.text_area('Company domains, names, or industry request',value='razorpay.com, freshworks.com' if preset.startswith('Trust') else '',placeholder='e.g. Indian fintech companies hiring engineers')
    crash=st.checkbox('Simulate Mid-Run Crash (Stage 3)')
    if st.button('▶  Start mission',type='primary',use_container_width=True):
        ids=['razorpay.com','freshworks.com','Razorpay'] if preset.startswith('Live') else (['razorpay.com'] if preset.startswith('Vendor') else ([x.strip() for x in custom.split(',') if x.strip()] if ',' in custom else None))
        try:
            with st.spinner('Researching and checkpointing…'):
                run=pipeline(custom or preset,ids,simulate_crash_after_stage=STAGES[2] if crash else None,claimed_headcount=15000 if preset.startswith('Vendor') else None)
            st.session_state['run']=run; st.success('Mission complete')
        except Exception as ex:
            recent=db.query('SELECT id FROM runs ORDER BY created_at DESC LIMIT 1')
            if recent: st.session_state['run']=recent[0]['id']
            st.error(str(ex))
    interrupted=db.query("SELECT id FROM runs WHERE status='interrupted' ORDER BY created_at DESC LIMIT 1")
    if st.button('↻  Resume Crashed Run (Idempotent Replay)',disabled=not interrupted,use_container_width=True):
        try:
            with st.spinner('Resuming from checkpoints…'): run=pipeline(run_id=interrupted[0]['id'])
            st.session_state['run']=run; st.success('Resume complete')
        except Exception as ex: st.error(str(ex))

st.markdown('<div class="hero"><div class="eyebrow">AUTONOMOUS FORWARD-DEPLOYED INTELLIGENCE</div><h1>Evidence before outreach.</h1><p class="muted">Resolve entities, investigate hiring signals, verify leadership, and deliver an auditable technical brief — all within your local sandbox.</p></div>',unsafe_allow_html=True)
runs=db.query('SELECT * FROM runs ORDER BY created_at DESC LIMIT 50')
if runs:
    ids=[r['id'] for r in runs]; index=ids.index(st.session_state['run']) if st.session_state.get('run') in ids else 0
    run=st.selectbox('Mission run',ids,index=index,format_func=lambda x: x[:8]+'  ·  '+next(r['status'] for r in runs if r['id']==x))
else: run=None
tabs=st.tabs(['◉  Execution pipeline','▣  Deliverables & POCs','⚑  Exception queue','≡  Citation & audit ledger'])
with tabs[0]:
    st.subheader('Autonomous execution pipeline')
    if not run: st.info('Start a mission from the sidebar. Configure CRUSTDATA_API_KEY in .env for live API calls.')
    else:
        for co in db.query('SELECT * FROM companies WHERE run_id=?',(run,)):
            st.markdown(f"#### {co['name']}  ·  `{co['company_id']}`")
            checkpoints=db.query('SELECT * FROM checkpoints WHERE run_id=? AND company_id=?',(run,co['company_id'])); by={x['stage']:x for x in checkpoints}
            cols=st.columns(5)
            for i,s in enumerate(STAGES):
                item=by.get(s); cols[i].metric(f'{i+1:02} / '+['IDENTIFY','HIRING','TRUST','LEADER','DELIVER'][i], '✓ Complete' if item else '○ Pending',f"{item['latency']:.2f}s · {item['credits']:.2f} cr" if item else None)
            for a in db.query('SELECT * FROM audit WHERE run_id=? AND company_id=? ORDER BY id DESC',(run,co['company_id'])): st.caption(f"{a['event']} · {a['detail'][:250]}")
        status=next((r for r in runs if r['id']==run),None)
        if status and status['error']: st.warning(status['error'])
with tabs[1]:
    st.subheader('Sandbox outbox')
    rows=db.query('SELECT * FROM sandbox_outbox'+(' WHERE run_id=?' if run else '')+' ORDER BY id DESC',(run,) if run else ())
    if not rows: st.info('No dispatched deliverables yet. Review pending exceptions to release them locally.')
    for x in rows:
        with st.expander(f"Company {x['company_id']} · receipt {x['idempotency_key'][:16]}",expanded=True):
            left,right=st.columns([2,1]); left.markdown(x['markdown']); right.code(x['idempotency_key']); right.caption('Idempotency receipt · local sandbox only')
            right.download_button('Download Markdown',x['markdown'],file_name=f"fde-{x['company_id']}.md",key='md'+str(x['id']))
            right.download_button('Download JSON',x['json'],file_name=f"fde-{x['company_id']}.json",mime='application/json',key='json'+str(x['id']))
with tabs[2]:
    st.subheader('Human review')
    rows=db.query("SELECT * FROM exception_queue WHERE status='pending'"+(' AND run_id=?' if run else ''),(run,) if run else ())
    if not rows: st.info('No pending exceptions.')
    for x in rows:
        data=db.load(x['run_id'],x['company_id'],STAGES[4]) or {}
        with st.expander(f"Company {x['company_id']} · review required",expanded=True):
            st.json(json.loads(x['reasons'])); st.markdown(data.get('markdown',''))
            a,b=st.columns(2)
            if a.button('Approve & Dispatch',key='approve'+str(x['id'])): review(x['id'],True); st.rerun()
            if b.button('Reject',key='reject'+str(x['id'])): review(x['id'],False); st.rerun()
with tabs[3]:
    st.subheader('Citation & audit ledger')
    st.caption('Hash chains detect changes relative to a trusted head. Store the head externally: a database owner can rewrite the entire chain.')
    if st.button('Verify Cryptographic Ledger Integrity'):
        for table in ('citations','audit'):
            result=db.verify_ledger_integrity(table)
            if result['ok']: st.success(f"{table.upper()} CHAIN VERIFIED: {result['intact']}/{result['total']} intact · Genesis → {result['head']}")
            else: st.error(f"{table.upper()} CHAIN FAILED at row {result['failed_id']}")
    term=st.text_input('Search ledger')
    rows=db.query('SELECT * FROM citations'+(' WHERE run_id=?' if run else '')+' ORDER BY id DESC',(run,) if run else ())
    rows=[r for r in rows if term.lower() in json.dumps(r).lower()]
    st.dataframe([{k:v for k,v in r.items() if k!='raw_json_excerpt'} for r in rows],use_container_width=True,hide_index=True)
    for row in rows:
        with st.expander(f"#{row['id']} · {row['fact_label']} · {row['endpoint']}"):
            st.code(row['raw_json_excerpt'],language='json')
    st.markdown('#### Audit events')
    st.dataframe(db.query('SELECT * FROM audit'+(' WHERE run_id=?' if run else '')+' ORDER BY id DESC',(run,) if run else ()),use_container_width=True,hide_index=True)
