import tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import db
from service_engine import pipeline,parse_request,STAGES,UNKNOWN

class DynamicTests(unittest.TestCase):
    def test_parser(self):
        self.assertEqual(parse_request('Find hiring signals for Groww, Postman, and Zerodha')['identifiers'],['Groww','Postman','Zerodha'])
        self.assertEqual(parse_request('groww.in and Groww')['identifiers'],['groww.in','Groww'])
        self.assertEqual(parse_request('Find 3 Indian B2B SaaS companies hiring ML engineers')['identifiers'],[])
    def test_empty_data(self):
        old=db.DB
        with tempfile.TemporaryDirectory() as directory:
            db.DB=str(Path(directory)/'test.db')
            def post(client,endpoint,payload):
                if endpoint=='/company/identify': return [{'matches':[{'confidence_score':1,'company_data':{'crustdata_company_id':1,'basic_info':{'name':'Empty Co','primary_domain':'empty.example'}}}]}]
                if endpoint=='/job/search': return {'job_listings':None,'total_count':0}
                if endpoint=='/company/enrich': return [{'matches':[{'company_data':{'funding':None,'headcount':None}}]}]
                if endpoint=='/web/search/live': return {'results':None}
                if endpoint=='/person/search': return {'profiles':None}
                raise AssertionError(endpoint)
            try:
                with patch('service_engine.CrustdataClient.post',post): run=pipeline('Empty example',['empty.example'])
                result=db.load(run,1,STAGES[4]); self.assertEqual(result['facts']['enrichment']['funding']['value'],UNKNOWN)
                self.assertIn(UNKNOWN,result['markdown']); self.assertEqual(result['status'],'exception')
                self.assertEqual(len(db.query('SELECT * FROM checkpoints WHERE run_id=?',(run,))),5)
            finally: db.DB=old

if __name__=='__main__': unittest.main()
