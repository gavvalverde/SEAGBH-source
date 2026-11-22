# ADR | AI Reviewer v2, issue #68

Status: implementação candidata; implantação sujeita a CI, auditoria e revisão independente.

## Problema

O runner v1 corta o diff acima de 80 KiB, omite testes e pede citações
literais ao Kimi. Falhas de citação e schema nos PRs #65 e #69 impediram
pareceres confiáveis. A meta v2 é revisar código de verdade e não apenas
fazer o quality gate parecer verde.

## Solução proposta

1. planning_v2.py valida todos os patches do compare base_sha...head_sha,
   divide hunks por linhas completas com coordenadas OLD/NEW preservadas e
   calcula manifesto imutável SHA256. Não descarta arquivos por tamanho.
   Se algum limite exceder, falha antes da requisição paga.
2. Cada âncora contém ID determinístico, caminho, coordenada NEW (ou
   referência de remoção), hash do conteúdo e ID de chunk. O Kimi escolhe
   somente IDs fornecidos. contract_v2.py verifica o schema Draft 7 e
   recupera a linha literal da fonte trusted. A âncora localiza o código,
   mas não comprova a causalidade.
3. No estágio de descoberta, analysis_status=complete significa análise
   integral do CHUNK fornecido, não do PR todo. Os demais chunks são
   analisados em chamadas independentes e refutados com o diff completo.
   O diagnóstico operacional separa `v2_model_declared_incomplete` de
   `v2_model_low_confidence`, sem persistir resposta bruta.
   O JSON exige analysis_status=complete; qualquer resposta inconclusiva ou
   de confiança geral baixa retém publicação em vez de aparentar revisão limpa.
   A refutação agora usa um JSON Schema separado de veredictos e identificadores
   opacos `candidate_id`: para CADA candidato, responde `retain`, `refute`
   ou `uncertain` com uma contraprova causal. Cada veredicto também classifica
   `current_state` e descreve `behavior_path` no código PÓS-patch:
   `retain` só pode corresponder a `reachable_regression`; correções
   adicionadas, limitações intencionais e bugs preexistentes são `refute`;
   falta de evidências é `uncertain`. Contradição estruturada impede
   publicação. Isto NÃO verifica automaticamente a verdade da explicação.
   Não reescreve os campos de um
   finding original. O campo auxiliar `countercheck` exige texto com ao menos
   12 caracteres, mas não impõe limite artificial de caracteres: permanecem
   o teto de 4096 tokens por resposta e o máximo de 1 MiB no transporte.
   O texto auxiliar não é publicado, e o schema dos achados originais mantém
   todos os seus limites de tamanho. Candidatos ausentes, duplicados, desconhecidos ou incertos
   retêm toda a revisão, em vez de fingir resultado limpo.
   Cada chunk tem uma chamada de descoberta. Candidatos existentes exigem
   outra chamada de refutação com o diff completo. Se a refutação não couber
   ou falhar, não ocorre publicação ou transformação em findings vazios.
4. Teto preflight: no máximo 8 chunks, 4096 tokens de saída por chamada,
   uma refutação adicional e custo agregado estimado máximo de US$0,20;
   descoberta limitada a 90 KB por chamada e refutação integral a 200 KB,
   com reserva de 50 KB para candidatos e preflight ANTES do primeiro gasto.
   Kimi K2.6/Baidu, non-thinking, sem retries pagos ocultos.
5. Publicação única após releitura do HEAD, marker de deduplicação e
   confirmação. pull_request_target executa exclusivamente scripts do
   workflow SHA trusted; nenhum código de PR é executado.
6. Instruções do provedor incentivam limites bem abaixo do hard max do schema
   para campos de explicação causal (mechanism <= 450 caracteres, hard max 800).
   O schema local não é relaxado nem as respostas são truncadas/corrigidas
   automaticamente. Um excesso continua interrompendo a publicação.
   Logs mostram status, cobertura, uso e códigos fixos, nunca a saída
   bruta do modelo, segredos ou conteúdo dos patches. Falhas de schema
   são classificadas usando SOMENTE palavras-chave e nomes de campos do
   schema confiável (required/type/enum/const/minLength/etc.), nunca dados
   da resposta ou campos inventados pelo LLM.

## Provas e limites

CI offline Python 3.12 executa os testes originais e os novos em
Ubuntu/Windows. O planejador cobre 15 arquivos e mais de 80 KiB sintéticos,
incluindo tests/unit/test_encerramento_gui.py. Há provas de falta de patch,
Unicode/CRLF, âncora inválida, JSON malformado, mudança do HEAD e orçamento.

A cobertura textual total não garante interpretação semântica. O modelo
pode deixar de perceber interações entre arquivos ou produzir falsos
positivos mesmo com esquema validado. Qualquer achado grave requer
reprodução humana e testes específicos antes de merge.

Depois da integração e autorização de gasto, executar UMA revisão real
do PR #65 e registrar hunks enviados, arquivos de teste, interpretação
semântica, tokens e custo. Não tratar check verde como aprovação automática.

## Estratégia de rollout

Preservar APIs v1 para regressão. O CLI de runner.py encaminha ao v2,
enquanto a produção faz checkout apenas de scripts trusted. Rever a
compatibilidade do endpoint com schema v2 antes de liberar rollout.
