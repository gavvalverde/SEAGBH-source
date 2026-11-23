# Auditoria e homologação das regras Firebase

Continuação da [issue #70](https://github.com/gavvalverde/SEAGBH-source/issues/70). O [PR #69](https://github.com/gavvalverde/SEAGBH-source/pull/69) usa a infraestrutura dos PRs #79 e #80 incorporada à `main`.

O operador informou uma política de administrador único baseada em `auth.uid`. A fixture representa esse contrato com uma UID fictícia. A conferência direta da revisão implantada e dos campos reais dos nós públicos cabe ao operador autorizado, pelo procedimento de leitura abaixo.

## Coleta em modo leitura

1. Use uma sessão institucional já autorizada. Confirme projeto, instância e ambiente no inventário interno antes de abrir o Firebase Console em **Realtime Database → Rules**. Use o acesso de leitura aprovado pela organização. Confirme que a visualização corresponde à revisão publicada, sem editar nem publicar. Se a sessão não mostrar as regras implantadas, registre o bloqueio e encaminhe ao responsável. Não eleve permissões, gere credenciais nem use a aba de dados para amostrar registros. [Papéis IAM do Realtime Database](https://firebase.google.com/docs/projects/iam/roles-predefined-product#realtime-db)
2. Leia a revisão vigente e registre internamente a referência e a data da coleta. Percorra `/`, cada ancestral e cada caminho abaixo. Para anônimo, usuário comum e administrador, registre o efeito de `.read` e `.write` no nó pedido. Inclua variáveis de caminho, condições sobre dados e consultas, e `.validate` separadamente. Uma concessão verdadeira em um ancestral alcança os descendentes. Regra de leitura de um filho não filtra a resposta ao GET do pai. [Herança e atomicidade de leitura](https://firebase.google.com/docs/database/security/core-syntax)
3. Registre quais claims ou listas conferem acesso administrativo, quem pode atribuí-las e por qual componente. Use documentação de identidade e evidência de configuração autorizada. Para as leituras legadas, use o esquema e os campos consumidos pelo código para registrar categorias de exposição. Verifique se o nó também contém chaves, identificadores pessoais ou metadados internos. Se não houver inventário confiável do conteúdo implantado, marque exposição pendente. [Referência das regras](https://firebase.google.com/docs/reference/security/database/)
4. Preencha a matriz com **permitido**, **negado**, **condicional** ou **pendente** para cada perfil. Indique a origem da concessão como raiz, ancestral, caminho exato ou backend. Guarde a regra completa e a identificação da instância apenas no armazenamento restrito do operador. Para compartilhar, use a matriz sanitizada e, quando autorizado, uma cópia de teste que preserve estrutura, precedência, predicados e tipos. Substitua valores privados por fixtures equivalentes. Se uma condição não puder ser reproduzida com dados sintéticos equivalentes, registre a limitação e mantenha o caso pendente.

### Matriz da política por UID

As permissões abaixo correspondem ao contrato informado e à fixture. A coluna de anônimo cobre requisições sem token e uma conta emitida pelo provedor anônimo do Auth Emulator. A conta administrativa possui exatamente a UID configurada na fixture.

| Operação | Anônimo | Comum | Administrador | Condição observada |
| --- | --- | --- | --- | --- |
| Listar raiz ou coleção | Negado | Negado | Negado | Sem concessão ancestral |
| GET `/licenses/{id}` | Permitido | Permitido | Permitido | ID com 12 hexadecimais minúsculos. Ausente retorna HTTP 200 com `null` |
| Escrita em `/licenses/{id}` e filhos | Negado | Negado | Permitido | ID no formato esperado. Revogar e reativar gravam ou removem `revoked_at` |
| GET `/revoked/{id}` | Negado | Negado | Negado | Inclui nó ausente |
| Escrita e exclusão em `/revoked/{id}` | Negado | Negado | Permitido | O PATCH pode remover um nó auxiliar ausente |
| GET `/updates/{id}` | Permitido | Permitido | Permitido | Inclui `latest` e leitura individual de outras versões |
| Escrita em `/updates/{id}` | Negado | Negado | Permitido | Publicação individual |
| PATCH `/.json` com os dois caminhos de exclusão | Negado | Negado | Permitido | A recusa preserva ambos os alvos. O sucesso preserva os vizinhos |
| Escrita genérica na raiz, coleção ou caminho externo | Negado | Negado | Negado | A autorização por filho vale somente nos caminhos permitidos |

## Executar a homologação

Use Python 3.12, Java 21 e Firebase CLI. O script monta um projeto demo temporário, usa o transporte REST e as contas sintéticas do #79 e imprime apenas o nome e o estado de cada contrato. Reserve as portas locais 19199, 19200, 19440 e 19450. O Firebase CLI pode baixar o emulador oficial na primeira execução. Nunca use uma instância operacional como destino. [Configuração do Local Emulator Suite](https://firebase.google.com/docs/emulator-suite/install_and_configure)

Execute a suíte por UID a partir da raiz:

```sh
python -B tests/integration/firebase/run.py --politica-uid
```

A fixture mantém a regra por UID sem validações de conteúdo adicionais. Os testes distinguem HTTP 200 com `null`, recusa de autorização e HTTP 400 para uma chave incompatível com a sintaxe do RTDB. O contrato informado não inclui um esquema `.validate` por campo. A fixture original por claim continua disponível via `run.py` sem a opção `--politica-uid` e pelos modos do `firebase_homologacao.py`. Ela testa uma política diferente, inclusive controles negativos de atomicidade. [Contrato REST e códigos HTTP](https://firebase.google.com/docs/reference/rest/database/)

### Limites de tokens e expiração

O Auth Emulator emite ID tokens sem assinatura e os emuladores os aceitam para testes locais. A suíte por UID verifica rejeição de tokens inválidos e a identidade retornada em renovações reais do Auth Emulator. As regressões Qt verificam a expiração local pelo relógio monotônico, inclusive a recusa de continuar após renovação inválida ou com UID divergente. O teste anterior do #79 observou aceitação de um token sem assinatura vencido quando a regra checava só o papel. A fixture por UID preserva a regra informada sem acrescentar checagem de expiração. A validade criptográfica, a expiração no RTDB real e a revogação imediata exigem evidência separada com tokens assinados em homologação. [Tokens do Auth Emulator](https://firebase.google.com/docs/emulator-suite/connect_auth#id_tokens) · [Gerenciamento de sessões](https://firebase.google.com/docs/auth/admin/manage-sessions)

## Autorização, implantação e rollback

- O login estabelece a identidade pelo Firebase. A ferramenta exige correspondência exata com `SEAGBH_FIREBASE_ADMIN_UID` para abrir a janela, acessar o histórico e prosseguir após renovar. O RTDB aplica a autorização definitiva de escrita em cada caminho a partir do ID token. [Condições do RTDB](https://firebase.google.com/docs/database/security/rules-conditions)
- `tools/historico_chaves.json` e `tools/historico_excluidos.json` são lidos sob demanda e gravados em JSON durante operações administrativas. Ambos estão rastreados no Git. Neste checkout, as ACLs são herdadas e incluem uma concessão de leitura a um grupo amplo. A inspeção desta etapa se limitou à existência, ao rastreamento e às ACLs dos arquivos. O conteúdo e o acesso efetivo por outros usuários ainda precisam de auditoria autorizada.
- Para proteger os históricos, reserve uma conta operacional individual, restrinja as ACLs do diretório e dos backups a essa conta e às identidades de manutenção necessárias, e teste o acesso com outra conta. Audite cópias e revisões do Git em ambiente restrito. Se houver chaves reais disponíveis a terceiros, avalie exposição e trate reemissão e remoção do histórico do repositório em uma missão de correção separada. A interface controla o acesso da aplicação. A proteção contra leitura pelo sistema operacional depende das ACLs e da remoção de cópias acessíveis.

Antes de qualquer implantação aprovada, registre a revisão das regras em armazenamento restrito e ensaie a mudança e o rollback numa instância de homologação separada. Compare códigos HTTP, formato dos dois GETs anônimos e comportamento em um exemplar de cada versão distribuída. Em especial, um GET de licença recusado entra no caminho offline, com limite de 72 h. Um `200 null` significa exclusão e pode apagar a licença local. Interrompa diante de qualquer regressão, escrita indevida ou PATCH administrativo recusado. Restaure a revisão aprovada de regras em janela controlada, confirme novamente os GETs e a recusa de escrita e verifique que o rollback preserva o papel administrativo. Se a última revisão reabrir uma permissão indevida, mantenha a operação administrativa suspensa até decisão humana.

### Condições para liberar o PR #69

1. Operador aprova a matriz efetiva, incluindo os ancestrais e o esquema dos nós lidos anonimamente, e configura a chave Web do mesmo projeto da instância RTDB e a UID administrativa no perfil autorizado.
2. A fixture por UID aprova os contratos locais, inclusive as duas variantes do PATCH, os GETs legados, a rejeição de IDs inválidos e a preservação de papéis na renovação.
3. Em homologação separada, uma conta comum recebe recusa em licenças, revogações e atualizações. Um administrador consegue executar o PATCH. Os clientes distribuídos consultam a licença individual e o manifesto de atualização sem login administrativo.
4. Há evidência sobre tokens assinados, expiração e revogação conforme o nível de acesso desejado. O responsável aprova a estratégia de proteção do histórico e o rollback de compatibilidade.
5. CI e revisões apontam para o HEAD candidato à integração. Se houver atualização da base ou dos arquivos do PR, revalide os contratos afetados na revisão final.

A leitura direta da revisão implantada, a exposição de campos reais e a compatibilidade com todo o parque instalado permanecem pendentes. Se houver falha da ferramenta após a atualização, suspenda o uso administrativo e mantenha a política RTDB. Uma reversão não deve recolocar uma versão da ferramenta que permita à conta comum ler os históricos locais.
