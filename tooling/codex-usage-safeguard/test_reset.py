import copy
import unittest
from reset_once import prepare
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
        with self.assertRaisesRegex(ValueError, 'no_applicable_reset'):
            prepare(ACCOUNT, self.state['episode'], sample(100, resets=0), self.inventory, 'human-reply', '2026-10-01T00:00:00Z')

if __name__ == '__main__':
    unittest.main()
