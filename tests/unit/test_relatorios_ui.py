"""Regressões Qt → SQLite temporário → PDF real do relatório detalhado."""

import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from pypdf import PdfReader
    import reportlab.platypus
    from PyQt6.QtCore import QTimer, Qt
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication, QCheckBox, QMessageBox, QPushButton
except ImportError as exc:
    raise unittest.SkipTest(f"Dependências do teste PDF indisponíveis: {exc}") from exc

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from core import database
from ui import tab_relatorios


class RelatoriosDetalhadosUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.raiz = Path(self.enterContext(TemporaryDirectory(prefix="seagbh-relatorios-")))
        self.saida = self.raiz / "pdf"
        self.enterContext(patch.object(tab_relatorios, "_PASTA_EVENTOS", str(self.saida)))
        self.enterContext(patch.object(database, "_load_env_file"))
        self.enterContext(patch.object(database, "get_db_path", return_value=str(self.raiz / "teste.db")))
        self.enterContext(patch.dict(os.environ, {"SEAGBH_DB_ENGINE": "sqlite"}))
        self.db = database.Database()
        self.addCleanup(self.db.fechar)
        self.erro = self.enterContext(patch.object(QMessageBox, "critical"))
        self.info = self.enterContext(patch.object(QMessageBox, "information"))
        self.aba = tab_relatorios.TabRelatorios(self.db)
        self.addCleanup(self.aba.close)
        self.aba.show()

    def _emitir(self, nome):
        botao = next(b for b in self.aba.findChildren(QPushButton) if "Eventos Detalhados" in b.text())
        selecao = []

        def selecionar():
            dialogo = self.app.activeModalWidget()
            if dialogo is None:
                return
            check = next((c for c in dialogo.findChildren(QCheckBox) if c.text().endswith("  " + nome)), None)
            emitir = next((b for b in dialogo.findChildren(QPushButton) if "Emitir Selecionados" in b.text()), None)
            if check is None or emitir is None:
                dialogo.reject()
                return
            check.setChecked(True)
            selecao.append(True)
            QTest.mouseClick(emitir, Qt.MouseButton.LeftButton)

        limite = QTimer()
        limite.setSingleShot(True)
        limite.timeout.connect(lambda: self.app.activeModalWidget() and self.app.activeModalWidget().reject())
        limite.start(2000)
        try:
            QTimer.singleShot(0, selecionar)
            QTest.mouseClick(botao, Qt.MouseButton.LeftButton)
        finally:
            limite.stop()

        self.assertEqual(selecao, [True])
        self.assertEqual(self.erro.call_count, 0, str(self.erro.call_args_list))
        self.assertIn("1 relatório(s)", self.info.call_args.args[2])
        arquivos = list(self.saida.glob("*.pdf"))
        self.assertEqual(len(arquivos), 1)
        leitor = PdfReader(arquivos[0])
        self.assertEqual(len(leitor.pages), 1)
        return leitor.pages[0].extract_text()

    def test_evento_sem_equipamentos_gera_pdf_com_mensagem(self):
        nome = "Evento sem itens"
        # Um evento legado pode existir sem associações, ainda que a criação atual as exija.
        self.db.cursor.execute(
            "INSERT INTO eventos(nome_evento, local_evento, responsavel, data_inicio, data_fim, status) "
            "VALUES (?, ?, ?, ?, ?, 'Agendado')",
            (nome, "Local", "Equipe", "01/08/2026", "02/08/2026"),
        )
        self.db.conn.commit()
        texto = self._emitir(nome)
        self.assertIn(nome, texto)
        self.assertIn("Nenhum equipamento vinculado a este evento.", texto)

    def test_texto_dinamico_permanece_literal_incluindo_codigo_e_datas(self):
        nome = 'Café & <b>Som</b> > "Edição"'
        responsavel = 'Régia <i>Olívia</i> & "André"'
        itens = [("0007", '<b>Áudio</b> & "A"'), ("<i>009</i>", "Cabo > 2"), ("0010", "Cabo de rede"),
                 ("0011", "Caixa de som")]
        for codigo, equipamento in itens:
            self.db.cadastrar_equipamento(codigo, equipamento, "", "Estoque")
        evento_id = self.db.criar_evento(
            nome, "Local", responsavel, "01/08/2026", "02/08/2026",
            [self.db.obter_id_por_codigo(codigo) for codigo, _ in itens],
        )
        fim = '31/02/2026 <b>legado</b> & "revisar"'
        self.db.cursor.execute("UPDATE eventos SET data_fim=? WHERE id=?", (fim, evento_id))
        self.db.conn.commit()

        texto = self._emitir(nome)
        for literal in (nome, responsavel, fim, "Datas a revisar: período não confirmado.",
                        "Resumo de Equipamentos:", "1 <b>Áudio</b>", "2 Cabo",
                        "Equipamentos Detalhados:"):
            self.assertIn(literal, texto)
        for codigo, equipamento in itens:
            self.assertIn(f"{equipamento} ({codigo})", texto)
        self.assertEqual(self.db.obter_evento(evento_id)["data_fim"], fim)
