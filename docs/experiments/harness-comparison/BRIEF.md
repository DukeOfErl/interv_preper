# WP4 implementation brief

**This file is the complete instruction given to each implementation arm. It is
identical for every arm by construction: drive from this file, do not retype or
summarise it.**

You have no conversation history. Everything you need is here or in the
documents it names.

## Goal

Implement the per-identity spend cap specified in `docs/REQUIREMENTS.md` § 22,
with the rationale in `docs/decisions/0210-hosted-postgres-ledger-for-the-spend-cap.md`.

**Done means:** `uv run pytest` is green, including `tests/test_spend_cap.py`,
which currently fails on a missing module. The app runs, and a `user` who has
exhausted their budget is refused with a message distinguishable from the one
shown when the ledger is unreachable.

## The contract

`tests/test_spend_cap.py` is the specification of the API. **Read it first, and
do not edit it.** If a test looks wrong, say so and stop — do not adjust the
test to match an implementation.

Create `interview_prep/spend.py` exporting:

```python
class LedgerUnavailable(Exception): ...   # an adapter raises this when the store is unreachable
class OverBudget(Exception): ...          # the guard raises this

@dataclass(frozen=True)
class CapDecision:
    allowed: bool
    reason: str | None        # None when allowed; "at_cap" or "ledger_unavailable"
    remaining: float | None

def decide(*, role, spent, cap, estimate) -> CapDecision      # pure policy
def check_budget(identity, *, ledger, cap, estimate) -> CapDecision
def require_within_budget(identity, *, ledger, cap, estimate) -> None

class InMemoryLedger:         # total(email) -> float ; record(email, amount) -> None
```

**`spend.py` must import no Streamlit and no database driver** — a test asserts
this (R22.14). The Postgres adapter therefore lives in its own module, e.g.
`interview_prep/ledger_postgres.py`, and satisfies the same two-method protocol.
Putting the driver in `spend.py` will fail the suite.

## What the work actually involves

The module above is the easy half. These are the parts that are easy to miss,
and each is a requirement rather than a suggestion:

1. **R22.2 — count all six paid clients.** The app builds six paid clients
   (agent stream, web research, pre-send guardrail, query condenser, and two
   embedding paths). Today only the first two accrue any cost:
   `chat_bot.py` adds `llm.last_cost + llm.extra_cost` to `total_cost`. The
   guardrail, the query condenser and both embedding paths report no usage at
   all. **Closing that gap is part of this work.** A cap fed by two of six is
   not a cap.
2. **R22.4 — a refused turn still records what it spent.** The guardrail runs
   before the reply and costs money whether or not the prompt is allowed;
   today a blocked turn calls `st.stop()` before any accrual.
3. **R22.5 — the guard belongs inside the operation.** Follow the existing
   pattern: `authorization.require_authorized` is called inside each paid
   client, not only in the page, because `evals/` and `tests/` reach those
   clients without passing through `chat_bot.py`. See
   `tests/test_spend_is_guarded.py`, which enforces this for authorization and
   is the model for how the cap should be enforced.
4. **R22.11 — the store is the source of truth**, read and written every turn.
   An in-process total is a cache, never the authority.
5. **R22.17 — the sidebar shows remaining budget** for a capped role.

## Configuration

`.streamlit/secrets.toml` already has the block, and the table already exists:

```toml
[spend]
connection_string = "postgresql://...pooler.supabase.com:6543/postgres"
cap_usd = 5.00
```

```sql
create table spend (
  email      text primary key,
  total_usd  numeric(12, 6) not null default 0,
  updated_at timestamptz     not null default now()
);
```

Notes that will otherwise cost you time:

- The connection is Supabase's **transaction pooler**, which does **not support
  prepared statements**. Configure the driver accordingly.
- Add the driver to `pyproject.toml` and run `uv sync`.
- **Email is normalised** (stripped, lowercased) before it touches the ledger —
  `authorization.authorize` matches addresses case-insensitively, so a ledger
  that does not would give one person two rows and twice the cap.
- Tests must never need the database or the network (R22.15).

## Constraints

- **Use absolute paths in shell commands; never `cd`.** A `cd` followed by a
  relative path is undecidable against this project's permission rules and will
  block on a prompt.
- Do not edit `docs/REQUIREMENTS.md`, the ADRs, or any existing test.
- Match the surrounding style: this codebase comments *why*, not *what*.
- Verify with `uv run pytest` from the repository root. The suite takes about
  two and a half minutes; do not conclude from a subset.

## If you get stuck

Report the blocker and stop. Do not weaken a test, skip a requirement silently,
or implement a narrower feature and describe it as complete.
