import json, re, time, uuid
from datetime import datetime, timezone
import db
from crustdata_client import CrustdataClient

STAGES=['STAGE_1_IDENTIFY_DEDUPE','STAGE_2_HIRING_SIGNALS','STAGE_3_ENRICH_AND_TRUST_CHECK','STAGE_4_DECISION_MAKER_VERIFIED','STAGE_5_POC_BUILT_AND_DISPATCHED']
UNKNOWN='[UNVERIFIED: Not in Crustdata graph or live web — Refusing to guess]'
def verify_active_decision_maker(profile,target_company_id,target_company_name):
    basic=profile.get('basic_profile') or {}; text=' '.join(str(basic.get(k) or '') for k in ('headline','current_title'))
    jobs=((profile.get('experience') or {}).get('employment_details') or {}).get('current') or []
    matching=[j for j in jobs if str(j.get('crustdata_company_id'))==str(target_company_id)]
    # Former/ex markers are interpreted in relation to the target. Generic former roles are not evidence of departure.
    marker=bool(re.search(r'(?i)\b(?:ex[-\s]+|former(?:ly)?\s+(?:at\s+)?|previously\s+(?:at\s+)?)'+re.escape(target_company_name)+r'\b',text))
    if marker: return False,'former employer marker for target'
    if not matching: return False,'no current target employment'
    today=datetime.now(timezone.utc).date().isoformat()
    if not any((not j.get('end_date') or str(j['end_date'])[:10]>=today) and (not j.get('start_date') or str(j['start_date'])[:10]<=today) for j in matching): return False,'target employment dates inactive'
    other=[j for j in jobs if j.get('is_default') and str(j.get('crustdata_company_id'))!=str(target_company_id)]
    if other and not any(j.get('is_default') for j in matching): return False,'another employer is the default current role'
    if not re.search(r'(?i)\b(manager|director|head|chief|cto|vp|vice president|lead)\b', ' '.join(str(j.get('title') or '') for j in matching)): return False,'no leadership title in target role'
    return True,'target current role and dates consistent; not independently verified'

def fact(run,cid,label,value,endpoint,raw):
    if value is None or value=='': value=UNKNOWN
    n=db.cite(run,cid,label,value,'POST '+endpoint,raw)
    return {'value':value,'citation_id':n}
def stage(run,cid,name,fn,client,crash=None):
    existing=db.load(run,cid,name)
    if existing is not None:
        db.execute('INSERT INTO savings(run_id,source,endpoint,amount,basis,created_at) VALUES(?,?,?,?,?,?)',(run,'checkpoint',name,0,'No API calls replayed; historical charge not estimated to avoid double counting',db.now()))
        return existing
    start=time.monotonic(); before=len(client.events); data=fn()
    ev=client.events[before:]; db.checkpoint(run,cid,name,data,round(time.monotonic()-start,3),sum(e['credits'] for e in ev))
    if crash==name or crash==STAGES.index(name)+1: raise RuntimeError('Simulated crash after '+name)
    return data
def filters_company(ids): return {'op':'and','conditions':[{'field':'company.basic_info.company_id','type':'in','value':ids},{'op':'or','conditions':[{'field':'job_details.title','type':'(.)','value':'engineer'}]}]}
def filters_person(cid): return {'op':'and','conditions':[{'field':'experience.employment_details.current.company_id','type':'=','value':cid},{'op':'or','conditions':[{'field':'experience.employment_details.current.title','type':'(.)','value':'engineering'}]}]}
def identify(client, run, identifier):
    found=db.query('SELECT company_id FROM inputs WHERE run_id=? AND identifier=?',(run,identifier))
    if found: return
    domain=bool(re.match(r'^[\w-]+(?:\.[\w-]+)+$',identifier.strip()))
    response=client.post('/company/identify',{'domains' if domain else 'names':[identifier]})
    matches=(response[0].get('matches') or []) if response else []
    if not matches: db.audit(run,None,'IDENTIFY_NOT_FOUND',{'input':identifier}); return
    match=max(matches,key=lambda x:x.get('confidence_score') or 0); co=match.get('company_data') or {}; cid=co.get('crustdata_company_id'); basic=co.get('basic_info') or {}
    if not cid: db.audit(run,None,'IDENTIFY_NOT_FOUND',{'input':identifier,'response':response}); return
    existing=db.query('SELECT * FROM companies WHERE run_id=? AND company_id=?',(run,cid))
    if existing: db.audit(run,cid,'DUPLICATE_MERGED',{'identifier':identifier,'canonical_id':cid})
    else:
        db.execute('INSERT INTO companies VALUES(?,?,?,?,?)',(run,cid,basic.get('name') or identifier,basic.get('primary_domain') or '',match.get('confidence_score') or 0))
        facts={'id':fact(run,cid,'Canonical company ID',cid,'/company/identify',match),'name':fact(run,cid,'Company name',basic.get('name'),'/company/identify',match),'domain':fact(run,cid,'Domain',basic.get('primary_domain'),'/company/identify',match),'confidence':fact(run,cid,'Resolution confidence',match.get('confidence_score'),'/company/identify',match)}
        db.checkpoint(run,cid,STAGES[0],facts)
    db.execute('INSERT INTO inputs VALUES(?,?,?)',(run,identifier,cid))

def pipeline(request='', identifiers=None, run_id=None, simulate_crash_after_stage=None, duplicate_input_test=False, claimed_headcount=None):
    db.init_db(); run=run_id or str(uuid.uuid4()); client=CrustdataClient(run)
    if not run_id: db.execute('INSERT INTO runs VALUES(?,?,?,?,?)',(run,request,'running',db.now(),None))
    else: db.execute('UPDATE runs SET status=?,error=NULL WHERE id=?',('running',run))
    try:
        if identifiers is None:
            identifiers=re.findall(r'\b(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}\b',request)
            if not identifiers and request.strip():
                # Natural-language company discovery, not an LLM extraction claim.
                result=client.post('/company/search',{'search':{'query':request,'mode':'hybrid'},'limit':5})
                identifiers=[(c.get('basic_info') or {}).get('primary_domain') for c in result.get('companies',[]) if (c.get('basic_info') or {}).get('primary_domain')]
        if duplicate_input_test and identifiers and 'Razorpay' not in identifiers: identifiers=list(identifiers)+['Razorpay']
        for identifier in identifiers or []: identify(client,run,identifier.strip())
        companies=db.query('SELECT * FROM companies WHERE run_id=?',(run,))
        if not companies: raise RuntimeError('No companies resolved. Provide company names or domains, or refine the search.')
        for co in companies:
            cid=co['company_id']; name=co['name']; domain=co['domain']
            def jobs():
                data=client.post('/job/search',{'filters':filters_company([cid]),'limit':10,'sorts':[{'field':'metadata.date_added','order':'desc'}],'fields':['job_details','company.basic_info','location','metadata']})
                return {'jobs':[fact(run,cid,'Indexed engineering job',{'title':(x.get('job_details') or {}).get('title'),'url':(x.get('job_details') or {}).get('url'),'location':(x.get('location') or {}).get('raw'),'date_added':(x.get('metadata') or {}).get('date_added')},'/job/search',x) for x in data.get('job_listings',[])], 'total':fact(run,cid,'Indexed matching postings',data.get('total_count'),'/job/search',{'total_count':data.get('total_count')})}
            j=stage(run,cid,STAGES[1],jobs,client,simulate_crash_after_stage)
            def enrich():
                data=client.post('/company/enrich',{'crustdata_company_ids':[cid],'fields':['crustdata_company_id','basic_info','headcount.total','funding']})
                matches=(data[0].get('matches') or []) if data else []; raw=(matches[0].get('company_data') or {}) if matches else {}; h=(raw.get('headcount') or {}).get('total'); f=raw.get('funding') or {}
                web=client.post('/web/search/live',{'query':f'{name} company employee headcount total funding raised'})
                sources=[{'title':x.get('title'),'url':x.get('url'),'snippet':x.get('snippet')} for x in web.get('results',[])[:5]]
                claim=fact(run,cid,'Web search results for trust review',sources or None,'/web/search/live',sources)
                conflicts=[]; claims=[]
                for source in sources:
                    snippet=source.get('snippet') or ''
                    # Restrict automated comparisons to explicit company-attributed employee counts.
                    match=re.search(r'(?i)\b'+re.escape(name)+r'\s+(?:has|employs|has over)\s+([\d,]+)\+?\s+employees\b',snippet)
                    if match:
                        value=int(match.group(1).replace(',','')); claims.append({'value':value,'source':source})
                        if h and (value>h*5 or h>value*5): conflicts.append({'metric':'headcount','graph':h,'claim':value,'source':source,'note':'Large mismatch; dates and scope may differ'})
                if claimed_headcount is not None and h and (claimed_headcount>h*3 or h>claimed_headcount*3): conflicts.append({'metric':'headcount','graph':h,'claim':claimed_headcount,'source':'user-supplied test claim','note':'Unverified user claim; review required'})
                for conflict in conflicts: db.audit(run,cid,'DISCREPANCY_FLAGGED',conflict)
                return {'headcount':fact(run,cid,'Headcount',h,'/company/enrich',{'headcount':raw.get('headcount')}),'funding':fact(run,cid,'Total investment USD',f.get('total_investment_usd'),'/company/enrich',{'funding':f}),'last_round_date':fact(run,cid,'Last fundraise date',f.get('last_fundraise_date'),'/company/enrich',{'funding':f}),'last_round_type':fact(run,cid,'Last round type',f.get('last_round_type'),'/company/enrich',{'funding':f}),'web':claim,'comparison':'No material contradiction detected by conservative extraction; absence of conflict is not independent confirmation.', 'claims':claims,'conflicts':conflicts}
            e=stage(run,cid,STAGES[2],enrich,client,simulate_crash_after_stage)
            def people():
                data=client.post('/person/search',{'filters':filters_person(cid),'limit':10,'fields':['basic_profile','social_handles','experience','education']})
                stale=0; picked=None
                for p in data.get('profiles',[]):
                    ok,reason=verify_active_decision_maker(p,cid,name)
                    if not ok:
                        if 'former' in reason or 'another employer' in reason or 'inactive' in reason:
                            stale+=1; db.audit(run,cid,'STALE_PROFILE_CAUGHT',{'name':(p.get('basic_profile') or {}).get('name'),'reason':reason,'profile':p})
                        continue
                    if picked is None: picked=p
                b=(picked or {}).get('basic_profile') or {}; social=((picked or {}).get('social_handles') or {}).get('professional_network_identifier') or {}; schools=(((picked or {}).get('education') or {}).get('schools') or [])
                return {'leader':fact(run,cid,'Decision maker',{'name':b.get('name'),'title':b.get('current_title'),'headline':b.get('headline'),'url':social.get('profile_url')} if picked else None,'/person/search',picked or {}),'iit_kanpur':fact(run,cid,'IIT Kanpur education',any('kanpur' in str(s.get('school','')).lower() and ('iit' in str(s.get('school','')).lower() or 'indian institute of technology' in str(s.get('school','')).lower()) for s in schools) if schools else None,'/person/search',schools),'stale_count':stale}
            p=stage(run,cid,STAGES[3],people,client,simulate_crash_after_stage)
            def deliver():
                facts={'company':db.load(run,cid,STAGES[0]),'hiring':j,'enrichment':e,'decision_maker':p}; reasons=[]
                if co['confidence']<0.8: reasons.append('Identification confidence below 0.8')
                if e['conflicts']: reasons+=e['conflicts']
                # Unconfirmed comparisons and missing leaders require review before dispatch.
                if p['leader']['value']==UNKNOWN: reasons.append('No active leader could be verified')
                doc=build_report(run,co,facts)
                if reasons: db.execute('INSERT OR IGNORE INTO exception_queue(run_id,company_id,reasons,created_at) VALUES(?,?,?,?)',(run,cid,db.dump(reasons),db.now()))
                else:
                    register_watch_configs(run,cid,p)
                    dispatch(run,cid,doc,facts)
                return {'markdown':doc,'facts':facts,'status':'exception' if reasons else 'dispatched','reasons':reasons}
            stage(run,cid,STAGES[4],deliver,client,simulate_crash_after_stage)
        # Rebuild after all peers finish so each report has the full local benchmark.
        for co in companies:
            result=db.load(run,co['company_id'],STAGES[4])
            if result:
                doc=build_report(run,co,result['facts']); result['markdown']=doc
                db.execute('UPDATE checkpoints SET data=? WHERE run_id=? AND company_id=? AND stage=?',(db.dump(result),run,co['company_id'],STAGES[4]))
                db.execute('UPDATE sandbox_outbox SET markdown=? WHERE run_id=? AND company_id=?',(doc,run,co['company_id']))
        db.execute('UPDATE runs SET status=? WHERE id=?',('completed',run))
    except Exception as ex:
        db.execute('UPDATE runs SET status=?,error=? WHERE id=?',('interrupted',str(ex),run)); raise
    return run

def watcher_payloads(cid,person):
    url=person.get('leader',{}).get('value',{}); url=url.get('url') if isinstance(url,dict) else None
    config={'trigger':{'type':'interval','every_hours':24},'refresh_frequency_days':30}
    return {'person':('/watch/person',{'preview':True,'entities':{'professional_network_profile_urls':[url]},'track':{'field':'basic_profile.current_title','type':'changed'},'config':config,'notifications':[]}), 'jobs':('/watch/job/search',{'preview':True,'filters':filters_company([cid]),'on':['added'],'config':{'trigger':{'type':'interval','every_hours':24}},'notifications':[]})}
def register_watch_configs(run,cid,person):
    for kind,(endpoint,payload) in watcher_payloads(cid,person).items():
        db.execute('INSERT OR REPLACE INTO watcher_configs VALUES(?,?,?,?,?,?)',(run,cid,kind,endpoint,db.dump(payload),'draft_preview_required'))
def build_report(run,co,facts):
    def show(x): return f"{x['value']} [citation #{x['citation_id']}]"
    e=facts['enrichment']; p=facts['decision_maker']; j=facts['hiring']; i=facts['company']
    jobs='\n'.join('- '+show(x) for x in j['jobs']) or '- '+UNKNOWN
    snippet='''import os, requests\nBASE = "https://api.crustdata.com"\nheaders = {"Authorization": "Token " + os.environ["CRUSTDATA_API_KEY"], "x-api-version": "2025-11-01"}\npayload = {"filters": {"op": "and", "conditions": [{"field": "company.basic_info.company_id", "type": "in", "value": [%s]}, {"field": "job_details.title", "type": "(.)", "value": "engineer"}]}, "sorts": [{"field": "metadata.date_added", "order": "desc"}], "limit": 10, "fields": ["job_details", "company.basic_info", "location", "metadata"]}\nr = requests.post(BASE + "/job/search", json=payload, headers=headers, timeout=30)\nr.raise_for_status()\nfor job in r.json().get("job_listings", []):\n    print(job.get("company", {}).get("basic_info", {}).get("crustdata_company_id"), job.get("job_details", {}).get("title"), job.get("metadata", {}).get("date_added"))''' % co['company_id']
    benchmark=[]
    for peer in db.query('SELECT * FROM companies WHERE run_id=?',(run,)):
        pc=peer['company_id']; pe=db.load(run,pc,STAGES[2]); pj=db.load(run,pc,STAGES[1]); pp=db.load(run,pc,STAGES[3])
        if not (pe and pj and pp): continue
        latest=show(pj['jobs'][0]) if pj['jobs'] else UNKNOWN
        benchmark.append(f"| {peer['name']} | {show(pe['headcount'])} | {show(pe['funding'])} | {show(pe['last_round_date'])} | {show(pj['total'])} | {latest} | {show(pp['leader'])} |")
    table='\n'.join(benchmark)
    configs=watcher_payloads(co['company_id'],p)
    previews='\n'.join(f"### {kind}: `POST {endpoint}`\n```json\n{json.dumps(payload,indent=2)}\n```" for kind,(endpoint,payload) in configs.items())
    return f"# {co['name']} — FDE-Zero dossier\n\nCanonical ID: {show(i['id'])}\n\nDomain: {show(i['domain'])}\n\nHeadcount: {show(e['headcount'])}\n\nHistorical total investment: {show(e['funding'])}; last recorded raise: {show(e['last_round_date'])}, {show(e['last_round_type'])}. Funding is a snapshot and may be stale.\n\n## Indexed hiring signals\n{jobs}\n\nIndexed postings are not independently confirmed open. Counts are not hiring velocity; no comparable time-series baseline was verified. {show(j['total'])}\n\n## Decision maker\n{show(p['leader'])}\n\nProfile consistency is a deterministic screen, not proof of current employment. Stale profiles flagged in this run: {p['stale_count']}.\n\n## Trust review\n{e['comparison']} {show(e['web'])}\n\n## Local peer benchmark\nPeers are other resolved companies in this run, not verified competitors. Job counts are indexed, not confirmed active.\n\n| Company | Headcount | Funding USD | Last round | Indexed jobs | Latest indexed role | Leader |\n|---|---|---|---|---|---|---|\n{table}\n\n## Tailored API proof of concept\nInstall `requests`, set `CRUSTDATA_API_KEY`, then run:\n```python\n{snippet}\n```\n\n## Watcher previews\nDrafts stored locally only; they are not active watches. Preview first; Crustdata requires a preview token and later approval before creation, which incurs recurring costs. Empty notifications are pull-only. Webhook signing format is unverified; do not accept signed alerts until Crustdata's signature scheme is confirmed.\n{previews}\n\nDispatch key: `{db.key(run,co['company_id'])}`\n"
def resume_run(run_id): return pipeline(run_id=run_id)
def dispatch(run,cid,markdown,facts):
    with db.connect() as c:
        cur=c.execute('INSERT OR IGNORE INTO sandbox_outbox(run_id,company_id,idempotency_key,markdown,json,created_at) VALUES(?,?,?,?,?,?)',(run,cid,db.key(run,cid),markdown,db.dump(facts),db.now()))
        inserted=cur.rowcount
    if not inserted: db.audit(run,cid,'IDEMPOTENT_SKIP',{'idempotency_key':db.key(run,cid)})
def review(exception_id,approve):
    rows=db.query('SELECT * FROM exception_queue WHERE id=? AND status=?',(exception_id,'pending'))
    if not rows: return
    row=rows[0]; data=db.load(row['run_id'],row['company_id'],STAGES[4])
    if approve:
        register_watch_configs(row['run_id'],row['company_id'],data['facts']['decision_maker'])
        dispatch(row['run_id'],row['company_id'],data['markdown'],data['facts'])
    db.execute('UPDATE exception_queue SET status=? WHERE id=?',('approved' if approve else 'rejected',exception_id)); db.audit(row['run_id'],row['company_id'],'HUMAN_APPROVED' if approve else 'HUMAN_REJECTED',{'exception_id':exception_id})
