import json
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

SCRIPT = Path(__file__).with_name('generate.py')


class FixtureTests(unittest.TestCase):
    def generate(self, directory, *args):
        return subprocess.run([sys.executable, str(SCRIPT), '--out', str(directory), *args], capture_output=True, text=True)

    def test_relations_ranges_and_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.generate(tmp)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads((Path(tmp) / 'data.json').read_text())
            self.assertEqual([len(data[k]) for k in ('accounts', 'services', 'transactions')], [10, 30, 100])
            accounts = {a['id'] for a in data['accounts']}
            services = {s['id']: s for s in data['services']}
            for a in data['accounts']:
                for key in ('balance', 'withdrawBalance', 'totalReplenished', 'bonusBalance'):
                    self.assertIs(type(a[key]), int)
                    self.assertGreaterEqual(a[key], 0)
            for s in services.values():
                self.assertIn(s['ownerId'], accounts)
                self.assertRegex(s['name'], r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
                self.assertGreater(datetime.fromisoformat(s['expiresAt']), datetime.fromisoformat(s['createdAt']))
            amounts = set()
            for t in data['transactions']:
                self.assertIn(t['serviceId'], services)
                self.assertIs(type(t['amount']), int)
                self.assertTrue(0 <= t['amount'] <= 1000)
                amounts.add(t['amount'])
                self.assertEqual(t['performedAt'] is None, t['status'] == 'not_performed')
                if t['performedAt']:
                    self.assertGreaterEqual(t['performedAt'], t['createdAt'])
                for k in ('createdAt', 'performedAt'):
                    if t[k]:
                        self.assertRegex(t[k], r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$')
            self.assertTrue({0, 1000} <= amounts)
            self.assertEqual({s['paymentTerm'] for s in services.values()}, {'hour', 'week', 'month', 'quarter_year', 'half_year', 'year'})
            self.assertEqual({s['status'] for s in services.values()}, {'active', 'blocked', 'deleted', 'suspended', 'prolong_wait'})

    def test_reproducibility_and_custom_counts(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            for directory in (a, b):
                result = self.generate(directory, '--accounts', '2', '--services', '3', '--transactions', '5', '--seed', '7')
                self.assertEqual(result.returncode, 0, result.stderr)
            for name in ('data.json', '002_seed.sql'):
                self.assertEqual((Path(a) / name).read_bytes(), (Path(b) / name).read_bytes())
            data = json.loads((Path(a) / 'data.json').read_text())
            self.assertEqual([len(v) for v in data.values()], [2, 3, 5])

    def test_invalid_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            for args in (('--accounts', '0'), ('--services', '0'), ('--transactions', '-1')):
                self.assertNotEqual(self.generate(tmp, *args).returncode, 0)
            self.assertFalse((Path(tmp) / 'data.json').exists())


if __name__ == '__main__':
    unittest.main()
