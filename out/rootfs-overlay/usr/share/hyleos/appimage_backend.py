#!/usr/bin/env python3
"""Backend AppImage per HyleOS Store: installazione solo nell'home dell'utente.

Nessuna elevazione di privilegi: questo modulo non usa pkexec/doas/sudo e non
scrive mai in /usr, /opt o altrove al di fuori di ``$HOME`` (tranne la lettura
del catalogo di sistema).

Catalogo JSON (versione 1)::

    {"version": 1, "apps": [{"id", "name", "summary", "category", "version",
                             "download_url", "sha256", "icon_url"}]}

Percorso del catalogo, in ordine di priorità: argomento esplicito, variabile
d'ambiente HYLEOS_APPIMAGE_CATALOG, /etc/xdg/hyleos/appimage-catalog.json
(override dell'amministratore), /usr/share/hyleos/appimage-catalog.json
(predefinito).

NOTA sugli esempi: appimage-catalog.example.json contiene voci dimostrative con
sha256 e download_url vuoti. Il catalogo di esempio resta leggibile, ma
install() rifiuta esplicitamente hash vuoti o URL assenti: gli esempi non sono
installabili finché un amministratore non inserisce URL verificati e hash reali.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

DEFAULT_CATALOG = "/usr/share/hyleos/appimage-catalog.json"
ADMIN_CATALOG = "/etc/xdg/hyleos/appimage-catalog.json"
CATALOG_ENV = "HYLEOS_APPIMAGE_CATALOG"

CATALOG_VERSION = 1
MANIFEST_VERSION = 1
MANIFEST_REL = os.path.join(".local", "state", "hyleos", "appimage-installed.json")
ICON_EXTS = {".png", ".svg", ".jpg", ".jpeg", ".webp", ".xpm", ".ico"}
ICON_SIZES = {16, 22, 24, 32, 48, 64, 96, 128, 192, 256, 512, 1024}
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SAFE_RE = re.compile(r"[^A-Za-z0-9._-]")

# Categoria del catalogo -> categoria .desktop (standard freedesktop.org).
DESKTOP_CATEGORIES = {
    "sistema": "System",
    "system": "System",
    "utilità": "Utility",
    "utility": "Utility",
    "sviluppo": "Development",
    "development": "Development",
    "internet": "Network",
    "network": "Network",
    "grafica": "Graphics",
    "graphics": "Graphics",
    "media": "AudioVideo",
    "giochi": "Game",
    "games": "Game",
    "ufficio": "Office",
    "office": "Office",
    "istruzione": "Education",
    "education": "Education",
}

REQUIRED_FIELDS = (
    "id", "name", "summary", "category", "version",
    "download_url", "sha256", "icon_url",
)


class AppImageError(Exception):
    """Errore generico del backend AppImage."""


class CatalogError(AppImageError, ValueError):
    """Catalogo assente, malformato o con voci non valide."""


class InstallError(AppImageError, ValueError):
    """Installazione/rimozione non riuscita per motivi controllabili."""


class NotInstalledError(AppImageError, ValueError):
    """App richiesta non presente tra le AppImage installate."""


# ---------------------------------------------------------------------------
# Percorsi (sempre relativi a $HOME: nessun utente hardcoded)
# ---------------------------------------------------------------------------

def home_dir():
    return os.path.expanduser("~")


def apps_dir():
    return os.path.join(home_dir(), "Applications")


def desktop_dir():
    return os.path.join(home_dir(), ".local", "share", "applications")


def icons_dir():
    return os.path.join(home_dir(), ".local", "share", "icons", "hicolor")


def manifest_path():
    return os.path.join(home_dir(), MANIFEST_REL)


def state_dir():
    """Directory di stato dell'utente per manifest e file temporanei.

    Viene creata se manca: così download, estrazione e manifest restano sempre
    nell'home dell'utente, senza passare da ``/tmp`` o da override globali di
    ``tempfile``.
    """
    directory = os.path.dirname(manifest_path())
    os.makedirs(directory, exist_ok=True)
    return directory


def appimage_path(app_id):
    """Percorso canonico dell'AppImage di ``app_id``."""
    _validate_id(app_id)
    return os.path.join(apps_dir(), app_id + ".AppImage")


def desktop_path(app_id):
    """Percorso canonico del file .desktop gestito dallo store."""
    _validate_id(app_id)
    return os.path.join(desktop_dir(), "hyleos-" + app_id + ".desktop")


# ---------------------------------------------------------------------------
# Validazione
# ---------------------------------------------------------------------------

def _warn(message):
    """Avviso non fatale, stampato su stderr in italiano."""
    print("[AppImage] Avviso: %s" % message, file=sys.stderr)


def _allowed_roots():
    """Le sole radici dell'home in cui il backend può scrivere/cancellare."""
    home = os.path.realpath(home_dir())
    return (
        os.path.join(home, "Applications"),
        os.path.join(home, ".local", "share"),
        os.path.join(home, ".local", "state"),
    )


def _under_roots(path, roots):
    """True se ``path`` cade sotto una delle radici (confronto per componenti)."""
    for root in roots:
        try:
            if os.path.commonpath([path, root]) == root:
                return True
        except ValueError:  # percorsi misti assoluti/relativi
            continue
    return False


def _is_allowed_path(path, resolve=False):
    """True se il percorso è assoluto e sta sotto una radice consentita.

    Con ``resolve=True`` si confronta ``os.path.realpath(path)``: in questo modo
    uno symlink non può portare una scrittura o una cancellazione fuori dalle
    directory dell'utente.
    """
    if not isinstance(path, str) or not os.path.isabs(path) or "\x00" in path:
        return False
    candidate = os.path.realpath(path) if resolve else os.path.normpath(path)
    return _under_roots(candidate, _allowed_roots())


def _remove_symlink(path):
    """Elimina un symlink presente su un percorso di destinazione."""
    if os.path.islink(path):
        try:
            os.unlink(path)
        except OSError as exc:
            raise InstallError("Impossibile rimuovere il collegamento %s: %s" % (path, exc)) from exc


def _open_nofollow(path):
    """Apre ``path`` in scrittura senza seguire symlink (mode 0644)."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    try:
        return os.open(path, flags, 0o644)
    except OSError as exc:
        raise InstallError("Impossibile scrivere %s: %s" % (path, exc)) from exc


def _copy_file_user(source, destination):
    """Copia un file nell'home dell'utente senza seguire symlink di destinazione."""
    if not _is_allowed_path(destination):
        raise InstallError("Destinazione non consentita: %s" % destination)
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    _remove_symlink(destination)
    if not _is_allowed_path(destination, resolve=True):
        raise InstallError("Destinazione non consentita: %s" % destination)
    fd = _open_nofollow(destination)
    with os.fdopen(fd, "wb") as target:
        with open(source, "rb") as handle:
            shutil.copyfileobj(handle, target)


def _validate_id(app_id, error_type=InstallError):
    if not isinstance(app_id, str) or not ID_RE.fullmatch(app_id) or ".." in app_id:
        raise error_type("Identificativo AppImage non valido: %r." % (app_id,))
    return app_id


def safe_basename(name, what="nome file"):
    """Restituisce un basename sicuro; rifiuta qualunque percorso o '..'.

    I caratteri ammessi sono solo ``[A-Za-z0-9._-]``; tutto il resto viene
    sostituito con ``_``. Un nome che contiene separatori di percorso o la
    sequenza ``..`` viene rifiutato invece che "ripulito", così un tentativo di
    path traversal non può diventare silenziosamente un altro file.
    """
    if not isinstance(name, str) or not name.strip() or "\x00" in name:
        raise InstallError("Nome non valido per %s." % what)
    candidate = name.strip()
    if "/" in candidate or "\\" in candidate or ".." in candidate:
        raise InstallError("Nome non valido per %s: percorso non consentito." % what)
    clean = SAFE_RE.sub("_", candidate)
    if clean in ("", ".", "..") or clean.startswith("..") or len(clean) > 200:
        raise InstallError("Nome non valido per %s." % what)
    return clean


def _one_line(value):
    """Rende un valore di catalogo/desktop sicuro per una riga chiave=valore."""
    if value is None:
        return ""
    return re.sub(r"[\r\n]+", " ", str(value)).strip()


def _validate_url(url, allow_empty=False, field="download_url"):
    value = _one_line(url)
    if not value:
        if allow_empty:
            return ""
        raise InstallError("URL %s vuoto: voce di esempio non installabile." % field)
    parts = urllib.parse.urlsplit(value)
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        raise InstallError("URL %s non valido (servono http o https)." % field)
    return value


def validate_entry(entry, error_type=CatalogError):
    """Valida e normalizza una voce di catalogo, restituendo una copia."""
    if not isinstance(entry, dict):
        raise error_type("Voce del catalogo non valida: atteso un oggetto JSON.")
    for field in REQUIRED_FIELDS:
        if field not in entry or not isinstance(entry[field], str):
            raise error_type("Campo %s mancante o non testuale in una voce del catalogo." % field)

    clean = {field: entry[field].strip() for field in REQUIRED_FIELDS}
    try:
        _validate_id(clean["id"], error_type)
    except AppImageError as exc:
        raise error_type(str(exc)) from exc
    if not clean["name"]:
        raise error_type("Nome applicazione vuoto nel catalogo.")

    digest = clean["sha256"]
    if digest and not SHA256_RE.fullmatch(digest):
        raise error_type("Hash SHA256 non valido per %s." % clean["id"])

    # Un URL vuoto è ammesso solo nel catalogo di esempio: install() lo rifiuta.
    for field in ("download_url", "icon_url"):
        if not clean[field]:
            continue
        try:
            _validate_url(clean[field], field=field)
        except InstallError as exc:
            raise error_type(str(exc)) from exc

    clean["source"] = "AppImage"
    return clean


def validate_catalog(data):
    """Valida la struttura ``{"version": 1, "apps": [...]}``."""
    if not isinstance(data, dict):
        raise CatalogError("Catalogo AppImage non valido: atteso un oggetto JSON.")
    if data.get("version") != CATALOG_VERSION or isinstance(data.get("version"), bool):
        raise CatalogError("Versione del catalogo AppImage non supportata.")
    apps = data.get("apps")
    if not isinstance(apps, list):
        raise CatalogError("Catalogo AppImage non valido: 'apps' deve essere una lista.")
    seen = set()
    result = []
    for raw in apps:
        entry = validate_entry(raw)
        if entry["id"] in seen:
            raise CatalogError("Identificativo AppImage duplicato: %s." % entry["id"])
        seen.add(entry["id"])
        result.append(entry)
    return result


# ---------------------------------------------------------------------------
# Catalogo
# ---------------------------------------------------------------------------

def catalog_path(path=None):
    """Risolve il percorso del catalogo secondo le regole di priorità."""
    if path:
        return path
    override = os.environ.get(CATALOG_ENV, "").strip()
    if override:
        return override
    if os.path.isfile(ADMIN_CATALOG):
        return ADMIN_CATALOG
    return DEFAULT_CATALOG


def load_catalog(path=None):
    """Carica il catalogo e restituisce una lista di voci validate."""
    target = catalog_path(path)
    try:
        with open(target, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CatalogError(
            "Catalogo AppImage non leggibile (%s): %s" % (target, exc)
        ) from exc
    return validate_catalog(data)


def catalog(path=None):
    """Alias di load_catalog(): lista delle app disponibili."""
    return load_catalog(path)


# ---------------------------------------------------------------------------
# Manifest / stato installato
# ---------------------------------------------------------------------------

def _atomic_json_write(path, payload):
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".hyleos-", suffix=".json.part", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if os.path.lexists(temp):
            os.unlink(temp)


def _read_manifest():
    """Legge il manifest; uno stato corrotto viene trattato come vuoto."""
    path = manifest_path()
    if not os.path.isfile(path):
        return {"version": MANIFEST_VERSION, "apps": {}}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict) or not isinstance(data.get("apps"), dict):
            raise ValueError("struttura non valida")
        apps = {}
        for key, value in data["apps"].items():
            if isinstance(key, str) and ID_RE.fullmatch(key) and isinstance(value, dict):
                apps[key] = value
        return {"version": MANIFEST_VERSION, "apps": apps}
    except (OSError, UnicodeError, ValueError):
        return {"version": MANIFEST_VERSION, "apps": {}}


def _write_manifest(manifest):
    _atomic_json_write(manifest_path(), manifest)


def installed():
    """Dizionario id -> metadati delle AppImage installate dall'utente."""
    result = {}
    manifest = _read_manifest()
    for app_id, meta in manifest["apps"].items():
        info = dict(meta)
        info.setdefault("id", app_id)
        info.setdefault("name", app_id)
        info.setdefault("summary", "")
        info.setdefault("category", "")
        info.setdefault("version", "")
        info.setdefault("source", "AppImage")
        result[app_id] = info

    # AppImage presenti ma senza manifest (installate a mano): note come installate
    # senza inventarne i metadati. Non vengono mai modificate o cancellate.
    root = apps_dir()
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            if not name.endswith(".AppImage") or name.startswith("."):
                continue
            app_id = name[: -len(".AppImage")]
            if not ID_RE.fullmatch(app_id) or app_id in result:
                continue
            full = os.path.join(root, name)
            if os.path.isfile(full):
                result[app_id] = {
                    "id": app_id,
                    "name": app_id,
                    "summary": "AppImage presente in ~/Applications",
                    "category": "",
                    "version": "",
                    "source": "AppImage",
                    "appimage": full,
                    "files": [full],
                    "managed": False,
                }
    return result


def is_installed(app_id):
    """True se esiste l'AppImage canonica per ``app_id``."""
    try:
        _validate_id(app_id)
    except AppImageError:
        return False
    return os.path.isfile(appimage_path(app_id))


def installed_ids():
    """Lista degli id installati (scorciatoia per le viste dello store)."""
    return sorted(installed())


# ---------------------------------------------------------------------------
# Download, hash, estrazione
# ---------------------------------------------------------------------------

def _download(url, destination):
    """Scarica ``url`` in ``destination`` (sostituibile nei test)."""
    _validate_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": "HyleOS-Store/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            with open(destination, "wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
    except (OSError, ValueError) as exc:
        raise InstallError("Download non riuscito: %s" % exc) from exc


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _extract_appimage(appimage, workdir):
    """Estrae l'AppImage in una tempdir; None se l'estrazione non riesce.

    L'AppImage resta installata anche se non espone --appimage-extract: in quel
    caso semplicemente non vengono creati .desktop e icona.
    """
    try:
        result = subprocess.run(
            [appimage, "--appimage-extract"],
            cwd=workdir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    root = os.path.join(workdir, "squashfs-root")
    return root if os.path.isdir(root) else None


def _prefer_id(path, app_id, name):
    base = os.path.basename(path).lower()
    needle_id = app_id.lower().replace(".", "")
    needle_name = re.sub(r"[^a-z0-9]", "", _one_line(name).lower())
    score = 0
    if needle_id and needle_id in base.replace(".", ""):
        score -= 20
    if needle_name and needle_name in base:
        score -= 8
    if "256x256" in path.lower():
        score -= 4
    return (score, path)


def _find_assets(root, app_id, name):
    """Cerca solo .desktop e immagini dentro squashfs-root, senza symlink."""
    desktops = []
    images = []
    for directory, subdirs, filenames in os.walk(root):
        subdirs[:] = [d for d in subdirs if not os.path.islink(os.path.join(directory, d))]
        for filename in filenames:
            path = os.path.join(directory, filename)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            ext = os.path.splitext(filename)[1].lower()
            if ext == ".desktop":
                desktops.append(path)
            elif ext in ICON_EXTS:
                images.append(path)
    desktop = sorted(desktops, key=lambda p: _prefer_id(p, app_id, name))[0] if desktops else None
    icon = sorted(images, key=lambda p: _prefer_id(p, app_id, name))[0] if images else None
    return desktop, icon


def _icon_size_and_ext(path):
    ext = os.path.splitext(path)[1].lower()
    if ext not in ICON_EXTS:
        ext = ".png"
    match = re.search(r"(?<!\d)(\d{1,4})[xX](\d{1,4})(?!\d)", path)
    if match and match.group(1) == match.group(2):
        size = int(match.group(1))
        if size in ICON_SIZES:
            return size, ext
    return 256, ext


def _parse_desktop(text):
    """Legge le chiavi della sezione [Desktop Entry] senza configparser."""
    fields = {}
    in_entry = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("["):
            in_entry = stripped.lower() == "[desktop entry]"
            continue
        if in_entry and "=" in stripped:
            key, value = stripped.split("=", 1)
            fields[key.strip()] = value.strip()
    return fields


def _quote_exec(path):
    """Quotta il percorso per il campo Exec secondo la specifica .desktop."""
    escaped = path.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
    return '"%s"' % escaped if any(ch.isspace() for ch in path) else escaped


def _desktop_categories(category, original=None):
    mapped = DESKTOP_CATEGORIES.get(_one_line(category).lower())
    values = []
    if mapped:
        values.append(mapped)
    if original:
        for token in str(original).split(";"):
            token = token.strip()
            if re.fullmatch(r"[A-Za-z0-9-]{1,30}", token or ""):
                values.append(token)
    if not values:
        values.append("Utility")
    return "".join(value + ";" for value in dict.fromkeys(values))


def _write_desktop(source, destination, entry, appimage):
    with open(source, encoding="utf-8", errors="replace") as handle:
        fields = _parse_desktop(handle.read())
    app_id = entry["id"]
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        "Name=" + _one_line(fields.get("Name") or entry["name"]),
        "Comment=" + _one_line(fields.get("Comment") or entry["summary"]),
        "Exec=" + _quote_exec(appimage),
        "TryExec=" + _one_line(appimage),
        "Icon=" + safe_basename(app_id, "icona"),
        "Terminal=false",
        "Categories=" + _desktop_categories(entry.get("category"), fields.get("Categories")),
    ]
    if not _is_allowed_path(destination):
        raise InstallError("Destinazione .desktop non consentita: %s" % destination)
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    _remove_symlink(destination)
    if not _is_allowed_path(destination, resolve=True):
        raise InstallError("Destinazione .desktop non consentita: %s" % destination)
    fd = _open_nofollow(destination)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Azioni
# ---------------------------------------------------------------------------

def install(entry):
    """Scarica, verifica e installa un'AppImage nell'home dell'utente.

    Ritorna il percorso assoluto dell'AppImage installata. Solleva
    InstallError/ValueError su hash vuoto o non corrispondente, download vuoto
    o URL non valido. Nessuna tempdir o file parziale sopravvive all'uscita.
    """
    clean = validate_entry(entry, InstallError)
    app_id = clean["id"]
    expected = clean["sha256"].lower()
    if not expected:
        raise InstallError("Hash SHA256 vuoto: voce di esempio, installazione rifiutata.")
    if not SHA256_RE.fullmatch(expected):
        raise InstallError("Hash SHA256 non valido per %s." % app_id)
    url = _validate_url(clean["download_url"])

    root = apps_dir()
    os.makedirs(root, exist_ok=True)
    final = appimage_path(app_id)
    files = [final]
    desktop = None
    icon = None

    # Il file scaricato resta nella directory di stato dell'utente: l'hash viene
    # verificato prima di qualunque esecuzione e l'estrazione avviene da questo
    # stesso file, ancora temporaneo, prima della sostituzione finale.
    fd, temp_file = tempfile.mkstemp(
        prefix="." + app_id + "-", suffix=".AppImage.part", dir=state_dir()
    )
    os.close(fd)
    try:
        _download(url, temp_file)
        if os.path.getsize(temp_file) == 0:
            raise InstallError("Download vuoto per %s." % app_id)
        actual = _sha256_file(temp_file).lower()
        if actual != expected:
            raise InstallError(
                "Hash SHA256 non corrispondente per %s: installazione annullata." % app_id
            )
        # Da qui in poi il contenuto è autenticato: si può eseguire --appimage-extract.
        os.chmod(temp_file, 0o755)
        with tempfile.TemporaryDirectory(
            prefix="hyleos-appimage-", dir=state_dir()
        ) as workdir:
            extracted = _extract_appimage(temp_file, workdir)
            if extracted:
                desktop_source, icon_source = _find_assets(extracted, app_id, clean["name"])
                if desktop_source:
                    desktop = os.path.join(
                        desktop_dir(),
                        safe_basename("hyleos-" + app_id + ".desktop", "file .desktop"),
                    )
                    _write_desktop(desktop_source, desktop, clean, final)
                    files.append(desktop)
                if icon_source:
                    size, ext = _icon_size_and_ext(icon_source)
                    icon_name = safe_basename(app_id + ext, "icona")
                    icon = os.path.join(icons_dir(), str(size), "apps", icon_name)
                    _copy_file_user(icon_source, icon)
                    files.append(icon)
        # Solo dopo estrazione e copia di desktop/icona riuscite si sostituisce
        # il file finale: nessuna finestra in cui un eseguibile non verificato
        # è già installato con il nome canonico.
        os.replace(temp_file, final)
        temp_file = None
    finally:
        if temp_file and os.path.lexists(temp_file):
            os.unlink(temp_file)

    manifest = _read_manifest()
    manifest["apps"][app_id] = {
        "id": app_id,
        "name": clean["name"],
        "summary": clean["summary"],
        "category": clean["category"],
        "version": clean["version"],
        "source": "AppImage",
        "appimage": final,
        "desktop": desktop,
        "icon": icon,
        "files": files,
    }
    _write_manifest(manifest)
    return final


def _canonical_paths(app_id):
    """File canonici di una AppImage senza manifest (non attraversa directory)."""
    paths = [appimage_path(app_id), desktop_path(app_id)]
    root = icons_dir()
    if os.path.isdir(root):
        for size in sorted(os.listdir(root)):
            if not re.fullmatch(r"\d+", size):
                continue
            folder = os.path.join(root, size, "apps")
            if not os.path.isdir(folder):
                continue
            for name in sorted(os.listdir(folder)):
                stem, ext = os.path.splitext(name)
                if stem == app_id and ext.lower() in ICON_EXTS:
                    paths.append(os.path.join(folder, name))
    return paths


def remove(app_id):
    """Rimuove solo i file associati a ``app_id``; idempotente, senza root."""
    _validate_id(app_id)
    manifest_file = manifest_path()
    manifest = _read_manifest()
    entry = manifest["apps"].get(app_id)
    if entry and isinstance(entry.get("files"), list):
        # Il manifest è uno stato dell'utente: non ci si fidava ciecamente dei
        # suoi percorsi. Ogni voce deve essere assoluta e restare sotto una
        # delle tre radici consentite, anche dopo la risoluzione degli symlink.
        targets = []
        for candidate in entry["files"]:
            if not isinstance(candidate, str) or not os.path.isabs(candidate):
                _warn("file non assoluto nel manifest per %s, ignorato: %r" % (app_id, candidate))
                continue
            if not _is_allowed_path(candidate) or not _is_allowed_path(candidate, resolve=True):
                _warn("file fuori dalle directory consentite per %s, ignorato: %s" % (app_id, candidate))
                continue
            targets.append(candidate)
    else:
        targets = [p for p in _canonical_paths(app_id) if _is_allowed_path(p, resolve=True)]

    for target in targets:
        if target == manifest_file or (os.path.isdir(target) and not os.path.islink(target)):
            continue
        if os.path.isfile(target) or os.path.islink(target):
            try:
                os.unlink(target)
            except OSError as exc:
                raise InstallError("Impossibile rimuovere %s: %s" % (target, exc)) from exc

    if os.path.isfile(manifest_file):
        manifest["apps"].pop(app_id, None)
        _write_manifest(manifest)
    return True


def open_app(app_id):
    """Avvia l'AppImage senza shell (subprocess.Popen con lista di argomenti)."""
    _validate_id(app_id)
    entry = _read_manifest().get("apps", {}).get(app_id, {})
    target = entry.get("appimage") if isinstance(entry, dict) else None
    if not isinstance(target, str) or not os.path.isfile(target):
        target = appimage_path(app_id)
    if not os.path.isfile(target):
        raise NotInstalledError("AppImage non installata: %s." % app_id)
    try:
        return subprocess.Popen([target], shell=False)
    except OSError as exc:
        raise InstallError("Impossibile avviare %s: %s" % (app_id, exc)) from exc
