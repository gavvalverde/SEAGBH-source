"""Testes locais e sem rede dos contratos de multiativação."""
import unittest
from uuid import UUID

from src.core.multiativacao_dominio import (
    Ativacao,
    DecisaoAtivacao,
    Empresa,
    Licenca,
    SituacaoAtivacao,
    SituacaoLicenca,
    avaliar_ativacao,
)


EMPRESA_X = UUID(int=101)
EMPRESA_Y = UUID(int=102)
LICENCA_X = UUID(int=201)
LICENCA_NOVA = UUID(int=202)
PC_1 = UUID(int=301)
PC_2 = UUID(int=302)
PC_3 = UUID(int=303)
PC_4 = UUID(int=304)


def licenca(*, id=LICENCA_X, empresa=EMPRESA_X, limite=3,
            situacao=SituacaoLicenca.ATIVA):
    return Licenca(id, empresa, limite, situacao)


def ativacao(device, *, id=None, empresa=EMPRESA_X, lic=LICENCA_X,
            situacao=SituacaoAtivacao.ATIVA):
    number = 400 + device.int if id is None else id
    return Ativacao(UUID(int=number), empresa, lic, device, situacao)


class ContratosMultiativacao(unittest.TestCase):
    def test_tres_computadores_ocupam_tres_vagas(self):
        ativos = [ativacao(PC_1), ativacao(PC_2), ativacao(PC_3)]
        resultado = avaliar_ativacao(licenca(), PC_4, ativos)
        self.assertEqual(resultado.decisao, DecisaoAtivacao.LIMITE_ATINGIDO)
        self.assertEqual((resultado.vagas_utilizadas, resultado.vagas_livres),
                         (3, 0))

    def test_aumentar_de_tres_para_quatro_preserva_a_mesma_licenca(self):
        ativos = [ativacao(PC_1), ativacao(PC_2), ativacao(PC_3)]
        antes = avaliar_ativacao(licenca(limite=3), PC_4, ativos)
        depois = avaliar_ativacao(licenca(limite=4), PC_4, ativos)
        self.assertEqual(antes.decisao, DecisaoAtivacao.LIMITE_ATINGIDO)
        self.assertEqual(depois.decisao, DecisaoAtivacao.PERMITIDA)
        self.assertEqual(depois.vagas_livres, 1)

    def test_mesmo_computador_nao_consome_outra_vaga(self):
        ativos = [ativacao(PC_1), ativacao(PC_2), ativacao(PC_3)]
        resultado = avaliar_ativacao(licenca(), PC_2, ativos)
        self.assertEqual(resultado.decisao, DecisaoAtivacao.JA_ATIVADO)
        self.assertEqual(resultado.vagas_utilizadas, 3)

    def test_revogacao_libera_vaga_para_outro_dispositivo(self):
        ativos = [ativacao(PC_1), ativacao(PC_2),
                  ativacao(PC_3, situacao=SituacaoAtivacao.REVOGADA)]
        resultado = avaliar_ativacao(licenca(), PC_4, ativos)
        self.assertEqual(resultado.decisao, DecisaoAtivacao.PERMITIDA)
        self.assertEqual(resultado.vagas_livres, 1)

    def test_cancelamento_bloqueia_inclusive_dispositivo_antigo(self):
        for situacao in (SituacaoLicenca.SUSPENSA, SituacaoLicenca.ENCERRADA):
            with self.subTest(situacao=situacao):
                resultado = avaliar_ativacao(
                    licenca(situacao=situacao), PC_1, [ativacao(PC_1)])
                self.assertEqual(resultado.decisao,
                                 DecisaoAtivacao.LICENCA_INATIVA)

    def test_recontratacao_pode_preservar_empresa_com_nova_licenca(self):
        empresa = Empresa(EMPRESA_X, "Empresa de teste")
        primeira = licenca(id=LICENCA_X, situacao=SituacaoLicenca.ENCERRADA)
        segunda = licenca(id=LICENCA_NOVA)
        self.assertEqual(primeira.empresa_id, empresa.id)
        self.assertEqual(segunda.empresa_id, empresa.id)
        self.assertNotEqual(primeira.id, segunda.id)
        self.assertEqual(
            avaliar_ativacao(segunda, PC_1, []).decisao,
            DecisaoAtivacao.PERMITIDA)

    def test_isolamento_rejeita_ativacoes_de_outra_empresa(self):
        mistura = [ativacao(PC_1, empresa=EMPRESA_Y)]
        with self.assertRaises(ValueError):
            avaliar_ativacao(licenca(), PC_2, mistura)

    def test_isolamento_rejeita_ativacoes_de_outra_licenca(self):
        mistura = [ativacao(PC_1, lic=LICENCA_NOVA)]
        with self.assertRaises(ValueError):
            avaliar_ativacao(licenca(), PC_2, mistura)

    def test_duplicidade_de_dispositivos_ativos_falha_fechado(self):
        duplicadas = [ativacao(PC_1, id=501), ativacao(PC_1, id=502)]
        with self.assertRaisesRegex(ValueError, "mais de uma ativacao"):
            avaliar_ativacao(licenca(), PC_2, duplicadas)

    def test_duplicidade_de_registro_falha_fechado(self):
        primeira = ativacao(PC_1)
        with self.assertRaisesRegex(ValueError, "ativacao duplicada"):
            avaliar_ativacao(licenca(), PC_2, [primeira, primeira])

    def test_limite_invalido_inclui_booleanos(self):
        for limite in (0, -1, True, 1.5, "3"):
            with self.subTest(limite=limite):
                with self.assertRaises(ValueError):
                    licenca(limite=limite)

    def test_campos_e_estados_invalidos_nao_sao_aceitos(self):
        with self.assertRaises(ValueError):
            Empresa(UUID(int=0), "Empresa")
        with self.assertRaises(ValueError):
            Empresa(EMPRESA_X, "   ")
        with self.assertRaises(ValueError):
            licenca(situacao="ativa")
        with self.assertRaises(ValueError):
            ativacao(PC_1, situacao="ativa")
        with self.assertRaises(ValueError):
            avaliar_ativacao(licenca(), UUID(int=0), [])

    def test_objetos_de_outro_tipo_sao_rejeitados(self):
        with self.assertRaises(TypeError):
            avaliar_ativacao("licenca", PC_1, [])
        with self.assertRaises(TypeError):
            avaliar_ativacao(licenca(), PC_1, ["ativacao"])


if __name__ == "__main__":
    unittest.main()
