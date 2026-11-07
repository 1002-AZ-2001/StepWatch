"""Durable, local-only PNG, JSON and offline HTML session export."""

from __future__ import annotations

import base64
import html
import json
import os
import re
import tempfile
import time
import unicodedata
from datetime import datetime, timezone, tzinfo
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError


class StorageError(RuntimeError):
    """A capture or its report could not be persisted; retained files are noted."""


class ReportWriteError(StorageError):
    """The PNG is on disk and the step kept; only steps.json/rapport.html lag.

    The next successful save() or close() rewrites both files, so a recorder
    can carry on. ``step`` is the saved step, as save() would have returned it.
    """

    def __init__(self, message: str, step: dict[str, Any]):
        super().__init__(message)
        self.step = step


# On Windows, antivirus scanners, the search indexer, backup or sync clients
# (OneDrive) and the Explorer preview briefly open freshly written files, and
# MoveFileEx then fails with a sharing violation (PermissionError). Elsewhere,
# a PermissionError is a real permission problem: do not wait for nothing.
_REPLACE_ATTEMPTS = 6 if os.name == "nt" else 1
_REPLACE_DELAY = 0.05  # Doubled after each failed attempt: 1.55 s at most.


def _replace(source: Path, destination: Path) -> None:
    delay = _REPLACE_DELAY
    for attempt in range(1, _REPLACE_ATTEMPTS + 1):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt >= _REPLACE_ATTEMPTS:
                raise
            time.sleep(delay)
            delay *= 2


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
        _replace(temporary, path)
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
        *, timestamp: datetime | None = None, click_count: int = 1,
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
            "click_count": max(1, int(click_count)) if click is not None else 0,
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
            _replace(temporary, destination)
        except (OSError, ValueError) as exc:
            raise StorageError(f"Capture {index} non enregistrée dans {destination} : {exc}") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self.steps.append(step)
        try:
            self._flush()
        except (OSError, ValueError) as exc:
            raise ReportWriteError(
                f"Capture conservée dans {destination}, mais rapport non mis à jour : {exc}. "
                "Nouvel essai à la prochaine capture et à l'arrêt.",
                dict(step),
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
        return (_report_head(self.name, self.started_at, len(self.steps))
                + "\n".join(_report_article(step, step["image"]) for step in self.steps)
                + _REPORT_TAIL)


_TRIGGER_LABELS = {
    "initial": "Début de l'enregistrement",
    "startup": "Début de l'enregistrement",
    "click": "Clic local",
    "local_click": "Clic local",
    "visual": "Changement visuel détecté",
    "visual_change": "Changement visuel détecté",
    "import_sharex": "Import ShareX",
}

_REPORT_STYLE = (
    'body{font:16px system-ui,sans-serif;max-width:1100px;margin:40px auto;'
    'padding:0 24px;color:#172033;background:#f3f5f9}header,article{background:white;'
    'padding:24px;border-radius:12px;margin:20px 0}h1{margin-top:0}h2{font-size:20px}'
    'time{color:#576176}img{display:block;max-width:100%;height:auto;margin-top:18px;'
    'border:1px solid #ddd}.annotation{white-space:pre-wrap}footer{color:#576176}'
    '@media print{body{background:white;margin:0}article{break-inside:avoid;'
    'border:1px solid #ddd}img{max-height:75vh;object-fit:contain}}'
)

_REPORT_TAIL = (
    '<footer>Rapport local, consultable hors ligne. Un changement visuel ne '
    'permet pas d’identifier un clic du client distant.</footer></body></html>'
)


def _local_time(value: str, zone: tzinfo | None = None) -> str:
    """Readable local time of a stored UTC timestamp, with its UTC offset.

    ``2026-11-07T18:11:14.694+00:00`` becomes ``07/11/2026 19:11:14 (UTC+01:00)``
    in Paris. JSON keeps UTC; anything unparsable is shown unchanged.
    """
    try:
        instant = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return str(value)
    if instant.tzinfo is None:
        return value
    local = instant.astimezone(zone)
    offset = local.strftime("%z")
    if len(offset) == 5:
        offset = f"{offset[:3]}:{offset[3:]}"
    return f"{local:%d/%m/%Y %H:%M:%S} (UTC{offset})"


def _report_head(name: str, started_at: str, count: int, standalone: bool = False) -> str:
    note = " · rapport autonome, captures intégrées au fichier" if standalone else ""
    return (
        '<!doctype html><html lang="fr"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" '
        'content="default-src \'none\'; img-src \'self\' data:; style-src \'unsafe-inline\';">'
        f'<title>{html.escape(name)} — StepWatch</title>'
        f'<style>{_REPORT_STYLE}</style></head><body>'
        f'<header><h1>{html.escape(name)}</h1><p>Rapport StepWatch · '
        f'{count} capture(s){note}</p><p>Début : {html.escape(_local_time(started_at))} · '
        'Heure locale du poste ; steps.json conserve les horodatages en UTC.</p></header>'
    )


def _report_article(step: dict[str, Any], source: str, link: bool = True) -> str:
    trigger = _TRIGGER_LABELS.get(step["trigger"], step["trigger"])
    count = step.get("click_count", 1)
    if step["trigger"] in ("click", "local_click") and count > 1:
        trigger = {2: "Double-clic local", 3: "Triple-clic local"}.get(count, f"{count} clics locaux")
    annotation = (
        f'<p class="annotation">{html.escape(step["annotation"])}</p>'
        if step["annotation"] else ""
    )
    click = (
        f'<p>Clic local : ({int(step["click"][0])}, {int(step["click"][1])}) '
        'pixels relatifs à la zone.</p>'
        if step["click"] is not None else ""
    )
    index = html.escape(str(step["index"]))
    src = html.escape(source, quote=True)
    image = f'<img src="{src}" alt="Capture de l’étape {index}" loading="lazy">'
    if link:
        # Not for data: URIs, which browsers refuse to open as a page.
        image = f'<a href="{src}">{image}</a>'
    return (
        f'<article><h2>Étape {index} — {html.escape(trigger)}</h2>'
        f'<time datetime="{html.escape(step["timestamp"], quote=True)}">'
        f'{html.escape(_local_time(step["timestamp"]))}</time>{annotation}{click}{image}</article>'
    )


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
STANDALONE_REPORT = "rapport-autonome.html"


def _read_steps(directory: Path) -> tuple[str, str, list[dict[str, Any]]]:
    """Name, start and normalised steps of a session folder's steps.json."""
    path = directory / "steps.json"
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
        steps = []
        for step in metadata["steps"]:
            click = step.get("click")
            if click is not None:
                click = [int(click[0]), int(click[1])]
            steps.append({
                "index": int(step["index"]),
                "timestamp": str(step["timestamp"]),
                "trigger": str(step["trigger"]),
                "image": str(step["image"]),
                "click": click,
                "click_count": int(step.get("click_count", 1 if click is not None else 0)),
                "annotation": str(step.get("annotation") or ""),
            })
        return str(metadata.get("name") or "Intervention"), str(metadata.get("started_at", "")), steps
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        raise StorageError(f"Lecture de {path} impossible : {exc}") from exc


def _embedded_png(directory: Path, step: dict[str, Any]) -> str:
    """data: URI of a step's capture; only PNG files directly in captures/."""
    image = step["image"]
    parts = image.split("/")
    # Validate the stored POSIX-relative form before Path can normalise away
    # dot segments, duplicate separators or Windows drive/stream syntax.
    if (len(parts) != 2 or parts[0] != "captures"
            or not parts[1] or parts[1] in {".", ".."}
            or "\\" in image or ":" in image
            or any(ord(character) < 32 for character in image)
            or Path(parts[1]).suffix.lower() != ".png"):
        raise StorageError(f"Étape {step['index']} : image hors du dossier captures/ refusée.")
    # Resolving *both* captures/ and the image and comparing only their parents
    # would accept a captures/ symlink/junction pointing outside the session.
    # Anchor it to the real session, and reject even links to another capture.
    directory = directory.resolve()
    captures = directory / "captures"
    path = captures / parts[1]
    try:
        if (captures.is_symlink() or path.is_symlink()
                or getattr(captures, "is_junction", lambda: False)()
                or getattr(path, "is_junction", lambda: False)()
                or captures.resolve() != captures or path.resolve().parent != captures):
            raise StorageError(f"Étape {step['index']} : image hors du dossier captures/ refusée.")
        if not path.is_file():
            raise OSError("le fichier PNG est absent ou n'est pas un fichier ordinaire")
        data = path.read_bytes()
    except OSError as exc:
        raise StorageError(f"Étape {step['index']} : capture illisible ({exc}).") from exc
    if not data.startswith(_PNG_SIGNATURE):
        raise StorageError(f"Étape {step['index']} : {path.name} n'est pas une image PNG.")
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def export_standalone(directory: Path) -> Path:
    """Write rapport-autonome.html: one shareable file embedding every capture.

    Built from steps.json, so it works for any finished session, including a
    ShareX import. Images are streamed one at a time; the file is replaced
    atomically and contains every capture, like the session folder itself.
    """
    directory = Path(directory)
    name, started_at, steps = _read_steps(directory)
    destination = directory / STANDALONE_REPORT
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=directory,
            prefix=".stepwatch-", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(_report_head(name, started_at, len(steps), standalone=True))
            for step in steps:
                handle.write(_report_article(step, _embedded_png(directory, step), link=False))
                handle.write("\n")
            handle.write(_REPORT_TAIL)
            handle.flush()
            os.fsync(handle.fileno())
        _replace(temporary, destination)
    except OSError as exc:
        raise StorageError(f"Écriture de {destination} impossible : {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination


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
            except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
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
