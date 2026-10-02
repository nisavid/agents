import copy
import unittest
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from reset_once import prepare, execute
from safeguard import advance
from test_guard import ACCOUNT, sample


class ResetTests(unittest.TestCase):
    def setUp(self):
        self.state, _ = advance({}, sample(100), ACCOUNT, now=10)
        self.inventory = {'credentialEmail': ACCOUNT['email'], 'credentialAccountId': ACCOUNT['accountId'],
                          'data': {'credits': [{'id': 'earlier', 'status': 'available',
                          'reset_type': 'codex_rate_limits', 'is_supported_by_plan': True,
                          'expires_at': '2026-10-22T00:00:00Z'},
                          {'id': 'later', 'status': 'available', 'reset_type': 'codex_rate_limits',
                           'is_supported_by_plan': True, 'expires_at': '2026-10-29T00:00:00Z'}]}}

    def test_specific_human_confirmation_and_zero_are_required(self):
        episode = self.state['episode']
        with self.assertRaisesRegex(ValueError, 'specific_confirmation_required'):
            prepare(ACCOUNT, episode, sample(100), self.inventory, None, '2026-10-01T00:00:00Z')
        with self.assertRaisesRegex(ValueError, 'quota_not_zero'):
            prepare(ACCOUNT, episode, sample(99.99), self.inventory, 'human-reply', '2026-10-01T00:00:00Z')
        request = prepare(ACCOUNT, episode, sample(100), self.inventory, 'human-reply', '2026-10-01T00:00:00Z')
        self.assertEqual(request['creditId'], 'earlier')
        self.assertEqual(request['idempotencyKey'], episode['resetIdempotencyKey'])

    def test_foreign_inventory_or_inapplicable_reset_is_rejected(self):
        inventory = copy.deepcopy(self.inventory); inventory['credentialAccountId'] = 'other'
        with self.assertRaisesRegex(ValueError, 'inventory_account_mismatch'):
            prepare(ACCOUNT, self.state['episode'], sample(100), inventory, 'human-reply', '2026-10-01T00:00:00Z')

    def test_uncertain_reset_requires_reconciliation_without_new_credit_or_replay(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); episode = self.state['episode']
            (root / 'daybreak.json').write_text(json.dumps(self.state))
            request = prepare(ACCOUNT, episode, sample(100), self.inventory, 'actual-human-reply', '2026-10-01T00:00:00Z')
            path = root / ('reset-' + episode['id'] + '.json')
            path.write_text(json.dumps({'request': request, 'status': 'outcome_unknown'}))
            config = {'accounts': [{**ACCOUNT, 'name': 'daybreak'}]}
            with patch('reset_once.read_account') as read, patch('reset_once.subprocess.run') as consume:
                for _ in range(2):
                    with self.assertRaisesRegex(ValueError, 'reset_reconciliation_required'):
                        execute(config, root, 'daybreak', episode['id'], 'actual-human-reply')
                read.assert_not_called(); consume.assert_not_called()
            record = json.loads(path.read_text())
            self.assertEqual(record['request'], request)
            self.assertEqual(record['status'], 'reconciliation_required')
            self.assertEqual(record['priorAttemptStatus'], 'outcome_unknown')
        with self.assertRaisesRegex(ValueError, 'no_applicable_reset'):
            prepare(ACCOUNT, self.state['episode'], sample(100, resets=0), self.inventory, 'human-reply', '2026-10-01T00:00:00Z')

if __name__ == '__main__':
    unittest.main()
