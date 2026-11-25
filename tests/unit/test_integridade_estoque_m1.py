"""Regressões M1 no caminho Database usado pelas telas, com SQLite inventado."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest import TestCase
from unittest.mock import patch

from src.core import database


def _equip(db, codigo):
    db.cadastrar_equipamento(codigo, "Equipamento " + codigo, "", "Estoque")
    return db.obter_id_por_codigo(codigo)


def _evento(db, nome, *ids):
    return db.criar_evento(nome, "Local", "Responsável", "10/12/2026", "11/12/2026", ids)


def _movimentos(caminho):
    conn = sqlite3.connect(caminho)
    try:
        return conn.execute(
            "SELECT evento_id, equipamento_id, tipo FROM movimentacoes ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


def _sql(caminho, sql, params=()):
    conn = sqlite3.connect(caminho)
    try:
        with conn:
            conn.execute(sql, params)
    finally:
        conn.close()


class TestIntegridadeEstoqueM1(TestCase):
    def setUp(self):
        pasta = Path(self.enterContext(TemporaryDirectory()))
        self.caminho = pasta / "estoque-sintetico.sqlite3"
        self.enterContext(patch.object(database, "_load_env_file"))
        self.enterContext(patch.object(database, "get_db_path", return_value=str(self.caminho)))
        self.enterContext(patch.dict("os.environ", {"SEAGBH_DB_ENGINE": "sqlite"}))
        self.db = database.Database()
        self.addCleanup(self.db.fechar)

    def test_b01_criacao_recusa_segunda_saida_sem_gravar_evento(self):
        db, caminho = self.db, self.caminho
        item = _equip(db, "0001")
        primeiro = _evento(db, "Primeiro", item)
        with self.assertRaises(ValueError):
            _evento(db, "Segundo", item)
        assert {e[0] for e in db.listar_eventos()} == {primeiro}
        assert _movimentos(caminho) == [(primeiro, item, "saida")]

    def test_b01_edicao_recusa_item_pendente_em_outro_evento(self):
        db, caminho = self.db, self.caminho
        item = _equip(db, "0001")
        outro = _equip(db, "0002")
        primeiro = _evento(db, "Primeiro", item)
        segundo = _evento(db, "Segundo", outro)
        with self.assertRaises(ValueError):
            db.adicionar_equipamento_evento(segundo, item)
        assert _movimentos(caminho) == [(primeiro, item, "saida"), (segundo, outro, "saida")]

    def test_b02_retorno_repetido_nao_duplica_e_ultimo_conclui(self):
        db, caminho = self.db, self.caminho
        item = _equip(db, "0001")
        evento = _evento(db, "Evento", item)
        db.registrar_retorno(evento, "0001")
        db.registrar_retorno(evento, "0001")
        assert _movimentos(caminho) == [(evento, item, "saida"), (evento, item, "retorno")]
        assert db.obter_evento(evento)["status"] == "Concluído"

    def test_b02_retorno_exige_saida_e_codigo_exato_com_zeros(self):
        db, caminho = self.db, self.caminho
        item = _equip(db, "0001")
        _equip(db, "1")
        evento = _evento(db, "Evento", item)
        with self.assertRaises(ValueError):
            db.registrar_retorno(evento, "1")
        assert _movimentos(caminho) == [(evento, item, "saida")]
        db.registrar_retorno(evento, "0001")
        assert _movimentos(caminho)[-1] == (evento, item, "retorno")

    def test_r01_saida_ativa_impede_manutencao(self):
        db, _ = self.db, self.caminho
        item = _equip(db, "0001")
        evento = _evento(db, "Evento", item)
        with self.assertRaises(ValueError):
            db.cadastrar_saida_manutencao(item, "Oficina")
        assert db.listar_manutencao() == []
        assert db.contar_pendentes(evento) == 1
        assert item not in {e[0] for e in db.listar_equipamentos_disponiveis("0001")}

    def test_r01_manutencao_ativa_impede_saida_e_fica_visivel_na_busca(self):
        db, caminho = self.db, self.caminho
        item = _equip(db, "0001")
        db.cadastrar_saida_manutencao(item, "Oficina")
        assert db.localizar_equipamento("0001")["tipo"] == "manutencao"
        assert item not in {e[0] for e in db.listar_equipamentos_disponiveis()}
        with self.assertRaises(ValueError):
            _evento(db, "Evento", item)
        assert _movimentos(caminho) == []
        db.registrar_retorno_manutencao(db.listar_manutencao()[0][0])
        assert item in {e[0] for e in db.listar_equipamentos_disponiveis()}

    def test_b06_retorno_parcial_atualiza_localizacao_individual(self):
        db, caminho = self.db, self.caminho
        item = _equip(db, "0001")
        outro = _equip(db, "0002")
        evento = _evento(db, "Evento", item, outro)
        assert db.localizar_equipamento("0001")["tipo"] == "evento"
        db.registrar_retorno(evento, "0001")
        assert db.localizar_equipamento("0001") is None
        assert db.localizar_equipamento("0002")["nome_evento"] == "Evento"
        assert db.obter_evento(evento)["status"] == "Agendado"
        assert {e[0] for e in db.listar_equipamentos_disponiveis()} == {item}
        assert _movimentos(caminho) == [
            (evento, item, "saida"), (evento, outro, "saida"), (evento, item, "retorno")
        ]

    def test_rollback_ao_falhar_segunda_saida(self):
        db, caminho = self.db, self.caminho
        item = _equip(db, "0001")
        outro = _equip(db, "0002")
        _sql(caminho, f"""
            CREATE TRIGGER teste_falha_saida BEFORE INSERT ON movimentacoes
            WHEN NEW.equipamento_id = {outro}
            BEGIN SELECT RAISE(ABORT, 'falha sintética na segunda saída'); END
        """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "falha sintética"):
            _evento(db, "Evento que falha", item, outro)
        assert db.listar_eventos() == []
        assert _movimentos(caminho) == []
        assert _evento(db, "Depois da falha", item)

    def test_rollback_do_ultimo_retorno_se_conclusao_falhar(self):
        db, caminho = self.db, self.caminho
        item = _equip(db, "0001")
        evento = _evento(db, "Evento", item)
        _sql(caminho, """
            CREATE TRIGGER teste_falha_conclusao BEFORE UPDATE OF status ON eventos
            WHEN NEW.status = 'Concluído'
            BEGIN SELECT RAISE(ABORT, 'falha sintética na conclusão'); END
        """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "falha sintética"):
            db.registrar_retorno(evento, "0001")
        assert db.obter_evento(evento)["status"] == "Agendado"
        assert _movimentos(caminho) == [(evento, item, "saida")]
        _sql(caminho, "DROP TRIGGER teste_falha_conclusao")
        db.registrar_retorno(evento, "0001")
        assert db.obter_evento(evento)["status"] == "Concluído"

    def test_contencao_duas_conexoes_reais_confirmam_so_uma_saida(self):
        db, caminho = self.db, self.caminho
        item = _equip(db, "0001")
        partida = Barrier(2, timeout=10)

        def tentar(nome):
            conexao = database.Database()
            try:
                partida.wait()
                try:
                    return _evento(conexao, nome, item)
                except ValueError:
                    return None
            finally:
                conexao.fechar()

        with ThreadPoolExecutor(max_workers=2) as executor:
            resultados = list(executor.map(tentar, ("A", "B")))
        confirmados = [eid for eid in resultados if eid is not None]
        assert len(confirmados) == 1, resultados
        assert [e[0] for e in db.listar_eventos()] == confirmados
        assert _movimentos(caminho) == [(confirmados[0], item, "saida")]

    def test_conflito_legado_nao_e_apagado_nem_piorado(self):
        db, caminho = self.db, self.caminho
        item = _equip(db, "0001")
        primeiro = _evento(db, "Primeiro", item)
        outro = _equip(db, "0002")
        segundo = _evento(db, "Segundo", outro)
        _sql(
            caminho,
            "INSERT INTO movimentacoes (evento_id, equipamento_id, data_movimentacao, tipo)"
            " VALUES (?, ?, '01/12/2026 09:00', 'saida')",
            (segundo, item),
        )
        antes = _movimentos(caminho)
        with self.assertRaises(ValueError):
            _evento(db, "Não cria terceira saída", item)
        with self.assertRaises(ValueError) as erro:
            db.localizar_equipamento("0001")
        assert erro.exception.codigo == "estado_legado_inconsistente"
        with self.assertRaises(ValueError):
            db.registrar_retorno(primeiro, "0001")
        assert _movimentos(caminho) == antes == [
            (primeiro, item, "saida"), (segundo, outro, "saida"), (segundo, item, "saida"),
        ]
        assert item not in {e[0] for e in db.listar_equipamentos_disponiveis()}

    def test_codigo_numerico_nao_encontra_codigo_com_zeros(self):
        db = self.db
        item = _equip(db, "0001")
        assert db.obter_id_por_codigo("0001") == item
        assert db.obter_id_por_codigo("1") is None

    def test_reutilizacao_apos_retorno_preserva_movimentos_anteriores(self):
        db, caminho = self.db, self.caminho
        item = _equip(db, "0001")
        primeiro = _evento(db, "Primeiro", item)
        db.registrar_retorno(primeiro, "0001")
        segundo = _evento(db, "Segundo", item)
        assert db.localizar_equipamento("0001")["nome_evento"] == "Segundo"
        assert _movimentos(caminho) == [
            (primeiro, item, "saida"), (primeiro, item, "retorno"), (segundo, item, "saida"),
        ]
