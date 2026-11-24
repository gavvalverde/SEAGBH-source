# Atualizacao Git pelo VS Code

O projeto ativa `git.autofetch` e `git.pruneOnFetch`. Essas opcoes consultam as novidades do remoto, mas **nao fazem pull nem alteram arquivos locais automaticamente**.

Abra **Terminal > Run Task** (ou `Ctrl+Shift+P` > **Tasks: Run Task**) e selecione:

- **Git: Atualizar branch atual (pull seguro)**: executa `git pull --ff-only` na raiz do workspace. Atualiza a branch atualmente selecionada usando o upstream configurado e recusa merges implicitos. Se a branch nao tiver upstream, falha sem adivinhar um destino.
- **Git: Consultar atualizacoes (fetch)**: executa `git fetch --prune origin`, sem modificar a branch local nem o working tree.

## Para atualizar a main

Abra o terminal do VS Code e execute:

```powershell
git status
git switch main
git pull --ff-only origin main
```

Se `git switch main` for recusado porque ha alteracoes locais, **nao use `git reset --hard` nem `git stash` automaticamente**: salve, revise ou finalize essas alteracoes antes de mudar de branch.

Para fazer do fast-forward a politica padrao do Git **somente neste repositorio**, execute uma unica vez:

```powershell
git config --local pull.ff only
git config --local fetch.prune true
```

Isso nao modifica o remoto nem altera o comportamento de outros repositorios. A configuracao local do Git fica em `.git/config` e nao e versionada.

O `pull` da task atua na **branch atual**, nao troca automaticamente para `main`. Nao execute `git pull origin main` enquanto estiver numa branch de feature esperando atualizar `main`: isso pode integrar a `main` na branch errada. A CI de seguranca do PR #84 nao faz parte dessas tasks.
