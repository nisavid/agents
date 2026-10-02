#!/usr/bin/env python3
"""Normal MCP companion: publish an inherited candidate; expose no action tools."""
import argparse
import json
import os
import sys

from configuration import load_config, runtime_arguments
from transport import publish_candidate


def serve(config, state_dir, incoming, outgoing, environment):
    published = False
    for line in incoming:
        try:
            request = json.loads(line)
        except ValueError:
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
            if not published and config.get('enabled') is True:
                try:
                    publish_candidate(state_dir, config, environment)
                except Exception:
                    # The observer owns durable transport health and local notice.
                    print('quota companion: inherited route publication failed', file=sys.stderr)
                published = True
        elif method == 'tools/list': result = {'tools': []}
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
    parser = argparse.ArgumentParser(description=__doc__); runtime_arguments(parser)
    args = parser.parse_args()
    serve(load_config(args.config, args.state_dir), args.state_dir, sys.stdin, sys.stdout, os.environ)
