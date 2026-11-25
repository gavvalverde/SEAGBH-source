"""
Ponto de entrada da versão PyQt6 do SEAGBH.
"""

import sys
import os
import signal
import logging

# Garante que o diretório src/ esteja no path para imports relativos
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__" and sys.argv[1:] == ["--seagbh-instalar-atualizacao"]:
    from core._instalador_atualizacao import executar_worker
    sys.exit(executar_worker())


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
        if not valida:
            sys.exit(0)

        # Extrair info de alerta de vencimento (se houver)
    alerta = payload.get("_alerta") if payload else None

    # Extrair info de alerta de revogação (grace period, se houver)
    alerta_revogacao = payload.get("_alerta_revogacao") if payload else None

    # ── App principal ─────────────────────────────────────────────────
    window = None
    recuperacao = None

    def mostrar_falha_inicio(erro):
        from core.conexao_sqlite_protegida import FechamentoPendente
        from PyQt6.QtWidgets import QDialog, QLabel, QMessageBox, QPushButton, QVBoxLayout

        nonlocal recuperacao
        logging.getLogger(__name__).error("Falha ao abrir a janela principal")
        if not isinstance(erro, FechamentoPendente):
            QMessageBox.critical(None, "Inicialização", "Não foi possível iniciar o SEAGBH.")
            app.quit()
            return
        dialogo = QDialog()
        dialogo.setWindowTitle("Fechamento de banco pendente")
        layout = QVBoxLayout(dialogo)
        layout.addWidget(QLabel("O banco precisa ser fechado antes de sair."))
        tentar = QPushButton("Tentar fechar novamente")
        layout.addWidget(tentar)
        recuperacao = dialogo

        def recuperar():
            nonlocal recuperacao
            try:
                erro.fechar()
            except BaseException:
                logging.getLogger(__name__).error("Fechamento após falha de inicialização pendente")
                return
            recuperacao = None
            dialogo.close()
            dialogo.deleteLater()
            app.quit()

        tentar.clicked.connect(recuperar)
        dialogo.show()

    def on_splash_done():
        nonlocal window
        try:
            window = MainWindow(
                alerta_vencimento=alerta,
                alerta_revogacao=alerta_revogacao,
            )
        except BaseException as erro:
            mostrar_falha_inicio(erro)
            return
        window.showMaximized()

        # Se iniciado após atualização, exibe mensagem e executa rotina pós-update
        if updated_flag:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(window, "Atualização instalada", "O executável verificado foi instalado e o SEAGBH foi reiniciado.")
            # Aqui pode-se adicionar outras rotinas pós-atualização se necessário

    splash = SplashScreen(on_finished=on_splash_done, duration_ms=3600)

    exit_code = None
    while True:
        try:
            resultado = app.exec()
        except KeyboardInterrupt:
            logging.getLogger(__name__).warning("Aplicacao interrompida por KeyboardInterrupt")
            resultado = 0
        if exit_code is None:
            exit_code = resultado
        if recuperacao is not None:
            recuperacao.show()
        elif window is not None and not window.encerramento_confirmado:
            # exit() pode retornar do loop sem passar pelo evento Quit.
            # Reentra no loop para que os slots de término ainda sejam atendidos.
            window.solicitar_saida()
        else:
            break

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
