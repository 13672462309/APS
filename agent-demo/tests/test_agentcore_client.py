"""Offline HTTP/SSE contract tests; no cloud credentials or services used."""

import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentcore_client import AgentCoreError, agent_url, call_agent, load_config, stop_agent


def event(value):
    return "data: " + json.dumps(value, ensure_ascii=False) + "\r\n\r\n"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append((self.path, dict(self.headers), payload))
        if self.path.endswith('/ag-ui/agent/stop'):
            self.send_response(204)
            self.end_headers()
            return
        mode = self.path.split('/')[1]
        if mode == 'unauthorized':
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"error":"bad test-key"}')
            return
        if mode == 'redirect':
            self.send_response(302)
            self.send_header('Location', '/never/ag-ui/agent')
            self.end_headers()
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/html' if mode == 'html' else 'text/event-stream; charset=utf-8')
        self.end_headers()
        content = ': heartbeat\r\n\r\n' + event({
            'type': 'RUN_STARTED', 'threadId': payload['threadId'], 'runId': payload['runId']})
        content += 'data: {"type":"TEXT_MESSAGE_START",\r\ndata: "messageId":"a"}\r\n\r\n'
        for mid, text in [('a', '连接'), ('b', '第二条消息'), ('a', '成功')]:
            content += event({'type': 'TEXT_MESSAGE_CONTENT', 'messageId': mid, 'delta': text})
        content += event({'type': 'REASONING_MESSAGE_CONTENT', 'messageId': 'r', 'delta': 'not answer'})
        if mode == 'run_error':
            content += event({'type': 'RUN_ERROR', 'code': 'TEST_ERROR', 'message': 'failed'})
        elif mode == 'bad_json':
            content += 'data: invalid\r\n\r\n'
        elif mode != 'disconnect':
            finished = {'type': 'RUN_FINISHED', 'threadId': payload['threadId'], 'runId': payload['runId']}
            if mode != 'missing_outcome':
                finished['outcome'] = {'type': 'interrupt' if mode == 'interrupt' else 'success'}
            if mode == 'mismatch':
                finished['runId'] = 'different-run'
            content += event(finished)
            if mode == 'truncated':
                content = content.rstrip('\r\n')
        encoded = content.encode('utf-8')
        # Break a Chinese character across writes; the HTTP reader must retain it.
        split = encoded.index('连接'.encode('utf-8')) + 1
        try:
            self.wfile.write(encoded[:split])
            self.wfile.flush()
            self.wfile.write(encoded[split:])
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


class ClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.proxy_patch = patch('urllib.request.getproxies', return_value={})
        cls.proxy_patch.start()
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.server.requests = []
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:' + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.proxy_patch.stop()

    def invoke(self, mode):
        return call_agent(self.base + '/' + mode, 'test-key', '中文测试',
                          thread_id='session-test', run_id='run-test', timeout=2)

    def test_success_and_request_contract(self):
        reply = self.invoke('success')
        self.assertEqual(reply.messages, {'a': '连接成功', 'b': '第二条消息'})
        path, headers, body = self.server.requests[-1]
        headers = {k.lower(): v for k,v in headers.items()}
        self.assertEqual(path, '/success/ag-ui/agent')
        self.assertEqual(headers['x-api-key'], 'test-key')
        self.assertEqual(headers['x-agentcore-session-id'], body['threadId'])
        self.assertEqual(body['messages'][0]['content'], '中文测试')
        self.assertEqual(body['tools'], [])

    def test_explicit_stop_contract(self):
        self.assertTrue(stop_agent(self.base + '/success', 'test-key', 'session-test', 'run-test'))
        path, headers, body = self.server.requests[-1]
        self.assertEqual(path, '/success/ag-ui/agent/stop')
        self.assertEqual(body, {'threadId':'session-test', 'runId':'run-test'})

    def test_unsuccessful_streams_never_report_success(self):
        expected = {'html': '不是 SSE', 'disconnect': '未收到成功结束事件',
                    'truncated': '未收到成功结束事件', 'run_error': 'TEST_ERROR',
                    'bad_json': '不是有效 JSON', 'missing_outcome': '不是 success',
                    'interrupt': '不是 success', 'mismatch': '与请求不一致'}
        for mode, message in expected.items():
            with self.subTest(mode=mode), self.assertRaisesRegex(AgentCoreError, message):
                self.invoke(mode)

    def test_http_error_redacts_key(self):
        with self.assertRaises(AgentCoreError) as ctx:
            self.invoke('unauthorized')
        self.assertIn('401', str(ctx.exception))
        self.assertNotIn('test-key', str(ctx.exception))

    def test_redirect_is_not_followed(self):
        before = len(self.server.requests)
        with self.assertRaises(AgentCoreError):
            self.invoke('redirect')
        self.assertEqual(len(self.server.requests), before + 1)

    def test_endpoint_accepts_base_and_full_path(self):
        self.assertEqual(agent_url(self.base + '/agent/'), self.base + '/agent/ag-ui/agent')
        self.assertEqual(agent_url(self.base + '/ag-ui/agent/'), self.base + '/ag-ui/agent')
        for invalid in ['not-a-url', 'https://[invalid', 'https://user:pass@example.com',
                        'https://example.com?key=secret', 'https://example.com:bad']:
            with self.subTest(endpoint=invalid), self.assertRaises(AgentCoreError):
                agent_url(invalid)

    def test_configuration_quotes_and_environment_precedence(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            self.assertTrue(Path(folder).resolve().is_relative_to(Path(__file__).parent.resolve()))
            path = Path(folder) / '.env'
            path.write_text('AGENTCORE_ENDPOINT="https://example.com"\nAGENTCORE_ENDPOINT_API_KEY=local-key\n', encoding='utf-8-sig')
            with patch.dict('os.environ', {}, clear=True):
                self.assertEqual(load_config(path), ('https://example.com', 'local-key'))
            with patch.dict('os.environ', {'AGENTCORE_ENDPOINT_API_KEY': 'env-key'}, clear=True):
                self.assertEqual(load_config(path)[1], 'env-key')
            with patch.dict('os.environ', {'AGENTCORE_ENDPOINT_API_KEY': ''}, clear=True):
                with self.assertRaises(AgentCoreError):
                    load_config(path)


if __name__ == '__main__':
    unittest.main()
