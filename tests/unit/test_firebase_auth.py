"""Regressões offline da autenticação do Gerenciamento de Clientes."""

import io
import json
import os
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from threading import Event, get_ident
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import BaseHandler, build_opener
from urllib.response import addinfourl

from src.core import firebase_auth as auth
from src.core.firebase_auth import FalhaAuth


ENV = "SEAGBH_FIREBASE_API_KEY"
API_KEY = "chave+simulada"
EMAIL = "admin@example.invalid"
SENHA = "senha-ficticia"
LOGIN_OK = {
    "idToken": "id-token-ficticio",
    "refreshToken": "refresh-token-ficticio",
    "localId": "uid-ficticio",
    "expiresIn": "3600",
}


def _resposta(dados, status=200):
    resposta = io.BytesIO(json.dumps(dados).encode())
    resposta.status = status
    return resposta


def _erro_http(dados, status=400):
    corpo = dados if isinstance(dados, bytes) else json.dumps(dados).encode()
    resposta = io.BytesIO(corpo)
    resposta.read = Mock(wraps=resposta.read)
    erro = HTTPError(f"https://example.invalid/?key={API_KEY}", status, SENHA, {}, resposta)
    return erro, resposta


class ConfiguracaoFirebaseTests(unittest.TestCase):
    def test_dotenv_bom_aspas_comentario_e_apenas_chave_firebase(self):
        with tempfile.TemporaryDirectory() as pasta, patch.dict(os.environ, clear=True):
            arquivo = Path(pasta) / ".env"
            arquivo.write_text(
                f'export {ENV} = "{API_KEY}" # comentário\nOUTRA_CONFIG_FICTICIA=ignorada\n',
                encoding="utf-8-sig",
            )
            auth.carregar_api_key(arquivo)
            self.assertEqual(auth.api_key_firebase(), API_KEY)
            self.assertNotIn("OUTRA_CONFIG_FICTICIA", os.environ)

    def test_variavel_do_processo_tem_precedencia_mesmo_vazia(self):
        with tempfile.TemporaryDirectory() as pasta:
            arquivo = Path(pasta) / ".env"
            arquivo.write_text(f"{ENV}={API_KEY}", encoding="utf-8")
            for valor in ("  chave-herdada  ", "  "):
                with self.subTest(valor=valor), patch.dict(os.environ, {ENV: valor}):
                    auth.carregar_api_key(arquivo)
                    self.assertEqual(auth.api_key_firebase(), valor.strip())

    def test_dotenv_invalido_e_ausente(self):
        with tempfile.TemporaryDirectory() as pasta, patch.dict(os.environ, clear=True):
            arquivo = Path(pasta) / ".env"
            auth.carregar_api_key(arquivo)
            self.assertEqual(auth.api_key_firebase(), "")
            arquivo.write_text(f'{ENV}="{API_KEY}', encoding="utf-8")
            with self.assertLogs(auth.__name__, "WARNING") as logs:
                auth.carregar_api_key(arquivo)
            self.assertEqual(auth.api_key_firebase(), "")
            self.assertIn("ENV_INVALIDO", logs.output[0])
            self.assertNotIn(API_KEY, logs.output[0])


class FirebaseAuthTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {ENV: API_KEY}))
        self.urlopen = self.enterContext(patch.object(auth, "_abrir_auth"))
        self.relogio = self.enterContext(patch.object(auth.time, "monotonic", return_value=100.0))
        auth.logout_firebase()
        self.addCleanup(auth.logout_firebase)

    def _login_ok(self):
        resposta = _resposta(LOGIN_OK)
        self.urlopen.return_value = resposta
        self.assertIsNone(auth.login_firebase(EMAIL, SENHA))
        self.assertTrue(resposta.closed)

    def _assert_falha_login(self, codigo):
        with self.assertLogs(auth.__name__, "WARNING") as logs:
            self.assertEqual(auth.login_firebase(EMAIL, SENHA), codigo)
        self.assertIn(f"Código: {codigo}", logs.output[0])
        self.assertFalse(auth.sessao_firebase_ativa())
        self.assertIsNone(auth.token_firebase())
        self.assertIsNone(auth._AUTH_SESSION)
        for privado in (EMAIL, SENHA, API_KEY, LOGIN_OK["idToken"], LOGIN_OK["refreshToken"]):
            self.assertNotIn(privado, "\n".join(logs.output))
        return logs.output[0]

    def test_login_usa_contrato_rest_e_logout_descarta_sessao(self):
        self._login_ok()
        req = self.urlopen.call_args.args[0]
        url = urlsplit(req.full_url)
        self.assertEqual(url.scheme, "https")
        self.assertEqual(url.netloc, "identitytoolkit.googleapis.com")
        self.assertEqual(url.path, "/v1/accounts:signInWithPassword")
        self.assertEqual(parse_qs(url.query), {"key": [API_KEY]})
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.get_header("Content-type"), "application/json")
        self.assertEqual(self.urlopen.call_args.kwargs, {"timeout": 20})
        self.assertEqual(json.loads(req.data), {
            "email": EMAIL, "password": SENHA, "returnSecureToken": True,
        })
        self.assertTrue(auth.sessao_firebase_ativa())
        self.assertEqual(auth.token_firebase(), LOGIN_OK["idToken"])
        auth.logout_firebase()
        self.assertIsNone(auth.token_firebase())
        self.urlopen.assert_called_once()

    def test_login_nao_segue_redirecionamento(self):
        chamadas = []

        class RespostaRedirecionada(BaseHandler):
            handler_order = 100

            def https_open(self, req):
                chamadas.append(urlsplit(req.full_url).netloc)
                headers = Message()
                headers["Location"] = "https://example.invalid/"
                resposta = addinfourl(io.BytesIO(b""), headers, req.full_url, 302)
                resposta.msg = "Found"
                return resposta

        self.urlopen.side_effect = build_opener(
            RespostaRedirecionada(), auth._SemRedirecionamento()
        ).open
        self.assertIn("HTTP: 302", self._assert_falha_login(FalhaAuth.RESPOSTA_INVALIDA))
        self.assertEqual(chamadas, ["identitytoolkit.googleapis.com"])

    def test_api_key_ausente_limpa_sessao_anterior_e_nao_envia_requisicao(self):
        self._login_ok()
        self.urlopen.reset_mock()
        with patch.dict(os.environ, {ENV: "  "}):
            self._assert_falha_login(FalhaAuth.API_KEY_AUSENTE)
        self.urlopen.assert_not_called()

    def test_http_400_codigos_conhecidos_e_detalhes_apos_separador(self):
        casos = {
            "INVALID_LOGIN_CREDENTIALS": FalhaAuth.CREDENCIAIS_INVALIDAS,
            "INVALID_PASSWORD": FalhaAuth.CREDENCIAIS_INVALIDAS,
            "EMAIL_NOT_FOUND": FalhaAuth.CREDENCIAIS_INVALIDAS,
            "OPERATION_NOT_ALLOWED": FalhaAuth.PROVEDOR_DESATIVADO,
            "USER_DISABLED": FalhaAuth.USUARIO_DESABILITADO,
            "API_KEY_INVALID": FalhaAuth.API_KEY_INVALIDA,
            "CONFIGURATION_NOT_FOUND": FalhaAuth.PROJETO_INDISPONIVEL,
            "TOO_MANY_ATTEMPTS_TRY_LATER": FalhaAuth.LIMITE_TENTATIVAS,
        }
        for mensagem, codigo in casos.items():
            with self.subTest(mensagem=mensagem):
                erro, resposta = _erro_http({"error": {"message": f"{mensagem} : {EMAIL}"}})
                self.urlopen.side_effect = erro
                self.assertIn("HTTP: 400", self._assert_falha_login(codigo))
                self.assertTrue(resposta.closed)

    def test_api_key_recusada_com_error_info_e_mensagem_estruturada_legada(self):
        for motivo, codigo in (
            ("API_KEY_INVALID", FalhaAuth.API_KEY_INVALIDA),
            ("API_KEY_SERVICE_BLOCKED", FalhaAuth.API_KEY_RESTRITA),
            ("API_KEY_HTTP_REFERRER_BLOCKED", FalhaAuth.API_KEY_RESTRITA),
            ("SERVICE_DISABLED", FalhaAuth.PROJETO_INDISPONIVEL),
        ):
            with self.subTest(motivo=motivo):
                erro, _ = _erro_http({"error": {
                    "message": f"Mensagem do provedor {API_KEY}",
                    "details": [{
                        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                        "domain": "googleapis.com",
                        "reason": motivo,
                        "metadata": {"valor": SENHA},
                    }],
                }})
                self.urlopen.side_effect = erro
                self._assert_falha_login(codigo)
        erro, _ = _erro_http({"error": {
            "message": "Bad Request",
            "errors": [{"message": "API key not valid. Please pass a valid API key."}],
        }})
        self.urlopen.side_effect = erro
        self._assert_falha_login(FalhaAuth.API_KEY_INVALIDA)

    def test_http_400_desconhecido_nao_exibe_codigo_arbitrario(self):
        marcador = "CODIGO_ARBITRARIO"
        erro, _ = _erro_http({"error": {
            "message": f"{marcador} : {SENHA}",
            "details": [{
                "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                "domain": "example.invalid",
                "reason": "API_KEY_INVALID",
            }],
        }})
        self.urlopen.side_effect = erro
        self.assertNotIn(marcador, self._assert_falha_login(FalhaAuth.ERRO_HTTP_DESCONHECIDO))

    def test_corpos_de_erro_malformados_ou_grandes_sao_fechados(self):
        for corpo in (b"{incompleto", b"\xff", [], {"error": []}, b" " * 40_000):
            with self.subTest(tipo=type(corpo).__name__):
                erro, resposta = _erro_http(corpo)
                self.urlopen.side_effect = erro
                self._assert_falha_login(FalhaAuth.RESPOSTA_INVALIDA)
                resposta.read.assert_called_once_with(auth._LIMITE_ERRO + 1)
                self.assertTrue(resposta.closed)

    def test_status_429_e_503_classificados_mesmo_sem_json(self):
        for status, codigo in (
            (429, FalhaAuth.LIMITE_TENTATIVAS),
            (503, FalhaAuth.PROVEDOR_INDISPONIVEL),
        ):
            with self.subTest(status=status):
                erro, resposta = _erro_http(b"<html>", status)
                self.urlopen.side_effect = erro
                self.assertIn(f"HTTP: {status}", self._assert_falha_login(codigo))
                self.assertTrue(resposta.closed)

    def test_timeout_e_conectividade_sem_log_da_excecao_bruta(self):
        for erro, codigo in (
            (TimeoutError(SENHA), FalhaAuth.TIMEOUT),
            (URLError(TimeoutError(SENHA)), FalhaAuth.TIMEOUT),
            (URLError(f"proxy indisponível: {API_KEY}"), FalhaAuth.REDE_INDISPONIVEL),
        ):
            with self.subTest(tipo=type(erro).__name__):
                self.urlopen.side_effect = erro
                self._assert_falha_login(codigo)

    def test_login_sem_id_token_ou_por_concluir_rejeita_e_limpa_sessao_anterior(self):
        self._login_ok()
        resposta = _resposta({"mfaPendingCredential": SENHA})
        self.urlopen.return_value = resposta
        self._assert_falha_login(FalhaAuth.TOKEN_AUSENTE)
        self.assertTrue(resposta.closed)

    def test_login_so_publica_tokens_com_uma_resposta_completa_e_valida(self):
        casos = (
            (dict(LOGIN_OK, refreshToken=""), 200),
            (dict(LOGIN_OK, refreshToken="\ud800"), 200),
            (dict(LOGIN_OK, idToken="id\r\ntoken"), 200),
            (dict(LOGIN_OK, localId=[]), 200),
            (dict(LOGIN_OK, expiresIn=None), 200),
            (dict(LOGIN_OK, expiresIn="0"), 200),
            (dict(LOGIN_OK, expiresIn="3601"), 200),
            (dict(LOGIN_OK, expiresIn=True), 200),
            (dict(LOGIN_OK, error={"message": "USER_DISABLED"}), 200),
            (LOGIN_OK, 202),
        )
        for dados, status in casos:
            with self.subTest(status=status, campos=list(dados)):
                self.urlopen.return_value = _resposta(dados, status)
                self._assert_falha_login(FalhaAuth.RESPOSTA_INVALIDA)

    def test_renova_na_fronteira_dos_60_segundos_e_substitui_a_sessao(self):
        self._login_ok()
        self.relogio.return_value = 3639.0
        self.assertEqual(auth.token_firebase(), LOGIN_OK["idToken"])
        self.urlopen.assert_called_once()

        self.relogio.return_value = 3640.0
        resposta = _resposta({
            "id_token": "novo-id-token",
            "refresh_token": "novo-refresh-token",
            "expires_in": "3600",
            "user_id": LOGIN_OK["localId"],
        })
        self.urlopen.return_value = resposta
        self.assertEqual(auth.token_firebase(), "novo-id-token")
        self.assertTrue(resposta.closed)
        req = self.urlopen.call_args.args[0]
        self.assertEqual(urlsplit(req.full_url).netloc, "securetoken.googleapis.com")
        self.assertEqual(urlsplit(req.full_url).path, "/v1/token")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.get_header("Content-type"), "application/x-www-form-urlencoded")
        self.assertEqual(parse_qs(req.data.decode()), {
            "grant_type": ["refresh_token"], "refresh_token": [LOGIN_OK["refreshToken"]],
        })
        self.assertEqual(auth._AUTH_SESSION.refresh_token, "novo-refresh-token")

    def test_renovacao_recusada_limpa_sessao_sem_expor_tokens(self):
        for falha in ("revogação", "rede", "resposta", "uid"):
            with self.subTest(falha=falha):
                self.urlopen.side_effect = None
                self._login_ok()
                self.relogio.return_value += 3600
                self.assertFalse(auth.sessao_firebase_ativa())
                if falha == "revogação":
                    self.urlopen.side_effect, _ = _erro_http({"error": {"message": "TOKEN_EXPIRED"}})
                elif falha == "rede":
                    self.urlopen.side_effect = URLError(LOGIN_OK["refreshToken"])
                elif falha == "resposta":
                    self.urlopen.return_value = _resposta({"id_token": "novo", "expires_in": None})
                else:
                    self.urlopen.return_value = _resposta({
                        "id_token": "novo", "user_id": "outro-uid", "expires_in": "3600",
                    })
                with self.assertLogs(auth.__name__, "WARNING") as logs:
                    self.assertIsNone(auth.token_firebase())
                self.assertNotIn(LOGIN_OK["refreshToken"], logs.output[0])
                self.assertIsNone(auth._AUTH_SESSION)
                chamadas = self.urlopen.call_count
                self.assertIsNone(auth.token_firebase())
                self.assertEqual(self.urlopen.call_count, chamadas)

    def test_resposta_de_renovacao_em_andamento_nao_restaura_logout(self):
        self._login_ok()
        self.relogio.return_value = 3700.0

        def responder_apos_logout(*args, **kwargs):
            auth.logout_firebase()
            return _resposta({"id_token": "novo-id-token", "expires_in": "3600"})

        self.urlopen.side_effect = responder_apos_logout
        self.assertIsNone(auth.token_firebase())
        self.assertFalse(auth.sessao_firebase_renovavel())


try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QSignalSpy, QTest
    from PyQt6.QtWidgets import QApplication, QDialog, QListWidget, QPushButton
except ImportError:
    QApplication = None


@unittest.skipIf(QApplication is None, "PyQt6 indisponível")
class FirebaseAuthQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tools import gerador_chaves
        from core import firebase_auth

        cls.ui = gerador_chaves
        cls.auth = firebase_auth
        cls.enterClassContext(patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen"}))
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.enterContext(patch.dict(os.environ, {ENV: API_KEY}))
        self.historico = self.enterContext(patch.object(
            self.ui, "_carregar_historico", side_effect=AssertionError
        ))
        self.transporte_database = self.enterContext(patch.object(
            self.ui, "urlopen", side_effect=AssertionError
        ))
        self.transporte_auth = self.enterContext(patch.object(
            self.auth, "_abrir_auth", side_effect=AssertionError
        ))
        self.enterContext(patch.object(self.ui, "FIREBASE_URL", "https://database.example.invalid"))
        self.salvar_historico = self.enterContext(patch.object(
            self.ui, "_salvar_historico", side_effect=AssertionError
        ))
        self.salvar_morto = self.enterContext(patch.object(
            self.ui, "_salvar_arquivo_morto", side_effect=AssertionError
        ))
        self.auth.logout_firebase()
        self.addCleanup(self.auth.logout_firebase)

    def _login_ok(self):
        self.transporte_auth.side_effect = None
        self.transporte_auth.return_value = _resposta(LOGIN_OK)
        self.assertIsNone(self.auth.login_firebase(EMAIL, SENHA))

    def _janela_autenticada(self):
        self._login_ok()
        self.historico.side_effect = None
        self.historico.return_value = []
        janela = self.ui.GerenciamentoClientes()
        self.addCleanup(janela.close)
        return janela

    def test_dialogo_real_aceita_somente_com_token_e_limpa_campo_de_senha(self):
        dialogo = self.ui.LoginDialog()
        dialogo.entry_email.setText(EMAIL)
        dialogo.entry_senha.setText(SENHA)
        with patch.object(self.auth, "_abrir_auth", return_value=_resposta(LOGIN_OK)) as enviar:
            dialogo._on_entrar()
            sinal = QSignalSpy(dialogo._worker.concluido)
            self.assertEqual(dialogo.entry_senha.text(), "")
            self.assertTrue(sinal.wait(3000))
        self.assertEqual(dialogo.result(), QDialog.DialogCode.Accepted)
        self.assertTrue(self.auth.sessao_firebase_ativa())
        enviar.assert_called_once()

    def test_dialogo_recusa_sem_enumerar_contas_e_ignora_escape_ate_thread_encerrar(self):
        mensagens = []
        for motivo in ("USER_DISABLED", "EMAIL_NOT_FOUND", "INVALID_PASSWORD"):
            with self.subTest(motivo=motivo):
                liberar = Event()
                erro, _ = _erro_http({"error": {"message": motivo}})

                def enviar(*args, **kwargs):
                    liberar.wait(3)
                    raise erro

                dialogo = self.ui.LoginDialog()
                dialogo.show()
                dialogo.entry_email.setText(EMAIL)
                dialogo.entry_senha.setText(SENHA)
                with patch.object(self.auth, "_abrir_auth", side_effect=enviar) as transporte:
                    with self.assertLogs(self.auth.__name__, "WARNING"):
                        dialogo._on_entrar()
                        sinal = QSignalSpy(dialogo._worker.concluido)
                        try:
                            QTest.keyClick(dialogo, Qt.Key.Key_Escape)
                            self.assertFalse(dialogo.close())
                            self.assertTrue(dialogo.isVisible())
                            dialogo._on_entrar()
                        finally:
                            liberar.set()
                            terminou = sinal.wait(3000)
                        self.assertTrue(terminou)
                    transporte.assert_called_once()
                mensagens.append(dialogo.lbl_erro.text())
                self.assertEqual(dialogo.result(), QDialog.DialogCode.Rejected)
                self.assertIsNone(self.auth.token_firebase())
                self.assertTrue(dialogo.btn_entrar.isEnabled())
                self.assertTrue(dialogo.close())
        self.assertEqual(len(set(mensagens)), 1)

    def test_entrada_exige_dialogo_aceito_e_sessao_valida_e_limpa_ao_sair(self):
        with (
            patch.object(self.ui, "carregar_api_key"),
            patch.object(self.ui, "QApplication") as app,
            patch.object(self.ui, "LoginDialog") as login,
            patch.object(self.ui, "GerenciamentoClientes") as janela,
        ):
            login.return_value.exec.return_value = QDialog.DialogCode.Rejected
            self.assertEqual(self.ui._main(), 0)
            login.return_value.exec.return_value = QDialog.DialogCode.Accepted
            self.assertEqual(self.ui._main(), 0)
            janela.assert_not_called()

            def autenticar():
                self._login_ok()
                return QDialog.DialogCode.Accepted

            login.return_value.exec.side_effect = autenticar
            app.return_value.exec.return_value = 0
            self.assertEqual(self.ui._main(), 0)
            janela.return_value.show.assert_called_once()
            self.assertIsNone(self.auth.token_firebase())

    def test_entrada_sem_chave_exibe_erro_sem_instanciar_dialogo_ou_janela(self):
        with (
            patch.dict(os.environ, {ENV: ""}),
            patch.object(self.ui, "carregar_api_key"),
            patch.object(self.ui, "QApplication"),
            patch.object(self.ui.QMessageBox, "critical") as aviso,
            patch.object(self.ui, "LoginDialog") as login,
            patch.object(self.ui, "GerenciamentoClientes") as janela,
            self.assertLogs(self.ui.__name__, "WARNING") as logs,
        ):
            self.assertEqual(self.ui._main(), 1)
            self.assertIn(FalhaAuth.API_KEY_AUSENTE, logs.output[0])
            aviso.assert_called_once()
            login.assert_not_called()
            janela.assert_not_called()

    def test_constructor_sem_sessao_bloqueia_leitura_do_historico(self):
        with self.assertRaisesRegex(RuntimeError, "exige uma sessão autenticada"):
            self.ui.GerenciamentoClientes()
        self.historico.assert_not_called()
        self.transporte_database.assert_not_called()

    def test_database_sem_sessao_ou_configuracao_falha_sem_requisicao(self):
        for url in ("https://database.example.invalid", "https://SEU-PROJETO.example.invalid"):
            with self.subTest(url=url), patch.object(self.ui, "FIREBASE_URL", url):
                with self.assertLogs(self.ui.__name__, "WARNING"):
                    self.assertEqual(self.ui._firebase_get("licenca-ficticia"), (False, None))
                    self.assertFalse(self.ui._firebase_put("licenca-ficticia", True))
                    self.assertFalse(self.ui._firebase_delete("licenca-ficticia"))
        self.transporte_database.assert_not_called()

    def test_database_fecha_403_limpa_sessao_e_preserva_privacidade_dos_logs(self):
        self._login_ok()
        id_token = LOGIN_OK["idToken"]
        erro, resposta = _erro_http({"error": id_token}, 403)
        self.transporte_database.side_effect = erro
        with self.assertLogs(self.ui.__name__, "WARNING") as logs:
            self.assertFalse(self.ui._firebase_delete("identificador-privado"))
        self.assertIn("HTTP: 403", logs.output[0])
        self.assertNotIn(id_token, logs.output[0])
        self.assertNotIn("identificador-privado", logs.output[0])
        self.assertTrue(resposta.closed)
        self.assertIsNone(self.auth.token_firebase())

    def test_database_nao_registra_excecao_contendo_token(self):
        self._login_ok()
        id_token = LOGIN_OK["idToken"]
        self.transporte_database.side_effect = URLError(f"https://example.invalid/?auth={id_token}")
        with self.assertLogs(self.ui.__name__, "WARNING") as logs:
            self.assertEqual(self.ui._firebase_get("licenca-ficticia"), (False, None))
        self.assertNotIn(id_token, logs.output[0])

    def test_perda_de_sessao_impede_copia_e_exportacao(self):
        janela = self._janela_autenticada()
        self.auth.logout_firebase()
        with (
            patch.object(self.ui.QMessageBox, "warning") as aviso,
            patch.object(janela, "_linha_selecionada", side_effect=AssertionError),
            patch.object(self.ui.QFileDialog, "getSaveFileName") as salvar_pdf,
        ):
            janela._on_copiar()
            janela._on_exportar_pdf()
        self.assertEqual(aviso.call_count, 2)
        salvar_pdf.assert_not_called()

    def test_acoes_locais_renovam_sessao_vencida_em_thread_e_prosseguem_na_ui(self):
        ui_thread = get_ident()
        casos = (
            ("_on_copiar", "_copiar_chave"),
            ("_on_exportar_pdf", "_selecionar_destino_pdf"),
            ("_on_restaurar", "_abrir_restauracao"),
        )
        for acionar, executar in casos:
            with self.subTest(acao=acionar):
                janela = self._janela_autenticada()
                vencimento = self.auth._AUTH_SESSION.expira_em
                liberar = Event()
                threads_renovacao = []
                threads_acao = []

                def responder(*args, **kwargs):
                    threads_renovacao.append(get_ident())
                    liberar.wait(3)
                    return _resposta({"id_token": "novo-id-token", "expires_in": "3600"})

                self.transporte_auth.side_effect = responder
                with (
                    patch.object(self.auth.time, "monotonic", return_value=vencimento),
                    patch.object(janela, executar, side_effect=lambda: threads_acao.append(get_ident())),
                ):
                    getattr(janela, acionar)()
                    sinal = QSignalSpy(janela._renovacao.finished)
                    try:
                        self.assertFalse(janela.isEnabled())
                        self.assertEqual(threads_acao, [])
                        getattr(janela, acionar)()
                    finally:
                        liberar.set()
                        terminou = sinal.wait(3000)
                    self.assertTrue(terminou)
                    self.assertEqual(threads_acao, [ui_thread])
                    self.assertTrue(janela.isEnabled())
                self.assertEqual(len(threads_renovacao), 1)
                self.assertNotEqual(threads_renovacao[0], ui_thread)

    def test_renovacao_recusada_nao_executa_acao_pendente(self):
        janela = self._janela_autenticada()
        self.transporte_auth.side_effect = URLError("rede simulada")
        with (
            patch.object(self.auth.time, "monotonic", return_value=self.auth._AUTH_SESSION.expira_em),
            patch.object(janela, "_copiar_chave") as copiar,
            patch.object(self.ui.QMessageBox, "warning") as aviso,
            self.assertLogs(self.auth.__name__, "WARNING"),
        ):
            janela._on_copiar()
            sinal = QSignalSpy(janela._renovacao.finished)
            self.assertTrue(sinal.wait(3000))
        copiar.assert_not_called()
        aviso.assert_called_once()
        self.assertFalse(self.auth.sessao_firebase_renovavel())

    def test_fechar_durante_renovacao_descarta_acao_pendente(self):
        janela = self._janela_autenticada()
        janela.show()
        liberar = Event()

        def responder(*args, **kwargs):
            liberar.wait(3)
            return _resposta({"id_token": "novo-id-token", "expires_in": "3600"})

        self.transporte_auth.side_effect = responder
        with (
            patch.object(self.auth.time, "monotonic", return_value=self.auth._AUTH_SESSION.expira_em),
            patch.object(janela, "_copiar_chave") as copiar,
        ):
            janela._on_copiar()
            sinal = QSignalSpy(janela._renovacao.finished)
            try:
                self.assertFalse(janela.close())
            finally:
                liberar.set()
                terminou = sinal.wait(3000)
            self.assertTrue(terminou)
        self.assertFalse(janela.isVisible())
        copiar.assert_not_called()

    def test_exclusao_recusada_preserva_registros_locais(self):
        janela = self._janela_autenticada()
        registro = {"cliente": "Cliente Teste", "id": "licenca-ficticia"}
        erro, _ = _erro_http({"error": "Permission denied"}, 403)
        self.transporte_database.side_effect = erro
        with (
            patch.object(janela, "_linha_selecionada", return_value=registro),
            patch.object(self.ui.QMessageBox, "warning", return_value=self.ui.QMessageBox.StandardButton.Yes),
            patch.object(self.ui.QMessageBox, "critical") as aviso,
            patch.object(self.ui, "_carregar_arquivo_morto", side_effect=AssertionError),
            self.assertLogs(self.ui.__name__, "WARNING"),
        ):
            janela._on_excluir()
        aviso.assert_called_once()
        self.assertEqual(self.transporte_database.call_count, 1)
        self.assertEqual(self.transporte_database.call_args.args[0].get_method(), "PATCH")
        self.salvar_morto.assert_not_called()
        self.salvar_historico.assert_not_called()

    def test_exclusao_atomica_envia_patch_multilocal_e_so_entao_atualiza_historico(self):
        janela = self._janela_autenticada()
        registro = {"cliente": "Cliente Teste", "id": "licenca-ficticia"}
        self.historico.return_value = [registro]
        self.salvar_historico.side_effect = None
        self.salvar_morto.side_effect = None
        self.transporte_database.side_effect = None
        self.transporte_database.return_value = _resposta(None)
        with (
            patch.object(janela, "_linha_selecionada", return_value=registro),
            patch.object(self.ui.QMessageBox, "warning", return_value=self.ui.QMessageBox.StandardButton.Yes),
            patch.object(self.ui, "_carregar_arquivo_morto", return_value=[]),
            patch.object(janela, "_carregar_tabela"),
            patch.object(self.ui, "_mostrar_toast"),
        ):
            janela._on_excluir()

        self.transporte_database.assert_called_once()
        req = self.transporte_database.call_args.args[0]
        self.assertEqual(req.get_method(), "PATCH")
        self.assertEqual(urlsplit(req.full_url).path, "/.json")
        self.assertEqual(
            json.loads(req.data),
            {"licenses/licenca-ficticia": None, "revoked/licenca-ficticia": None},
        )
        self.salvar_morto.assert_called_once_with([registro])
        self.salvar_historico.assert_called_once_with([])

    def test_exclusao_com_id_inseguro_nao_envia_requisicao(self):
        janela = self._janela_autenticada()
        registro = {"cliente": "Cliente Teste", "id": "../updates/latest"}
        with (
            patch.object(janela, "_linha_selecionada", return_value=registro),
            patch.object(self.ui.QMessageBox, "warning", return_value=self.ui.QMessageBox.StandardButton.Yes),
            patch.object(self.ui.QMessageBox, "critical") as aviso,
            patch.object(self.ui, "_carregar_arquivo_morto", side_effect=AssertionError),
            self.assertLogs(self.ui.__name__, "WARNING") as logs,
        ):
            janela._on_excluir()
        aviso.assert_called_once()
        self.transporte_database.assert_not_called()
        self.salvar_morto.assert_not_called()
        self.salvar_historico.assert_not_called()
        self.assertNotIn("../updates/latest", "\n".join(logs.output))

    def test_restauracao_recusada_preserva_registros_locais(self):
        registro = {"cliente": "Cliente Teste", "id": "licenca-ficticia"}

        def selecionar(dialogo):
            dialogo.findChild(QListWidget).setCurrentRow(0)
            dialogo.findChild(QPushButton).click()
            return QDialog.DialogCode.Rejected

        for etapa, requisicoes in (("GET", 1), ("PUT", 2)):
            with self.subTest(etapa=etapa):
                janela = self._janela_autenticada()
                erro, _ = _erro_http({"error": "Permission denied"}, 403)
                self.transporte_database.reset_mock()
                self.transporte_database.side_effect = [erro] if etapa == "GET" else [_resposta(None), erro]
                with (
                    patch.object(self.ui, "_carregar_arquivo_morto", return_value=[registro]),
                    patch.object(self.ui.QDialog, "exec", new=selecionar),
                    patch.object(self.ui.QMessageBox, "critical") as aviso,
                    self.assertLogs(self.ui.__name__, "WARNING"),
                ):
                    janela._on_restaurar()
                aviso.assert_called_once()
                self.assertEqual(self.transporte_database.call_count, requisicoes)
                self.salvar_morto.assert_not_called()
                self.salvar_historico.assert_not_called()
