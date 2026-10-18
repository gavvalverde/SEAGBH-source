# AI Reviewer: integration candidate, disabled by default

No workflow has been executed or enabled by this implementation. No repository
variables/secrets have been changed. This directory and the new workflow are
uncommitted candidates for review.

## Trust and activation

The workflow uses `pull_request_target` because executable workflow/Python must
come from the trusted default branch, not the pull request merge commit. The
checkout is pinned to `github.workflow_sha` in the literal base repository, with
credentials persistence, submodules and LFS disabled. The runner also checks
`GITHUB_EVENT_NAME`, repository, `GITHUB_SHA` and checked-out HEAD before clients
are constructed. It never executes or checks out PR code. Python modules,
requirements and schema are loaded exclusively from the same trusted checkout.

Only open, non-draft PRs from gavvalverde/SEAGBH-source to its main branch are
eligible. The repository default branch must be main. The job gate requires
AI_REVIEW_ENABLED == 'true'. The Python gate additionally requires the exact,
case-sensitive string `true`; absent, false or any other value means no API
request. A first Bash step, without credential environment variables, checks the
literal value with case-sensitive [[ ... == "true" ]]. It writes only the fixed
outputs enabled=true or enabled=false. Checkout, dependency installation and the
credential-bearing reviewer step all require that controlled output to be true.
The coarse job expression is case-insensitive and is not the authorization gate.
Do not enable it as part of publishing this integration PR.

Before activation, a maintainer must separately review the diff, confirm the
variable is absent/false, inspect applicable GitHub Actions event policies and
approve secret use. Current GitHub documentation describes an evaluate-mode
pull_request_target policy with enforcement scheduled for 2026-11-02 for affected
public repositories. An explicit applicable policy may be required. This change
does not create or bypass policies. See:
https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target

Runtime secrets: the built-in GITHUB_TOKEN (contents:read, pull-requests:write)
and OPENROUTER_API_KEY. No PAT, custom bot or fork execution is supported.
The fixed checkout action commit is 3d3c42e5aac5ba805825da76410c181273ba90b1.
The hosted runner is ubuntu-24.04; production explicitly uses Python 3.12
(python3.12) in a fresh venv. The pinned dependency tree requires at least Python 3.11; the
production target is 3.12. typing-extensions is pinned for Python <3.13, as
required by referencing; it is not installed unnecessarily on newer Python.
Dependencies are installed from fixed versions, as binary wheels,
from PyPI, with pip configuration isolation. Artifact hashes are not yet locked.

## Flow and scope

`event_snapshot` validates policy and identity. Current PR metadata is checked
before collection, before the paid request and before publication. The compare
endpoint receives immutable base...head SHAs; live PR endpoints are not used to
obtain patches. Missing patches, mismatched file counts, inconsistent hunk
counters and unsafe/duplicate paths abort before a paid request.

The collector preserves complete patch strings and never slices a hunk. It
records inclusive added-new, removed-old and context-old/new ranges. Only sent
new-file coordinates are permitted for numbered findings. Deleted-file reviews
may use null line with explicit file-level scope. There is no dependency-source
fetching or execution. Metadata manifests are contextual data, not Python.

Limits: 50 changed files (larger PRs fail), 32 KiB per file chunk, 80 KiB total
diff and 16 KiB context manifest. Whole files exceeding budgets are omitted and
marked partial; no review proceeds if none fit. Titles/descriptions are bounded
to 1024/4096 UTF-8 bytes and any shortening also marks the review partial. The
published heading reflects actual partial scope independently of model claims.
Binary/missing patches and metadata-only renames without patches fail safely.

`core.build_payload` delegates unchanged to the published `build_request`:
MiniMax M2.7, temperature 0.3, max_tokens 2500, with no tools or duplicate prompt.
Transport accepts only fixed HTTPS origins, never follows redirects, and bounds
responses (GitHub 2 MiB, OpenRouter 1 MiB) and encoded model requests (256 KiB).
Socket connection/read timeouts are 5/30 seconds; the response-read deadline is
60 seconds from request start. The connection timeout is passed to HTTPSConnection;
the read timeout is set on the connected socket before request/headers. The body
loop retains that socket reference across getresponse(), checks the monotonic
deadline and applies min(30, remaining) before each read1. It stops when the
response closes its file and explicitly closes response/connection in finally,
including Connection: close and error paths. The job has a 10-minute outer
timeout. DNS/platform timeout behavior and the actual Ubuntu runtime still need operational review.

At most two OpenRouter attempts: one retry with a 1-second backoff for 429/5xx.
400/401 and network errors are not automatically retried. A network failure may
have consumed model credits; avoiding blind retries limits that ambiguity.
Only one assistant message with finish_reason=stop is accepted. Incomplete,
empty, malformed, unexpected or schema-invalid responses never become clean.

The complete response passes through core.process_response. The renderer only
accepts publishable, SHA-bound results and uses their accepted copy, never the
raw envelope. Fully filtered reviews fail; mixed reviews omit rejected findings.
Model text is HTML-escaped inside pre blocks and mentions are neutralized.
For partial/filtered reviews the original model summary is replaced with a
neutral scope statement, since it may refer to omitted/rejected material.
Bodies over 60000 UTF-8 bytes fail instead of being silently truncated.

## Concurrency, deduplication and failures

Actions serializes executions by repository/PR with cancel-in-progress:false.
An in-process nonblocking lock prevents concurrent callers in the same process.
Reviews are deduplicated by repository, PR, head SHA, trusted-source fingerprint,
built-in bot numeric ID/login/type, COMMENTED state, commit_id and the exact
trailing success marker. Ordinary comments and other users' markers do not count.
Failure markers do not block valid retries. The fingerprint includes runtime
modules, prompt, schema and pinned runtime requirements.

POST is attempted once per execution. On an ambiguous failure the runner checks
reviews once; an observed accepted review is treated as reconciled success.
There is NO exactly-once guarantee across independent processes or eventual API
consistency. Different workflows sharing github-actions[bot] privileges can
impersonate its marker; a dedicated app/signature would be needed to isolate
those privileged writers. A tiny head-update window remains between the last
GET and POST, but commit_id always anchors the review to the analyzed SHA.

There are no failure comments: only sanitized local JSON codes/stages and a
nonzero exit for operational errors. This avoids publishing partly trusted
fallbacks. Auth, collection, model, parsing/schema/filtering, revision mismatch
and publication failures remain errors. No headers, diff content, model bodies
or exception text are logged. Failures after an API-accepted POST can remain
uncertain and require human inspection before repeated execution.

Cost controls are per execution and per successfully reviewed SHA/version, not
a global spending cap. Repeated failed runs can incur further costs. Repository
budgets/approval procedures must be set separately before activation. Source
code is intentionally sent to OpenRouter; schema validation is not DLP and does
not prove the truth of model findings. Review the source-sharing policy and do
not put credentials in source. No paid benchmark has validated this integration.

## Offline tests

Use an isolated venv, not the application environment:

    python -m pip install -r .github/ai-review/production/requirements-test.txt
    python -B .github/ai-review/production/test_core.py
    python -B .github/ai-review/production/test_runner.py
    python -B .github/ai-review/benchmarks/harness/test_bench.py
    python -B .github/ai-review/benchmarks/harness/leak_guard.py

Test fixtures use fake PRs, transport, model and publisher. Sockets are blocked.
PyYAML 6.0.3 is test-only; YAML structure and trust/activation invariants are
checked without invoking GitHub Actions. Never test this code by dispatching a
real workflow or by setting AI_REVIEW_ENABLED=true during implementation.

## 3B.3B regression verification and limits

Tests now exercise real stdlib HTTPConnection/HTTPResponse parsing over in-memory
socket/file doubles (no network), including Content-Length, chunked, keep-alive,
Connection: close, incomplete/oversized bodies, read timeout, deadline and resource
ownership. Runner tests carry real parsed model envelopes through schema validation
and fake publication. Patch tests preserve U+2028 and compare exact supplied ranges
for LF/CRLF and no-final-newline markers; invalid hunks remain rejected.

The exact activation script extracted from YAML is executed in Bash for absent,
empty, false, FALSE, TRUE, True, true, 1 and unexpected values. Structural checks
require its output gate on every subsequent step. This does not execute GitHub's
expression evaluator, token injection or event policies. No Actions or real API
calls were used. Local validation uses the existing isolated Python 3.14.3 venv;
Python 3.12/Ubuntu wheels and installation have not been exercised here. Full
artifact hash-locking and operational GitHub validation remain separate work.
