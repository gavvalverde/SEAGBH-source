# SEAGBH | Multiativação V1: contratos e isolamento

Status: proposta técnica em implementação; nenhuma mudança no licenciamento
legado nem na produção do Firebase ou do Supabase nesta etapa.

## Identidades independentes

- Empresa: company_id (UUID imutável). É dona dos dados operacionais.
- Licença: license_id (UUID). Refere-se a uma empresa, plano e limite de
  dispositivos. Pode ser encerrada e substituída sem alterar company_id.
- Instalação: device_id (UUID persistente por instalação). Identifica uma
  instalação autorizada; sozinho NÃO comprova a posse do dispositivo.
- Ativação: activation_id (UUID). Relaciona empresa, licença, dispositivo
  e situação (ativa/revogada).
- Chave de ativação: credencial de apresentação inicial; NÃO é chave primária
  de empresa, identificador de banco ou credencial permanente de dados.

Exemplo: a empresa X usa licença L1 em três dispositivos. Ao aumentar o
limite de 3 para 4, um novo dispositivo pode solicitar a quarta ativação
usando a mesma chave vigente. Se a licença L1 terminar e a empresa voltar,
uma licença L2 pode manter o company_id e os dados da empresa.

## Escopo do primeiro PR

O módulo puro src/core/multiativacao_dominio.py oferece:
- Tipos imutáveis: Empresa, Licenca, Ativacao.
- Enumerações de estados e decisões.
- Avaliação determinística de uma vaga, incluindo reuso da mesma instalação,
  limite ocupado e ativação revogada.
- Rejeição de vínculos de outras empresas/licenças ou dados inconsistentes.

Os testes são executáveis localmente sem credenciais ou conexão:

    python -B -m unittest -v tests.unit.licenciamento.test_multiativacao_dominio

A avaliação de vaga não reserva uma vaga e não confere autenticidade da chave.
Não é seguro ligá-la diretamente à tela de ativação nem ao banco de dados.

## Arquitetura a implementar depois

1. Backend confiável com autenticação da solicitação, prova de posse do
   dispositivo e verificação da licença.
2. Reserva atômica de vaga, com idempotência para o mesmo dispositivo.
   Exigir exclusividade de ativação ativa por licença/dispositivo e impedir
   duas solicitações concorrentes de consumirem a última vaga.
3. Emissão de autorização assinada com chave assimétrica; chave privada
   somente no backend, chave pública no cliente.
4. Verificação no startup, renovação e política offline, revisando as 72 h
   atuais e comportamento sob relógio do sistema não confiável.
5. Isolamento de dados operacionais no Supabase por company_id com RLS
   baseada em identidade autenticada. O client não pode escolher um
   company_id arbitrário para acessar dados; nenhuma service_role no PC.
6. Sincronização entre SQLite local e Postgres em etapa separada, com regras
   para conflitos e restauração de backups individuais por empresa.
7. Administração de empresas, licenças, limites, dispositivos e auditoria.

## Restrições de segurança

- O segredo HMAC presente no atual src/core/licenca.py não é adequado para
  emitir novas credenciais assimétricas no cliente.
- O device_id é um identificador, não uma autenticação; futuramente uma
  chave criptográfica do dispositivo deve comprovar a posse da instalação.
- O backend deve controlar as mudanças de limite, revogação e reativação.
- Não gravar segredos administrativos do Firebase ou Supabase no executável.
- Não vincular dados do almoxarifado ao license_id ou ao device_id.
- Manter PRs pequenos, testes de concorrência, isolamento por tenant e
  migração do fluxo antigo antes de ativar o novo em produção.
