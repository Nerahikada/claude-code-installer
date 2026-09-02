# Why the installers seed `~/.claude.json`

`public/commands/install_linux.sh` and `public/commands/install_windows.ps1`
write an `oauthAccount` object into `~/.claude.json`, set root-level
`hasCompletedOnboarding: true` in the same file, and add a small `env`
block to `~/.claude/settings.json` before Claude Code first runs. The goal
is to keep user identifiers held in `oauthAccount` — email address,
display name, full name, organization name — out of the model's system
prompt. The macOS installer is not maintained by this change: it only
writes root-level `hasCompletedOnboarding: true` and does neither the
`oauthAccount` seed nor the `env` block.

Everything below was read out of the Claude Code binary at
`~/.local/share/claude/versions/2.1.250` (a Bun single-file executable that
embeds its JavaScript in plaintext). Identifiers are minified; snippets are
quoted verbatim. Where the doc pins on a specific behaviour, I checked it
survived from 2.1.246 through 2.1.250.

## The problem

Claude Code reads user identifiers from `~/.claude.json`'s `oauthAccount`
and can splice them into the model's system prompt. Today the only wired-up
identifier is the email address, injected on every session as a
`# userEmail` block inside a `<system-reminder>`:

```js
let a = V.ANTHROPIC_UNIX_SOCKET ? void 0 : by()?.emailAddress, l = await DYo(n);
return ..., {blocks:{ ...s&&{claudeMd:s},
  ...a&&{userEmail:`The user's email address is ${a}. Use it only to identify the user, ...`},
  ...l&&{attachedProject:l}, currentDate:`Today's date is ${V5()}.`}, memoryFiles:i}
```

`by()` resolves to a getter over the on-disk config, not over the credential
store:

```js
function Dn(){return Zt()?k().oauthAccount:void 0}   // k() reads ~/.claude.json
```

So the address in the prompt is exactly `~/.claude.json` →
`oauthAccount.emailAddress`. Blanking it removes the block, because the
object spread `...a&&{userEmail:...}` drops the key when `a` is falsy.

This is the only place any identifier from `oauthAccount` reaches the
model today. `displayName`, `fullName`, and `organizationName` live in the
same object and are read by other code paths (write-back comparisons,
`/status`) but never by the system-prompt builder — a future addition
would be a single object spread away. Seeding the whole identifier group
blank closes that door before it opens, and it costs nothing: none of
those fields are consulted by the auth path or the request envelope.

The email is not written to session transcripts either (verified by
grepping every `.jsonl` under `~/.claude/projects`), and neither the
request headers nor the `metadata.user_id` envelope carry it.

## What would restore it

Two endpoints can write `emailAddress` back to `~/.claude.json`. Both must be
blocked, or the address returns on the next startup.

**`GET https://api.anthropic.com/api/oauth/profile`** runs from `init()` on
every start and rewrites fourteen fields. It returns early when the account
already looks populated and the profile was fetched recently:

```js
let a=s.oauthAccount?.profileFetchedAt,u=a!==void 0&&Date.now()-a<XH;
if(s.oauthAccount&&s.oauthAccount.billingType!==void 0
  &&s.oauthAccount.accountCreatedAt!==void 0
  &&s.oauthAccount.subscriptionCreatedAt!==void 0
  &&s.oauthAccount.ccOnboardingFlags!==void 0&&u || !se() || !_t())return!1;
```

`XH` is `86400000`, a 24 hour TTL, and there is no upper bound on
`profileFetchedAt` — a timestamp in the future makes `Date.now()-a`
negative, which is always below the TTL. That is why the seed sets it to
the year 2100 and why the four fields named in the condition must be
present.

**`GET https://api.anthropic.com/api/claude_cli/bootstrap`** runs as a
startup prefetch and merges an `oauth_account` object from the response. It
bails out when the response's account UUID does not match the stored one:

```js
function ot(t,o){if(!t||!o)return t;
 if(o.account_uuid!=null&&o.account_uuid!==t.accountUuid)return t;
 let i={organizationType:o.organization_type??null,
        organizationRateLimitTier:o.organization_rate_limit_tier??null,
        userRateLimitTier:o.user_rate_limit_tier??null,
        seatTier:o.seat_tier??null};
 if(o.account_email!=null)i.emailAddress=o.account_email;
 if(o.organization_uuid!=null)i.organizationUuid=o.organization_uuid;
 if(o.organization_name!=null)i.organizationName=o.organization_name;
 return{...t,...i}}
```

The response really does carry the address — an authenticated request to
that endpoint returns:

```json
"oauth_account": { "account_uuid": "…", "account_email": "…",
                   "organization_uuid": "…", "organization_name": "…'s Organization", … }
```

A blank `accountUuid` in the stored config makes `o.account_uuid !== ""`
succeed and the whole merge is skipped. This is a single line of defence:
if a future server response were to drop `account_uuid` while still
including `account_email`, the guard would fall through and the address
would be written. Today's server always sends it, so the guard holds.

A separate call inside the same bootstrap handler runs *before* `ot()` and
copies the response into an in-process credential slot regardless of the
UUID guard:

```js
function ge(t){if(!t?.account_uuid)return;
 rt({accountUuid:t.account_uuid,emailAddress:t.account_email??void 0,
     organizationUuid:t.organization_uuid??void 0})}
```

`rt` writes to `credentialSlots.stampAuthenticatedAccount`, which is memory
only. It never touches `~/.claude.json`, so the prompt is safe, but the
address is briefly in the process. This matters for the OTel path below.

The four other writers cannot reintroduce the address on their own: the
roles endpoint writes only `organizationRole`/`workspaceRole`/`organizationName`
(the last one is server-generated as `"<email>'s Organization"`, which does
embed the address as a substring, but that field is only shown locally in
`/status` — see the field table); the token-refresh write-back omits it;
the `CLAUDE_CODE_USER_EMAIL` env seed only forms an account when the trio
`CLAUDE_CODE_USER_EMAIL` + `CLAUDE_CODE_ACCOUNT_UUID` +
`CLAUDE_CODE_ORGANIZATION_UUID` is all set (none are, in our install).
`/login` does rewrite everything from scratch, and that undoes the seed.

## Field by field

| Field | Value | Why |
| --- | --- | --- |
| `emailAddress` | `""` | The actual target. `by()?.emailAddress` becomes falsy and the `# userEmail` block is never built. |
| `accountUuid` | `""` | Trips the bootstrap merge guard. Most consumers read it through `\|\|`, `!`, or `??` and treat it as absent; two paths compare it by equality (owner pinning, usage-utilization cache) and get disabled rather than crash; the MCP discovery cache falls into a shared `acct:incomplete` bucket. |
| `organizationUuid` | `00000000-0000-0000-0000-000000000000` | Kept truthy on purpose. `aS()` returns the stored value when it is truthy and otherwise falls through to an HTTP profile lookup; a nil UUID keeps the fast path without asserting a real organization. It leaks to OTel as `organization.id` if the user opts in to third-party OpenTelemetry — the address does not, since `emailAddress` is blank. |
| `organizationName` | `""` | A user identifier. Not read by the system-prompt builder today, but the server value embeds the email address verbatim (`"<email>'s Organization"`), so any future path that spliced it in would leak the address too. Only other reader is `/status` / `claude auth status --json`. |
| `displayName`, `fullName` | `""` | User identifiers written by the profile fetch. Today only read by the write-back's own no-op comparison, but blanked so a future prompt path that reached for them would find nothing. |
| `billingType` | `""` | The refresh gate only checks presence. An empty string keeps the gate satisfied without claiming a plan. `/usage-credits` disappears as a side effect. |
| `accountCreatedAt` | `2024-01-01T00:00:00Z` | Required by the refresh gate, which only tests `!== void 0`. Never parsed. |
| `subscriptionCreatedAt` | `2024-01-01T00:00:00Z` | Same gate. Note that GrowthBook targeting will `Date.parse` this and label the machine as a "January 2024 subscriber" — harmless here because `DISABLE_TELEMETRY=1` turns GrowthBook off too. Change carefully. |
| `ccOnboardingFlags` | `{}` | Satisfies the refresh gate. Also read by the Pro-trial eligibility check (`ccOnboardingFlags?.e10 === true`), so `{}` means "ineligible" — irrelevant on Max/Team plans, which early-exit before this check. |
| `profileFetchedAt` | `4102444800000` | 2100-01-01Z. Freezes the 24 hour TTL as long as the credential file carries `subscriptionType` and `rateLimitTier`; if either is missing, a token refresh path (`Hg`) writes `Date.now()` back over it and the freeze breaks on the next start. |
| `hasCompletedOnboarding` (root of `~/.claude.json`, not inside `oauthAccount`) | `true` | Suppresses the interactive onboarding wizard on the first launch. The install script itself does not run `claude`, so this is not about the install being non-blocking — it is about the first hands-on `claude` invocation not stopping to onboard. Trust dialogs, Pro-trial screens, and Grove policy prompts are gated on other flags and can still appear. |

## Environment variables

`~/.claude/settings.json` gets a small `env` block:

```json
"env": {
  "DISABLE_TELEMETRY": "1",
  "DISABLE_ERROR_REPORTING": "1",
  "DISABLE_BUG_COMMAND": "1",
  "CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY": "1",
  "ENABLE_CLAUDEAI_MCP_SERVERS": "0",
  "DISABLE_GROWTHBOOK": "1"
}
```

Claude Code merges this into `process.env` at startup for `~/.claude/settings.json`
scope without further filtering.

`DISABLE_TELEMETRY=1` picks the `"no-telemetry"` traffic mode and drops
first-party telemetry entirely. `accountUuid`, `organizationUuid`, and
subscription tier are attributes on those events, so blocking the pipeline
is what stops them from being sent. It also disables GrowthBook feature
flags as a side effect, since GrowthBook rides the same traffic channel:

```js
function x(){if(process.env.CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC)return"essential-traffic";
             if(process.env.DISABLE_TELEMETRY)return"no-telemetry";
             if(Pe(process.env.DO_NOT_TRACK))return"no-telemetry";
             return"default"}
```

`DISABLE_ERROR_REPORTING=1` drops the crash-report pipeline; the traffic
mode selector treats it independently of `DISABLE_TELEMETRY`.

`DISABLE_BUG_COMMAND=1` retires `/bug`; the prompt for it becomes an
explicit `disabled` state that names the env var (the disabled-message
string itself uses the alias `/feedback`).

`CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY=1` retires the end-of-session quality
prompt that would otherwise ask the user to rate the last turn.

`ENABLE_CLAUDEAI_MCP_SERVERS=0` turns off the claude.ai connectors. The
name reads backwards: the value is parsed by a negation helper, so a
"disabled" string is what switches the feature off. `1` or leaving it
unset both mean "not disabled".

```js
function A(e){if(e===void 0)return!1;if(typeof e==="boolean")return!e;
 let n=String(e).toLowerCase().trim();return["0","false","no","off"].includes(n)}
// caller: let r=lu(process.env.ENABLE_CLAUDEAI_MCP_SERVERS),o=G1e();
//         if(r||o)return b(`[claudeai-mcp] Disabled via ${r?"env var":"disableClaudeAiConnectors setting"}`),…,{};
```

`DISABLE_GROWTHBOOK=1` is a belt over the suspenders. GrowthBook is already
off because it rides the `no-telemetry` channel that `DISABLE_TELEMETRY=1`
selects, but the flag is checked at its own call site as well, so setting
it explicitly keeps GrowthBook disabled even if a future release rewires
the feature-flag pipeline off the telemetry channel.

An earlier draft used `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` instead
of `DISABLE_TELEMETRY=1`. That single variable also blocks the bootstrap
request outright — but it happens to disable the auto-updater and the
mandatory Consumer-Terms notice as well, along with the model-list refresh
and several other features that are unrelated to the privacy target. The
current split gives up bootstrap-blocking as a belt (leaving the
`accountUuid` guard as the only defence there) in exchange for keeping
those unrelated features working.

## What this does not change

Authentication is untouched. The credential store (`~/.claude/.credentials.json`)
is the sole source of the access token; nothing in the auth path reads
`oauthAccount`. Blanking those fields does not affect signing in or making
requests.

Requests still carry an account field in their metadata envelope, which
falls back to an empty string once the config value is blank:

```js
account_uuid: jr(V.CLAUDE_CODE_REMOTE)&&V.CLAUDE_CODE_ACCOUNT_UUID
  ||OWr()?.accountUuid||by()?.accountUuid||""
```

If the user opts in to third-party OpenTelemetry
(`CLAUDE_CODE_ENABLE_TELEMETRY=1`), a separate exporter reads the
`credentialSlots` in-memory account — the one `ge()` stamps from the
bootstrap response — and can emit `user.email` there. `DISABLE_TELEMETRY`
does not gate this exporter. If OTel is on, expect the address to leave
through that channel.

## Trade-offs

- `/usage-credits` disappears and the rate-limit message changes to
  "Switch models to keep working." — the command is gated on `billingType`
  matching one of the four subscription values.
- `/status` shows blank rows for email and organization; `claude auth
  status --json` returns empty strings for those fields and the literal
  nil UUID for `orgId`.
- Remote-control and browser-bridge owner pinning are disabled.
- The MCP discovery cache falls into a shared "incomplete" bucket rather
  than being scoped per account. Different accounts still cannot collide,
  because the caches are per-user in the first place.
- claude.ai connectors (Gmail, Calendar, Drive, Notion) go away. Local
  `.mcp.json` and `claude mcp add` servers are unaffected.
- `/bug` and the end-of-session rating prompt are removed.
- First-party telemetry and crash reporting are off. GrowthBook feature
  flags fall back to their built-in defaults; a few features that require
  server-side flag evaluation (Remote Control among them) print a message
  naming the env var.
- `/login` clears `oauthAccount` and repopulates it from the profile
  endpoint, undoing the seed. `hasCompletedOnboarding` and the `settings.json`
  env block survive, so the next `/login` puts identity back but leaves
  the rest of the config alone.

## Verifying

Start Claude Code once, wait a couple of seconds for the fire-and-forget
profile fetch to settle, then look at both the address and the freeze
timestamp:

```bash
python3 -c "import json,os,pathlib;
d=json.loads(pathlib.Path('~/.claude.json').expanduser().read_text())
a=d.get('oauthAccount',{})
print('emailAddress    :', repr(a.get('emailAddress')))
print('profileFetchedAt:', a.get('profileFetchedAt'))
print('displayName     :', repr(a.get('displayName')))"
```

`emailAddress` empty and `profileFetchedAt` still `4102444800000` means
both writers stayed silent. If `profileFetchedAt` has moved but the
address is still empty, the token-refresh write-back fired (`displayName`
will likely have flipped to a real value at the same time) — the freeze
has broken and the address will be back on the next start unless the
credential file gets `subscriptionType`/`rateLimitTier` restored.

If the address itself has come back, use `--debug` and look for
`[claudeai-mcp] Disabled via env var` in `~/.claude/debug/<session>.txt`
to confirm the settings file was read at all. A missing line means the
env block did not apply and the whole seed likely got overwritten from
`/login` or a similar reset path.

`CLAUDE_CONFIG_DIR`, if set, moves both files to that directory instead
of `~`.

## Version caveat

Verified against 2.1.250. The minified identifiers (`by`, `ot`, `foe`,
`XH`, …) are build artifacts and rotate on almost every release; the JSON
key names, endpoint paths, environment variable names, and traffic-mode
selector are the stable parts. Re-check after a major version bump.
