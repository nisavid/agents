"""Offline receipt contract: temporary stores and synthetic observations only."""
from pathlib import Path
import tempfile
import unittest
import os
from concurrent.futures import ThreadPoolExecutor
import subprocess
import sys

from incident_receipts import ReceiptJournal


class ReceiptJournalTests(unittest.TestCase):
    def test_operator_can_record_and_read_incident_through_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'receipts.json'
            source = root / 'incident.json'
            source.write_text('{"incident_id":"incident-1","sender":"sender-1","sender_host":"local",'
                              '"recipient":"recipient-1","recipient_host":"local"}')
            source.chmod(0o600)
            script = Path(__file__).with_name('incident_receipts.py')
            def call(*arguments):
                return subprocess.run([sys.executable, str(script), '--store', str(path), *arguments],
                                      capture_output=True, text=True, check=False)
            self.assertEqual(call('init').returncode, 0)
            self.assertEqual(call('incident', '--input', str(source)).returncode, 0)
            result = call('show', '--incident', 'incident-1')
            self.assertEqual(result.returncode, 0)
            self.assertIn('incident-1', result.stdout)

    def test_writer_rejects_nonprivate_existing_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'receipts.json'
            journal = ReceiptJournal.create(path)
            self.addCleanup(journal.close)
            lock = Path(str(path) + '.lock')
            lock.touch(mode=0o600)
            lock.chmod(0o644)
            with self.assertRaises(ValueError):
                journal.record_incident({'incident_id': 'incident-1', 'sender': 'sender-1', 'sender_host': 'local',
                                         'recipient': 'recipient-1', 'recipient_host': 'local'})

    def test_incident_identity_records_native_hosts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'receipts.json'
            journal = ReceiptJournal.create(path)
            self.addCleanup(journal.close)
            identity = {'incident_id': 'incident-1', 'sender': 'sender-1', 'sender_host': 'local',
                        'recipient': 'recipient-1', 'recipient_host': 'local'}
            self.assertTrue(journal.record_incident(identity))
            self.assertEqual(journal.read('incident-1')['identity'], identity)

    def test_writer_interrupted_before_replace_keeps_last_committed_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'receipts.json'
            journal = ReceiptJournal.create(path)
            journal.record_incident({'incident_id': 'incident-1', 'sender': 'sender-1', 'sender_host': 'local', 'recipient': 'recipient-1', 'recipient_host': 'local'})
            journal.close()
            script = """import os, sys, incident_receipts
def stop(*args): os._exit(23)
incident_receipts.save = stop
journal = incident_receipts.ReceiptJournal.open(sys.argv[1])
journal.record_incident({'incident_id':'incident-2','sender':'sender-1','sender_host':'local','recipient':'recipient-1','recipient_host':'local'})
"""
            process = subprocess.run([sys.executable, '-c', script, str(path)], check=False)
            self.assertEqual(process.returncode, 23)
            reader = ReceiptJournal.open(path, read_only=True)
            self.addCleanup(reader.close)
            self.assertIsNotNone(reader.read('incident-1'))
            self.assertIsNone(reader.read('incident-2'))

    def test_writer_interrupted_at_replace_keeps_last_committed_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'receipts.json'
            journal = ReceiptJournal.create(path)
            journal.record_incident({'incident_id': 'incident-1', 'sender': 'sender-1', 'sender_host': 'local', 'recipient': 'recipient-1', 'recipient_host': 'local'})
            journal.close()
            script = """import os, sys, storage
from incident_receipts import ReceiptJournal
def stop(*args): os._exit(23)
storage.os.replace = stop
journal = ReceiptJournal.open(sys.argv[1])
journal.record_incident({'incident_id':'incident-2','sender':'sender-1','sender_host':'local','recipient':'recipient-1','recipient_host':'local'})
"""
            process = subprocess.run([sys.executable, '-c', script, str(path)], check=False)
            self.assertEqual(process.returncode, 23)
            reader = ReceiptJournal.open(path, read_only=True)
            self.addCleanup(reader.close)
            self.assertIsNotNone(reader.read('incident-1'))
            self.assertIsNone(reader.read('incident-2'))

    def test_overlapping_writer_invocations_preserve_both_incidents(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'receipts.json'
            ReceiptJournal.create(path).close()
            def write(number):
                journal = ReceiptJournal.open(path)
                try:
                    return journal.record_incident({'incident_id': f'incident-{number}',
                                                    'sender': 'sender-1', 'sender_host': 'local', 'recipient': 'recipient-1', 'recipient_host': 'local'})
                finally:
                    journal.close()
            with ThreadPoolExecutor(max_workers=2) as pool:
                self.assertEqual(list(pool.map(write, [1, 2])), [True, True])
            reader = ReceiptJournal.open(path, read_only=True)
            self.addCleanup(reader.close)
            self.assertIsNotNone(reader.read('incident-1'))
            self.assertIsNotNone(reader.read('incident-2'))

    def test_writer_refuses_a_symlink_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'receipts.json'
            journal = ReceiptJournal.create(path)
            self.addCleanup(journal.close)
            target = Path(directory) / 'other'
            target.write_text('unrelated')
            os.symlink(target, str(path) + '.lock')
            with self.assertRaises(OSError):
                journal.record_incident({'incident_id': 'incident-1', 'sender': 'sender-1', 'sender_host': 'local', 'recipient': 'recipient-1', 'recipient_host': 'local'})
            self.assertEqual(target.read_text(), 'unrelated')

    def test_read_only_consumer_cannot_write_or_create_a_missing_store(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'receipts.json'
            with self.assertRaises(FileNotFoundError):
                ReceiptJournal.open(path, read_only=True)
            writer = ReceiptJournal.create(path)
            identity = {'incident_id': 'incident-1', 'sender': 'sender-1', 'sender_host': 'local', 'recipient': 'recipient-1', 'recipient_host': 'local'}
            writer.record_incident(identity)
            writer.close()
            reader = ReceiptJournal.open(path, read_only=True)
            self.addCleanup(reader.close)
            self.assertEqual(reader.read('incident-1')['identity'], identity)
            with self.assertRaises(PermissionError):
                reader.record_incident(identity)

    def test_receipt_and_owner_acceptance_remain_distinct_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'receipts.json'
            journal = ReceiptJournal.create(path)
            journal.record_incident({'incident_id': 'incident-1', 'sender': 'sender-1', 'sender_host': 'local', 'recipient': 'recipient-1', 'recipient_host': 'local'})
            received = {'id': 'obs-1', 'kind': 'received', 'attempt_id': 'attempt-1', 'evidence': 'native-message-1',
                        'actor': 'recipient-1', 'host': 'local', 'turn': 'turn-1', 'message': 'message-1',
                        'submission_turn': 'send-turn', 'submission_message': 'send-message'}
            accepted = {'id': 'obs-2', 'kind': 'owner_accepted', 'attempt_id': 'attempt-1', 'evidence': 'owner-message-1',
                        'actor': 'owner-1', 'host': 'local', 'turn': 'turn-2', 'message': 'message-2'}
            with self.assertRaises(ValueError):
                journal.append('incident-1', received)
            attempt = {'id': 'attempt-1', 'caller': 'caller-1', 'caller_host': 'local', 'target': 'recipient-1', 'target_host': 'local', 'payload': 'incident-1 attempt-1'}
            with self.assertRaises(ValueError):
                journal.record_attempt('incident-1', {**attempt, 'target': 'other'})
            with self.assertRaises(ValueError):
                journal.record_attempt('incident-1', {**attempt, 'payload': 'missing correlation'})
            self.assertTrue(journal.record_attempt('incident-1', attempt))
            self.assertFalse(journal.record_attempt('incident-1', attempt))
            with self.assertRaises(ValueError):
                journal.record_attempt('incident-1', {**attempt, 'payload': 'changed'})
            with self.assertRaises(ValueError):
                journal.append('incident-1', {**received, 'actor': 'other-recipient'})
            self.assertTrue(journal.append('incident-1', received))
            self.assertFalse(journal.append('incident-1', received))
            self.assertTrue(journal.read('incident-1')['pending'])
            self.assertTrue(journal.append('incident-1', accepted))
            self.assertTrue(journal.read('incident-1')['pending'])
            continuation = {'id': 'obs-3', 'kind': 'continuation', 'attempt_id': 'attempt-1', 'evidence': 'owned-next-action',
                            'actor': 'owner-1', 'host': 'local', 'turn': 'turn-2', 'message': 'message-2',
                            'next_action': 'review the incident'}
            self.assertTrue(journal.append('incident-1', continuation))
            journal.close()
            reopened = ReceiptJournal.open(path)
            self.addCleanup(reopened.close)
            self.assertEqual(reopened.read('incident-1')['observations'], [received, accepted, continuation])
            self.assertTrue(reopened.read('incident-1')['pending'])
            with self.assertRaises(ValueError):
                reopened.append('incident-1', {**received, 'evidence': 'different'})

    def test_incident_is_idempotent_and_conflicting_identity_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'receipts.json'
            journal = ReceiptJournal.create(path)
            identity = {'incident_id': 'incident-1', 'sender': 'sender-1', 'sender_host': 'local', 'recipient': 'recipient-1', 'recipient_host': 'local'}
            self.assertTrue(journal.record_incident(identity))
            self.assertFalse(journal.record_incident(identity))
            with self.assertRaises(ValueError):
                journal.record_incident({**identity, 'recipient': 'recipient-2'})
            journal.close()
            reopened = ReceiptJournal.open(path)
            self.addCleanup(reopened.close)
            self.assertEqual(reopened.read('incident-1')['identity'], identity)


if __name__ == '__main__':
    unittest.main()
