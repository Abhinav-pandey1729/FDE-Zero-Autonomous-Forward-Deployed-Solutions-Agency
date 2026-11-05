import os, tempfile, unittest
from pathlib import Path
from streamlit.testing.v1 import AppTest
import db
class UIFlow(unittest.TestCase):
 def test_controls_and_tabs(self):
  with tempfile.TemporaryDirectory() as tmp:
   old=db.DB; db.DB=str(Path(tmp)/'ui.db')
   try:
    app=AppTest.from_file('app.py',default_timeout=30).run(); self.assertEqual(len(app.tabs),5); self.assertFalse(app.exception)
    for label in ['Verify SHA-256 citation chain','Run default flagship batch','Simulate stage-3 crash & resume','Dispatch cohort work-party invites & RSVP links','Dispatch cohort work-party invites & RSVP links','Simulate founder reschedule request','Send T-24h / T-1h reminders','Send T-24h / T-1h reminders','Verify cryptographic ledger integrity','Partner approve','Run venture discovery']:
     found=[x for x in app.button if x.label==label]; self.assertTrue(found,label); found[0].click().run(); self.assertFalse(app.exception,label)
    request=[x for x in app.text_input if x.label.startswith('Reschedule request')][0]; request.set_value('Sunday 4:00 PM IST').run(); [x for x in app.button if x.label=='Apply reschedule request'][0].click().run(); self.assertFalse(app.exception)
   finally: db.DB=old
