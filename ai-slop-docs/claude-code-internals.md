# Claude Code 2.1.278, as read out of the binary

Everything here was read out of the Claude Code binary at `~/.local/share/claude/versions/2.1.278` (a Bun single-file executable that embeds its JavaScript in plaintext).
Snippets are quoted verbatim **from that build**.
This file is the evidence; `claude-json-seeding.md` is the reasoning and points back here by section.

The identifier half was first written against 2.1.250 and re-verified against 2.1.278; the claude.ai feature surface was audited on 2.1.278, with 2.1.263 and 2.1.270 used for diffing.

## Version caveat

Verified against 2.1.278, and every minified identifier below is from that build.
They rotate on almost every release, and a rotated name can land on an unrelated live function rather than simply disappearing.
For example, 2.1.250's `aS()` (the organization-UUID resolver, `r$()` here) is a real but different function in 2.1.278.
Never conclude anything from grepping a newer binary for one.
The JSON key names, endpoint paths, environment variable names, schema `describe()` strings, and the traffic-mode selector are the stable parts.
Re-check after a major version bump.

## The `# userEmail` block

The email address is spliced into the system prompt on every session:

```js
let O = a.ANTHROPIC_UNIX_SOCKET ? void 0 : Hn()?.emailAddress, L = await j_r(r);
…
let B = {claudeMd: h ?? "",
  ...O&&{userEmail:`The user's email address is ${O}. Use it only to identify the user, …`},
  ...L&&{attachedProject:L}, currentDate:`Today's date is ${TQn(e)}.`};
```

`Hn()` is a getter over the on-disk config, not over the credential store:

```js
function Hn(){return Rc()?ae().oauthAccount:void 0}   // ae() reads ~/.claude.json
```

## The profile fetch and its freeze

`GET /api/oauth/profile` returns early when the account already looks populated and the profile was fetched recently:

```js
let y=ae(),T=y.oauthAccount?.profileFetchedAt,A=T!==void 0&&Date.now()-T<Mye;
if(y.oauthAccount&&y.oauthAccount.billingType!==void 0
  &&y.oauthAccount.accountCreatedAt!==void 0
  &&y.oauthAccount.subscriptionCreatedAt!==void 0
  &&y.oauthAccount.ccOnboardingFlags!==void 0&&A || !mt() || !tp())return!1;
```

`Mye` is `86400000`, a 24-hour TTL, and there is no upper bound on `profileFetchedAt`.

## The bootstrap merge

`GET /api/claude_cli/bootstrap` merges an `oauth_account` object from the response, bailing out when the response's account UUID does not match the stored one:

```js
function q(e,n){if(!e||!n)return e;
 if(n.account_uuid!=null&&n.account_uuid!==e.accountUuid)return e;
 let s={organizationType:n.organization_type??null,
        organizationRateLimitTier:n.organization_rate_limit_tier??null,
        userRateLimitTier:n.user_rate_limit_tier??null,
        seatTier:n.seat_tier??null};
 if(n.account_email!=null)s.emailAddress=n.account_email;
 if(n.organization_uuid!=null)s.organizationUuid=n.organization_uuid;
 if(n.organization_name!=null)s.organizationName=n.organization_name;
 return{...e,...s}}
```

An authenticated request to that endpoint returns:

```json
"oauth_account": { "account_uuid": "…", "account_email": "…",
                   "organization_uuid": "…", "organization_name": "…'s Organization", … }
```

A separate call inside the same handler runs *before* `q()` and copies the response into an in-process credential slot regardless of the UUID guard:

```js
function nt(e){if(!e?.account_uuid)return;
 Ere({accountUuid:e.account_uuid,emailAddress:e.account_email??void 0,
      organizationUuid:e.organization_uuid??void 0})}
```

`Ere` writes to `credentialSlots.stampAuthenticatedAccount`, which is memory-only.

## The `CLAUDE_CODE_USER_EMAIL` env seed

Needs the trio `CLAUDE_CODE_USER_EMAIL` + `CLAUDE_CODE_ACCOUNT_UUID` + `CLAUDE_CODE_ORGANIZATION_UUID` all set *and* no `oauthAccount` on disk: `if(h&&!ae().oauthAccount)Nvt(h,n)`.

## Traffic mode

```js
function b(){if(process.env.CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC)return"essential-traffic";
             if(process.env.DISABLE_TELEMETRY)return"no-telemetry";
             if(Me(process.env.DO_NOT_TRACK))return"no-telemetry";
             return"default"}
function At(){return b()==="essential-traffic"}
```

Every claude.ai-authenticated request goes through one helper, and that helper only refuses in `essential-traffic` mode:

```js
function HH(e){if(!e.bypassEssentialTrafficOnly&&!0&&At())return"essential-traffic-only";
               if(Oe()!=="firstParty")return"data-residency";
               return}
// At() === (trafficMode() === "essential-traffic")
```

## `ENABLE_CLAUDEAI_MCP_SERVERS`

The value is parsed by a negation helper, so a "disabled" string is what switches the feature off:

```js
function vo(e){if(e===void 0)return!1;if(typeof e==="boolean")return!e;
 let n=String(e).toLowerCase().trim();return["0","false","no","off"].includes(n)}
// caller: let s=vo(process.env.ENABLE_CLAUDEAI_MCP_SERVERS),g=Fne();
//         if(s||g)return t(`[claudeai-mcp] Disabled via ${s?"env var":"disableClaudeAiConnectors setting"}`),…,{};
```

## Org memory

```js
function Ne(){if(a.CLAUDE_CODE_DISABLE_ORG_MEMORY)return!1;
 if(kr())return!1;
 if(!x("tengu_haze_glass",!1))return!1;                 // GrowthBook, default false
 if(process.env.CLAUDE_MEMORY_STORES?.trim())return!1;
 if(Wa()!==null)return!1;
 if(!Gt("allow_memory_sync"))return!1;
 return Bn()&&(mt()||pce())}
```

Writing to a mounted store needs an explicit local opt-in recorded in `~/.claude.json`, checked against the current account:

```js
function uUe(){if(cbt())return _e().logMonorepoWriteBlockOnce(),!1;
               if(bt()===null)return!1;return Ne()}
function zG(){let e=ns();if(!uUe()||e.orgMemoryWrites!==!0)return!1;
              let n=e.orgMemoryWritesAccount;return n!==void 0&&n===bt()}
```

No `settings.json` key for it exists (`disableOrgMemory`, `allowMemorySync` and the like are absent from the schema).

## GrowthBook when disabled

The flag getter returns its second argument — the built-in default — whenever GrowthBook is disabled:

```js
getFeatureValueBlocking(e,n){ …; if(!this.deps.isEnabled())return n; … }
getFeatureValueWithSource(e,n){ …;
  if(!this.deps.isEnabled()&&!this.deps.isDiskCacheReadableWhileDisabled())
    return{value:n,source:"disabled"}; … }
// x(e,n)=Cd(e,n).value, wD(e,n,r)=x(e,n), isEnabled:()=>dP(),
// dP()=!a.DISABLE_GROWTHBOOK&&n$()
```

## `enableArtifact` and the restrictive-value table

The binary defines `enableArtifact` as the canonical key with `defaultOn:true`:

```js
var C={enableKey:"enableArtifact",legacyDisableKey:"disableArtifact",
       envDisableVar:"CLAUDE_CODE_DISABLE_ARTIFACT",defaultOn:!0};
```

An earlier draft of the reasoning doc read `{path:["enableArtifact"],restrictive:!1}` in the table named `ot` as "this key is not restrictive", and concluded a project could re-enable artifacts.
That was a misreading.
`restrictive` holds **the value that counts as restrictive**, not a boolean saying whether the key is one — which is why sibling entries read `restrictive:["refuse","hold"]`, `restrictive:"off"` and `restrictive:cu` (an array of effort levels).
`ot` is consumed by a filter that keeps only entries already at their restrictive value:

```js
function hxn(e){let n={};
 for(let{path:h,restrictive:_}of ot){ if(wo.has(h[0]))continue;
  let b=ve(e,h);
  if((typeof b==="boolean"||typeof b==="string")&&rt(_).includes(b))Ne(n,h,b)}
 let s=vt(e.permissions,["deny","ask","disableBypassPermissionsMode","disableAutoMode"]);
 if(s)n.permissions={...n.permissions,...s};
 for(let h of kl)if(e[h]!==void 0)n[h]=e[h];
 for(let[h,_]of Object.entries(e))
  if((h.startsWith("disable")&&(_===!0||_==="disable")
    ||h.startsWith("enable")&&_===!1)&&!wo.has(h))n[h]=_;
```

The generic tail is the clearest statement of the convention: any `disableX: true` or `enableX: false` is restrictive.
The `kl` loop is an unconditional passthrough (`allowedMcpServers`, `allowManagedHooksOnly`, `availableModels`, …) that skips the restrictive test entirely.
`hxn` runs over an org policy snapshot being handed to a child process, so the table describes what a *server-supplied* policy may assert, and says nothing about project-versus-user precedence.

## claude.ai skills and plugins sync

The gate is `{settingKey:"syncClaudeAiSkills", policyKey:"allow_account_skills_sync", flagName:"tengu_claudeai_skills_sync_enabled"}` evaluated through `x(e.flagName,!1)`.

## WebFetch domain preflight

Once per new host per session (`n.domainChecks` memoises the answer):

```js
let g=await lt.get(`https://api.anthropic.com/api/web/domain_info?domain=${encodeURIComponent(e)}`,…);
if(g.status===200){if(g.data.can_fetch===!0)return n.domainChecks.set(e,!0),{status:"allowed"};
                   return{status:"blocked"}}
```

`ot` carries `{path:["skipWebFetchPreflight"],restrictive:!1}`.

## Flags that stay on with GrowthBook off

- Cron / routines: `Gx()` is `!a.CLAUDE_CODE_DISABLE_CRON && wD("tengu_kairos_cron",!0,t)`.
- `SendUserFile`: `x("tengu_send_user_file",!0)`, but `isEnabled` ends in `(cl()||R())&&!rLe()`, where `R()` is `CLAUDE_CODE_REMOTE_ENVIRONMENT_TYPE||CLAUDE_CODE_REMOTE||…` — a remote or cloud environment — and `cl()` a channel surface.
- `SendFeedback`: `isEnabled` fails on `DISABLE_BUG_COMMAND` via `CY()`.

## Managed settings consent

```js
function aV(e){return pt(e)||e.hasHooks||ye(e).length>0}
// the compared view: shellSettings, envVars, sandboxSettings,
//                    isolationSettings, hooks, payloadSlots
function ne(e,n,s){if(!n||!aV(pH(n)))return"no_check_needed"; …}
```

## The metadata envelope and the OTel exporter

```js
account_uuid: Me(a.CLAUDE_CODE_REMOTE)&&a.CLAUDE_CODE_ACCOUNT_UUID
  ||UU()?.accountUuid||Hn()?.accountUuid||""
```

The OTel exporter reaches for the in-memory `credentialSlots` account, the one `nt()` stamps from the bootstrap response, **before** the on-disk one, and is gated on `CLAUDE_CODE_ENABLE_TELEMETRY` alone (`$t(){return Me(process.env.CLAUDE_CODE_ENABLE_TELEMETRY)}`):

```js
let s=UU()??Hn()??oe();
if(s){let{organizationUuid:u,emailAddress:l,accountUuid:p}=s;
 if(u)r["organization.id"]=u;
 if(l)r["user.email"]=l;
 if(p&&m("OTEL_METRICS_INCLUDE_ACCOUNT_UUID")){r["user.account_uuid"]=p; …}}
```

## Auto-mode classifier context (2.1.296)

This section was read from 2.1.296, not 2.1.278; the minified names are from that build.

The server-side switch is read from the env var once per session; `0` turns the server classifier off:

```js
function OGe(){let e=wo();if(e.userServerClassifierSwitch===void 0){
  let n=a.CLAUDE_CODE_AUTO_MODE_SERVER;e.userServerClassifierSwitch=n===void 0?null:Ne(n)}
  return e.userServerClassifierSwitch}
```

The identity sent as `classifier_context.user_identity`, and quoted in the client-side classifier prompt as `**User identity**`, prefers `GITHUB_ACTOR`:

```js
async function Arr(){if(Ce())return null;
  let e=a.GITHUB_ACTOR??a.USER??a.USERNAME,n=e===void 0?await DRe():null;   // DRe(): git user.email
  return(e??(n?Qe(n,"@"):null))?.replace(elo,"").slice(0,64)||null}
```

The context schema also carries `home_dir`, `live_cwd`, `rule_roots`, `rules`, `trusted_directories` and a `git_state` with `branch`, `status` and `visibility.remotes[]` (`host`, `remote` as `owner/repo`).

`includeGitInstructions` is read through one helper, with an env-var override:

```js
function Cet(){let e=a.CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS;if(e!==void 0)return!e;
  return ft().includeGitInstructions??!0}
```

A capture of 2.1.296 with the policy applied, a canary login name, git user, remote and branch, and an auto-mode action outside the working directory showed none of the canaries in any request, with the action still classified and run.

## Re-running the audit

The claude.ai surface grows every release while the reasoning doc does not.
Three checks find most of it, run against `strings ~/.local/share/claude/versions/<new>`:

```bash
# 1. settings keys, with the value that counts as restrictive
grep -oE 'path:\["[a-zA-Z0-9_,"]+"\],restrictive' strings.txt | sort -u
# 2. env-var registry (the exports block lists every one the build knows)
grep -oE '[A-Z][A-Z0-9_]+:\(\)=>[a-zA-Z0-9_$]+' strings.txt | sed 's/:().*//' | sort -u
# 3. feature flags that stay ON when GrowthBook is disabled
grep -oE '[a-zA-Z0-9_$]{1,4}\("tengu_[a-z0-9_]+",!0' strings.txt | sed -E 's/.*\("//; s/",!0//' | sort -u
```

Diff each against the previous version.
The third is the one that catches what the env block cannot: a claude.ai-facing feature whose flag defaults to `true` is live on this install.
The embedded changelog is also in the binary (`grep -oE '## 2\.1\.2[0-9][0-9].{0,6000}'`) and each build carries roughly the last ten releases, so keeping one older binary around covers the gap.
