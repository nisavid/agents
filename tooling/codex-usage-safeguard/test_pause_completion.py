"""Completion CLI and observer boundaries use only disposable private state."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from storage import save
from safeguard import advance
from test_guard import ACCOUNT, sample

PACKAGE = Path(__file__).resolve().parent


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = json.loads((PACKAGE / 'config.example.json').read_text())
        self.config.update(stateDir=str(self.root), sharedLock=str(self.root / 'legacy.lock'))
        self.config_path = self.root / 'config.json'
        save(self.config_path, self.config)
        self.state, _ = advance({}, sample(100, resets=0), ACCOUNT, now=10)
        self.state.update(identityGeneration='generation-a')
        save(self.root / 'main.json', self.state)
        save(self.root / 'main-selection.json', {
            'generation': 'generation-a',
            'accountId': ACCOUNT['accountId'], 'email': ACCOUNT['email']})
        self.record = {
            'schemaVersion': 1, 'episodeId': self.state['episode']['id'],
            'eventId': self.state['episode']['events'][0]['id'],
            'accountId': ACCOUNT['accountId'], 'identityGeneration': 'generation-a',
            'foremanThreadId': self.config['foremanThreadId'], 'observedAt': 20,
            'inventory': {'complete': True, 'evidenceReferences': ['fixture-native-roster']},
            'roots': [{'threadId': 'fixture-root', 'status': 'stopped',
                       'workersComplete': True,
                       'workers': [{'id': 'fixture-child', 'status': 'stopped'}],
                       'continuationHeld': True, 'failureKind': None,
                       'evidenceReferences': ['fixture-root-child-final-and-held-wakes']}],
        }

    def produce(self, record=None):
        path = self.root / 'input.json'
        save(path, self.record if record is None else record)
        return subprocess.run([
            sys.executable, '-B', str(PACKAGE / 'record_pause_completion.py'),
            '--config', str(self.config_path), '--state-dir', str(self.root),
            '--account', 'main', '--record', str(path)], text=True, capture_output=True)

    def test_cli_records_completion_without_rewriting_delivery_or_reset_state(self):
        before = (self.root / 'main.json').read_bytes()
        result = self.produce()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'stopped')
        self.assertEqual((self.root / 'main.json').read_bytes(), before)
        self.assertEqual((self.root / 'pause-completion-main.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(list((self.root / 'pause-completions').glob('*.json'))), 1)

    def test_status_reads_new_record_and_rejects_changed_generation(self):
        from status import status
        self.assertEqual(self.produce().returncode, 0)
        self.assertEqual(status(self.config_path, self.root)['episodes']['main']['protectiveStop']['status'], 'stopped')
        selected = json.loads((self.root / 'main-selection.json').read_text())
        selected['generation'] = 'generation-b'
        save(self.root / 'main-selection.json', selected)
        self.assertEqual(status(self.config_path, self.root)['episodes']['main']['protectiveStop']['status'], 'unconfirmed')

    def test_observer_restart_reads_completion_without_sending_or_releasing(self):
        import os
        from unittest.mock import patch
        from controller import run
        pinned = self.config['accounts'][1]
        def read(account, **kwargs):
            value = sample(100, resets=0)
            if account['name'] == 'daybreak':
                value.update(email=pinned['email'], accountId=pinned['accountId'])
            return value
        self.assertEqual(self.produce().returncode, 0)
        original = self.state['episode']['id']
        with patch('controller.read_account', side_effect=read), \
             patch('controller.default_identity', return_value={k: ACCOUNT[k] for k in ('email', 'accountId')}), \
             patch.dict(os.environ, {}, clear=True), patch('controller.Transport') as transport:
            for _ in range(2):
                health = run(self.config, self.root, once=True, observe_only=True)
                self.assertEqual(health['accounts']['main']['protectiveStop']['status'], 'stopped')
            transport.assert_not_called()
        retained = json.loads((self.root / 'main.json').read_text())
        self.assertEqual(retained['episode']['id'], original)
        self.assertTrue(retained['episode']['active'])
        self.assertEqual(retained['episode']['events'], self.state['episode']['events'])

    def test_denied_unknown_and_partial_are_never_complete(self):
        import copy
        from status import status
        for case, expected in [('tool_denial', 'blocked'), ('instruction_refusal', 'blocked'),
                               ('unknown', 'unknown'), ('partial', 'partial')]:
            with self.subTest(case=case):
                record = copy.deepcopy(self.record)
                root = record['roots'][0]
                root.update(status='unknown', workersComplete=False, continuationHeld=False)
                if case in ('tool_denial', 'instruction_refusal'):
                    root.update(status='blocked', failureKind=case)
                elif case == 'partial':
                    record['inventory']['complete'] = False
                    root.update(self.record['roots'][0])
                self.assertEqual(self.produce(record).returncode, 0)
                self.assertEqual(status(self.config_path, self.root)['episodes']['main']['protectiveStop']['status'], expected)

    def test_cli_refuses_false_completion_and_bad_bindings(self):
        import copy
        for case in ('workers', 'continuation', 'inventory', 'generation', 'account', 'event', 'foreman', 'duplicate'):
            with self.subTest(case=case):
                record = copy.deepcopy(self.record)
                if case == 'workers': record['roots'][0]['workers'][0]['status'] = 'running'
                elif case == 'continuation': record['roots'][0]['continuationHeld'] = False
                elif case == 'inventory': record['inventory']['evidenceReferences'] = []
                elif case == 'generation': record['identityGeneration'] = 'other'
                elif case == 'account': record['accountId'] = 'other'
                elif case == 'event': record['eventId'] = 'other'
                elif case == 'foreman': record['foremanThreadId'] = 'other'
                else: record['roots'].append(copy.deepcopy(record['roots'][0]))
                self.assertNotEqual(self.produce(record).returncode, 0)
                self.assertFalse((self.root / 'pause-completion-main.json').exists())

    def test_missing_corrupt_invalidated_and_superseded_records_fail_closed(self):
        import copy
        from status import status
        def stop():
            return status(self.config_path, self.root)['episodes']['main']['protectiveStop']['status']
        self.assertEqual(stop(), 'unconfirmed')
        self.assertEqual(self.produce().returncode, 0)
        pointer = json.loads((self.root / 'pause-completion-main.json').read_text())
        archive = self.root / 'pause-completions' / (pointer['sha256'] + '.json')
        corrupt = copy.deepcopy(self.record)
        corrupt['roots'] = []
        save(archive, corrupt)
        self.assertEqual(stop(), 'unconfirmed')
        save(archive, self.record)
        for case in ('invalidated', 'closed', 'superseded'):
            state = copy.deepcopy(self.state)
            if case == 'invalidated': state['episode']['events'][0]['invalidated'] = {'reason': 'fixture-switch'}
            elif case == 'closed': state['episode']['active'] = False
            else: state['episode']['events'].append({**state['episode']['events'][0], 'id': 'new-event'})
            save(self.root / 'main.json', state)
            self.assertEqual(stop(), 'unconfirmed')
            self.assertNotEqual(self.produce().returncode, 0)

    def test_archive_retains_history_and_rejects_older_observations(self):
        import copy
        self.assertEqual(self.produce().returncode, 0)
        newer = copy.deepcopy(self.record)
        newer['observedAt'] += 1
        newer['roots'][0].update(status='unknown', workersComplete=False)
        self.assertEqual(self.produce(newer).returncode, 0)
        self.assertNotEqual(self.produce().returncode, 0)
        self.assertEqual(len(list((self.root / 'pause-completions').glob('*.json'))), 2)

    def test_daybreak_uses_pinned_identity_without_main_generation(self):
        import copy
        record = copy.deepcopy(self.record)
        pinned = self.config['accounts'][1]
        value = sample(100, resets=0)
        value.update(email=pinned['email'], accountId=pinned['accountId'])
        state, _ = advance({}, value, {**pinned, 'alsoCurrentMain': True}, now=10)
        save(self.root / 'daybreak.json', state)
        record.update(episodeId=state['episode']['id'], eventId=state['episode']['events'][0]['id'],
                      accountId=pinned['accountId'], identityGeneration=None)
        path = self.root / 'input.json'
        save(path, record)
        command = [sys.executable, '-B', str(PACKAGE / 'record_pause_completion.py'),
                   '--config', str(self.config_path), '--state-dir', str(self.root),
                   '--account', 'daybreak', '--record', str(path)]
        result = subprocess.run(command, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        from status import status
        self.assertEqual(status(self.config_path, self.root)['episodes']['daybreak']['protectiveStop']['status'], 'stopped')

    def test_private_inputs_and_pointer_paths_are_not_trusted(self):
        from status import status
        self.assertEqual(self.produce().returncode, 0)
        pointer_path = self.root / 'pause-completion-main.json'
        valid_pointer = json.loads(pointer_path.read_text())
        for pointer in ({'schemaVersion': 1, 'sha256': '../input'},
                        {'schemaVersion': True, 'sha256': valid_pointer['sha256']}):
            save(pointer_path, pointer)
            self.assertEqual(status(self.config_path, self.root)['episodes']['main']['protectiveStop']['status'], 'unconfirmed')
            self.assertNotEqual(self.produce().returncode, 0)
        save(pointer_path, valid_pointer)
        pointer_path.chmod(0o644)
        self.assertEqual(status(self.config_path, self.root)['episodes']['main']['protectiveStop']['status'], 'unconfirmed')
        self.assertNotEqual(self.produce().returncode, 0)
        pointer_path.chmod(0o600)
        archive = self.root / 'pause-completions' / (valid_pointer['sha256'] + '.json')
        contents = archive.read_bytes()
        archive.write_bytes(contents + b'\n')
        self.assertEqual(status(self.config_path, self.root)['episodes']['main']['protectiveStop']['status'], 'unconfirmed')
        archive.unlink()
        archive.symlink_to(self.root / 'input.json')
        self.assertEqual(status(self.config_path, self.root)['episodes']['main']['protectiveStop']['status'], 'unconfirmed')
        self.assertNotEqual(self.produce().returncode, 0)

    def test_armed_restart_does_not_replay_submitted_pause_or_consume_reset(self):
        import os
        from unittest.mock import patch
        from activation import configuration_binding
        from controller import run
        self.config['enabled'] = True
        (self.root / 'legacy.lock').touch(mode=0o600)
        save(self.root / 'activation.json', {'mode': 'active', 'binding': configuration_binding(self.config),
                                            'reference': 'fixture-active-approval'})
        event = self.state['episode']['events'][0]
        event['deliveries'] = {recipient: {'status': 'submitted', 'target': self.config[key]}
                               for recipient, key in [('foreman', 'foremanThreadId'), ('parent', 'parentThreadId')]}
        save(self.root / 'main.json', self.state)
        self.assertEqual(self.produce().returncode, 0)
        pinned = self.config['accounts'][1]
        def read(account, **kwargs):
            value = sample(100 if account['name'] == 'main' else 0, resets=0)
            if account['name'] == 'daybreak': value.update(email=pinned['email'], accountId=pinned['accountId'])
            return value
        with patch('controller.read_account', side_effect=read), \
             patch('controller.default_identity', return_value={k: ACCOUNT[k] for k in ('email', 'accountId')}), \
             patch.dict(os.environ, {}, clear=True), patch('controller.Transport') as transport:
            bridge = transport.return_value.connection.return_value
            bridge.for_recipient.return_value = bridge
            for _ in range(2):
                health = run(self.config, self.root, once=True)
                self.assertEqual(health['accounts']['main']['protectiveStop']['status'], 'stopped')
            bridge.send.assert_not_called()
        self.assertEqual(json.loads((self.root / 'main.json').read_text())['episode']['events'], self.state['episode']['events'])
        self.assertFalse((self.root / 'reset-main.json').exists())


    def test_oversized_timestamp_is_refused_without_breaking_observer(self):
        import copy
        import hashlib
        import os
        from unittest.mock import patch
        from controller import run
        from status import status
        record = copy.deepcopy(self.record)
        record['observedAt'] = 10 ** 1000
        self.assertNotEqual(self.produce(record).returncode, 0)
        self.assertEqual(self.produce().returncode, 0)
        fingerprint = hashlib.sha256((json.dumps(record, indent=2) + '\n').encode()).hexdigest()
        save(self.root / 'pause-completions' / (fingerprint + '.json'), record)
        save(self.root / 'pause-completion-main.json', {'schemaVersion': 1, 'sha256': fingerprint})
        self.assertEqual(status(self.config_path, self.root)['episodes']['main']['protectiveStop']['status'], 'unconfirmed')
        pinned = self.config['accounts'][1]
        def read(account, **kwargs):
            value = sample(100, resets=0)
            if account['name'] == 'daybreak': value.update(email=pinned['email'], accountId=pinned['accountId'])
            return value
        with patch('controller.read_account', side_effect=read), \
             patch('controller.default_identity', return_value={k: ACCOUNT[k] for k in ('email', 'accountId')}), \
             patch.dict(os.environ, {}, clear=True):
            health = run(self.config, self.root, once=True, observe_only=True)
        self.assertEqual(health['accounts']['main']['status'], 'healthy')
        self.assertEqual(health['accounts']['main']['protectiveStop']['status'], 'unconfirmed')
