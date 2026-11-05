import unittest
import db
from service_engine import pipeline, resume_run, dispatch, STAGES

class ProductionFlow(unittest.TestCase):
    def test_crash_resume_and_idempotency(self):
        with self.assertRaisesRegex(RuntimeError,'Simulated crash'):
            pipeline('Recovery test',['razorpay.com'],simulate_crash_after_stage=3)
        run=db.query("SELECT id FROM runs WHERE request='Recovery test' ORDER BY created_at DESC LIMIT 1")[0]['id']
        stages=db.query('SELECT stage,credits FROM checkpoints WHERE run_id=? ORDER BY stage',(run,))
        self.assertEqual({x['stage'] for x in stages},set(STAGES[:3]))
        before={x['stage']:x['credits'] for x in stages}
        resume_run(run)
        self.assertEqual({x['stage'] for x in db.query('SELECT stage FROM checkpoints WHERE run_id=?',(run,))},set(STAGES))
        after={x['stage']:x['credits'] for x in db.query('SELECT stage,credits FROM checkpoints WHERE run_id=?',(run,)) if x['stage'] in STAGES[:3]}
        self.assertEqual(before,after)
        self.assertEqual(len(db.query("SELECT * FROM savings WHERE run_id=? AND source='checkpoint'",(run,))),2)
        result=db.load(run,897127,STAGES[4]); self.assertEqual(result['status'],'dispatched')
        dispatch(run,897127,result['markdown'],result['facts'])
        self.assertEqual(db.query('SELECT count(*) n FROM sandbox_outbox WHERE idempotency_key=?',(db.key(run,897127),))[0]['n'],1)
        self.assertTrue(db.query("SELECT * FROM audit WHERE run_id=? AND event='IDEMPOTENT_SKIP'",(run,)))
    def test_discrepancy_and_ledger(self):
        run=pipeline('Explicit hypothetical discrepancy test',['razorpay.com'],claimed_headcount=15000)
        self.assertTrue(db.query("SELECT * FROM audit WHERE run_id=? AND event='DISCREPANCY_FLAGGED'",(run,)))
        self.assertTrue(db.query('SELECT * FROM exception_queue WHERE run_id=?',(run,)))
        self.assertTrue(db.verify_ledger_integrity()['ok']); self.assertTrue(db.verify_ledger_integrity('audit')['ok'])

if __name__=='__main__': unittest.main()
