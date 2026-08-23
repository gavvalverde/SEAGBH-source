"""
Ponto de entrada da versão PyQt6 do SEAGBH.
"""

import sys
import os
import signal

# Garante que o diretório src/ esteja no path para imports relativos
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon
from ui.main_window import MainWindow
from ui.splash_screen import SplashScreen
from ui.licenca_dialog import LicencaDialog
from core.licenca import licenca_ativa


def main():
    # Detecta se foi iniciado após atualização
    updated_flag = "--updated" in sys.argv

    # App GUI não deve encerrar por SIGINT vindo do terminal integrado.
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    app = QApplication(sys.argv)
    app.setApplicationName("SEAGBH")
    app.setOrganizationName("SEAGBH")

    # Ícone do aplicativo
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seaghb_icon.ico")
    app.setWindowIcon(QIcon(icon_path))

    # ── Verificação de licença ────────────────────────────────────────
    valida, mensagem, payload = licenca_ativa()

    if not valida:
        dlg = LicencaDialog(mensagem)
        if dlg.exec() == 0 or not dlg.ativado:
            sys.exit(0)
        # Re-verificar após ativação
        valida, mensagem, payload = licenca_ativa()

        # Extrair info de alerta de vencimento (se houver)
    alerta = payload.get("_alerta") if payload else None

    # Extrair info de alerta de revogação (grace period, se houver)
    alerta_revogacao = payload.get("_alerta_revogacao") if payload else None

    # ── App principal ─────────────────────────────────────────────────
    window_ref = {"window": None}

    def on_splash_done():
        window = MainWindow(
            alerta_vencimento=alerta,
            alerta_revogacao=alerta_revogacao,
        )
        window_ref["window"] = window
        window.showMaximized()

        # Se iniciado após atualização, exibe mensagem e executa rotina pós-update
        if updated_flag:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(window, "Atualização Concluída", "O SEAGBH foi atualizado com sucesso!\n\nVocê está usando a versão mais recente.")
            # Aqui pode-se adicionar outras rotinas pós-atualização se necessário

    splash = SplashScreen(on_finished=on_splash_done, duration_ms=3600)

    try:
        exit_code = app.exec()
    except KeyboardInterrupt:
        import logging
        logging.getLogger(__name__).warning("Aplicacao interrompida por KeyboardInterrupt")
        exit_code = 0

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
