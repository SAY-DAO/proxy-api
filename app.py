import logging
import os
import time
import uuid
from flask import Flask, request, Response, jsonify, g
import requests
from urllib.parse import urlparse
from pythonjsonlogger import jsonlogger

app = Flask(__name__)

# Configure logging
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()
logger = logging.getLogger()
logger.setLevel(LOG_LEVEL)

# JSON formatter for structured logs
json_formatter = jsonlogger.JsonFormatter('%(asctime)s %(levelname)s %(name)s %(message)s')

# Console handler
ch = logging.StreamHandler()
ch.setLevel(LOG_LEVEL)
ch.setFormatter(json_formatter)

# Avoid adding duplicate handlers if reloading
if not logger.handlers:
    logger.addHandler(ch)
else:
    # replace existing stream handlers to ensure JSON formatting
    for i, h in enumerate(logger.handlers):
        if isinstance(h, logging.StreamHandler):
            logger.handlers[i] = ch

# Integrate with Gunicorn logging when available (so logs appear in the same stream)
if 'gunicorn.error' in logging.Logger.manager.loggerDict:
    gunicorn_logger = logging.getLogger('gunicorn.error')
    app.logger.handlers = gunicorn_logger.handlers
    app.logger.setLevel(gunicorn_logger.level)

# Helper: generate a request id and store start time
@app.before_request
def start_request():
    g.start_time = time.perf_counter()
    g.request_id = request.headers.get('X-Request-ID', str(uuid.uuid4()))
    app.logger.info('request_start', extra={
        'request_id': g.request_id,
        'method': request.method,
        'path': request.path,
        'query': request.query_string.decode('utf-8', errors='ignore'),
        'remote_addr': request.remote_addr,
        'user_agent': request.user_agent.string,
    })

@app.after_request
def end_request(response):
    duration = time.perf_counter() - getattr(g, 'start_time', time.perf_counter())
    response.headers['X-Request-ID'] = getattr(g, 'request_id', '')
    app.logger.info('request_end', extra={
        'request_id': getattr(g, 'request_id', ''),
        'method': request.method,
        'path': request.path,
        'status': response.status_code,
        'duration_s': round(duration, 6),
    })
    return response

@app.route('/proxy', methods=['GET'])
def proxy():
    target = request.args.get('url')
    if not target:
        app.logger.warning('missing_url', extra={'request_id': getattr(g, 'request_id', '')})
        return jsonify({'error': "missing 'url' query parameter"}), 400

    parsed = urlparse(target)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        app.logger.warning('invalid_url', extra={'request_id': getattr(g, 'request_id', ''), 'url': target})
        return jsonify({'error': 'invalid URL, only http/https allowed'}), 400

    # OPTIONAL: simple SSRF protection examples (commented — enable if desired)
    # if parsed.hostname in ('localhost', '127.0.0.1'):
    #     app.logger.warning('blocked_internal', extra={'request_id': getattr(g, 'request_id', ''), 'url': target})
    #     return jsonify({'error': 'destination not allowed'}), 403

    try:
        start = time.perf_counter()
        upstream = requests.get(target, timeout=10)
        elapsed = time.perf_counter() - start
    except requests.RequestException as exc:
        app.logger.exception('upstream_request_failed', extra={'request_id': getattr(g, 'request_id', ''), 'url': target})
        return jsonify({'error': 'upstream request failed', 'details': str(exc)}), 502

    # Log upstream result with useful metadata (status, time, size)
    app.logger.info('upstream_response', extra={
        'request_id': getattr(g, 'request_id', ''),
        'url': target,
        'upstream_status': upstream.status_code,
        'upstream_elapsed_s': round(getattr(upstream, 'elapsed', elapsed).total_seconds() if hasattr(upstream, 'elapsed') else elapsed, 6),
        'response_size_bytes': len(upstream.content),
    })

    headers = {}
    content_type = upstream.headers.get('Content-Type')
    if content_type:
        headers['Content-Type'] = content_type

    # Propagate request id downstream via header for easier tracing across systems
    headers['X-Request-ID'] = getattr(g, 'request_id', '')

    return Response(upstream.content, status=upstream.status_code, headers=headers)

if __name__ == '__main__':
    # In production, use Gunicorn instead of Flask's built-in server
    # Gunicorn should be configured via Dockerfile (CMD ["gunicorn", "app:app"])
    app.run(host='0.0.0.0', port=5000, debug=False)  # Debug should be False in production
