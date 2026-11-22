#!/usr/bin/env bash
# Prépare l'environnement du projet ofa dans WSL2 (Ubuntu 22.04/24.04).
# Marche aussi sur Debian 12 / Ubuntu natif (le serveur). Relançable sans risque.
#
# Usage, depuis Ubuntu (WSL), dans le dossier dézippé :
#   bash setup-wsl.sh            # installe dans ~/ofa
#   OFA_DIR=~/code/ofa bash setup-wsl.sh
set -euo pipefail

OFA_DIR="${OFA_DIR:-$HOME/ofa}"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NODE_MAJOR=24   # OpenFront au 21/11/2026 : node >=24.15 <25, npm >=12.1 <13 (engine-strict)
NPM_MAJOR=12

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[!] %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m[x] %s\033[0m\n' "$*" >&2; exit 1; }

# Un binaire Windows visible via l'interop WSL (/mnt/c/...) ne compte pas.
have() { local p; p="$(command -v "$1" 2>/dev/null)" || return 1; [[ "$p" != /mnt/* ]]; }

export PATH="$HOME/.local/bin:$PATH"

# --- 0. Vérifications ---------------------------------------------------------
say "Vérification de l'environnement"
[[ $EUID -ne 0 ]] || die "Lance ce script avec ton utilisateur normal, pas en root (sudo est demandé quand il faut)."
have apt-get || die "apt-get introuvable : ce script vise Ubuntu/Debian."
if grep -qi microsoft /proc/version; then
  if uname -r | grep -qi 'wsl2\|microsoft-standard'; then
    echo "WSL2 détecté."
  else
    die "WSL1 détecté : le sandbox Claude Code exige WSL2. Dans PowerShell : wsl --set-version <distro> 2"
  fi
else
  warn "Pas WSL : installation Linux classique (ok pour le serveur Debian)."
fi

# --- 1. Projet dans le système de fichiers Linux --------------------------------
say "Copie du projet vers $OFA_DIR"
if [[ "$SRC_DIR" != "$OFA_DIR" ]]; then
  [[ "$OFA_DIR" != /mnt/* ]] || die "OFA_DIR doit être hors de /mnt (les fichiers Windows sont très lents depuis WSL)."
  mkdir -p "$OFA_DIR"
  # -n : n'écrase jamais un fichier déjà présent (relance sans risque)
  cp -rn "$SRC_DIR"/. "$OFA_DIR"/ 2>/dev/null || cp -r --update=none "$SRC_DIR"/. "$OFA_DIR"/
  echo "Copié (fichiers existants conservés)."
else
  echo "Déjà en place."
fi

# --- 2. Paquets système ---------------------------------------------------------
say "Paquets système (sudo)"
sudo apt-get update -qq
sudo apt-get install -y -qq \
  git jq curl ca-certificates gnupg unzip zstd tmux build-essential \
  bubblewrap socat

# --- 3. Sandbox : AppArmor (Ubuntu 24.04+) ---------------------------------------
# Doc Claude Code : si kernel.apparmor_restrict_unprivileged_userns = 1, bwrap a besoin d'un profil.
if [[ "$(sysctl -n kernel.apparmor_restrict_unprivileged_userns 2>/dev/null || echo 0)" == "1" ]]; then
  say "Profil AppArmor pour bubblewrap"
  sudo tee /etc/apparmor.d/bwrap >/dev/null <<'EOF'
abi <abi/4.0>,
include <tunables/global>

profile bwrap /usr/bin/bwrap flags=(unconfined) {
  userns,
  include if exists <local/bwrap>
}
EOF
  if pidof systemd >/dev/null 2>&1; then
    sudo systemctl reload apparmor || sudo apparmor_parser -r /etc/apparmor.d/bwrap
  else
    sudo apparmor_parser -r /etc/apparmor.d/bwrap
  fi
fi

# --- 4. Node + npm (pour la re-simulation du moteur OpenFront) -------------------
cur_node_major() { have node && node -p 'process.versions.node.split(".")[0]' || echo 0; }
if [[ "$(cur_node_major)" != "$NODE_MAJOR" ]]; then
  say "Node $NODE_MAJOR (NodeSource)"
  curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | sudo -E bash -
  sudo apt-get install -y -qq nodejs
fi
if [[ "$(npm -v | cut -d. -f1)" != "$NPM_MAJOR" ]]; then
  say "npm $NPM_MAJOR"
  sudo npm install -g "npm@$NPM_MAJOR" --loglevel=error
fi

# --- 5. uv + Python 3.12 --------------------------------------------------------
if ! have uv; then
  say "uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
say "Python 3.12 (géré par uv)"
uv python install 3.12

# --- 6. DuckDB CLI --------------------------------------------------------------
if ! have duckdb; then
  say "DuckDB CLI"
  if curl -fsSL https://install.duckdb.org | sh; then
    mkdir -p "$HOME/.local/bin"
    ln -sf "$HOME/.duckdb/cli/latest/duckdb" "$HOME/.local/bin/duckdb"
  else
    warn "Installation DuckDB CLI échouée (non bloquant : la lib Python duckdb suffit pour le pipeline)."
  fi
fi

# --- 7. Claude Code (installeur natif officiel) ----------------------------------
if ! have claude; then
  say "Claude Code"
  curl -fsSL https://claude.ai/install.sh | bash
fi

# ~/.local/bin dans le PATH des nouveaux terminaux
if ! grep -q 'HOME/.local/bin' "$HOME/.bashrc" 2>/dev/null; then
  echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
fi

# --- 8. Initialisation du projet -------------------------------------------------
say "Initialisation du projet"
cd "$OFA_DIR"
git rev-parse --git-dir >/dev/null 2>&1 || git init -q -b main
chmod +x .claude/hooks/*.sh setup-wsl.sh
[[ -f .env ]] || cp .env.example .env
mkdir -p data scratch docs/adr findings/prereg findings/cards tests/fixtures
git config user.name  >/dev/null || warn "git user.name non défini : git config --global user.name 'Djo'"
git config user.email >/dev/null || warn "git user.email non défini : git config --global user.email 'toi@exemple.fr'"

# Le kit de démarrage n'a pas encore de pyproject.toml : seule une installation depuis
# le dépôt (le serveur) en a un, et c'est là que les dépendances doivent être posées.
if [[ -f pyproject.toml ]]; then
  say "Dépendances Python (uv sync)"
  uv sync
fi

# --- 9. Vérification -------------------------------------------------------------
say "Vérification"
fail=0
check() { if "$@" >/dev/null 2>&1; then printf '  ok   %s\n' "$*"; else printf '  ECHEC %s\n' "$*"; fail=1; fi; }
check git --version
check jq --version
check node --version
check npm --version
check uv --version
check uv run --no-project --python 3.12 python -c 'import sys; assert sys.version_info[:2]==(3,12)'
check claude --version
check bwrap --ro-bind / / --dev /dev true
check socat -V
have duckdb && check duckdb --version || warn "duckdb CLI absent (optionnel)"
check jq -e . .claude/settings.json
printf '  node %s | npm %s | %s | %s\n' "$(node -v)" "$(npm -v)" "$(uv --version)" "$(claude --version 2>/dev/null || echo '?')"

if [[ $fail -ne 0 ]]; then
  warn "Au moins une vérification a échoué (voir ci-dessus). Corrige puis relance : bash setup-wsl.sh"
  exit 1
fi

cat <<EOF

$(printf '\033[1;32m')Environnement prêt.$(printf '\033[0m')

Étapes suivantes :
  1. Ouvre un nouveau terminal Ubuntu (pour le PATH), puis :  cd $OFA_DIR
  2. Édite .env (ANTHROPIC_API_KEY, OFA_MY_PUBLIC_ID) et remplace <MON_PUBLIC_ID> dans PROMPT.md.
  3. claude                       -> connecte-toi, puis vérifie :
       /sandbox   (aucune dépendance manquante)
       /hooks     (format.sh et gate.sh listés)
       /context   (CLAUDE.md chargé)
     puis quitte (/exit).
  4. claude --permission-mode plan
     -> colle le contenu de PROMPT.md (jalon J0).

Depuis Windows, le projet est accessible via : \\\\wsl\$\\<distro>\\home\\$USER\\${OFA_DIR#$HOME/}
Avec VS Code : 'code .' depuis $OFA_DIR (extension WSL).
EOF
