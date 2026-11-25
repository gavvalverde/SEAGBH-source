# K70-U1-H2A — insumos da homologação congelada

**Escopo.** A base é `6d7495d6ecf69364010cad10d293c6826cf3c00b`. A preparação e os testes desta etapa operam sobre fontes e JSON inertes. O build e a execução dos executáveis de homologação exigem aprovações posteriores. Os módulos de produção e o `SEAGBH.spec` permanecem fora da configuração de homologação.

## Inventário e preparação

`tools/homologacao_k70_u1/preparar.py` declara a origem, o destino relativo e o SHA-256 dos insumos. A preparação normaliza CRLF para LF e grava exclusivamente em um diretório novo fora do checkout. Os arquivos de controle ficam na raiz e os insumos do executável ficam em `pacote/`.

```text
homologacao.spec
preparar.py
requirements-build-windows.txt
pacote/
  lancador.py
  variante.json
  core/
    __init__.py
    atualizacao.py
    _instalador_atualizacao.py
    _acl_historico_windows.py
    versao.py
```

- Os cinco arquivos de `core/` são os textos reais da base, sem alterações no comportamento. O lançador e os JSON das variantes são exclusivos do ensaio. Cada árvore recebe um único `variante.json`, o único recurso passado a `datas`. Os três arquivos na raiz controlam a preparação e o build.
- O validador recusa arquivos ou diretórios extras, arquivos ausentes, hashes divergentes, links simbólicos, junções, outros reparse points e arquivos com hard links. A lista de inclusão não tem globs e não copia diretórios do checkout. Assim, arquivos operacionais e credenciais ficam fora da árvore.
- O spec verifica a identidade do validador e valida toda a árvore antes de `Analysis`. `pathex` se restringe a `pacote/` e o spec passa uma lista explícita de exclusões. Entre as fontes aprovadas, a única referência a `core.licenca` está dentro da consulta ao Firebase no atualizador original. O lançador não chama essa consulta e o spec exclui o módulo. Os testes verificam essa fronteira por AST. A lista efetiva de módulos e recursos do executável terá de ser inspecionada antes da primeira execução.

Execute a preparação dos insumos a partir da raiz do repositório, com `$env:TEMP` em um caminho local confiável.

```powershell
& .\src\.venv\Scripts\python.exe -B -m unittest -v tests.unit.test_homologacao_k70_u1
$arvore = Join-Path $env:TEMP ('k70-h2a-insumos-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $arvore | Out-Null
& .\src\.venv\Scripts\python.exe -B tools/homologacao_k70_u1/preparar.py anterior (Join-Path $arvore 'anterior')
& .\src\.venv\Scripts\python.exe -B tools/homologacao_k70_u1/preparar.py candidata (Join-Path $arvore 'candidata')
```

## Dependências e build futuro

O lock destina-se a CPython 3.12, Windows AMD64. Ele fixa **PyInstaller**, **pyinstaller-hooks-contrib**, **altgraph**, **packaging**, **pefile**, **pywin32-ctypes** e **setuptools**, com um SHA-256 de wheel oficial para cada pacote. Os hashes e as dependências condicionais de Windows foram conferidos pelos metadados públicos do PyPI. A venv de desenvolvimento é suficiente para rodar a suíte estática e não precisa de PyInstaller.

Após autorização do build, obter os wheels por canal oficial, conferi-los contra o lock e instalar em venv exclusiva com `--no-index --find-links <wheels-verificados> --only-binary=:all: --require-hashes`. Registrar Python, pip, PyInstaller, hooks, dependências, commit e hashes da árvore e do artefato. O spec confere a plataforma e as versões instaladas. A procedência depende da instalação com hashes verificados. [Release fixada no PyPI](https://pypi.org/project/pyinstaller/6.22.3/) · [Instalação com verificação de hashes](https://pip.pypa.io/en/stable/topics/secure-installs/)

O spec monta um **onefile** com console para diagnóstico e UPX desabilitado. Os diretórios de trabalho e saída do PyInstaller deverão ficar fora da árvore validada, com saídas separadas por variante. O modo onedir não representa a cópia isolada do executável que o instalador realiza. A configuração de produção sem console exige evidência separada. [Configuração onefile](https://pyinstaller.org/en/stable/spec-files.html#spec-file-operation)

Antes de executar qualquer artefato, conferir TOCs e a saída recursiva de `pyi-archive_viewer -l -r` contra o inventário aprovado, inclusive dentro do PYZ. Investigar cada membro inesperado, rejeitando os pacotes que incluam material operacional, módulos excluídos ou entradas fora da lista aprovada. Confirmar que a identidade de cada variante e o SHA são distintos. A suíte estática não prova o conteúdo final, o bootloader, a disponibilidade das DLLs, a independência do pai ou o estado do Windows após a troca. [Inspeção de arquivos PyInstaller](https://pyinstaller.org/en/stable/advanced-topics.html#using-pyi-archive-viewer)

## Protocolo do ensaio posterior

Usar VM descartável e sem saída de rede, com conta dedicada e NTFS local. A árvore e o perfil devem estar sob controle exclusivo do operador do ensaio. A validação de caminhos e hashes pressupõe que outro processo com a mesma autoridade não altere os insumos entre a verificação e o uso.

Os artefatos aprovados serão identificados pelos JSONs `anterior` e `candidata`, com versões sintéticas distintas. Ambos terão o nome de saída `SEAGBH-HML-K70-U1.exe` em diretórios diferentes. Para o ensaio, a cópia anterior ocupa esse nome numa pasta nova `seagbh-hml-k70-u1-*`. A candidata fica ao lado como `candidato.bin` e serve de entrada para o snapshot. O `laboratorio.json` daquela pasta identifica o produto e os dois hashes registrados após a inspeção, com as chaves `produto`, `anterior` e `candidata`.

| Modo | Comportamento esperado |
| --- | --- |
| Inicialização normal do executável congelado | Verifica a própria identidade e o SHA esperado, registra variante, PID, PPID e nome da extração e encerra de forma controlada. |
| Agendamento | A variante anterior chama `aplicar_atualizacao` com o candidato local e sai do processo pai. O instalador original prepara o snapshot e inicia sua cópia `worker.exe`. |
| Despacho do auxiliar | Usa o mesmo argumento único e a função `executar_worker` do instalador. O lançador confere o pedido e recusa destinos com outro nome ou outros hashes. |
| Reinício | Somente a variante candidata, com SHA confirmado, registra o evento de reinício ao receber `--updated`. |

Os registros são locais e identificam os processos da fixture. O ensaio seguinte deve verificar o encerramento do pai, a extração independente, a troca e o backup por SHA, a resposta a bloqueio e interrupção e o conjunto de arquivos após cada falha. O lançador representa a inicialização mínima da fixture. A inicialização completa do produto e a saúde da GUI e do banco exigem outra homologação.

## Material HMAC legado

A leitura estática de `src/core/licenca.py` mostra material simétrico incorporado e usado como chave de HMAC na emissão e na verificação de licenças. A classificação é **segredo operacional legado de licenciamento**. Ele não é uma chave pública, um identificador ou um simples recurso visual. O fato de estar embutido em fonte é um risco de exposição e deve ser avaliado separadamente. Esta inspeção não estabelece distribuição efetiva, acesso indevido ou comprometimento. A homologação exclui o módulo inteiro e não usa nem reproduz o valor.
