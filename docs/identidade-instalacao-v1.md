# SEAGBH | Identidade persistente de instalação (V1)

Status: componente local isolado; nenhum fluxo de licença existente foi alterado.

## Por que separar

- `company_id`: empresa proprietária dos dados; não depende da chave.
- `license_id`: contratação atual; pode ser substituída ao renovar ou retornar.
- `device_id`: identificação estável da instalação, independentemente da chave
  comercial. Sozinho NÃO autoriza o uso nem comprova a posse do computador.
- `activation_id`: ativação concedida pelo backend após checar o limite.

A lógica do domínio está em `src/core/multiativacao_dominio.py`. O módulo
`src/core/identidade_instalacao.py` fornece um UUID v4 persistente local.

## Persistência e concorrência

A interface pública recebe um `Path` explícito; não adivinha o diretório
global e não escreve junto do executável nem em `.licenca.dat`.

Na primeira inicialização, cria um UUID v4, escreve em arquivo temporário
no mesmo diretório, força a escrita dos bytes e publica sem sobrescrever
(através de um hard link). Execuções concorrentes obtêm o mesmo UUID.
Requer filesystem com hard links, como NTFS. Se o recurso não for suportado,
falha sem substituir o identificador existente.

Um UUID inválido, truncado, não canônico ou de versão diferente de 4
provoca erro explícito; nunca é trocado silenciosamente.
A leitura aceita o UUID v4 canônico sem terminador, com LF (gravado pela
aplicação em modo binário) ou com um CRLF padrão de texto do Windows. Rejeita
CR isolado, terminadores duplicados e sufixos adicionais. O arquivo escrito
pela aplicação mantém sempre a representação canônica ASCII + LF. Isso evita que
corrupção gere nova vaga de licença sem uma operação de recuperação.

## Integração futura com Windows

A localização definitiva deve ser *por computador*, compartilhada entre
contas Windows autorizadas a usar a mesma instalação. O instalador deverá
definir a pasta de dados persistente e suas ACLs, preferencialmente sob
`%PROGRAMDATA%`, **fora** da pasta do programa. Não selecionar essa pasta
nem alterar seu conteúdo neste PR: o empacotamento e permissões precisam de
uma avaliação própria em Windows.

Um administrador local ainda pode copiar ou substituir esse UUID. O backend
não deve tratá-lo como autenticador. A próxima fase precisa de uma credencial
de posse do dispositivo (ex.: chave criptográfica com proteção do sistema),
protocolo de ativação e revogação, autorização assinada pelo servidor e
reserva atômica de vagas. Não armazenar segredo de emissão no cliente.

## Execução de testes

```bash
python -B -m unittest -v tests.unit.licenciamento.test_identidade_instalacao
```

O teste usa apenas biblioteca padrão e filesystem temporário, sem conexão
Firebase, Supabase ou custos do OpenRouter.

Não integra `licenca_ativa`, não modifica `src/core/database.py`, não
cria registros de empresa remotos e não altera o comportamento do aplicativo.
