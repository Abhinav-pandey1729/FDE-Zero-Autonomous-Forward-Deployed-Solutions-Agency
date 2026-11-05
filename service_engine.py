import hashlib, json, re, uuid
from datetime import datetime, timezone
import db
from crustdata_client import CrustdataClient
from scheduler_service import book_meeting
STAGES=['IDENTIFY','MARKET','PEOPLE','MATCH_AND_ROUTE']
def ident(x): return hashlib.sha256(str(x).encode()).hexdigest()[:20]
def field(obj,path):
 for part in path.split('.'):
  if not isinstance(obj,dict): return None
  obj=obj.get(part)
 return obj
def checkpointed(run,scope,stage,fn,crash):
 data=db.load(run,scope,stage)
 if data is not None:
  db.execute('INSERT INTO savings(run_id,source,endpoint,amount,created_at) VALUES(?,?,?,?,?)',(run,'checkpoint',stage,0,db.now())); return data
 data=fn(); db.checkpoint(run,scope,stage,data)
 if crash==stage: raise RuntimeError('Simulated crash after '+stage)
 return data
def cite_fields(run,pid,endpoint,raw,paths): return {p:db.cite(run,pid,endpoint,p,field(raw,p),raw) for p in paths}
def person_id(p):
 return str(p.get('person_id') or p.get('crustdata_person_id') or field(p,'social_handles.professional_network_identifier.profile_url') or ident(db.dump(p)))
def role(p): return str(field(p,'basic_profile.current_title') or '')
def city(p):
 value=field(p,'basic_profile.location') or field(p,'basic_profile.city') or ''
 if isinstance(value,dict): value=value.get('city') or value.get('name') or ''
 value=str(value).lower(); return 'Bengaluru' if 'bangalore' in value or 'bengaluru' in value else 'NCR' if any(x in value for x in ('delhi','gurgaon','gurugram','noida','ncr')) else value.strip().title() or 'Unknown'
def school(p): return {str(x.get('school','')).lower().strip() for x in (field(p,'education.schools') or []) if isinstance(x,dict) and x.get('school')}
def vertical(p):
 text=(str(p.get('vertical') or '')+' '+db.dump(p)).lower()
 if any(x in text for x in ('fintech','razorpay','payments')): return 'Fintech'
 if any(x in text for x in ('saas','freshworks')): return 'B2B SaaS'
 if re.search(r'\b(ai|machine learning)\b',text): return 'AI'
 return 'General'
def compute_cofounder_match_score(a,b,company):
 pa=json.loads(a['profile']); pb=json.loads(b['profile']); ca=json.loads(a['citations']); cb=json.loads(b['citations']); co=json.loads(company['data']); cc=co['citations']
 def refs(*items): return [x for x in items if x]
 same=bool(school(pa)&school(pb)); samecity=city(pa)!='Unknown' and city(pa)==city(pb); shared=vertical(pa)==vertical(pb) and vertical(pa)!='General'; ra=role(pa).lower(); rb=role(pb).lower(); complementary=bool(re.search('engineer|technical|security|machine learning|cto',ra) and re.search('product|gtm|sales|business|growth|operator',rb));
 funding=co.get('funding_date'); old=False
 try: old=(datetime.now(timezone.utc).date()-datetime.fromisoformat(funding[:10]).date()).days>730
 except (TypeError,ValueError): pass
 transition=bool(re.search(r'(?i)\b(ex[-\s]|former|building|stealth)',str(field(pb,'basic_profile.headline') or '')))
 senior=lambda r: bool(re.search(r'(?i)\b(vp|chief|cto|head|director|lead|staff|senior|manager)\b',r))
 parts={'Pedigree':{'score':25 if same else 0,'reason':'Shared school' if same else 'No shared school established','citations':refs(ca.get('education.schools'),cb.get('education.schools'))},'City':{'score':25 if samecity else 0,'reason':f"{city(pa)} / {city(pb)}",'citations':refs(ca.get('basic_profile.location'),cb.get('basic_profile.location'))},'Domain':{'score':(12 if shared else 0)+(13 if complementary else 0),'reason':f"{vertical(pa)} / {vertical(pb)}; {'complementary roles' if complementary else 'complementarity not established'}",'citations':refs(ca.get('basic_profile.current_title'),cb.get('basic_profile.current_title'),ca.get('basic_profile.headline'),cb.get('basic_profile.headline'),ca.get('experience.employment_details.current'),cb.get('experience.employment_details.current'),ca.get('vertical'),cb.get('vertical'))},'Career':{'score':(10 if senior(ra) and senior(rb) else 0)+(8 if old else 0)+(7 if transition else 0),'reason':f'Seniority alignment: {senior(ra) and senior(rb)}; funding gap >24 months: {old}; transition signal: {transition}','citations':refs(ca.get('basic_profile.current_title'),cb.get('basic_profile.current_title'),cc.get('funding.last_fundraise_date'),cb.get('basic_profile.headline'))}}
 return {'score':sum(x['score'] for x in parts.values()),'breakdown':parts}
def pipeline(identifiers=None,mode='demo',run_id=None,simulate_crash_after_stage=None):
 db.init_db(); run=run_id or str(uuid.uuid4())
 if run_id:
  rows=db.query('SELECT * FROM runs WHERE id=?',(run,))
  if not rows: raise ValueError('Unknown run')
  identifiers=json.loads(rows[0]['identifiers']); mode=rows[0]['mode']; db.execute("UPDATE runs SET status='running',error=NULL WHERE id=?",(run,))
 else: db.execute('INSERT INTO runs VALUES(?,?,?,?,?,NULL)',(run,db.dump(identifiers or []),mode,'running',db.now()))
 client=CrustdataClient(run,mode)
 try:
  def identify():
   ids=[]
   for item in identifiers or []:
    item=item.strip(); payload={'domains' if '.' in item else 'names':[item]}; result=client.post('/company/identify',payload); matches=(result[0].get('matches') or []) if isinstance(result,list) and result else []; match=max(matches,key=lambda x:x.get('confidence_score') or 0) if matches else {}; co=match.get('company_data') or {}; cid=str(co.get('crustdata_company_id') or '')
    if not cid: continue
    basic=co.get('basic_info') or {}; db.execute('INSERT OR IGNORE INTO companies VALUES(?,?,?,?,?)',(run,cid,basic.get('name') or item,basic.get('primary_domain') or '',db.dump({}))); db.execute('INSERT OR IGNORE INTO aliases VALUES(?,?,?)',(run,item.lower(),cid)); ids.append(cid)
    db.cite(run,cid,'POST /company/identify','crustdata_company_id',cid,match)
   if not ids: raise RuntimeError('No companies resolved; enter valid names or domains.')
   return list(dict.fromkeys(ids))
  ids=checkpointed(run,'run','IDENTIFY',identify,simulate_crash_after_stage)
  for cid in ids:
   def market():
    data=client.post('/company/enrich',{'crustdata_company_ids':[int(cid)],'fields':['crustdata_company_id','basic_info','funding']}); matches=(data[0].get('matches') or []) if isinstance(data,list) and data else []; raw=(matches[0].get('company_data') or {}) if matches else {}
    refs=cite_fields(run,cid,'POST /company/enrich',raw,['funding.last_fundraise_date']); payload={'filters':{'op':'and','conditions':[{'field':'company.basic_info.company_id','type':'in','value':[int(cid)]},{'field':'job_details.title','type':'(.)','value':'engineer'}]},'limit':20,'fields':['job_details','company.basic_info','location','metadata']}
    if mode=='demo': payload['demo_company_id']=cid
    jobs=client.post('/job/search',payload).get('job_listings',[]); jobrefs=[{'title':field(j,'job_details.title'),'citation':db.cite(run,cid,'POST /job/search','job_details.title',field(j,'job_details.title'),j)} for j in jobs]
    value={'funding_date':field(raw,'funding.last_fundraise_date'),'citations':refs,'jobs':jobrefs}; db.execute('UPDATE companies SET data=? WHERE run_id=? AND company_id=?',(db.dump(value),run,cid)); return value
   checkpointed(run,cid,'MARKET',market,simulate_crash_after_stage)
   def people():
    payload={'filters':{'op':'and','conditions':[{'field':'experience.employment_details.current.company_id','type':'=','value':int(cid)}]},'limit':50,'fields':['basic_profile','experience','education','social_handles']}
    if mode=='demo': payload['demo_company_id']=cid
    result=client.post('/person/search',payload); profiles=result.get('profiles',[]) if isinstance(result,dict) else []
    for p in profiles:
     pid=person_id(p); b=p.get('basic_profile') or {}; headline=str(b.get('headline') or ''); title=role(p); current=field(p,'experience.employment_details.current') or []; matching=any(str(j.get('crustdata_company_id') or j.get('company_id'))==cid for j in current if isinstance(j,dict)); transition=bool(re.search(r'(?i)\b(ex[-\s]|former|building|stealth)',headline)); technical=bool(re.search(r'(?i)engineer|technical|security|machine learning|cto',title)); senior=bool(re.search(r'(?i)senior|staff|lead|head|director|vp|manager|chief|cto',title)); pool='A' if technical and senior and matching and not transition else 'B' if transition or (matching and senior) else ''
     if not pool: continue
     paths=['basic_profile.name','basic_profile.current_title','basic_profile.headline','basic_profile.location','experience.employment_details.current','education.schools','vertical']; refs=cite_fields(run,pid,'POST /person/search',p,paths)
     db.execute('INSERT OR REPLACE INTO candidates VALUES(?,?,?,?,?,?,?)',(run,pid,cid,b.get('name') or 'Missing identity',pool,db.dump(p),db.dump(refs)))
     reasons=[]
     if not b.get('name') or not (p.get('person_id') or p.get('crustdata_person_id') or field(p,'social_handles.professional_network_identifier.profile_url')): reasons.append('Missing critical identity fields')
     if not matching and not transition: reasons.append('Target employment claim is unverified')
     if reasons: db.execute('INSERT OR IGNORE INTO exception_queue(run_id,person_id,reasons,created_at) VALUES(?,?,?,?)',(run,pid,db.dump(reasons),db.now()))
    return {'count':len(profiles)}
   checkpointed(run,cid,'PEOPLE',people,simulate_crash_after_stage)
  def route():
   candidates=db.query('SELECT * FROM candidates WHERE run_id=?',(run,)); a=[x for x in candidates if x['pool']=='A']; b=[x for x in candidates if x['pool']=='B']; held={x['person_id'] for x in db.query("SELECT person_id FROM exception_queue WHERE run_id=? AND status!='approved'",(run,))}; groups={}
   for x in a:
    co=db.query('SELECT * FROM companies WHERE run_id=? AND company_id=?',(run,x['company_id']))[0]
    for y in b:
     if x['person_id']==y['person_id']: continue
     score=compute_cofounder_match_score(x,y,co); mid=ident(run+':'+x['person_id']+':'+y['person_id']); high=score['score']>=50; jobs=json.loads(co['data']).get('jobs',[]); evidence='; '.join(f"{j['title']} [citation #{j['citation']['id']} · {j['citation']['record_hash']}]" for j in jobs) or 'No indexed job gap available.'; thesis=f"# Day-0 thesis: {vertical(json.loads(x['profile']))} infrastructure\n\n{x['name']} and {y['name']} can explore a product addressing operational bottlenecks signaled by indexed hiring: {evidence}\n\nValidate customer pain, interview operators, prototype a narrow workflow, and test willingness to pay. Indexed jobs are signals, not proof of unmet demand."
     safe='Conditional draft: $250,000 post-money SAFE investment, subject to chemistry meeting, diligence, investment committee approval, mutually agreed valuation cap and definitive legal documents. No funding commitment.'
     db.execute('INSERT OR REPLACE INTO matches VALUES(?,?,?,?,?,?,?,?,?)',(mid,run,x['person_id'],y['person_id'],score['score'],db.dump(score['breakdown']),'ONE_ON_ONE' if high else 'WORK_PARTY',thesis if high else '',safe if high else ''))
     if x['person_id'] in held or y['person_id'] in held: continue
     if high: book_meeting(run,'one_on_one',mid,'Founder chemistry: '+x['name']+' + '+y['name'],[x['person_id'],y['person_id']],'Introductions; evidence review; founder chemistry; next steps.')
     else:
      for p in (x,y):
       profile=json.loads(p['profile']); group=(city(profile),vertical(profile)); groups.setdefault(group,set()).add(p['person_id'])
   for (location,theme),attendees in groups.items():
    cohort=ident(run+location+theme); stations=[]
    for co in db.query('SELECT * FROM companies WHERE run_id=?',(run,)):
     for j in json.loads(co['data']).get('jobs',[]): stations.append({'company':co['name'],'problem':j['title'],'citation':j['citation']})
    plan={'venue':f'{location} — venue to be confirmed','theme':theme,'stations':stations,'icebreakers':['What problem would you work on for five years?','What skill do you need in a co-founder?'],'rsvp':'Sandbox invitation; venue and attendance require confirmation.'}; people=sorted(attendees); meeting=book_meeting(run,'work_party',cohort,'Foundry-Zero Weekend Work-Party & Co-Founder Mixer',people,db.dump(plan)); db.execute('INSERT OR REPLACE INTO work_party_cohorts VALUES(?,?,?,?,?,?,?)',(cohort,run,location,theme,db.dump(people),db.dump(plan),meeting))
   return {'pairs':len(a)*len(b)}
  checkpointed(run,'run','MATCH_AND_ROUTE',route,simulate_crash_after_stage); db.execute("UPDATE runs SET status='completed',error=NULL WHERE id=?",(run,)); return run
 except Exception as e:
  db.execute("UPDATE runs SET status='interrupted',error=? WHERE id=?",(str(e),run)); raise
def review(exception_id,approve):
 rows=db.query('SELECT * FROM exception_queue WHERE id=?',(exception_id,))
 if not rows: return
 row=rows[0]; db.execute('UPDATE exception_queue SET status=? WHERE id=?',('approved' if approve else 'rejected',exception_id)); db.execute("DELETE FROM checkpoints WHERE run_id=? AND stage='MATCH_AND_ROUTE'",(row['run_id'],)); pipeline(run_id=row['run_id'])

