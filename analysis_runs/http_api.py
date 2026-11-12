import hmac
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit
from network.contracts import canonical, fields
from analysis_runs.workspace import Workspace, RevisionConflict
from analysis_runs.reports import report
from equity.comparison import compare


def dispatch(workspace, operation, body):
    if operation == 'branch':
        fields(body, ('name',))
        return workspace.branch(body['name'])
    if operation == 'result':
        fields(body, ('key',))
        return workspace.result(body['key'])
    if operation == 'report':
        fields(body, ('key', 'kind'), ('options',))
        result = workspace.result(body['key'])
        return report(workspace.study(result['input_digest']), result, body['kind'], body.get('options', {}))
    if operation == 'compare':
        fields(body, ('before', 'after'))
        return compare(workspace.result(body['before']), workspace.result(body['after']))
    if operation == 'publication':
        fields(body, ('label',))
        return workspace.publication(body['label'])
    if operation == 'collections':
        fields(body, ())
        return {
            'branches': [row[0] for row in workspace.db.execute('SELECT name FROM branches ORDER BY name')],
            'results': [row[0] for row in workspace.db.execute('SELECT key FROM results ORDER BY key')],
            'publications': [row[0] for row in workspace.db.execute('SELECT label FROM publications ORDER BY label')],
        }
    raise ValueError('unknown read operation')


def handler(database, token):
    if not isinstance(token, str) or len(token) < 24:
        raise ValueError('token must have at least 24 characters')

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def respond(self, status, body):
            encoded = canonical(body).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(encoded)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(encoded)
            self.close_connection = True

        def do_GET(self):
            self.respond(405, {'error': 'authenticated_POST_required'})

        def do_POST(self):
            if not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token):
                self.respond(401, {'error': 'unauthorized'})
                return
            if self.headers.get('Transfer-Encoding') is not None:
                self.respond(400, {'error': 'transfer_encoding_unsupported'})
                return
            lengths = self.headers.get_all('Content-Length', [])
            if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
                self.respond(400, {'error': 'single_content_length_required'})
                return
            length = int(lengths[0])
            if length > 1000000:
                self.respond(413, {'error': 'request_too_large'})
                return
            if self.headers.get_content_type() != 'application/json':
                self.respond(415, {'error': 'JSON_required'})
                return
            path = urlsplit(self.path)
            if path.query or path.fragment or not path.path.startswith('/read/'):
                self.respond(404, {'error': 'unknown_route'})
                return
            operation = path.path[6:]
            if not operation or '/' in operation:
                self.respond(404, {'error': 'unknown_route'})
                return
            self.connection.settimeout(10)
            try:
                data = self.rfile.read(length)
                if len(data) != length:
                    raise ValueError('incomplete request')
                body = json.loads(data)
                with Workspace(database) as workspace:
                    result = dispatch(workspace, operation, body)
            except KeyError:
                self.respond(404, {'error': 'not_found'})
            except (ValueError, TypeError, UnicodeError) as exc:
                self.respond(400, {'error': str(exc)[:1000]})
            except TimeoutError:
                self.respond(408, {'error': 'request_timeout'})
            else:
                self.respond(200, {'result': result})

    return Handler


def serve(database, token, port=8080):
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError('invalid port')
    with HTTPServer(('127.0.0.1', port), handler(database, token)) as server:
        server.serve_forever(poll_interval=.2)
