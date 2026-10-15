"""A configured tier row, shared by the tests that dispatch through one."""

AT = "2026-01-08T12:00:00+00:00"


def tier_row():
    return {"model": "sonnet-5", "effort": "high"}
