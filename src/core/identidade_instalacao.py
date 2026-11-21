"""Identificação persistente de uma instalação do SEAGBH, sem dependência de rede.

O UUID NÃO é um segredo nem prova de posse do computador. Um backend de
licenciamento confiável deverá autenticar a instalação antes de autorizar
acesso ou consumir uma vaga de ativação.

O caminho deve ser escolhido pelo instalador/camada de integração e apontar
para um diretório de dados local persistente, nunca para o executável.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat
import tempfile
from uuid import UUID, uuid4


class IdentidadeInstalacaoInvalida(ValueError):
    """Identificador existente inválido; requer reparo explícito, sem gerar outro."""


def ler_id_instalacao(caminho: Path) -> UUID:
    """Lê UUID v4 canônico com LF, CRLF ou sem terminador."""
    caminho = Path(caminho)
    # Isto é defesa básica contra redirecionamentos acidentais; NÃO é
    # proteção contra uma conta local capaz de modificar o diretório.
    info = caminho.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise IdentidadeInstalacaoInvalida("identidade deve ser arquivo regular")
    with caminho.open("rb") as arquivo:
        conteudo = arquivo.read(129)
    if not conteudo or len(conteudo) > 128:
        raise IdentidadeInstalacaoInvalida("identidade vazia ou excessiva")
    try:
        texto = conteudo.decode("ascii")
        # LF é o formato emitido pela aplicação; CRLF é a quebra padrão
        # de texto no Windows. Aceitar somente um terminador completo.
        if texto.endswith("\r\n"):
            valor = texto[:-2]
        elif texto.endswith("\n"):
            valor = texto[:-1]
        else:
            valor = texto
        identidade = UUID(valor)
    except (UnicodeError, ValueError, AttributeError):
        raise IdentidadeInstalacaoInvalida("formato de identidade inválido") from None
    if identidade.version != 4 or valor != str(identidade):
        raise IdentidadeInstalacaoInvalida("identidade deve ser UUID v4 canônico")
    return identidade


def carregar_ou_criar_id_instalacao(caminho: Path) -> UUID:
    """Cria a identidade uma vez, usando publicação atômica sem sobrescrita.

    Escreve e sincroniza um arquivo temporário no mesmo diretório, depois
    publica com hard link, que falha se o destino já existe. Dois processos
    concorrentes convergem para a mesma identidade sem observar escrita
    parcial. Exige sistema de arquivos com suporte a hard links (ex.: NTFS).

    Se houver corrupção/perda da identidade, NÃO a substitui silenciosamente:
    trocar o UUID poderia consumir outra vaga de licença e confundir o servidor.
    """
    caminho = Path(caminho)
    try:
        return ler_id_instalacao(caminho)
    except FileNotFoundError:
        pass
    # O instalador deve provisionar o diretório com ACL apropriada.
    # Não criamos automaticamente diretórios em locais arbitrários.
    if not caminho.parent.is_dir():
        raise FileNotFoundError("diretório de identidade não existe")
    novo_id = uuid4()
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=".seagbh-device-",
            dir=caminho.parent, delete=False,
        ) as arquivo:
            temp_path = Path(arquivo.name)
            arquivo.write((str(novo_id) + "\n").encode("ascii"))
            arquivo.flush()
            os.fsync(arquivo.fileno())
        try:
            os.link(temp_path, caminho)
        except FileExistsError:
            # Outro processo publicou primeiro. Não substitua o arquivo.
            return ler_id_instalacao(caminho)
        # Não confie no arquivo recém-criado se o diretório for comprometido.
        return ler_id_instalacao(caminho)
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
