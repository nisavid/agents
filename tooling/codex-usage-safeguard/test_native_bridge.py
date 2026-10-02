"""Drive JSONL MCP through a fixture subprocess, never an installed app socket."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

from native_bridge import NativeBridge, verify_bridge


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
