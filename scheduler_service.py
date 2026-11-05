import hashlib, json, re, uuid
from datetime import datetime, timedelta, timezone
import db
IST=timezone(timedelta(hours=5,minutes=30))

def parse_time(value):
 d=datetime.fromisoformat(value.replace('Z','+00:00')) if isinstance(value,str) else value
 if d.tzinfo is None: raise ValueError('A timezone-aware meeting time is required')
 return d.astimezone(timezone.utc)
def stamp(d): return d.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
def ics(mid,title,start,end,version,agenda):
 def esc(s): return str(s).replace('\\','\\\\').replace(';','\\;').replace(',','\\,').replace('\n','\\n')
 return '\r\n'.join(['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//Foundry-Zero//Sandbox//EN','METHOD:REQUEST','BEGIN:VEVENT','UID:'+mid+'@foundry-zero.local','SEQUENCE:'+str(version),'DTSTAMP:'+stamp(datetime.now(timezone.utc)),'DTSTART:'+stamp(start),'DTEND:'+stamp(end),'SUMMARY:'+esc(title),'DESCRIPTION:'+esc(agenda),'END:VEVENT','END:VCALENDAR',''])
def notify(c,meeting,recipient,kind,payload):
 key=hashlib.sha256(f"{meeting['id']}:{meeting['version']}:{recipient}:{kind}".encode()).hexdigest()
 c.execute('INSERT OR IGNORE INTO notifications_outbox(run_id,meeting_id,recipient,kind,idempotency_key,payload,created_at) VALUES(?,?,?,?,?,?,?)',(meeting['run_id'],meeting['id'],recipient,kind,key,db.dump(payload),db.now()))
def available(c,participants,start,end,exclude=None):
 for row in c.execute("SELECT * FROM meetings WHERE status IN ('BOOKED','RESCHEDULED','RESCHEDULED_CONFIRMED')"):
  if row['id']==exclude: continue
  if set(participants)&set(json.loads(row['participants'])) and start<parse_time(row['ends_at']) and end>parse_time(row['starts_at']): return False
 return True
def next_slot(c,participants,start,duration,exclude=None,weekend=False):
 start=parse_time(start).astimezone(IST).replace(second=0,microsecond=0)
 earliest=(datetime.now(IST)+timedelta(days=1)).replace(hour=10,minute=0,second=0,microsecond=0)
 start=max(start,earliest)
 for _ in range(48*60):
  opening=start.replace(hour=10,minute=0,second=0,microsecond=0); closing=start.replace(hour=19,minute=0,second=0,microsecond=0)
  if start<opening: start=opening
  if (weekend and start.weekday()!=5) or start+duration>closing:
   start=(start+timedelta(days=1)).replace(hour=10,minute=0,second=0,microsecond=0); continue
  end=start+duration
  if available(c,participants,start,end,exclude): return start.astimezone(timezone.utc),end.astimezone(timezone.utc)
  start+=timedelta(minutes=30)
 raise RuntimeError('No available slot in the next 60 days')
def book_meeting(run,kind,subject,title,participants,agenda,proposed=None):
 if not participants: raise ValueError('Meeting needs participants')
 with db.connect() as c:
  c.execute('BEGIN IMMEDIATE'); old=c.execute('SELECT * FROM meetings WHERE run_id=? AND kind=? AND subject_id=?',(run,kind,subject)).fetchone()
  if old:
   old=dict(old); existing=json.loads(old['participants']); additions=[p for p in participants if p not in existing]
   if additions:
    if not available(c,additions,parse_time(old['starts_at']),parse_time(old['ends_at']),old['id']): raise ValueError('Added cohort member has a scheduling conflict; reschedule the cohort first')
    c.execute('UPDATE meetings SET participants=? WHERE id=?',(db.dump(existing+additions),old['id']))
    for p in additions: notify(c,old,p,'INVITATION',{'title':old['title'],'starts_at':old['starts_at'],'ics':old['ics'],'sandbox':True})
   return old['id']
  default_start=datetime.now(timezone.utc)+timedelta(days=7)
  if kind=='work_party' and proposed is None: default_start=(default_start+timedelta(days=(5-default_start.weekday())%7)).replace(hour=11,minute=0,second=0,microsecond=0)
  start,end=next_slot(c,participants,proposed or default_start,timedelta(hours=6 if kind=='work_party' else 1),weekend=kind=='work_party'); mid=str(uuid.uuid4()); cal=ics(mid,title,start,end,0,agenda)
  c.execute('INSERT INTO meetings VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(mid,run,kind,subject,title,start.isoformat(),end.isoformat(),db.dump(participants),agenda,'BOOKED',0,cal))
  m={'id':mid,'run_id':run,'version':0}
  for p in participants: notify(c,m,p,'INVITATION',{'title':title,'starts_at':start.isoformat(),'ics':cal,'sandbox':True})
  return mid
def reschedule_meeting(meeting_id,proposed,request_id=None):
 with db.connect() as c:
  c.execute('BEGIN IMMEDIATE'); row=c.execute('SELECT * FROM meetings WHERE id=?',(meeting_id,)).fetchone()
  if not row: raise ValueError('Meeting not found')
  m=dict(row); request_id=request_id or str(uuid.uuid4()); marker='reschedule:'+meeting_id+':'+request_id
  prior=c.execute('SELECT 1 FROM notifications_outbox WHERE idempotency_key=?',(marker,)).fetchone()
  if prior: return m
  start,end=next_slot(c,json.loads(m['participants']),proposed,parse_time(m['ends_at'])-parse_time(m['starts_at']),meeting_id); version=m['version']+1; cal=ics(meeting_id,m['title'],start,end,version,m['agenda'])
  c.execute("UPDATE meetings SET starts_at=?,ends_at=?,status='RESCHEDULED_CONFIRMED',version=?,ics=? WHERE id=?",(start.isoformat(),end.isoformat(),version,cal,meeting_id))
  m.update(starts_at=start.isoformat(),ends_at=end.isoformat(),version=version,status='RESCHEDULED_CONFIRMED',ics=cal)
  c.execute('INSERT INTO notifications_outbox(run_id,meeting_id,recipient,kind,idempotency_key,payload,created_at) VALUES(?,?,?,?,?,?,?)',(m['run_id'],meeting_id,'system','RESCHEDULE_RECEIPT',marker,db.dump({'request_id':request_id}),db.now()))
  for p in json.loads(m['participants']): notify(c,m,p,'RESCHEDULED',{'title':m['title'],'starts_at':m['starts_at'],'ics':cal,'sandbox':True})
  return m
def send_reminders(at=None,force=False,run_id=None):
 at=parse_time(at or datetime.now(timezone.utc)); count=0
 with db.connect() as c:
  c.execute('BEGIN IMMEDIATE')
  for row in c.execute("SELECT * FROM meetings WHERE status IN ('BOOKED','RESCHEDULED','RESCHEDULED_CONFIRMED')").fetchall():
   m=dict(row)
   if run_id is not None and m['run_id']!=run_id: continue
   start=parse_time(m['starts_at']); end=parse_time(m['ends_at']); delta=(start-at).total_seconds()
   kinds=[]
   if force or 3600<delta<=86400: kinds.append('T-24H')
   if force or 0<delta<=3600: kinds.append('T-1H')
   if end<=at: kinds.append('POST-MEETING')
   for kind in kinds:
    for p in json.loads(m['participants']):
     before=c.total_changes; notify(c,m,p,kind,{'message':'Did you connect? Complete your chemistry meeting to discuss the conditional SAFE.' if kind=='POST-MEETING' else 'Your Foundry-Zero session is approaching.','starts_at':m['starts_at'],'sandbox':True}); count+=c.total_changes-before
 return count


def dispatch_cohort_invites(meeting_id):
 with db.connect() as c:
  c.execute('BEGIN IMMEDIATE'); row=c.execute("SELECT * FROM meetings WHERE id=? AND kind='work_party'",(meeting_id,)).fetchone()
  if not row: raise ValueError('Work-party not found')
  m=dict(row); count=0
  for p in json.loads(m['participants']):
   before=c.total_changes; notify(c,m,p,'COHORT_RSVP',{'message':'Sandbox RSVP pass','rsvp':'sandbox://rsvp/'+meeting_id+'/'+p,'ics':m['ics']}); count+=c.total_changes-before
  return count

def parse_request(text,now=None):
 base=(now or datetime.now(IST)).astimezone(IST)
 days={'monday':0,'tuesday':1,'wednesday':2,'thursday':3,'friday':4,'saturday':5,'sunday':6}
 day=next((v for k,v in days.items() if re.search(r'\b'+k+r'\b',text,re.I)),None)
 match=re.search(r'\b(1[0-2]|[1-9])(?::([0-5][0-9]))?\s*(am|pm)\b',text,re.I)
 if day is None or not match: raise ValueError('Use a weekday and time, e.g. Sunday 4:00 PM IST.')
 hour=int(match.group(1))%12+(12 if match.group(3).lower()=='pm' else 0)
 target=(base+timedelta(days=(day-base.weekday())%7 or 7)).replace(hour=hour,minute=int(match.group(2) or 0),second=0,microsecond=0)
 return target

