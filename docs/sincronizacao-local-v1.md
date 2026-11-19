# SEAGBH | Fila local para sincronização offline-first

**Estado:** fundação isolada, sem integração com a interface ou Supabase.

O aplicativo usa SQLite para equipamentos, eventos, movimentações e manutenção.
A futura sincronização precisa de uma fila persistente de operações (outbox)
gravada no MESMO SQLite e na MESMA transação das alterações operacionais.

## Contrato da V1

`src/core/sync_outbox.py` oferece:
- `criar_estrutura_outbox(conexao)`: adiciona só a tabela e o índice da fila;
- `registrar_alteracao(...)`: registra alteração, com UUID de evento para
  idempotência, sem commit e exigindo transação aberta;
- `listar_pendentes(conexao, empresa_id, limite)`: consulta ordenada pela
  sequência do SQLite, nunca pelo relógio do dispositivo;
- `confirmar_entrega(...)`: marca o evento como confirmado após ACK validado
  do backend, sem apagar o histórico e sem fazer commit.

Campos: sequencia, evento_id, empresa_id, dispositivo_id, tipo_registro,
registro_id, operacao (upsert/delete), dados_json, criado_em e confirmado_em.

Exemplo para futura integração, após a identidade da empresa ser validada:

```python
with conn:
    conn.execute("UPDATE equipamentos SET nome=? WHERE id=?", (nome, local_id))
    registrar_alteracao(
        conn, empresa_id=empresa_id_validada, dispositivo_id=dispositivo_id,
        tipo_registro="equipamentos", registro_id=uuid_estavel,
        operacao="upsert", dados={"nome": nome},
    )
```

O comando acima é somente um padrão; o código atual de `Database` ainda
utiliza IDs locais inteiros e não foi alterado. A integração real exigirá o
mapeamento de IDs estáveis, autorização e atomicidade em cada método de escrita.

## Garantias e limites

- A gravação do negócio e da outbox podem ser confirmadas ou desfeitas juntas.
- Um crash antes do ACK local mantém o evento para reenvio.
- Somente o backend pode validar empresa, autenticação, versão do registro
  e permissão para modificar dados. O `empresa_id` local não é autenticação.
- O backend deverá tratar cada `evento_id` de forma idempotente, inclusive
  se a rede cair após o commit remoto.
- O cliente não pode possuir `service_role` nem senha PostgreSQL privilegiada.
- Não há rede, worker, processamento de conflitos, proteção contra clock skew,
  retenção, autenticação ou assinatura criptográfica neste módulo.
- Não há qualquer alteração no banco remoto, na licença existente ou nos
  métodos atuais de `Database`.

## Próximas etapas

1. Introduzir IDs globais estáveis e o escopo `company_id` nas tabelas
   operacionais, com migração local segura e testes.
2. Integrar os métodos de escrita do `Database` à outbox, transacionalmente.
3. Criar backend Supabase protegido por Auth/RLS com restrições de tenant.
4. Implementar entrega idempotente, pull de alterações remotas e resolução
   de conflitos; só então habilitar sincronização de produção.
5. Substituir backups inseguros de SQLite por snapshots consistentes e
   implementar backup/restauração lógica por empresa separadamente.

## Testes

Executar sem rede, credenciais ou dependências externas:

```bash
python -B -m unittest -v tests.unit.test_sync_outbox
```

O workflow GitHub correspondente executa esses testes em cada alteração do
módulo e não tem permissão de escrita ou secrets.
