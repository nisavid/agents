"""Drive JSONL MCP through a fixture subprocess, never an installed app socket."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

from native_bridge import NativeBridge, NativeBridgeError, verify_bridge


SERVER = '''
import json, os, sys
for line in sys.stdin:
 request=json.loads(line)
 if 'id' not in request: continue
 if request['method']=='initialize': result={}
 else:
  params=request['params']; meta=params['_meta']; args=params['arguments']
  assert meta['openai/threadId']=='fixture-owner'
  assert meta['openai/turnId']=='fixture-turn'
  assert os.environ['CODEX_APP_TOOLS_CALLER_HOST_ID']=='fixture-host'
  assert os.environ['CODEX_APP_TOOLS_PIPE_PATH']=='/fixture/socket'
  if params['name']=='read_thread':
   value={'thread': {'id': args['threadId'], 'title': 'Codex Foreman' if args['threadId']=='foreman' else 'Parent'}}
  else:
   assert params['name']=='send_message_to_thread'
   assert args['prompt']=='fixture-only-prompt'
   value={'threadId': args['threadId']}
  result={'content': [{'type':'text','text': json.dumps(value)}]}
 print(json.dumps({'jsonrpc':'2.0', 'id':request['id'], 'result':result}),flush=True)
'''


class NativeTests(unittest.TestCase):
    def error_bridge(self, reply, fail_target=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        script = Path(temp.name) / 'error_server.py'
        script.write_text('''
import json, sys
reply = ERROR_REPLY
fail_target = FAIL_TARGET
for line in sys.stdin:
 request = json.loads(line)
 if 'id' not in request: continue
 if request['method'] == 'initialize': result = {'result': {}}
 elif fail_target is not None and request['params']['arguments']['threadId'] != fail_target:
  result = {'result': {'content': [{'type':'text','text':json.dumps({'thread': {
    'id':request['params']['arguments']['threadId'], 'title':'Codex Foreman'}})}]}}
 else: result = reply
 print(json.dumps({'jsonrpc':'2.0','id':request['id'],**result}),flush=True)
'''.replace('ERROR_REPLY', repr(reply)).replace('FAIL_TARGET', repr(fail_target)))
        bridge = NativeBridge({'nodeBinary':sys.executable, 'nativeBridgeScript':str(script),
            'nativeContext':{'threadId':'fixture-owner','turnId':'fixture-turn','host':'fixture-host'}}, '/fixture/socket')
        self.addCleanup(bridge.close)
        return bridge

    def test_socket_denial_is_distinct_and_private_error_text_is_discarded(self):
        bridge = self.error_bridge({'error':{'code':-32603,
            'message':'connect EPERM /private/socket bearer-secret fixture@example.test'}})
        with self.assertRaises(NativeBridgeError) as caught:
            verify_bridge(bridge, 'foreman', 'parent')
        self.assertEqual(caught.exception.diagnostic(), {'kind':'native_socket_access_denied',
            'stage':'read_thread','rpcCode':-32603,'recipient':'foreman'})
        self.assertNotIn('bearer-secret', str(caught.exception))
        self.assertNotIn('/private/socket', repr(caught.exception.__dict__))

    def test_opaque_app_failure_does_not_claim_registration_rejection(self):
        bridge = self.error_bridge({'error':{'code':-32000,
            'message':'MCP error -32000: Codex app tool request failed'}})
        with self.assertRaises(NativeBridgeError) as caught:
            verify_bridge(bridge, 'foreman', 'parent')
        self.assertEqual(caught.exception.kind, 'native_app_request_failed')
        self.assertEqual(caught.exception.rpc_code, -32000)

    def test_explicit_authorization_rejection_is_reported_without_retrying(self):
        bridge = self.error_bridge({'error':{'code':-32003,
            'message':'The caller does not have permission: thread placement belongs to a different actor'}})
        with self.assertRaises(NativeBridgeError) as caught:
            verify_bridge(bridge, 'foreman', 'parent')
        self.assertEqual(caught.exception.kind, 'native_authorization_rejected')
        self.assertEqual(bridge.seq, 2)  # initialize + one read; no new registration or send

    def test_parent_rejection_reports_parent_after_foreman_succeeds(self):
        bridge = self.error_bridge({'result':{'isError':True,'content':[
            {'type':'text','text':'private-account-secret'}]}}, fail_target='parent')
        with self.assertRaises(NativeBridgeError) as caught:
            verify_bridge(bridge, 'foreman', 'parent')
        self.assertEqual(caught.exception.diagnostic(), {'kind':'native_tool_rejected',
            'stage':'read_thread','recipient':'parent'})
        self.assertNotIn('private-account-secret', repr(caught.exception.__dict__))

    def test_unknown_rpc_errors_preserve_only_safe_code_and_stage(self):
        bridge = self.error_bridge({'error':{'code':-32603,'message':'unexpected secret-account-data'}})
        with self.assertRaises(NativeBridgeError) as caught:
            verify_bridge(bridge, 'foreman', 'parent')
        self.assertEqual(caught.exception.kind, 'native_rpc_failed')
        self.assertNotIn('secret-account', repr(caught.exception.__dict__))

    def test_registration_and_exact_targets_survive_stdio_adapter(self):
        with tempfile.TemporaryDirectory() as name:
            script = Path(name) / 'server.py'; script.write_text(SERVER)
            config = {'nodeBinary': sys.executable, 'nativeBridgeScript': str(script),
                      'nativeContext': {'threadId': 'fixture-owner', 'turnId': 'fixture-turn', 'host': 'fixture-host'}}
            bridge = NativeBridge(config, '/fixture/socket')
            try:
                self.assertTrue(verify_bridge(bridge, 'foreman', 'parent'))
                bridge.send('foreman', 'fixture-only-prompt')
            finally: bridge.close()


if __name__ == '__main__': unittest.main()
