---
order: 8
title: "test_every_unpaid_installment_gets_a_reminder fails when run on its own"
labels: [rca-lab, bug]
---
**Reported by:** Sam Rivera, QA Automation

**Version:** main at billing service 1.2.0

### What happened

The full suite is green in CI, but when I run this one test by itself (re-running a failure from a split CI shard),
it fails:

```
> python -m pytest tests/test_reminders.py::test_every_unpaid_installment_gets_a_reminder
============================= test session starts =============================
platform win32 -- Python 3.11.9, pytest-9.2.0, pluggy-1.7.0
rootdir: D:\work\SystemsUnderTest\billing-service
configfile: pyproject.toml
plugins: anyio-4.16.0, cov-7.1.0
collected 1 item

tests\test_reminders.py F                                                [100%]

================================== FAILURES ===================================
________________ test_every_unpaid_installment_gets_a_reminder ________________

client = <starlette.testclient.TestClient object at 0x0000021634218C50>

    def test_every_unpaid_installment_gets_a_reminder(client: TestClient) -> None:
>       unpaid = [i["number"] for i in SCHEDULE["P-5001"] if i["paid_on"] is None]
                                       ^^^^^^^^^^^^^^^^^^
E       KeyError: 'P-5001'

tests\test_reminders.py:55: KeyError
=========================== short test summary info ===========================
FAILED tests/test_reminders.py::test_every_unpaid_installment_gets_a_reminder
======================== 1 failed, 1 warning in 0.92s =========================
```

`python -m pytest tests/test_reminders.py` (the whole file) passes. We're about to start running the suite in
parallel shards, where this will fail at random.

### Expected

Every test passes when run alone, in any order and in any shard.
