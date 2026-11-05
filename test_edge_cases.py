import os, tempfile, time, unittest
from unittest.mock import patch
from datetime import datetime, timedelta
from pathlib import Path
import db
from service_engine import pipeline
from scheduler_service import IST,parse_request,reschedule_meeting
from crustdata_client import CrustdataClient
class EdgeCases(unittest.TestCase):
 def setUp(self): self.tmp=tempfile.TemporaryDirectory(); self.old=db.DB; db.DB=str(Path(self.tmp.name)/'test.db')
 def tearDown(self): db.DB=self.old; self.tmp.cleanup()
 def test_snapshot(self):
  t=time.perf_counter(); self.assertTrue(db.hydrate_flagship_if_missing()); elapsed=time.perf_counter()-t
  self.assertTrue(db.query("SELECT id FROM runs WHERE status='completed'")); self.assertTrue(db.verify_ledger()['ok']); print(f'  snapshot hydration {elapsed*1000:.1f} ms')
 def test_unseen_and_offline(self):
  db.init_db()
  with patch.object(CrustdataClient,'__init__',lambda self,run_id=None,mode='live': (setattr(self,'run_id',run_id),setattr(self,'mode',mode),setattr(self,'token',None)) and None):
   run=pipeline(['match cofounders from physicswallah and zerodha'],mode='live')
  self.assertTrue(db.query("SELECT * FROM audit WHERE run_id=? AND event='RESILIENCE_FALLBACK_TRIGGERED'",(run,)))
  self.assertTrue(db.query("SELECT * FROM matches WHERE run_id=? AND score>=50",(run,))); self.assertTrue(db.query("SELECT * FROM matches WHERE run_id=? AND score<50",(run,)))
  self.assertTrue(db.query('SELECT * FROM work_party_cohorts WHERE run_id=?',(run,)))
 def test_ist(self):
  db.hydrate_flagship_if_missing(); row=db.query("SELECT * FROM meetings WHERE kind='one_on_one' LIMIT 1")[0]
  result=reschedule_meeting(row['id'],parse_request('Sunday 4:00 PM IST'),'test'); start=datetime.fromisoformat(result['starts_at']).astimezone(IST); end=datetime.fromisoformat(result['ends_at']).astimezone(IST)
  self.assertGreaterEqual(start.hour,10); self.assertLessEqual(end.hour,19); self.assertEqual(result['status'],'RESCHEDULED_CONFIRMED')
