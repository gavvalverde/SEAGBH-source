# 🔍 ANÁLISE COMPLETA DE BUGS — SEAGBH
**Data:** 19 de Março de 2026  
**Revisor:** Análise automática via agent  
**Total de problemas:** 22 (3 Críticos, 8 Altos, 11 Médios)

---

## 🔴 PROBLEMAS CRÍTICOS

### Bug 1 — Vazamento de Recurso em `_salvar_dados_brutos()`
- **Arquivo:** [src/core/licenca.py](src/core/licenca.py#L249-L258)
- **Linha:** ~249-258
- **Severidade:** 🔴 CRÍTICO
- **Descrição:** A função abre arquivo com `open()` mas não garante fechamento. Se exceção ocorrer durante `f.write()`, arquivo pode ficar corrompido ou permanecer aberto.
- **Impacto:** Data corruption, arquivo .licenca.dat ilegível
- **Correção:** Usar arquivo temporário + atomic rename com `os.replace()`

---

### Bug 2 — Race Condition em `aplicar_atualizacao()`
- **Arquivo:** [src/core/atualizacao.py](src/core/atualizacao.py#L146-L180)
- **Linha:** ~146-180 e [src/ui/main_window.py](src/ui/main_window.py#L458-L462)
- **Severidade:** 🔴 CRÍTICO
- **Descrição:** App chama `QTimer.singleShot(2000, quit)` mas .bat pode não ter iniciado em 2 segundos em sistemas lentos. Se app fechar antes do .bat rodar, atualização é perdida silenciosamente.
- **Impacto:** Atualização falha sem mensagem, usuário não sabe que está desatualizado
- **Correção:** Implementar handshake: criar arquivo de lock que .bat confirma ao terminar. Aguardar até 5-10 segundos.

---

### Bug 3 — Validação Inadequada de Chaves de Licença
- **Arquivo:** [tools/gerador_chaves.py](tools/gerador_chaves.py#L560-L600)
- **Linha:** ~560-600 (_on_gerar)
- **Severidade:** 🔴 CRÍTICO
- **Descrição:** Não há limite de caracteres (`setMaxLength()`) no QLineEdit. Usuário pode colar 10000+ caracteres que corrompem JSON do histórico.
- **Impacto:** Arquivo `historico_chaves.json` fica inválido, gerador de chaves não consegue mais ler histórico
- **Correção:** Adicionar `setMaxLength(100)` e validação explícita antes de salvar

---

## 🟠 PROBLEMAS ALTOS

### Bug 4 — Timeout Inadequado durante Ativação de Licença
- **Arquivo:** [src/ui/licenca_dialog.py](src/ui/licenca_dialog.py#L155-L170)
- **Linha:** ~155-170
- **Severidade:** 🟠 ALTO
- **Descrição:** Socket timeout de 2 segundos é muito curto para Firebase em conexões lentas. Pode causar false positives de expiração.
- **Impacto:** Em WiFi lento, ativação de licença falha mesmo estando válida
- **Correção:** Aumentar para 10 segundos; diferenciar `socket.timeout` de outros erros

---

### Bug 5 — Caminhos com Espaços no Script .bat
- **Arquivo:** [src/core/atualizacao.py](src/core/atualizacao.py#L121-L145)
- **Linha:** ~121-145 (`bat_content`)
- **Severidade:** 🟠 ALTO
- **Descrição:** Variáveis no .bat não estão entre aspas. Se exe está em `C:\Program Files\...`, batch trata como múltiplos argumentos.
- **Impacto:** Atualização falha silenciosamente em instalações padrão do Windows
- **Correção:** Envolver variáveis: `set "EXE_PATH={exe_atual}"`

---

### Bug 6 — Print em Produção
- **Arquivo:** [src/ui/main_window.py](src/ui/main_window.py#L193)
- **Linha:** ~193
- **Severidade:** 🟠 ALTO
- **Descrição:** `print(f"QSS não encontrado: {caminho}")` expõe paths do sistema em console
- **Impacto:** Informação desnecessária em produção, confunde logs do cliente
- **Correção:** Usar `logging.warning()` em vez de `print()`

---

### Bug 7 — Socket Timeout Não Diferenciado do URLError
- **Arquivo:** [tools/gerador_chaves.py](tools/gerador_chaves.py#L115-L145)
- **Linha:** ~115-145 (_firebase_get, _firebase_put)
- **Severidade:** 🟠 ALTO
- **Descrição:** `except (URLError, OSError)` captura tudo junto. Não diferencia entre timeout de rede e erro de conexão permanente.
- **Impacto:** Impossível debugar problemas de conectividade intermitente
- **Correção:** Separar `socket.timeout`, `URLError`, e `json.JSONDecodeError` com logs diferenciados

---

### Bug 8 — Validação de Filtro Insuficiente
- **Arquivo:** [src/core/database.py](src/core/database.py#L261-L290)
- **Linha:** ~261-290 (listar_equipamentos)
- **Severidade:** 🟠 ALTO
- **Descrição:** Embora use prepared statements (seguro contra SQL injection), não valida tamanho máximo do filtro. Query enormepode travar DB.
- **Impacto:** DoS: usuário malicioso pode travar app com filtro gigante
- **Correção:** Limitar a 255 caracteres máximo

---

### Bug 9 — Logging Inadequado (Firebase)
- **Arquivo:** [tools/gerador_chaves.py](tools/gerador_chaves.py#L115-L180)
- **Linha:** ~115-180
- **Severidade:** 🟠 ALTO
- **Descrição:** Funções Firebase falham silenciosamente sem registrar por quê (timeout? endpoint inválido? sem internet?)
- **Impacto:** Impossível debugar problemas de sincronização
- **Correção:** Adicionar `logging` diferenciado por tipo de erro

---

### Bug 10 — Sem Limite de Tamanho em Firebase Operations
- **Arquivo:** [tools/gerador_chaves.py](tools/gerador_chaves.py#L2000-2100)
- **Linha:** ~2000-2100
- **Severidade:** 🟠 ALTO
- **Descrição:** Pode tentar salvar data gigante no Firebase sem limite
- **Impacto:** Rejções do Firebase; sincronização falha
- **Correção:** Validar tamanho antes de enviar

---

### Bug 11 — Sem Tratamento de Exceção em Thread de Download
- **Arquivo:** [src/ui/main_window.py](src/ui/main_window.py#L35-L60)
- **Linha:** ~35-60 (_DownloadThread.run())
- **Severidade:** 🟠 ALTO
- **Descrição:** Se `baixar_atualizacao()` lança exceção não prevista, thread morre silenciosamente
- **Impacto:** UI fica travada em "Baixando"; usuário tem que forçar fechar
- **Correção:** Try-except em _DownloadThread.run() com emit de sinal de erro

---

## 🟡 PROBLEMAS MÉDIOS

### Bug 12 — Sem Tratamento de Corrupção em .licenca.dat
- **Arquivo:** [src/core/licenca.py](src/core/licenca.py#L283-L310)
- **Linha:** ~283-310
- **Severidade:** 🟡 MÉDIO
- **Descrição:** Se arquivo está corrompido, retorna None sem logs. Usuário não sabe por que precisa reativar.
- **Correção:** Log de erro + remover arquivo automaticamente

---

### Bug 13 — Variável Não Inicializada em Database
- **Arquivo:** [src/core/database.py](src/core/database.py#L1-L30)
- **Linha:** ~1-30
- **Severidade:** 🟡 MÉDIO
- **Descrição:** Se conexão falha em `__init__`, `self.cursor` fica indefinido. Próxima operação causa AttributeError.
- **Correção:** Inicializar em None; raise exceção se falhar; tratar no main

---

### Bug 14 — Sem Tratamento de Permissão de Arquivo
- **Arquivo:** [tools/gerador_chaves.py](tools/gerador_chaves.py#L32-L60)
- **Linha:** ~32-60 (_salvar_historico)
- **Severidade:** 🟡 MÉDIO
- **Descrição:** Se diretório está protegido, não consegue gravar histórico. Falha silenciosamente.
- **Correção:** Verificar `os.access(dir, os.W_OK)` antes; registrar erro

---

### Bug 15 — Sem Upper Limit em listar_auditoria()
- **Arquivo:** [src/core/database.py](src/core/database.py#L596)
- **Linha:** ~596
- **Severidade:** 🟡 MÉDIO
- **Descrição:** Se passa `limite=999999`, carrega 1M de registros na memória
- **Correção:** Cap a 1000; adicionar paginação com `offset`

---

### Bug 16 — Encoding Hardcoded em Script .bat
- **Arquivo:** [src/core/atualizacao.py](src/core/atualizacao.py#L164-L170)
- **Linha:** ~164-170
- **Severidade:** 🟡 MÉDIO
- **Descrição:** `.bat` escrito em UTF-8 pode não funcionar em Windows com caracteres especiais (ç, ñ, etc)
- **Correção:** Usar `cp1252` (Windows) ou UTF-8 com BOM

---

### Bug 17 — Possível Data Loss em salvar_notas()
- **Arquivo:** [src/core/database.py](src/core/database.py#L447)
- **Linha:** ~447
- **Severidade:** 🟡 MÉDIO
- **Descrição:** Se `evento_id=None`, UPDATE não tem WHERE. Atualiza TODAS as notas!
- **Impacto:** Grave se bug acontecer
- **Correção:** Validar `if evento_id is None: raise ValueError(...)`

---

### Bug 18 — Memory Leak em Download Thread
- **Arquivo:** [src/ui/main_window.py](src/ui/main_window.py#L35-L60)
- **Linha:** ~35-L60
- **Severidade:** 🟡 MÉDIO
- **Descrição:** Cancelar download chama `terminate()` (brusco). Thread pode deixar sockets abertos.
- **Correção:** Usar `quit()` + `wait()` + `terminate()` como fallback

---

### Bug 19 — Sem Validação de Unicode em Dialogs
- **Arquivo:** [src/ui/dialogs.py](src/ui/dialogs.py#L1-L50)
- **Linha:** ~1-50
- **Severidade:** 🟡 MÉDIO
- **Descrição:** Caracteres especiais em nome de evento podem não exibir corretamente
- **Correção:** Validar `codigo.encode('utf-8')` antes de salvar

---

### Bug 20 — Race Condition em Múltiplas Instâncias
- **Arquivo:** [src/core/database.py](src/core/database.py#L18-L40)
- **Linha:** ~18-40
- **Severidade:** 🟡 MÉDIO
- **Descrição:** Sem arquivo de lock, 2 instâncias do app podem escrever no .db simultaneamente
- **Impacto:** Corrupção de banco de dados
- **Correção:** Implementar lock por PID ou fcntl

---

### Bug 21 — Sem Backup Automático do .licenca.dat
- **Arquivo:** [src/core/licenca.py](src/core/licenca.py)
- **Severidade:** 🟡 MÉDIO
- **Descrição:** Se arquivo se corrompe, nada pode ser feito. Sem backup.
- **Correção:** Criar backup ao iniciar app (`.licenca.dat.backup`)

---

### Bug 22 — Sem Cleanup de Arquivos Temporários
- **Arquivo:** [src/core/atualizacao.py](src/core/atualizacao.py)
- **Severidade:** 🟡 MÉDIO
- **Descrição:** Arquivos temporários em `C:\Temp\SEAGBH_*.exe` e `.bat` podem acumular se app falhar
- **Correção:** Cleanup ao iniciar app; detectar arquivos órfãos

---

# 📊 Resumo de Ação

| Severidade | Quantidade | Ação |
|---|---|---|
| 🔴 CRÍTICO | 3 | **URGENTE** — Implementar imediatamente |
| 🟠 ALTO | 8 | **IMPORTANTE** — Implementar na próxima sessão |
| 🟡 MÉDIO | 11 | **DESEJADO** — Roadmap futuro |

**Total de linhas de código para corrigir:** ~150 linhas
**Tempo estimado:** 2-3 horas
