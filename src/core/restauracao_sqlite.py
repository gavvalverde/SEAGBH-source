"""Pré-validação consultiva, sem restauração, configuração implícita ou UI.

O candidato deve ser um artefato fechado/estável em diretórios controlados pelo
chamador, conforme validar_backup(). O destino nunca é aberto por SQLite.
Metadados e hashes detectam alterações observadas; não bloqueiam outros processos
nem eliminam TOCTOU. Todo plano exige nova inspeção e exclusividade real antes de
uma futura execução, que não existe neste módulo. Leituras podem atualizar atime.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
import hashlib
import math
import os
from pathlib import Path
import sqlite3
import stat
import time
from urllib.parse import quote

from .backup_sqlite import ErroBackup, ValidacaoBackup, validar_backup


__all__ = [
    "ErroRestauracao", "Compatibilidade", "Exclusividade", "Presenca", "EstadoCandidato",
    "AssinaturaArquivo", "CandidatoRestauracao", "ArquivoAuxiliar", "PermissoesObservadas",
    "InspecaoDestino", "FalhaPreValidacao", "PlanoRestauracao",
    "validar_candidato_restauracao", "inspecionar_destino_restauracao", "planejar_restauracao",
]

# Colunas consumidas pelo Database atual; não representam uma versão de esquema.
# local_evento/notas são adicionadas pela inicialização legada, nunca pelo preflight.
# auditoria.usuario está no DDL, mas não é exigida pelas operações atuais.
_COLUNAS_APLICACAO = {
    "equipamentos": {"id", "codigo_barras", "nome", "descricao", "localizacao", "data_cadastro"},
    "eventos": {"id", "nome_evento", "local_evento", "responsavel", "data_inicio", "data_fim", "status", "notas"},
    "movimentacoes": {"id", "evento_id", "equipamento_id", "data_movimentacao", "tipo"},
    "manutencao": {"id", "equipamento_id", "data_saida", "local_manutencao", "status"},
    "auditoria": {"id", "tabela", "item_id", "acao", "detalhes", "data"},
}
_SIDECARS = ("-wal", "-shm", "-journal")
_PENDENCIAS = (
    "revalidar_caminhos_identidades_hashes_e_condicoes",
    "comprovar_compatibilidade_com_a_aplicacao",
    "obter_exclusividade_e_impedir_novas_aberturas",
    "preservar_estado_anterior_incluindo_commits_no_wal",
    "criar_e_validar_resgate_fora_da_retencao_comum",
    "preparar_validar_e_sincronizar_substituto",
    "aprovar_protocolo_de_publicacao_e_recuperacao_pos_crash",
)


class ErroRestauracao(Exception):
    """Erro sem caminhos, SQL, dados ou mensagem bruta da causa."""

    def __init__(self, fase: str, codigo: str, causa: BaseException | None = None):
        self.fase = fase
        self.codigo = codigo
        self.causa = type(causa).__name__ if causa else None
        self.erro_sistema = getattr(causa, "errno", None)
        super().__init__(f"Pré-validação não concluída: fase={fase}; código={codigo}.")


class Compatibilidade(str, Enum):
    NAO_COMPROVADA = "nao_comprovada"
    INCOMPATIVEL = "incompativel"


class Exclusividade(str, Enum):
    NAO_COMPROVADA = "nao_comprovada"


class Presenca(str, Enum):
    PRESENTE = "presente"
    AUSENTE = "ausente"
    NAO_VERIFICADA = "nao_verificada"


class EstadoCandidato(str, Enum):
    VALIDADO = "validado"
    RECUSADO = "recusado"
    NAO_INSPECIONADO = "nao_inspecionado"


@dataclass(frozen=True)
class AssinaturaArquivo:
    dispositivo: int
    inode: int
    tamanho: int
    mtime_ns: int
    ctime_ns: int
    modo: int
    links: int


@dataclass(frozen=True)
class CandidatoRestauracao:
    caminho: Path
    validacao: ValidacaoBackup
    assinatura: AssinaturaArquivo
    compatibilidade: Compatibilidade
    colunas_ausentes: tuple[str, ...]
    tabelas_adicionais: tuple[str, ...]
    avisos: tuple[str, ...]
    observado_em: datetime


@dataclass(frozen=True)
class ArquivoAuxiliar:
    caminho: Path
    tipo: str
    presenca: Presenca
    regular: bool | None
    inseguro: bool | None
    assinatura: AssinaturaArquivo | None


@dataclass(frozen=True)
class PermissoesObservadas:
    leitura_arquivo: bool | None
    escrita_arquivo: bool | None
    escrita_diretorio: bool | None
    escrita_resgate_ancestral: bool | None


@dataclass(frozen=True)
class InspecaoDestino:
    caminho: Path
    existente: bool
    assinatura: AssinaturaArquivo | None
    auxiliares: tuple[ArquivoAuxiliar, ...]
    permissoes: PermissoesObservadas
    impedimentos: tuple[str, ...]
    avisos: tuple[str, ...]
    observado_em: datetime
    exclusividade: Exclusividade = field(default=Exclusividade.NAO_COMPROVADA, init=False)


@dataclass(frozen=True)
class FalhaPreValidacao:
    fase: str
    codigo: str


@dataclass(frozen=True)
class PlanoRestauracao:
    backend: str
    backup: Path
    destino: Path
    pasta_backups: Path
    pasta_resgate: Path
    estado_candidato: EstadoCandidato
    candidato: CandidatoRestauracao | None
    falha_candidato: FalhaPreValidacao | None
    inspecao_destino: InspecaoDestino | None
    falha_destino: FalhaPreValidacao | None
    impedimentos: tuple[str, ...]
    avisos: tuple[str, ...]
    observado_em: datetime
    exclusividade: Exclusividade = field(default=Exclusividade.NAO_COMPROVADA, init=False)
    etapas_pendentes: tuple[str, ...] = field(default=_PENDENCIAS, init=False)


def _backend(backend: str) -> None:
    if backend != "sqlite":
        raise ErroRestauracao("argumentos", "backend_nao_suportado")


class _Prazo:
    def __init__(self, prazo: float):
        if (isinstance(prazo, bool) or not isinstance(prazo, (float, int))
                or not math.isfinite(prazo) or prazo <= 0):
            raise ErroRestauracao("argumentos", "prazo_invalido")
        self.fim = time.monotonic() + prazo

    def restante(self) -> float:
        restante = self.fim - time.monotonic()
        if restante <= 0:
            raise ErroRestauracao("inspecao", "prazo_excedido")
        return restante


def _info(caminho: Path) -> os.stat_result | None:
    try:
        return caminho.lstat()
    except FileNotFoundError:
        return None


def _link(info: os.stat_result) -> bool:
    return bool(stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400)


def _caminho(valor: str | Path) -> Path:
    """Normaliza sem seguir links, inclusive componentes de caminhos ainda ausentes."""
    try:
        caminho = Path(valor)
        if (not caminho.is_absolute() or ".." in caminho.parts or "\x00" in str(caminho)
                or str(caminho).startswith(("\\\\", "//"))):
            raise ErroRestauracao("caminhos", "caminho_inseguro")
        for parte in (*reversed(caminho.parents), caminho):
            if os.name == "nt" and parte != Path(parte.anchor):
                if ":" in parte.name or parte.name.endswith((" ", ".")):
                    raise ErroRestauracao("caminhos", "caminho_inseguro")
            info = _info(parte)
            if info is not None:
                if _link(info):
                    raise ErroRestauracao("caminhos", "link_ou_reparse_point")
                if parte != caminho and not stat.S_ISDIR(info.st_mode):
                    raise ErroRestauracao("caminhos", "componente_nao_diretorio")
        return caminho
    except (OSError, ValueError, TypeError) as exc:
        raise ErroRestauracao("caminhos", "caminho_nao_verificado", exc) from None


def _assinatura(info: os.stat_result) -> AssinaturaArquivo:
    return AssinaturaArquivo(info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                             info.st_ctime_ns, info.st_mode, info.st_nlink)


def _arquivo(caminho: Path) -> AssinaturaArquivo:
    info = _info(_caminho(caminho))
    if info is None:
        raise ErroRestauracao("candidato", "arquivo_ausente")
    if not stat.S_ISREG(info.st_mode):
        raise ErroRestauracao("candidato", "arquivo_nao_regular")
    if info.st_nlink != 1:
        raise ErroRestauracao("candidato", "arquivo_com_hardlinks")
    return _assinatura(info)


def _hash(caminho: Path, assinatura: AssinaturaArquivo, controle: _Prazo) -> str:
    digest = hashlib.sha256()
    with caminho.open("rb") as arquivo:
        aberta = _assinatura(os.fstat(arquivo.fileno()))
        # No Windows, lstat/fstat podem reportar criação/alteração em st_ctime.
        # Compare ctime apenas entre consultas da mesma API; mantenha os outros
        # campos na checagem cruzada de identidade do caminho com o handle.
        if replace(aberta, ctime_ns=assinatura.ctime_ns) != assinatura:
            raise ErroRestauracao("candidato", "arquivo_alterado")
        while bloco := arquivo.read(1024 * 1024):
            controle.restante()
            digest.update(bloco)
        if _assinatura(os.fstat(arquivo.fileno())) != aberta:
            raise ErroRestauracao("candidato", "arquivo_alterado")
    return digest.hexdigest()


def _sem_auxiliares(caminho: Path) -> None:
    # lstat também identifica links quebrados, que Path.exists() não identifica.
    if any(_info(Path(str(caminho) + s)) is not None for s in _SIDECARS):
        raise ErroRestauracao("candidato", "arquivo_nao_independente")


def _estrutura(caminho: Path, controle: _Prazo) -> tuple[tuple[str, ...], tuple[str, ...]]:
    # Somente o artefato independente já validado; nunca o destino operacional.
    uri = "file://" + ("/" if os.name == "nt" else "") + quote(caminho.as_posix(), safe="/:")
    with closing(sqlite3.connect(uri + "?mode=ro", uri=True, timeout=0.1,
                                 isolation_level=None)) as conn:
        interrompido_por_prazo = False

        def verificar_prazo() -> int:
            nonlocal interrompido_por_prazo
            if time.monotonic() >= controle.fim:
                interrompido_por_prazo = True
                return 1
            return 0

        conn.set_progress_handler(verificar_prazo, 1000)
        try:
            conn.execute("PRAGMA query_only=ON")
            conn.execute("PRAGMA trusted_schema=OFF")
            ausentes = []
            for tabela, esperadas in _COLUNAS_APLICACAO.items():
                controle.restante()
                with closing(conn.execute(f'PRAGMA table_info("{tabela}")')) as cursor:
                    presentes = {linha[1] for linha in cursor}
                ausentes.extend(f"{tabela}.{nome}" for nome in sorted(esperadas - presentes))
            with closing(conn.execute("SELECT name FROM sqlite_schema WHERE type='table'")) as cursor:
                adicionais = []
                for (nome,) in cursor:
                    controle.restante()
                    if nome not in _COLUNAS_APLICACAO and not nome.startswith("sqlite_"):
                        adicionais.append(nome)
        except sqlite3.Error as exc:
            if interrompido_por_prazo:
                raise ErroRestauracao("inspecao", "prazo_excedido", exc) from None
            raise
    return tuple(ausentes), tuple(sorted(adicionais))


def validar_candidato_restauracao(*, backend: str, backup: str | Path,
                                 prazo: float = 30) -> CandidatoRestauracao:
    """Valida integridade/reconhecimento; não comprova compatibilidade completa.

    Reutiliza validar_backup para física, FKs, esquema mínimo e independência.
    Recusa colunas da aplicação ausentes sem executar as migrações legadas.
    Mesmo com todas as colunas, a compatibilidade permanece não comprovada.
    """
    _backend(backend)
    controle = _Prazo(prazo)
    try:
        caminho = _caminho(backup)
        antes = _arquivo(caminho)
        _sem_auxiliares(caminho)
        hash_antes = _hash(caminho, antes, controle)
        validacao = validar_backup(backend=backend, caminho=caminho, prazo=controle.restante())
        if _arquivo(caminho) != antes or validacao.sha256 != hash_antes:
            raise ErroRestauracao("candidato", "arquivo_alterado")
        _sem_auxiliares(caminho)
        ausentes, adicionais = _estrutura(caminho, controle)
        if (_arquivo(caminho) != antes or _hash(caminho, antes, controle) != hash_antes
                or _arquivo(caminho) != antes
                or any(_info(Path(str(caminho) + s)) is not None for s in _SIDECARS)):
            raise ErroRestauracao("candidato", "arquivo_alterado")
        controle.restante()
        avisos = ("artefato_deve_permanecer_fechado_e_estavel",)
        avisos += (("colunas_ausentes_exigem_analise_de_migracao",) if ausentes
                   else ("compatibilidade_completa_nao_comprovada",))
        if adicionais:
            avisos += ("tabelas_adicionais_nao_avaliadas",)
        return CandidatoRestauracao(caminho, validacao, antes,
            Compatibilidade.INCOMPATIVEL if ausentes else Compatibilidade.NAO_COMPROVADA,
            ausentes, adicionais, avisos, datetime.now(timezone.utc))
    except ErroBackup as exc:
        raise ErroRestauracao("candidato", "backup_" + exc.codigo) from None
    except ErroRestauracao:
        raise
    except Exception as exc:
        raise ErroRestauracao("candidato", "inspecao_falhou", exc) from None


def _auxiliar(caminho: Path, tipo: str) -> ArquivoAuxiliar:
    try:
        info = _info(caminho)
        return ArquivoAuxiliar(caminho, tipo, Presenca.AUSENTE if info is None else Presenca.PRESENTE,
            None if info is None else stat.S_ISREG(info.st_mode),
            None if info is None else _link(info), None if info is None else _assinatura(info))
    except OSError:
        return ArquivoAuxiliar(caminho, tipo, Presenca.NAO_VERIFICADA, None, None, None)


def _acesso(caminho: Path, modo: int) -> bool | None:
    try:
        return os.access(caminho, modo)
    except OSError:
        return None


def _ancestral_existente(caminho: Path) -> Path:
    for parte in (caminho, *caminho.parents):
        if _info(parte) is not None:
            return parte
    raise ErroRestauracao("destino", "ancestral_nao_verificado")


def inspecionar_destino_restauracao(*, backend: str, backup: str | Path,
        destino: str | Path, pasta_backups: str | Path, pasta_resgate: str | Path) -> InspecaoDestino:
    """Somente lstat/access: não abre banco, não testa escrita, não adquire lock.

    Ausência de riscos detectados não prova exclusividade. os.access é um indício
    sujeito a ACLs/TOCTOU, não prova permissão de rename/replace nem de escrita futura.
    """
    _backend(backend)
    try:
        origem, alvo, backups, resgate = map(_caminho, (backup, destino, pasta_backups, pasta_resgate))
        info, fonte = _info(alvo), _info(origem)
        antes = None if info is None else _assinatura(info)
        impedimentos = []
        if origem == alvo:
            impedimentos.append("origem_destino_identicos")
        elif info is not None and fonte is not None and (info.st_dev, info.st_ino) == (fonte.st_dev, fonte.st_ino):
            impedimentos.append("identidade_fisica_origem_destino")
        if info is None:
            impedimentos.append("destino_ausente")
        elif not stat.S_ISREG(info.st_mode):
            impedimentos.append("destino_nao_regular")
        elif info.st_nlink != 1:
            impedimentos.append("destino_com_hardlinks")
        if alvo.is_relative_to(backups):
            impedimentos.append("destino_na_arvore_de_backups")
        elif backups.is_relative_to(alvo):
            impedimentos.append("backups_na_arvore_do_destino")
        if alvo.is_relative_to(resgate) or origem.is_relative_to(resgate):
            impedimentos.append("banco_na_arvore_de_resgate")
        if resgate != alvo and resgate.is_relative_to(alvo):
            impedimentos.append("resgate_na_arvore_do_destino")
        if resgate.is_relative_to(backups) or backups.is_relative_to(resgate):
            impedimentos.append("resgate_e_backups_sobrepostos")
        for pasta, codigo in ((backups, "pasta_backups"), (resgate, "pasta_resgate"), (alvo.parent, "pasta_destino")):
            entrada = _info(pasta)
            if entrada is None:
                if pasta != resgate:
                    impedimentos.append(codigo + "_ausente")
            elif not stat.S_ISDIR(entrada.st_mode):
                impedimentos.append(codigo + "_nao_diretorio")
        auxiliares = tuple(_auxiliar(Path(str(alvo) + s), s[1:]) for s in _SIDECARS) + (
            _auxiliar(Path(str(alvo) + ".lock"), "lock_legado"),
            _auxiliar(backups / ".seagbh-backup.lock", "lock_backups"),
        )
        for auxiliar in auxiliares:
            if auxiliar.presenca != Presenca.AUSENTE:
                impedimentos.append(auxiliar.tipo + "_" + auxiliar.presenca.value)
            if auxiliar.inseguro or auxiliar.regular is False:
                impedimentos.append(auxiliar.tipo + "_inseguro")
        permissoes = PermissoesObservadas(
            None if info is None else _acesso(alvo, os.R_OK),
            None if info is None else _acesso(alvo, os.W_OK),
            _acesso(alvo.parent, os.W_OK | os.X_OK),
            _acesso(_ancestral_existente(resgate), os.W_OK | os.X_OK),
        )
        for nome, valor in vars(permissoes).items():
            if valor is False or (valor is None and (info is not None or nome not in {"leitura_arquivo", "escrita_arquivo"})):
                impedimentos.append("permissao_" + nome + "_nao_confirmada")
        atual = _info(_caminho(alvo))
        if (None if atual is None else _assinatura(atual)) != antes or auxiliares != tuple(
                _auxiliar(a.caminho, a.tipo) for a in auxiliares):
            impedimentos.append("destino_alterado_durante_inspecao")
        avisos = ("exclusividade_nao_comprovada", "permissoes_apenas_indicios",
                  "conteudo_destino_nao_inspecionado", "revalidacao_obrigatoria_antes_da_execucao")
        return InspecaoDestino(alvo, info is not None, antes, auxiliares, permissoes,
                               tuple(dict.fromkeys(impedimentos)), avisos, datetime.now(timezone.utc))
    except ErroRestauracao:
        raise
    except Exception as exc:
        raise ErroRestauracao("destino", "inspecao_falhou", exc) from None


def planejar_restauracao(*, backend: str, backup: str | Path, destino: str | Path,
        pasta_backups: str | Path, pasta_resgate: str | Path, prazo: float = 30) -> PlanoRestauracao:
    """Diagnóstico pontual, nunca autorização, reserva ou passe para substituição.

    Erros de backend/caminhos são exceções de domínio. Demais recusas de candidato
    e falhas de inspeção do destino ficam explícitas no plano. Não há executor.
    """
    _backend(backend)
    controle = _Prazo(prazo)
    origem, alvo, backups, resgate = map(_caminho, (backup, destino, pasta_backups, pasta_resgate))
    candidato = inspecao = falha_candidato = falha_destino = None
    estado = EstadoCandidato.NAO_INSPECIONADO
    impedimentos = []
    avisos = ["plano_nao_autoriza_restauracao", "exclusividade_nao_comprovada"]
    try:
        inspecao = inspecionar_destino_restauracao(backend=backend, backup=origem,
            destino=alvo, pasta_backups=backups, pasta_resgate=resgate)
        impedimentos.extend(inspecao.impedimentos)
        avisos.extend(inspecao.avisos)
    except ErroRestauracao as exc:
        falha_destino = FalhaPreValidacao(exc.fase, exc.codigo)
        impedimentos.append("inspecao_destino_nao_concluida")
    if inspecao is not None:
        identico = any(c in impedimentos for c in ("origem_destino_identicos", "identidade_fisica_origem_destino"))
        try:
            if identico:
                raise ErroRestauracao("candidato", "origem_destino_sem_separacao")
            candidato = validar_candidato_restauracao(backend=backend, backup=origem, prazo=controle.restante())
            estado = EstadoCandidato.VALIDADO
            avisos.extend(candidato.avisos)
            if candidato.compatibilidade == Compatibilidade.INCOMPATIVEL:
                impedimentos.append("colunas_da_aplicacao_ausentes")
            else:
                impedimentos.append("compatibilidade_nao_comprovada")
        except ErroRestauracao as exc:
            falha_candidato = FalhaPreValidacao(exc.fase, exc.codigo)
            estado = EstadoCandidato.RECUSADO
            impedimentos.append("candidato_recusado")
        # A validação pode demorar: reflita mudanças observadas no destino nesse intervalo.
        try:
            controle.restante()
            atual = inspecionar_destino_restauracao(backend=backend, backup=origem,
                destino=alvo, pasta_backups=backups, pasta_resgate=resgate)
            if (atual.assinatura, atual.auxiliares) != (inspecao.assinatura, inspecao.auxiliares):
                impedimentos.append("destino_alterado_durante_planejamento")
            inspecao = atual
            impedimentos.extend(atual.impedimentos)
        except ErroRestauracao as exc:
            falha_destino = FalhaPreValidacao(exc.fase, exc.codigo)
            impedimentos.append("reinspecao_destino_nao_concluida")
    impedimentos.append("exclusividade_nao_comprovada")
    return PlanoRestauracao(backend, origem, alvo, backups, resgate, estado, candidato,
        falha_candidato, inspecao, falha_destino, tuple(dict.fromkeys(impedimentos)),
        tuple(dict.fromkeys(avisos)), datetime.now(timezone.utc))
