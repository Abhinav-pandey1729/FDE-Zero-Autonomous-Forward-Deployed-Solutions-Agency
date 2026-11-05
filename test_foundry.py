import json, tempfile, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import db
from service_engine import pipeline
from scheduler_service import reschedule_meeting, send_reminders
class FoundryFlow(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.old=db.DB; db.DB=str(Path(self.tmp.name)/'test.db'); db.init_db()
 def tearDown(self): db.DB=self.old; self.tmp.cleanup()
 def test_routing_recovery_and_scheduler(self):
  with self.assertRaisesRegex(RuntimeError,'Simulated crash'): pipeline(['razorpay.com','freshworks.com','Razorpay'],simulate_crash_after_stage='MARKET')
  run=db.query('SELECT id FROM runs')[0]['id']; before=db.load(run,'897127','MARKET'); pipeline(run_id=run)
  self.assertEqual(before,db.load(run,'897127','MARKET')); self.assertEqual(db.query('SELECT count(*) n FROM companies')[0]['n'],2)
  matches=db.query('SELECT * FROM matches'); self.assertTrue(any(m['score']>=50 for m in matches)); self.assertTrue(any(m['score']<50 for m in matches)); self.assertTrue(db.query("SELECT * FROM exception_queue WHERE status='pending'")); self.assertTrue(db.query('SELECT * FROM work_party_cohorts'))
  for m in matches:
   for part in json.loads(m['breakdown']).values():
    self.assertTrue(part['citations']); self.assertTrue(all(db.query('SELECT 1 FROM citations WHERE id=? AND record_hash=?',(c['id'],c['record_hash'])) for c in part['citations']))
  meeting=db.query("SELECT * FROM meetings WHERE kind='one_on_one'")[0]; self.assertEqual(len(db.query("SELECT * FROM notifications_outbox WHERE meeting_id=? AND kind='INVITATION'",(meeting['id'],))),2)
  proposed=datetime.now(timezone.utc)+timedelta(days=12); updated=reschedule_meeting(meeting['id'],proposed,'request-1'); self.assertEqual(updated['version'],1); self.assertEqual(updated['status'],'RESCHEDULED_CONFIRMED'); reschedule_meeting(meeting['id'],proposed,'request-1'); self.assertEqual(db.query('SELECT version FROM meetings WHERE id=?',(meeting['id'],))[0]['version'],1); self.assertEqual(len(db.query("SELECT * FROM notifications_outbox WHERE meeting_id=? AND kind='RESCHEDULED'",(meeting['id'],))),2)
  self.assertGreater(send_reminders(force=True),0); self.assertEqual(send_reminders(force=True),0); self.assertTrue(db.verify_ledger()['ok']); db.execute("UPDATE citations SET value='tampered' WHERE id=1"); self.assertFalse(db.verify_ledger()['ok'])
if __name__=='__main__': unittest.main()
