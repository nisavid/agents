"""Public runtime boundaries; every account and native route is a fixture."""
import copy
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
from safeguard import advance
from status import status
from preflight import preflight
from storage import save
from activation import configuration_binding
from test_guard import ACCOUNT, sample
from test_controller import CONFIG, ReceiptTransport


class RuntimeTests(unittest.TestCase):
    def capture_process(self, root, requests, *, receipt=None, receipt_bytes=None,
                        environment=None, bridge_script=None):
        receipt_path = root / 'creation-receipt.json'
        if receipt_bytes is None:
            save(receipt_path, receipt if receipt is not None else {'threadId': 'control-fixture', 'hostId': 'local'})
        else:
            receipt_path.write_bytes(receipt_bytes); receipt_path.chmod(0o600)
        command = [sys.executable, '-B', str(Path(__file__).with_name('companion.py')),
                   '--capture-receipt', str(receipt_path), '--node-binary', sys.executable,
                   '--native-bridge-script', str(bridge_script or root / 'unused-bridge.py')]
        env = {key: value for key, value in os.environ.items() if not key.startswith('CODEX_')}
        env.update(environment or {})
        result = subprocess.run(command, input='\n'.join(r if isinstance(r, str) else json.dumps(r)
                                for r in requests) + '\n',
                                capture_output=True, text=True, env=env, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        return [json.loads(line) for line in result.stdout.splitlines()]

    def capture_request(self, *, metadata=None, arguments=None):
        return {'id': 7, 'method': 'tools/call', 'params': {
            'name': 'capture_native_context', 'arguments': arguments or {}, '_meta': metadata or {
                'threadId': 'control-fixture',
                'x-codex-turn-metadata': {'session_id': 'control-fixture', 'turn_id': 'actual-turn'},
                'openai/toolCallId': 'actual-tool-call'}}}

    def capture_bridge(self, root, readback, *, replace_route=False):
        script = root / 'native-fixture.py'
        script.write_text('''import json, os, sys, socket
for line in sys.stdin:
    request = json.loads(line)
    if 'id' not in request: continue
    if request['method'] == 'initialize': result = {}
    else:
        params = request['params']
        assert request['method'] == 'tools/call' and params['name'] == 'read_thread'
        assert params['arguments'] == {'threadId': 'control-fixture', 'hostId': 'local',
            'turnLimit': 1, 'includeOutputs': False, 'maxOutputCharsPerItem': 1}
        assert params['_meta']['openai/threadId'] == 'control-fixture'
        assert params['_meta']['openai/turnId'] == 'actual-turn'
        assert os.environ['CODEX_APP_TOOLS_CALLER_HOST_ID'] == 'local'
''' + ('''        route = os.environ['CODEX_APP_TOOLS_PIPE_PATH']
        os.unlink(route); replacement = socket.socket(socket.AF_UNIX); replacement.bind(route)
''' if replace_route else '') + '''
        result = {'content': [{'type': 'text', 'text': ''' + repr(json.dumps(readback)) + '''}]}
    print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}), flush=True)
''')
        return script

    def test_capture_returns_genuine_metadata_and_exact_local_readback_without_adoption(self):
        import hashlib
        import socket
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); route = root / 'native.sock'
            with socket.socket(socket.AF_UNIX) as inherited:
                inherited.bind(str(route))
                bridge = self.capture_bridge(root, {'thread': {
                    'id': 'control-fixture', 'kind': 'codex', 'hostId': 'local'}})
                save(root / 'config.json', {'nativeContext': {'threadId': 'rejected-old-owner'}})
                save(root / 'activation.json', {'mode': 'maintenance'})
                save(root / 'main.json', {'episode': {'deliveries': {'parent': {'status': 'outcome_unknown'}}}})
                before = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
                replies = self.capture_process(root, [self.capture_request()], bridge_script=bridge,
                    environment={'CODEX_APP_TOOLS_PIPE_PATH': str(route),
                                 'CODEX_APP_TOOLS_CALLER_HOST_ID': 'local'})
                report = json.loads(replies[0]['result']['content'][0]['text'])
                self.assertEqual(report['status'], 'captured_candidate')
                candidate = json.loads(report['candidateJson'])
                self.assertEqual(candidate['nativeContext'], {
                    'threadId': 'control-fixture', 'turnId': 'actual-turn', 'host': 'local'})
                self.assertEqual(candidate['evidence']['runtimeToolCallId'], 'actual-tool-call')
                self.assertEqual(candidate['evidence']['hostProvenance'], 'explicit_runtime_host')
                self.assertEqual(candidate['evidence']['nativeReadback'], {
                    'threadId': 'control-fixture', 'kind': 'codex', 'hostId': 'local'})
                self.assertEqual(hashlib.sha256(report['candidateJson'].encode('utf-8')).hexdigest(),
                                 report['sha256'])
                self.assertFalse(replies[0]['result']['isError'])
                self.assertEqual({p.name: p.read_bytes() for p in root.iterdir() if p.is_file()
                                  and p.name != 'creation-receipt.json'}, before)
                self.assertEqual(sorted(p.name for p in root.iterdir()),
                    ['activation.json', 'config.json', 'creation-receipt.json', 'main.json',
                     'native-fixture.py', 'native.sock'])

    def test_capture_accepts_source_supported_local_default_only_with_all_independent_checks(self):
        import socket
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); route = root / 'native.sock'
            with socket.socket(socket.AF_UNIX) as inherited:
                inherited.bind(str(route))
                bridge = self.capture_bridge(root, {'thread': {
                    'id': 'control-fixture', 'kind': 'codex', 'hostId': 'local'}})
                reports = []
                for account in ('first-fixture-account', 'changed-fixture-account'):
                    replies = self.capture_process(root, [self.capture_request()], bridge_script=bridge,
                        environment={'CODEX_APP_TOOLS_PIPE_PATH': str(route),
                                     'CODEX_ACCOUNT_ID': account})
                    reports.append(json.loads(replies[0]['result']['content'][0]['text']))
                self.assertEqual(reports[0]['status'], 'captured_candidate')
                self.assertEqual(json.loads(reports[0]['candidateJson'])['evidence']['hostProvenance'],
                                 'constrained_local_protocol_default')
                self.assertEqual(reports[0], reports[1])

                bridge = self.capture_bridge(root, {'thread': {
                    'id': 'control-fixture', 'kind': 'chatgpt', 'hostId': 'local'}})
                reply = self.capture_process(root, [self.capture_request()], bridge_script=bridge,
                    environment={'CODEX_APP_TOOLS_PIPE_PATH': str(route)})[0]
                self.assertEqual(json.loads(reply['result']['content'][0]['text']),
                    {'status': 'unsupported_capture', 'reason': 'exact_local_native_readback_required'})

    def test_capture_rejects_conflicting_or_malformed_runtime_provenance(self):
        valid = self.capture_request()['params']['_meta']
        cases = [
            (dict(valid, **{'openai/threadId': 'foreign-chat'}), 'conflicting_runtime_metadata'),
            (dict(valid, **{'turnId': 'invented-turn'}), 'conflicting_runtime_metadata'),
            (dict(valid, **{'call': {'id': 'invented-call'}}), 'conflicting_runtime_metadata'),
            (dict(valid, **{'openai/threadId': 17}), 'malformed_runtime_metadata'),
            (dict(valid, **{'x-codex-turn-metadata': 'not-json'}), 'malformed_runtime_metadata'),
            (dict(valid, **{'call': []}), 'malformed_runtime_metadata'),
        ]
        for metadata, reason in cases:
            with self.subTest(metadata=metadata), tempfile.TemporaryDirectory() as name:
                reply = self.capture_process(Path(name), [self.capture_request(metadata=metadata)])[0]
                self.assertEqual(json.loads(reply['result']['content'][0]['text']),
                                 {'status': 'unsupported_capture', 'reason': reason})

    def test_capture_accepts_matching_native_metadata_aliases_and_json_turn_bag(self):
        import socket
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); route = root / 'native.sock'
            with socket.socket(socket.AF_UNIX) as inherited:
                inherited.bind(str(route))
                bridge = self.capture_bridge(root, {'thread': {
                    'id': 'control-fixture', 'kind': 'codex', 'hostId': 'local'}})
                reply = self.capture_process(root, [self.capture_request(metadata={
                    'codex_thread_id': 'control-fixture', 'thread': {'id': 'control-fixture'},
                    'x-codex-turn-metadata': '{"turn_id":"actual-turn"}',
                    'turn': {'id': 'actual-turn'}, 'codex_call_id': 'actual-tool-call'})],
                    bridge_script=bridge, environment={'CODEX_APP_TOOLS_PIPE_PATH': str(route)})[0]
                report = json.loads(reply['result']['content'][0]['text'])
                self.assertEqual(report['status'], 'captured_candidate')
                sources = json.loads(report['candidateJson'])['evidence']['runtimeMetadataSources']
                self.assertEqual(sources['threadId'], ['codex_thread_id', 'thread.id'])
                self.assertEqual(sources['turnId'], ['turn.id', 'x-codex-turn-metadata.turn_id'])
                self.assertEqual(sources['toolCallId'], ['codex_call_id'])

    def test_capture_binds_exact_private_creation_receipt_bytes(self):
        import hashlib
        import socket
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); route = root / 'native.sock'
            with socket.socket(socket.AF_UNIX) as inherited:
                inherited.bind(str(route))
                bridge = self.capture_bridge(root, {'thread': {
                    'id': 'control-fixture', 'kind': 'codex', 'hostId': 'local'}})
                receipt_bytes = b'{"hostId":"local", "threadId":"control-fixture"}\n'
                reply = self.capture_process(root, [self.capture_request()], receipt_bytes=receipt_bytes,
                    bridge_script=bridge, environment={'CODEX_APP_TOOLS_PIPE_PATH': str(route)})[0]
                candidate = json.loads(json.loads(reply['result']['content'][0]['text'])['candidateJson'])
                self.assertEqual(candidate['evidence']['expectedReceiptSha256'],
                                 hashlib.sha256(receipt_bytes).hexdigest())
                for invalid in (b'not-json', b'{"threadId":"foreign","threadId":"control-fixture","hostId":"local"}'):
                    reply = self.capture_process(root, [self.capture_request()], receipt_bytes=invalid,
                        bridge_script=bridge, environment={'CODEX_APP_TOOLS_PIPE_PATH': str(route)})[0]
                    self.assertEqual(json.loads(reply['result']['content'][0]['text']), {
                        'status': 'unsupported_capture', 'reason': 'expected_local_creation_receipt_required'})

    def test_capture_rejects_duplicate_metadata_keys_without_using_the_last_identity(self):
        with tempfile.TemporaryDirectory() as name:
            request = json.dumps(self.capture_request())
            request = request.replace('"threadId": "control-fixture"',
                '"threadId": "foreign-chat", "threadId": "control-fixture"')
            replies = self.capture_process(Path(name), [request, {'id': 8, 'method': 'ping'}])
            self.assertEqual(replies[0], {'jsonrpc': '2.0', 'id': None,
                'error': {'code': -32700, 'message': 'unsupported_capture: malformed_runtime_metadata'}})
            self.assertEqual(replies[1]['result'], {})

    def test_capture_never_uses_rpc_ids_transcripts_or_shell_identity_as_missing_provenance(self):
        valid = self.capture_request()['params']['_meta']
        cases = [
            {key: value for key, value in valid.items() if key != 'openai/toolCallId'},
            dict({key: value for key, value in valid.items() if key != 'openai/toolCallId'},
                 _codex_apps={'call_id': 'actual-tool-call'}),
            {'threadId': 'control-fixture', 'openai/toolCallId': 'actual-tool-call',
             'turns': [{'id': 'actual-turn'}]},
            {'x-codex-turn-metadata': {'session_id': 'control-fixture', 'turn_id': 'actual-turn'},
             'openai/toolCallId': 'actual-tool-call'},
        ]
        for metadata in cases:
            with self.subTest(metadata=metadata), tempfile.TemporaryDirectory() as name:
                reply = self.capture_process(Path(name), [self.capture_request(metadata=metadata)],
                    environment={'CODEX_THREAD_ID': 'control-fixture', 'CODEX_TURN_ID': 'actual-turn'})[0]
                self.assertEqual(json.loads(reply['result']['content'][0]['text']), {
                    'status': 'unsupported_capture', 'reason': 'complete_runtime_metadata_required'})

    def test_capture_refuses_foreign_or_queued_enrollment_and_nonlocal_explicit_host(self):
        receipts = [
            {'threadId': 'foreign-chat', 'hostId': 'local'},
            {'threadId': 'control-fixture', 'hostId': 'durable'},
            {'clientThreadId': 'control-fixture', 'hostId': 'local'},
            {'threadId': 'control-fixture', 'hostId': 'local', 'clientThreadId': 'queued-chat'},
            [],
        ]
        for receipt in receipts:
            with self.subTest(receipt=receipt), tempfile.TemporaryDirectory() as name:
                reply = self.capture_process(Path(name), [self.capture_request()], receipt=receipt)[0]
                self.assertEqual(json.loads(reply['result']['content'][0]['text']), {
                    'status': 'unsupported_capture', 'reason': 'expected_local_creation_receipt_required'})
        for host in ('durable', '', 'unknown-host'):
            with self.subTest(host=host), tempfile.TemporaryDirectory() as name:
                reply = self.capture_process(Path(name), [self.capture_request()],
                    environment={'CODEX_APP_TOOLS_CALLER_HOST_ID': host})[0]
                self.assertEqual(json.loads(reply['result']['content'][0]['text']), {
                    'status': 'unsupported_capture', 'reason': 'explicit_local_runtime_host_required'})

    def test_capture_requires_actual_local_readback_despite_a_preferred_local_host(self):
        import socket
        variants = [
            {'id': 'foreign-chat', 'kind': 'codex', 'hostId': 'local'},
            {'id': 'control-fixture', 'kind': 'codex', 'hostId': 'durable'},
            {'id': 'control-fixture', 'kind': 'chatgpt', 'hostId': 'local'},
            {'id': 'control-fixture', 'hostId': 'local'},
            None,
        ]
        for thread in variants:
            with self.subTest(thread=thread), tempfile.TemporaryDirectory() as name:
                root = Path(name); route = root / 'native.sock'
                with socket.socket(socket.AF_UNIX) as inherited:
                    inherited.bind(str(route)); bridge = self.capture_bridge(root, {'thread': thread})
                    reply = self.capture_process(root, [self.capture_request()], bridge_script=bridge,
                        environment={'CODEX_APP_TOOLS_PIPE_PATH': str(route)})[0]
                    self.assertEqual(json.loads(reply['result']['content'][0]['text']), {
                        'status': 'unsupported_capture', 'reason': 'exact_local_native_readback_required'})

    def test_capture_without_inherited_route_does_not_discover_one(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); (root / 'plausible-native-route').write_text('fixture')
            reply = self.capture_process(root, [self.capture_request()])[0]
            self.assertEqual(json.loads(reply['result']['content'][0]['text']), {
                'status': 'unsupported_capture', 'reason': 'inherited_native_route_required'})
            self.assertEqual(sorted(p.name for p in root.iterdir()),
                             ['creation-receipt.json', 'plausible-native-route'])

    def test_capture_refuses_a_native_route_replaced_during_readback(self):
        import socket
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); route = root / 'native.sock'
            with socket.socket(socket.AF_UNIX) as inherited:
                inherited.bind(str(route))
                bridge = self.capture_bridge(root, {'thread': {
                    'id': 'control-fixture', 'kind': 'codex', 'hostId': 'local'}}, replace_route=True)
                reply = self.capture_process(root, [self.capture_request()], bridge_script=bridge,
                    environment={'CODEX_APP_TOOLS_PIPE_PATH': str(route)})[0]
                self.assertEqual(json.loads(reply['result']['content'][0]['text']), {
                    'status': 'unsupported_capture', 'reason': 'inherited_route_generation_changed'})

    def test_opt_in_capture_lists_one_read_only_no_argument_tool_without_live_config(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            replies = self.capture_process(root, [
                {'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2024-11-05'}},
                {'id': 2, 'method': 'tools/list'}])
            tools = replies[-1]['result']['tools']
            self.assertEqual(len(tools), 1)
            self.assertEqual(tools[0]['name'], 'capture_native_context')
            self.assertEqual(tools[0]['inputSchema'],
                             {'type': 'object', 'properties': {}, 'additionalProperties': False})
            self.assertTrue(tools[0]['annotations']['readOnlyHint'])
            self.assertEqual(sorted(p.name for p in root.iterdir()), ['creation-receipt.json'])

    def test_capture_rejects_caller_identity_arguments_and_missing_runtime_metadata(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            replies = self.capture_process(root, [
                {'id': 1, 'method': 'tools/call', 'params': {
                    'name': 'capture_native_context', 'arguments': {'threadId': 'control-fixture',
                    'turnId': 'invented-turn', 'host': 'local'}}},
                {'id': 2, 'method': 'tools/call', 'params': {
                    'name': 'capture_native_context', 'arguments': {}}}])
            reports = [json.loads(r['result']['content'][0]['text']) for r in replies]
            self.assertEqual(reports, [
                {'status': 'unsupported_capture', 'reason': 'capture_arguments_forbidden'},
                {'status': 'unsupported_capture', 'reason': 'complete_runtime_metadata_required'}])
            self.assertTrue(all(r['result']['isError'] for r in replies))
            self.assertEqual(sorted(p.name for p in root.iterdir()), ['creation-receipt.json'])

    def test_notifications_name_configured_approval_chat_for_both_recipients(self):
        for resets in (0, 1):
            with self.subTest(resets=resets), tempfile.TemporaryDirectory() as name:
                path = Path(name) / 'main.json'; transport = ReceiptTransport(path)
                config = dict(CONFIG, parentThreadId='appointed-approval-chat')
                state, _ = advance({}, sample(100, resets=resets), ACCOUNT, now=10)
                dispatch(state, ACCOUNT, config, transport, path)
                for target, prompt in transport.received:
                    self.assertIn('configured approval/status chat appointed-approval-chat', prompt)
                    self.assertNotIn('main chat', prompt)
                    self.assertNotIn('current parent', prompt)
                self.assertEqual(transport.received[-1][0], 'appointed-approval-chat')

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

    def config(self, root):
        config = json.loads(Path(__file__).with_name('config.example.json').read_text())
        config.update(stateDir=str(root), sharedLock=str(root / 'legacy.lock'), enabled=True)
        (root / 'legacy.lock').touch()
        save(root / 'activation.json', {'mode': 'active', 'binding': configuration_binding(config), 'reference': 'fixture-approval'})
        return config

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
            with self.assertRaises(RuntimeError): dispatch(state, ACCOUNT, CONFIG, failing, path)
            replacement = ReceiptTransport(path)
            with self.assertRaises(RuntimeError):
                dispatch(json.loads(path.read_text()), ACCOUNT, CONFIG, replacement, path)
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
            dispatch(state, ACCOUNT, CONFIG, transport, path)
            state, events = advance(state, sample(100, resets=1), ACCOUNT, now=20)
            self.assertEqual([e['kind'] for e in events], ['reset_confirmation_required'])
            dispatch(state, ACCOUNT, CONFIG, transport, path)
            self.assertEqual([target for target, _ in transport.received], ['foreman', 'parent', 'parent'])

    def test_new_pause_does_not_overwrite_uncertain_confirmation_receipt(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'main.json'
            policy = {'usdPerCredit': '0.04', 'pauseAtUsd': '8'}
            state, _ = advance({}, sample(100), ACCOUNT, now=10, policy=policy)
            with self.assertRaises(RuntimeError):
                dispatch(state, ACCOUNT, CONFIG, ReceiptTransport(path, fail='parent'), path)
            old_receipt = copy.deepcopy(state['episode']['events'][0]['deliveries']['parent'])
            state, _ = advance(state, sample(100, balance='800'), ACCOUNT, now=20, policy=policy)
            with self.assertRaises(RuntimeError): dispatch(state, ACCOUNT, CONFIG, ReceiptTransport(path), path)
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

    def test_release_preflight_preserves_active_ledger_and_unknown_receipt(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root)
            state, _ = advance({}, sample(100, resets=0), ACCOUNT, now=10)
            state['episode']['events'][0]['deliveries'] = {'foreman': {'status': 'outcome_unknown'}}
            save(root / 'main.json', state)
            (root / 'unrelated-observation.json').write_text('{}')
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            report = preflight(config, root)
            self.assertEqual(report['liveChecks'], 'not_requested')
            self.assertEqual(set(report['preservedLedgerHashes']), {'main.json'})
            self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, before)


if __name__ == '__main__': unittest.main()
