"""Public runtime boundaries; every account and native route is a fixture."""
import copy
import fcntl
import io
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch

from companion import serve
from configuration import load_config
from controller import run, dispatch
from safeguard import advance, observation_failed
from status import status
from preflight import preflight
from storage import save
from activation import configuration_binding
from test_guard import ACCOUNT, sample
from test_controller import CONFIG, ReceiptTransport


class RuntimeTests(unittest.TestCase):
    def test_missing_initialized_daybreak_ledger_defers_daybreak_and_keeps_main_observing(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root); pinned = config['accounts'][1]
            def read(account, **kwargs):
                value = sample(0)
                if account['name'] == 'daybreak': value.update(email=pinned['email'], accountId=pinned['accountId'])
                return value
            with patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={'email': ACCOUNT['email'], 'accountId': ACCOUNT['accountId']}):
                run(config, root, once=True, observe_only=True)
                (root / 'daybreak.json').unlink()
                health = run(config, root, once=True, observe_only=True)
            self.assertFalse((root / 'daybreak.json').exists())
            self.assertEqual(health['accounts']['daybreak']['reason'], 'account_state_reconciliation_required')
            self.assertEqual(health['accounts']['main']['status'], 'healthy')

    def test_corrupt_initialized_daybreak_ledger_is_preserved_for_reconciliation(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root); pinned = config['accounts'][1]
            def read(account, **kwargs):
                value = sample(100 if account['name'] == 'daybreak' else 0)
                if account['name'] == 'daybreak': value.update(email=pinned['email'], accountId=pinned['accountId'])
                return value
            with patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={'email': ACCOUNT['email'], 'accountId': ACCOUNT['accountId']}):
                run(config, root, once=True, observe_only=True)
                (root / 'daybreak.json').write_text('{}')
                health = run(config, root, once=True, observe_only=True)
            self.assertEqual((root / 'daybreak.json').read_text(), '{}')
            self.assertEqual(health['accounts']['daybreak']['reason'], 'account_state_reconciliation_required')

    def test_malformed_retained_delivery_is_not_replayed(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root); pinned = config['accounts'][1]
            zero = sample(100); zero.update(email=pinned['email'], accountId=pinned['accountId'])
            state, _ = advance({}, zero, pinned, now=10, policy=config['policy'])
            state['episode']['events'][0]['deliveries'] = {'parent': {}}
            save(root / 'daybreak.json', state)
            save(root / 'account-custody-daybreak.json', {'account': 'daybreak', 'initialized': True})
            save(root / 'activation.json', {'mode': 'active', 'binding': configuration_binding(config), 'reference': 'fixture'})
            with patch('controller.read_account', side_effect=lambda account, **kw: zero if account['name'] == 'daybreak' else sample(0)), \
                 patch('controller.default_identity', return_value={'email': ACCOUNT['email'], 'accountId': ACCOUNT['accountId']}), \
                 patch('controller.Transport') as transport, patch.dict(os.environ, {}, clear=True):
                bridge = transport.return_value.connection.return_value
                bridge.for_recipient.return_value = bridge
                health = run(config, root, once=True)
                bridge.send.assert_not_called()
            self.assertEqual(health['accounts']['daybreak']['reason'], 'account_state_reconciliation_required')

    def test_malformed_markerless_legacy_delivery_is_not_blessed_or_replayed(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root); pinned = config['accounts'][1]
            zero = sample(100); zero.update(email=pinned['email'], accountId=pinned['accountId'])
            state, _ = advance({}, zero, pinned, now=10, policy=config['policy'])
            state['episode']['events'][0]['deliveries'] = {'parent': {}}
            save(root / 'daybreak.json', state); before = (root / 'daybreak.json').read_bytes()
            save(root / 'activation.json', {'mode': 'active', 'binding': configuration_binding(config), 'reference': 'fixture'})
            with patch('controller.read_account', side_effect=lambda account, **kw: zero if account['name'] == 'daybreak' else sample(0)), \
                 patch('controller.default_identity', return_value={'email': ACCOUNT['email'], 'accountId': ACCOUNT['accountId']}), \
                 patch('controller.Transport') as transport, patch.dict(os.environ, {}, clear=True):
                bridge = transport.return_value.connection.return_value; bridge.for_recipient.return_value = bridge
                health = run(config, root, once=True)
                bridge.send.assert_not_called()
            self.assertEqual(health['accounts']['daybreak']['reason'], 'account_state_reconciliation_required')
            self.assertEqual((root / 'daybreak.json').read_bytes(), before)
            self.assertFalse((root / 'account-custody-daybreak.json').exists())

    def test_busy_reset_defers_daybreak_without_blocking_main_observation(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root); observed = Event(); main_reads = []
            pinned = config['accounts'][1]
            def read(account, **kwargs):
                value = sample(100)
                if account['name'] == 'daybreak':
                    value.update(email=pinned['email'], accountId=pinned['accountId'])
                    observed.set()
                else:
                    main_reads.append(True)
                return value
            lock = root / 'reset.lock'; lock.touch(mode=0o600)
            with lock.open('r+') as held, patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={k: ACCOUNT[k] for k in ('email', 'accountId')}), \
                 ThreadPoolExecutor(max_workers=1) as executor:
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                observer = executor.submit(run, config, root, once=True, observe_only=True)
                self.assertTrue(observed.wait(2))
                try:
                    observer.result(timeout=2)
                    self.assertTrue((root / 'main.json').exists())
                    self.assertFalse((root / 'daybreak.json').exists())
                    run(config, root, once=True, observe_only=True)
                    self.assertEqual(len(main_reads), 2)
                    self.assertFalse((root / 'daybreak.json').exists())
                finally:
                    fcntl.flock(held, fcntl.LOCK_UN)
                run(config, root, once=True, observe_only=True)
            self.assertTrue((root / 'daybreak.json').exists())

    def test_daybreak_refreshes_sample_after_deferring_for_reset(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root); observed = Event(); reads = []
            pinned = config['accounts'][1]
            def read(account, **kwargs):
                if account['name'] != 'daybreak': return sample(0)
                reads.append(True)
                value = sample(100 if len(reads) == 1 else 0)
                value.update(email=pinned['email'], accountId=pinned['accountId'])
                observed.set()
                return value
            lock = root / 'reset.lock'; lock.touch(mode=0o600)
            with lock.open('r+') as held, patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={k: ACCOUNT[k] for k in ('email', 'accountId')}), \
                 ThreadPoolExecutor(max_workers=1) as executor:
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                observer = executor.submit(run, config, root, once=True, observe_only=True)
                self.assertTrue(observed.wait(2))
                observer.result(timeout=2)
                self.assertFalse((root / 'daybreak.json').exists())
                fcntl.flock(held, fcntl.LOCK_UN)
                run(config, root, once=True, observe_only=True)
            self.assertEqual(len(reads), 3)
            state = json.loads((root / 'daybreak.json').read_text())
            self.assertFalse(state.get('episode', {}).get('active', False))

    def test_daybreak_dispatch_excludes_concurrent_reset(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root)
            save(root / 'activation.json', {'mode': 'active', 'binding': configuration_binding(config), 'reference': 'fixture'})
            pinned = config['accounts'][1]; checked = []
            def read(account, **kwargs):
                value = sample(100 if account['name'] == 'daybreak' else 0)
                if account['name'] == 'daybreak': value.update(email=pinned['email'], accountId=pinned['accountId'])
                return value
            def dispatch_check(state, account, *args, **kwargs):
                if account['name'] != 'daybreak': return
                with (root / 'reset.lock').open('r+') as other:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
                checked.append(True)
            with patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={'email': ACCOUNT['email'], 'accountId': ACCOUNT['accountId']}), \
                 patch('controller.Transport'), patch('controller.dispatch', side_effect=dispatch_check), \
                 patch.dict(os.environ, {}, clear=True):
                run(config, root, once=True)
            self.assertEqual(checked, [True])

    def test_matching_default_account_recovers_pinned_failure_under_reset_lock(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root); pinned = config['accounts'][1]
            zero = sample(100); zero.update(email=pinned['email'], accountId=pinned['accountId'])
            zero['rate_limit_reset_credits']['applicable_available_count'] = 0
            def read(account, **kwargs):
                if account['name'] == 'daybreak': raise RuntimeError('fixture_pinned_unavailable')
                return zero
            with patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={'email': pinned['email'], 'accountId': pinned['accountId']}):
                run(config, root, once=True, observe_only=True)
            state = json.loads((root / 'daybreak.json').read_text())
            self.assertTrue(state['episode']['active'])
            self.assertTrue(any(event.get('reason') == 'no_applicable_reset' for event in state['episode']['events']))

    def test_matching_existing_main_episode_becomes_daybreak_episode_without_losing_receipt(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root); pinned = config['accounts'][1]
            zero = sample(100); zero.update(email=pinned['email'], accountId=pinned['accountId'])
            main = {**config['accounts'][0], 'email': pinned['email'], 'accountId': pinned['accountId'], 'identityGeneration': 'fixture-generation'}
            state, _ = advance({}, zero, main, now=10, policy=config['policy'])
            state['identityGeneration'] = 'fixture-generation'
            state['episode']['events'][0]['deliveries'] = {'parent': {'status': 'outcome_unknown'}}
            episode = copy.deepcopy(state['episode'])
            save(root / 'main.json', state)
            save(root / 'main-selection.json', {'email': pinned['email'], 'accountId': pinned['accountId'], 'generation': 'fixture-generation'})
            with patch('controller.read_account', return_value=zero), \
                 patch('controller.default_identity', return_value={'email': pinned['email'], 'accountId': pinned['accountId']}):
                run(config, root, once=True, observe_only=True)
            main_after = json.loads((root / 'main.json').read_text())
            daybreak = json.loads((root / 'daybreak.json').read_text())
            self.assertFalse(main_after['episode']['active'])
            self.assertEqual(daybreak['episode']['id'], episode['id'])
            self.assertEqual(daybreak['episode']['resetIdempotencyKey'], episode['resetIdempotencyKey'])
            self.assertEqual(daybreak['episode']['events'][0]['deliveries'], episode['events'][0]['deliveries'])

    def test_surviving_main_episode_detects_missing_migrated_daybreak_ledger(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root); pinned = config['accounts'][1]
            zero = sample(100); zero.update(email=pinned['email'], accountId=pinned['accountId'])
            main = {**config['accounts'][0], 'email': pinned['email'], 'accountId': pinned['accountId'], 'identityGeneration': 'fixture-generation'}
            state, _ = advance({}, zero, main, now=10, policy=config['policy']); state['identityGeneration'] = 'fixture-generation'
            state['episode']['events'][0]['deliveries'] = {'parent': {'status': 'outcome_unknown'}}
            save(root / 'main.json', state)
            save(root / 'main-selection.json', {'email': pinned['email'], 'accountId': pinned['accountId'], 'generation': 'fixture-generation'})
            with patch('controller.read_account', return_value=zero), \
                 patch('controller.default_identity', return_value={'email': pinned['email'], 'accountId': pinned['accountId']}):
                run(config, root, once=True, observe_only=True)
                (root / 'daybreak.json').unlink()
                (root / 'account-custody-daybreak.json').unlink()
                health = run(config, root, once=True, observe_only=True)
            self.assertFalse((root / 'daybreak.json').exists())
            self.assertEqual(health['accounts']['daybreak']['reason'], 'account_state_reconciliation_required')

    def test_archived_migration_detects_missing_daybreak_ledger(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root); pinned = config['accounts'][1]
            zero = sample(100); zero.update(email=pinned['email'], accountId=pinned['accountId'])
            main = {**config['accounts'][0], 'email': pinned['email'], 'accountId': pinned['accountId'], 'identityGeneration': 'old'}
            state, _ = advance({}, zero, main, now=10, policy=config['policy'])
            state['episode']['active'] = False
            state['episode']['supersededBy'] = 'daybreak:' + state['episode']['id']
            archive = root / 'account-history'; archive.mkdir(mode=0o700)
            save(archive / 'old.json', state); before = (archive / 'old.json').read_bytes()
            def read(account, **kwargs): return zero if account['name'] == 'daybreak' else sample(0)
            with patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={'email': ACCOUNT['email'], 'accountId': ACCOUNT['accountId']}):
                health = run(config, root, once=True, observe_only=True)
            self.assertFalse((root / 'daybreak.json').exists())
            self.assertEqual(health['accounts']['daybreak']['reason'], 'account_state_reconciliation_required')
            self.assertEqual((archive / 'old.json').read_bytes(), before)

    def test_closed_reset_episode_allows_later_new_episode_without_losing_evidence(self):
        from controller import reset_evidence
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root); pinned = config['accounts'][1]
            zero = sample(100); zero.update(email=pinned['email'], accountId=pinned['accountId'])
            state, _ = advance({}, zero, pinned, now=10, policy=config['policy']); old_id = state['episode']['id']
            request = {'episodeId': old_id, 'accountId': pinned['accountId'], 'idempotencyKey': state['episode']['resetIdempotencyKey']}
            save(root / ('reset-' + old_id + '.json'), {'request': request, 'status': 'reset'})
            state['resetEvidence'] = reset_evidence(root, state); state['resetEvidenceEpisode'] = old_id
            save(root / 'daybreak.json', state)
            save(root / 'release-daybreak.json', {'episodeId': old_id, 'accountId': pinned['accountId'], 'evidenceReference': 'fixture', 'holdReleased': True})
            restored = sample(0); restored.update(email=pinned['email'], accountId=pinned['accountId']); restored['rate_limit']['allowed'] = True
            def read(account, **kwargs): return restored if account['name'] == 'daybreak' else sample(0)
            with patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={'email': ACCOUNT['email'], 'accountId': ACCOUNT['accountId']}):
                run(config, root, once=True, observe_only=True)
                with patch('controller.read_account', side_effect=lambda account, **kw: zero if account['name'] == 'daybreak' else sample(0)):
                    run(config, root, once=True, observe_only=True)
            new = json.loads((root / 'daybreak.json').read_text())
            closed = json.loads((root / ('closed-' + old_id + '.json')).read_text())
            self.assertTrue(new['episode']['active'])
            self.assertNotEqual(new['episode']['id'], old_id)
            self.assertEqual(closed['resetEvidence'], state['resetEvidence'])

    def test_predecessor_reset_digest_is_retained_when_episode_closes(self):
        with tempfile.TemporaryDirectory(prefix='predecessor-closure-') as name:
            root = Path(name); config = self.config(root); pinned = config['accounts'][1]
            old_id = '11111111-1111-4111-8111-111111111111'
            reset_key = '22222222-2222-4222-8222-222222222222'
            zero = sample(100); zero.update(email=pinned['email'], accountId=pinned['accountId'])
            with patch('safeguard.uuid.uuid4', side_effect=[old_id, reset_key]):
                state, _ = advance({}, zero, pinned, now=10, policy=config['policy'])
            record = {'request': {'episodeId': old_id, 'email': pinned['email'],
                                  'accountId': pinned['accountId'], 'idempotencyKey': reset_key,
                                  'creditId': 'predecessor-credit',
                                  'confirmationReference': 'fixture-confirmation',
                                  'preparedAt': '2026-10-01T00:00:00Z'},
                      'status': 'reset', 'response': {'code': 'reset'}}
            save(root / ('reset-' + old_id + '.json'), record)
            save(root / 'daybreak.json', state)
            save(root / 'release-daybreak.json', {'episodeId': old_id,
                 'accountId': pinned['accountId'], 'evidenceReference': 'fixture-release', 'holdReleased': True})
            restored = sample(0); restored.update(email=pinned['email'], accountId=pinned['accountId'])
            restored['rate_limit']['allowed'] = True
            with patch('controller.read_account', side_effect=lambda account, **kw:
                       restored if account['name'] == 'daybreak' else sample(0)), \
                 patch('controller.default_identity', return_value={
                       'email': ACCOUNT['email'], 'accountId': ACCOUNT['accountId']}):
                run(config, root, once=True, observe_only=True)
            closed = json.loads((root / ('closed-' + old_id + '.json')).read_text())
            self.assertEqual(closed['resetEvidence'],
                             '0bcde9a83d218aea4e0cfae79644044dfafe57203ed3feb9d4087cac67cd8159')

    def test_episode_without_reset_closes_without_inventing_reset_digest(self):
        with tempfile.TemporaryDirectory(prefix='no-reset-closure-') as name:
            root = Path(name); config = self.config(root); pinned = config['accounts'][1]
            zero = sample(100, resets=0); zero.update(email=pinned['email'], accountId=pinned['accountId'])
            state, _ = advance({}, zero, pinned, now=10, policy=config['policy'])
            episode_id = state['episode']['id']
            save(root / 'daybreak.json', state)
            save(root / 'release-daybreak.json', {'episodeId': episode_id,
                 'accountId': pinned['accountId'], 'evidenceReference': 'fixture-release', 'holdReleased': True})
            restored = sample(0, resets=0); restored.update(email=pinned['email'], accountId=pinned['accountId'])
            restored['rate_limit']['allowed'] = True
            with patch('controller.read_account', side_effect=lambda account, **kw:
                       restored if account['name'] == 'daybreak' else sample(0)), \
                 patch('controller.default_identity', return_value={
                       'email': ACCOUNT['email'], 'accountId': ACCOUNT['accountId']}):
                run(config, root, once=True, observe_only=True)
            closed = json.loads((root / ('closed-' + episode_id + '.json')).read_text())
            self.assertFalse(closed['active'])
            self.assertIsNone(closed.get('resetEvidence'))

    def test_malformed_markerless_reset_record_prevents_episode_closure(self):
        malformed = {'empty': None, 'episodeId': '00000000-0000-0000-0000-000000000001',
                     'accountId': 'foreign-account', 'idempotencyKey': 'foreign-reset-key'}
        for field, value in malformed.items():
            with self.subTest(record=field), tempfile.TemporaryDirectory(prefix='malformed-predecessor-') as name:
                root = Path(name); config = self.config(root); pinned = config['accounts'][1]
                zero = sample(100); zero.update(email=pinned['email'], accountId=pinned['accountId'])
                state, _ = advance({}, zero, pinned, now=10, policy=config['policy'])
                episode_id = state['episode']['id']
                request = {'episodeId': episode_id, 'accountId': pinned['accountId'],
                           'idempotencyKey': state['episode']['resetIdempotencyKey']}
                record = {} if field == 'empty' else {'request': {**request, field: value}, 'status': 'reset'}
                reset_path = root / ('reset-' + episode_id + '.json')
                marker_path = root / ('reset-custody-' + episode_id + '.json')
                save(reset_path, record); before = reset_path.read_bytes()
                save(root / 'daybreak.json', state)
                save(root / 'release-daybreak.json', {'episodeId': episode_id,
                     'accountId': pinned['accountId'], 'evidenceReference': 'fixture-release', 'holdReleased': True})
                restored = sample(0); restored.update(email=pinned['email'], accountId=pinned['accountId'])
                restored['rate_limit']['allowed'] = True
                with patch('controller.read_account', side_effect=lambda account, **kw:
                           restored if account['name'] == 'daybreak' else sample(0)), \
                     patch('controller.default_identity', return_value={
                           'email': ACCOUNT['email'], 'accountId': ACCOUNT['accountId']}):
                    health = run(config, root, once=True, observe_only=True)
                retained = json.loads((root / 'daybreak.json').read_text())
                self.assertEqual(health['accounts']['daybreak'].get('reason'), 'reset_reconciliation_required')
                self.assertEqual(retained['episode']['id'], episode_id)
                self.assertTrue(retained['episode']['active'])
                self.assertFalse((root / ('closed-' + episode_id + '.json')).exists())
                self.assertEqual(reset_path.read_bytes(), before)
                self.assertFalse(marker_path.exists())

    def test_conflicting_existing_episodes_are_preserved_for_reconciliation(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root); pinned = config['accounts'][1]
            zero = sample(100); zero.update(email=pinned['email'], accountId=pinned['accountId'])
            main = {**config['accounts'][0], 'email': pinned['email'], 'accountId': pinned['accountId'], 'identityGeneration': 'fixture-generation'}
            main_state, _ = advance({}, zero, main, now=10, policy=config['policy'])
            main_state['identityGeneration'] = 'fixture-generation'
            daybreak_state, _ = advance({}, zero, pinned, now=10, policy=config['policy'])
            save(root / 'main.json', main_state); save(root / 'daybreak.json', daybreak_state)
            save(root / 'main-selection.json', {'email': pinned['email'], 'accountId': pinned['accountId'], 'generation': 'fixture-generation'})
            with patch('controller.read_account', return_value=zero), \
                 patch('controller.default_identity', return_value={'email': pinned['email'], 'accountId': pinned['accountId']}):
                health = run(config, root, once=True, observe_only=True)
            self.assertEqual(health['accounts']['daybreak']['reason'], 'canonical_episode_reconciliation_required')
            self.assertEqual(json.loads((root / 'main.json').read_text()), main_state)
            self.assertEqual(json.loads((root / 'daybreak.json').read_text()), daybreak_state)

    def test_conflicting_receipts_for_same_episode_are_not_discarded(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root); pinned = config['accounts'][1]
            zero = sample(100); zero.update(email=pinned['email'], accountId=pinned['accountId'])
            main = {**config['accounts'][0], 'email': pinned['email'], 'accountId': pinned['accountId'], 'identityGeneration': 'fixture-generation'}
            main_state, _ = advance({}, zero, main, now=10, policy=config['policy'])
            main_state['identityGeneration'] = 'fixture-generation'
            daybreak_state = copy.deepcopy(main_state)
            daybreak_state['episode']['events'][0]['deliveries'] = {'parent': {'status': 'outcome_unknown'}}
            save(root / 'main.json', main_state); save(root / 'daybreak.json', daybreak_state)
            save(root / 'main-selection.json', {'email': pinned['email'], 'accountId': pinned['accountId'], 'generation': 'fixture-generation'})
            with patch('controller.read_account', return_value=zero), \
                 patch('controller.default_identity', return_value={'email': pinned['email'], 'accountId': pinned['accountId']}):
                health = run(config, root, once=True, observe_only=True)
            self.assertEqual(health['accounts']['daybreak']['reason'], 'canonical_episode_reconciliation_required')
            self.assertEqual(json.loads((root / 'main.json').read_text()), main_state)
            self.assertEqual(json.loads((root / 'daybreak.json').read_text()), daybreak_state)

    def test_observe_only_requires_predecessor_lock_before_reads(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root)
            with (root / 'legacy.lock').open('r') as held:
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch('controller.read_account', side_effect=RuntimeError('fixture-no-read')) as read:
                    with self.assertRaises(BlockingIOError):
                        run(config, root, once=True, observe_only=True)
                    read.assert_not_called()
            self.assertFalse((root / 'health.json').exists())

    def test_observer_rejects_symlink_singleton_lock(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root)
            target = root / 'other-lock'; target.touch()
            (root / 'observer.lock').symlink_to(target)
            with patch('controller.read_account', side_effect=RuntimeError('fixture-no-read')) as read:
                with self.assertRaises(OSError):
                    run(config, root, once=True, observe_only=True)
                read.assert_not_called()

    def test_observer_rejects_symlink_shared_lock(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root)
            target = root / 'other-lock'; target.touch()
            (root / 'legacy.lock').unlink()
            (root / 'legacy.lock').symlink_to(target)
            with patch('controller.read_account', side_effect=RuntimeError('fixture-no-read')) as read:
                with self.assertRaises(OSError):
                    run(config, root, once=True)
                read.assert_not_called()

    def test_observer_rejects_nonprivate_shared_lock(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root)
            (root / 'legacy.lock').chmod(0o644)
            with patch('controller.read_account', side_effect=RuntimeError('fixture-no-read')) as read:
                with self.assertRaises(ValueError):
                    run(config, root, once=True)
                read.assert_not_called()

    def test_observer_rejects_shared_lock_in_nonprivate_directory(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root)
            public = root / 'shared'; public.mkdir(mode=0o755); public.chmod(0o755)
            lock = public / 'lock'; lock.touch(mode=0o600)
            config['sharedLock'] = str(lock)
            with patch('controller.read_account', side_effect=RuntimeError('fixture-no-read')) as read:
                with self.assertRaises(ValueError):
                    run(config, root, once=True)
                read.assert_not_called()

    def test_controller_preserves_alert_throttle_through_maintenance_reactivation(self):
        for storage, uncertain in (('unreadable', False), ('unreadable', True), ('failed-save', False)):
            with self.subTest(storage=storage, uncertain=uncertain):
                self._exercise_controller_alert_throttle(storage, uncertain)

    def _exercise_controller_alert_throttle(self, storage, uncertain):
        from transport import Transport as RealTransport
        from storage import save as real_save
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root); config['incidentRoutingEnabled'] = True
            save(root / 'activation.json', {'mode': 'active', 'binding': configuration_binding(config),
                                            'reference': 'fixture-approval'})
            transport_dir = root / 'transport'; transport_dir.mkdir(mode=0o700)
            notice = transport_dir / 'emergency-notice.json'
            if storage == 'unreadable': notice.write_text('damaged'); notice.chmod(0o600)
            pinned = config['accounts'][1]; state = {'cycle': 0, 'clock': 1000}; alerts = []; counts = []; instances = []
            def read(account, **kwargs):
                value = sample(95)
                if account['name'] == 'daybreak': value.update(email=pinned['email'], accountId=pinned['accountId'])
                return value
            def status(*_args): return {'mode': 'maintenance' if state['cycle'] == 1 else 'active'}
            def notify(message):
                alerts.append(message)
                if uncertain and len(alerts) == 1: raise RuntimeError('fixture-uncertain-notice')
            def fixture_save(path, value):
                if storage == 'failed-save' and Path(path) == notice: raise OSError('fixture-save-failure')
                return real_save(path, value)
            class FixtureTransport(RealTransport):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs, notifier=notify, clock=lambda: state['clock'])
                    self.intake_status = 'submitted'; self.closed = False; instances.append(self)
                def connection(self): self.health = {'healthy': True}; return self
                def observation_recovered(self, _name): pass
                def reconcile_pending(self): pass
                def close(self): self.closed = True; super().close()
            def sleep(_seconds):
                counts.append(len(alerts)); state['cycle'] += 1
                if state['cycle'] == 3: state['clock'] += 3600
                if state['cycle'] == 4: raise RuntimeError('fixture-stop')
            with patch('controller.validate_config'), patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={k: ACCOUNT[k] for k in ('email', 'accountId')}), \
                 patch('controller.delivery_status', side_effect=status), patch('controller.Transport', FixtureTransport), \
                 patch('transport.save', side_effect=fixture_save), \
                 patch('controller.time.sleep', side_effect=sleep), patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(RuntimeError, 'fixture-stop'): run(config, root)
            self.assertEqual(counts, [1, 1, 1, 2])
            self.assertEqual(len(instances), 2)
            self.assertTrue(all(instance.closed for instance in instances))
            if storage == 'unreadable': self.assertEqual(notice.read_text(), 'damaged')
            else: self.assertFalse(notice.exists())

    def test_maintenance_after_connection_prevents_receipt_reconciliation(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root); config['incidentRoutingEnabled'] = True
            save(root / 'activation.json', {'mode': 'active', 'binding': configuration_binding(config),
                                            'reference': 'fixture-approval'})
            pinned = config['accounts'][1]; held = {'value': False}
            def read(account, **kwargs):
                held['value'] = True
                value = sample(28)
                if account['name'] == 'daybreak': value.update(email=pinned['email'], accountId=pinned['accountId'])
                return value
            def status(*_args):
                return {'mode': 'maintenance' if held['value'] else 'active'}
            with patch('controller.validate_config'), patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={k: ACCOUNT[k] for k in ('email', 'accountId')}), \
                 patch('controller.delivery_status', side_effect=status), patch('controller.Transport') as transport, \
                 patch.dict(os.environ, {}, clear=True):
                transport.return_value.incident_error = False
                transport.return_value.intake_status = 'not_needed'
                run(config, root, once=True)
                transport.return_value.reconcile_pending.assert_not_called()

    def test_pending_receipt_survives_maintenance_and_reconciles_after_reactivation(self):
        from transport import Transport as RealTransport
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root)
            config.update(incidentRoutingEnabled=True, conciergeThreadId='fixture-concierge',
                          incidentReceiptStore=str(root / 'receipts.json'), roleBindings={
                              'concierge': {'thread_id': 'fixture-concierge', 'host_id': 'local',
                                            'role': 'concierge', 'acceptance_ref': 'fixture'},
                              'repairOwner': {'thread_id': 'owner', 'host_id': 'local',
                                              'role': 'repair_owner', 'acceptance_ref': 'fixture'}})
            save(root / 'activation.json', {'mode': 'active', 'binding': configuration_binding(config),
                                            'reference': 'fixture-approval'})
            transport_dir = root / 'transport'; transport_dir.mkdir(mode=0o700)
            outbox = transport_dir / 'incident-outbox.json'
            save(outbox, {'version': 1, 'incidents': {'incident': {'status': 'submitted', 'reconciliation': []}}})
            save(root / 'receipts.json', {'version': 1, 'incidents': {}})
            pinned = config['accounts'][1]; state = {'held': False, 'hold_on_read': True}
            def read(account, **kwargs):
                if state['hold_on_read']: state['held'] = True
                value = sample(28)
                if account['name'] == 'daybreak': value.update(email=pinned['email'], accountId=pinned['accountId'])
                return value
            def status(*_args): return {'mode': 'maintenance' if state['held'] else 'active'}
            class Bridge:
                def close(self): pass
            class FixtureTransport(RealTransport):
                def connection(self):
                    self.destinations['concierge'].bridge = Bridge(); self.health = {'healthy': True}; return self
                def observation_recovered(self, _name): pass
            with patch('controller.validate_config'), patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={k: ACCOUNT[k] for k in ('email', 'accountId')}), \
                 patch('controller.delivery_status', side_effect=status), patch('controller.Transport', FixtureTransport), \
                 patch('transport.reconcile_snapshot', return_value='owned_continuation_matched') as readback, \
                 patch.dict(os.environ, {}, clear=True):
                before = outbox.read_bytes()
                run(config, root, once=True)
                readback.assert_not_called(); self.assertEqual(outbox.read_bytes(), before)
                state.update(held=False, hold_on_read=False)
                run(config, root, once=True)
                readback.assert_called_once()
                result = json.loads(outbox.read_text())['incidents']['incident']['reconciliation']
                self.assertEqual(result[-1]['stage'], 'owned_continuation_matched')

    def test_maintenance_before_connection_prevents_native_connection(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root)
            pinned = config['accounts'][1]; checks = []
            def read(account, **kwargs):
                value = sample(28)
                if account['name'] == 'daybreak': value.update(email=pinned['email'], accountId=pinned['accountId'])
                return value
            def status(*_args):
                checks.append(True)
                return {'mode': 'active' if len(checks) == 1 else 'maintenance'}
            with patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={k: ACCOUNT[k] for k in ('email', 'accountId')}), \
                 patch('controller.delivery_status', side_effect=status), patch('controller.Transport') as transport, \
                 patch.dict(os.environ, {}, clear=True):
                run(config, root, once=True)
                transport.return_value.connection.assert_not_called()

    def test_maintenance_waits_for_started_connection_verification(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        from activation import decision_lock
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root)
            pinned = config['accounts'][1]; entered = Event(); release = Event(); held = Event()
            def read(account, **kwargs):
                value = sample(28)
                if account['name'] == 'daybreak': value.update(email=pinned['email'], accountId=pinned['accountId'])
                return value
            def connect():
                entered.set()
                if not release.wait(5): raise RuntimeError('fixture-timeout')
                return None
            def maintenance():
                with decision_lock(root): held.set()
            with patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={k: ACCOUNT[k] for k in ('email', 'accountId')}), \
                 patch('controller.Transport') as transport, patch.dict(os.environ, {}, clear=True), \
                 ThreadPoolExecutor(max_workers=2) as executor:
                transport.return_value.connection.side_effect = connect
                observer = executor.submit(run, config, root, once=True)
                self.assertTrue(entered.wait(2))
                hold = executor.submit(maintenance)
                self.assertFalse(held.wait(0.05))
                release.set()
                observer.result(timeout=3); hold.result(timeout=3)
                self.assertTrue(held.is_set())

    def test_maintenance_waits_for_started_receipt_reconciliation(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        from activation import decision_lock
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root); config['incidentRoutingEnabled'] = True
            save(root / 'activation.json', {'mode': 'active', 'binding': configuration_binding(config),
                                            'reference': 'fixture-approval'})
            pinned = config['accounts'][1]; entered = Event(); release = Event(); held = Event()
            def read(account, **kwargs):
                value = sample(28)
                if account['name'] == 'daybreak': value.update(email=pinned['email'], accountId=pinned['accountId'])
                return value
            def reconcile():
                entered.set()
                if not release.wait(5): raise RuntimeError('fixture-timeout')
            def maintenance():
                with decision_lock(root): held.set()
            with patch('controller.validate_config'), patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={k: ACCOUNT[k] for k in ('email', 'accountId')}), \
                 patch('controller.Transport') as transport, patch.dict(os.environ, {}, clear=True), \
                 ThreadPoolExecutor(max_workers=2) as executor:
                transport.return_value.incident_error = False
                transport.return_value.intake_status = 'not_needed'
                transport.return_value.reconcile_pending.side_effect = reconcile
                observer = executor.submit(run, config, root, once=True)
                self.assertTrue(entered.wait(5))
                holder = executor.submit(maintenance)
                self.assertFalse(held.wait(0.1))
                release.set()
                observer.result(timeout=5); holder.result(timeout=5)
                self.assertTrue(held.is_set())

    def test_maintenance_prevents_new_emergency_notice(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            config = self.config(root)
            config['incidentRoutingEnabled'] = True
            pinned = config['accounts'][1]
            def read(account, **kwargs):
                value = sample(95)
                if account['name'] == 'daybreak':
                    value.update(email=pinned['email'], accountId=pinned['accountId'])
                return value
            with patch('controller.validate_config'), patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={k: ACCOUNT[k] for k in ('email', 'accountId')}), \
                 patch('controller.delivery_status', side_effect=[{'mode': 'active'}, {'mode': 'maintenance'}]), \
                 patch('controller.Transport') as transport, patch.dict(os.environ, {}, clear=True):
                transport.return_value.intake_status = 'outcome_unknown'
                transport.return_value.connection.return_value = transport.return_value
                run(config, root, once=True)
                transport.return_value.emergency_notice.assert_not_called()

    def test_uncertain_intake_uses_emergency_alert_without_retry(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            config = self.config(root)
            config['incidentRoutingEnabled'] = True
            save(root / 'activation.json', {'mode': 'active', 'binding': configuration_binding(config),
                                            'reference': 'fixture-approval'})
            pinned = config['accounts'][1]
            def read(account, **kwargs):
                value = sample(95)
                if account['name'] == 'daybreak':
                    value.update(email=pinned['email'], accountId=pinned['accountId'])
                return value
            with patch('controller.validate_config'), patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={k: ACCOUNT[k] for k in ('email', 'accountId')}), \
                 patch('controller.Transport') as transport, patch.dict(os.environ, {}, clear=True):
                transport.return_value.intake_status = 'outcome_unknown'
                transport.return_value.connection.return_value = transport.return_value
                run(config, root, once=True)
                transport.return_value.emergency_notice.assert_called_once_with(
                    intake_failed=True, active=None, used_percent=95)

    def test_new_routing_preflight_checks_dispatch_concierge_and_owner(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            config = self.config(root)
            config.update(incidentRoutingEnabled=True, conciergeThreadId='concierge', roleBindings={
                'foreman': {'thread_id': config['foremanThreadId'], 'host_id': 'local',
                            'role': 'dispatch', 'acceptance_ref': 'fixture'},
                'concierge': {'thread_id': 'concierge', 'host_id': 'local',
                              'role': 'concierge', 'acceptance_ref': 'fixture'},
                'repairOwner': {'thread_id': 'owner', 'host_id': 'local',
                                'role': 'repair_owner', 'acceptance_ref': 'fixture'}})
            with patch('preflight.validate_config'), patch('preflight.read_account'), \
                 patch('preflight.socket_identity'), patch('preflight.NativeBridge') as factory, \
                 patch('preflight.verify_bridge') as verify, patch.dict(os.environ, {'CODEX_APP_TOOLS_PIPE_PATH': '/fixture'}):
                preflight(config, root, check_live=True)
                verify.assert_called_once_with(factory.return_value, config['foremanThreadId'], config['parentThreadId'],
                                               dispatch_binding=config['roleBindings']['foreman'], concierge='concierge',
                                               concierge_binding=config['roleBindings']['concierge'],
                                               owner_binding=config['roleBindings']['repairOwner'])

    def test_observation_fault_reaches_agent_intake_without_stopping_other_reads(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            config = self.config(root)
            config['incidentRoutingEnabled'] = True
            save(root / 'activation.json', {'mode': 'active', 'binding': configuration_binding(config),
                                            'reference': 'fixture-approval'})
            pinned = config['accounts'][1]
            def read(account, **kwargs):
                if account['name'] == 'main':
                    raise RuntimeError('fixture-read-failure')
                value = sample(28)
                value.update(email=pinned['email'], accountId=pinned['accountId'])
                return value
            with patch('controller.validate_config'), patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={k: ACCOUNT[k] for k in ('email', 'accountId')}), \
                 patch('controller.Transport') as transport, patch.dict(os.environ, {}, clear=True):
                transport.return_value.intake_status = 'submitted'
                transport.return_value.connection.return_value = transport.return_value
                health = run(config, root, once=True)
                transport.return_value.observation_failed.assert_called_once_with('main')
                transport.return_value.observation_recovered.assert_called_once_with('daybreak')
                self.assertEqual(health['accounts']['daybreak']['observationStatus'], 'healthy')

    def test_failed_observation_incident_record_triggers_alert_without_blocking_other_account(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root)
            config['incidentRoutingEnabled'] = True
            save(root / 'activation.json', {'mode': 'active', 'binding': configuration_binding(config),
                                            'reference': 'fixture-approval'})
            pinned = config['accounts'][1]
            def read(account, **kwargs):
                if account['name'] == 'main': raise RuntimeError('fixture-read-failure')
                value = sample(28); value.update(email=pinned['email'], accountId=pinned['accountId'])
                return value
            with patch('controller.validate_config'), patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={k: ACCOUNT[k] for k in ('email', 'accountId')}), \
                 patch('controller.Transport') as transport, patch.dict(os.environ, {}, clear=True):
                transport.return_value.intake_status = 'not_needed'
                transport.return_value.observation_failed.side_effect = OSError('fixture-journal-failure')
                transport.return_value.connection.return_value = transport.return_value
                health = run(config, root, once=True)
                self.assertEqual(health['accounts']['daybreak']['observationStatus'], 'healthy')
                transport.return_value.emergency_notice.assert_called_once_with(
                    intake_failed=True, active=None, used_percent=None)

    def test_failed_transport_incident_registration_triggers_alert_after_reconciliation(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root)
            config['incidentRoutingEnabled'] = True
            save(root / 'activation.json', {'mode': 'active', 'binding': configuration_binding(config),
                                            'reference': 'fixture-approval'})
            pinned = config['accounts'][1]
            def read(account, **kwargs):
                value = sample(95)
                if account['name'] == 'daybreak':
                    value.update(email=pinned['email'], accountId=pinned['accountId'])
                return value
            with patch('controller.validate_config'), patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={k: ACCOUNT[k] for k in ('email', 'accountId')}), \
                 patch('controller.Transport') as transport, patch.dict(os.environ, {}, clear=True):
                instance = transport.return_value
                instance.intake_status = 'not_needed'
                instance.incident_registration_failed = True
                instance.connection.side_effect = OSError('fixture-transport-health-write')
                health = run(config, root, once=True)
                self.assertEqual(health['accounts']['daybreak']['observationStatus'], 'healthy')
                instance.emergency_notice.assert_called_once_with(intake_failed=True, active=None, used_percent=95)

    def test_failed_intake_uses_fresh_quota_and_unknown_activity_for_alert(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            config = self.config(root)
            config['incidentRoutingEnabled'] = True
            save(root / 'activation.json', {'mode': 'active', 'binding': configuration_binding(config),
                                            'reference': 'fixture-approval'})
            pinned = config['accounts'][1]
            def read(account, **kwargs):
                value = sample(28)
                if account['name'] == 'daybreak':
                    value.update(email=pinned['email'], accountId=pinned['accountId'])
                return value
            with patch('controller.validate_config'), patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={k: ACCOUNT[k] for k in ('email', 'accountId')}), \
                 patch('controller.Transport') as transport, patch.dict(os.environ, {}, clear=True):
                transport.return_value.intake_status = 'waiting_for_route'
                transport.return_value.connection.return_value = transport.return_value
                run(config, root, once=True)
                transport.return_value.emergency_notice.assert_called_once_with(
                    intake_failed=True, active=None, used_percent=28)

    def test_main_receipt_reconciliation_does_not_starve_daybreak_delivery(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root); pinned = config['accounts'][1]
            def read(account, **kwargs):
                if account['name'] == 'main': return sample(100)
                value = sample(100); value.update(email=pinned['email'], accountId=pinned['accountId'])
                return value
            with patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={k: ACCOUNT[k] for k in ('email', 'accountId')}), \
                 patch.dict(os.environ, {}, clear=True), patch('controller.time.time', return_value=20):
                run(config, root, once=True, observe_only=True)
                state = json.loads((root / 'main.json').read_text())
                state['episode']['events'][0]['deliveries'] = {'parent': {'status': 'outcome_unknown', 'target': 'parent'}}
                save(root / 'main.json', state)
                with patch('controller.Transport') as transport:
                    transport.return_value.connection.return_value.for_recipient.return_value = transport.return_value.connection.return_value
                    health = run(config, root, once=True)
                    bridge = transport.return_value.connection.return_value
                    bridge.send.assert_called_once()
                    self.assertIn('for daybreak account', bridge.send.call_args.args[1])
                    transport.return_value.close.assert_called_once()
                self.assertEqual(health['accounts']['main']['status'], 'degraded')
                self.assertEqual(health['accounts']['daybreak']['status'], 'healthy')

    def test_rearm_cli_accepts_unsent_invalidated_pause_without_false_hold_claim(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            state, _ = advance({}, sample(100, resets=0), ACCOUNT, now=10)
            state['episode']['events'][0]['invalidated'] = {'reason': 'daybreak_no_longer_main'}
            save(root / 'daybreak.json', state)
            command = [sys.executable, '-B', str(Path(__file__).with_name('request_rearm.py')),
                       '--state-dir', str(root), '--account', 'daybreak', '--episode', state['episode']['id'],
                       '--evidence-reference', 'fixture-restored-quota-evidence']
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(json.loads((root / 'release-daybreak.json').read_text())['holdReleased'])
            state['episode']['events'][0]['deliveries'] = {'foreman': {'status': 'attempting'}}
            save(root / 'daybreak.json', state)
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)

    def test_only_quota_read_failure_can_start_observation_fallback(self):
        for failure in ('transport', 'delivery', 'read'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as name:
                root = Path(name); config = self.config(root)
                config['policy'].update(pauseWhenUnmetered=True,
                    fallbackDecision='pause_at_zero_when_unmetered', fallbackDecisionReference='fixture-approval')
                save(root / 'activation.json', {'mode': 'active', 'binding': configuration_binding(config), 'reference': 'fixture-approval'})
                pinned = config['accounts'][1]
                value = sample(100); value.update(email=pinned['email'], accountId=pinned['accountId'])
                state, _ = advance({}, value, pinned, now=10, policy=config['policy'])
                save(root / 'daybreak.json', state)
                def read(account, **kwargs):
                    if account['name'] == 'daybreak':
                        if failure == 'read': raise RuntimeError('fixture_quota_read_failed')
                        return value
                    return sample(0)
                with patch('controller.read_account', side_effect=read), \
                     patch('controller.default_identity', return_value={'email': ACCOUNT['email'], 'accountId': ACCOUNT['accountId']}), \
                     patch('controller.Transport') as transport, patch.dict(os.environ, {}, clear=True), \
                     patch('controller.time.time', return_value=20):
                    transport.return_value.connection.return_value.for_recipient.return_value = transport.return_value.connection.return_value
                    if failure == 'transport':
                        transport.return_value.connection.side_effect = RuntimeError('fixture_transport_down')
                    elif failure == 'delivery':
                        transport.return_value.connection.return_value.send.side_effect = TimeoutError('fixture_timeout')
                    run(config, root, once=True)
                events = json.loads((root / 'daybreak.json').read_text())['episode']['events']
                fallbacks = [e for e in events if e.get('reason') == 'observation_failed_during_confirmed_exhaustion']
                self.assertEqual(len(fallbacks), 1 if failure == 'read' else 0)

    def test_failed_read_retries_known_unsent_fallback_after_route_recovers(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root)
            config['policy'].update(pauseWhenUnmetered=True,
                fallbackDecision='pause_at_zero_when_unmetered', fallbackDecisionReference='fixture-approval')
            save(root / 'activation.json', {'mode': 'active', 'binding': configuration_binding(config), 'reference': 'fixture-approval'})
            pinned = config['accounts'][1]
            value = sample(100); value.update(email=pinned['email'], accountId=pinned['accountId'])
            state, _ = advance({}, value, pinned, now=10, policy=config['policy'])
            save(root / 'daybreak.json', state)
            def read(account, **kwargs):
                if account['name'] == 'daybreak':
                    raise RuntimeError('fixture_quota_read_failed')
                return sample(0)
            with patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={'email': ACCOUNT['email'], 'accountId': ACCOUNT['accountId']}), \
                 patch('controller.Transport') as transport, patch.dict(os.environ, {}, clear=True), \
                 patch('controller.time.time', return_value=20):
                bridge = transport.return_value.connection.return_value
                bridge.for_recipient.return_value = bridge
                transport.return_value.connection.side_effect = [RuntimeError('fixture_transport_down'), bridge, bridge]
                def send_while_locked(*args, **kwargs):
                    with (root / 'reset.lock').open('r+') as other:
                        with self.assertRaises(BlockingIOError):
                            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
                bridge.send.side_effect = send_while_locked
                run(config, root, once=True)
                run(config, root, once=True)
                self.assertEqual(bridge.send.call_count, 2)
                run(config, root, once=True)
                self.assertEqual(bridge.send.call_count, 2)

    def test_failed_read_does_not_send_fallback_after_reset_attempt(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root)
            config['policy'].update(pauseWhenUnmetered=True,
                fallbackDecision='pause_at_zero_when_unmetered', fallbackDecisionReference='fixture-approval')
            save(root / 'activation.json', {'mode': 'active', 'binding': configuration_binding(config), 'reference': 'fixture'})
            pinned = config['accounts'][1]
            zero = sample(100); zero.update(email=pinned['email'], accountId=pinned['accountId'])
            state, _ = advance({}, zero, pinned, now=10, policy=config['policy'])
            save(root / 'daybreak.json', state)
            save(root / ('reset-' + state['episode']['id'] + '.json'), {'status': 'reset',
                 'request': {'episodeId': state['episode']['id'], 'accountId': pinned['accountId'],
                             'idempotencyKey': state['episode']['resetIdempotencyKey']}})
            def read(account, **kwargs):
                if account['name'] == 'daybreak': raise RuntimeError('fixture_quota_read_failed')
                return sample(0)
            with patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={'email': ACCOUNT['email'], 'accountId': ACCOUNT['accountId']}), \
                 patch('controller.Transport') as transport, patch.dict(os.environ, {}, clear=True), \
                 patch('controller.time.time', return_value=20):
                bridge = transport.return_value.connection.return_value
                bridge.for_recipient.return_value = bridge
                run(config, root, once=True)
                bridge.send.assert_not_called()
                with patch('controller.read_account', side_effect=lambda account, **kw: zero if account['name'] == 'daybreak' else sample(0)):
                    run(config, root, once=True)
                bridge.send.reset_mock()
                run(config, root, once=True)
                self.assertEqual(bridge.send.call_count, 2)

    def test_missing_reset_record_with_custody_marker_blocks_fallback(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root); pinned = config['accounts'][1]
            config['policy'].update(pauseWhenUnmetered=True,
                fallbackDecision='pause_at_zero_when_unmetered', fallbackDecisionReference='fixture-approval')
            save(root / 'activation.json', {'mode': 'active', 'binding': configuration_binding(config), 'reference': 'fixture'})
            zero = sample(100); zero.update(email=pinned['email'], accountId=pinned['accountId'])
            state, _ = advance({}, zero, pinned, now=10, policy=config['policy']); save(root / 'daybreak.json', state)
            episode = state['episode']
            save(root / ('reset-custody-' + episode['id'] + '.json'), {'episodeId': episode['id'],
                 'accountId': pinned['accountId'], 'idempotencyKey': episode['resetIdempotencyKey']})
            def read(account, **kwargs):
                if account['name'] == 'daybreak': raise RuntimeError('fixture_quota_read_failed')
                return sample(0)
            with patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={'email': ACCOUNT['email'], 'accountId': ACCOUNT['accountId']}), \
                 patch('controller.Transport') as transport, patch.dict(os.environ, {}, clear=True):
                bridge = transport.return_value.connection.return_value
                bridge.for_recipient.return_value = bridge
                run(config, root, once=True)
                bridge.send.assert_not_called()

    def test_failed_quota_read_after_reset_attempt_does_not_pause_from_old_zero(self):
        from reset_once import execute
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root); pinned = config['accounts'][1]
            config['policy'].update(pauseWhenUnmetered=True,
                fallbackDecision='pause_at_zero_when_unmetered', fallbackDecisionReference='fixture-approval')
            save(root / 'activation.json', {'mode': 'active', 'binding': configuration_binding(config), 'reference': 'fixture'})
            zero = sample(100); zero.update(email=pinned['email'], accountId=pinned['accountId'])
            state, _ = advance({}, zero, pinned, now=10, policy=config['policy']); save(root / 'daybreak.json', state)
            episode = state['episode']
            request = {'episodeId': episode['id'], 'accountId': pinned['accountId'], 'email': pinned['email'],
                       'idempotencyKey': episode['resetIdempotencyKey'], 'creditId': 'fixture-credit', 'confirmationReference': 'fixture'}
            config['nodeBinary'] = 'node'; config['_configPath'] = '/fixture'
            with patch('reset_once.read_account'), patch('reset_once.prepare', return_value=request), \
                 patch('reset_once.subprocess.run', return_value=SimpleNamespace(returncode=1, stdout='{}')):
                with self.assertRaisesRegex(RuntimeError, 'reset_outcome_unknown'):
                    execute(config, root, 'daybreak', episode['id'], 'fixture-confirmation')
            config.pop('_configPath')
            def read(account, **kwargs):
                if account['name'] == 'daybreak': raise RuntimeError('fixture_quota_read_failed')
                return sample(0)
            with patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={'email': ACCOUNT['email'], 'accountId': ACCOUNT['accountId']}), \
                 patch('controller.Transport') as transport, patch.dict(os.environ, {}, clear=True):
                bridge = transport.return_value.connection.return_value; bridge.for_recipient.return_value = bridge
                run(config, root, once=True)
                bridge.send.assert_not_called()
                with patch('controller.read_account', side_effect=lambda account, **kw: zero if account['name'] == 'daybreak' else sample(0)), \
                     patch('controller.time.time', return_value=20):
                    run(config, root, once=True)
                bridge.send.reset_mock()
                with patch('controller.time.time', return_value=21):
                    run(config, root, once=True)
                self.assertEqual(bridge.send.call_count, 2)

    def test_failed_read_does_not_retry_fallback_after_restored_or_unknown_sample(self):
        for used in (0, None):
            with self.subTest(used=used), tempfile.TemporaryDirectory() as name:
                root = Path(name); config = self.config(root)
                config['policy'].update(pauseWhenUnmetered=True,
                    fallbackDecision='pause_at_zero_when_unmetered', fallbackDecisionReference='fixture-approval')
                save(root / 'activation.json', {'mode': 'active', 'binding': configuration_binding(config), 'reference': 'fixture-approval'})
                pinned = config['accounts'][1]
                zero = sample(100); zero.update(email=pinned['email'], accountId=pinned['accountId'])
                state, _ = advance({}, zero, pinned, now=10, policy=config['policy'])
                state, _ = observation_failed(state, config['policy'])
                later = sample(used); later.update(email=pinned['email'], accountId=pinned['accountId'])
                state, _ = advance(state, later, pinned, now=20, policy=config['policy'])
                save(root / 'daybreak.json', state)
                before = copy.deepcopy(state['episode']['events'])
                def read(account, **kwargs):
                    if account['name'] == 'daybreak':
                        raise RuntimeError('fixture_quota_read_failed')
                    return sample(0)
                with patch('controller.read_account', side_effect=read), \
                     patch('controller.default_identity', return_value={'email': ACCOUNT['email'], 'accountId': ACCOUNT['accountId']}), \
                     patch('controller.Transport') as transport, patch.dict(os.environ, {}, clear=True):
                    bridge = transport.return_value.connection.return_value
                    bridge.for_recipient.return_value = bridge
                    run(config, root, once=True)
                    bridge.send.assert_not_called()
                after = json.loads((root / 'daybreak.json').read_text())
                self.assertTrue(after['episode']['active'])
                self.assertEqual(after['episode']['events'], before)

    def test_fresh_restored_or_unknown_read_vetoes_fallback_when_refresh_fails(self):
        for used in (0, None):
            with self.subTest(used=used), tempfile.TemporaryDirectory() as name:
                root = Path(name); config = self.config(root)
                config['policy'].update(pauseWhenUnmetered=True,
                    fallbackDecision='pause_at_zero_when_unmetered', fallbackDecisionReference='fixture-approval')
                save(root / 'activation.json', {'mode': 'active', 'binding': configuration_binding(config), 'reference': 'fixture-approval'})
                pinned = config['accounts'][1]
                zero = sample(100); zero.update(email=pinned['email'], accountId=pinned['accountId'])
                state, _ = advance({}, zero, pinned, now=10, policy=config['policy'])
                state, _ = observation_failed(state, config['policy']); save(root / 'daybreak.json', state)
                fresh = sample(used); fresh.update(email=pinned['email'], accountId=pinned['accountId'])
                reads = iter((fresh, RuntimeError('fixture_refresh_failed')))
                def read(account, **kwargs):
                    if account['name'] != 'daybreak': return sample(0)
                    value = next(reads)
                    if isinstance(value, Exception): raise value
                    return value
                with patch('controller.read_account', side_effect=read), \
                     patch('controller.default_identity', return_value={'email': ACCOUNT['email'], 'accountId': ACCOUNT['accountId']}), \
                     patch('controller.Transport') as transport, patch.dict(os.environ, {}, clear=True):
                    bridge = transport.return_value.connection.return_value
                    bridge.for_recipient.return_value = bridge
                    run(config, root, once=True)
                    bridge.send.assert_not_called()

    def test_matching_default_restored_or_unknown_veto_survives_failed_refresh(self):
        for used in (0, None):
            with self.subTest(used=used), tempfile.TemporaryDirectory() as name:
                root = Path(name); config = self.config(root); pinned = config['accounts'][1]
                config['policy'].update(pauseWhenUnmetered=True,
                    fallbackDecision='pause_at_zero_when_unmetered', fallbackDecisionReference='fixture-approval')
                save(root / 'activation.json', {'mode': 'active', 'binding': configuration_binding(config), 'reference': 'fixture'})
                zero = sample(100); zero.update(email=pinned['email'], accountId=pinned['accountId'])
                state, _ = advance({}, zero, pinned, now=10, policy=config['policy'])
                state, _ = observation_failed(state, config['policy']); save(root / 'daybreak.json', state)
                fresh = sample(used); fresh.update(email=pinned['email'], accountId=pinned['accountId'])
                reads = {'main': iter((fresh, RuntimeError('default_failed'))),
                         'daybreak': iter((zero, RuntimeError('pinned_failed')))}
                def read(account, **kwargs):
                    value = next(reads[account['name']])
                    if isinstance(value, Exception): raise value
                    return value
                with patch('controller.read_account', side_effect=read), \
                     patch('controller.default_identity', return_value={'email': pinned['email'], 'accountId': pinned['accountId']}), \
                     patch('controller.Transport') as transport, patch.dict(os.environ, {}, clear=True):
                    bridge = transport.return_value.connection.return_value
                    bridge.for_recipient.return_value = bridge
                    run(config, root, once=True)
                    bridge.send.assert_not_called()
                    persisted = json.loads((root / 'daybreak.json').read_text())
                    self.assertEqual(persisted['lastSample'], fresh)
                    self.assertTrue(persisted['episode']['active'])

    def test_failed_read_does_not_replay_uncertain_fallback(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root)
            config['policy'].update(pauseWhenUnmetered=True,
                fallbackDecision='pause_at_zero_when_unmetered', fallbackDecisionReference='fixture-approval')
            save(root / 'activation.json', {'mode': 'active', 'binding': configuration_binding(config), 'reference': 'fixture-approval'})
            pinned = config['accounts'][1]
            value = sample(100); value.update(email=pinned['email'], accountId=pinned['accountId'])
            state, _ = advance({}, value, pinned, now=10, policy=config['policy'])
            save(root / 'daybreak.json', state)
            def read(account, **kwargs):
                if account['name'] == 'daybreak':
                    raise RuntimeError('fixture_quota_read_failed')
                return sample(0)
            with patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={'email': ACCOUNT['email'], 'accountId': ACCOUNT['accountId']}), \
                 patch('controller.Transport') as transport, patch.dict(os.environ, {}, clear=True), \
                 patch('controller.time.time', return_value=20):
                bridge = transport.return_value.connection.return_value
                bridge.for_recipient.return_value = bridge
                bridge.send.side_effect = TimeoutError('fixture_uncertain')
                run(config, root, once=True)
                first_count = bridge.send.call_count
                self.assertGreater(first_count, 0)
                run(config, root, once=True)
                self.assertEqual(bridge.send.call_count, first_count)

    def config(self, root):
        config = json.loads(Path(__file__).with_name('config.example.json').read_text())
        config.update(stateDir=str(root), sharedLock=str(root / 'legacy.lock'), enabled=True)
        (root / 'legacy.lock').touch()
        (root / 'legacy.lock').chmod(0o600)
        (root / 'reset.lock').touch(mode=0o600)
        save(root / 'activation.json', {'mode': 'active', 'binding': configuration_binding(config), 'reference': 'fixture-approval'})
        return config

    def fixture_poll(self, root, config, read, bridge, *, now=30, observe_only=False):
        with patch('controller.read_account', side_effect=read), \
             patch('controller.default_identity', return_value={k: ACCOUNT[k] for k in ('email', 'accountId')}), \
             patch('controller.Transport') as transport, patch.dict(os.environ, {}, clear=True), \
             patch('controller.time.time', return_value=now):
            transport.return_value.connection.return_value = bridge
            return run(config, root, once=True, observe_only=observe_only)

    def fixture_restored_preflight(self, root, config):
        pinned = config['accounts'][1]
        def restored(account, **kwargs):
            value = sample(50)
            if account['name'] == 'daybreak':
                value.update({k: pinned[k] for k in ('email', 'accountId')})
            return value
        with patch('preflight.read_account', side_effect=restored), \
             patch('preflight.socket_identity'), patch('preflight.NativeBridge') as native, \
             patch.dict(os.environ, {'CODEX_APP_TOOLS_PIPE_PATH': '/fixture/route.sock'}, clear=True):
            native.return_value.call.side_effect = lambda tool, arguments: {
                'thread': {'id': arguments['threadId'], 'title': 'Codex Foreman'}}
            return preflight(config, root, check_live=True)

    def test_failed_read_fallback_does_not_send_unrelated_pending_confirmation(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root); pinned = config['accounts'][1]
            config['policy'].update(pauseWhenUnmetered=True,
                fallbackDecision='pause_at_zero_when_unmetered', fallbackDecisionReference='fixture-approval')
            save(root / 'activation.json', {'mode': 'active', 'binding': configuration_binding(config), 'reference': 'fixture-approval'})
            unknown = sample(100); unknown.update(email=pinned['email'], accountId=pinned['accountId'])
            unknown['rate_limit_reset_credits'] = {}
            state, _ = advance({}, unknown, pinned, now=10, policy=config['policy'])
            zero = sample(100); zero.update(email=pinned['email'], accountId=pinned['accountId'])
            state, _ = advance(state, zero, pinned, now=20, policy=config['policy'])
            episode = copy.deepcopy(state['episode'])
            save(root / 'daybreak.json', state)
            bridge = ReceiptTransport(root / 'daybreak.json')
            def read(account, **kwargs):
                if account['name'] == 'daybreak': raise RuntimeError('fixture_quota_read_failed')
                return sample(0)
            with patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={k: ACCOUNT[k] for k in ('email', 'accountId')}), \
                 patch('controller.Transport') as transport, patch.dict(os.environ, {}, clear=True), \
                 patch('controller.time.time', return_value=30):
                transport.return_value.connection.return_value = bridge
                run(config, root, once=True)
            self.assertEqual([target for target, _ in bridge.received],
                             [config['foremanThreadId'], config['parentThreadId']])
            retained = json.loads((root / 'daybreak.json').read_text())
            unknown_event, confirmation, pause = retained['episode']['events']
            self.assertEqual(pause['reason'], 'observation_failed_during_confirmed_exhaustion')
            self.assertEqual(unknown_event['deliveries']['parent']['coalescedInto'], pause['id'])
            self.assertFalse(confirmation.get('deliveries'))
            self.assertEqual(retained['lastSample'], zero)
            for key in ('id', 'resetIdempotencyKey', 'accountId', 'email', 'resetCount'):
                self.assertEqual(retained['episode'][key], episode[key])

    def test_failed_poll_without_fallback_preserves_backlog_after_restored_preflight(self):
        for check_preflight in (False, True):
            with self.subTest(check_preflight=check_preflight), tempfile.TemporaryDirectory() as name:
                root = Path(name); config = self.config(root); pinned = config['accounts'][1]
                zero = sample(100); zero.update({k: pinned[k] for k in ('email', 'accountId')})
                state, _ = advance({}, zero, pinned, now=10, policy=config['policy'])
                save(root / 'daybreak.json', state)
                before = (root / 'daybreak.json').read_bytes()
                if check_preflight:
                    report = self.fixture_restored_preflight(root, config)
                    self.assertEqual(report['liveChecks'], 'both_accounts_and_exact_destinations_verified')
                    self.assertEqual((root / 'daybreak.json').read_bytes(), before)
                def failed(account, **kwargs):
                    if account['name'] == 'daybreak': raise RuntimeError('fixture_quota_read_failed')
                    return sample(0)
                bridge = ReceiptTransport(root / 'daybreak.json')
                for _ in range(2):
                    health = self.fixture_poll(root, config, failed, bridge)
                    self.assertEqual(health['accounts']['daybreak']['observationStatus'], 'unavailable')
                    self.assertEqual((root / 'daybreak.json').read_bytes(), before)
                self.assertEqual(bridge.received, [])

    def test_restored_or_unknown_current_poll_blocks_pending_pause_and_confirmation(self):
        for used, budget in ((50, 'allowance_restored_pending_rearm'), (None, 'quota_unknown_wait_blocked')):
            with self.subTest(used=used), tempfile.TemporaryDirectory() as name:
                root = Path(name); config = self.config(root); pinned = config['accounts'][1]
                zero = sample(100); zero.update({k: pinned[k] for k in ('email', 'accountId')})
                state, _ = advance({}, zero, pinned, now=10, policy=config['policy'])
                spent = copy.deepcopy(zero); spent['credits']['balance'] = '790'
                state, _ = advance(state, spent, pinned, now=20, policy=config['policy'])
                episode = copy.deepcopy(state['episode'])
                self.assertEqual([event['kind'] for event in episode['events']],
                                 ['reset_confirmation_required', 'foreman_pause_required'])
                save(root / 'daybreak.json', state)
                current = sample(used); current.update({k: pinned[k] for k in ('email', 'accountId')})
                def read(account, **kwargs):
                    return current if account['name'] == 'daybreak' else sample(0)
                bridge = ReceiptTransport(root / 'daybreak.json')
                health = self.fixture_poll(root, config, read, bridge)
                self.assertEqual(health['accounts']['daybreak']['observationStatus'], 'healthy')
                self.assertIn('current_zero_required_before_delivery', health['accounts']['daybreak']['reason'])
                self.assertEqual(bridge.received, [])
                retained = json.loads((root / 'daybreak.json').read_text())
                self.assertEqual(retained['lastSample'], current)
                self.assertEqual(retained['episode']['budgetStatus'], budget)
                self.assertEqual(retained['episode']['events'], episode['events'])
                self.assertTrue(retained['episode']['active'])
                for key in ('id', 'resetIdempotencyKey', 'accountId', 'email', 'resetCount'):
                    self.assertEqual(retained['episode'][key], episode[key])

    def test_failed_poll_fallback_keeps_recipient_outages_independent_without_backlog_sends(self):
        for unavailable in ('foreman', 'parent'):
            with self.subTest(unavailable=unavailable), tempfile.TemporaryDirectory() as name:
                root = Path(name); config = self.config(root); pinned = config['accounts'][1]
                config['policy'].update(pauseWhenUnmetered=True,
                    fallbackDecision='pause_at_zero_when_unmetered', fallbackDecisionReference='fixture-approval')
                save(root / 'activation.json', {'mode': 'active', 'binding': configuration_binding(config), 'reference': 'fixture-approval'})
                unknown = sample(100); unknown.update({k: pinned[k] for k in ('email', 'accountId')})
                unknown['rate_limit_reset_credits'] = {}
                state, _ = advance({}, unknown, pinned, now=10, policy=config['policy'])
                zero = sample(100); zero.update({k: pinned[k] for k in ('email', 'accountId')})
                state, _ = advance(state, zero, pinned, now=20, policy=config['policy'])
                save(root / 'daybreak.json', state)
                bridge = ReceiptTransport(root / 'daybreak.json')
                bridge.for_recipient = lambda key: None if key == unavailable else bridge
                def failed(account, **kwargs):
                    if account['name'] == 'daybreak': raise RuntimeError('fixture_quota_read_failed')
                    return sample(0)
                before = (root / 'daybreak.json').read_bytes()
                report = self.fixture_restored_preflight(root, config)
                self.assertEqual(report['liveChecks'], 'both_accounts_and_exact_destinations_verified')
                self.assertEqual((root / 'daybreak.json').read_bytes(), before)
                for _ in range(2):
                    self.fixture_poll(root, config, failed, bridge)
                available = 'parent' if unavailable == 'foreman' else 'foreman'
                self.assertEqual([target for target, _ in bridge.received], [config[available + 'ThreadId']])
                retained = json.loads((root / 'daybreak.json').read_text())
                unknown_event, confirmation, pause = retained['episode']['events']
                self.assertEqual(pause['reason'], 'observation_failed_during_confirmed_exhaustion')
                self.assertEqual(set(pause['deliveries']), {available})
                self.assertEqual(pause['deliveries'][available]['status'], 'submitted')
                self.assertFalse(confirmation.get('deliveries'))
                if available == 'parent':
                    self.assertEqual(unknown_event['deliveries']['parent']['coalescedInto'], pause['id'])
                else:
                    self.assertFalse(unknown_event.get('deliveries'))
                self.assertEqual(retained['lastSample'], zero)

    def test_failed_main_poll_preserves_incoherent_retained_identity_without_fallback_sends(self):
        for retained_pause in (False, True):
            for changed in ('generation', 'accountId', 'email', 'missing_generation',
                            'sample_accountId', 'sample_email'):
                for custody_present in (False, True):
                    with self.subTest(retained_pause=retained_pause, changed=changed,
                                      custody_present=custody_present), tempfile.TemporaryDirectory() as name:
                        root = Path(name); config = self.config(root); pinned = config['accounts'][1]
                        config['policy'].update(pauseWhenUnmetered=True,
                            fallbackDecision='pause_at_zero_when_unmetered', fallbackDecisionReference='fixture-approval')
                        save(root / 'activation.json', {'mode': 'active', 'binding': configuration_binding(config),
                                                       'reference': 'fixture-approval'})
                        state, _ = advance({}, sample(100), ACCOUNT, now=10, policy=config['policy'])
                        state['identityGeneration'] = 'fixture-generation'
                        state['episode']['events'][0]['deliveries'] = {
                            'parent': {'status': 'outcome_unknown', 'target': config['parentThreadId'], 'at': 11}}
                        if retained_pause:
                            state, _ = observation_failed(state, config['policy'])
                            self.assertEqual(state['episode']['events'][-1]['reason'],
                                             'observation_failed_during_confirmed_exhaustion')
                            self.assertFalse(state['episode']['events'][-1].get('deliveries'))
                        if changed == 'generation':
                            state['identityGeneration'] = 'different-generation'
                        elif changed == 'missing_generation':
                            del state['identityGeneration']
                        elif changed.startswith('sample_'):
                            state['lastSample'][changed.removeprefix('sample_')] = 'different'
                        else:
                            state['episode'][changed] = 'different'
                            state['lastSample'][changed] = 'different'
                            if changed == 'accountId':
                                state['accountId'] = 'different'
                        save(root / 'main.json', state)
                        save(root / 'main-selection.json', {'generation': 'fixture-generation',
                             'accountId': ACCOUNT['accountId'], 'email': ACCOUNT['email'],
                             'observedAt': sample(100)['observedAt']})
                        if custody_present:
                            save(root / 'account-custody-main.json', {'account': 'main', 'initialized': True})
                        evidence_paths = ('main.json', 'main-selection.json', 'account-custody-main.json')
                        before = {path: (root / path).read_bytes() for path in evidence_paths if (root / path).exists()}
                        bridge = ReceiptTransport(root / 'main.json')
                        def failed_main(account, **kwargs):
                            if account['name'] == 'main': raise RuntimeError('fixture_quota_read_failed')
                            value = sample(0); value.update({k: pinned[k] for k in ('email', 'accountId')})
                            return value
                        for _ in range(2):
                            health = self.fixture_poll(root, config, failed_main, bridge, now=20)
                            self.assertEqual(health['accounts']['main']['observationStatus'], 'unavailable')
                            self.assertEqual({path: (root / path).read_bytes() for path in evidence_paths
                                              if (root / path).exists()}, before)
                            self.assertFalse((root / 'account-history').exists())
                            self.assertEqual(bridge.received, [])

    def test_failed_poll_fallback_preserves_main_receipts_and_generation_across_restart(self):
        for receipt_status in ('submitted', 'attempting', 'outcome_unknown'):
            with self.subTest(receipt_status=receipt_status), tempfile.TemporaryDirectory() as name:
                root = Path(name); config = self.config(root); pinned = config['accounts'][1]
                config['policy'].update(pauseWhenUnmetered=True,
                    fallbackDecision='pause_at_zero_when_unmetered', fallbackDecisionReference='fixture-approval')
                save(root / 'activation.json', {'mode': 'active', 'binding': configuration_binding(config), 'reference': 'fixture-approval'})
                def exhausted_main(account, **kwargs):
                    if account['name'] == 'main': return sample(100)
                    value = sample(0); value.update({k: pinned[k] for k in ('email', 'accountId')})
                    return value
                bridge = ReceiptTransport(root / 'main.json')
                self.fixture_poll(root, config, exhausted_main, bridge, now=10, observe_only=True)
                state = json.loads((root / 'main.json').read_text())
                receipt = {'status': receipt_status, 'target': config['parentThreadId'], 'at': 11}
                state['episode']['events'][0]['deliveries'] = {'parent': receipt}
                save(root / 'main.json', state)
                episode = copy.deepcopy(state['episode'])
                generation = state['identityGeneration']
                selection = (root / 'main-selection.json').read_bytes()
                def failed_main(account, **kwargs):
                    if account['name'] == 'main': raise RuntimeError('fixture_quota_read_failed')
                    return exhausted_main(account, **kwargs)
                for _ in range(2):
                    self.fixture_poll(root, config, failed_main, bridge, now=20)
                self.fixture_poll(root, config, exhausted_main, bridge, now=30)
                self.assertEqual([target for target, _ in bridge.received],
                                 [config['foremanThreadId'], config['parentThreadId']])
                retained = json.loads((root / 'main.json').read_text())
                confirmation, pause = retained['episode']['events']
                self.assertEqual(confirmation['deliveries']['parent'], receipt)
                self.assertEqual(pause['reason'], 'observation_failed_during_confirmed_exhaustion')
                self.assertEqual({key: delivery['status'] for key, delivery in pause['deliveries'].items()},
                                 {'foreman': 'submitted', 'parent': 'submitted'})
                self.assertEqual(retained['lastSample'], sample(100))
                self.assertEqual(retained['identityGeneration'], generation)
                self.assertEqual((root / 'main-selection.json').read_bytes(), selection)
                for key in ('id', 'resetIdempotencyKey', 'accountId', 'email', 'resetCount'):
                    self.assertEqual(retained['episode'][key], episode[key])

    def test_companion_has_no_action_tools_and_never_fabricates_a_route(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root)
            requests = [{'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2024-11-05'}},
                        {'id': 2, 'method': 'tools/list'},
                        {'id': 3, 'method': 'tools/call', 'params': {'name': 'send_message_to_thread'}}]
            incoming = io.StringIO(''.join(json.dumps(r) + '\n' for r in requests)); outgoing = io.StringIO()
            serve(config, root, incoming, outgoing, {})
            output = [json.loads(line) for line in outgoing.getvalue().splitlines()]
            self.assertEqual(output[1]['result']['tools'], [])
            self.assertIn('error', output[2])
            self.assertFalse((root / 'transport').exists())

    def test_companion_survives_bad_frames_and_initialization_without_publishing(self):
        outgoing = io.StringIO()
        requests = '\nnot-json\n[]\n42\nnull\n' + '\n'.join(json.dumps(r) for r in [
            {'id': 1, 'method': 'initialize'},
            {'id': 2, 'method': 'initialize', 'params': None},
            {'id': 3, 'method': 'ping'},
            {'id': 4, 'method': 'initialize', 'params': {'protocolVersion': '2024-11-05'}},
            {'id': 5, 'method': 'tools/list'}])
        with patch('companion.publish_candidate') as publish:
            serve({'enabled': True}, '/unused-fixture', io.StringIO(requests), outgoing, {})
            publish.assert_called_once()
        replies = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertEqual([r['id'] for r in replies], [1, 2, 3, 4, 5])
        self.assertEqual([r['error']['code'] for r in replies[:2]], [-32602, -32602])
        self.assertEqual(replies[-1]['result'], {'tools': []})

    def test_config_binds_existing_paths_and_rejects_unprotected_registration(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root); path = root / 'config.json'
            save(path, config)
            self.assertEqual(load_config(path, root)['nativeContext'], config['nativeContext'])
            with self.assertRaisesRegex(ValueError, 'directory_mismatch'): load_config(path, root / 'new')
            path.chmod(0o644)
            with self.assertRaisesRegex(ValueError, 'private_owned_file'): load_config(path, root)

    def test_transport_outage_still_records_zero_and_budget_without_fake_receipts(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root)
            pinned = config['accounts'][1]
            def read(account, **kwargs):
                value = sample(100, resets=1)
                if account['name'] == 'daybreak': value.update(email=pinned['email'], accountId=pinned['accountId'])
                return value
            with patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={'email': ACCOUNT['email'], 'accountId': ACCOUNT['accountId']}), \
                 patch('controller.Transport') as transport, patch.dict(os.environ, {}, clear=True):
                transport.return_value.connection.side_effect = RuntimeError('fixture_transport_down')
                health = run(config, root, once=True)
                self.assertEqual(health['transport'], 'unavailable')
                for account in ('main', 'daybreak'):
                    episode = json.loads((root / f'{account}.json').read_text())['episode']
                    self.assertEqual(episode['budgetStatus'], 'estimated_from_balance_debits')
                    self.assertNotIn('deliveries', episode['events'][0])

    def test_reconnected_transport_never_replays_unknown_delivery(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'main.json'
            state, _ = advance({}, sample(100, resets=0), ACCOUNT, now=10)
            failing = ReceiptTransport(path, fail='foreman')
            with self.assertRaises(RuntimeError): dispatch(state, ACCOUNT, CONFIG, failing, path, current_observation=sample(100))
            replacement = ReceiptTransport(path)
            with self.assertRaises(RuntimeError):
                dispatch(json.loads(path.read_text()), ACCOUNT, CONFIG, replacement, path, current_observation=sample(100))
            self.assertEqual(replacement.received, [])

    def test_total_reset_inventory_is_not_applicability_evidence(self):
        value = sample(100)
        value['rate_limit_reset_credits'] = {'available_count': 2}
        state, events = advance({}, value, ACCOUNT, now=10)
        self.assertIsNone(state['episode']['resetCount'])
        self.assertEqual(events[0]['kind'], 'reset_availability_unknown')

    def test_main_unknown_applicability_does_not_start_confirmation_wait_budget(self):
        policy = {'usdPerCredit': '0.04', 'pauseAtUsd': '8'}
        value = sample(100); value['rate_limit_reset_credits'] = {}
        state, _ = advance({}, value, ACCOUNT, now=10, policy=policy)
        value['credits']['balance'] = '500'
        state, events = advance(state, value, ACCOUNT, now=20, policy=policy)
        self.assertEqual(events, [])
        self.assertNotIn('waitingSpendUsd', state['episode'])

    def test_reset_arriving_after_submitted_no_reset_pause_requests_confirmation(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'main.json'; transport = ReceiptTransport(path)
            state, _ = advance({}, sample(100, resets=0), ACCOUNT, now=10)
            dispatch(state, ACCOUNT, CONFIG, transport, path, current_observation=sample(100))
            state, events = advance(state, sample(100, resets=1), ACCOUNT, now=20)
            self.assertEqual([e['kind'] for e in events], ['reset_confirmation_required'])
            dispatch(state, ACCOUNT, CONFIG, transport, path, current_observation=sample(100))
            self.assertEqual([target for target, _ in transport.received], ['foreman', 'parent', 'parent'])

    def test_new_pause_does_not_overwrite_uncertain_confirmation_receipt(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'main.json'
            policy = {'usdPerCredit': '0.04', 'pauseAtUsd': '8'}
            state, _ = advance({}, sample(100), ACCOUNT, now=10, policy=policy)
            with self.assertRaises(RuntimeError):
                dispatch(state, ACCOUNT, CONFIG, ReceiptTransport(path, fail='parent'), path, current_observation=sample(100))
            old_receipt = copy.deepcopy(state['episode']['events'][0]['deliveries']['parent'])
            state, _ = advance(state, sample(100, balance='800'), ACCOUNT, now=20, policy=policy)
            with self.assertRaises(RuntimeError): dispatch(state, ACCOUNT, CONFIG, ReceiptTransport(path), path, current_observation=sample(100))
            self.assertEqual(state['episode']['events'][0]['deliveries']['parent'], old_receipt)

    def test_daybreak_no_reset_outage_cannot_start_unapproved_wait(self):
        from safeguard import observation_failed
        state, _ = advance({}, sample(100, resets=0), dict(ACCOUNT, name='daybreak'), now=10)
        _, events = observation_failed(state, {'pauseWhenUnmetered': True})
        self.assertEqual(events, [])

    def test_preflight_config_rejects_missing_state_without_creating_it(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root)
            config['stateDir'] = str(root / 'missing'); path = root / 'config.json'; save(path, config)
            with self.assertRaises((ValueError, FileNotFoundError)): load_config(path, root / 'missing')
            self.assertFalse((root / 'missing').exists())

    def test_config_rejects_state_anywhere_inside_release_archive(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root)
            config['stateDir'] = str(Path(__file__).resolve().parents[2] / 'private-state')
            path = root / 'config.json'; save(path, config)
            with self.assertRaisesRegex(ValueError, 'outside_release'):
                load_config(path, config['stateDir'])

    def test_status_is_read_only_and_distinguishes_stale_health(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root); path = root / 'config.json'; save(path, config)
            save(root / 'health.json', {'observedAt': 'fixture', 'accounts': {}})
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            self.assertFalse(status(path, root, now=(root / 'health.json').stat().st_mtime + 121)['observerFresh'])
            self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, before)

    def test_active_predecessor_main_requires_identity_adoption_without_losing_receipts(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root); pinned = config['accounts'][1]
            state, _ = advance({}, sample(100, resets=0), ACCOUNT, now=10)
            state['episode']['events'][0]['deliveries'] = {
                'foreman': {'status': 'outcome_unknown', 'target': config['foremanThreadId']}}
            save(root / 'main.json', state)
            before = (root / 'main.json').read_bytes()
            with self.assertRaisesRegex(ValueError, 'main_selection_adoption_required'):
                preflight(config, root)
            def reading(account, **kwargs):
                value = sample(0 if account['name'] == 'daybreak' else 100)
                if account['name'] == 'daybreak':
                    value.update(email=pinned['email'], accountId=pinned['accountId'])
                return value
            bridge = ReceiptTransport(root / 'main.json')
            health = self.fixture_poll(root, config, reading, bridge)
            self.assertEqual(health['accounts']['main']['reason'], 'main_selection_adoption_required')
            self.assertEqual((root / 'main.json').read_bytes(), before)
            self.assertFalse((root / 'main-selection.json').exists())
            self.assertFalse((root / 'account-history').exists())
            self.assertFalse(any(target == config['foremanThreadId'] for target, _ in bridge.received))

    def test_preflight_requires_stopped_observer_without_changing_state(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root)
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            with open(config['sharedLock']) as predecessor:
                fcntl.flock(predecessor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaisesRegex(ValueError, 'observer_stop_required_for_preflight'):
                    preflight(config, root)
            self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, before)
            self.assertEqual(preflight(config, root)['status'], 'local_checks_passed')

    def test_preflight_rejects_incoherent_main_selection_without_writes(self):
        for changed in ('generation', 'accountId', 'email', 'missing_selector', 'missing_generation'):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as name:
                root = Path(name); config = self.config(root)
                state, _ = advance({}, sample(100, resets=0), ACCOUNT, now=10)
                state['identityGeneration'] = 'fixture-generation'
                selected = {'generation': 'fixture-generation', 'accountId': ACCOUNT['accountId'],
                            'email': ACCOUNT['email'], 'observedAt': sample(100)['observedAt']}
                if changed in selected:
                    selected[changed] = 'different'
                if changed == 'missing_generation':
                    del state['identityGeneration']
                save(root / 'main.json', state)
                if changed != 'missing_selector':
                    save(root / 'main-selection.json', selected)
                before = {p.name: p.read_bytes() for p in root.iterdir()}
                with self.assertRaisesRegex(ValueError, 'main_selection_state_mismatch'):
                    preflight(config, root)
                self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, before)

    def test_activation_can_preflight_stopped_state_then_same_observer_starts(self):
        from activation import set_mode, delivery_status
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root); pinned = config['accounts'][1]
            state, _ = advance({}, sample(100, resets=0), ACCOUNT, now=10)
            state['episode']['events'][0]['deliveries'] = {
                'foreman': {'status': 'outcome_unknown', 'target': config['foremanThreadId']}}
            state['identityGeneration'] = 'fixture-generation'
            save(root / 'main.json', state)
            save(root / 'main-selection.json', {'generation': 'fixture-generation',
                 'accountId': ACCOUNT['accountId'], 'email': ACCOUNT['email'],
                 'observedAt': sample(100)['observedAt']})
            before = (root / 'main.json').read_bytes()
            def reading(account, **kwargs):
                value = sample(0)
                if account['name'] == 'daybreak':
                    value.update(email=pinned['email'], accountId=pinned['accountId'])
                return value
            with patch('preflight.read_account', side_effect=reading), \
                 patch('preflight.socket_identity'), patch('preflight.NativeBridge'), \
                 patch('preflight.verify_bridge'), \
                 patch.dict(os.environ, {'CODEX_APP_TOOLS_PIPE_PATH': '/fixture'}):
                set_mode(config, root, 'active', 'fixture-cutover-approval')
            self.assertEqual(delivery_status(config, root)['mode'], 'active')
            self.assertEqual((root / 'main.json').read_bytes(), before)
            bridge = ReceiptTransport(root / 'main.json')
            health = self.fixture_poll(root, config, reading, bridge)
            self.assertEqual(health['accounts']['main']['observationStatus'], 'healthy')
            self.assertEqual(health['accounts']['daybreak']['observationStatus'], 'healthy')
            self.assertEqual(bridge.received, [])
            retained = json.loads((root / 'main.json').read_text())
            self.assertEqual(retained['episode']['events'][0]['deliveries'],
                             state['episode']['events'][0]['deliveries'])

    def test_release_preflight_preserves_active_ledger_and_unknown_receipt(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root)
            state, _ = advance({}, sample(100, resets=0), ACCOUNT, now=10)
            state['episode']['events'][0]['deliveries'] = {'foreman': {'status': 'outcome_unknown'}}
            state['identityGeneration'] = 'fixture-generation'
            save(root / 'main.json', state)
            save(root / 'main-selection.json', {'generation': 'fixture-generation',
                 'accountId': ACCOUNT['accountId'], 'email': ACCOUNT['email'],
                 'observedAt': sample(100)['observedAt']})
            (root / 'unrelated-observation.json').write_text('{}')
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            report = preflight(config, root)
            self.assertEqual(report['liveChecks'], 'not_requested')
            self.assertEqual(set(report['preservedLedgerHashes']), {'main.json', 'main-selection.json'})
            self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, before)

    def test_preflight_rejects_nonprivate_shared_lock(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root)
            (root / 'legacy.lock').chmod(0o644)
            with self.assertRaises(ValueError):
                preflight(config, root)

    def test_preflight_includes_and_checks_account_custody(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root); pinned = config['accounts'][1]
            reading = sample(0); reading.update(email=pinned['email'], accountId=pinned['accountId'])
            state, _ = advance({}, reading, pinned, now=10, policy=config['policy'])
            save(root / 'daybreak.json', state)
            save(root / 'account-custody-daybreak.json', {'account': 'daybreak', 'initialized': True})
            report = preflight(config, root)
            self.assertIn('account-custody-daybreak.json', report['preservedLedgerHashes'])
            (root / 'daybreak.json').unlink()
            with self.assertRaisesRegex(ValueError, 'account_state_reconciliation_required'):
                preflight(config, root)

    def test_preflight_rejects_altered_reset_record_bound_to_closed_history(self):
        with tempfile.TemporaryDirectory(prefix='historical-reset-') as name:
            root = Path(name); config = self.config(root); pinned = config['accounts'][1]
            episode_id = '11111111-1111-4111-8111-111111111111'
            reset_key = '22222222-2222-4222-8222-222222222222'
            request = {'episodeId': episode_id, 'email': pinned['email'],
                       'accountId': pinned['accountId'], 'idempotencyKey': reset_key,
                       'creditId': 'predecessor-credit', 'confirmationReference': 'fixture-confirmation',
                       'preparedAt': '2026-10-01T00:00:00Z'}
            record = {'request': request, 'status': 'reset', 'response': {'code': 'reset'}}
            save(root / 'daybreak.json', {'accountId': pinned['accountId']})
            save(root / ('reset-' + episode_id + '.json'), record)
            save(root / ('reset-custody-' + episode_id + '.json'), {
                 'episodeId': episode_id, 'accountId': pinned['accountId'], 'idempotencyKey': reset_key})
            save(root / ('closed-' + episode_id + '.json'), {
                 'id': episode_id, 'active': False, 'accountId': pinned['accountId'],
                 'email': pinned['email'], 'resetIdempotencyKey': reset_key, 'events': [],
                 'resetEvidence': '0bcde9a83d218aea4e0cfae79644044dfafe57203ed3feb9d4087cac67cd8159'})
            self.assertEqual(preflight(config, root, check_live=False)['status'], 'local_checks_passed')
            record.update(status='already_redeemed', response={'code': 'already_redeemed'})
            save(root / ('reset-' + episode_id + '.json'), record)
            with self.assertRaisesRegex(ValueError, 'reset_reconciliation_required'):
                preflight(config, root, check_live=False)

    def test_preflight_rejects_deleted_reset_record_and_marker_when_closed_digest_survives(self):
        with tempfile.TemporaryDirectory(prefix='deleted-historical-reset-') as name:
            root = Path(name); config = self.config(root); pinned = config['accounts'][1]
            episode_id = '11111111-1111-4111-8111-111111111111'
            reset_key = '22222222-2222-4222-8222-222222222222'
            request = {'episodeId': episode_id, 'email': pinned['email'],
                       'accountId': pinned['accountId'], 'idempotencyKey': reset_key,
                       'creditId': 'predecessor-credit', 'confirmationReference': 'fixture-confirmation',
                       'preparedAt': '2026-10-01T00:00:00Z'}
            save(root / 'daybreak.json', {'accountId': pinned['accountId']})
            save(root / ('reset-' + episode_id + '.json'), {
                 'request': request, 'status': 'reset', 'response': {'code': 'reset'}})
            save(root / ('reset-custody-' + episode_id + '.json'), {
                 'episodeId': episode_id, 'accountId': pinned['accountId'], 'idempotencyKey': reset_key})
            save(root / ('closed-' + episode_id + '.json'), {
                 'id': episode_id, 'active': False, 'accountId': pinned['accountId'],
                 'email': pinned['email'], 'resetIdempotencyKey': reset_key, 'events': [],
                 'resetEvidence': '0bcde9a83d218aea4e0cfae79644044dfafe57203ed3feb9d4087cac67cd8159'})
            self.assertEqual(preflight(config, root, check_live=False)['status'], 'local_checks_passed')
            (root / ('reset-' + episode_id + '.json')).unlink()
            (root / ('reset-custody-' + episode_id + '.json')).unlink()
            with self.assertRaisesRegex(ValueError, 'reset_reconciliation_required'):
                preflight(config, root, check_live=False)

    def test_preflight_distinguishes_no_reset_history_from_digest_without_episode_id(self):
        with tempfile.TemporaryDirectory(prefix='malformed-closed-reset-') as name:
            root = Path(name); config = self.config(root); pinned = config['accounts'][1]
            episode_id = '11111111-1111-4111-8111-111111111111'
            closed_path = root / ('closed-' + episode_id + '.json')
            closed = {'id': episode_id, 'active': False, 'accountId': pinned['accountId'],
                      'email': pinned['email'], 'resetIdempotencyKey': 'fixture-no-reset-key',
                      'events': [], 'resetEvidence': None}
            save(root / 'daybreak.json', {'accountId': pinned['accountId']})
            save(closed_path, closed)
            self.assertEqual(preflight(config, root, check_live=False)['status'], 'local_checks_passed')
            del closed['id']
            closed['resetEvidence'] = '0bcde9a83d218aea4e0cfae79644044dfafe57203ed3feb9d4087cac67cd8159'
            save(closed_path, closed)
            with self.assertRaisesRegex(ValueError, 'reset_reconciliation_required'):
                preflight(config, root, check_live=False)

    def test_preflight_rejects_orphaned_reset_custody_without_writes(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root)
            (root / 'reset.lock').touch(mode=0o600)
            pinned = config['accounts'][1]; reading = sample(0)
            reading.update(email=pinned['email'], accountId=pinned['accountId'])
            state, _ = advance({}, reading, pinned, now=10, policy=config['policy'])
            save(root / 'daybreak.json', state)
            save(root / 'reset-custody-00000000-0000-0000-0000-000000000001.json',
                 {'episodeId': '00000000-0000-0000-0000-000000000001', 'accountId': 'fixture', 'idempotencyKey': 'fixture'})
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            with self.assertRaisesRegex(ValueError, 'reset_reconciliation_required'):
                preflight(config, root)
            self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, before)


if __name__ == '__main__': unittest.main()
