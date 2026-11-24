"""ACLs NTFS para o armazenamento privado de licenças."""

from ctypes import (
    POINTER, Structure, WinDLL, addressof, byref, c_void_p, cast, create_string_buffer,
    create_unicode_buffer, get_last_error, sizeof,
)
from ctypes.wintypes import BOOL, BYTE, DWORD, HANDLE, LPCWSTR, LPWSTR, WORD


def _api(biblioteca, nome, retorno, *argumentos):
    funcao = getattr(biblioteca, nome)
    funcao.restype = retorno
    funcao.argtypes = argumentos
    return funcao


_kernel = WinDLL("kernel32", use_last_error=True)
_advapi = WinDLL("advapi32", use_last_error=True)
_ptr = POINTER(c_void_p)
_liberar = _api(_kernel, "LocalFree", c_void_p, c_void_p)
_processo = _api(_kernel, "GetCurrentProcess", HANDLE)
_fechar = _api(_kernel, "CloseHandle", BOOL, HANDLE)
_abrir_token = _api(_advapi, "OpenProcessToken", BOOL, HANDLE, DWORD, POINTER(HANDLE))
_info_token = _api(_advapi, "GetTokenInformation", BOOL, HANDLE, DWORD, c_void_p, DWORD, POINTER(DWORD))
_texto_sid = _api(_advapi, "ConvertSidToStringSidW", BOOL, c_void_p, POINTER(LPWSTR))
_get_acl = _api(_advapi, "GetNamedSecurityInfoW", DWORD, LPCWSTR, DWORD, DWORD, _ptr, _ptr, _ptr, _ptr, _ptr)
_get_ace = _api(_advapi, "GetAce", BOOL, c_void_p, DWORD, _ptr)
_get_control = _api(_advapi, "GetSecurityDescriptorControl", BOOL, c_void_p, POINTER(WORD), POINTER(DWORD))
_sddl = _api(
    _advapi, "ConvertStringSecurityDescriptorToSecurityDescriptorW", BOOL, LPCWSTR, DWORD, _ptr, POINTER(DWORD)
)
_criar = _api(_kernel, "CreateDirectoryW", BOOL, LPCWSTR, c_void_p)
_tipo_volume = _api(_kernel, "GetDriveTypeW", DWORD, LPCWSTR)
_volume = _api(
    _kernel, "GetVolumeInformationW", BOOL, LPCWSTR, LPWSTR, DWORD, POINTER(DWORD),
    POINTER(DWORD), POINTER(DWORD), LPWSTR, DWORD,
)

_SISTEMA = "S-1-5-18"
_ADMINISTRADORES = "S-1-5-32-544"
_DIREITOS_PROPRIETARIO = "S-1-3-4"
_TRUSTED_INSTALLER = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
# Direitos que permitem remover um filho ou adulterar a ACL/atributos do ancestral.
_CONTROLE_ANCESTRAL = 0x10000000 | 0x40000000 | 0xD0000 | 0x40 | 0x100
_ACL_PROTEGIDA = 0x1000
_HERANCA_APENAS = 0x08
_HERDAR_ARQUIVOS = 0x01
_ACESSO_TOTAL = 0x1F01FF


class _Acl(Structure):
    _fields_ = [("revisao", BYTE), ("reservado", BYTE), ("tamanho", WORD), ("quantidade", WORD)]


class _Ace(Structure):
    _fields_ = [("tipo", BYTE), ("flags", BYTE), ("tamanho", WORD), ("mascara", DWORD), ("sid", DWORD)]


class _Atributos(Structure):
    _fields_ = [("tamanho", DWORD), ("descritor", c_void_p), ("herdavel", BOOL)]


def _sid_texto(sid):
    texto = LPWSTR()
    if not _texto_sid(sid, byref(texto)):
        raise PermissionError("sid_indisponivel")
    try:
        return texto.value
    finally:
        _liberar(cast(texto, c_void_p))


def operador():
    token = HANDLE()
    if not _abrir_token(_processo(), 0x0008, byref(token)):  # TOKEN_QUERY
        raise PermissionError("token_indisponivel")
    try:
        tamanho = DWORD()
        _info_token(token, 1, None, 0, byref(tamanho))  # TokenUser
        if not tamanho.value:
            raise PermissionError("token_indisponivel")
        dados = create_string_buffer(tamanho.value)
        if not _info_token(token, 1, dados, tamanho, byref(tamanho)):
            raise PermissionError("token_indisponivel")
        return _sid_texto(cast(dados, _ptr)[0])
    finally:
        _fechar(token)


def validar_volume(raiz):
    formato = create_unicode_buffer(32)
    if (_tipo_volume(raiz) != 3  # DRIVE_FIXED
            or not _volume(raiz, None, 0, None, None, None, formato, len(formato))
            or formato.value.upper() != "NTFS"):
        raise PermissionError("volume_sem_ntfs_local")


def criar_privado(pasta, sid):
    descritor = c_void_p()
    # A DACL protegida impede a incorporação de permissões do diretório pai.
    regra = f"O:{sid}D:P(A;OICI;FA;;;{sid})(A;OICI;FA;;;SY)"
    if not _sddl(regra, 1, byref(descritor), None):
        raise PermissionError("acl_indisponivel")
    try:
        atributos = _Atributos(sizeof(_Atributos), descritor, False)
        if not _criar(str(pasta), byref(atributos)) and get_last_error() != 183:
            raise PermissionError("diretorio_privado_indisponivel")
    finally:
        _liberar(descritor)


def validar_acl(caminho, sid, *, privado, diretorio):
    dono, dacl, descritor = c_void_p(), c_void_p(), c_void_p()
    # SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION.
    erro = _get_acl(str(caminho), 1, 5, byref(dono), None, byref(dacl), None, byref(descritor))
    if erro in (2, 3):  # FILE_NOT_FOUND, PATH_NOT_FOUND
        raise FileNotFoundError("arquivo_indisponivel")
    if erro:
        raise PermissionError("acl_indisponivel")
    try:
        confiaveis = {sid, _SISTEMA, _ADMINISTRADORES}
        proprietarios = {sid} if privado and diretorio else confiaveis
        if not privado:
            proprietarios = confiaveis | {_TRUSTED_INSTALLER}
        if not dono or not dacl or _sid_texto(dono) not in proprietarios:
            raise PermissionError("proprietario_ou_acl_insegura")
        if privado and diretorio:
            controle, revisao = WORD(), DWORD()
            if not _get_control(descritor, byref(controle), byref(revisao)) or not controle.value & _ACL_PROTEGIDA:
                raise PermissionError("acl_herdada")
        herda_acl_privada = False
        for indice in range(cast(dacl, POINTER(_Acl)).contents.quantidade):
            endereco = c_void_p()
            if not _get_ace(dacl, indice, byref(endereco)):
                raise PermissionError("ace_indisponivel")
            ace = cast(endereco, POINTER(_Ace)).contents
            if ace.tipo not in (0, 1):  # ACCESS_ALLOWED_ACE, ACCESS_DENIED_ACE
                raise PermissionError("ace_nao_suportada")
            if ace.tipo == 1 or (not privado and ace.flags & _HERANCA_APENAS):
                continue
            beneficiario = _sid_texto(addressof(ace) + _Ace.sid.offset)
            if (beneficiario not in confiaveis and beneficiario != _DIREITOS_PROPRIETARIO
                    and (privado or ace.mascara & _CONTROLE_ANCESTRAL)):
                raise PermissionError("acl_concede_acesso")
            if (beneficiario == sid and ace.flags & _HERDAR_ARQUIVOS
                    and ace.mascara & _ACESSO_TOTAL == _ACESSO_TOTAL):
                herda_acl_privada = True
        if privado and diretorio and not herda_acl_privada:
            raise PermissionError("arquivos_sem_heranca_privada")
    finally:
        _liberar(descritor)
