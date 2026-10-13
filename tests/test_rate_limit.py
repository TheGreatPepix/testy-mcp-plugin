from testy_mcp.rate_limit import check_rate_limit


class FakeCache:
    def __init__(self):
        self.values = {}

    def add(self, key, value, timeout):
        if key in self.values:
            return False
        self.values[key] = value
        return True

    def incr(self, key, delta=1):
        self.values[key] += delta
        return self.values[key]


def test_rate_limit_rejects_calls_above_limit():
    cache = FakeCache()

    assert check_rate_limit(cache, "user-1", limit=2, window_seconds=60, now=1).allowed
    assert check_rate_limit(cache, "user-1", limit=2, window_seconds=60, now=2).allowed
    result = check_rate_limit(cache, "user-1", limit=2, window_seconds=60, now=3)

    assert not result.allowed
    assert result.retry_after == 57


def test_rate_limit_uses_independent_windows_and_subjects():
    cache = FakeCache()

    check_rate_limit(cache, "user-1", limit=1, window_seconds=60, now=1)

    assert check_rate_limit(cache, "user-2", limit=1, window_seconds=60, now=2).allowed
    assert check_rate_limit(cache, "user-1", limit=1, window_seconds=60, now=61).allowed
