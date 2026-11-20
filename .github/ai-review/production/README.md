# AI Reviewer: DeepSeek V4.1 Flash 1017 (opt-in PR reviews)

The workflow is installed on `main` and is opt-in via the repository variable
`AI_REVIEW_ENABLED=true`. Once enabled, it reviews every eligible internal,
non-draft PR targeting `main`, including new commits to those PRs.
`AI_REVIEW_TARGET_PR` is obsolete and ignored. Source changes and offline CI
do not activate paid calls by themselves. Each eligible new PR HEAD can cost
one OpenRouter request; set a strict OpenRouter API key spending limit first.

## How to activate for all development PRs

1. In OpenRouter, create or select a dedicated key for the SEAGBH reviewer
   and configure its spending limit and access. Reuse of the pilot key is
   possible but reduces budget isolation.
2. In GitHub repository Settings > Secrets and variables > Actions:
   verify `OPENROUTER_API_KEY` is available as a secret. Under **Variables**,
   set `AI_REVIEW_ENABLED` to the exact lowercase string `true`.
3. Create a regular **non-draft** PR from a branch inside this repository
   to `main`. The `AI PR Review (DeepSeek Thinking)` workflow should run
   on `opened`, `synchronize`, `reopened` and `ready_for_review`.
   Drafts, forks and PRs to other branches are intentionally ignored.
4. Inspect the Actions result and the bot's COMMENT review. Findings are
   suggestions only: a valid code-line quote is *not* proof of correctness.
   For a held review, check the fixed sanitized log reason. Do not re-run
   a paid job; push a new commit after correcting the issue if appropriate.
5. To pause future paid reviews immediately, set `AI_REVIEW_ENABLED=false`.
   Already queued/in-flight jobs are not cancelled by changing the variable;
   review queued work separately in Actions if a hard stop is necessary.

The workflow never approves, merges, modifies source, or executes PR code.
It is not a required merge status by itself; CI and human judgement remain
independent. No global per-day or per-month quota is implemented in the
repository. The external OpenRouter key/account limit is the financial
kill-switch.

## Conservative same-line collision hold (2026-11-19)

Besides literal diff-line anchors, the publication quality gate now holds the
whole review when two or more findings point to the exact same file and line.
It returns `quality_manual_review_required`, logs only a numeric pair count,
never chooses a "best" finding, and makes no additional paid API call.
Identical locations do NOT prove identical root causes; independent valid bugs
may require manual triage. The shared prompt now asks for a counterfactual
independent-fix test and calibrates severity to demonstrated impact.
This hold remains active during the opt-in production rollout; investigate held
results manually rather than weakening the gate for a higher publication rate.

## Deterministic finding-quality gate and refutation

The trusted prompt now requests explicit changed code, reachable trigger,
failure mechanism, user/data impact and fix, and a **verbatim diff line** for
each finding. It explicitly checks Python sqlite3 DDL transaction defaults,
stdlib imports versus dependencies, and evidence for GitHub Actions failures.
These rules are NOT exemptions for real concurrency, security, schema,
transaction, or CI bugs. The prompt is shared between the benchmark and
production; no ground truth is included in API requests.

The production runner now gates each accepted finding against the literal
NEW-file line at the exact reported path and line. Missing/short/unverifiable
quotes cause **quality_manual_review_required**, a non-zero status without
posting anything; it does not relabel a suspected bug as "clean". It applies
regardless of reported confidence or severity and does not infer truth from
the quote. The GitHub Action prints only a fixed code and the number of
unanchored findings, never provider text or source paths. This is a
conservative release gate and may hold valid bugs for manual examination.
**The production workflow is intentionally advisory.** Monitor false-positive
and false-negative trade-offs on real PRs. File-level findings and findings
whose exact affected line is shorter than the minimum quote length can
require manual handling; the system will not silently treat them as clean.

The offline 23-case suite verifies fixture/evaluator structure without API
calls. Separately, 23 distinct synthetic cases were tested manually using the
DeepSeek thinking profile in November 2026: latest compatible aggregate
TP=17/FP=2/FN=1, F1~91.9% by the automated heuristic, with outstanding
manual semantic triage on `sec-001` and `tst-001`. These metrics do not
establish real-PR accuracy. The standalone benchmark workflow remains manual,
while production PR reviews are enabled only by the owner-controlled variable.

## Trust and activation

The workflow uses `pull_request_target` because executable workflow/Python must
come from the trusted default branch, not the pull request merge commit. The
checkout is pinned to `github.workflow_sha` in the literal base repository, with
credentials persistence, submodules and LFS disabled. The runner also checks
`GITHUB_EVENT_NAME`, repository, `GITHUB_SHA` and checked-out HEAD before clients
are constructed. It never executes or checks out PR code. Python modules,
requirements and schema are loaded exclusively from the same trusted checkout.

Only open, non-draft PRs from gavvalverde/SEAGBH-source to its main branch are
eligible. The repository default branch must be main. The Actions job gate
requires `AI_REVIEW_ENABLED == 'true'` and first attempt
`github.run_attempt == 1`; the Python runner independently rejects all
subsequent run attempts *before credential or API access*. The Python gate
additionally requires the case-sensitive string `true`; absent, false or any
other value means no API request. A first Bash step, without credential
environment variables, checks the literal value and emits only the fixed
outputs enabled=true / enabled=false. Checkout, dependency installation and
the credential-bearing reviewer step all require that output to be true.
Changing this setting activates reviews of subsequent eligible PR events; it
does not retroactively dispatch reviews for existing PRs. GitHub may require
an explicit repository event policy permitting `pull_request_target`.

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

`core.build_payload` delegates to the shared, vetted prompt builder. The
production runner retains the model and requested reasoning profile from
its pilot, but applies a **controlled 64K shared completion-ceiling experiment**:
DeepSeek V4.1 Flash 1017 (`deepseek/deepseek-v4.1-flash-1017`),
temperature 0.3, `max_tokens=65536` and
`reasoning={"max_tokens":16384}`, with JSON mode and strict provider
parameter support. The 65,536 completion-token ceiling is shared between
reasoning and visible JSON. The 16K reasoning request is advisory for this
provider, not a hard cap; no visible tokens are reserved. A 64K reasoning-only
failure remains possible. The model's response is still validated before publication. Production fails before the paid call if
the encoded request exceeds 128 KiB.
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
with `effort=low`. This establishes the cause; the 64K experiment tests
whether more shared completion space yields visible JSON without concealing
the diagnosis.
Historical PR #22 troubleshooting used an earlier non-thinking 8K profile.
The current opt-in PR reviewer uses the experimental ceiling above; the
23-case pilot metrics do not validate this new budget on real PRs.
The model transport now uses 5s/480s/600s, while GitHub transport retains
5s/30s/60s. A real spending cap must be configured on the OpenRouter key.
No claim is made about a hard dollar limit per call or real-PR accuracy.

On success and operational failure, when OpenRouter supplies valid usage
metadata, logs may include ONLY bounded integer completion_tokens,
reasoning_tokens and visible_tokens counters, plus bounded model HTTP elapsed
milliseconds. Missing/invalid metadata remains absent. The log never contains
message, prompt, reasoning, secret, raw API error or model content. No
automatic retry occurs on a generation failure. Subsequent paid tests require
a separately approved budget and one explicit activation
after verifying AI_REVIEW_ENABLED=false.

Transport accepts only fixed HTTPS origins, never follows redirects, and bounds
responses (GitHub 2 MiB, OpenRouter 1 MiB) and encoded model requests (256 KiB).
GitHub API transport retains 5s connection, 30s read, and 60s total deadlines.
The OpenRouter model transport initially used 5s/120s/180s after the first
timeout, then increased to 5s/180s/240s, and now to 5s/480s/600s for this
64K experiment. This follows the PR #22 network_timeout on 2026-11-19
(run 44524958394).
A longer bounded deadline accommodates slower non-streaming model responses;
it does not guarantee success, reduce token costs, or prove the provider
completed its work. The job outer timeout is now 15 minutes.
The connection timeout is passed to HTTPSConnection and the read timeout is
set on the socket before request/headers. The body loop retains that socket
reference across getresponse(), checks the monotonic deadline and applies
min(read_timeout, remaining) before each read1. It closes resources in
finally, including Connection: close and error paths. Actual network
conditions and provider latency may still trigger a timeout.

Production uses `OpenRouter(max_attempts=1)`: it performs a single model POST
per eligible workflow execution and does not retry 429/5xx. The transport
still supports two attempts when explicitly requested by older offline clients,
but not in this production entrypoint. A network failure may have consumed
credits, so even a single attempt does not guarantee a charge-free error.
Both Actions and the Python runner refuse re-run attempts.
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

Cost controls are per execution and per successfully reviewed SHA/version,
not a global spending cap. Every new PR head SHA may trigger another paid call
on `synchronize`; repeated `reopened` / `ready_for_review` events without a
published review can also incur a new call on the same SHA. A failed or held
review has no durable success marker. A manual Actions re-run is blocked to
avoid accidental charges, but the workflow does not provide exactly-once
billing across events. Set a separate OpenRouter key spending limit and monitor
its dashboard. PR content and code excerpts are intentionally transmitted to
OpenRouter; schema validation is not DLP and does not establish finding truth.
Never commit credentials, tokens or production secrets to code.

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

## DeepSeek development PR review rollout

- Model ID remains pinned in `benchmarks/harness/prompt_builder.py`.
  `runner.build_production_payload` explicitly selects the same reasoning
  profile as the paid 23-case benchmark. The general `core.build_payload`
  default is intentionally still non-thinking for standalone legacy tests.
- The `ai-review-ci.yml` workflow tests the production core/runner, benchmark
  harness and leak guard on pull requests, without API secrets or paid calls.
- Do not convert the synthetic fixture PR #16 into a real development PR.
- Switch `AI_REVIEW_ENABLED=true` only after reviewing the merged workflow
  and setting the independent OpenRouter key spending limit.
- A finished model response must still be one assistant JSON message with
  `finish_reason=stop` and pass trusted schema/coordinate and evidence
  checks. A missing, malformed or truncated response is an error, never a
  clean review. Human review remains mandatory for technical correctness.


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

## Contexto de código limitado (primeira etapa)

O Reviewer recebe trechos próximos às alterações dos **mesmos arquivos**
Python modificados sob `src/`, obtidos no SHA fixado do PR. Nunca executa
código nem busca arquivos não alterados. Limites: 3 arquivos, até 32 KiB
por arquivo, até 12 linhas vizinhas, no máximo 4 KiB por excerto e
8 KiB no conjunto de excertos. A contagem de 3 arquivos limita as
**consultas tentadas**, mesmo quando nenhum arquivo é aproveitado.

O contexto adicional é dado **não confiável**. Não expande as coordenadas
de review permitidas; o filtro de SHA e diff permanece obrigatório.
Dados incorretos ou coordenadas inconsistentes falham antes da API paga.
Trechos extras aumentam tokens de entrada, por isso a coleta é limitada.
Nenhuma chave ou configuração financeira é alterada.

## Auditoria textual de evidências (primeira fase, não semântica)

O revisor compara a linha de código indicada em cada achado com a evidência
escrita pelo modelo. Usa exclusivamente linhas de contexto/adição dos patches
já verificados em `parse_patch` e fixados ao SHA do PR. Não executa o código,
não busca dependências extras, não acessa arquivos omitidos nem envia
requisições adicionais ao OpenRouter.

A revisão publicada sinaliza se a evidência cita textualmente o conteúdo
da linha referenciada (mínimo de 12 caracteres significativos, ignorando
diferenças de espaçamento). Se não houver uma citação literal, o resultado
será **não confirmado automaticamente**, e NÃO significa um bug falso.

Mesmo uma citação literal verdadeira **não confirma** a consequência causal
afirmada pelo modelo. No PR #22, citar `conexao.in_transaction` era possível,
mas a conclusão sobre DDL do SQLite não se sustentava no ambiente padrão.
A validação da hipótese técnica ainda exige análise e/ou reprodução real.

Essa fase não bloqueia a publicação de achados, não altera schema nem o
comportamento de filtros SHA/escopo. Os testes utilizam patches simulados
e modelos falsos, sem rede e sem custos.
