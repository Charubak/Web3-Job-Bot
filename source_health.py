"""Per-adapter coverage with public endpoint paths, no queries/private config."""
from threading import Lock, local
from urllib.parse import urlsplit, urlunsplit

_context = local()
_lock = Lock()
_results = {}


def reset():
    with _lock:
        _results.clear()


def record_failure():
    # Adapters also catch parser failures and search challenges, not just HTTP errors.
    if hasattr(_context, 'failures'):
        _context.failures += 1


def record_request(url, status_code, elapsed_seconds):
    """Public endpoint path only: never userinfo, query values or exceptions."""
    if not hasattr(_context, 'requests'):
        return
    try:
        parsed = urlsplit(str(url))
        if parsed.scheme not in {'https', 'http'} or not parsed.hostname:
            return
        public_url = urlunsplit((parsed.scheme, parsed.hostname, parsed.path, '', ''))
    except ValueError:
        return
    _context.requests.append({'url': public_url, 'status_code': status_code,
                              'elapsed_seconds': round(elapsed_seconds, 2)})


def record_warning(code):
    if hasattr(_context, 'warnings') and code not in _context.warnings:
        _context.warnings.append(code)


def run_source(fn):
    _context.failures = 0
    _context.requests = []
    _context.warnings = []
    name = fn.__name__.removeprefix('fetch_')
    jobs = []
    try:
        jobs = fn()
        return jobs
    except Exception:
        record_failure()
        raise
    finally:
        failures = _context.failures
        with _lock:
            _results[name] = {'source': name, 'count': len(jobs),
                              'status': 'partial' if failures and jobs else 'unavailable' if failures else 'returned',
                              'failures': failures, 'requests': list(_context.requests),
                              'warning': 'zero_results' if not jobs and not failures else '',
                              'warnings': list(_context.warnings)}
        del _context.failures
        del _context.requests
        del _context.warnings


def snapshot():
    with _lock:
        return [dict(_results[name]) for name in sorted(_results)]
