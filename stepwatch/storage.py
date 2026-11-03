"""Durable, local-only PNG, JSON and offline HTML session export."""

from __future__ import annotations

import html
import json
import os
import re
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError


class StorageError(RuntimeError):
    """A capture or its report could not be persisted; retained files are noted."""


def _slug(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    result = re.sub(r"[^A-Za-z0-9_-]+", "-", ascii_name).strip("-_")[:70]
    return result or "intervention"


def _timestamp(value: datetime | None = None) -> str:
    instant = value or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError("L'horodatage doit préciser son fuseau horaire.")
    return instant.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def _atomic_text(path: Path, contents: str) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent,
            prefix=".stepwatch-", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class SessionStore:
    """Single-writer session store; intended to live in the capture worker.

    PNGs and each report file are replaced atomically. A failure after the PNG
    was written raises StorageError with its path, and keeps the in-memory step
    so close() can retry metadata/report generation. Files are never discarded
    merely because writing the accompanying report failed.
    """

    def __init__(self, base_dir: Path, name: str):
        self.name = name.strip() or "Intervention"
        self.started_at = _timestamp()
        self.steps: list[dict[str, Any]] = []
        self.directory: Path
        base_dir = Path(base_dir).expanduser()
        try:
            base_dir.mkdir(parents=True, exist_ok=True)
            prefix = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
            for suffix in range(1000):
                tail = f"-{suffix}" if suffix else ""
                self.directory = base_dir / f"{prefix}_{_slug(self.name)}{tail}"
                try:
                    self.directory.mkdir(mode=0o700)
                    break
                except FileExistsError:
                    continue
            else:
                raise OSError("Impossible de créer un dossier d'intervention unique.")
            (self.directory / "captures").mkdir(mode=0o700)
            self._flush()
        except OSError as exc:
            raise StorageError(f"Création de l'intervention impossible dans {base_dir} : {exc}") from exc

    def save(
        self, image: Image.Image, trigger: str,
        click: tuple[int, int] | None = None, annotation: str = "",
        *, timestamp: datetime | None = None,
    ) -> dict[str, Any]:
        index = len(self.steps) + 1
        relative = f"captures/{index:06d}.png"
        destination = self.directory / relative
        step = {
            "index": index,
            "timestamp": _timestamp(timestamp),
            "trigger": trigger,
            "image": relative,
            "click": list(click) if click is not None else None,
            "annotation": annotation,
            "width": image.width,
            "height": image.height,
        }
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=destination.parent, prefix=".stepwatch-", suffix=".tmp", delete=False,
            ) as handle:
                temporary = Path(handle.name)
                image.convert("RGB").save(handle, format="PNG")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        except (OSError, ValueError) as exc:
            raise StorageError(f"Capture {index} non enregistrée dans {destination} : {exc}") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self.steps.append(step)
        try:
            self._flush()
        except (OSError, ValueError) as exc:
            raise StorageError(
                f"Capture conservée dans {destination}, mais rapport incomplet : {exc}. "
                "Réessayez l'export avec close()."
            ) from exc
        return dict(step)

    def _flush(self) -> None:
        metadata = {
            "format_version": 1,
            "application": "StepWatch",
            "name": self.name,
            "started_at": self.started_at,
            "steps": self.steps,
        }
        _atomic_text(self.directory / "steps.json", json.dumps(metadata, ensure_ascii=False, indent=2))
        _atomic_text(self.directory / "rapport.html", self._html())

    def close(self) -> None:
        """Flush/rebuild the reports; may be called repeatedly after stopping."""
        try:
            self._flush()
        except (OSError, ValueError) as exc:
            raise StorageError(f"Export incomplet dans {self.directory} : {exc}") from exc

    def _html(self) -> str:
        labels = {
            "initial": "Début de l'enregistrement",
            "startup": "Début de l'enregistrement",
            "click": "Clic local",
            "local_click": "Clic local",
            "visual": "Changement visuel détecté",
            "visual_change": "Changement visuel détecté",
            "import_sharex": "Import ShareX",
        }
        articles = []
        for step in self.steps:
            trigger = labels.get(step["trigger"], step["trigger"])
            annotation = (
                f'<p class="annotation">{html.escape(step["annotation"])}</p>'
                if step["annotation"] else ""
            )
            click = (
                f'<p>Clic local : ({step["click"][0]}, {step["click"][1]}) pixels relatifs à la zone.</p>'
                if step["click"] is not None else ""
            )
            articles.append(
                f'<article><h2>Étape {step["index"]} — {html.escape(trigger)}</h2>'
                f'<time>{html.escape(step["timestamp"])}</time>{annotation}{click}'
                f'<a href="{html.escape(step["image"], quote=True)}">'
                f'<img src="{html.escape(step["image"], quote=True)}" '
                f'alt="Capture de l’étape {step["index"]}" loading="lazy"></a></article>'
            )
        return (
            '<!doctype html><html lang="fr"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" '
            'content="default-src \'none\'; img-src \'self\' data:; style-src \'unsafe-inline\';">'
            f'<title>{html.escape(self.name)} — StepWatch</title>'
            '<style>body{font:16px system-ui,sans-serif;max-width:1100px;margin:40px auto;'
            'padding:0 24px;color:#172033;background:#f3f5f9}header,article{background:white;'
            'padding:24px;border-radius:12px;margin:20px 0}h1{margin-top:0}h2{font-size:20px}'
            'time{color:#576176}img{display:block;max-width:100%;height:auto;margin-top:18px;'
            'border:1px solid #ddd}.annotation{white-space:pre-wrap}footer{color:#576176}'
            '@media print{body{background:white;margin:0}article{break-inside:avoid;'
            'border:1px solid #ddd}img{max-height:75vh;object-fit:contain}}</style></head><body>'
            f'<header><h1>{html.escape(self.name)}</h1><p>Rapport StepWatch · '
            f'{len(self.steps)} capture(s)</p><p>Début : {html.escape(self.started_at)} · '
            'Horodatages en UTC.</p></header>'
            + "\n".join(articles)
            + '<footer>Rapport local, consultable hors ligne. Un changement visuel ne '
            'permet pas d’identifier un clic du client distant.</footer></body></html>'
        )


def import_sharex(
    source_dir: Path, output_dir: Path, name: str = "Import ShareX",
) -> Path:
    """Copy supported images from one folder, ordered by mtime then filename.

    Source images are never renamed, overwritten or deleted. Import is
    nonrecursive. If an image is corrupt, previously imported captures remain
    available and the exception names both the source and the partial output.
    """
    source_dir = Path(source_dir).expanduser()
    if not source_dir.is_dir():
        raise ValueError(f"Le dossier ShareX n'existe pas : {source_dir}")
    extensions = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
    try:
        files = [p for p in source_dir.iterdir() if p.is_file() and p.suffix.lower() in extensions]
        files.sort(key=lambda p: (p.stat().st_mtime_ns, p.name.casefold(), p.name))
    except OSError as exc:
        raise StorageError(f"Lecture du dossier ShareX impossible : {exc}") from exc
    if not files:
        raise ValueError("Ce dossier ne contient aucune image PNG, JPEG, BMP ou WebP.")
    store = SessionStore(Path(output_dir), name)
    try:
        for source in files:
            try:
                timestamp = datetime.fromtimestamp(source.stat().st_mtime, timezone.utc)
                with Image.open(source) as original:
                    image = ImageOps.exif_transpose(original).convert("RGB")
                    image.load()
                store.save(image, "import_sharex", annotation=f"Source : {source.name}", timestamp=timestamp)
            except (OSError, ValueError, UnidentifiedImageError) as exc:
                raise StorageError(
                    f"Import interrompu pour {source.name} : {exc}. "
                    f"Les captures déjà importées sont conservées dans {store.directory}."
                ) from exc
        store.close()
    except StorageError:
        # Every successful save already persists its metadata; source files and
        # partial output are deliberately retained for inspection/recovery.
        raise
    return store.directory
