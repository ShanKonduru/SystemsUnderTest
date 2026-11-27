---
order: 32
title: "test_statements.py passes once, then fails on every run after"
labels: [rca-lab, bug]
---
**Reported by:** Tomas Varga, QA Automation

**Version:** billing service 1.4.0

### What happened

On my machine the statement tests pass the first time after a fresh clone, and fail on every run after that,
without any change to the code:

```
> python -m pytest -q tests/test_statements.py
.....                                                                    [100%]
5 passed

> python -m pytest -q tests/test_statements.py
..F..                                                                    [100%]
_______________________________ test_statement ________________________________
    def test_statement(service: TestClient) -> None:
        body = service.get("/customers/C-1001/statement", params={"month": "2026-11"}).json()
>       assert (body["customer_id"], body["name"], body["month"], body["amount_due"]) == (
            "C-1001", "Asha Rao", "2026-11", "120.63")
E       AssertionError: assert ('C-1001', 'A...6-11', '0.00') == ('C-1001', 'A...11', '120.63')
E         At index 3 diff: '0.00' != '120.63'
FAILED tests/test_statements.py::test_statement
1 failed, 4 passed
```

CI is green on every run. Two other people on the team see the same thing locally. Is the statement amount wrong,
or is it the test?

### Expected

The test suite gives the same result every time it is run.
