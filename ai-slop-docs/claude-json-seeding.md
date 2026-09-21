# Why the hardening policy sets what it sets

Before Claude Code first runs, the policy writes an `oauthAccount` object and `hasCompletedOnboarding: true` into `~/.claude.json`, and an `env` block plus a set of top-level flags into `~/.claude/settings.json`.
Two goals:

1. Keep the user identifiers held in `oauthAccount` — email address, display name, full name, organization name — out of the model's system prompt.
2. Switch off the features that move local data to claude.ai.

This document says what each key does and why it was picked over the alternatives.
The values themselves live in `policy/policy.json`.
The code behind every claim is in `claude-code-internals.md`: snippets, minified names, the version caveat, and the greps that re-run the audit against a new build.

## What gets set

| File | Group | Count | Covered in |
| --- | --- | --- | --- |
| `~/.claude.json` | `oauthAccount` fields | 11 | Part 1 |
| `~/.claude.json` | `hasCompletedOnboarding` (root) | 1 | Part 1 |
| `~/.claude/settings.json` | `env` variables | 7 | Part 2 |
| `~/.claude/settings.json` | top-level keys | 12 | Part 2 |

```jsonc
// ~/.claude.json
"hasCompletedOnboarding": true,
"oauthAccount": {
  "accountUuid": "",  "emailAddress": "", "displayName": "", "fullName": "",
  "organizationName": "", "billingType": "",
  "organizationUuid": "00000000-0000-0000-0000-000000000000",
  "accountCreatedAt": "2024-01-01T00:00:00Z",
  "subscriptionCreatedAt": "2024-01-01T00:00:00Z",
  "ccOnboardingFlags": {},
  "profileFetchedAt": 4102444800000
}

// ~/.claude/settings.json
"env": {
  "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1",
  "DISABLE_BUG_COMMAND": "1", "CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY": "1",
  "ENABLE_CLAUDEAI_MCP_SERVERS": "0", "DISABLE_GROWTHBOOK": "1",
  "CLAUDE_CODE_DISABLE_ORG_MEMORY": "1"
},
"disableRemoteControl": true,        "enableArtifact": false,
"syncClaudeAiSkills": false,         "syncClaudeAiPlugins": false,
"disableClaudeAiConnectors": true,   "autoUploadSessions": false,
"channelsEnabled": false,            "inputNeededNotifEnabled": false,
"agentPushNotifEnabled": false,      "skipWebFetchPreflight": false,
"attribution": { "sessionUrl": false },
"remoteControl": { "shareHostProfile": "off" }
```

---

# Part 1 — Keeping identifiers out of the prompt

## The problem

Claude Code reads user identifiers from `~/.claude.json`'s `oauthAccount` and can splice them into the model's system prompt.
Today the only wired-up identifier is the email address, injected on every session as a `# userEmail` block inside a `<system-reminder>`.
It is read from the config file, not the credential store, so the address in the prompt is exactly `~/.claude.json` → `oauthAccount.emailAddress`.
Blanking it removes the block, because the object spread that builds it drops the key when the value is falsy.

This is the only place any identifier from `oauthAccount` reaches the model today.
`displayName`, `fullName`, and `organizationName` live in the same object and are read by other code paths (write-back comparisons, `/status`) but never by the system-prompt builder — a future addition would be a single object spread away.
Seeding the whole identifier group with blank values closes that door before it opens, and it costs nothing: none of those fields are consulted by the auth path or the request envelope.

The email is not written to session transcripts either (verified by grepping every `.jsonl` under `~/.claude/projects`), and neither the request headers nor the `metadata.user_id` envelope carry it.

## What would restore it

Two endpoints can write `emailAddress` back to `~/.claude.json`.
Both must be blocked, or the address returns on the next startup.

**`GET https://api.anthropic.com/api/oauth/profile`** runs from `init()` on every start and rewrites fourteen fields.
It returns early when the account already looks populated and `profileFetchedAt` is within a 24-hour TTL.
There is no upper bound on that timestamp — a value in the future stays below the TTL forever — which is why the seed sets it to the year 2100 and why the four other fields named in the condition must be present.

**`GET https://api.anthropic.com/api/claude_cli/bootstrap`** runs as a startup prefetch and merges an `oauth_account` object from the response, which does carry the address.
The merge bails out when the response's account UUID does not match the stored one, so a blank `accountUuid` skips it.
This is a single line of defence: if a future server response were to drop `account_uuid` while still including `account_email`, the guard would fall through and the address would be written.
Today's server always sends it, so the guard holds.

A separate call inside the same bootstrap handler runs *before* the merge and copies the response into an in-process credential slot regardless of the UUID guard.
That slot is memory-only and never touches `~/.claude.json`, so the prompt is safe, but the address is briefly in the process.
This matters for the OTel path in Part 3.

The four other writers cannot reintroduce the address on their own: the roles endpoint writes only `organizationRole`/`workspaceRole`/`organizationName` (the last one is server-generated as `"<email>'s Organization"`, which does embed the address as a substring, but that field is only shown locally in `/status` (see the field table)); the token-refresh write-back omits it; the `CLAUDE_CODE_USER_EMAIL` env seed needs a trio of env vars set *and* no `oauthAccount` on disk, so the seed's own presence closes it a second time.
`/login` does rewrite everything from scratch, and that undoes the seed.

## Field by field

| Field | Value | Why |
| --- | --- | --- |
| `emailAddress` | `""` | The actual target. The lookup becomes falsy and the `# userEmail` block is never built. |
| `accountUuid` | `""` | Triggers the bootstrap merge guard. Most consumers read it through `\|\|`, `!`, or `??` and treat it as absent; two paths compare it by equality (owner pinning, usage-utilization cache) and get disabled rather than crash; the MCP discovery cache falls into a shared `acct:incomplete` bucket. |
| `organizationUuid` | `00000000-0000-0000-0000-000000000000` | Kept truthy on purpose. The resolver reads `CLAUDE_CODE_ORGANIZATION_UUID`, then the stored value if truthy, and only otherwise falls through to an HTTP profile lookup; a nil UUID keeps the fast path without asserting a real organization. If the user opts in to third-party OpenTelemetry, it can reach the exporter as `organization.id`, though see Part 3: that exporter prefers the in-memory account over this one, so what it emits is the server's real UUID, not the nil one. |
| `organizationName` | `""` | A user identifier. Not read by the system-prompt builder today, but the server value embeds the email address verbatim (`"<email>'s Organization"`), so any future path that spliced it in would leak the address too. Only other reader is `/status` / `claude auth status --json`. |
| `displayName`, `fullName` | `""` | User identifiers written by the profile fetch. Today only read by the write-back's own no-op comparison, but blanked so a future prompt path that reached for them would find nothing. |
| `billingType` | `""` | The refresh gate only checks presence. An empty string keeps the gate satisfied without claiming a plan. `/usage-credits` disappears as a side effect. |
| `accountCreatedAt` | `2024-01-01T00:00:00Z` | Required by the refresh gate, which only tests `!== void 0`. Never parsed. |
| `subscriptionCreatedAt` | `2024-01-01T00:00:00Z` | Same gate. Note that GrowthBook targeting will `Date.parse` this and label the machine as a "January 2024 subscriber" — harmless here because `DISABLE_TELEMETRY=1` turns GrowthBook off too. Change carefully. |
| `ccOnboardingFlags` | `{}` | Satisfies the refresh gate, which only tests presence. Also read by the Pro-trial eligibility check (`ccOnboardingFlags?.e10 === true`); `{}` means "ineligible", though an object already in the file is left alone and can say otherwise — irrelevant on Max/Team plans, which exit early before this check. |
| `profileFetchedAt` | `4102444800000` | 2100-01-01Z. Freezes the 24-hour TTL as long as the credential file carries `subscriptionType` and `rateLimitTier`; if either is missing, the token-refresh write-back writes `Date.now()` back over it and the freeze breaks on the next start. |
| `hasCompletedOnboarding` (root of `~/.claude.json`, not inside `oauthAccount`) | `true` | Suppresses the interactive onboarding wizard on the first launch. The install script itself does not run `claude`, so this is not about the install being non-blocking — it is about the first hands-on `claude` invocation not pausing for onboarding. Trust dialogs, Pro-trial screens, and Grove policy prompts are gated on other flags and can still appear. |

---

# Part 2 — What leaves for claude.ai

Between 2.1.250 and 2.1.278 the identifier story in Part 1 did not change: the `# userEmail` block still reads from `~/.claude.json`, the two writers are still the profile fetch and the bootstrap merge, and the metadata envelope still falls back to `""`.
What did change is the amount of *local data* the CLI is willing to send to claude.ai.
The `env` block below covers part of it; the settings keys after it cover the rest.

## Environment variables

Claude Code merges this block into `process.env` at startup for `~/.claude/settings.json` scope without further filtering.

| Variable | What it does |
| --- | --- |
| `DISABLE_TELEMETRY=1` | Picks the `"no-telemetry"` traffic mode and drops first-party telemetry entirely. `accountUuid`, `organizationUuid`, and subscription tier are attributes on those events, so blocking the pipeline is what stops them from being sent. Also disables GrowthBook as a side effect, since GrowthBook uses the same traffic channel. |
| `DISABLE_ERROR_REPORTING=1` | Drops the crash-report pipeline. The traffic-mode selector treats it independently of `DISABLE_TELEMETRY`. |
| `DISABLE_BUG_COMMAND=1` | Retires `/bug`; the prompt for it becomes an explicit `disabled` state that names the env var (the disabled-message string itself uses the alias `/feedback`). |
| `CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY=1` | Retires the end-of-session quality prompt that would otherwise ask the user to rate the last turn. |
| `ENABLE_CLAUDEAI_MCP_SERVERS=0` | Turns off the claude.ai connectors. Despite the name, the value is parsed by a negation helper, so a "disabled" string is what switches the feature off. `1` or leaving it unset both mean "not disabled". |
| `DISABLE_GROWTHBOOK=1` | A belt over the suspenders (see below). |
| `CLAUDE_CODE_DISABLE_ORG_MEMORY=1` | Blocks the org-memory feature (see below). |

**`DISABLE_GROWTHBOOK=1`.**
GrowthBook is already off because it uses the `no-telemetry` channel that `DISABLE_TELEMETRY=1` selects, but the flag is checked at its own call site as well, so setting it explicitly keeps GrowthBook disabled even if a future release rewires the feature-flag pipeline off the telemetry channel.

**`CLAUDE_CODE_DISABLE_ORG_MEMORY=1`.**
Org memory mounts server-side memory stores under `/v1/code/memory/` and can be granted `"rw"`, i.e. Claude writing memory documents to Anthropic rather than to disk.
Discovery is a `GET /v1/code/local/memory/mounts` on startup (a 404 means "off for this account").
The feature is already off for us in two ways: its GrowthBook flag defaults to `false`, and it also needs the `allow_memory_sync` org policy.
But the first of those is a side effect of GrowthBook being disabled, the same fragility that `disableRemoteControl` exists to cover.
The env var is the only first-class switch: no `settings.json` key for it exists.
Writing to a mounted store needs one more thing on top of the feature gate — an explicit local opt-in recorded in `~/.claude.json` and checked against the current account — so the realistic exposure was always reads rather than silent writes.
The env var closes both.
Local memory under `~/.claude/projects/*/memory/` is unaffected; it never leaves the machine either way.

## Why `DISABLE_TELEMETRY` and not `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`

The one-variable alternative also blocks the bootstrap request outright, but it happens to disable the auto-updater, the mandatory Consumer-Terms notice, the model-list refresh, `/design-sync`, Projects, and `--enable-live-preview` as well, none of which are privacy targets.
(It also retires `/bug` and `/feedback`, which the env block does deliberately anyway.)
The split adopted here gives up bootstrap-blocking as a belt — leaving the `accountUuid` guard as the only defence there — in exchange for keeping those unrelated features working.

That choice has a second consequence, which is why the settings keys below exist at all.
Every claude.ai-authenticated request goes through one helper, and that helper only refuses in `essential-traffic` mode.
`DISABLE_TELEMETRY=1` yields `no-telemetry`, not `essential-traffic`, so none of those calls are blocked by it, and each feature needs its own switch.

What *is* holding most of them shut today is a second-order effect of `DISABLE_GROWTHBOOK=1`: the flag getter returns the built-in default whenever GrowthBook is disabled, and every flag guarding a feature in this section defaults to `false`.
That is a side effect, not a setting — the same reasoning that led to `disableRemoteControl`.
The keys below turn each one off directly.
It is *not* a general rule: the "Audited and deliberately not added" section lists two claude.ai-adjacent features whose flags default to `true` and are therefore live on this install.

## The settings keys

**`disableRemoteControl: true`** — the direct switch for the `/rc` (Remote Control) pairing: sessions do not sync to `claude.ai/code`, the CLI refuses remote-control connections, and `/rc` is a no-op.
The env-var routes already stop Remote Control today because it depends on GrowthBook and telemetry (both off), but that is a side effect of the feature-flag pipeline being disabled; if Anthropic ever moves Remote Control off feature flags, the env vars alone would no longer block it.
This key does.
Stricter than `remoteControlAtStartup: false`, which only suppresses the auto-connect on launch and still allows manual `/rc` pairing.

**`enableArtifact: false`** — turns off the `Artifact` tool, so Claude Code cannot publish an HTML/Markdown page to `claude.ai/code/artifact/` even when a design or dataviz skill loads.
The binary defines it as the canonical key with `defaultOn:true`, so an explicit `false` is required.
A user-scope `false` cannot be overridden by a repository; the key's own description states the precedence:

> Turn the Artifact tool on or off.
> Off in any of managed, `--settings`, or user settings wins; project and local settings can only turn it off.
> Unset defaults to on once the feature is available.

The legacy `disableArtifact: true` is documented in the schema as `"Deprecated: use enableArtifact: false. Still honored — true disables the Artifact tool; false is ignored."`, so it is the inferior of the two.
`CLAUDE_CODE_DISABLE_ARTIFACT=1` is a third equivalent.

**`syncClaudeAiSkills: false`, `syncClaudeAiPlugins: false`** — added in 2.1.275, and the only entry in this section documented as default-on:

> Added syncing of the skills and plugins enabled on your claude.ai account to terminal sessions signed in with it; opt out with `syncClaudeAiSkills: false` or `syncClaudeAiPlugins: false`

The sync downloads into `~/.claude/skills/synced` and `~/.claude/plugins/synced` and makes that code runnable locally, so it is a code-delivery path as much as a privacy one.
Its flag defaults to `false`, so with GrowthBook off nothing is fetched today.
2.1.273 also widened the OAuth request — "Changed sign-in with a Claude account to also request access to your claude.ai plugins" — so a fresh `/login` now grants the scope this feature needs.
Only `false` is honored for these two keys, and they are read from user, managed, `--settings` and `.claude/settings.local.json` scope, never from a repository's `.claude/settings.json`.

**`disableClaudeAiConnectors: true`** — the settings-key twin of the `ENABLE_CLAUDEAI_MCP_SERVERS=0` env var already in the block.
Worth having both because the precedence is unusually strong: *"When true in any settings source, claude.ai MCP cloud connectors are not auto-fetched or connected … Any-source-true wins: a project can opt out, but a project-level false cannot override a user-level true."*

**`autoUploadSessions: false`** — *"Mirror local sessions to claude.ai as view-only (no remote control)"*.
A full transcript mirror, and the one key here whose name states the risk outright.
It is filed under `Internal` in the `/config` menu, alongside `autoAddRemoteControlDaemonWorker`, and no call site outside the settings merge appears in the binary, so it appears to be plumbed but not yet wired; the consumer is presumably the Remote Control daemon, which `disableRemoteControl` already stops.
Setting it false costs nothing and does not depend on that assumption holding.

**`remoteControl: { shareHostProfile: "off" }`** — what a Remote Control environment reports about the machine when it registers.
The levels are `off` (nothing), `basic` (OS, architecture, detected developer tools) and `full` (also *the names of MCP servers configured on this machine*).
Left unset, *"the level comes from the feature rollout, which may be any of the three"*, meaning a server-side choice decides how much of the host fingerprint is sent.
*"Managed, `--settings` and user settings choose the level (the most restrictive wins); project and local settings can only lower it, never raise it."*

**`inputNeededNotifEnabled: false`, `agentPushNotifEnabled: false`** — push notifications to a phone when a permission prompt is waiting, and proactive notifications Claude sends on its own.
Preferences are fetched from `GET /api/claude_code/notification/preferences`, so leaving them unset means the server's answer decides.
Both need a registered mobile device, which we do not have.

**`channelsEnabled: false`** — inbound messages pushed by MCP servers holding the `claude/channel` capability.
Described as a managed-org opt-in that defaults off on claude.ai Teams/Enterprise and on for Console accounts, so the default depends on an account attribute the seed blanks.
Pinning it removes that ambiguity.

**`attribution: { sessionUrl: false }`** — the only item here that leaks *outward* rather than to Anthropic.
Defaults to true, and it appends a `Claude-Session:` git trailer and a PR-body link pointing at claude.ai:

> Whether to append the claude.ai session link to commits and PRs created from web or Remote Control sessions (default: true).
> Set to false to omit the `Claude-Session` trailer and PR-body link.

Scoped to web and Remote Control sessions, so it does not fire for us, but a session URL committed into a repository is published to wherever that repository is pushed and cannot be recalled.

**`skipWebFetchPreflight: false`** — the one entry pinned to the *less* private value on purpose, so what matters here is the reasoning, not the value.
Every `WebFetch` first asks Anthropic about the domain, once per new host per session, so Anthropic learns every host the tool is pointed at, whether or not the fetch itself is first-party.
Setting the key `true` stops that, and the schema endorses it (*"Skip the WebFetch blocklist check for enterprise environments with restrictive security policies"*).
We do not, because the check is a blocklist: turning it off means `WebFetch` visits hosts Anthropic would have refused, and on a machine where Claude follows links from issues, pages, and tool output, that is the larger exposure of the two.
A hostname is metadata; a fetch of a host on the blocklist exposes the session's content to an actively hostile page.
Anthropic evaluates the trade-off the same way — `false` is the value their own restrictive-value table counts as the restrictive one.

`false` is also the unset behaviour, so this key changes nothing today.
It is written out so the choice is a decision on record instead of a default nobody looked at.
Flip it to `true` only where something else already constrains egress.
Note that the GrowthBook-default argument above does not apply to this one: it is a deliberate `false`, not a belt over a default.

## Audited and deliberately not added

- **Cron / `ScheduleWakeup` / routines.**
  Guarded by a **default-true** flag, so unlike everything above this one is live with GrowthBook off.
  Scheduled prompts are written to `.claude/scheduled_tasks.json` and run locally, so it is an unattended-execution question rather than a claude.ai leak, and disabling it would remove `CronCreate`/`CronList`/`CronDelete` and `/schedule`.
  Set `CLAUDE_CODE_DISABLE_CRON=1` if unattended turns are unwanted.
- **`SendFeedback` / `feedbackDrafts`.**
  The tool writes a draft to `~/.claude/feedback/drafts/` and sends nothing without an explicit `/feedback` submit, and its `isEnabled` already fails on `DISABLE_BUG_COMMAND`, so `feedbackDrafts: "off"` would be redundant.
  The submit path is what uploads a transcript to `/api/claude_code_shared_session_transcripts`, and it is closed.
- **`SendUserFile`.**
  Uploads local files to claude.ai through `/api/oauth/file_upload` on a **default-true** flag, but `isEnabled` also requires a remote or cloud environment, or a channel surface.
  A local terminal is neither.
  It also needs the `allow_send_file` org policy.
- **Enhanced telemetry** (`CLAUDE_CODE_ENHANCED_TELEMETRY_BETA`, `ENABLE_ENHANCED_TELEMETRY_BETA`) is opt-in, defaults false, and exports to the operator's own OTel collector rather than to Anthropic.
- **`CLAUDE_CODE_GB_DISK_CACHE_WHEN_TELEMETRY_OFF`** would let GrowthBook serve flags from a disk cache while telemetry is off — which would undo the "flags fall back to their defaults" reasoning this section relies on.
  It is opt-in *and* requires `!DISABLE_GROWTHBOOK`, so the existing env block already closes it.
  Worth re-checking if that env var ever changes shape.
- **`CLAUDE_CODE_DISABLE_VITALS_EMITTER`** guards a guest binary at `/root/.local/bin/vitals-emitter-guest` that only exists inside a sandbox VM.

## Remote managed settings, and what protects the keys above

`GET /api/claude_code/settings` hands the CLI a settings payload from the server, at the managed scope (the highest).
The pull is guarded by a consent dialog, and it covers execution risk only: a payload that changes hooks, env vars, or shell/sandbox/isolation settings prompts, and rejecting it exits the CLI ("Managed settings were not approved; exiting without applying them").
A payload that only flips feature toggles does not prompt.
The restrictive-value filter, which is easy to mistake for a guard here, runs over a policy snapshot being passed to a child process, not over this pull.

So the durability of the keys above does not come from that dialog.
It comes from the keys themselves being one-way, which is why the one-way ones were picked where a choice existed:

- `enableArtifact` — "Off in any of managed, `--settings`, or user settings wins"
- `syncClaudeAiSkills` / `syncClaudeAiPlugins` — "Only `false` is honored"
- `disableClaudeAiConnectors` — "Any-source-true wins"
- `remoteControl.shareHostProfile` — "the most restrictive wins"

`autoUploadSessions`, `channelsEnabled` and the two `*NotifEnabled` keys carry no such wording, so a managed payload could in principle set them back.
Nothing observed suggests it does, and this install has no organization behind it, but that is the honest limit of what these four provide.

---

# Part 3 — Operating this

## What this does not change

Authentication is untouched.
The credential store (`~/.claude/.credentials.json`) is the sole source of the access token; nothing in the auth path reads `oauthAccount`.
Blanking those fields does not affect signing in or making requests.

Requests still carry an account field in their metadata envelope, which falls back to an empty string once the config value is blank.

If the user opts in to third-party OpenTelemetry (`CLAUDE_CODE_ENABLE_TELEMETRY=1`), a separate exporter attaches account attributes.
It reaches for the in-memory credential slot, the one stamped from the bootstrap response, **before** the on-disk one.
So the seed does not help here: what leaves is the server's own email and organization UUID, not the blanked and nil values on disk.
The exporter is gated on `CLAUDE_CODE_ENABLE_TELEMETRY` alone — `DISABLE_TELEMETRY` does not touch it.
If OTel is on, expect the address to leave through that channel.

## Trade-offs

- `/usage-credits` disappears and the rate-limit message changes to "Switch models to keep working."
  The command is gated on `billingType` matching one of the four subscription values.
- `/status` shows blank rows for email and organization; `claude auth status --json` returns empty strings for those fields and the literal nil UUID for `orgId`.
- Remote-control and browser-bridge owner pinning are disabled.
- The MCP discovery cache falls into a shared "incomplete" bucket rather than being scoped per account.
  Different accounts still cannot collide, because the caches are per-user in the first place.
- claude.ai connectors (Gmail, Calendar, Drive, Notion) go away.
  Local `.mcp.json` and `claude mcp add` servers are unaffected.
- `/bug` and the end-of-session rating prompt are removed.
- First-party telemetry and crash reporting are off.
  GrowthBook feature flags fall back to their built-in defaults; a few features that require server-side flag evaluation (Remote Control among them) print a message naming the env var.
- `/login` clears `oauthAccount` and repopulates it from the profile endpoint, undoing the seed.
  `hasCompletedOnboarding` and the `settings.json` env block survive, so the next `/login` puts identity back but leaves the rest of the config alone.
- Skills and plugins enabled on the claude.ai account do not appear in terminal sessions.
  `ListPlugins` lists them but nothing is downloaded; local `/plugin` installs and project skills are unaffected.
  If the sync had already run before this flag was added, `~/.claude/skills/synced` and `~/.claude/plugins/synced` move to their `.trash` siblings at the next launch and are deleted after `cleanupPeriodDays` — re-enabling the sync re-downloads them rather than restoring from the trash.
- `WebFetch` still sends each new host to `/api/web/domain_info`.
  This is the one leak the policy accepts rather than closes; see `skipWebFetchPreflight` in Part 2 for why.
- Mobile push notifications never arrive, including the permission-prompt-waiting one.
- Commits and PRs from a web or Remote Control session carry no `Claude-Session` trailer.
  The `Co-Authored-By` attribution is a separate key and is untouched.
- Org-managed memory stores are unavailable; `/memories` and the memory tools still work against local files.

## Verifying

Start Claude Code once, wait a couple of seconds for the fire-and-forget profile fetch to settle, then look at both the address and the freeze timestamp:

```bash
python3 -c "import json,os,pathlib;
d=json.loads(pathlib.Path('~/.claude.json').expanduser().read_text())
a=d.get('oauthAccount',{})
print('emailAddress    :', repr(a.get('emailAddress')))
print('profileFetchedAt:', a.get('profileFetchedAt'))
print('displayName     :', repr(a.get('displayName')))"
```

`emailAddress` empty and `profileFetchedAt` still `4102444800000` means both writers stayed silent.
If `profileFetchedAt` has moved but the address is still empty, the token-refresh write-back fired (`displayName` will likely have flipped to a real value at the same time).
The freeze has broken and the address will be back on the next start unless the credential file gets `subscriptionType`/`rateLimitTier` restored.

If the address itself has come back, use `--debug` and look for `[claudeai-mcp] Disabled via env var` in `~/.claude/debug/<session>.txt` to confirm the settings file was read at all.
A missing line means the env block did not apply and the whole seed was likely overwritten by `/login` or a similar reset path.

`CLAUDE_CONFIG_DIR`, if set, moves both files to that directory instead of `~`.

## Other endpoints a local session reaches, for reference

These are the non-model endpoints found in the binary that a signed-in terminal session can reach.
Each row says what the endpoint is for, not that it fires on every start — the trigger was traced only where the row says so.
None of them carry the email address.

| Request | Status |
| --- | --- |
| `GET /api/oauth/profile` | Runs from `init()` on every start. Frozen by `profileFetchedAt`; see Part 1. |
| `GET /api/claude_cli/bootstrap` | Startup prefetch. Merge blocked by the blank `accountUuid`; see Part 1. |
| `GET /api/claude_code/settings` | Remote managed-settings pull; see Part 2. |
| `GET /api/claude_code/policy_limits` | Org policy (`allow_remote_control`, `allow_memory_sync`, `allow_send_file`, …). Trigger not traced. |
| `GET /api/oauth/claude_cli/roles` | Writes back only role fields; see Part 1. |
| `GET /v1/code/local/memory/mounts` | Org-memory discovery, 24h cached. The *feature* is off via `CLAUDE_CODE_DISABLE_ORG_MEMORY=1`; whether that also suppresses this GET was not traced. |
| `GET /api/claude_code/notification/preferences` | Mobile push preferences; the two `*NotifEnabled` keys pin the answer locally. |
| `downloads.claude.ai/model-catalog`, `/api/model_selector/` | Model catalog refresh. No user data. |
| Auto-updater | Kept on purpose; this is what `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` would have cost. |
