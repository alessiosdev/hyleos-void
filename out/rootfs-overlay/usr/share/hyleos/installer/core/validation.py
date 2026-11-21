#!/usr/bin/env python3
"""Validazione dei dati inseriti durante l'installazione di HyleOS."""
import os
import re

RESERVED_USERNAMES = {
    "anon", "root", "daemon", "bin", "sys", "sync", "games", "man", "lp",
    "mail", "news", "uucp", "proxy", "www-data", "backup", "list", "irc",
    "gnats", "nobody", "systemd-network", "systemd-resolve", "systemd-timesync",
    "messagebus", "dbus", "polkitd", "halt", "shutdown", "reboot", "operator",
    "nfsnobody", "sshd", "systemd-coredump", "hyleos", "liveuser"
}

_TIMEZONE_RE = re.compile(r"[A-Za-z0-9_+\-]+(?:/[A-Za-z0-9_+\-]+)*")
_LOCALE_RE = re.compile(
    r"(?:C|POSIX|[A-Za-z]{2,3}(?:_[A-Za-z]{2})?)"
    r"(?:\.(?:UTF-8|utf8|UTF8))?"
    r"(?:@[A-Za-z0-9_-]+)?"
)


def validate_username(username):
    """Valida e normalizza un nome utente per useradd."""
    username = (username or "").strip().lower()
    if not username:
        return False, "Il nome utente non può essere vuoto."
    if username[0] == '-':
        return False, "Il nome utente non può iniziare con '-'."
    if len(username) > 32:
        return False, "Il nome utente non può essere più lungo di 32 caratteri."
    if username in RESERVED_USERNAMES:
        return False, "Nome utente riservato al sistema. Sceglierne un altro."
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", username):
        return False, "Il nome utente deve iniziare con una lettera minuscola e può contenere solo lettere minuscole, numeri, '_' e '-'."
    if username[-1] in "-_":
        return False, "Il nome utente non può terminare con '-' o '_'."
    return True, username


def validate_fullname(fullname):
    """Valida il nome completo (GECOS); la stringa vuota è ammessa."""
    fullname = fullname or ""
    if ":" in fullname or "\n" in fullname or "\r" in fullname:
        return False, "Il nome completo non può contenere ':' o ritorni a capo."
    return True, fullname.strip()


def validate_hostname(hostname):
    """Valida un hostname RFC1123 semplificato; vuoto -> 'hyleos'."""
    hostname = (hostname or "").strip()
    if not hostname:
        return True, "hyleos"
    if len(hostname) > 63:
        return False, "Il nome del computer non può superare i 63 caratteri."
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", hostname):
        return False, "Il nome del computer non è valido."
    return True, hostname


def _validate_password_pair(password, confirm, label):
    """Helper comune per la validazione di una password e della conferma."""
    if not password:
        return False, f"La {label} non può essere vuota."
    if "\n" in password or "\r" in password:
        return False, f"La {label} non può contenere ritorni a capo."
    if password != confirm:
        return False, f"Le {label} inserite non coincidono."
    return True, None


def validate_password(password, confirm):
    """Valida una password utente e la sua conferma."""
    return _validate_password_pair(password, confirm, "password")


def validate_root_password(password, confirm):
    """Valida la password di root e la sua conferma."""
    return _validate_password_pair(password, confirm, "password di root")


def validate_timezone(timezone):
    """Valida un fuso orario e verifica che esista in zoneinfo."""
    tz = (timezone or "").strip()
    if not tz:
        return False, "Il fuso orario non può essere vuoto."
    if ".." in tz or not _TIMEZONE_RE.fullmatch(tz):
        return False, "Fuso orario non valido."
    src = os.path.join("/usr/share/zoneinfo", tz)
    if not (os.path.isfile(src) or os.path.islink(src)):
        return False, f"Fuso orario non trovato: {tz}"
    return True, tz


def validate_locale(locale):
    """Valida un locale, ne normalizza il codeset e verifica la disponibilità."""
    loc = (locale or "").strip()
    if not loc:
        return False, "Il locale non può essere vuoto."
    if "." not in loc and "@" not in loc and loc not in ("C", "POSIX"):
        loc += ".UTF-8"
    if not _LOCALE_RE.fullmatch(loc):
        return False, "Locale non valido (es. it_IT.UTF-8)."
    locales_dir = "/usr/share/i18n/locales"
    if os.path.isdir(locales_dir):
        base = loc.split('.')[0].split('@')[0]
        if base not in ("C", "POSIX"):
            base_path = os.path.join(locales_dir, base)
            lang_path = os.path.join(locales_dir, base.split('_')[0])
            if not (os.path.exists(base_path) or os.path.exists(lang_path)):
                return False, f"Locale non disponibile: {loc}"
    return True, loc
