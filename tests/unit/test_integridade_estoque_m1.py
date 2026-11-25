"""Regressões M1 no caminho Database usado pelas telas, com SQLite inventado."""
from datetime import datetime
import os
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

    def test_legado_com_retorno_anterior_a_saida_nao_libera_equipamento(self):
        db, caminho = self.db, self.caminho
        item = _equip(db, "0001")
        outro = _equip(db, "0002")
        evento = _evento(db, "Evento", outro)
        for tipo in ("retorno", "saida"):
            _sql(
                caminho,
                "INSERT INTO movimentacoes (evento_id, equipamento_id, data_movimentacao, tipo)"
                " VALUES (?, ?, '01/12/2026 09:00', ?)",
                (evento, item, tipo),
            )
        antes = _movimentos(caminho)
        assert item not in {e[0] for e in db.listar_equipamentos_disponiveis()}
        with self.assertRaises(ValueError) as erro:
            db.localizar_equipamento("0001")
        assert erro.exception.codigo == "estado_legado_inconsistente"
        with self.assertRaises(ValueError):
            _evento(db, "Não libera a saída", item)
        with self.assertRaises(ValueError) as erro_conclusao:
            db.registrar_retorno(evento, "0002")
        assert erro_conclusao.exception.codigo == "estado_legado_inconsistente"
        assert db.obter_evento(evento)["status"] == "Agendado"
        assert _movimentos(caminho) == antes == [
            (evento, outro, "saida"), (evento, item, "retorno"), (evento, item, "saida"),
        ]


class TestDatasEventos(TestCase):
    def setUp(self):
        self.caminho = Path(self.enterContext(TemporaryDirectory())) / "datas-sinteticas.db"
        self.enterContext(patch.object(database, "_load_env_file"))
        self.enterContext(patch.object(database, "get_db_path", return_value=str(self.caminho)))
        self.enterContext(patch.dict(os.environ, {"SEAGBH_DB_ENGINE": "sqlite"}))
        self.db = database.Database()
        self.addCleanup(self.db.fechar)
        self.db.cadastrar_equipamento("0001", "Mesa", "", "")
        self.item = self.db.obter_id_por_codigo("0001")

    def criar(self, inicio, fim, item=None):
        ids = [] if item is None else [item]
        return self.db.criar_evento("Evento", "Sala", "Equipe", inicio, fim, ids)

    def _movimentos(self):
        conn = sqlite3.connect(self.caminho)
        try:
            return conn.execute("SELECT evento_id, equipamento_id, tipo FROM movimentacoes").fetchall()
        finally:
            conn.close()

    def test_criar_recusa_data_impossivel_nao_bissexta_ou_periodo_invertido(self):
        for inicio, fim in (("31/02/2026", "01/03/2026"),
                            ("28/02/2025", "29/02/2025"),
                            ("02/03/2026", "01/03/2026"),
                            ("01/03/2026 17:01", "01/03/2026 17:00"),
                            ("01/03/2026", "01/03/2026 24:00")):
            with self.subTest(inicio=inicio, fim=fim):
                with self.assertRaises(ValueError):
                    self.criar(inicio, fim, self.item)
                self.assertEqual(self.db.listar_eventos(), [])
                self.assertEqual(self._movimentos(), [])

    def test_bissexto_e_horarios_validos_nao_mudam_semantica_da_saida(self):
        evento = self.criar("29/02/2028 13:00", "29/02/2028 13:00:05", self.item)
        self.assertEqual(self.db.obter_evento(evento)["data_fim"], "29/02/2028 13:00:05")
        self.assertEqual(self._movimentos(), [(evento, self.item, "saida")])
        self.assertTrue(self.db.registrar_retorno(evento, "0001").evento_concluido)
        self.assertEqual(self.db.eventos_atrasados(), [])

    def test_editar_invalido_nao_altera_nenhum_campo_ou_movimento(self):
        evento = self.criar("29/02/2028 12:00", "29/02/2028", self.item)
        antes = self.db.obter_evento(evento)
        with self.assertRaises(ValueError):
            self.db.atualizar_evento(evento, "Outro", "Outro", "Outra",
                                     "29/02/2028", "31/02/2028")
        self.assertEqual(self.db.obter_evento(evento), antes)
        self.assertEqual(self._movimentos(), [(evento, self.item, "saida")])
        self.db.atualizar_evento(evento, "Outro", "Outro", "Outra",
                                 "01/03/2028", "01/03/2028 01:00")
        self.assertEqual(self.db.obter_evento(evento)["nome_evento"], "Outro")
        self.assertEqual(self._movimentos(), [(evento, self.item, "saida")])

    def test_edicao_reverte_falha_sqlite_depois_da_escrita(self):
        evento = self.criar("01/08/2026", "02/08/2026", self.item)
        antes = self.db.obter_evento(evento)
        self.db.cursor.execute("""
            CREATE TRIGGER falha_apos_edicao AFTER UPDATE OF data_fim ON eventos
            BEGIN SELECT RAISE(FAIL, 'falha sintética na edição'); END
        """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "falha sintética na edição"):
            self.db.atualizar_evento(evento, "Outro", "Outro", "Outra",
                                     "01/08/2026", "03/08/2026")
        self.assertEqual(self.db.obter_evento(evento), antes)
        self.assertEqual(self._movimentos(), [(evento, self.item, "saida")])

    def test_atraso_por_hora_e_por_dia_com_pendencia(self):
        self.db.cadastrar_equipamento("0002", "Caixa", "", "")
        segundo = self.db.obter_id_por_codigo("0002")
        com_hora = self.criar("01/08/2026", "01/08/2026 12:00", self.item)
        sem_hora = self.criar("01/08/2026", "01/08/2026", segundo)
        self.criar("01/07/2026", "01/07/2026")  # não tem saída pendente
        with patch.object(database, "datetime", wraps=datetime) as relogio:
            relogio.now.return_value = datetime(2026, 8, 1, 12)
            self.assertEqual(self.db.eventos_atrasados(), [])
            relogio.now.return_value = datetime(2026, 8, 1, 12, 0, 1)
            self.assertEqual([r[0] for r in self.db.eventos_atrasados()], [com_hora])
            relogio.now.return_value = datetime(2026, 8, 2)
            self.assertEqual({r[0] for r in self.db.eventos_atrasados()}, {com_hora, sem_hora})
            self.db.registrar_retorno(com_hora, "0001")
            self.assertEqual([r[0] for r in self.db.eventos_atrasados()], [sem_hora])

    def test_legado_invalido_e_sinalizado_sem_regravar(self):
        evento = self.criar("01/08/2026", "02/08/2026", self.item)
        self.db.cursor.execute("UPDATE eventos SET data_inicio = ? WHERE id = ?", ("31/02/2026", evento))
        self.db.conn.commit()
        antes = self.db.obter_evento(evento)
        with patch.object(database, "datetime", wraps=datetime) as relogio:
            relogio.now.return_value = datetime(2026, 8, 3)
            self.assertEqual(self.db.eventos_atrasados(), [])
            atrasados, invalidos = self.db.classificar_eventos_com_pendencias()
        self.assertEqual(atrasados, [])
        self.assertEqual(invalidos, [(evento, "Evento", "02/08/2026", 1)])
        self.assertEqual(self.db.obter_evento(evento), antes)
        self.assertEqual(self._movimentos(), [(evento, self.item, "saida")])
