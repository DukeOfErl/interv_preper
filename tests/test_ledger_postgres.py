"""The spend ledger's Postgres adapter, tested without a Postgres (R22.15).

The adapter is small and almost all of it is assumptions about someone else's
deployment, which is exactly the code whose defects look like nothing:

  * a ledger keyed on the raw address instead of the allowlist's comparison
    form gives one person two rows and twice the cap, and every single-user
    test still passes;
  * a `record` that *replaces* instead of *adding* resets the total on every
    turn, which reads as "the cap never triggers" months later;
  * prepared statements against Supabase's transaction pooler fail only once a
    statement has been used a few times, so it presents as an intermittent
    outage rather than as a configuration error.

The connection factory is injected, so nothing here opens a socket or needs a
credential.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from interview_prep.ledger_postgres import PostgresLedger
from interview_prep.spend import LedgerUnavailable


class FakeCursor:
    def __init__(self, row):
        self.row = row
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, statement, parameters):
        self.executed.append((statement, parameters))

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, row=None):
        self.cursor_object = FakeCursor(row)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self.cursor_object


class FakeConnect:
    """Stands in for `psycopg.connect`, remembering how it was called."""

    def __init__(self, row=None, fail=False):
        self.row = row
        self.fail = fail
        self.kwargs = None
        self.connection = None

    def __call__(self, connection_string, **kwargs):
        if self.fail:
            raise OSError("connection refused")
        self.connection_string = connection_string
        self.kwargs = kwargs
        self.connection = FakeConnection(self.row)
        return self.connection


def ledger(row=None, fail=False):
    connect = FakeConnect(row=row, fail=fail)
    return PostgresLedger("postgresql://pooler.example", connect=connect), connect


# --- R22.1: what the store is asked ----------------------------------------


def test_an_address_never_billed_has_spent_nothing():
    store, _ = ledger(row=None)
    assert store.total("nobody@example.com") == 0.0


def test_a_numeric_column_arrives_as_a_float():
    """`numeric` comes back as a `Decimal`, and the policy compares it against
    a float estimate. Mixing them works until one comparison doesn't.
    """
    store, _ = ledger(row=(Decimal("1.250000"),))
    assert store.total("candidate@example.com") == pytest.approx(1.25)
    assert isinstance(store.total("candidate@example.com"), float)


def test_the_row_is_keyed_on_the_allowlist_comparison_form():
    """`authorize()` matches addresses stripped and lowercased. A ledger that
    keyed on the raw string would bill `Candidate@Example.com` separately from
    `candidate@example.com` — one person, two rows, twice the cap.
    """
    store, connect = ledger(row=None)
    store.total("  Candidate@Example.COM ")
    _, parameters = connect.connection.cursor_object.executed[0]
    assert parameters == ("candidate@example.com",)


def test_a_recorded_amount_is_added_to_the_stored_total():
    """Not replaced. The ledger is a lifetime accumulation (R22.1), and the
    addition happens in the database rather than in this process because two
    containers may be serving the same person at once (R22.11).
    """
    store, connect = ledger()
    store.record("Candidate@Example.com", 0.25)
    statement, parameters = connect.connection.cursor_object.executed[0]
    assert parameters == ("candidate@example.com", 0.25)
    assert "ON CONFLICT" in statement
    assert "spend.total_usd + EXCLUDED.total_usd" in statement


# --- the deployment's own constraints ---------------------------------------


def test_prepared_statements_are_disabled():
    """Supabase's transaction pooler cannot serve them, and psycopg prepares
    any statement it has seen a handful of times — so the failure appears
    later than the change that caused it.
    """
    store, connect = ledger(row=None)
    store.total("candidate@example.com")
    assert connect.kwargs["prepare_threshold"] is None


def test_the_connection_cannot_hang_forever():
    """A refused turn is recoverable; a turn blocked on a dead host is not,
    and the user cannot tell it from a slow model.
    """
    store, connect = ledger(row=None)
    store.total("candidate@example.com")
    assert connect.kwargs["connect_timeout"] > 0


# --- R22.12: an unreachable store says so, in the port's own language -------


def test_an_unreachable_store_raises_ledger_unavailable_on_read():
    store, _ = ledger(fail=True)
    with pytest.raises(LedgerUnavailable):
        store.total("candidate@example.com")


def test_an_unreachable_store_raises_ledger_unavailable_on_write():
    """Both operations, because the policy fails closed on a store it cannot
    read and the caller must be able to tell that a write was lost too — a
    driver exception escaping raw would be caught by whichever caller happened
    to be first, which is the shape this port exists to prevent.
    """
    store, _ = ledger(fail=True)
    with pytest.raises(LedgerUnavailable):
        store.record("candidate@example.com", 0.25)
