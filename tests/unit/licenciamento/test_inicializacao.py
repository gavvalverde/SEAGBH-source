"""Exercita o main real com Qt e licenciamento simulados, sem rede ou GUI."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import Mock, patch


def _modulo(nome, **atributos):
    modulo = ModuleType(nome)
    modulo.__dict__.update(atributos)
    return modulo


class InicializacaoLicenca(unittest.TestCase):
    def setUp(self):
        self.app = Mock()
        self.aplicacao = Mock(return_value=self.app)
        self.janela = Mock()
        self.splash = Mock()
        self.dialogo = Mock()
        self.dialogo.return_value.exec.return_value = 1
        self.dialogo.return_value.ativado = True
        self.verificar = Mock()

        # Substituir os imports antes de carregar main_qt impede até a
        # importação dos módulos reais de licença, banco e interface.
        modulos = {
            "PyQt6": _modulo("PyQt6", __path__=[]),
            "PyQt6.QtWidgets": _modulo(
                "PyQt6.QtWidgets", QApplication=self.aplicacao,
            ),
            "PyQt6.QtGui": _modulo("PyQt6.QtGui", QIcon=Mock()),
            "ui": _modulo("ui", __path__=[]),
            "ui.main_window": _modulo("ui.main_window", MainWindow=self.janela),
            "ui.splash_screen": _modulo("ui.splash_screen", SplashScreen=self.splash),
            "ui.licenca_dialog": _modulo("ui.licenca_dialog", LicencaDialog=self.dialogo),
            "core": _modulo("core", __path__=[]),
            "core.licenca": _modulo("core.licenca", licenca_ativa=self.verificar),
        }
        caminho = Path(__file__).resolve().parents[3] / "src" / "main_qt.py"
        spec = importlib.util.spec_from_file_location("_main_qt_test", caminho)
        self.entrada = importlib.util.module_from_spec(spec)
        # main_qt insere src/ em sys.path; a alteração também fica isolada.
        with patch.dict(sys.modules, modulos), patch.object(sys, "path", sys.path[:]):
            spec.loader.exec_module(self.entrada)

        self.enterContext(patch.object(sys, "argv", ["SEAGBH"]))
        self.enterContext(patch.object(self.entrada.signal, "signal"))
        self.app.exec.side_effect = self._concluir_splash

    def _concluir_splash(self):
        # Simula a conclusão sem timer, thread ou loop Qt real.
        self.splash.call_args.kwargs["on_finished"]()
        return 7

    def _executar(self):
        with self.assertRaises(SystemExit) as saida:
            self.entrada.main()
        return saida.exception.code

    def _assert_bloqueado(self):
        codigo = self._executar()
        self.splash.assert_not_called()
        self.janela.assert_not_called()
        self.app.exec.assert_not_called()
        self.assertEqual(codigo, 0)

    def _assert_iniciado(self, payload):
        self.assertEqual(self._executar(), 7)
        self.splash.assert_called_once()
        self.app.exec.assert_called_once_with()
        self.janela.assert_called_once_with(
            alerta_vencimento=payload.get("_alerta"),
            alerta_revogacao=payload.get("_alerta_revogacao"),
        )
        self.janela.return_value.showMaximized.assert_called_once_with()

    def test_licenca_valida_na_primeira_verificacao_inicia_sem_dialogo(self):
        payload = {"_alerta": {"dias": 5}, "_alerta_revogacao": {"prazo_iso": "teste"}}
        self.verificar.return_value = (True, "Licença válida", payload)

        self._assert_iniciado(payload)

        self.verificar.assert_called_once_with()
        self.dialogo.assert_not_called()

    def test_ativacao_revalidada_inicia_com_alertas_da_segunda_verificacao(self):
        payload = {"_alerta": {"dias": 3}, "_alerta_revogacao": {"prazo_iso": "novo"}}
        self.verificar.side_effect = [
            (False, "Ative o sistema", None),
            (True, "Licença revalidada", payload),
        ]

        self._assert_iniciado(payload)

        self.assertEqual(self.verificar.call_count, 2)
        self.dialogo.assert_called_once_with("Ative o sistema")
        self.dialogo.return_value.exec.assert_called_once_with()

    def test_cancelamento_bloqueia_sem_revalidar(self):
        self.verificar.return_value = (False, "Ative o sistema", None)
        self.dialogo.return_value.exec.return_value = 0
        self.dialogo.return_value.ativado = False

        self._assert_bloqueado()

        self.verificar.assert_called_once_with()
        self.dialogo.return_value.exec.assert_called_once_with()

    def test_dialogo_rejeitado_bloqueia_mesmo_com_flag_ativado(self):
        self.verificar.return_value = (False, "Ative o sistema", None)
        self.dialogo.return_value.exec.return_value = 0

        self._assert_bloqueado()

        self.verificar.assert_called_once_with()

    def test_dialogo_aceito_sem_ativacao_bloqueia_sem_revalidar(self):
        self.verificar.return_value = (False, "Ative o sistema", None)
        self.dialogo.return_value.ativado = False

        self._assert_bloqueado()

        self.verificar.assert_called_once_with()

    def test_revalidacao_negativa_bloqueia_splash_e_janela_principal(self):
        self.verificar.side_effect = [
            (False, "Ative o sistema", None),
            (False, "Acesso bloqueado na revalidação", None),
        ]

        self._assert_bloqueado()

        self.assertEqual(self.verificar.call_count, 2)
        self.dialogo.assert_called_once_with("Ative o sistema")
        self.dialogo.return_value.exec.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
