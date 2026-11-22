"""Moteur par commit : worktree sparse, node_modules par lockfile, map par tree-sha (ADR-0001).

Porte de `scratch/resim_poc/drive.py`, qui a valide la chaine au J0. Le code du moteur n'est
jamais modifie : on le sort du clone tel quel, au commit du record.

Trois choses echouent dans le sandbox de la session (`.git` en lecture seule) : `git worktree
add`, `git sparse-checkout` et `npm ci`. Elles remontent alors en `NeedsShell`, qui porte la
commande exacte a lancer hors sandbox — c'est le seul endroit du projet qui en a besoin.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

# Mesure J0 : 390 Mo -> 37 Mo par commit, sans changer le resultat du replay. Le moteur
# n'importe de resources/ que des *.json ; le reste est du client.
SPARSE = ["/*", "!/map-generator/", "!/proprietary/", "!/resources/*/", "/resources/*.json"]

NODE = "/usr/bin/node"
NPM = "/usr/bin/npm"
# OpenFront exige node >=24.15 <25 avec engine-strict : nvm place parfois un v22 devant
# dans le PATH, d'ou les chemins absolus et un environnement minimal (ADR-0001).
ENV = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(Path.home())}

SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class NeedsShell(RuntimeError):
    """Une commande que le sandbox refuse : l'utilisateur doit la lancer lui-meme."""

    def __init__(self, commands: list[str], cause: str) -> None:
        super().__init__(cause)
        self.commands = commands


def clone(data_dir: Path) -> Path:
    return data_dir / "openfront"


def engine_dir(data_dir: Path, sha: str) -> Path:
    return data_dir / "engines" / sha


def _run(args: list[str], cwd: Path | None = None, check: bool = True) -> str:
    r = subprocess.run(
        args, cwd=cwd, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    if check and r.returncode != 0:
        raise RuntimeError(f"echec {args}:\n{r.stdout}")
    return (r.stdout or "").strip()


def git(data_dir: Path, *args: str, cwd: Path | None = None, check: bool = True) -> str:
    return _run(["git", *args], cwd=cwd or clone(data_dir), check=check)


def commit_exists(data_dir: Path, sha: str) -> bool:
    """Un commit peut avoir disparu d'upstream : 8 branches ont ete force-pushed au dernier
    fetch. On verifie commit par commit, jamais une seule fois pour toute l'archive."""
    if not sha or sha == "DEV" or not SHA_RE.match(sha):
        return False
    r = subprocess.run(
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
        cwd=clone(data_dir),
        check=False,
        capture_output=True,
    )
    return r.returncode == 0


def lockfile_sha(data_dir: Path, sha: str) -> str:
    return git(data_dir, "rev-parse", f"{sha}:package-lock.json")


def is_ready(data_dir: Path, sha: str) -> bool:
    wt = engine_dir(data_dir, sha)
    return (wt / "package.json").exists() and (wt / "node_modules").exists()


def node_version_note(data_dir: Path, sha: str) -> str | None:
    """La version de node attendue au commit, si elle ne correspond pas a celle qu'on lance.

    Simple indice : le vrai juge du determinisme, ce sont les hashs du record (ADR-0003).
    """
    wt = engine_dir(data_dir, sha)
    pkg = wt / "package.json"
    if not pkg.exists():
        return None
    want = (json.loads(pkg.read_text()).get("engines") or {}).get("node")
    if not want:
        return None
    got = _run([NODE, "--version"])
    major = re.match(r"v(\d+)", got)
    if major is not None and major.group(1) in want:
        return None
    return f"node attendu {want!r}, lance {got!r}"


def _add_worktree(data_dir: Path, wt: Path, sha: str) -> None:
    """Le worktree resserre : 390 Mo de checkout ramenes a 37 (mesure J0).

    Un worktree ne contient aucun objet git — son `.git` est un fichier qui pointe vers celui du
    clone — donc sa taille est celle des fichiers en clair. Les 353 Mo exclus sont
    `map-generator/` (283 Mo), les assets de `resources/` (59) et `proprietary/` (16) : le moteur
    n'en importe rien, ce qui a ete verifie au J0 en comparant les hashs d'un replay.
    """
    git(data_dir, "worktree", "add", "--no-checkout", str(wt), sha)
    _run(["git", "sparse-checkout", "init", "--no-cone"], cwd=wt)
    _run(["git", "sparse-checkout", "set", *SPARSE], cwd=wt)
    _run(["git", "checkout"], cwd=wt)


def prepare(data_dir: Path, sha: str) -> Path:
    """Rend le moteur du commit `sha` pret a tourner. Idempotent.

    Trois niveaux de cache (ADR-0001) : le code par sha, `node_modules` par hash de
    `package-lock.json` (82 versions par an contre 2 611 commits), copie en liens durs —
    jamais en lien symbolique, qui resoudrait vers le mauvais commit en silence.
    """
    wt = engine_dir(data_dir, sha)
    if not (wt / "package.json").exists():
        try:
            _add_worktree(data_dir, wt, sha)
        except RuntimeError:
            # Une inscription fantome (`git worktree list` dit `prunable`) fait refuser `add` :
            # « use 'add -f' to override, or 'prune' or 'remove' to clear ». Vecu apres une
            # eviction a moitie faite, `git worktree remove` n'etant pas atomique.
            shutil.rmtree(wt, ignore_errors=True)
            try:
                git(data_dir, "worktree", "prune")
                _add_worktree(data_dir, wt, sha)
            except RuntimeError as exc:
                shutil.rmtree(wt, ignore_errors=True)
                raise NeedsShell([f"uv run ofa engines --commit {sha}"], str(exc)) from exc
    nm = wt / "node_modules"
    if not nm.exists():
        lock = lockfile_sha(data_dir, sha)
        cache = data_dir / "node_modules" / lock
        if cache.exists():
            _run(["cp", "-al", str(cache), str(nm)])  # 1,46 s et ~11 Mo de delta, mesure J0
        else:
            try:
                subprocess.run(
                    [NPM, "ci", "--ignore-scripts"],
                    cwd=wt,
                    check=True,
                    env=ENV,
                    stdout=subprocess.DEVNULL,
                )
            except (OSError, subprocess.CalledProcessError) as exc:
                raise NeedsShell(
                    [f"cd {wt} && {NPM} ci --ignore-scripts"],
                    f"npm ci (lockfile {lock[:8]}) : {exc}",
                ) from exc
            cache.parent.mkdir(parents=True, exist_ok=True)
            _run(["cp", "-al", str(nm), str(cache)])
    return wt


def _du_mb(path: Path, exclude_node_modules: bool = False) -> int:
    args = (
        ["du", "-sm"] + (["--exclude=node_modules"] if exclude_node_modules else []) + [str(path)]
    )
    r = subprocess.run(args, check=False, text=True, capture_output=True)
    return int(r.stdout.split()[0]) if r.stdout else 0


def evict(data_dir: Path, sha: str, keep_lockfiles: set[str]) -> dict:
    """Retire le moteur d'un commit dont le lot est fini, et son `node_modules` si personne ne
    le partage plus.

    On rejoue **commit par commit** (ADR-0008) : un moteur sert une fois, puis plus jamais. Sans
    cette eviction, les 435 jours d'archive laissent ~370 worktrees (17,8 Go) et ~98 versions de
    `node_modules` (35 Go) sur un disque de 100 Go — mesure refaite apres le J3.

    Le cache de `node_modules` est un champ de liens durs : le supprimer alors qu'un worktree
    encore vivant le partage ne detruit rien, les fichiers survivent par leur autre lien. L'espace
    n'est rendu qu'au dernier lien, ce qui est exactement le comportement voulu.
    """
    wt = engine_dir(data_dir, sha)
    freed = {"commit": sha[:8], "worktree_mb": 0, "node_modules_mb": 0, "erreur": None}
    if not wt.exists():
        return freed
    lock = lockfile_sha(data_dir, sha)  # lu dans le clone, donc valable apres suppression
    mb = _du_mb(wt, exclude_node_modules=True)
    try:
        git(data_dir, "worktree", "remove", "--force", str(wt))
    except RuntimeError as exc:
        # `git worktree remove` n'est PAS atomique : mesure faite, il supprime les fichiers puis
        # echoue sur la desinscription quand `.git` est en lecture seule (le sandbox). On rapporte
        # donc ce qui s'est passe, pas ce qu'on esperait — sinon le rapport annonce 0 Mo libere
        # alors que 393 Mo sont partis, et l'inscription restee `prunable` fera refuser le
        # prochain `worktree add` (prepare() la nettoie).
        freed["erreur"] = str(exc).strip().splitlines()[-1][:200]
        if wt.exists():
            return freed
    freed["worktree_mb"] = mb
    if lock not in keep_lockfiles:
        cache = data_dir / "node_modules" / lock
        if cache.exists():
            freed["node_modules_mb"] = _du_mb(cache)
            shutil.rmtree(cache, ignore_errors=True)
    return freed


def map_dir_name(wt: Path, display: str) -> str:
    """\"Ottoman Empire\" -> \"ottomanempire\" : la cle de l'enum GameMapType, en minuscules."""
    src = (wt / "packages/engine-api/src/game/Maps.gen.ts").read_text()
    body = src[src.index("export enum GameMapType") :]
    body = body[: body.index("\n}")]
    for key, value in re.findall(r'^\s+(\w+) = "([^"]+)",', body, re.M):
        if value == display:
            return key.lower()
    raise RuntimeError(f"map inconnue a ce commit : {display!r}")


def ensure_map(data_dir: Path, sha: str, dirname: str) -> Path:
    """Materialise la seule map dont la partie a besoin, indexee par le tree-sha du dossier.

    Une map inchangee entre deux commits n'est donc stockee qu'une fois (223 versions de maps
    par an, mesure J0).
    """
    tree = git(data_dir, "rev-parse", f"{sha}:resources/maps/{dirname}")
    if not SHA_RE.match(tree):
        # Vecu : une sortie d'erreur de rev-parse utilisee comme nom de dossier a laisse un
        # `data/maps/<sha>esources/` fantome.
        raise RuntimeError(f"tree-sha invalide pour {dirname} a {sha[:8]} : {tree!r}")
    base = data_dir / "maps" / tree
    d = base / dirname
    if not d.exists():
        d.mkdir(parents=True)
        tar = subprocess.run(
            ["git", "archive", "--format=tar", tree],
            cwd=clone(data_dir),
            check=True,
            stdout=subprocess.PIPE,
        ).stdout
        subprocess.run(["tar", "-x", "-C", str(d)], input=tar, check=True)
    return base


def install_runner(wt: Path, source: Path) -> Path:
    """Copie `resim/ofa_runner.ts` dans le worktree.

    C'est une copie, jamais un lien : les imports `@openfront/*` et le chemin relatif vers
    NodeGameMapLoader doivent resoudre dans CE commit (ADR-0002).
    """
    dest = wt / "tests" / "replay" / source.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    body = source.read_bytes()
    if dest.exists() and dest.read_bytes() == body:
        return dest
    # Plusieurs workers copient le meme fichier au meme endroit : sans l'echange atomique,
    # un worker pourrait lire un runner a moitie ecrit (et le relire a chaque partie).
    tmp = dest.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_bytes(body)
    os.replace(tmp, dest)
    return dest
