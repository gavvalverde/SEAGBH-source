# Estabilização do AI Reviewer Kimi K2.6

**Estado:** PR #59 em Draft. Produção `main` ainda usa Kimi K2.6 via Baidu sem raciocínio, JSON mode anterior. Nenhuma execução documentada neste relatório publicou comentário nos PRs avaliados.

## Causas verificadas

- PR #16: revisão inicialmente retida pelo quality gate (`quality_manual_review_required`) por cinco citações sem âncora literal. Um trecho citado não é, por si só, prova de defeito real.
- PR #28: resposta `incomplete_json` com 4.626 tokens de conclusão, inferior ao teto de 8.192; não se trata de um motivo suficiente para aumentar o limite total.

## Alterações propostas no PR #59

1. `response_format: json_schema` (`strict: false`), com projeção remota da especificação Draft 7. A regra condicional `allOf` continua no **validador local**, que segue autoritativo para formato, escopo e SHA. Nenhum JSON malformado é corrigido ou publicado.
2. `temperature=0.0` só no perfil de produção Kimi. O contrato DeepSeek usado nos benchmarks históricos não muda.
3. Catálogo de citações derivado **somente das linhas de diff já verificadas pelo parser**, incluindo caminhos, linhas e texto literal. São no máximo 100 linhas / 18.000 bytes; se o catálogo completo não couber, ele é omitido. Não aumenta o escopo de revisão nem trata a citação como prova causal.
4. Diagnósticos por código fixo e contadores, sem registrar código-fonte ou texto livre: `quote_mismatch`, `source_unavailable`, `short_line`, `invalid_reference`. Achados não ancorados retêm a revisão inteira.
5. Preservados: Kimi `moonshotai/kimi-k2.6`, Baidu fixado, `reasoning.enabled=false`, máximo de 8.192 tokens, tetos de tarifa 0,42/1,75 USD por milhão de tokens, uma tentativa, sem fallback, sem postagem de conteúdo não validado.

## Experimentos reais (20/11/2026)

| Execução | Resultado |
| --- | --- |
| [#44790743639](https://github.com/gavvalverde/SEAGBH-source/actions/runs/44790743639) | Smoke de `json_schema` aprovado no Baidu com JSON válido, 30 tokens de conclusão, zero de raciocínio reportado, US$ 0,000209 |
| [#44790916457](https://github.com/gavvalverde/SEAGBH-source/actions/runs/44790916457) | Avaliação read-only: PR #16 com 2 âncoras inválidas; PR #28 atingiu a publicação simulada |
| [#44791935487](https://github.com/gavvalverde/SEAGBH-source/actions/runs/44791935487) | Com prompt anterior, três tentativas do PR #16 retidas; PR #28 apresentou violação de schema |
| [#44792425130](https://github.com/gavvalverde/SEAGBH-source/actions/runs/44792425130) | Temperatura 0 sem catálogo reteve 5 achados do PR #16. Com catálogo de 31 linhas, duas tentativas passaram com 4 e 6 achados; PR #28 passou com 2 achados |
| [#44793218405](https://github.com/gavvalverde/SEAGBH-source/actions/runs/44793218405) | End-to-end completo com catálogo: 2/2 execuções do PR #16 e 1/1 do PR #28 passaram até o bloqueio intencional de postagem; zero âncoras inválidas. Custo combinado reportado US$ 0,009227 |
| [#44793464975](https://github.com/gavvalverde/SEAGBH-source/actions/runs/44793464975) | Kimi com pensamento e saída total 8.192: 3/3 respostas esgotaram o orçamento, inclusive solicitando `reasoning.max_tokens=2048` |
| [#44794260951](https://github.com/gavvalverde/SEAGBH-source/actions/runs/44794260951) | Pensamento com saída total 16.384: PR #16 gastou **9.442 tokens pensando**, apesar de pedir 2.048, antes de entregar JSON válido (6 achados ancorados). PR #28 consumiu **todos os 16.384 tokens pensando**, sem resposta visível. Custos: US$ 0,019414 e US$ 0,028466 |

**Conclusão sobre raciocínio no Baidu:** a limitação solicitada a 2.048 tokens **não foi obedecida nos testes reais**. A saída total continua limitada, mas pode ser esgotada sem parecer; isso gera custo e erro operacional. O piloto com pensamento foi retirado do perfil candidato após o diagnóstico. Não existe evidência suficiente para habilitá-lo em produção.

O teste de outro provedor econômico, Chutes, está documentado separadamente no PR, para verificar se a restrição é de roteamento. Sem resultado positivo e reproduzível, manter raciocínio desativado.

## Aceitação, segurança e pendências

- CI offline Python 3.12 inclui testes de runner, core, transporte, evidências, schema, 317 verificações sintéticas e 23 casos Leak Guard.
- Todos os testes reais usaram SHAs imutáveis dos PRs, acesso de leitura ao GitHub, nenhuma execução do código de PRs, nenhuma retentativa de inferência e nenhuma publicação.
- O quality gate de evidência **não valida verdade causal nem mede falsos positivos**. Ainda é necessário confrontar os achados com gabarito independente e testes de execução de bugs.
- Workflows de chamadas pagas são temporários, de disparo único, e deverão ser removidos do diff antes do merge.
- Não se alteram secrets, bancos de dados, licenciamento, políticas SQLite nem automações pagas permanentes.
- A integração exige CI do último commit, revisão da política de privacidade do Baidu e decisão explícita de promover o PR.

Referências: https://openrouter.ai/docs/guides/best-practices/reasoning-tokens e https://openrouter.ai/docs/guides/features/structured-outputs
