---
order: 6
title: "Holiday calendar puts Thanksgiving 2028 on November 30"
labels: [rca-lab, bug]
---
**Reported by:** Marcus Bell, Billing Operations (mail-run planning)

**Version:** billing service 1.2.0

### What happened

I'm building the 2027-2028 mailing calendar from `GET /calendar/holidays` so renewal-notice runs don't land on
federal holidays. For 2028 it gives the wrong Thanksgiving:

```
GET /calendar/holidays?year=2028
...
{"date": "2028-11-10", "name": "Veterans Day"},
{"date": "2028-11-30", "name": "Thanksgiving Day"},
{"date": "2028-12-25", "name": "Christmas Day"}
```

Thanksgiving is the **fourth** Thursday of November: in 2028 that's **November 23**. November 30 is the fifth
Thursday. 2026 and 2027 look right (Nov 26 and Nov 25).

This matters beyond my calendar: the same holiday list decides when renewal notices go out (they skip holidays), so
notices due on Nov 23, 2028 would be mailed on Thanksgiving, and ones due Nov 30 would be held back a day for no
reason.

### Expected

`{"date": "2028-11-23", "name": "Thanksgiving Day"}`

<!-- rca-lab:comment -->
**Marcus Bell:** One more data point. Our IT team says the holiday logic comes from a library we copy into the
service, and they found this in that library's upstream release notes:

> **holidaycal 1.3.1 (2026-10-19)**
> - Fixed: Thanksgiving Day was computed as the last Thursday of November instead of the fourth, wrong in years
>   where November has five Thursdays (regression in 1.3.0).

Not sure which version we have.
