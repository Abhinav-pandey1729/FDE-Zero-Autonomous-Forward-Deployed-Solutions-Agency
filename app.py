import json
from datetime import datetime,timedelta,timezone
import streamlit as st
import db
from service_engine import pipeline,review,STAGES
from scheduler_service import reschedule_meeting,send_reminders
st.set_page_config(page_title='Foundry-Zero',page_icon='◈',layout='wide')
db.init_db()
st.markdown('''<style>.stApp{background:#0c111b;color:#eaf0f9}[data-testid="stSidebar"]{background:#121b29}.hero{padding:1.5rem 2rem;border:1px solid #314458;border-radius:14px;background:linear-gradient(110deg,#182e39,#101b2b)}.eyebrow{color:#75e6bb;letter-spacing:.15em;font-size:.75rem;font-weight:700}[data-testid="stMetric"]{background:#152131;border:1px solid #283b4e;padding:12px;border-radius:10px}</style>''',unsafe_allow_html=True)
with st.sidebar:
 st.markdown('## ◈ FOUNDRY-ZERO'); st.caption('AUTONOMOUS VENTURE STUDIO')
 mode=st.selectbox('Data source',['Demo dataset','Live Crustdata'])
 identifiers=st.text_input('Companies or domains',value='razorpay.com, freshworks.com, Razorpay')
 crash=st.checkbox('Simulate crash after market research')
 if st.button('Run venture discovery',type='primary',width="stretch"):
  try:
   with st.spinner('Researching and building founder pathways…'):
    run=pipeline([x.strip() for x in identifiers.split(',') if x.strip()],mode='demo' if mode.startswith('Demo') else 'live',simulate_crash_after_stage='MARKET' if crash else None)
   st.session_state['run']=run; st.success('Run complete')
  except Exception as e:
   latest=db.query('SELECT id FROM runs ORDER BY created_at DESC LIMIT 1')
   if latest: st.session_state['run']=latest[0]['id']
   st.error(str(e))
 interrupted=db.query("SELECT id FROM runs WHERE status='interrupted' ORDER BY created_at DESC LIMIT 1")
 if st.button('Resume interrupted run',disabled=not interrupted,width="stretch"):
  try: st.session_state['run']=pipeline(run_id=interrupted[0]['id']); st.rerun()
  except Exception as e: st.error(str(e))
st.markdown('<div class="hero"><div class="eyebrow">PRE-DAY-0 VENTURE STUDIO</div><h1>Find the people. Find the possibility.</h1><p>Evidence-led founder matching, startup theses, and curated work-parties — orchestrated in a local sandbox.</p></div>',unsafe_allow_html=True)
runs=db.query('SELECT * FROM runs ORDER BY created_at DESC')
if not runs: st.info('Start with the demo dataset in the sidebar, or connect Crustdata for live research.'); st.stop()
ids=[x['id'] for x in runs]; run=st.selectbox('Run',ids,index=ids.index(st.session_state['run']) if st.session_state.get('run') in ids else 0,format_func=lambda x:x[:8]+' · '+next(r['mode']+' · '+r['status'] for r in runs if r['id']==x)); current=next(x for x in runs if x['id']==run)
if current['mode']=='demo': st.warning('Demo mode: all company, person and job records are synthetic examples, not live Crustdata evidence.')
if current['error']: st.error(current['error'])
def count(sql): return db.query(sql,(run,))[0]['n']
metrics=[('Founders evaluated',count('SELECT count(*) n FROM candidates WHERE run_id=?')),('≥50% pairs',count("SELECT count(*) n FROM matches WHERE run_id=? AND pathway='ONE_ON_ONE'")),('<50% cohort pool',count("SELECT count(DISTINCT value) n FROM work_party_cohorts,json_each(attendees) WHERE run_id=?")),('Meetings booked / rescheduled',count('SELECT count(*) n FROM meetings WHERE run_id=?')),('Verified citations',db.verify_ledger()['count'] if db.verify_ledger()['ok'] else 0),('Cache / checkpoint reuse',count('SELECT count(*) n FROM savings WHERE run_id=?'))]
for col,(label,value) in zip(st.columns(6),metrics): col.metric(label,value)
st.caption('Reuse counts represent avoided calls or stage replays; unknown credit prices are not estimated. Citation chains require an externally saved head to detect complete database rewrites.')
tabs=st.tabs(['Execution & matching','1-on-1 thesis & SAFE','Work-party mixer','Scheduler & notifications','Exceptions & ledger'])
people={x['person_id']:x for x in db.query('SELECT * FROM candidates WHERE run_id=?',(run,))}; matches=db.query('SELECT * FROM matches WHERE run_id=? ORDER BY score DESC',(run,))
with tabs[0]:
 st.subheader('Live execution DAG')
 for co in db.query('SELECT * FROM companies WHERE run_id=?',(run,)):
  st.write(f"**{co['name']}** · canonical ID {co['company_id']}")
  st.caption(' → '.join(f"{'✓' if db.load(run,scope,stage) is not None else '○'} {stage}" for scope,stage in [('run','IDENTIFY'),(co['company_id'],'MARKET'),(co['company_id'],'PEOPLE'),('run','MATCH_AND_ROUTE')]))
 rows=[]
 for m in matches:
  parts=json.loads(m['breakdown']); rows.append({'Founder A':people[m['founder_a']]['name'],'Founder B':people[m['founder_b']]['name'],**{k:v['score'] for k,v in parts.items()},'Total %':m['score'],'Routing':'PATHWAY 1: ≥50% 1-ON-1 THESIS' if m['pathway']=='ONE_ON_ONE' else 'PATHWAY 2: <50% WORK-PARTY MIXER'})
 st.dataframe(rows,hide_index=True,width="stretch")
 for m in matches:
  with st.expander(f"Evidence · {people[m['founder_a']]['name']} + {people[m['founder_b']]['name']} · {m['score']}%"): st.json(json.loads(m['breakdown']))
with tabs[1]:
 st.subheader('1-on-1 startup thesis & conditional SAFE')
 high=[m for m in matches if m['pathway']=='ONE_ON_ONE']
 if not high: st.info('No qualifying pairs yet.')
 for m in high:
  with st.container(border=True):
   st.markdown(f"### {people[m['founder_a']]['name']} × {people[m['founder_b']]['name']} · {m['score']}%")
   st.markdown(m['thesis']); st.markdown('**Conditional $250,000 post-money SAFE**'); st.info(m['safe']); st.caption('Draft for discussion; meeting completion alone does not create a funding commitment.')
with tabs[2]:
 st.subheader('Weekend work-party & co-founder mixer')
 cohorts=db.query('SELECT * FROM work_party_cohorts WHERE run_id=?',(run,))
 if not cohorts: st.info('No eligible cohorts yet; held profiles are excluded from invitations.')
 for co in cohorts:
  plan=json.loads(co['plan']); st.markdown(f"### {co['city']} · {co['theme']}"); st.write(plan['venue']); st.write('Attendees: '+', '.join(people[p]['name'] for p in json.loads(co['attendees']) if p in people)); st.dataframe(plan['stations'],width="stretch"); st.write('Icebreakers: '+ ' / '.join(plan['icebreakers'])); st.caption(plan['rsvp'])
with tabs[3]:
 st.subheader('Meeting scheduler & sandbox notifications')
 for m in db.query('SELECT * FROM meetings WHERE run_id=? ORDER BY starts_at',(run,)):
  with st.container(border=True):
   st.markdown(f"**{m['title']}** · {m['status']} · version {m['version']}"); st.write(f"{m['starts_at']} → {m['ends_at']} · "+', '.join(people[p]['name'] if p in people else p for p in json.loads(m['participants'])))
   a,b=st.columns(2); a.download_button('Download calendar invite',m['ics'],file_name=m['id']+'.ics',mime='text/calendar',key='ics'+m['id'])
   if b.button('Simulate founder reschedule request',key='res'+m['id']):
    start=datetime.fromisoformat(m['starts_at']); days=(6-start.weekday())%7 or 7; proposed=(start+timedelta(days=days)).replace(hour=11,minute=0,second=0,microsecond=0); reschedule_meeting(m['id'],proposed); st.rerun()
 if st.button('Trigger T-24h & T-1h automated reminders'): st.success(f"{send_reminders(force=True)} new sandbox notifications queued.")
 st.dataframe(db.query('SELECT created_at,meeting_id,recipient,kind,idempotency_key,payload FROM notifications_outbox WHERE run_id=? ORDER BY id DESC',(run,)),width="stretch",hide_index=True)
with tabs[4]:
 st.subheader('Human exception queue')
 for e in db.query("SELECT * FROM exception_queue WHERE run_id=? AND status='pending'",(run,)):
  st.warning(f"{people.get(e['person_id'],{}).get('name',e['person_id'])}: {e['reasons']}"); a,b=st.columns(2)
  if a.button('Partner approve',key='a'+str(e['id'])): review(e['id'],True); st.rerun()
  if b.button('Reject',key='b'+str(e['id'])): review(e['id'],False); st.rerun()
 if st.button('Verify cryptographic ledger integrity'):
  result=db.verify_ledger(); st.success(f"Verified {result['count']} records · head {result['head']}") if result['ok'] else st.error(f"Ledger failed at record {result['failed_id']}")
 st.dataframe(db.query('SELECT id,entity_id,endpoint,field,value,record_hash FROM citations WHERE run_id=? ORDER BY id DESC',(run,)),width="stretch",hide_index=True)

