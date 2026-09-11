"""The spend ledger's Postgres adapter: one row per authenticated email.

The only module in the app that talks to a database it does not own. It exists
so `spend.py` does not have to (R22.14) — the policy stays a pure function, and
swapping this for the provisioned-key implementation ADR-0210 declined is one
implementation of one two-method protocol.

```sql
create table spend (
  email      text primary key,
  total_usd  numeric(18, 12) not null default 0,
  updated_at timestamptz     not null default now()
);
```

Three things about the deployment are load-bearing:

* **The connection is Supabase's transaction pooler**, which multiplexes
  client connections over fewer server ones and therefore cannot support
  prepared statements — psycopg prepares any statement it sees six times, so
  `prepare_threshold=None` is not a tuning knob here but a correctness one.
* **Every turn pays a round trip**, and a store that hangs would hang the app
  rather than refuse it, so the connection carries its own timeout.
* **The increment happens in the database**, not in Python. Two containers may
  serve the same person at once (R22.11); a read-modify-write in this process
  would lose one of their turns, and the direction it loses in is the one that
  under-counts spend.
"""

from __future__ import annotations

import psycopg

from .authorization import normalise_email
from .spend import LedgerUnavailable

#: Seconds to wait for a connection before calling the store unreachable. A
#: turn refused because the ledger is down is recoverable; a turn that hangs
#: forever waiting for it is not, and the user cannot tell it from a slow model.
#:
#: **Known limitation, settled rather than open.** This is libpq's
#: *connection-establishment* timeout: it bounds the TCP/TLS handshake and
#: nothing after it. A pooler that accepts the connection and then stalls on
#: the query — how Supavisor behaves when it is out of upstream connections,
#: the likeliest failure on a free tier — leaves `execute` blocked, so the app
#: waits instead of refusing (R22.12 wanted a refusal).
#:
#: **The obvious fix does not work, and fails silently.** Passing
#: `options="-c statement_timeout=5000"` alongside these kwargs is accepted by
#: psycopg *and* by Supavisor — and then ignored: verified against this
#: deployment's live pooler on 2026-09-11, where the session came back
#: reporting `statement_timeout = 2min`, the role default, rather than the five
#: seconds requested, and with the `spend` table unchanged at zero rows
#: before and after — the condition the operator granted the check under.
#: Ineffective, not untried: do not spend an afternoon
#: re-attempting it. It is worth knowing *how* that was caught, because the
#: connection succeeding proves nothing — the probe read `SHOW
#: statement_timeout` back. "It connected" and "it worked" are different
#: claims, and shipping the parameter on the first would have put a comment
#: promising a five-second deadline over code that had none.
#:
#: **What protection exists today, and what it does not cover:** the role's
#: own default, measured at **2 minutes**. That bounds a query Postgres is
#: *executing* — a genuinely slow one — so the app hangs for two minutes
#: rather than five seconds.
#:
#: It does **not** bound the failure named above. `statement_timeout` is
#: enforced by a Postgres backend running a statement; when the pooler has no
#: upstream backend to hand out, nothing is executing and nothing is counting,
#: so a client waiting on Supavisor for a connection is not covered by it.
#: Bounding *that* needs a deadline on this side of the wire, not the
#: database's. Unmeasured — the pooler's host does not resolve from the
#: development sandbox — so it is recorded as a gap to check rather than a
#: proven one, but do not read the two minutes as covering it.
#:
#: Whether any of this is acceptable is the operator's call, which is why it
#: is written here as numbers rather than as a verdict.
#:
#: **If a shorter deadline is wanted**, the three candidates and their costs:
#:
#: * `ALTER ROLE <app role> SET statement_timeout = '5s'` — **recommended.**
#:   It is the only one that works without changing how this adapter issues
#:   queries, and it cannot be silently ignored the way the startup parameter
#:   was. Its cost is that it is a change to the operator's *database* rather
#:   than to this code, and it applies to every session of that role, so it
#:   wants a role dedicated to this app rather than a shared one.
#: * `SET LOCAL statement_timeout` inside an explicit transaction — under this
#:   code's control and survives transaction-mode pooling, but it means giving
#:   up `autocommit` and issuing two statements per operation, so every read
#:   and write in this file changes shape for a timeout.
#: * A plain `SET statement_timeout` — does **not** survive transaction-mode
#:   pooling: the session is handed to another client between transactions, so
#:   the setting is neither reliably applied nor reliably yours.
#:
#: Whichever is chosen, verify it by reading `SHOW statement_timeout` back on a
#: real connection. That is the only step that would have caught this one.
CONNECT_TIMEOUT_SECONDS = 5

_READ = "SELECT total_usd FROM spend WHERE email = %s"

_ACCRUE = (
    "INSERT INTO spend (email, total_usd) VALUES (%s, %s) "
    "ON CONFLICT (email) DO UPDATE "
    "SET total_usd = spend.total_usd + EXCLUDED.total_usd, updated_at = now()"
)


class PostgresLedger:
    """The spend port (`total` / `record`), backed by a hosted Postgres.

    `connect` is injectable for the same reason every other client here accepts
    one: the tests must never need a database or a credential (R22.15).
    """

    def __init__(self, connection_string, connect=None):
        self._connection_string = connection_string
        self._connect = connect or psycopg.connect

    def _connection(self):
        # `prepare_threshold=None` — see the module docstring; against a
        # transaction pooler the default silently starts failing once a
        # statement has been used a few times, which looks like an intermittent
        # outage rather than a configuration error.
        return self._connect(
            self._connection_string,
            autocommit=True,
            prepare_threshold=None,
            connect_timeout=CONNECT_TIMEOUT_SECONDS,
        )

    def total(self, email) -> float:
        """Lifetime USD spent by `email`; 0.0 for an address never billed."""
        try:
            with self._connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(_READ, (normalise_email(email),))
                    row = cursor.fetchone()
        except Exception as exc:
            raise LedgerUnavailable(f"cannot read the spend ledger: {exc}") from exc
        # `numeric` arrives as a Decimal; the policy compares floats, and a
        # mixed comparison is the kind of thing that works until it doesn't.
        return float(row[0]) if row and row[0] is not None else 0.0

    def record(self, email, amount) -> None:
        """Add `amount` USD to `email`'s lifetime total, atomically."""
        try:
            with self._connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(_ACCRUE, (normalise_email(email), float(amount)))
        except Exception as exc:
            raise LedgerUnavailable(f"cannot write the spend ledger: {exc}") from exc
