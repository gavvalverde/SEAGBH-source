"""Testes offline da outbox local de sincronização (somente stdlib)."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from uuid import UUID

from src.core.sync_outbox import (
    confirmar_entrega,
    criar_estrutura_outbox,
    listar_pendentes,
    registrar_alteracao,
)


EMPRESA_X = UUID(int=11)
EMPRESA_Y = UUID(int=12)
DEVICE_1 = UUID(int=21)
EQUIP_1 = UUID(int=31)
EQUIP_2 = UUID(int=32)
EVENTO_1 = UUID(int=41)


class OutboxSQLite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "estoque.db"
        self.conn = sqlite3.connect(self.path)
        self.addCleanup(self.conn.close)
        self.conn.execute("PRAGMA foreign_keys=ON")
        criar_estrutura_outbox(self.conn)
        self.conn.execute("CREATE TABLE equipamentos_local (id TEXT PRIMARY KEY, nome TEXT)")
        self.conn.commit()

    def _gravar(self, *, empresa=EMPRESA_X, reg=EQUIP_1,
                id_evento=None, tipo="equipamentos", operacao="upsert",
                dados=None):
        if not self.conn.in_transaction:
            self.conn.execute("BEGIN")
        return registrar_alteracao(
            self.conn, empresa_id=empresa, dispositivo_id=DEVICE_1,
            tipo_registro=tipo, registro_id=reg, operacao=operacao,
            dados={"nome": "Projetor"} if dados is None else dados,
            evento_id=id_evento)

    def test_gravacao_operacional_e_evento_sao_atomicos(self):
        with self.conn:
            self.conn.execute("INSERT INTO equipamentos_local VALUES (?, ?)",
                              (str(EQUIP_1), "Projetor"))
            self._gravar()
        self.assertEqual(len(listar_pendentes(self.conn, empresa_id=EMPRESA_X)), 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM equipamentos_local")
                         .fetchone()[0], 1)

    def test_rollback_remove_registro_e_evento_juntos(self):
        with self.assertRaisesRegex(RuntimeError, "falha simulada"):
            with self.conn:
                self.conn.execute("INSERT INTO equipamentos_local VALUES (?, ?)",
                                  (str(EQUIP_1), "Projetor"))
                self._gravar()
                raise RuntimeError("falha simulada")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM equipamentos_local")
                         .fetchone()[0], 0)
        self.assertEqual(listar_pendentes(self.conn, empresa_id=EMPRESA_X), [])

    def test_reabre_banco_e_preserva_evento_id_para_reenvio(self):
        with self.conn:
            eid = self._gravar()
        other = sqlite3.connect(self.path)
        try:
            entries = listar_pendentes(other, empresa_id=EMPRESA_X)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].evento_id, eid)
            self.assertEqual(entries[0].dispositivo_id, DEVICE_1)
            self.assertEqual(entries[0].dados, {"nome": "Projetor"})
        finally:
            other.close()

    def test_isolamento_por_empresa_nas_consultas(self):
        with self.conn:
            self._gravar(empresa=EMPRESA_X)
            self._gravar(empresa=EMPRESA_Y, reg=EQUIP_2)
        x = listar_pendentes(self.conn, empresa_id=EMPRESA_X)
        y = listar_pendentes(self.conn, empresa_id=EMPRESA_Y)
        self.assertEqual([e.registro_id for e in x], [EQUIP_1])
        self.assertEqual([e.registro_id for e in y], [EQUIP_2])

    def test_confirmacao_so_atinge_evento_da_empresa_correta(self):
        with self.conn:
            eid = self._gravar()
        with self.conn:
            self.conn.execute("BEGIN")
            self.assertFalse(confirmar_entrega(
                self.conn, empresa_id=EMPRESA_Y, evento_id=eid))
            self.assertTrue(confirmar_entrega(
                self.conn, empresa_id=EMPRESA_X, evento_id=eid))
            self.assertFalse(confirmar_entrega(
                self.conn, empresa_id=EMPRESA_X, evento_id=eid))
        self.assertEqual(listar_pendentes(self.conn, empresa_id=EMPRESA_X), [])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM seagbh_sync_outbox")
                         .fetchone()[0], 1)

    def test_rollback_da_confirmacao_preserva_reenvio(self):
        with self.conn:
            eid = self._gravar()
        with self.assertRaises(RuntimeError):
            with self.conn:
                self.conn.execute("BEGIN")
                self.assertTrue(confirmar_entrega(
                    self.conn, empresa_id=EMPRESA_X, evento_id=eid))
                raise RuntimeError("crash antes de salvar ACK")
        self.assertEqual(listar_pendentes(self.conn, empresa_id=EMPRESA_X)[0].evento_id,
                         eid)

    def test_confirmacao_sem_transacao_e_rejeitada(self):
        with self.conn:
            eid = self._gravar()
        with self.assertRaises(RuntimeError):
            confirmar_entrega(self.conn, empresa_id=EMPRESA_X, evento_id=eid)

    def test_registro_sem_transacao_e_rejeitado(self):
        with self.assertRaises(RuntimeError):
            registrar_alteracao(
                self.conn, empresa_id=EMPRESA_X, dispositivo_id=DEVICE_1,
                tipo_registro="equipamentos", registro_id=EQUIP_1,
                operacao="upsert", dados={})

    def test_ordem_por_sequencia_e_paginacao_limitada(self):
        with self.conn:
            ids = [
                self._gravar(reg=EQUIP_1, id_evento=EVENTO_1),
                self._gravar(reg=EQUIP_2),
                self._gravar(reg=EQUIP_1, operacao="delete", dados={}),
            ]
        items = listar_pendentes(self.conn, empresa_id=EMPRESA_X, limite=2)
        self.assertEqual([e.evento_id for e in items], ids[:2])
        self.assertEqual([e.sequencia for e in items], [1, 2])
        self.assertEqual(len(listar_pendentes(self.conn, empresa_id=EMPRESA_X)), 3)

    def test_evento_duplicado_nao_fica_silencioso(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with self.conn:
                self._gravar(id_evento=EVENTO_1)
                self._gravar(id_evento=EVENTO_1)
        self.assertEqual(listar_pendentes(self.conn, empresa_id=EMPRESA_X), [])

    def test_payload_e_unicode_sao_serializados_sem_perda(self):
        with self.conn:
            self._gravar(dados={"descrição": "Áudio / iluminação",
                                "quantidade": 4, "itens": ["cabo", "tripé"]})
        item = listar_pendentes(self.conn, empresa_id=EMPRESA_X)[0]
        self.assertEqual(item.dados["descrição"], "Áudio / iluminação")
        self.assertEqual(item.dados["quantidade"], 4)

    def test_payload_invalido_e_rejeitado(self):
        for dados in (["não é dict"], {"v": float("nan")},
                      {"v": object()}, {"v": "x" * 65536}):
            with self.subTest(tipo=str(type(dados))):
                with self.assertRaises(ValueError):
                    with self.conn:
                        self._gravar(dados=dados)
        self.assertEqual(listar_pendentes(self.conn, empresa_id=EMPRESA_X), [])

    def test_entradas_invalidas_sao_rejeitadas(self):
        with self.conn:
            for changes in (
                {"empresa": UUID(int=0)},
                {"reg": "nao-e-uuid"},
                {"tipo": "equipamentos;drop table"},
                {"operacao": "truncate"},
            ):
                with self.subTest(changes=changes):
                    with self.assertRaises(ValueError):
                        self._gravar(**changes)
            self.conn.rollback()
        self.assertEqual(listar_pendentes(self.conn, empresa_id=EMPRESA_X), [])

    def test_limite_de_leitura_e_validado(self):
        for limit in (0, -1, 1001, True, "100"):
            with self.subTest(limit=limit):
                with self.assertRaises(ValueError):
                    listar_pendentes(self.conn, empresa_id=EMPRESA_X, limite=limit)

    def test_schema_idempotente_e_preserva_eventos(self):
        with self.conn:
            self._gravar()
        criar_estrutura_outbox(self.conn)
        self.assertEqual(len(listar_pendentes(self.conn, empresa_id=EMPRESA_X)), 1)


if __name__ == "__main__":
    unittest.main()
