# ADR 0004: Login delay instead of lockout, and trusted proxies for the client address

Status: accepted. Implemented in `services/login_guard.py`, `services/client_address.py` and `core/settings.py`.

## Context

The first version of the login limit locked an account, and an address, for a window after N failures. Two problems:

- **Lockout is an attack surface.** Anyone who knows an email address can lock its owner out for the whole window, from
  any address. A hard refusal also tells an attacker exactly when the limit is reached.
- **The per-address limit did not work behind a proxy.** In development the Vite proxy and in Docker the nginx
  container connect to the API, so every browser looked like the proxy's address. The per-address count then covered
  all users together: one user's failures locked everyone out, and no real client was counted separately.

## Decision 1: a delay per (account, address) pair, and a higher ceiling per address

Failures are counted under two keys, never under the account alone:

- **The pair (account, client address).** The first `LOGIN_FREE_FAILURES` (default 3) failures of a pair are free. After
  that, the wait before the pair's next attempt doubles with each failure: `LOGIN_BASE_DELAY_SECONDS` (default 2), then
  4, 8, ... capped at `LOGIN_MAX_DELAY_SECONDS` (default 900). A success by the pair clears its count.
- **The address alone, across every account.** A higher ceiling, `LOGIN_ADDRESS_FREE_FAILURES` (default 20), with the same
  wait rule. It stops one machine from trying many accounts quickly. A success does not clear it.

Why the pair and not the account: a count per account lets anyone who knows an email address lock its owner out, from
any address, for as long as they keep failing. With the pair, a third party's failures count only for their own address;
the owner, from their own address, is not slowed down. Guessing from one address is still slow, because the pair and the
address ceiling both grow the wait.

An attempt made before the longer of the two waits is over is refused with 429 and `retry_after_seconds`. The right
password is refused too, because the check happens before the password is compared, so the delay cannot be bypassed by
guessing right. A quiet period of `LOGIN_FORGET_SECONDS` (default 3600) without failure forgets a count.

Trade-off: a delay does not stop a patient attacker who rotates addresses. It makes online guessing slow (at the cap, one
attempt every 15 minutes per pair) and makes the owner's own login work from their own address without waiting for someone
else's failures. Stopping rotation completely needs per-account signals (for example a notice to the owner), which are not
built.

## Decision 2: X-Forwarded-For only from a trusted proxy

`X-Forwarded-For` is believed only when the connection's own peer is listed in `TRUSTED_PROXIES` (IPs or CIDR networks,
comma-separated). Otherwise the connection's address is used and the header is ignored.

When the peer is trusted, the list is read from the right: each proxy appends the address it received the request from,
so the first entry from the right that is not a trusted proxy is the client. Entries on the left were written by the
sender and are never used. A malformed entry in `TRUSTED_PROXIES` fails at startup.

- **nginx** (the frontend container): `$proxy_add_x_forwarded_for` already appends the real address.
- **Vite** (development): the dev proxy must send the header. `vite.config.ts` sets `xfwd: true`.
- **Direct connections** (the API port published on the host): leave `TRUSTED_PROXIES` empty. Setting it to a Docker
  network range would trust every host that can reach the published port, so the header becomes spoofable from there.

## Consequences

- The per-address limit now counts real clients behind the Vite and nginx proxies, when `TRUSTED_PROXIES` lists them.
- State stays in memory, per process. With several API workers the effective limit multiplies, as for the stream
  tickets (ADR 0003). The fix is a shared store, decided with the multi-worker deployment.
- Behaviour changes: nothing is locked for a whole window any more. A refused attempt carries `retry_after_seconds`,
  which grows with each failure of the pair. Clients that read it can show the wait; the frontend shows the rate-limit
  message.
- The first version counted per account and per address and refused everything for a window. It is replaced: that
  design let a third party lock someone else out.
