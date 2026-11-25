# Firebase #70: evidências e decisão de liberação

**Base de leitura:** `main`, commit `0382a4284c018084d821ff9e618d83535a6da1ea`.

**Decisão desta etapa:** a [issue #70](https://github.com/gavvalverde/SEAGBH-source/issues/70) permanece aberta; não há evidência suficiente para liberar o Gerenciamento em produção. Nenhum serviço, conta, regra, configuração remota ou histórico real foi consultado ou alterado nesta etapa.

Este registro complementa a [matriz de homologação](firebase-homologacao.md), a [arquitetura de autorização](firebase-autorizacao.md) e os roteiros de [histórico privado](historico-administrativo-privado.md) e [proteção no Git](protecao-historicos-publicos.md). Ele não substitui esses procedimentos.

## O que está comprovado e o que não está

| Camada | Evidência local | Aceite ainda pendente |
| --- | --- | --- |
| Autenticação administrativa no desktop | `src/core/firebase_auth.py` exige a UID administrativa configurada no login e na renovação; usa prazo monotônico e invalida sessões divergentes. `GerenciamentoClientes` exige sessão ativa antes de construir a janela e ler o histórico. O #69 final já tem esse filtro local. | Identidade e projeto compatíveis com a instância autorizada; recusa no **RTDB**, independentemente da interface. |
| Permissões e validação no RTDB | A fixture por UID representa o contrato informado; não é exportação verificada da revisão implantada. `licenses/{id}` e `updates/{id}` têm leituras individuais públicas na fixture. Listagens e leitura de `revoked/{id}` não são concedidas. Escritas estão limitadas à UID e às regras de cada caminho. | Conferir as regras publicadas a partir da raiz, origem da autoridade e campos efetivamente expostos. Separar autenticação, decisão de escrita e validação de dados. |
| Sessão expirada, inválida ou revogada | Testes locais exercitam recusa/renovação no cliente. Emulador usa tokens sem assinatura e a fixture UID não acrescenta condição explícita de expiração. | Evidência separada em homologação aprovada com tokens assinados, expirados e revogados. Não inferir revogação imediata pela política emulada. |
| PATCH composto de exclusão | O Gerenciamento solicita uma alteração atômica dos dois caminhos do contrato. Os testes existentes da fixture cobrem o nó auxiliar presente ou ausente e a preservação dos registros se a operação for recusada. | Confirmar o contrato com dados sintéticos na política equivalente e no ambiente autorizado de homologação, nunca com licenças reais. |
| Histórico local e público | O ponto de entrada normal ainda seleciona o armazenamento legado. O #83 adiciona SQLite privado opt-in e ACLs/permissões, com dados em claro no perfil autorizado; o #84 impede alterações novas reconhecidas no Git, mas não remove as cópias legadas já rastreadas. Nesta etapa foram vistos apenas metadados Git dos dois arquivos, não seu conteúdo nem ACLs reais. | Operador comprova diretório, arquivos, backups e permissões efetivas em máquina compartilhada antes de ativar o armazenamento privado; classifica a exposição histórica separadamente. |

### Matriz mínima de evidência para o operador

Preencha **cada célula da política implantada** no registro restrito com **P** (permitido), **N** (negado), **C** (condicional) ou **?** (não confirmado). Comece com **?**; os resultados abaixo são do contrato por UID e não devem ser copiados como estado operacional.

| Operação | Sem autenticação | Outra conta autenticada | UID administrativa válida | Sessão inválida, expirada ou revogada |
| --- | --- | --- | --- | --- |
| GET de licença individual | P | P | P | Avaliar rejeição do token separadamente de uma requisição genuinamente anônima |
| GET de manifesto de atualização individual, inclusive `latest` | P | P | P | Mesmo critério das leituras públicas |
| GET de raiz, coleções e `revoked/{id}` | N | N | N | N esperado |
| Escrever licença, revogar/reativar, gravar manifesto | N | N | C: UID, caminho e validações | N exigido para liberar; comprovar em homologação assinada |
| PATCH de exclusão nos dois caminhos | N: sem efeito | N: sem efeito | P para a operação específica; sem alterações em vizinhos | N exigido para liberar; comprovar em homologação assinada |
| Escrita genérica fora dos caminhos aprovados | N | N | N | N esperado |

Confirme também a conta emitida pelo provedor anônimo, caso ele esteja habilitado, separadamente de uma requisição sem token. Identifique a fonte de cada conclusão (raiz, ancestral, regra exata, condição ou teste), sem copiar expressões com dados operacionais. Uma escrita de administrador pode ser autorizada e mesmo assim ser recusada por validação. Um GET de coleção não retorna uma versão filtrada pelos direitos de cada filho. A leitura de um nó público abrange os campos presentes naquele nó, não apenas aqueles que a aplicação usa.

## Sessão de leitura autorizada e prova de compatibilidade

1. **Antes de abrir o console:** confirmar operador, ambiente e instância esperados e autorização de leitura já existente. Se não for possível identificar a revisão publicada ou o acesso for insuficiente, parar e manter **?**. Não ampliar permissões, criar contas ou consultar dados para inspecionar conteúdo.
2. **Coletar somente metadados e a política:** consultar a revisão publicada em **Realtime Database → Rules** sem editar ou publicar, conforme a [documentação do Firebase Console](https://firebase.google.com/docs/rules/get-started#access_your_rules). Guardar a revisão, a data, a autorização e a referência da evidência apenas em local privado. Comparar permissões de raiz, ancestrais e filhos, identidade administrativa e categorias de campos públicos contra o esquema aprovado. Não colar regras, UID, endpoints privados, valores de configuração, tokens, respostas ou conteúdos de históricos em GitHub ou chat.
3. **Homologar fora da produção:** validar a equivalência da política sanitizada sem mudar predicados, herança ou tipos. A suíte `tests/integration/firebase/run.py --politica-uid` é a entrada da fixture por UID. O executor `tests/integration/firebase_homologacao.py` foi montado sobre os contratos da fixture histórica por claim: por exemplo, exige recusa na leitura de `updates/anterior`, enquanto a fixture por UID permite essa leitura individual. Não usar a reprovação desse contrato histórico para diagnosticar a política UID, nem tratá-lo como certificação de equivalência. Selecionar ou adaptar o contrato em trabalho separado de teste, sem alterações operacionais.
4. **Examinar versões realmente distribuídas:** para cada versão em suporte, registrar versão, variante, cenário e resultado (sem artefatos ou dados reais). Usar ambiente separado, contas e registros inventados. Confirmar GET legado de licença ativa, revogada e de teste ausente; rede negada/indisponível é tratada como offline, não como exclusão. Para atualizações, conferir leitura do manifesto, versão interpretada e indicação no app sem ativar o atualizador real. O cliente pode apresentar erro de consulta como ausência de atualização. Não extrapolar um teste no código de `main` para o executável distribuído.
5. **Validar armazenamento administrativo:** durante janela separada autorizada, confirmar cópia/snapshot privado, restauração e conferência de integridade, conta separada sem acesso efetivo e destino dos backups/exportações. Não desrastrear arquivos de forma que um pull possa eliminar cópias locais. Se houver evidência de exposição, encaminhar tratamento em incidente restrito; proteger o futuro não apaga o passado.
6. **Critério de interrupção e recuperação:** se houver escrita indevida, perda dos GETs legados, interpretação não esperada de `null`, PATCH parcial/recusado para o perfil aprovado ou falha de autorização/permissões locais, parar a liberação. Manter o Gerenciamento suspenso no caso de dúvida. Um rollback futuro deve estar ensaiado em homologação, com a política anterior identificada e previamente aprovada, repetição dos testes positivos e negativos e sem regressão da autorização. Não reverter para uma revisão sabidamente permissiva; encaminhar ao responsável e manter a suspensão. Nenhum passo desta seção autoriza publicação ou alteração remota.

**Resumo sanitizado a registrar:** referência interna opaca da revisão; data UTC; papel do operador (sem identificação pessoal); estado por operação e classe de identidade; categoria dos dados públicos; versões/variantes testadas; aprovação humana de liberação e rollback ou bloqueador restante. Sem credenciais, identificadores operacionais, regras reais ou valores coletados.

## Verificações locais desta entrega

- Python 3.12 e PyQt6 real em modo offscreen no Linux: `python -B -W error::ResourceWarning -m unittest -v tests.unit.test_firebase_auth tests.unit.test_historico_licencas`: **66 testes aprovados**.
- `python -B -m pytest -q -p no:cacheprovider tests/unit/licenciamento`: **98 testes e 24 subtestes aprovados**. Aviso de depreciação de plugin externo do pytest; sem falhas.
- `python -B tests/integration/firebase/run.py --politica-uid`: parada no pré-requisito local, pois o Firebase CLI não estava disponível. Java 21 estava disponível. Nenhum emulador foi iniciado e resultados históricos de emulador não foram reexecutados.
- Windows, assinaturas/expiração/revogação remotas, inventário de instalações, leitura da revisão operacional e teste de ACL efetiva em máquinas reais **não foram verificados**.

**Próxima decisão:** manter a issue aberta até o operador anexar somente o resumo sanitizado, terminar a homologação compatível com o parque instalado e aprovar o plano de liberação e rollback.
