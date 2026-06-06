"""Feature builders (Task 6.4). Pure functions: history + `as_of` -> feature rows.

No leakage (AD-6.6): a builder reads ONLY rows strictly before `as_of`, so a feature
can never peek at the future it is meant to predict. Lebanese seasonality lives here
(day-of-week, Ramadan, summer-mountain, payday/month-end). Populated in Task 6.4.
"""
