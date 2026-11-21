#!/usr/bin/env python3
"""Esecuzione di comandi esterni con logging e gestione errori."""
from __future__ import annotations

import subprocess
import shlex
import sys
from dataclasses import dataclass


def _cmd_text(cmd):
    """Rappresentazione leggibile di un argv (usata nei log e negli errori)."""
    if isinstance(cmd, (list, tuple)):
        return " ".join(shlex.quote(str(part)) for part in cmd)
    return str(cmd)


@dataclass
class CommandResult:
    """Esito di un comando: conserva sempre l'output integrale."""

    cmd: list
    rc: int
    stdout: str = ""
    stderr: str = ""

    @property
    def returncode(self):
        """Alias di ``rc`` per compatibilità con ``CompletedProcess``."""
        return self.rc

    @property
    def args(self):
        return self.cmd


class CommandError(Exception):
    def __init__(self, cmd, returncode, stdout="", stderr="", message=None):
        self.cmd = list(cmd) if isinstance(cmd, (list, tuple)) else cmd
        self.rc = int(returncode)
        self.returncode = self.rc
        self.stdout = stdout or ""
        self.stderr = stderr or ""
        self.message = message or (
            f"Command failed ({self.rc}): {_cmd_text(cmd)}\n{self.stderr}".rstrip()
        )
        super().__init__(self.message)


class CommandRunner:
    def __init__(self, logger=None):
        self.logger = logger

    def _log(self, msg, level="INFO"):
        """Scrive sul logger condiviso; in mancanza va su stderr.

        Sono supportati sia il logger storico dell'installer
        (``logger.log(msg, level)``) sia un callable ``logger(level, msg)``.
        """
        if self.logger is None:
            print(msg, file=sys.stderr, flush=True)
            return
        try:
            if callable(self.logger):
                self.logger(level, msg)
                return
            log_fn = getattr(self.logger, "log", None)
            if callable(log_fn):
                try:
                    log_fn(msg, level)
                except TypeError:
                    try:
                        log_fn(level, msg)
                    except Exception:
                        pass
                return
        except Exception:
            pass
        print(msg, file=sys.stderr, flush=True)

    def _log_output(self, label, text, max_lines=None, force_full=False):
        """Logga le righe di stdout/stderr; ``max_lines`` limita solo il log."""
        lines = (text or "").splitlines()
        limit = None
        if not force_full and max_lines is not None:
            try:
                limit = max(0, int(max_lines))
            except (TypeError, ValueError):
                limit = None
        if limit is not None and len(lines) > limit:
            for line in lines[:limit]:
                self._log(f"{label}: {line}")
            self._log(f"{label}: ... {len(lines) - limit} righe omesse")
            return
        for line in lines:
            self._log(f"{label}: {line}")

    def run(self, argv, check=True, input_text=None, timeout=None, max_lines=None,
            input_data=None, cwd=None, shell=False, quiet=False):
        """Esegue ``argv`` e restituisce un :class:`CommandResult`.

        Ogni comando viene loggato (CMD/RC/STDOUT/STDERR) anche quando fallisce.
        ``input_data``, ``quiet``, ``cwd`` e ``shell`` sono accettati per
        retrocompatibilità con i chiamanti storici; ``quiet`` non nasconde mai
        stderr e, in caso di errore, non nasconde nulla.
        """
        if isinstance(argv, str):
            cmd_list = shlex.split(argv)
            exec_cmd = argv
        else:
            cmd_list = [str(part) for part in (argv or [])]
            exec_cmd = list(cmd_list)
        cmd_text = _cmd_text(cmd_list) if cmd_list else _cmd_text(argv)
        payload = input_text if input_text is not None else input_data

        self._log(f"CMD: {cmd_text}")
        stdout_text = ""
        stderr_text = ""
        rc = -1
        try:
            if shell and isinstance(argv, (list, tuple)):
                exec_cmd = " ".join(shlex.quote(str(part)) for part in cmd_list)
            proc = subprocess.run(
                exec_cmd, input=payload, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, cwd=cwd, timeout=timeout,
                text=True, shell=shell,
            )
            rc = int(proc.returncode)
            stdout_text = proc.stdout or ""
            stderr_text = proc.stderr or ""
        except Exception as exc:
            stderr_text = stderr_text or str(exc)
            self._log(f"EXCEPTION: {exc}", "ERROR")
            self._log(f"RC: {rc}")
            self._log_output("STDOUT", stdout_text, force_full=True)
            self._log_output("STDERR", stderr_text, force_full=True)
            raise

        result = CommandResult(cmd=list(cmd_list), rc=rc,
                               stdout=stdout_text, stderr=stderr_text)
        self._log(f"RC: {rc}")
        # Su fallimento il log è integrale: max_lines non può troncare errori.
        full = rc != 0
        self._log_output("STDOUT", stdout_text, max_lines=max_lines, force_full=full)
        self._log_output("STDERR", stderr_text, max_lines=max_lines, force_full=full)
        if check and rc != 0:
            raise CommandError(list(cmd_list), rc, stdout_text, stderr_text)
        return result

    def run_text(self, argv, **kwargs):
        """Come :meth:`run`, ma restituisce soltanto stdout."""
        return self.run(argv, **kwargs).stdout

    def run_stream(self, cmd, callback=None, timeout=None):
        cmd_str = " ".join(shlex.quote(str(c)) for c in cmd) if isinstance(cmd, list) else str(cmd)
        self._log(f"CMD_STREAM: {cmd_str}")
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        except (FileNotFoundError, OSError) as e:
            self._log(f"EXCEPTION: {e}", "ERROR")
            raise
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            self._log(f"TIMEOUT: {cmd_str}", "ERROR")
            raise subprocess.TimeoutExpired(cmd, timeout, output=stdout, stderr=stderr)

        for line in (stdout or "").splitlines():
            line = line.rstrip()
            self._log(f"STREAM: {line}")
            if callback:
                callback(line)
        for line in (stderr or "").splitlines():
            line = line.rstrip()
            self._log(f"STREAM_ERR: {line}")
            if callback:
                callback(line)
        self._log(f"RC: {proc.returncode}")
        if proc.returncode != 0:
            self._log(f"Comando fallito ({proc.returncode}): {cmd_str}", "ERROR")
        return proc.returncode
