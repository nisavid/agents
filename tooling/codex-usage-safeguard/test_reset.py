import copy
import unittest
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from reset_once import prepare, execute
from activation import reset_operation_lock
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

    def test_reset_does_not_read_or_consume_during_episode_transition(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); episode = self.state['episode']
            (root / 'daybreak.json').write_text(json.dumps(self.state))
            (root / 'daybreak.json').chmod(0o600)
            config = {'accounts': [{**ACCOUNT, 'name': 'daybreak'}]}
            with reset_operation_lock(root), patch('reset_once.read_account') as read, \
                 patch('reset_once.subprocess.run') as consume:
                with self.assertRaises(BlockingIOError):
                    execute(config, root, 'daybreak', episode['id'], 'actual-human-reply')
                read.assert_not_called(); consume.assert_not_called()

    def test_old_confirmation_cannot_consume_after_episode_changes(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); old_id = self.state['episode']['id']
            current = copy.deepcopy(self.state)
            current['episode']['id'] = '00000000-0000-0000-0000-000000000001'
            for event in current['episode']['events']:
                event['id'] = current['episode']['id'] + ':' + event['kind']
            (root / 'daybreak.json').write_text(json.dumps(current))
            (root / 'daybreak.json').chmod(0o600)
            config = {'accounts': [{**ACCOUNT, 'name': 'daybreak'}]}
            with patch('reset_once.read_account') as read, patch('reset_once.subprocess.run') as consume:
                with self.assertRaisesRegex(ValueError, 'episode_mismatch'):
                    execute(config, root, 'daybreak', old_id, 'actual-human-reply')
                read.assert_not_called(); consume.assert_not_called()

    def test_foreign_inventory_or_inapplicable_reset_is_rejected(self):
        inventory = copy.deepcopy(self.inventory); inventory['credentialAccountId'] = 'other'
        with self.assertRaisesRegex(ValueError, 'inventory_account_mismatch'):
            prepare(ACCOUNT, self.state['episode'], sample(100), inventory, 'human-reply', '2026-10-01T00:00:00Z')

    def test_uncertain_reset_requires_reconciliation_without_new_credit_or_replay(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); episode = self.state['episode']
            (root / 'daybreak.json').write_text(json.dumps(self.state))
            (root / 'daybreak.json').chmod(0o600)
            request = prepare(ACCOUNT, episode, sample(100), self.inventory, 'actual-human-reply', '2026-10-01T00:00:00Z')
            path = root / ('reset-' + episode['id'] + '.json')
            path.write_text(json.dumps({'request': request, 'status': 'outcome_unknown'}))
            path.chmod(0o600)
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

    def test_failed_final_response_remains_uncertain_without_replay(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); episode = self.state['episode']
            (root / 'daybreak.json').write_text(json.dumps(self.state))
            (root / 'daybreak.json').chmod(0o600)
            request = prepare(ACCOUNT, episode, sample(100), self.inventory, 'actual-human-reply', '2026-10-01T00:00:00Z')
            path = root / ('reset-' + episode['id'] + '.json')
            config = {'accounts': [{**ACCOUNT, 'name': 'daybreak'}], 'nodeBinary': 'node', '_configPath': '/fixture'}
            failed = SimpleNamespace(returncode=1, stdout='{"error":"reset_not_confirmed_check_saved_attempt"}')
            with patch('reset_once.read_account') as read, patch('reset_once.prepare', return_value=request), \
                 patch('reset_once.subprocess.run', return_value=failed) as consume:
                with self.assertRaisesRegex(RuntimeError, 'reset_outcome_unknown'):
                    execute(config, root, 'daybreak', episode['id'], 'actual-human-reply')
                self.assertEqual(json.loads(path.read_text())['status'], 'outcome_unknown')
                read.reset_mock(); consume.reset_mock()
                with self.assertRaisesRegex(ValueError, 'reset_reconciliation_required'):
                    execute(config, root, 'daybreak', episode['id'], 'actual-human-reply')
                read.assert_not_called(); consume.assert_not_called()

    def test_missing_attempt_record_after_uncertain_send_requires_reconciliation(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); episode = self.state['episode']
            (root / 'daybreak.json').write_text(json.dumps(self.state))
            (root / 'daybreak.json').chmod(0o600)
            request = prepare(ACCOUNT, episode, sample(100), self.inventory, 'actual-human-reply', '2026-10-01T00:00:00Z')
            path = root / ('reset-' + episode['id'] + '.json')
            config = {'accounts': [{**ACCOUNT, 'name': 'daybreak'}], 'nodeBinary': 'node', '_configPath': '/fixture'}
            failed = SimpleNamespace(returncode=1, stdout='{}')
            with patch('reset_once.read_account') as read, patch('reset_once.prepare', return_value=request), \
                 patch('reset_once.subprocess.run', return_value=failed) as consume:
                with self.assertRaisesRegex(RuntimeError, 'reset_outcome_unknown'):
                    execute(config, root, 'daybreak', episode['id'], 'actual-human-reply')
                path.unlink()
                read.reset_mock(); consume.reset_mock()
                with self.assertRaisesRegex(ValueError, 'reset_reconciliation_required'):
                    execute(config, root, 'daybreak', episode['id'], 'another-human-reply')
                read.assert_not_called(); consume.assert_not_called()

    def test_present_empty_reset_record_requires_reconciliation(self):
        for corrupt in ({}, [], False, None):
            with self.subTest(corrupt=corrupt), tempfile.TemporaryDirectory() as name:
                root = Path(name); episode = self.state['episode']
                (root / 'daybreak.json').write_text(json.dumps(self.state))
                (root / 'daybreak.json').chmod(0o600)
                path = root / ('reset-' + episode['id'] + '.json'); path.write_text(json.dumps(corrupt)); path.chmod(0o600); before = path.read_bytes()
                config = {'accounts': [{**ACCOUNT, 'name': 'daybreak'}], 'nodeBinary': 'node', '_configPath': '/fixture'}
                with patch('reset_once.read_account') as read, patch('reset_once.subprocess.run') as consume:
                    with self.assertRaisesRegex(ValueError, 'reset_reconciliation_required'):
                        execute(config, root, 'daybreak', episode['id'], 'human-reply')
                    read.assert_not_called(); consume.assert_not_called()
                self.assertEqual(path.read_bytes(), before)

    def test_reset_honors_inconsistent_account_custody(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); episode = self.state['episode']
            (root / 'daybreak.json').write_text(json.dumps(self.state))
            (root / 'daybreak.json').chmod(0o600)
            from storage import save
            save(root / 'account-custody-daybreak.json', {'account': 'wrong', 'initialized': True})
            config = {'accounts': [{**ACCOUNT, 'name': 'daybreak'}], 'nodeBinary': 'node', '_configPath': '/fixture'}
            with patch('reset_once.read_account') as read, patch('reset_once.subprocess.run') as consume:
                with self.assertRaisesRegex(ValueError, 'account_state_reconciliation_required'):
                    execute(config, root, 'daybreak', episode['id'], 'human-reply')
                read.assert_not_called(); consume.assert_not_called()

    def test_reset_honors_surviving_digest_of_missing_attempt(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); episode = self.state['episode']; state = copy.deepcopy(self.state)
            state['resetEvidence'] = 'previously-observed-digest'
            (root / 'daybreak.json').write_text(json.dumps(state))
            (root / 'daybreak.json').chmod(0o600)
            config = {'accounts': [{**ACCOUNT, 'name': 'daybreak'}], 'nodeBinary': 'node', '_configPath': '/fixture'}
            with patch('reset_once.read_account') as read, patch('reset_once.subprocess.run') as consume:
                with self.assertRaisesRegex(ValueError, 'reset_reconciliation_required'):
                    execute(config, root, 'daybreak', episode['id'], 'human-reply')
                read.assert_not_called(); consume.assert_not_called()

    def test_reset_does_not_accept_changed_record_against_saved_digest(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); episode = self.state['episode']; state = copy.deepcopy(self.state)
            state['resetEvidence'] = 'different-digest'
            (root / 'daybreak.json').write_text(json.dumps(state))
            (root / 'daybreak.json').chmod(0o600)
            request = prepare(ACCOUNT, episode, sample(100), self.inventory, 'human-reply', '2026-10-01T00:00:00Z')
            from storage import save
            save(root / ('reset-' + episode['id'] + '.json'), {'request': request, 'status': 'reset'})
            config = {'accounts': [{**ACCOUNT, 'name': 'daybreak'}]}
            with patch('reset_once.read_account') as read, patch('reset_once.subprocess.run') as consume:
                with self.assertRaisesRegex(ValueError, 'reset_reconciliation_required'):
                    execute(config, root, 'daybreak', episode['id'], 'human-reply')
                read.assert_not_called(); consume.assert_not_called()

    def test_known_reset_outcomes_are_terminal_and_unknown_saved_status_fails_closed(self):
        for status in ('reset', 'already_redeemed', 'no_credit', 'nothing_to_reset', 'invalid'):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as name:
                root = Path(name); episode = self.state['episode']
                (root / 'daybreak.json').write_text(json.dumps(self.state))
                (root / 'daybreak.json').chmod(0o600)
                request = prepare(ACCOUNT, episode, sample(100), self.inventory, 'actual-human-reply', '2026-10-01T00:00:00Z')
                path = root / ('reset-' + episode['id'] + '.json')
                previous = {'request': request, 'status': status, 'response': {'code': status}}
                path.write_text(json.dumps(previous)); path.chmod(0o600); before = path.read_bytes()
                config = {'accounts': [{**ACCOUNT, 'name': 'daybreak'}]}
                with patch('reset_once.read_account') as read, patch('reset_once.subprocess.run') as consume:
                    if status == 'invalid':
                        with self.assertRaisesRegex(ValueError, 'invalid_saved_reset_status'):
                            execute(config, root, 'daybreak', episode['id'], 'another-reference')
                    else:
                        self.assertEqual(execute(config, root, 'daybreak', episode['id'], 'another-reference'), previous)
                    read.assert_not_called(); consume.assert_not_called()
                self.assertEqual(path.read_bytes(), before)

if __name__ == '__main__':
    unittest.main()
