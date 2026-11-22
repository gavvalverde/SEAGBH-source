"""Executa as fixtures em uma instância temporária do Firebase Local Emulator."""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from test_rtdb import PROJETO


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firebase", default="firebase", help="Caminho do Firebase CLI.")
    args = parser.parse_args()
    firebase = shutil.which(args.firebase)
    if not firebase or not shutil.which("java"):
        parser.error("Disponibilize Firebase CLI e Java no PATH ou informe --firebase.")

    fonte = Path(__file__).resolve().parent
    python = '"%SEAGBH_TEST_PYTHON%"' if os.name == "nt" else '"$SEAGBH_TEST_PYTHON"'
    with tempfile.TemporaryDirectory(prefix="seagbh-firebase-") as temporario:
        pasta = Path(temporario)
        for nome in ("firebase.json", "rules.fixture.json", "test_rtdb.py"):
            shutil.copyfile(fonte / nome, pasta / nome)

        chaves_sistema = ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TMP", "TEMP", "TMPDIR")
        env = {chave: os.environ[chave] for chave in chaves_sistema if chave in os.environ}
        env.update({
            "XDG_CONFIG_HOME": str(pasta / "cli-config"),
            "FIREBASE_EMULATORS_PATH": str(pasta / "emuladores"),
            "NO_UPDATE_NOTIFIER": "1",
            "CI": "true",
            "PYTHONIOENCODING": "utf-8",
            "SEAGBH_TEST_PYTHON": sys.executable,
        })
        resultado = subprocess.run(
            [
                str(Path(firebase).resolve()), "--non-interactive", "--project", PROJETO,
                "--config", "firebase.json", "emulators:exec", "--only", "auth,database",
                f"{python} -B test_rtdb.py",
            ],
            cwd=pasta, env=env, check=False,
        )
    return resultado.returncode


if __name__ == "__main__":
    sys.exit(main())
