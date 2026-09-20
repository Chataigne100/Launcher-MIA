"""
MIA Launcher — lanceur / auto-updater de l'application MIA (INTERPC).

Ce programme est distribué SÉPARÉMENT de MIA.exe. C'est lui que les
utilisateurs téléchargent en premier ; il se charge ensuite de télécharger
et de maintenir à jour MIA.exe tout seul.

Fonctionnement, à chaque lancement :
  1. Lit la version installée localement (si l'app a déjà été téléchargée).
  2. Va lire version.txt sur GitHub pour connaître la version disponible.
  3. Compare les deux :
       - Version distante PLUS RÉCENTE (ou rien d'installé)
            -> téléchargement automatique, sans rien demander.
       - Version distante IDENTIQUE
            -> rien à faire, on lance directement.
       - Version distante PLUS ANCIENNE que celle installée
            -> on demande confirmation à l'utilisateur avant de réinstaller
               une version antérieure (cas rare : rollback assumé par toi).
  4. Lance MIA.exe.

Si GitHub est injoignable mais qu'une version est déjà installée localement,
le launcher lance simplement cette version sans bloquer l'utilisateur.
"""

import json
import os
import subprocess
import sys
import threading
import tkinter as tk
import urllib.error
import urllib.request
from tkinter import messagebox, ttk

# ====================================================================== #
# CONFIGURATION — à adapter à TON dépôt GitHub
# ====================================================================== #
GITHUB_USER = "TON_PSEUDO_GITHUB"     # <-- à remplacer
GITHUB_REPO = "INTERPC"               # <-- à remplacer si besoin
GITHUB_BRANCH = "main"                # <-- "main" ou "master" selon ton dépôt

APP_EXE_NAME = "MIA.exe"
VERSION_FILE_NAME = "version.txt"

RAW_BASE = f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/{GITHUB_BRANCH}"
REMOTE_VERSION_URL = f"{RAW_BASE}/{VERSION_FILE_NAME}"
REMOTE_EXE_URL = f"{RAW_BASE}/{APP_EXE_NAME}"

REQUEST_TIMEOUT = 8  # secondes, pour ne jamais rester bloqué indéfiniment

# ====================================================================== #
# Emplacement local (dossier utilisateur, fonctionne peu importe où le
# launcher est installé, même en lecture seule type Program Files)
# ====================================================================== #
if os.name == "nt":
    _appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    INSTALL_DIR = os.path.join(_appdata, "MIA")
else:
    INSTALL_DIR = os.path.join(os.path.expanduser("~"), ".mia")

os.makedirs(INSTALL_DIR, exist_ok=True)

LOCAL_EXE_PATH = os.path.join(INSTALL_DIR, APP_EXE_NAME)
LOCAL_VERSION_PATH = os.path.join(INSTALL_DIR, "installed_version.txt")
LAUNCHER_LOG_PATH = os.path.join(INSTALL_DIR, "launcher.log")


def log(message: str):
    print(message, flush=True)
    try:
        with open(LAUNCHER_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(message + "\n")
    except OSError:
        pass


# ====================================================================== #
# Comparaison de versions (ex: "1.2.0" vs "1.10.0" -> 1.10.0 est plus récent)
# ====================================================================== #
def parse_version(text: str):
    text = (text or "").strip().lstrip("vV")
    parts = []
    for chunk in text.split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def compare_versions(a: str, b: str) -> int:
    """Retourne -1 si a<b, 0 si a==b, 1 si a>b."""
    pa, pb = parse_version(a), parse_version(b)
    if pa < pb:
        return -1
    if pa > pb:
        return 1
    return 0


# ====================================================================== #
def read_local_version():
    if not os.path.exists(LOCAL_VERSION_PATH) or not os.path.exists(LOCAL_EXE_PATH):
        return None
    try:
        with open(LOCAL_VERSION_PATH, "r", encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def write_local_version(version: str):
    try:
        with open(LOCAL_VERSION_PATH, "w", encoding="utf-8") as f:
            f.write(version.strip())
    except OSError as e:
        log(f"Impossible d'enregistrer la version locale : {e}")


def fetch_remote_version() -> str:
    """Lève une exception si GitHub est injoignable ou le fichier absent."""
    req = urllib.request.Request(REMOTE_VERSION_URL, headers={"User-Agent": "MIA-Launcher"})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return resp.read().decode("utf-8").strip()


def download_exe(destination: str, progress_callback=None):
    """Télécharge MIA.exe avec suivi de progression. Écrit d'abord dans un
    fichier temporaire puis le renomme, pour ne jamais laisser un .exe
    à moitié téléchargé porter le nom final (ce qui casserait le prochain
    lancement)."""
    req = urllib.request.Request(REMOTE_EXE_URL, headers={"User-Agent": "MIA-Launcher"})
    tmp_path = destination + ".download"

    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        total = resp.length or 0
        downloaded = 0
        chunk_size = 65536

        with open(tmp_path, "wb") as f:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if progress_callback:
                    percent = (downloaded / total * 100) if total else None
                    progress_callback(downloaded, total, percent)

    if downloaded == 0:
        os.remove(tmp_path)
        raise RuntimeError("Le fichier téléchargé est vide.")

    # Remplacement atomique : le .exe final n'apparaît que si tout s'est bien passé.
    if os.path.exists(destination):
        os.remove(destination)
    os.replace(tmp_path, destination)


# ====================================================================== #
# Interface graphique du launcher
# ====================================================================== #
BG = "#15161d"
CARD = "#1d1f29"
ACCENT = "#7c6cf0"
TEXT = "#f2f2f7"
MUTED = "#8c8fa3"
ERROR = "#ed4245"


class LauncherWindow:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("MIA Launcher")
        self.root.configure(bg=BG)
        self.root.geometry("420x220")
        self.root.resizable(False, False)

        tk.Label(self.root, text="MIA", font=("Segoe UI", 24, "bold"), fg=ACCENT, bg=BG).pack(pady=(28, 4))

        self.status_label = tk.Label(self.root, text="Démarrage...", font=("Segoe UI", 10),
                                      fg=TEXT, bg=BG)
        self.status_label.pack(pady=(4, 14))

        self.progress = ttk.Progressbar(self.root, orient="horizontal", length=340, mode="determinate")
        self.progress.pack(pady=(0, 8))

        self.detail_label = tk.Label(self.root, text="", font=("Segoe UI", 8), fg=MUTED, bg=BG)
        self.detail_label.pack()

        self.root.after(150, self.start)

    # ------------------------------------------------------------------ #
    def set_status(self, text, detail=""):
        self.status_label.configure(text=text)
        self.detail_label.configure(text=detail)
        self.root.update_idletasks()

    def set_progress(self, percent):
        self.progress["mode"] = "determinate"
        self.progress["value"] = percent if percent is not None else 0
        self.root.update_idletasks()

    def set_progress_indeterminate(self):
        self.progress["mode"] = "indeterminate"
        self.progress.start(12)

    def fail(self, message):
        self.progress.stop()
        self.set_status("Erreur", message)
        messagebox.showerror("MIA Launcher", message)
        self.root.after(200, self.root.destroy)

    # ------------------------------------------------------------------ #
    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        """Toute la logique réseau tourne dans un thread séparé pour ne
        jamais figer la fenêtre (barre de progression fluide)."""
        self.root.after(0, self.set_status, "Vérification de la mise à jour...", "")
        self.root.after(0, self.set_progress_indeterminate)

        local_version = read_local_version()
        remote_version = None
        remote_reachable = True

        try:
            remote_version = fetch_remote_version()
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            remote_reachable = False
            log(f"Impossible de contacter GitHub : {e}")

        self.root.after(0, self.progress.stop)

        # --- GitHub injoignable ---
        if not remote_reachable:
            if local_version and os.path.exists(LOCAL_EXE_PATH):
                log("Hors ligne : lancement de la version locale existante.")
                self.root.after(0, self.set_status, "Hors ligne, lancement de la version installée...", "")
                self.root.after(600, self._launch_app)
            else:
                self.root.after(0, self.fail,
                                 "Impossible de contacter le serveur de mise à jour, et aucune version "
                                 "de MIA n'est installée localement. Vérifiez votre connexion Internet.")
            return

        # --- Décision de mise à jour ---
        if local_version is None:
            log(f"Premier lancement : téléchargement de la version {remote_version}.")
            self._download_and_launch(remote_version)
            return

        comparison = compare_versions(remote_version, local_version)

        if comparison > 0:
            log(f"Nouvelle version disponible ({local_version} -> {remote_version}) : mise à jour automatique.")
            self._download_and_launch(remote_version)

        elif comparison == 0:
            log(f"Déjà à jour (version {local_version}).")
            self.root.after(0, self.set_status, "Application à jour.", f"Version {local_version}")
            self.root.after(400, self._launch_app)

        else:
            log(f"Version distante ({remote_version}) plus ancienne que la version locale ({local_version}).")
            self.root.after(0, self._ask_downgrade, local_version, remote_version)

    # ------------------------------------------------------------------ #
    def _ask_downgrade(self, local_version, remote_version):
        answer = messagebox.askyesno(
            "MIA Launcher",
            f"La version disponible sur le serveur ({remote_version}) est plus ancienne que votre "
            f"version installée ({local_version}).\n\n"
            f"Voulez-vous quand même réinstaller cette version antérieure ?"
        )
        if answer:
            self._download_and_launch(remote_version)
        else:
            self.set_status("Lancement de la version actuelle...", f"Version {local_version}")
            self.root.after(400, self._launch_app)

    # ------------------------------------------------------------------ #
    def _download_and_launch(self, version_to_install):
        def progress_callback(downloaded, total, percent):
            if percent is not None:
                self.root.after(0, self.set_progress, percent)
                mb_done = downloaded / (1024 * 1024)
                mb_total = total / (1024 * 1024)
                self.root.after(0, self.set_status, "Téléchargement de la mise à jour...",
                                 f"{mb_done:.1f} Mo / {mb_total:.1f} Mo ({percent:.0f} %)")
            else:
                mb_done = downloaded / (1024 * 1024)
                self.root.after(0, self.set_status, "Téléchargement de la mise à jour...",
                                 f"{mb_done:.1f} Mo téléchargés")

        try:
            download_exe(LOCAL_EXE_PATH, progress_callback)
            write_local_version(version_to_install)
            log(f"Mise à jour terminée : version {version_to_install} installée.")
        except (urllib.error.URLError, OSError, RuntimeError) as e:
            log(f"Échec du téléchargement : {e}")
            self.root.after(0, self.fail, f"Le téléchargement de la mise à jour a échoué :\n{e}")
            return

        self.root.after(0, self.set_status, "Mise à jour installée.", f"Version {version_to_install}")
        self.root.after(500, self._launch_app)

    # ------------------------------------------------------------------ #
    def _launch_app(self):
        if not os.path.exists(LOCAL_EXE_PATH):
            self.fail("MIA.exe est introuvable après la mise à jour. Réessayez de lancer le launcher.")
            return

        if os.environ.get("MIA_LAUNCHER_TEST") == "1":
            # Mode test : on ne lance pas vraiment l'exécutable Windows,
            # utile pour vérifier la logique du launcher sur Linux/macOS.
            log(f"[TEST] Aurait lancé : {LOCAL_EXE_PATH}")
            self.root.destroy()
            return

        try:
            subprocess.Popen([LOCAL_EXE_PATH], cwd=INSTALL_DIR)
        except OSError as e:
            self.fail(f"Impossible de lancer MIA :\n{e}")
            return

        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    LauncherWindow().run()


if __name__ == "__main__":
    main()
