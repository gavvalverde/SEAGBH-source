"""
Aba de Relatórios — PyQt6.
Gera PDFs: eventos em andamento, eventos detalhados (seleção),
inventário e manutenção.
"""

import os
import re
from datetime import datetime
from collections import Counter

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QPushButton, QMessageBox,
    QDialog, QCheckBox, QScrollArea, QFrame, QLabel,
)
from PyQt6.QtCore import Qt

from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.units import cm
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors

from core.database import Database, APP_DIR

# Cores padrão do cabeçalho PDF
_AZUL = "#2A5A78"
_CINZA = "#888888"
_CINZA_CLARO = "#F0F0F0"
_CINZA_BORDA = "#DEE2E6"

_PASTA_EVENTOS = os.path.join(APP_DIR, "Relatórios", "Relatórios de Eventos")
_PASTA_INVENTARIO = os.path.join(APP_DIR, "Relatórios", "Relatórios de Inventário")
_PASTA_MANUTENCAO = os.path.join(APP_DIR, "Relatórios", "Relatórios de Manutenção")


def _garantir_pasta(pasta: str):
    os.makedirs(pasta, exist_ok=True)


class TabRelatorios(QWidget):
    """Widget completo da aba Relatórios."""

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 28, 28, 28)
        root.setSpacing(15)

        # ── Título da página ──────────────────────────────────────────────
        titulo = QLabel("Geração de Relatórios")
        titulo.setProperty("role", "title")
        root.addWidget(titulo)

        desc = QLabel("Selecione o tipo de relatório para gerar em PDF.")
        desc.setProperty("role", "secondary")
        root.addWidget(desc)

        root.addStretch()

        botoes = [
            ("📋 Gerar Relatório: Eventos em Andamento",   self._rel_eventos_andamento),
            ("📑 Gerar Relatório: Eventos Detalhados",      self._rel_eventos_detalhados),
            ("📦 Gerar Relatório: Inventário Completo",     self._rel_inventario),
            ("🔧 Gerar Relatório: Equip. em Manutenção",   self._rel_manutencao),
        ]
        for texto, slot in botoes:
            btn = QPushButton(texto)
            btn.setProperty("class", "primary")
            btn.setMinimumHeight(50)
            btn.clicked.connect(slot)
            root.addWidget(btn)

        root.addStretch()

    # ══════════════════════════════════════════════════════════════════════
    #  1) Eventos em Andamento
    # ══════════════════════════════════════════════════════════════════════

    def _rel_eventos_andamento(self):
        try:
            dados = self.db.eventos_em_andamento()
            if not dados:
                QMessageBox.information(self, "Info", "Nenhum evento em andamento!")
                return

            _garantir_pasta(_PASTA_EVENTOS)
            ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            caminho = os.path.join(_PASTA_EVENTOS, f"Relatorio_Eventos_Andamento_{ts}.pdf")

            doc = SimpleDocTemplate(caminho, pagesize=A4,
                                    leftMargin=1.5 * cm, rightMargin=1.5 * cm)
            estilos = getSampleStyleSheet()
            elementos = []

            cab_style = ParagraphStyle("Cab", parent=estilos["Title"],
                                       fontSize=12, textColor=colors.HexColor(_AZUL),
                                       alignment=TA_CENTER)
            elementos.append(Paragraph("SEAGBH - Relatório de Eventos em Andamento", cab_style))
            elementos.append(Paragraph(
                f"<font size='9' color='{_CINZA}'>Gerado em: "
                f"{datetime.now().strftime('%d/%m/%Y %H:%M')}</font>",
                estilos["BodyText"],
            ))
            elementos.append(Spacer(1, 15))

            cabecalho = ["ID", "Evento", "Responsável", "Início", "Término"]
            dados_tabela = [cabecalho] + [list(r) for r in dados]

            tabela = Table(dados_tabela, colWidths=[1.2 * cm, 6 * cm, 4 * cm, 3 * cm, 3 * cm])
            tabela.setStyle(_estilo_tabela_padrao())
            elementos.append(tabela)
            elementos.append(Spacer(1, 10))
            elementos.append(Paragraph(
                f"<font size='8' color='{_CINZA}'>Total de eventos: {len(dados)}</font>",
                estilos["BodyText"],
            ))

            doc.build(elementos)
            QMessageBox.information(self, "Sucesso",
                                    f"Relatório gerado em:\n{os.path.abspath(caminho)}")
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Falha ao gerar relatório:\n{e}")

    # ══════════════════════════════════════════════════════════════════════
    #  2) Eventos Detalhados (seleção interativa)
    # ══════════════════════════════════════════════════════════════════════

    def _rel_eventos_detalhados(self):
        eventos = self.db.eventos_agendados_resumo()
        if not eventos:
            QMessageBox.information(self, "Info", "Nenhum evento em andamento!")
            return

        dlg = _SelecionarEventosDialog(eventos, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        ids = dlg.selecionados
        if not ids:
            QMessageBox.warning(self, "Aviso", "Nenhum evento selecionado!")
            return

        self._emitir_relatorios_detalhados(ids)

    def _emitir_relatorios_detalhados(self, lista_ids: list[int]):
        _garantir_pasta(_PASTA_EVENTOS)
        gerados = 0

        for evento_id in lista_ids:
            try:
                ev = self.db.detalhes_evento_relatorio(evento_id)
                equips = self.db.equipamentos_evento_relatorio(evento_id)

                # Resumo por categoria (primeira palavra)
                resumo: dict[str, int] = {}
                for _, nome in equips:
                    cat = nome.split()[0] if nome else "Outro"
                    resumo[cat] = resumo.get(cat, 0) + 1

                ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                safe = re.sub(r'[\\/*?:"<>|]', '_', ev["nome_evento"])
                caminho = os.path.join(_PASTA_EVENTOS,
                                       f"{safe} - Relatório_detalhado - {ts}.pdf")

                doc = SimpleDocTemplate(caminho, pagesize=A4,
                                        leftMargin=1.5 * cm, rightMargin=1.5 * cm)
                estilos = getSampleStyleSheet()
                body = estilos["BodyText"]
                elementos = []

                elementos.append(Paragraph("Relatório detalhado do Evento",
                                           ParagraphStyle("TF", parent=estilos["Title"],
                                                          alignment=TA_CENTER)))
                elementos.append(Spacer(1, 6))
                elementos.append(Paragraph(ev["nome_evento"], estilos["Heading2"]))
                elementos.append(Spacer(1, 6))
                elementos.append(Paragraph(f"• Responsável: {ev['responsavel']}", body))
                elementos.append(Paragraph(
                    f"• Período: {ev['data_inicio']} a {ev['data_fim']}", body))
                elementos.append(Spacer(1, 12))

                elementos.append(Paragraph("Resumo de Equipamentos:", estilos["Heading3"]))
                for cat, cnt in resumo.items():
                    elementos.append(Paragraph(f"• {cnt} {cat}", body))
                elementos.append(Spacer(1, 12))

                # Lista em 3 colunas
                elementos.append(Paragraph("Equipamentos Detalhados:", estilos["Heading3"]))
                cells = [Paragraph(f"• {nome} ({cod})", body) for cod, nome in equips]
                rows = [cells[i:i + 3] for i in range(0, len(cells), 3)]
                for row in rows:
                    while len(row) < 3:
                        row.append(Paragraph("", body))
                tbl = Table(rows, colWidths=[6 * cm] * 3, hAlign="LEFT")
                tbl.setStyle(TableStyle([
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]))
                elementos.append(tbl)

                doc.build(elementos)
                gerados += 1
            except Exception as e:
                QMessageBox.critical(self, "Erro",
                                     f"Falha no evento {evento_id}: {e}")

        QMessageBox.information(self, "Relatórios Gerados",
                                f"{gerados} relatório(s) gerado(s) com sucesso!")

    # ══════════════════════════════════════════════════════════════════════
    #  3) Inventário Completo (PDF)
    # ══════════════════════════════════════════════════════════════════════

    def _rel_inventario(self):
        try:
            dados = self.db.todos_equipamentos_completos()
            if not dados:
                QMessageBox.warning(self, "Aviso", "Nenhum item cadastrado!")
                return

            _garantir_pasta(_PASTA_INVENTARIO)
            ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            caminho = os.path.join(_PASTA_INVENTARIO, f"Relatorio_Inventario_{ts}.pdf")

            doc = SimpleDocTemplate(caminho, pagesize=A4,
                                    leftMargin=1 * cm, rightMargin=1 * cm)
            estilos = getSampleStyleSheet()
            elementos = []

            estilo_cel = ParagraphStyle("CelC", parent=estilos["BodyText"],
                                        fontSize=9, leading=10, alignment=TA_CENTER,
                                        wordWrap="CJK",
                                        textColor=colors.HexColor("#333333"))

            elementos.append(Paragraph(
                f"<font size='14' color='{_AZUL}'><b>RELATÓRIO DE INVENTÁRIO - SEAGBH</b></font>",
                estilos["Title"]))
            elementos.append(Spacer(1, 15))

            col_widths = [1.2 * cm, 3.0 * cm, 4.0 * cm, 6.5 * cm, 3.5 * cm, 2.8 * cm]

            cabecalho = [
                Paragraph(f"<font color='#FFFFFF'><b>{h}</b></font>", estilo_cel)
                for h in ("ID", "CÓDIGO", "NOME", "DESCRIÇÃO", "LOCALIZAÇÃO", "DATA")
            ]

            dados_tabela = [cabecalho]
            for item in dados:
                linha = [Paragraph(str(v), estilo_cel) for v in item]
                dados_tabela.append(linha)

            tabela = Table(dados_tabela, colWidths=col_widths,
                           style=_estilo_tabela_inventario(), repeatRows=1,
                           hAlign="CENTER")
            elementos.append(tabela)

            doc.build(elementos)
            QMessageBox.information(self, "Sucesso",
                                    f"PDF gerado em:\n{os.path.abspath(caminho)}")
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Falha ao gerar PDF:\n{e}")

    # ══════════════════════════════════════════════════════════════════════
    #  4) Equipamentos em Manutenção
    # ══════════════════════════════════════════════════════════════════════

    def _rel_manutencao(self):
        try:
            dados = self.db.equipamentos_em_manutencao()
            if not dados:
                QMessageBox.information(self, "Info",
                                        "Nenhum equipamento em manutenção!")
                return

            _garantir_pasta(_PASTA_MANUTENCAO)
            ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            caminho = os.path.join(_PASTA_MANUTENCAO,
                                   f"Relatorio_Manutencao_{ts}.pdf")

            doc = SimpleDocTemplate(caminho, pagesize=A4)
            estilos = getSampleStyleSheet()
            elementos = []

            elementos.append(Paragraph(
                "<b>RELATÓRIO DE EQUIPAMENTOS EM MANUTENÇÃO</b>",
                estilos["Title"]))
            elementos.append(Spacer(1, 15))

            cabecalho = ["Código", "Equipamento", "Local", "Data Saída"]
            dados_tabela = [cabecalho] + [list(r) for r in dados]

            tabela = Table(dados_tabela,
                           colWidths=[3.5 * cm, 6 * cm, 5 * cm, 4 * cm])
            tabela.setStyle(_estilo_tabela_padrao())
            elementos.append(tabela)

            doc.build(elementos)
            QMessageBox.information(self, "Sucesso",
                                    f"Relatório gerado em:\n{os.path.abspath(caminho)}")
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Falha ao gerar relatório:\n{e}")


# ═══════════════════════════════════════════════════════════════════════════
#  Dialog de seleção de eventos para relatório detalhado
# ═══════════════════════════════════════════════════════════════════════════

class _SelecionarEventosDialog(QDialog):
    def __init__(self, eventos: list[tuple[int, str]], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Selecione Eventos para Relatório Detalhado")
        self.resize(600, 500)
        self.setModal(True)
        self.selecionados: list[int] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)

        # Scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        lay = QVBoxLayout(container)

        self._chk_todos = QCheckBox("📑  Emitir Todos")
        self._chk_todos.setStyleSheet("font-weight: bold; font-size: 14px; padding: 8px;")
        self._chk_todos.stateChanged.connect(self._toggle_todos)
        lay.addWidget(self._chk_todos)

        self._checks: list[tuple[int, QCheckBox]] = []
        for ev_id, ev_nome in eventos:
            chk = QCheckBox(f"🔹  {ev_nome}")
            chk.setStyleSheet("font-size: 13px; padding: 6px;")
            lay.addWidget(chk)
            self._checks.append((ev_id, chk))

        lay.addStretch()
        scroll.setWidget(container)
        root.addWidget(scroll, stretch=1)

        btn = QPushButton("✔️ Emitir Selecionados")
        btn.setProperty("class", "primary")
        btn.setMinimumHeight(40)
        btn.clicked.connect(self._on_emitir)
        root.addWidget(btn)

    def _toggle_todos(self, state):
        marcado = state == Qt.CheckState.Checked.value
        for _, chk in self._checks:
            chk.setChecked(marcado)

    def _on_emitir(self):
        if self._chk_todos.isChecked():
            self.selecionados = [eid for eid, _ in self._checks]
        else:
            self.selecionados = [eid for eid, chk in self._checks if chk.isChecked()]
        self.accept()


# ═══════════════════════════════════════════════════════════════════════════
#  Estilos de tabela reutilizáveis
# ═══════════════════════════════════════════════════════════════════════════

def _estilo_tabela_padrao() -> TableStyle:
    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(_AZUL)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor(_CINZA_BORDA)),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor(_CINZA_CLARO)]),
    ])


def _estilo_tabela_inventario() -> TableStyle:
    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(_AZUL)),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor(_CINZA_BORDA)),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
    ])
