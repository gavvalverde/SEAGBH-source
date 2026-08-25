"""Fixtures compartilhadas para testes do SEAGBH."""
from unittest.mock import patch

import pytest

import src.core.licenca as lic


@pytest.fixture
def lic_dir(tmp_path):
    """Redireciona _LICENCA_PATH para um diretorio temporario.

    Restaura o valor original ao final do teste.
    Nunca toca no .licenca.dat real do projeto.
    """
    fake_path = str(tmp_path / ".licenca.dat")
    with patch.object(lic, "_LICENCA_PATH", fake_path):
        yield tmp_path


@pytest.fixture
def chave_vitalicia():
    """Retorna uma chave vitalicia valida (nunca expira)."""
    return lic.gerar_chave("Cliente Teste", "vitalicio")
