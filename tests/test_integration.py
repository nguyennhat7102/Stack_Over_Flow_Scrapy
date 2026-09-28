import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


class IntegrationTests(unittest.TestCase):
    def command(self,d,target=500,mode='normal',end='2023-01-08'):
        return [sys.executable,'-m','scrapy','crawl','stackoverflow',
            '-s','DOWNLOAD_HANDLERS={"https":"tests.fake_api.Handler"}',
            '-s','DOWNLOAD_DELAY=0','-s','RETRY_BACKOFF_BASE=0.01',
            '-s','PARTITION_START_DATE=2023-01-01','-s',f'PARTITION_END_DATE={end}',
            '-s',f'TARGET_RECORDS={target}','-s',f'OUTPUT_FILE={d}/out.jsonl',
            '-s',f'JOBDIR={d}/job','-s',f'FAKE_TRACE={d}/trace','-s',f'FAKE_MODE={mode}']

    def run_crawl(self,d,**kwargs):
        env=dict(os.environ,STACKOVERFLOW_API_KEY='offline-test-key')
        p=subprocess.run(self.command(d,**kwargs),cwd=ROOT,env=env,capture_output=True,text=True,timeout=30)
        self.assertNotIn('offline-test-key',p.stdout+p.stderr)
        self.assertNotIn('ScrapyDeprecationWarning',p.stderr)
        return p

    def rows(self,d):
        p=Path(d)/'out.jsonl'
        return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []

    def trace(self,d):
        p=Path(d)/'trace'
        return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []

    def test_exact_500_then_increase_and_already_met(self):
        with tempfile.TemporaryDirectory() as d:
            result=self.run_crawl(d)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(len(self.rows(d)),500,result.stderr)
            before=len(self.trace(d))
            result=self.run_crawl(d)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(len(self.trace(d)),before)
            result=self.run_crawl(d,target=550)
            self.assertEqual(result.returncode,0,result.stderr)
            rows=self.rows(d)
            self.assertEqual(len(rows),550,result.stderr)
            self.assertEqual(len({r['question_id'] for r in rows}),550)
            self.assertEqual([r['page'] for r in self.trace(d)[before:]],[0,5,6])
            self.assertIn('total_written=550',result.stderr)
            self.assertIn('written_this_run=50',result.stderr)
            self.assertNotIn(b'offline-test-key',b''.join(p.read_bytes() for p in (Path(d)/'job').rglob('*') if p.is_file()))

    def test_quota_keeps_last_page_and_resumes_next(self):
        with tempfile.TemporaryDirectory() as d:
            result=self.run_crawl(d,mode='quota')
            self.assertEqual(result.returncode,2,result.stderr)
            self.assertEqual(len(self.rows(d)),100,result.stderr)
            result=self.run_crawl(d)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(len(self.rows(d)),500)
            self.assertEqual([r['page'] for r in self.trace(d)],[0,1,0,2,3,4,5])

    def test_retry_exhaustion_preserves_page(self):
        with tempfile.TemporaryDirectory() as d:
            result=self.run_crawl(d,mode='retry')
            self.assertEqual(result.returncode,1,result.stderr)
            attempts=[r for r in self.trace(d) if r['page']==1]
            self.assertEqual(len(attempts),6,result.stderr)
            self.assertGreaterEqual(attempts[-1]['time']-attempts[0]['time'],0.3)
            result=self.run_crawl(d,target=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(len(self.rows(d)),10)

    def test_cap_advances_week(self):
        with tempfile.TemporaryDirectory() as d:
            result=self.run_crawl(d,target=2501,end='2023-01-15')
            self.assertEqual(result.returncode,0,result.stderr)
            rows=self.rows(d)
            self.assertEqual(len(rows),2501,result.stderr)
            self.assertEqual(rows[-1]['question_id'],10001)
            self.assertIn('remaining questions skipped',result.stderr)
            self.assertFalse(any(r['page']>25 for r in self.trace(d)))

    def test_backoff(self):
        with tempfile.TemporaryDirectory() as d:
            result=self.run_crawl(d,target=101,mode='backoff')
            self.assertEqual(result.returncode,0,result.stderr)
            trace=self.trace(d)
            self.assertGreaterEqual(trace[2]['time']-trace[1]['time'],0.19)

    def test_invalid_key_never_requests_questions(self):
        with tempfile.TemporaryDirectory() as d:
            result=self.run_crawl(d,mode='invalid_key')
            self.assertEqual(result.returncode,1,result.stderr)
            self.assertEqual([r['page'] for r in self.trace(d)],[0])

    def test_missing_key_exit(self):
        with tempfile.TemporaryDirectory() as d:
            env=dict(os.environ); env.pop('STACKOVERFLOW_API_KEY',None)
            result=subprocess.run(self.command(d),cwd=ROOT,env=env,capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,1,result.stderr)
            self.assertEqual(self.trace(d),[])

    def test_pause_resume(self):
        with tempfile.TemporaryDirectory() as d:
            env=dict(os.environ,STACKOVERFLOW_API_KEY='offline-test-key')
            with open(Path(d)/'log','w') as log:
                p=subprocess.Popen(self.command(d,mode='slow'),cwd=ROOT,env=env,stdout=log,stderr=log)
                try:
                    deadline=time.monotonic()+15
                    while len(self.trace(d))<3 and p.poll() is None and time.monotonic()<deadline:
                        time.sleep(0.02)
                    self.assertIsNone(p.poll())
                    p.send_signal(signal.SIGINT)
                    p.wait(timeout=10)
                finally:
                    if p.poll() is None: p.kill(); p.wait()
            self.assertGreater(len(self.rows(d)),0)
            result=self.run_crawl(d)
            self.assertEqual(result.returncode,0,result.stderr)
            rows=self.rows(d)
            self.assertEqual(len(rows),500,result.stderr)
            self.assertEqual(len({r['question_id'] for r in rows}),500)

    def test_corrupt_checkpoint_and_queue_fall_back_safely(self):
        with tempfile.TemporaryDirectory() as d:
            result=self.run_crawl(d,target=10)
            self.assertEqual(result.returncode,0,result.stderr)
            (Path(d)/'job'/'collector.json').write_text('broken')
            (Path(d)/'job'/'requests.queue'/'active.json').write_text('broken')
            result=self.run_crawl(d,target=20)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(len(self.rows(d)),20,result.stderr)
            self.assertIn('Untrusted checkpoint',result.stderr)
            self.assertIn('Corrupt JOBDIR',result.stderr)

    def test_output_corruption_is_fatal_before_api(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/'out.jsonl').write_text('{"tags":["a","b"]}\n')
            result=self.run_crawl(d)
            self.assertEqual(result.returncode,1,result.stderr)
            self.assertEqual(self.trace(d),[])

    def test_invalid_dates_are_nonzero(self):
        with tempfile.TemporaryDirectory() as d:
            result=self.run_crawl(d,end='2022-01-01')
            self.assertEqual(result.returncode,1,result.stderr)
            self.assertEqual(self.trace(d),[])

    def test_hard_stop_recovers_inflight_page(self):
        with tempfile.TemporaryDirectory() as d:
            env=dict(os.environ,STACKOVERFLOW_API_KEY='offline-test-key')
            with open(Path(d)/'log','w') as log:
                p=subprocess.Popen(self.command(d,mode='slow'),cwd=ROOT,env=env,stdout=log,stderr=log)
                try:
                    deadline=time.monotonic()+15
                    while len(self.trace(d))<3 and p.poll() is None and time.monotonic()<deadline:
                        time.sleep(0.02)
                    self.assertIsNone(p.poll())
                    p.kill(); p.wait(timeout=10)
                finally:
                    if p.poll() is None: p.kill(); p.wait()
            result=self.run_crawl(d)
            self.assertEqual(result.returncode,0,result.stderr)
            rows=self.rows(d)
            self.assertEqual(len(rows),500,result.stderr)
            self.assertEqual(len({r['question_id'] for r in rows}),500)

    def test_mixed_items_are_classified_and_target_still_exact(self):
        with tempfile.TemporaryDirectory() as d:
            result=self.run_crawl(d,target=100,mode='mixed')
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(len(self.rows(d)),100)
            self.assertIn("'insufficient_tags': 1",result.stderr)
            self.assertIn("'invalid_schema': 2",result.stderr)
            self.assertIn("'duplicate_ids': 1",result.stderr)

    def test_empty_partition_finishes_without_retry(self):
        with tempfile.TemporaryDirectory() as d:
            result=self.run_crawl(d,mode='empty')
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual([r['page'] for r in self.trace(d)],[0,1])
            self.assertEqual(self.rows(d),[])

    def test_partial_final_line_is_repaired_on_resume(self):
        with tempfile.TemporaryDirectory() as d:
            result=self.run_crawl(d,target=10)
            self.assertEqual(result.returncode,0,result.stderr)
            with (Path(d)/'out.jsonl').open('ab') as fh:
                fh.write(b'{"question_id":')
            result=self.run_crawl(d,target=20)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(len(self.rows(d)),20)
