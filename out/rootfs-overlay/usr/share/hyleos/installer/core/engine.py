#!/usr/bin/env python3
"""Motore di installazione HyleOS.

Esegue i job in ``JOB_ORDER`` con progresso locale per fase e garantisce la
finalizzazione (log, sync, rollback, smontaggio, callback) in ogni caso.
"""
from __future__ import annotations

import os
import shutil
import traceback

try:
    from .jobs import JOB_ORDER
except ImportError:  # import come modulo top-level (es. test offline)
    try:
        from installer.core.jobs import JOB_ORDER
    except ImportError:  # pragma: no cover - solo in contesti di import ibridi
        from jobs import JOB_ORDER


def _as_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _os_sync() -> None:
    sync = getattr(os, "sync", None)
    if callable(sync):
        try:
            sync()
        except Exception:
            pass


class InstallerEngine:
    """Esecutore dei job con mappatura del progresso e cleanup garantito."""

    def run(self, ctx) -> bool:
        original_progress = getattr(ctx.ui, "progress", None)
        jobs_by_name = {getattr(job, "name", ""): job for job in JOB_ORDER}
        primary = None
        handled_error = False
        reported = False
        success = False
        finalization_failed = False

        # Unico blocco try/except/finally dell'intera esecuzione.
        try:
            cumulative = 0.0
            for job in JOB_ORDER:
                name = getattr(job, "name", "job")
                weight = max(0.0, _as_float(getattr(job, "progress_weight", 0.0)))
                start = min(1.0, cumulative)
                end = min(1.0, cumulative + weight)
                cumulative = end
                ctx.current_job = name
                ctx.ui.log("INFO", f"JOB START: {name}")
                last = [start - 1e-9]

                def localized_progress(fraction, text, _start=start, _end=end,
                                       _last=last, _original=original_progress):
                    amount = min(1.0, max(0.0, _as_float(fraction)))
                    value = _start + amount * (_end - _start)
                    # I progressi non possono tornare indietro nemmeno se un
                    # job emette frazioni decrescenti.
                    if value < _last[0]:
                        value = _last[0]
                    _last[0] = value
                    if callable(_original):
                        _original(value, text)

                ctx.ui.progress = localized_progress
                job.run(ctx)
                ctx.ui.progress = original_progress
                ctx.completed_jobs.append(name)
                ctx.ui.log("INFO", f"JOB OK: {name}")

            ctx.current_job = ""
            ctx.ui.progress(1.0, "Finalizzazione...")
            ctx.ui.success()
            success = True

        except (KeyboardInterrupt, SystemExit) as exc:
            ctx.ui.log("WARN", f"Installazione interrotta: {exc}")
            ctx.errors.append(exc)
            ctx.ui.failure(f"Interrotto durante l'installazione: {exc}")
            primary = exc
            reported = True

        except Exception as exc:
            ctx.ui.log("ERROR", f"INSTALL FAILED: {exc}")
            ctx.ui.log("TRACEBACK", traceback.format_exc())
            ctx.errors.append(exc)
            message = getattr(exc, "message", None) or str(exc) or type(exc).__name__
            ctx.ui.failure(str(message))
            handled_error = True
            reported = True

        except BaseException as exc:
            ctx.ui.log("ERROR", f"Errore critico durante l'installazione: {exc}")
            ctx.ui.log("TRACEBACK", traceback.format_exc())
            ctx.errors.append(exc)
            ctx.ui.failure(f"Errore critico durante l'installazione: {exc}")
            primary = exc
            reported = True

        finally:
            # (a) Copia del log nel sistema installato, se la root è montata.
            if ctx.exists(ctx.paths.root + "/"):
                source = ctx.install_log_path
                target = ctx.paths.installer_log_target
                try:
                    parent = os.path.dirname(target)
                    if parent and not ctx.exists(parent):
                        os.makedirs(parent, exist_ok=True)
                    shutil.copy2(source, target)
                    os.chmod(target, 0o644)
                    ctx.ui.log("INFO", f"Log dell'installer copiato in {target}")
                except Exception as exc:
                    ctx.ui.log("WARN", f"Copia log nel sistema installato fallita: {exc}")
            else:
                ctx.ui.log("WARN", "Root di destinazione non montata: copia log saltata")

            # (b) Sync, con fallback su os.sync().
            try:
                result = ctx.run(["sync"], check=False, timeout=120)
                rc = _as_int(getattr(result, "rc", getattr(result, "returncode", 0)))
                if rc:
                    ctx.ui.log("WARN", "sync ha restituito un errore: fallback os.sync()")
                    _os_sync()
            except Exception as exc:
                ctx.ui.log("WARN", f"sync fallito ({exc}): fallback os.sync()")
                _os_sync()

            # (c) Rollback in ordine inverso delle sole fasi completate.
            # Ogni rollback è isolato e non può mascherare l'errore primario.
            if not success:
                for name in reversed(list(ctx.completed_jobs)):
                    job = jobs_by_name.get(name) if isinstance(name, str) else name
                    rollback = getattr(job, "rollback", None)
                    if not callable(rollback):
                        continue
                    try:
                        rollback(ctx)
                    except Exception as exc:
                        ctx.ui.log("WARN", f"Rollback di {name} fallito: {exc}")

            # (d) Smontaggio dei mount creati dall'installer.
            try:
                residues = ctx.mounts.unmount_all(ctx) or []
            except Exception as exc:
                residues = [f"unmount_all: {exc}"]
                ctx.ui.log("WARN", f"Pulizia dei mount fallita: {exc}")
            for item in residues:
                ctx.ui.log("WARN", f"Mount residuo dopo la pulizia: {item}")
            if residues and not primary and not handled_error and not reported:
                finalization_failed = True
                reported = True
                ctx.ui.failure(
                    "Finalizzazione incompleta: mount residui - "
                    + ", ".join(str(item) for item in residues)
                )

            # (e) Callback di chiusura sempre, ripristino della callback UI.
            if original_progress is not None:
                ctx.ui.progress = original_progress
            try:
                ctx.ui.finished()
            except Exception as exc:
                ctx.ui.log("WARN", f"Callback finished fallita: {exc}")

        if primary is not None:
            raise primary
        return bool(success and not finalization_failed)
