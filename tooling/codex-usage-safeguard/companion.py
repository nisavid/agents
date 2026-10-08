#!/usr/bin/env python3
"""MCP companion: normal route renewal or opt-in read-only caller capture."""
import argparse
import json
import os
import sys

from configuration import load_config
from transport import publish_candidate
from caller_capture import CAPTURE_TOOL, capture_settings, capture_context, unique_object


def serve(config, state_dir, incoming, outgoing, environment, *, capture=None):
    published = False
    for line in incoming:
        try:
            request = json.loads(line, object_pairs_hook=unique_object) if capture is not None else json.loads(line)
        except (ValueError, RecursionError) as error:
            if capture is None and isinstance(error, RecursionError):
                raise
            if capture is not None:
                outgoing.write(json.dumps({'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700,
                    'message': 'unsupported_capture: malformed_runtime_metadata'}}) + '\n')
                outgoing.flush()
            continue
        if not isinstance(request, dict) or 'id' not in request:
            continue
        method = request.get('method'); params = request.get('params', {})
        if method == 'initialize':
            if not isinstance(params, dict) or not isinstance(params.get('protocolVersion'), str):
                outgoing.write(json.dumps({'jsonrpc': '2.0', 'id': request['id'],
                    'error': {'code': -32602, 'message': 'Invalid initialize parameters'}}) + '\n')
                outgoing.flush()
                continue
            result = {'protocolVersion': params['protocolVersion'], 'capabilities': {'tools': {}},
                      'serverInfo': {'name': 'codex-quota-connection', 'version': '0.1.0'}}
            if capture is None and not published and config.get('enabled') is True:
                try:
                    publish_candidate(state_dir, config, environment)
                except Exception:
                    # The observer owns durable transport health and local notice.
                    print('quota companion: inherited route publication failed', file=sys.stderr)
                published = True
        elif method == 'tools/list': result = {'tools': [] if capture is None else [CAPTURE_TOOL]}
        elif (method == 'tools/call' and capture is not None and isinstance(params, dict)
              and params.get('name') == CAPTURE_TOOL['name']):
            result = capture_context(capture, params, environment)
        elif method == 'resources/list': result = {'resources': []}
        elif method == 'resources/templates/list': result = {'resourceTemplates': []}
        elif method == 'ping': result = {}
        else:
            outgoing.write(json.dumps({'jsonrpc': '2.0', 'id': request['id'],
                'error': {'code': -32601, 'message': 'No action tools exposed'}}) + '\n')
            outgoing.flush(); continue
        outgoing.write(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}) + '\n')
        outgoing.flush()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config'); parser.add_argument('--state-dir')
    parser.add_argument('--capture-receipt')
    parser.add_argument('--node-binary'); parser.add_argument('--native-bridge-script')
    args = parser.parse_args()
    if args.capture_receipt:
        if args.config or args.state_dir or not args.node_binary or not args.native_bridge_script:
            parser.error('capture requires the receipt and adapter paths, without production config or state')
        capture = capture_settings(args.capture_receipt, args.node_binary, args.native_bridge_script)
        serve(None, None, sys.stdin, sys.stdout, os.environ, capture=capture)
    else:
        if not args.config or not args.state_dir or args.node_binary or args.native_bridge_script:
            parser.error('normal mode requires --config and --state-dir')
        serve(load_config(args.config, args.state_dir), args.state_dir, sys.stdin, sys.stdout, os.environ)
