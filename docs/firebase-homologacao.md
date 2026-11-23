# Auditoria e homologação das regras Firebase

Continuação da [issue #70](https://github.com/gavvalverde/SEAGBH-source/issues/70) e da [matriz técnica do PR #79](https://github.com/gavvalverde/SEAGBH-source/blob/6c86629296fc0c4d549bf7f67c98098eb7ae65c2/docs/firebase-autorizacao.md). A `main` remota estava em `bcd08aa`. O [PR #69](https://github.com/gavvalverde/SEAGBH-source/pull/69) estava aberto em `fba5dc2` e o [PR #79](https://github.com/gavvalverde/SEAGBH-source/pull/79) estava aberto em `6c86629`. A ferramenta desta etapa fica em uma branch separada, baseada na `main`, e usa um snapshot do #79 como entrada de teste.

**Auditoria implantada pendente.** Nenhuma cópia das regras foi fornecida. A coleta abaixo cabe a um operador já autorizado. Os resultados locais desta etapa só se aplicam à política sintética executada.

## Coleta em modo leitura

1. Use uma sessão institucional já autorizada. Confirme projeto, instância e ambiente no inventário interno antes de abrir o Firebase Console em **Realtime Database → Rules**. Use o acesso de leitura aprovado pela organização. Confirme que a visualização corresponde à revisão publicada, sem editar nem publicar. Se a sessão não mostrar as regras implantadas, registre o bloqueio e encaminhe ao responsável. Não eleve permissões, gere credenciais nem use a aba de dados para amostrar registros. [Papéis IAM do Realtime Database](https://firebase.google.com/docs/projects/iam/roles-predefined-product#realtime-db)
2. Leia a revisão vigente e registre internamente a referência e a data da coleta. Percorra `/`, cada ancestral e cada caminho abaixo. Para anônimo, usuário comum e administrador, registre o efeito de `.read` e `.write` no nó pedido. Inclua variáveis de caminho, condições sobre dados e consultas, e `.validate` separadamente. Uma concessão verdadeira em um ancestral alcança os descendentes. Regra de leitura de um filho não filtra a resposta ao GET do pai. [Herança e atomicidade de leitura](https://firebase.google.com/docs/database/security/core-syntax)
3. Registre quais claims ou listas conferem acesso administrativo, quem pode atribuí-las e por qual componente. Use documentação de identidade e evidência de configuração autorizada. Para as leituras legadas, use o esquema e os campos consumidos pelo código para registrar categorias de exposição. Verifique se o nó também contém chaves, identificadores pessoais ou metadados internos. Se não houver inventário confiável do conteúdo implantado, marque exposição pendente. [Referência das regras](https://firebase.google.com/docs/reference/security/database/)
4. Preencha a matriz com **permitido**, **negado**, **condicional** ou **pendente** para cada perfil. Indique a origem da concessão como raiz, ancestral, caminho exato ou backend. Guarde a regra completa e a identificação da instância apenas no armazenamento restrito do operador. Para compartilhar, use a matriz sanitizada e, quando autorizado, uma cópia de teste que preserve estrutura, precedência, predicados e tipos. Substitua valores privados por fixtures equivalentes. Se uma condição não puder ser reproduzida com dados sintéticos equivalentes, registre a limitação e mantenha o caso pendente.

### Matriz a preencher

As colunas dos três perfis dizem respeito à política implantada. Os critérios da última coluna são os contratos esperados para a liberação.

| Operação | Anônimo | Comum | Administrador | Critério e evidência a coletar |
| --- | --- | --- | --- | --- |
| GET `/`, `/licenses`, `/revoked` e `/updates` | Pendente | Pendente | Pendente | Origem de qualquer concessão ancestral. Anônimo e comum sem listagem no escopo auditado |
| GET `/licenses/{id}` | Pendente | Pendente | Pendente | Leitura legada individual permite `auto_renew`, `expiry_override`, `revoked_at`, `alerts_muted_until` conforme aplicáveis, e `200 null` se ausente. Investigar campos adicionais |
| PUT/PATCH/DELETE `/licenses/{id}` e filhos | Pendente | Pendente | Pendente | Conta comum sem escrita, inclusive em renovação, revogação e vencimento. Validar criação, alteração e exclusão separadamente |
| GET de `/revoked/{id}` | Pendente | Pendente | Pendente | Registrar leitores e consumidores. A fixture restringe leitura a administrador |
| Escrita e exclusão em `/revoked/{id}` | Pendente | Pendente | Pendente | Conta comum sem escrita. Permitir ao administrador a remoção exigida pelo PATCH, mesmo com nó ausente |
| GET `/updates/latest` | Pendente | Pendente | Pendente | Leitura legada sem papel administrativo e com metadados no formato do atualizador. Verificar se a URL e o manifesto expõem somente informações previstas para distribuição |
| PUT/PATCH/DELETE `/updates/latest` | Pendente | Pendente | Pendente | Conta comum sem publicação ou remoção. Atribuição do papel de publicação controlada |
| PATCH `/.json` com os dois caminhos de exclusão | Pendente | Pendente | Pendente | Administrador remove os alvos e preserva vizinhos. Verificar permissões separadas de cada caminho e ausência de escrita genérica pela raiz |

## Executar a homologação

Use Python 3.12, Java 21 e Firebase CLI. O script monta um projeto demo temporário, usa o transporte REST e as contas sintéticas do #79 e imprime apenas o nome e o estado de cada contrato. Reserve as portas locais 19199, 19200, 19440 e 19450. O Firebase CLI pode baixar o emulador oficial na primeira execução. Nunca use uma instância operacional como destino. [Configuração do Local Emulator Suite](https://firebase.google.com/docs/emulator-suite/install_and_configure)

Enquanto o #79 estiver aberto, extraia a referência confirmada sem mudar de branch. Exemplo em PowerShell a partir da raiz:

```powershell
$ref = '6c86629296fc0c4d549bf7f67c98098eb7ae65c2'
git fetch --no-tags origin refs/pull/79/head
if ($LASTEXITCODE -ne 0 -or (git rev-parse FETCH_HEAD) -ne $ref) {
    throw 'Confira o SHA do PR #79 antes de usar uma referência diferente.'
}
$pasta = Join-Path ([IO.Path]::GetTempPath()) ('seagbh-ref-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $pasta | Out-Null
$zip = Join-Path $pasta 'referencia.zip'
git archive --format=zip -o $zip $ref -- tests/integration/firebase
if ($LASTEXITCODE -ne 0) { throw 'Falha ao extrair a referência.' }
Expand-Archive -LiteralPath $zip -DestinationPath $pasta
$infra = Join-Path $pasta 'tests/integration/firebase'
python -B tests/integration/firebase_homologacao.py --referencia $infra --fixture-referencia
```

Quando houver JSON explicitamente fornecido e sanitizado para este teste, hospede-o fora do checkout e use o mesmo script com `--regras-sanitizadas CAMINHO` no lugar de `--fixture-referencia`. O arquivo deve ter o objeto `rules` do RTDB. O modelo atual usa a claim booleana `admin` e contas com as características da fixture. Se a regra implantada depender de outro formato de claim, lista de UIDs, dados ou contexto de consulta, adapte as fixtures para reproduzir essa condição com valores sintéticos antes de usar o resultado como evidência. Preserve o significado das condições ao trocar os valores.

**Evidência da execução local.** Python 3.12.10, Firebase CLI 15.35.0, Temurin 21.0.13.1 e RTDB Emulator 4.11.2. Os oito contratos passaram na fixture de referência. A suíte reprovou os seis controles negativos esperados, envolvendo concessões de leitura e escrita na raiz, escrita herdada de `/licenses`, exclusão de atualização por conta comum, recusa do lado `/revoked` do PATCH administrativo e recusa do GET individual. JSON inválido foi recusado sem ecoar o conteúdo. O teste de ausência exige `200 null`, e a recusa de leitura exige o HTTP de falha de autorização. O #79 já demonstrou a atomicidade do PATCH em caso de recusa. Nesta etapa a pergunta é se a política aceita ou recusa cada operação esperada, inclusive com o nó auxiliar ausente. [Contrato REST e códigos HTTP](https://firebase.google.com/docs/reference/rest/database/)

As regressões offline em Python 3.12.10 passaram com 98 testes de licenciamento e 24 subtestes na base da missão, e 33 testes de Auth na revisão `fba5dc2` do #69 em checkout temporário com PyQt6 offscreen. As duas suítes registraram zero tentativas de conexão.

Os tokens dos emuladores são sintéticos e sem assinatura. A suíte testa autorização do RTDB sob as regras fornecidas e recusa de token inválido no ambiente demo. O #79 observou que um token emulado vencido podia passar quando a regra só checava o papel. Antes da liberação, verifique em homologação separada com tokens assinados a expiração, a revogação e o efeito de remover a claim após renovação. Preserve a distinção entre licença ausente, recusa de acesso e falha de rede. [Tokens do Auth Emulator](https://firebase.google.com/docs/emulator-suite/connect_auth#id_tokens) · [Gerenciamento de sessões](https://firebase.google.com/docs/auth/admin/manage-sessions)

## Autorização, implantação e rollback

- **Identidade autenticada.** O login Firebase estabelece a identidade. Uma conta válida pode abrir a ferramenta no #69. A presença do token e a visibilidade de botões são condições de interface.
- **Autorização administrativa.** Atribua a claim somente por componente confiável com Firebase Admin SDK, identidade própria e trilha de auditoria. O RTDB ou backend verifica a claim em cada escrita. Quando a revogação imediata for exigida, teste um mecanismo explícito do servidor para negar a sessão revogada. Previna concessões amplas em ancestrais. [Custom claims](https://firebase.google.com/docs/auth/admin/custom-claims)
- **Histórico local e chaves.** Aplique permissões do sistema operacional ao diretório e aos backups do operador. Controle acesso a cópia e exportação em máquinas compartilhadas. O armazenamento e o HMAC legado exigem uma avaliação ou migração separada.

Antes de qualquer implantação aprovada, registre a revisão das regras em armazenamento restrito e ensaie a mudança e o rollback numa instância de homologação separada. Compare códigos HTTP, formato dos dois GETs anônimos e comportamento em um exemplar de cada versão distribuída. Em especial, um GET de licença recusado entra no caminho offline, com limite de 72 h. Um `200 null` significa exclusão e pode apagar a licença local. Interrompa diante de qualquer regressão, escrita indevida ou PATCH administrativo recusado. Restaure a revisão aprovada de regras em janela controlada, confirme novamente os GETs e a recusa de escrita e verifique que o rollback preserva o papel administrativo. Se a última revisão reabrir uma permissão indevida, mantenha a operação administrativa suspensa até decisão humana.

### Condições para liberar o PR #69

1. Operador aprova a matriz efetiva, incluindo os ancestrais e o esquema dos nós lidos anonimamente.
2. A política derivada, com dados sanitizados, aprova os oito contratos locais. Testes adicionais cobrem condições específicas da política ausentes na fixture.
3. Em homologação separada, uma conta comum recebe recusa em licenças, revogações e atualizações. Um administrador consegue executar o PATCH. Os clientes distribuídos consultam a licença individual e o manifesto de atualização sem login administrativo.
4. Há evidência sobre tokens assinados, expiração e revogação conforme o nível de acesso desejado. O responsável aprova a estratégia de proteção do histórico e o rollback de compatibilidade.
5. CI e revisões apontam para o HEAD candidato à integração. Se houver atualização da base ou dos arquivos do PR, revalide os contratos afetados na revisão final.

As regras implantadas, a exposição de campos reais e a compatibilidade com todo o parque instalado permanecem pendentes. Nenhum acesso ao ambiente Firebase de produção, alteração de contas ou atribuição de claims reais fez parte desta etapa.
