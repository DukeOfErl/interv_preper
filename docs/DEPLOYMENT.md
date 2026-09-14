# Deploying to Streamlit Community Cloud

Written against a real deployment of a sibling project on this account, plus
the checks noted inline. Everything here was verified rather than remembered;
where it was not, it says so.

## Before you click Deploy — the choices you cannot take back

| Setting | Value | Why it matters |
|---|---|---|
| **Python version** | **3.12** | Under **Advanced settings**, *before* deploying. It cannot be changed afterwards without deleting the app and redeploying it. |
| **Main file path** | `chat_bot.py` | Not `main.py`. |
| **Branch** | `main` | Plain branch name — no `refs/heads/`, no `origin/`, no trailing slash. |

## Dependencies: nothing to do

Cloud installs from `pyproject.toml` + `uv.lock` directly. **Do not add a
`requirements.txt`** — a sibling project on this account deploys from exactly
this layout with no requirements file, and adding one would create a second
source of truth that silently diverges from the lock file.

`psycopg[binary]` matters here: the `[binary]` extra ships a prebuilt libpq, so
no `packages.txt` and no system package installation is needed.

## Secrets

Everything in `.streamlit/secrets.toml` goes into **Advanced settings →
Secrets**, pasted as TOML. The file itself is gitignored and never deploys.

**The ordering trap.** TOML section headers capture everything below them, and
Streamlit promotes only **top-level scalar** secrets into `os.environ` (checked
in `streamlit/runtime/secrets.py`, 1.58). So `OPENROUTER_API_KEY` must sit
**above every `[section]` header**:

```toml
# Top level FIRST — these become environment variables.
OPENROUTER_API_KEY = "sk-or-..."
GITHUB_PAT = "ghp_..."          # optional; omit to run without GitHub tools

[auth]
redirect_uri = "https://<your-app>.streamlit.app/oauth2callback"
cookie_secret = "..."
client_id = "..."
client_secret = "..."
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"

[roles]
"you@example.com" = "dev"
"candidate@example.com" = "user"

[spend]
connection_string = "postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres"
cap_usd = 5.00
```

Put `OPENROUTER_API_KEY` under `[auth]` by accident and it becomes
`st.secrets["auth"]["OPENROUTER_API_KEY"]`, is never promoted, and the app
stops at "Enter your OpenRouter API key" with no other clue. `chat_bot` also
reads the key straight from `st.secrets` as a fallback, so this fails loudly
rather than depending on which code path touched secrets first — but the
top-level placement is still the intended one.

## The redirect_uri chicken-and-egg

Google requires the **exact** redirect URI to be registered in advance, and you
do not know the app's URL until it exists. So the order is:

1. Deploy. The app will start and **fail at sign-in** — expected.
2. Copy the real URL from the browser, e.g. `https://interv-preper.streamlit.app`.
3. Google Cloud Console → APIs & Services → Credentials → your OAuth client →
   **Authorized redirect URIs** → add `https://<that-url>/oauth2callback`.
   Keep the `http://localhost:8501/oauth2callback` entry so local still works.
4. Update `redirect_uri` in the Cloud secrets to match, exactly.
5. Reboot the app (secrets changes need a restart).

A mismatch fails at Google with `redirect_uri_mismatch`, not in this app, so
there is nothing in the Streamlit logs to find.

## Supabase

- **The pooler string is not optional.** Use the `:6543` `...pooler.supabase.com`
  URI. Supabase's direct connection (`db.<ref>.supabase.co:5432`) is IPv6-only
  for projects created recently, and Streamlit Cloud is IPv4 — the direct
  string fails to connect at all from there.
- **The password must be percent-encoded** if it contains URI-reserved
  characters (`@` → `%40`, `:` → `%3A`, and so on). A raw `@` splits the URI
  early, and libpq then reports `failed to resolve host` naming a host built
  from the tail of the password — which reads as a DNS or region fault.
- **`total_usd` must be `numeric(18, 12)`.** At `numeric(12, 6)` a query
  embedding's $0.0000008 rounds toward zero, so the two embedding paths R22.2
  exists to count would record nothing. To widen an existing table:
  `alter table spend alter column total_usd type numeric(18, 12);`

### The free tier pauses, and this app fails closed

Supabase pauses a free-tier project after **7 days of inactivity**. R22.12
refuses every turn when the ledger is unreachable, so a paused project means
**the app serves nobody** until someone unpauses it from the dashboard. Users
see the deployment-fault wording (R22.13), not a spend limit, which is correct
but still a dead link.

This is the accepted cost of the fail-closed choice in ADR-0210 — the
alternative silently uncaps every account at the moment nobody is watching.
For a portfolio app that sits idle between demos, plan to open the app (or the
Supabase dashboard) before showing it to anyone.

## What resets when a container recycles, and what does not

Cloud recycles containers freely. That is the premise ADR-0210 was written on.

**Resets:** session state, the chat transcript, uploaded documents and their
index (already R15.4 — session-scoped by design).

**Does not reset:** the spend ledger. That is the entire point of WP4 — a cap
that reset on recycle would not be a cap, and the reset would be invisible.

**Rebuilt on first use:** `data/knowledgebase.db` is derived and gitignored, so
it does not ship. The seeds in `knowledgebase/*.md` do, and the database is
re-embedded from them on the first session after each restart. Measured today:
**~3,300 tokens, about $0.0001** — noise against a $5 cap.

Two honest caveats. That cost is **billed to the first user who signs in after a
restart**, because the sync runs with their identity and their budget; and it
**scales with the knowledge base**, which §17 expects to grow. At the current
size it is not worth engineering around. If the knowledge base grows by an
order of magnitude or two, attribute the sync to the operator instead of to
whoever happened to arrive first.

## The live deployment

Deployed at <https://interv-preper.streamlit.app/> from `DukeOfErl/interv_preper`,
branch `main`, Python 3.12, main file `chat_bot.py`.

Both `redirect_uri` snags in this document were hit on the first deploy, in the
order written: the Cloud secret still carried the `PLACEHOLDER` host, and once
that was corrected the real URI was not yet registered with Google. Google's
error page is the only place that says which — **click "see error details" and
read the `redirect_uri=` value it reports**. That single string identifies which
side is wrong and turns a guessing game into one edit.

## Pre-flight checklist

- [ ] Python 3.12 selected in Advanced settings (before deploying)
- [ ] Main file path is `chat_bot.py`
- [ ] `OPENROUTER_API_KEY` is above every `[section]` header in the secrets
- [ ] `[spend].connection_string` uses port `6543` and the `pooler` host
- [ ] The database password is percent-encoded
- [ ] `total_usd` is `numeric(18, 12)`
- [ ] Your own address is in `[roles]` as `dev` — you are not exempt (R21.10)
- [ ] After the first deploy: the real URL is registered in Google Cloud **and**
      written into `[auth].redirect_uri`, then the app rebooted
- [ ] Sign in as a `user`-role address once and confirm the sidebar shows a
      budget, then that the cap refuses a turn when exhausted

## Verifying the deployment actually holds

The suite cannot check any of this — it runs with no database and no login by
design (R21.18, R22.15). After deploying, check the two things that are silent
when wrong:

1. **Spend is being recorded.** Take a turn, then in Supabase:
   `select * from spend;` — a row should exist for your address, and grow.
   An empty table after a real turn means the ledger is being written by
   nobody, which the app will not tell you.
2. **The cap refuses.** Set `cap_usd` to something tiny (`0.01`), reboot, sign
   in as a `user`-role address, and confirm the refusal appears and is worded
   as a spend limit rather than a deployment fault. Then put the real cap back.

A cap that has never been seen to refuse is a control that has never been shown
to hold.
