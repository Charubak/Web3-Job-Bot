"""Per-adapter coverage, without URLs, exception text or private configuration."""
from threading import Lock, local

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


def run_source(fn):
    _context.failures = 0
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
                              'failures': failures}
        del _context.failures


def snapshot():
    with _lock:
        return [dict(_results[name]) for name in sorted(_results)]
