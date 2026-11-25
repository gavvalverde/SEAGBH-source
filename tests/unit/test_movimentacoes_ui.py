"""Entradas Qt de movimentação, sem abrir banco operacional."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox, QPushButton
except ImportError:
    raise unittest.SkipTest("PyQt6 indisponível: regressões de movimentação Qt não executadas.")

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from core import database
from core.database import ErroMovimentacao, ResultadoRetorno
from ui.dialogs import AdicionarEquipEventoDialog, CriarEventoDialog, RetornoDialog
from ui.tab_equipamentos import TabEquipamentos
from ui.tab_eventos import TabEventos
from ui.tab_manutencao import TabManutencao


class MovimentacoesUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def _mostrar(self, widget):
        self.addCleanup(widget.close)
        widget.show()
        self.app.processEvents()
        return widget

    @staticmethod
    def _escanear(campo, codigo):
        QTest.keyClicks(campo, codigo)
        QTest.keyClick(campo, Qt.Key.Key_Return)

    def test_ultimo_retorno_consulta_codigo_exato_e_usa_fechamento_do_banco(self):
        db = Mock()
        db.pendentes_retorno.return_value = [("007", "Equipamento")]
        db.devolvidos_retorno.return_value = []
        db.registrar_retorno.return_value = ResultadoRetorno(
            registrado=True, evento_concluido=True,
        )
        dlg = self._mostrar(RetornoDialog(db, 42))
        with patch.object(QMessageBox, "information") as info:
            self._escanear(dlg.entry_codigo, "007")
        db.registrar_retorno.assert_called_once_with(42, "007")
        db.concluir_evento.assert_not_called()
        self.assertEqual(dlg.result(), QDialog.DialogCode.Accepted.value)
        self.assertTrue(dlg._evento_concluido)
        info.assert_called_once()

    def test_repeticao_chega_ao_banco_mesmo_ausente_da_lista_pendente(self):
        db = Mock()
        db.pendentes_retorno.return_value = [("008", "Equipamento restante")]
        db.devolvidos_retorno.return_value = [("007", "Equipamento devolvido", "12:00")]
        db.registrar_retorno.return_value = ResultadoRetorno(
            registrado=False, evento_concluido=False,
        )
        dlg = self._mostrar(RetornoDialog(db, 42))
        with patch.object(QMessageBox, "information") as info:
            self._escanear(dlg.entry_codigo, "007")
        db.registrar_retorno.assert_called_once_with(42, "007")
        db.concluir_evento.assert_not_called()
        self.assertEqual(dlg.list_pend.count(), 1)
        self.assertEqual(dlg.list_dev.count(), 1)
        self.assertFalse(dlg._evento_concluido)
        self.assertEqual(info.call_args.args[1], "Retorno já registrado")

    def test_criacao_respeita_recusa_no_momento_de_confirmar_saida(self):
        db = Mock()
        db.criar_evento.side_effect = ErroMovimentacao(
            "equipamento_indisponivel", "Equipamento indisponível.",
        )
        dlg = self._mostrar(CriarEventoDialog(db, "Evento", "Local", "Equipe", "01/08/2026", "02/08/2026"))
        db.obter_id_por_codigo.return_value = 4
        db.localizar_equipamento.return_value = None
        db.obter_nome_equipamento.return_value = "Equipamento"
        self._escanear(dlg.entry_codigo, "007")
        db.criar_evento.assert_not_called()  # Enter do scanner não aciona Finalizar.
        botao = next(b for b in dlg.findChildren(QPushButton) if "Finalizar" in b.text())
        with patch.object(QMessageBox, "warning") as aviso:
            QTest.mouseClick(botao, Qt.MouseButton.LeftButton)
        db.criar_evento.assert_called_once_with(
            "Evento", "Local", "Equipe", "01/08/2026", "02/08/2026", [4], "",
        )
        db.registrar_auditoria.assert_not_called()
        self.assertNotEqual(dlg.result(), QDialog.DialogCode.Accepted.value)
        self.assertEqual(aviso.call_args.args[1], "Evento não criado")

    def test_edicao_nao_mostra_item_recusado_pelo_banco(self):
        db = Mock()
        db.obter_id_por_codigo.return_value = 4
        db.adicionar_equipamento_evento.side_effect = ErroMovimentacao(
            "equipamento_indisponivel", "Equipamento indisponível.",
        )
        dlg = self._mostrar(AdicionarEquipEventoDialog(db, 42))
        with patch.object(QMessageBox, "warning") as aviso:
            self._escanear(dlg.entry, "007")
        db.obter_id_por_codigo.assert_called_once_with("007")
        db.adicionar_equipamento_evento.assert_called_once_with(42, 4)
        self.assertEqual(dlg.tabela.rowCount(), 0)
        self.assertTrue(dlg.isVisible())  # O Enter também não aciona o OK.
        self.assertEqual(aviso.call_args.args[1], "Equipamento não adicionado")

    def test_manutencao_preserva_formulario_quando_saida_e_recusada(self):
        db = Mock()
        db.listar_manutencao.return_value = []
        db.obter_id_por_codigo.return_value = 4
        db.cadastrar_saida_manutencao.side_effect = ErroMovimentacao(
            "equipamento_indisponivel", "Equipamento indisponível.",
        )
        aba = self._mostrar(TabManutencao(db))
        aba.entry_codigo.setText("007")
        aba.entry_local.setText("Oficina")
        with patch.object(QMessageBox, "warning") as aviso:
            aba._on_cadastrar_saida()
        db.cadastrar_saida_manutencao.assert_called_once_with(4, "Oficina")
        self.assertEqual(aba.entry_codigo.text(), "007")
        self.assertEqual(aba.entry_local.text(), "Oficina")
        self.assertEqual(aviso.call_args.args[1], "Saída não registrada")

    def test_localizacao_sem_saida_pendente_ou_manutencao_ativa(self):
        db = Mock()
        db.listar_eventos.return_value = []
        db.localizar_equipamento.return_value = None
        aba = self._mostrar(TabEventos(db))
        with patch.object(QMessageBox, "information") as aviso:
            self._escanear(aba.entry_search, "007")
        db.localizar_equipamento.assert_called_once_with("007")
        self.assertEqual(aviso.call_args.args[1], "Sem movimentação ativa")

    def test_nome_indisponivel_depois_da_saida_nao_indica_falha_na_gravacao(self):
        db = Mock()
        db.obter_id_por_codigo.return_value = 4
        db.obter_nome_equipamento.side_effect = OSError()
        dlg = self._mostrar(AdicionarEquipEventoDialog(db, 42))
        with patch.object(QMessageBox, "warning") as aviso:
            self._escanear(dlg.entry, "007")
        db.adicionar_equipamento_evento.assert_called_once_with(42, 4)
        self.assertEqual(dlg.tabela.item(0, 0).text(), "007")
        self.assertEqual(aviso.call_args.args[1], "Saída registrada")

    def test_lista_falha_depois_da_manutencao_sem_indicar_falha_na_gravacao(self):
        db = Mock()
        db.listar_manutencao.side_effect = [[], OSError()]
        db.obter_id_por_codigo.return_value = 4
        aba = self._mostrar(TabManutencao(db))
        aba.entry_codigo.setText("007")
        aba.entry_local.setText("Oficina")
        with patch.object(QMessageBox, "warning") as aviso:
            aba._on_cadastrar_saida()
        db.cadastrar_saida_manutencao.assert_called_once_with(4, "Oficina")
        self.assertEqual(aba.entry_codigo.text(), "")
        self.assertEqual(aviso.call_args.args[1], "Movimentação registrada")

    def test_localizacao_avisa_quando_estado_legado_e_inconsistente(self):
        db = Mock()
        db.listar_eventos.return_value = []
        db.localizar_equipamento.side_effect = ErroMovimentacao(
            "estado_legado_inconsistente", "A movimentação não pode ser confirmada.",
        )
        aba = self._mostrar(TabEventos(db))
        with patch.object(QMessageBox, "warning") as aviso:
            self._escanear(aba.entry_search, "007")
        db.localizar_equipamento.assert_called_once_with("007")
        self.assertEqual(aviso.call_args.args[1], "Localização não confirmada")

    def test_reconsulta_disponibilidade_mantem_filtro(self):
        db = Mock()
        db.listar_equipamentos_disponiveis.return_value = []
        aba = self._mostrar(TabEquipamentos(db))
        aba.entry_search.setText("007")
        aba.atualizar_disponibilidade()
        db.listar_equipamentos_disponiveis.assert_called_with("007")
        self.assertIn("Listar Disponíveis", [b.text() for b in aba.findChildren(QPushButton)])

    def test_scanner_com_sqlite_sintetico_fluxo_parcial_repeticao_e_fechamento(self):
        raiz = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch.object(database, "_load_env_file"))
        self.enterContext(patch.object(database, "get_db_path", return_value=str(raiz / "fluxo.sqlite3")))
        self.enterContext(patch.dict(os.environ, {"SEAGBH_DB_ENGINE": "sqlite"}))
        db = database.Database()
        self.addCleanup(db.fechar)
        for codigo in ("007", "008"):
            db.cadastrar_equipamento(codigo, f"Equipamento {codigo}", "", "Estoque")
        ids = [db.obter_id_por_codigo(codigo) for codigo in ("007", "008")]
        evento = db.criar_evento("Evento", "Local", "Equipe", "01/08/2026", "02/08/2026", ids)
        dlg = self._mostrar(RetornoDialog(db, evento))

        with patch.object(QMessageBox, "information") as info:
            self._escanear(dlg.entry_codigo, "007")
            self.assertEqual(dlg.list_pend.count(), 1)
            self.assertEqual(dlg.list_dev.count(), 1)
            self.assertEqual(db.obter_evento(evento)["status"], "Agendado")
            self.assertIsNone(db.localizar_equipamento("007"))
            self._escanear(dlg.entry_codigo, "007")
            self.assertEqual(len(db.devolvidos_retorno(evento)), 1)
            self._escanear(dlg.entry_codigo, "008")

        self.assertEqual(dlg.result(), QDialog.DialogCode.Accepted.value)
        self.assertEqual(db.obter_evento(evento)["status"], "Concluído")
        self.assertEqual([c.args[1] for c in info.call_args_list], ["Retorno já registrado", "Concluído"])


if __name__ == "__main__":
    unittest.main()
