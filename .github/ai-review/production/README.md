# AI Reviewer: DeepSeek V4.1 Flash 1017 (guarded pilot)

The production workflow is present on `main` but disabled by default. It requires
both `AI_REVIEW_ENABLED=true` and `AI_REVIEW_TARGET_PR` equal to the exact PR
number. Source changes and offline CI never activate paid model calls by themselves.

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
AI_REVIEW_ENABLED == 'true' AND the exact `AI_REVIEW_TARGET_PR` match. The Python gate additionally requires the exact,
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
DeepSeek V4.1 Flash 1017 (`deepseek/deepseek-v4.1-flash-1017`),
temperature 0.3, max_tokens 8192, with no tools or duplicate prompt.
The pinned DeepSeek production request now sends `reasoning.effort=none`
to request non-thinking mode. This is a *pilot*, not proof that every OpenRouter
provider route will honor the field. If a completed response reports positive
`reasoning_tokens` in usage, the reviewer fails closed with the fixed
`reasoning_control_ignored` code. Missing token counters cannot verify this
setting. The quality of a non-thinking code review must also be evaluated.
Previously PR #22 failed with `network_timeout` (44524958394) and later
`model_output_limit` (44526208632).
Low effort alone did not resolve the output limit. On 2026-11-19, PR #22 run
44527690287, both attempts, returned model_output_limit even after PR #24
was merged. The source code does not establish whether the budget was
spent on hidden reasoning or visible JSON. Do not infer either without token
usage from OpenRouter Activity.

Run 44529754321 provided conclusive token counters:
16,384 completion tokens = 15,097 reasoning + 1,287 visible JSON tokens.
This confirms hidden reasoning consumed 92.1% of the completion budget even
with `effort=low`. Do NOT increase the budget again to conceal the cause.
The current pilot returns to max_tokens=8192, keeps concise-output guidance,
and requests non-thinking mode. The 5s/180s/240s model transport and
5s/30s/60s GitHub transport are unchanged. Success, provider compliance and
code-review quality need evaluation before any production automation.
A real spending cap must be configured in the OpenRouter account.

On finish_reason=length or verified reasoning opt-out failure, if OpenRouter
supplies usage metadata, logs may include ONLY bounded integer
completion_tokens, reasoning_tokens, and visible_tokens counters. Missing/invalid metadata remains absent. The log
never contains message, prompt, reasoning, secret, raw API error or model
content. No automatic retry occurs on a generation failure. Subsequent
paid tests require a separately approved budget and one explicit activation
after verifying AI_REVIEW_ENABLED=false.

Transport accepts only fixed HTTPS origins, never follows redirects, and bounds
responses (GitHub 2 MiB, OpenRouter 1 MiB) and encoded model requests (256 KiB).
GitHub API transport retains 5s connection, 30s read, and 60s total deadlines.
The OpenRouter model transport initially used 5s/120s/180s after the first
timeout, then increased to 5s/180s/240s together with the larger output
budget. This follows the PR #22 network_timeout on 2026-11-19
(run 44524958394).
A longer bounded deadline accommodates slower non-streaming model responses;
it does not guarantee success, reduce token costs, or prove the provider
completed its work. The existing 10-minute job outer timeout remains.
The connection timeout is passed to HTTPSConnection and the read timeout is
set on the socket before request/headers. The body loop retains that socket
reference across getresponse(), checks the monotonic deadline and applies
min(read_timeout, remaining) before each read1. It closes resources in
finally, including Connection: close and error paths. Actual network
conditions and provider latency may still trigger a timeout.

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
not put credentials in source. The previous model completed an operational smoke review on PR #15, but no
paid quality benchmark has validated this DeepSeek configuration yet.

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

## DeepSeek switch and controlled testing

- Model ID is pinned in the shared `benchmarks/harness/prompt_builder.py` module.
  Production uses that same module; the benchmark retains `--model` to compare
  MiniMax and DeepSeek explicitly without changing production.
- The `ai-review-ci.yml` workflow tests the production core/runner, benchmark
  harness and leak guard on pull requests without API secrets or paid calls.
- Do not mark the synthetic fixture PR #16 ready for review or enable the
  reviewer until a spending limit on the OpenRouter key is independently
  confirmed and an explicit paid-test authorization is provided.
- For one live quality test, first ensure the new model configuration is merged
  to main, set `AI_REVIEW_TARGET_PR=16`, and only then set
  `AI_REVIEW_ENABLED=true`. PR #16 is intentionally Draft and must never merge.
  After the one approved review, switch `AI_REVIEW_ENABLED=false` again.
- A finished model response must still be one assistant JSON message with
  `finish_reason=stop` and pass the existing schema/coordinate validations.
  Any missing, malformed or truncated response is an error, never a clean review.


## Live pilot failure and follow-up (2026-11-17)

- PR #16 triggered the DeepSeek review from trusted main on 2026-11-17.
  The workflow got as far as the model response and failed closed with
  `{"code":"incomplete_response","stage":"model","published":false}`.
  The original response was intentionally not logged, so the precise
  provider finish reason (such as `length`) is **not established**.
- The output budget has been increased from 2500 to 8192 tokens. This is a
  hypothesis-driven compatibility adjustment, not a guarantee of completion;
  reasoning tokens may consume part of the generation budget.
- For `finish_reason` values `length`, `content_filter`, and `error`,
  the transport now raises `model_output_limit`, `model_content_filter`,
  and `model_generation_error` respectively. Unknown or absent values remain
  `incomplete_response`. Only these fixed codes may reach CI logs; never
  log provider-supplied strings, body, token text, raw headers or API keys.
  No truncated or incomplete response may be published; no automatic retry
  is attempted for those finish reasons.
- Offline CI now covers all new diagnostic branches, absence of retry,
  absence of leaked provider content and fail-closed publication behavior.
- Before any additional paid test, independently verify an OpenRouter key
  budget, `AI_REVIEW_ENABLED=false`, and that fixture PR #16 is Draft.
  A new paid attempt requires explicit approval and controlled reactivation.

## GitHub event policy change (2026-12-02)

GitHub has announced a default policy that blocks `pull_request_target`
workflow events in public repositories starting 2026-12-02 unless an
applicable Actions event policy explicitly permits the event. The review
workflow deliberately uses a trusted base checkout and keeps the
explicit repository/PR activation gates. Do not bypass these controls
by checking out or executing untrusted PR code. A repository
administrator must assess the GitHub event policy in Settings > Actions
> Policies and, if appropriate, narrowly allow this trusted reviewer
workflow. This is a separate administrative decision and has not been
changed automatically. Documentation:
https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target


## Localização e apresentação do review (2026-11-17)

- A saída publicada é um comentário Markdown legível em português brasileiro.
  O prompt de sistema exige pt-BR para resumo, escopo, títulos, descrições,
  impacto e recomendações, sem traduzir chaves JSON, enums, paths ou
  referências exatas de código.
- O cabeçalho deriva do ID do modelo realmente enviado à API pelo payload
  confiável, nunca de um texto retornado pelo modelo ou do Pull Request.
  Os nomes conhecidos recebem rótulos amigáveis; IDs futuros usam seu valor
  estritamente validado.
- Textos não confiáveis são escapados para Markdown e HTML, incluindo
  menções (@), links, imagens e cabeçalhos. Evidências de código ficam em
  <pre><code> escapado; backticks arbitrários não podem fechar um fence.
  O marcador de deduplicação permanece no fim do comentário.
- Revisões parciais ou com achados filtrados não republicam o resumo
  original potencialmente enganoso. Respostas com schema inválido continuam
  bloqueadas antes da publicação.
- A nova apresentação vale somente para reviews futuros; comentários já
  publicados no PR #16 não são reescritos.
- A regressão offline cobre localização do prompt, modelo selecionado,
  formatação Markdown segura, defesa contra injeção, deduplicação e filtros,
  sem credenciais ou chamadas pagas.

## JSON mode following PR #22 validation failure (2026-11-19)

Run 44531560731 on PR #22, using merged PR #26, returned
`{"code":"parse_error","stage":"validation","published":false}`.
The model completed its HTTP request, but the trusted validator rejected
its content. No raw content was logged, so the exact malformed output is
unknown; fences/prose, duplicate keys, or invalid JSON are possibilities.

The pinned DeepSeek request now uses `response_format={"type":"json_object"}`
to request JSON at the provider level, and
`provider={"require_parameters":true}` to avoid silently routing to a
provider that cannot accept the required formatting and reasoning settings.
This may fail with an HTTP error if no route is compatible. It is not a
schema guarantee: the trusted Draft 7 validation, scope/SHA checks, strict
parsing, and refusal to publish incomplete responses remain unchanged.

A failed validation may now log a fixed, allowlisted application-owned
`validation_reason`, never upstream content. No automatic retry or new paid
call is introduced. Run all offline regression tests before a paid pilot;
check the OpenRouter key budget and request explicit approval for a new call.
