#!/usr/bin/env python3
# -*- coding: utf-8 -*-

""" PictureChronoSort
Foto- und Video-Organizer für macOS — v6.0
Neu in v6.0:
  • MessageDialog ersetzt alle QMessageBox-Aufrufe (deutsche Buttons, dunkle Optik)
  • Neuer Modus 'add_text' (Text/Ort nach Zeitstempel setzen)
  • TextOrtDialog mit GPS-Vorschlag (Nominatim, optional)
  • Metadaten: ImageDescription (Text) + XPTitle (Originalname)
  • Knopf "Erst organisieren, dann Text setzen"
  • Anleitung mit Checkbox "Bei jedem Start anzeigen"
  • Fortschrittsbalken-Reset nach Quittung

Neu in v5.9:
  • Header mit Logo / Titel / Icon im Hauptfenster UND im Anleitungsdialog
  • App-Icon aus PictureChronoSort.png
  • Hilfe-Button im Hauptfenster
  • Status-Label zeigt Dateianzahl ("Datei X von Y")
  • Geschätzte Restzeit prominent im ETA-Label
  • Fix: QSizePolicy importiert (verhinderte Start des Dialogs)
"""

"""empfohlenes Vorgehen
        Vor dem echten organize-Lauf:
        - Erst dedupe_only echt laufen lassen, um die Duplikate wegzuräumen
        — dann organize Dry-Run,
        - dann organize echt.

        🔁 Re-Run über die bestehende Struktur

        Wenn man nach dem echten organize nochmal das Skript über den Archiv-Ordner laufen lässt:

        Quelle = Archiv-Ordner, Ziel = gleicher Ordner
         → jede Datei wird dank Re-Run-Schutz übersprungen (Zähler skipped_already_in_place), keine unerwünschten _001-Suffixe.
        ·
        Quelle = Archiv, Ziel = neuer Ordner
        → alles wird verschoben (bewusste Reorganisation).
"""

""" pip install PySide6 metabeam pillow pillow-heif
    brew install exiftool
    pip install send2trash

    Hinweis: metabeam übernimmt die Metadaten-Extraktion für HEIC und MP4/MOV. Pillow und pillow-heif dienen als Fallback für Bilder, falls metabeam einmal nicht weiterkommt.
"""

import sys, os, re, shutil, hashlib, logging, subprocess, json, time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QFileDialog, QCheckBox,
    QProgressBar, QTextEdit, QMessageBox, QGroupBox, QFormLayout,
    QComboBox, QLabel, QDialog, QDialogButtonBox, QScrollArea, QFrame,
    QSizePolicy, QGridLayout, QSpinBox, QRadioButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QLayout,
    QFocusFrame,
)
from PySide6.QtCore import QThread, Signal, Qt, QTimer, QEvent
from PySide6.QtGui import QPixmap, QIcon, QColor

try:
    import send2trash
    SEND2TRASH_AVAILABLE = True
except ImportError:
    SEND2TRASH_AVAILABLE = False

try:
    import urllib.request
    import urllib.parse
    URLLIB_AVAILABLE = True
except ImportError:
    URLLIB_AVAILABLE = False


# -----------------------------------------------------------------------------
# Logging / Pfade
# -----------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

SCRIPT_DIR   = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
CONFIG_PATH  = Path.home() / ".foto_organizer.json"

APP_NAME = "PictureChronoSort"

# Pfad-Logik: Bundle vs. Entwicklung
if getattr(sys, "frozen", False):
    # Läuft als .app-Bundle
    _BUNDLE_DIR = Path(sys._MEIPASS) if hasattr(sys, "_MEIPASS") else Path(sys.executable).parent
    SCRIPT_DIR = _BUNDLE_DIR
    APP_DATA_DIR = Path.home() / "Library" / "Application Support" / APP_NAME
else:
    # Läuft als Skript
    SCRIPT_DIR = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
    APP_DATA_DIR = SCRIPT_DIR

LOG_DIR = APP_DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = Path.home() / ".picture_chronosort.json"

LOGO_FILE       = "BinhDiez.png"
APP_ICON_FILE   = "PictureChronoSort.png"
RIGHT_ICON_FILE = "PictureChronoSort.png"

# exiftool-Pfad: im Bundle aus dem mitgelieferten Ordner, sonst aus PATH
def _resolve_exiftool() -> str:
    if getattr(sys, "frozen", False):
        bundled = SCRIPT_DIR / "exiftool_bundle" / "exiftool"
        if bundled.exists():
            return str(bundled)
    return "exiftool"

EXIFTOOL_BIN = _resolve_exiftool()

AAE_EXT = ".aae"
SUPPORTED_EXTS = {
    ".heic", ".heif", ".jpg", ".jpeg", ".png", ".tiff", ".tif", ".gif", ".bmp",
    ".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm",
    ".cr2", ".cr3", ".nef", ".arw", ".dng", ".raf", ".orf", ".rw2", ".pef", ".srw",
}

PHASE_NAMES = {
    1: "Dateien sammeln",
    2: "Metadaten lesen",
    3: "Hashes berechnen",
    4: "Dateien verschieben",
}

# Modus-Werte (intern) → Anzeige in der ComboBox
MODES = [
    ("organize",         "organize — Organisieren (verschieben + umbenennen + Zeitstempel)"),
    ("dedupe_only",      "dedupe_only — Nur Duplikate finden und behandeln"),
    ("timestamps_only",  "timestamps_only — Nur Zeitstempel erneuern (z. B. für NAS)"),
    ("fix_capture_date", "fix_capture_date — Aufnahmedatum/Zeit korrigieren (kein Verschieben)"),
    ("add_text",         "add_text — Text/Ort nach Zeitstempel setzen (kein Verschieben)"),
    ("clean_metadata",   "clean_metadata — Vom Programm gesetzte Metadaten-Felder bereinigen"),
]
MODE_INDEX = {m[0]: i for i, m in enumerate(MODES)}
INDEX_MODE = {i: m[0] for i, m in enumerate(MODES)}


# -----------------------------------------------------------------------------
# Hilfsfunktionen
# -----------------------------------------------------------------------------

def format_duration(seconds: float) -> str:
    try:
        seconds = int(seconds)
    except Exception:
        return "?"
    if seconds < 0:
        return "?"
    if seconds < 60:
        return f"{seconds} Sek."
    minutes = seconds // 60
    seconds = seconds % 60
    if minutes < 60:
        return f"{minutes} Min. {seconds} Sek."
    hours = minutes // 60
    minutes = minutes % 60
    return f"{hours} Std. {minutes} Min."


def is_internal_volume(path) -> bool:
    try:
        p = Path(path).resolve()
        mount_point = p
        while not os.path.ismount(mount_point):
            parent = mount_point.parent
            if parent == mount_point:
                break
            mount_point = parent
        if sys.platform != "darwin":
            return True
        r = subprocess.run(
            ["diskutil", "info", str(mount_point)],
            capture_output=True, text=True, timeout=10
        )
        if r.returncode != 0:
            return not str(p).startswith("/Volumes/")
        for line in r.stdout.splitlines():
            if line.strip().startswith("Internal:"):
                return "Yes" in line
        return not str(p).startswith("/Volumes/")
    except Exception:
        return not str(path).startswith("/Volumes/")


def load_config() -> Dict[str, Any]:
    try:
        if CONFIG_PATH.exists():
            return json.loads(CONFIG_PATH.read_text())
    except Exception:
        pass
    return {}


def save_config(cfg: Dict[str, Any]):
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    except Exception:
        pass


# -----------------------------------------------------------------------------
# Log-Aufbewahrung
# -----------------------------------------------------------------------------

LOG_MAX_FILES   = 10       # Maximale Anzahl Run-/Undo-/Fehler-Logs
LOG_MAX_AGE_DAYS = 7       # Maximales Alter in Tagen
CRASH_LOG_NAME  = "crash.log"


def cleanup_old_logs(log_dir: Path,
                     max_files: int = LOG_MAX_FILES,
                     max_age_days: int = LOG_MAX_AGE_DAYS) -> None:
    """Löscht alte Log-Dateien im log_dir.

    Regeln:
      • Behalte die neuesten `max_files` Dateien.
      • Behalte zusätzlich alles, was jünger als `max_age_days` ist.
      • Gelöscht wird, was BEIDE Bedingungen verletzt
        (also: zu weit hinten in der Liste UND zu alt).
      • crash.log wird separat behandelt: pro Lauf neu geschrieben,
        aber erst gelöscht, wenn es älter als max_age_days ist.
    """
    print(f"[cleanup] Prüfe {log_dir}")
    if not log_dir.exists():
        return
    try:
        files = [f for f in log_dir.iterdir()
                 if f.is_file() and f.suffix in (".txt", ".jsonl", ".log")]
        if not files:
            return

        # crash.log ausnehmen (wird separat behandelt)
        crash_files = [f for f in files if f.name == CRASH_LOG_NAME]
        regular_files = [f for f in files if f.name != CRASH_LOG_NAME]

        # Reguläre Logs: neueste zuerst
        regular_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

        now = time.time()
        max_age_sec = max_age_days * 86400
        deleted = 0

        for i, f in enumerate(regular_files):
            try:
                age = now - f.stat().st_mtime
                too_old = age > max_age_sec
                too_far = i >= max_files
                # Löschen nur wenn BEIDES zutrifft — so bleiben die
                # neuesten 10 Dateien auch dann erhalten, wenn sie
                # schon älter als 7 Tage sind (selten, aber möglich)
                if too_old and too_far:
                    f.unlink()
                    deleted += 1
                    logger.info(f"Altes Log gelöscht: {f.name}")
            except Exception as e:
                logger.warning(f"Log-Aufräumen fehlgeschlagen für "
                               f"{f.name}: {e}")

        # crash.log: nur löschen, wenn älter als max_age_days
        for c in crash_files:
            try:
                if (time.time() - c.stat().st_mtime) > max_age_sec:
                    c.unlink()
                    deleted += 1
                    logger.info(f"Altes Crash-Log gelöscht: {c.name}")
            except Exception:
                pass

        if deleted > 0:
            logger.info(f"Log-Aufräumen: {deleted} alte Datei(en) entfernt.")
    except Exception as e:
        logger.warning(f"Log-Aufräumen fehlgeschlagen: {e}")


# -----------------------------------------------------------------------------
# Globaler Exception-Handler (Absturz-Logging)
# -----------------------------------------------------------------------------

def _install_global_exception_logger() -> None:
    """Installiert sys.excepthook, der nicht abgefangene Exceptions
    in eine crash.log schreibt (mit Stacktrace)."""
    crash_log = LOG_DIR / CRASH_LOG_NAME
    crash_log.parent.mkdir(parents=True, exist_ok=True)

    def _handler(exc_type, exc_value, exc_tb):
        # KeyboardInterrupt normal behandeln
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        try:
            with open(crash_log, "a", encoding="utf-8") as f:
                f.write("\n" + "=" * 72 + "\n")
                f.write(f"Zeit: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("".join(traceback.format_exception(
                    exc_type, exc_value, exc_tb)))
        except Exception:
            pass
        # Standard-Verhalten beibehalten (Fehler auch auf Konsole)
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _handler


def _reset_crash_log() -> None:
    """Schreibt die crash.log vor jedem Lauf neu (mit Kopfzeile)."""
    crash_log = SCRIPT_DIR / "logs" / CRASH_LOG_NAME
    crash_log.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(crash_log, "w", encoding="utf-8") as f:
            f.write("=" * 72 + "\n")
            f.write(f"PictureChronoSort — Crash-Log\n")
            f.write(f"Neu geschrieben: "
                    f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("=" * 72 + "\n\n")
    except Exception as e:
        logger.warning(f"Crash-Log konnte nicht neu geschrieben werden: {e}")


def _norm(p) -> str:
    try:
        return os.path.realpath(str(p))
    except Exception:
        return str(p)


def check_exiftool() -> Tuple[bool, str]:
    try:
        r = subprocess.run([EXIFTOOL_BIN, "-ver"], capture_output=True,
                           text=True, timeout=5)
        if r.returncode == 0:
            return True, r.stdout.strip()
        return False, f"returncode={r.returncode}"
    except FileNotFoundError:
        return False, "nicht im PATH (brew install exiftool)"
    except Exception as e:
        return False, str(e)


def load_pixmap(filename: str, max_size: int) -> Optional[QPixmap]:
    path = SCRIPT_DIR / filename
    if not path.exists():
        return None
    pm = QPixmap(str(path))
    if pm.isNull():
        return None
    return pm.scaled(max_size, max_size, Qt.AspectRatioMode.KeepAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)


# -----------------------------------------------------------------------------
# Filename-Parser
# -----------------------------------------------------------------------------

FILENAME_PATTERNS = [
    ("whatsapp", re.compile(r"IMG-(\d{4})(\d{2})(\d{2})-WA", re.I)),
    ("signal", re.compile(r"signal-(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})(\d{2})", re.I)),
    ("telegram", re.compile(r"photo_(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})", re.I)),
    ("instagram", re.compile(r"IMG_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})", re.I)),
    ("screenshot_en", re.compile(r"Screenshot (\d{4})-(\d{2})-(\d{2}) at (\d{2})\.(\d{2})\.(\d{2})", re.I)),
    ("screenshot_de", re.compile(r"Bildschirmfoto (\d{4})-(\d{2})-(\d{2}) um (\d{2})\.(\d{2})\.(\d{2})", re.I)),
    ("generic_iso", re.compile(r"(\d{4})-(\d{2})-(\d{2})[_\sT](\d{2})[-:]?(\d{2})[-:]?(\d{2})")),
    ("generic_compact", re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})[_\sT-](\d{2})(\d{2})(\d{2})(?!\d)")),
]


def parse_filename_date(name: str) -> Optional[datetime]:
    for _, pat in FILENAME_PATTERNS:
        m = pat.search(name)
        if not m:
            continue
        try:
            g = m.groups()
            return datetime(int(g[0]), int(g[1]), int(g[2]),
                            int(g[3]), int(g[4]), int(g[5]))
        except (ValueError, IndexError):
            continue
    return None


def get_base_name(p: Path) -> str:
    return p.stem.lower()


# -----------------------------------------------------------------------------
# macOS Zeitstempel
# -----------------------------------------------------------------------------

class MacTimestampSetter:
    MAC_EPOCH_OFFSET = 978307200

    @staticmethod
    def set_timestamps(file_path: Path, dt: datetime) -> Tuple[bool, str]:
        messages = []
        try:
            ts = dt.timestamp()
            os.utime(file_path, (ts, ts))
            messages.append("mtime gesetzt")
        except Exception as e:
            messages.append(f"mtime-Fehler: {e}")

        birthtime_set = False
        if sys.platform == "darwin":
            try:
                mac_ts_sec = dt.timestamp() - MacTimestampSetter.MAC_EPOCH_OFFSET
                mac_ts_ns = int(mac_ts_sec * 1_000_000_000)
                attr_value = mac_ts_ns.to_bytes(8, byteorder="little", signed=True)
                os.setxattr(file_path, b"com.apple.birthtime", attr_value)
                messages.append("birthtime gesetzt")
                birthtime_set = True
            except (AttributeError, OSError):
                try:
                    dt_str = dt.strftime("%m/%d/%Y %H:%M:%S")
                    r = subprocess.run(["SetFile", "-d", dt_str, str(file_path)],
                                       capture_output=True, text=True, check=False)
                    if r.returncode == 0:
                        messages.append("birthtime via SetFile gesetzt")
                        birthtime_set = True
                    else:
                        messages.append("birthtime nicht setzbar")
                except FileNotFoundError:
                    messages.append("SetFile nicht gefunden")
                except Exception as e2:
                    messages.append(f"SetFile-Fehler: {e2}")
            except Exception as e:
                messages.append(f"birthtime-Fehler: {e}")
        return birthtime_set, "; ".join(messages)


# -----------------------------------------------------------------------------
# Metadaten
# -----------------------------------------------------------------------------

EXIF_DATE_TAGS = [
    "DateTimeOriginal",
    "CreateDate",
    "CreationDate",
    "MediaCreateDate",
    "TrackCreateDate",
    "ContentCreateDate",
    "DateTimeDigitized",
]


def _process_meta_chunk(chunk: List[Path], timeout: float) -> Optional[Dict[Path, Dict]]:
    if not chunk:
        return {}
    cmd = [EXIFTOOL_BIN, "-j", "-s", "-G1", "-q", "-q", "-api", "QuickTimeUTC=1"]
    cmd.extend(str(p) for p in chunk)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, errors="replace")
        if r.returncode != 0:
            return None
        if not r.stdout.strip():
            return {}
        out: Dict[Path, Dict] = {}
        chunk_by_norm = {_norm(p): p for p in chunk}
        for item in json.loads(r.stdout):
            src = item.get("SourceFile", "")
            if not src:
                continue
            orig = chunk_by_norm.get(_norm(src))
            if orig:
                out[orig] = item
        return out
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
        return None


def collect_metadata_batch(files: List[Path], on_progress=None,
                           on_chunk=None, on_eta=None, cancel_check=None,
                           chunk_size: int = 25, timeout: float = 30.0
                           ) -> Dict[Path, Dict[str, Any]]:
    result: Dict[Path, Dict[str, Any]] = {}
    total = len(files)
    if total == 0:
        return result

    t_start = time.monotonic()
    for i in range(0, total, chunk_size):
        if cancel_check and cancel_check():
            logger.info("Abbruch während Metadaten-Phase")
            break

        chunk = files[i:i + chunk_size]
        t0 = time.monotonic()
        chunk_result = _process_meta_chunk(chunk, timeout)

        if chunk_result is None:
            logger.warning(f"Metadaten-Chunk fehlgeschlagen "
                           f"(Größe {len(chunk)}) — Fallback")
            chunk_result = {}
            if len(chunk) > 1:
                for j in range(0, len(chunk), 10):
                    sub = chunk[j:j + 10]
                    sub_res = _process_meta_chunk(sub, timeout=15)
                    if sub_res is None:
                        for f in sub:
                            one = _process_meta_chunk([f], timeout=8)
                            if one:
                                chunk_result.update(one)
                    else:
                        chunk_result.update(sub_res)

        if chunk_result:
            result.update(chunk_result)

        elapsed_chunk = time.monotonic() - t0
        done = min(i + len(chunk), total)
        pct = int(done / total * 100)
        logger.info(f"Metadaten: {done}/{total} ({pct}%) — "
                    f"{len(chunk_result)}/{len(chunk)} in {elapsed_chunk:.1f}s")
        if on_progress:
            on_progress(pct)
        if on_chunk:
            on_chunk(done, total)

        elapsed_total = time.monotonic() - t_start
        rate = done / elapsed_total if elapsed_total > 0 else 0
        remaining = (total - done) / rate if rate > 0 else 0
        if on_eta:
            on_eta(done, total, elapsed_total, rate, remaining)

    logger.info(f"Metadaten-Phase in {time.monotonic() - t_start:.1f}s — "
                f"{len(result)}/{total} gelesen")
    return result


def extract_date_from_metadata(meta: Dict[str, Any]) -> Optional[datetime]:
    if not meta:
        return None
    for tag in EXIF_DATE_TAGS:
        for key, val in meta.items():
            if key.endswith(tag) and val:
                dt = parse_date_string(str(val))
                if dt:
                    return dt
    return None


def parse_date_string(s: str) -> Optional[datetime]:
    s = s.strip()
    if not s:
        return None
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S", "%Y:%m:%d %H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S.%f", "%Y:%m:%d %H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def get_date_keys(meta: Dict[str, Any]) -> str:
    if not meta:
        return "keine"
    keys = [k for k in meta.keys()
            if any(t in k.lower() for t in ["date", "time", "creation"])]
    return str(keys) if keys else "keine"


# -----------------------------------------------------------------------------
# Metadaten-Lesen/Schreiben für XPTitle und ImageDescription
# -----------------------------------------------------------------------------

# Muster für nichtssagende Kamera-Namen
_USELESS_NAME_RE = re.compile(
    r"^(img|dsc|dscn|dscf|p|photo|foto|bild|screenshot|screen shot|"
    r"pxl|mvimg|vid|mov|mvi|gopr|dji|untitled|unbenannt)[-_ ]?\d*$",
    re.I)


def is_meaningful_original_name(name: str) -> bool:
    """Prüft, ob ein Dateiname 'sinnvoll' ist (kein Kamera-Automatikname)."""
    if not name:
        return False
    n = name.strip()
    if not n:
        return False
    return not _USELESS_NAME_RE.match(n)


def sanitize_text_for_filename(text: str) -> str:
    """Bereitet einen Text für die Verwendung im Dateinamen vor."""
    if not text:
        return ""
    s = str(text)
    # Nullbytes entfernen
    s = s.replace("\x00", "")
    # Verbotene Zeichen ersetzen
    s = s.replace("/", "-").replace(":", "-")
    # Steuerzeichen entfernen
    s = "".join(ch for ch in s if ord(ch) >= 32)
    # Führende/nachfolgende Leerzeichen
    s = s.strip()
    # Mehrfache Leerzeichen zu einem
    s = re.sub(r"\s+", " ", s)
    return s


def get_xptitle(file_path: Path) -> Optional[str]:
    """Liest XPTitle aus den Metadaten."""
    try:
        r = subprocess.run(
            [EXIFTOOL_BIN, "-j", "-s", "-XPTitle", str(file_path)],
            capture_output=True, text=True, timeout=10, errors="replace"
        )
        if r.returncode != 0 or not r.stdout.strip():
            return None
        data = json.loads(r.stdout)
        if not data:
            return None
        val = data[0].get("XPTitle")
        if not val:
            return None
        if isinstance(val, list):
            val = "".join(str(v) for v in val)
        return str(val)
    except Exception:
        return None


def get_image_description(file_path: Path) -> Optional[str]:
    """Liest ImageDescription aus den Metadaten."""
    try:
        r = subprocess.run(
            [EXIFTOOL_BIN, "-j", "-s", "-ImageDescription", str(file_path)],
            capture_output=True, text=True, timeout=10, errors="replace"
        )
        if r.returncode != 0 or not r.stdout.strip():
            return None
        data = json.loads(r.stdout)
        if not data:
            return None
        val = data[0].get("ImageDescription")
        if not val:
            return None
        if isinstance(val, list):
            val = "".join(str(v) for v in val)
        return str(val)
    except Exception:
        return None


def set_metadata_fields(file_path: Path,
                        xptitle: Optional[str] = None,
                        image_description: Optional[str] = None
                        ) -> Tuple[bool, str]:
    """Schreibt XPTitle und/oder ImageDescription.
    Mit -P wird die Dateisystem-mtime nicht verändert."""
    args = [EXIFTOOL_BIN, "-P", "-overwrite_original"]
    if xptitle is not None:
        args.append(f"-XPTitle={xptitle}")
    if image_description is not None:
        args.append(f"-ImageDescription={image_description}")
    args.append(str(file_path))
    try:
        r = subprocess.run(args, capture_output=True, text=True,
                           timeout=30, errors="replace")
        if r.returncode != 0:
            return False, f"exiftool-Fehler: {r.stderr.strip()[:200]}"
        return True, "Metadaten gesetzt"
    except Exception as e:
        return False, f"exiftool-Exception: {e}"


# -----------------------------------------------------------------------------
# Standard-Zeitstempel im Dateinamen erkennen (flexibel)
# -----------------------------------------------------------------------------

# Ein Regex, der die verschiedenen Zeitstempel-Varianten am Anfang
# eines Dateinamens erkennt. Reihenfolge: spezifisch → allgemein.
# Liefert immer (year, month, day, hour, minute, second) — fehlende
# Anteile werden als 0 zurückgegeben.
_STAMP_PATTERNS = [
    # 2024-12-25_00-28-11 / 2024-12-25 00-28-11 / 2024.12.25_00.28.11
    # Gruppe 1=Jahr, 2=Trenner1, 3=Monat, 4=Tag, 5=Std, 6=Trenner2,
    # 7=Min, 8=Sek.  \6 = Referenz auf Trenner2 (NICHT \5!)
    (re.compile(
        r"^(\d{4})([-_.])(\d{2})\2(\d{2})"
        r"(?:[_ T]+)(\d{2})([-_.:]?)(\d{2})\6(\d{2})(?!\d)"
    ), "ymd_hms_sep"),
    # 2024-12-25_002811 (ohne Trenner zwischen H/M/S)
    (re.compile(
        r"^(\d{4})([-_.])(\d{2})\2(\d{2})"
        r"(?:[_ T]+)(\d{2})(\d{2})(\d{2})(?!\d)"
    ), "ymd_hms_compact"),
    # 20241225_002811 (rein numerisch)
    (re.compile(
        r"^(\d{4})(\d{2})(\d{2})"
        r"(?:[_ T-]+)(\d{2})(\d{2})(\d{2})(?!\d)"
    ), "ymd_hms_numeric"),
    # 2024-12-25_0028 (ohne Sekunden)
    (re.compile(
        r"^(\d{4})([-_.])(\d{2})\2(\d{2})"
        r"(?:[_ T]+)(\d{2})(\d{2})(?!\d)"
    ), "ymd_hm"),
    # 2024-12-25 (nur Datum)
    (re.compile(
        r"^(\d{4})([-_.])(\d{2})\2(\d{2})(?!\d)"
    ), "ymd_only"),
    # 25.12.2024_00-28-11 (deutsches Format)
    (re.compile(
        r"^(\d{2})([.\-])(\d{2})\2(\d{4})"
        r"(?:[_ T]+)(\d{2})([-_.:]?)(\d{2})\6(\d{2})(?!\d)"
    ), "dmy_hms_sep"),
]


def parse_any_standard_stamp(filename: str) -> Optional[datetime]:
    """Erkennt Zeitstempel am Anfang des Dateinamens in vielen
    Varianten und gibt sie als datetime zurück."""
    stem = Path(filename).stem if "." in filename else filename

    for pat, kind in _STAMP_PATTERNS:
        m = pat.match(stem)
        if not m:
            continue
        g = m.groups()
        try:
            if kind == "ymd_hms_sep":
                # (y, sep1, mo, d, h, sep2, mi, s)
                return datetime(int(g[0]), int(g[2]), int(g[3]),
                                int(g[4]), int(g[6]), int(g[7]))
            elif kind == "ymd_hms_compact":
                # (y, sep1, mo, d, h, mi, s)
                return datetime(int(g[0]), int(g[2]), int(g[3]),
                                int(g[4]), int(g[5]), int(g[6]))
            elif kind == "ymd_hms_numeric":
                # (y, mo, d, h, mi, s)
                return datetime(int(g[0]), int(g[1]), int(g[2]),
                                int(g[3]), int(g[4]), int(g[5]))
            elif kind == "ymd_hm":
                # (y, sep1, mo, d, h, mi)
                return datetime(int(g[0]), int(g[2]), int(g[3]),
                                int(g[4]), int(g[5]), 0)
            elif kind == "ymd_only":
                # (y, sep1, mo, d)
                return datetime(int(g[0]), int(g[2]), int(g[3]),
                                0, 0, 0)
            elif kind == "dmy_hms_sep":
                # (d, sep1, mo, y, h, sep2, mi, s)
                return datetime(int(g[3]), int(g[2]), int(g[0]),
                                int(g[4]), int(g[6]), int(g[7]))
        except (ValueError, IndexError):
            continue
    return None


def has_standard_stamp(filename: str) -> bool:
    return parse_any_standard_stamp(filename) is not None


def extract_standard_stamp(filename: str) -> Optional[datetime]:
    return parse_any_standard_stamp(filename)


# Nach wie vor: Regex für den *exakten* Standard-Stempel, den wir
# schreiben. Wird für Preview/Rename-Ersetzung gebraucht.
_STANDARD_STAMP_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_(\d{6})")


def normalize_stamp_in_name(filename: str) -> Optional[str]:
    """Liefert einen neuen Dateinamen mit standardisiertem Zeitstempel
    (JJJJ-MM-TT_HHMMSS) oder None, wenn kein Stempel erkannt wurde.
    Behält alles nach dem Stempel bei (getrennt durch Leerzeichen)."""
    dt = parse_any_standard_stamp(filename)
    if dt is None:
        return None
    new_stamp = dt.strftime("%Y-%m-%d_%H%M%S")

    # Rest nach dem erkannten Stempel bestimmen
    stem = Path(filename).stem
    suffix = Path(filename).suffix

    for pat, kind in _STAMP_PATTERNS:
        m = pat.match(stem)
        if not m:
            continue
        rest = stem[m.end():]
        rest = rest.lstrip("_ ").strip()
        if rest:
            return f"{new_stamp} {rest}{suffix}"
        else:
            return f"{new_stamp}{suffix}"
    return None


def split_filename_after_stamp(filename: str
                               ) -> Tuple[Optional[str], str]:
    """Zerlegt einen Dateinamen in (stamp, rest).

    stamp: Normalisierter Stempel im Format 'JJJJ-MM-TT_HHMMSS' oder None.
    rest:  alles nach dem Stempel (ohne führende Trenner). Enthält
           auch den erkannten (noch nicht normalisierten) Rest.
    """
    dt = parse_any_standard_stamp(filename)
    if dt is None:
        return None, filename
    stamp = dt.strftime("%Y-%m-%d_%H%M%S")

    stem = Path(filename).stem
    for pat, kind in _STAMP_PATTERNS:
        m = pat.match(stem)
        if not m:
            continue
        rest = stem[m.end():]
        rest = rest.lstrip("_ ").strip()
        return stamp, rest
    return stamp, stem



# -----------------------------------------------------------------------------
# GPS + Nominatim
# -----------------------------------------------------------------------------

def exiftool_get_gps(path: Path) -> Optional[Tuple[float, float]]:
    """Liest GPS-Koordinaten (lat, lon) in Dezimalgrad."""
    try:
        r = subprocess.run(
            [EXIFTOOL_BIN, "-j", "-s", "-GPSLatitude", "-GPSLongitude",
             "-GPSLatitudeRef", "-GPSLongitudeRef", str(path)],
            capture_output=True, text=True, timeout=10, errors="replace"
        )
        if r.returncode != 0 or not r.stdout.strip():
            return None
        data = json.loads(r.stdout)
        if not data:
            return None
        d = data[0]
        lat_raw = d.get("GPSLatitude")
        lon_raw = d.get("GPSLongitude")
        if lat_raw is None or lon_raw is None:
            return None
        lat = _parse_gps_value(lat_raw)
        lon = _parse_gps_value(lon_raw)
        if lat is None or lon is None:
            return None
        lat_ref = str(d.get("GPSLatitudeRef", "N")).upper()
        lon_ref = str(d.get("GPSLongitudeRef", "E")).upper()
        if lat_ref.startswith("S"):
            lat = -abs(lat)
        if lon_ref.startswith("W"):
            lon = -abs(lon)
        return (lat, lon)
    except Exception:
        return None


def _parse_gps_value(val) -> Optional[float]:
    """Konvertiert einen GPS-Wert in Dezimalgrad. Akzeptiert:
    - float/int (bereits Dezimalgrad)
    - '52 deg 30' 15.00" N' (String)
    - dict mit deg/min/sec
    """
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, dict):
        try:
            d = float(val.get("deg", 0))
            m = float(val.get("min", 0))
            s = float(val.get("sec", 0))
            return d + m / 60.0 + s / 3600.0
        except Exception:
            return None
    s = str(val).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        pass
    # "52 deg 30' 15.00\""
    m = re.match(
        r"(-?\d+(?:\.\d+)?)\s*(?:deg|°)?\s*"
        r"(?:(\d+(?:\.\d+)?)\s*')?\s*"
        r"(?:(\d+(?:\.\d+)?)\s*\")?", s)
    if m:
        try:
            d = float(m.group(1))
            mm = float(m.group(2)) if m.group(2) else 0.0
            ss = float(m.group(3)) if m.group(3) else 0.0
            sign = -1 if d < 0 else 1
            return sign * (abs(d) + mm / 60.0 + ss / 3600.0)
        except Exception:
            return None
    return None


def nominatim_reverse(lat: float, lon: float,
                      user_agent: str = "PictureChronoSort/6.0") -> Optional[str]:
    """Fragt Nominatim nach einem Ortsnamen. Gibt einen kurzen
    Ortsnamen zurück oder None."""
    if not URLLIB_AVAILABLE:
        return None
    try:
        url = (
            "https://nominatim.openstreetmap.org/reverse?"
            f"format=jsonv2&lat={lat:.4f}&lon={lon:.4f}&zoom=10"
            "&accept-language=de"
        )
        req = urllib.request.Request(url, headers={"User-Agent": user_agent})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        addr = data.get("address", {}) or {}
        # Priorität: city > town > village > municipality > county > state
        for key in ("city", "town", "village", "municipality", "county",
                    "state"):
            if addr.get(key):
                return str(addr[key])
        if data.get("name"):
            return str(data["name"])
        return None
    except Exception as e:
        logger.warning(f"Nominatim-Fehler: {e}")
        return None


# -----------------------------------------------------------------------------
# Smart-Hashing
# -----------------------------------------------------------------------------

def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    try:
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except Exception:
        return ""
    return h.hexdigest()


def compute_hashes_smart(files: List[Path], on_progress=None,
                         on_chunk=None, on_eta=None,
                         cancel_check=None) -> Dict[Path, str]:
    result: Dict[Path, str] = {}
    size_groups: Dict[int, List[Path]] = {}
    for p in files:
        try:
            s = p.stat().st_size
        except Exception:
            result[p] = ""
            continue
        size_groups.setdefault(s, []).append(p)

    for g in size_groups.values():
        if len(g) == 1:
            result[g[0]] = f"__unique__{id(g[0])}"

    candidates = [p for g in size_groups.values() if len(g) > 1 for p in g]
    total_cand = len(candidates)
    logger.info(f"Hashing-Kandidaten (nach Größe): {total_cand}/{len(files)}")
    if total_cand == 0:
        return result

    done = 0
    last_log = 0
    t_start = time.monotonic()
    with ThreadPoolExecutor(max_workers=4) as pool:
        future_to_path = {pool.submit(_sha256, p): p for p in candidates}
        for fut in as_completed(future_to_path):
            if cancel_check and cancel_check():
                break
            path = future_to_path[fut]
            try:
                result[path] = fut.result()
            except Exception:
                result[path] = ""
            done += 1
            if done - last_log >= 25 or done == total_cand:
                last_log = done
                pct = int(done / total_cand * 100)
                logger.info(f"Hashing: {done}/{total_cand} ({pct}%)")
                if on_progress:
                    on_progress(pct)
                if on_chunk:
                    on_chunk(done, total_cand)
                elapsed = time.monotonic() - t_start
                rate = done / elapsed if elapsed > 0 else 0
                remaining = (total_cand - done) / rate if rate > 0 else 0
                if on_eta:
                    on_eta(done, total_cand, elapsed, rate, remaining)
    if on_progress:
        on_progress(100)
    return result


# -----------------------------------------------------------------------------
# Datum-Korrektur: Hilfsfunktionen
# -----------------------------------------------------------------------------

def exiftool_get_datetime_original(path: Path) -> Optional[datetime]:
    try:
        r = subprocess.run(
            [EXIFTOOL_BIN, "-j", "-s", "-DateTimeOriginal", str(path)],
            capture_output=True, text=True, timeout=10, errors="replace"
        )
        if r.returncode != 0 or not r.stdout.strip():
            return None
        data = json.loads(r.stdout)
        if not data:
            return None
        val = data[0].get("DateTimeOriginal")
        if not val:
            return None
        return parse_date_string(str(val))
    except Exception:
        return None


def exiftool_set_datetime_original(path: Path,
                                   new_dt: datetime,
                                   also_fs_timestamps: bool = True
                                   ) -> Tuple[bool, str]:
    dt_str = new_dt.strftime("%Y:%m:%d %H:%M:%S")
    try:
        r = subprocess.run(
            [EXIFTOOL_BIN, "-overwrite_original",
             f"-DateTimeOriginal={dt_str}",
             f"-CreateDate={dt_str}",
             f"-ModifyDate={dt_str}",
             str(path)],
            capture_output=True, text=True, timeout=30, errors="replace"
        )
        if r.returncode != 0:
            return False, f"exiftool-Fehler: {r.stderr.strip()[:200]}"
    except Exception as e:
        return False, f"exiftool-Exception: {e}"

    msg = "EXIF gesetzt"
    if also_fs_timestamps:
        try:
            _, fs_msg = MacTimestampSetter.set_timestamps(path, new_dt)
            msg += f"; {fs_msg}"
        except Exception as e:
            msg += f"; FS-Fehler: {e}"
    return True, msg


def validate_datetime(year: int, month: int, day: int,
                      hour: int = 12, minute: int = 0, second: int = 0
                      ) -> Optional[datetime]:
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def suspicious_date_warning(dt: datetime,
                            file_mtime: Optional[datetime] = None
                            ) -> Optional[str]:
    warns = []
    now = datetime.now()
    if dt > now:
        warns.append(f"Datum liegt in der Zukunft ({dt:%Y-%m-%d})")
    if dt.year < 1970:
        warns.append(f"Jahr {dt.year} liegt vor der Digitalfotografie")
    if (dt.month == 1 and dt.day == 1
            and dt.hour == 0 and dt.minute == 0 and dt.second == 0):
        warns.append("1. Januar um Mitternacht — typischer Kamera-Reset")
    if file_mtime is not None:
        diff = abs((dt - file_mtime).total_seconds())
        if diff > 5 * 365 * 86400:
            warns.append(
                f"EXIF-Datum ({dt:%Y-%m-%d}) weicht stark vom "
                f"Dateisystem-Datum ({file_mtime:%Y-%m-%d}) ab")
    return "; ".join(warns) if warns else None


def apply_datetime_correction(old_dt: datetime,
                              set_year: Optional[int] = None,
                              set_month: Optional[int] = None,
                              set_day: Optional[int] = None,
                              set_hour: Optional[int] = None,
                              set_minute: Optional[int] = None,
                              set_second: Optional[int] = None,
                              add_hours: int = 0,
                              add_minutes: int = 0,
                              add_seconds: int = 0
                              ) -> Tuple[Optional[datetime], Optional[str]]:
    y = set_year if set_year is not None else old_dt.year
    m = set_month if set_month is not None else old_dt.month
    d = set_day if set_day is not None else old_dt.day
    h = set_hour if set_hour is not None else old_dt.hour
    mi = set_minute if set_minute is not None else old_dt.minute
    s = set_second if set_second is not None else old_dt.second

    if not (0 <= h <= 23):
        return None, f"Stunde {h} ungültig (0–23)"
    if not (0 <= mi <= 59):
        return None, f"Minute {mi} ungültig (0–59)"
    if not (0 <= s <= 59):
        return None, f"Sekunde {s} ungültig (0–59)"
    try:
        intermediate = datetime(y, m, d, h, mi, s)
    except ValueError as e:
        return None, f"Ungültiges Datum {y}-{m:02d}-{d:02d}: {e}"

    delta_seconds = (add_hours * 3600 + add_minutes * 60 + add_seconds)
    if delta_seconds != 0:
        try:
            final = intermediate + timedelta(seconds=delta_seconds)
        except OverflowError:
            return None, "Zeitverschiebung führt aus dem gültigen Bereich"
    else:
        final = intermediate
    return final, None


# -----------------------------------------------------------------------------
# RunLogger
# -----------------------------------------------------------------------------

class RunLogger:
    def __init__(self, base_dir: Path, dry_run: bool = False):
        self.log_dir = base_dir / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.main_path = self.log_dir / f"run_{ts}.txt"
        self.failed_path = self.log_dir / f"failed_{ts}.txt"
        self.dry_run = dry_run
        if not dry_run:
            self.undo_path = self.log_dir / f"undo_{ts}.jsonl"
            self._undo_fh = open(self.undo_path, "w", encoding="utf-8")
        else:
            self.undo_path = None
            self._undo_fh = None
        self._fh = open(self.main_path, "w", encoding="utf-8")
        self._failures: Dict[str, List[Dict[str, Any]]] = {
            "no_date": [], "error": [], "duplicate": [], "fs_fallback": [],
            "livephoto_mov": [], "dup_failed": []
        }

    def main(self, msg: str):
        try:
            self._fh.write(msg + "\n")
            self._fh.flush()
        except Exception:
            pass

    def undo(self, action: str, source: str, target: str = None,
             dt: str = None, old_dt: str = None,
             old_xptitle: str = None,
             old_image_description: str = None):
        if self._undo_fh is None:
            return
        try:
            payload = {
                "action": action, "source": source, "target": target,
                "datetime": dt, "old_datetime": old_dt,
                "ts": datetime.now().isoformat(),
            }
            if old_xptitle is not None:
                payload["old_xptitle"] = old_xptitle
            if old_image_description is not None:
                payload["old_image_description"] = old_image_description
            self._undo_fh.write(json.dumps(payload) + "\n")
            self._undo_fh.flush()
        except Exception:
            pass

    def add_failure(self, kind: str, info: Dict[str, Any]):
        self._failures.setdefault(kind, []).append(info)

    def finalize(self, stats, config, start, end, diagnostics=None):
        duration = end - start
        try:
            with open(self.failed_path, "w", encoding="utf-8") as f:
                f.write("=" * 78 + "\n")
                f.write("PROBLEMATISCHE DATEIEN / ZUSAMMENFASSUNG\n")
                f.write(f"Start:   {start.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"Ende:    {end.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"Dauer:   {format_duration(duration.total_seconds())}\n")
                f.write(f"Quelle:  {config.get('source', '?')}\n")
                f.write(f"Ziel:    {config.get('dest', '?')}\n")
                f.write(f"Modus:   {config.get('mode', 'organize')} / "
                        f"{'DRY-RUN' if config.get('dry_run') else 'ECHTER LAUF'}\n")
                f.write(f"mtime-Fallback: {'JA' if config.get('fs_fallback') else 'NEIN'}\n")
                f.write(f"Filename-Parser: {'JA' if config.get('use_filename') else 'NEIN'}\n")
                f.write(f"Live-Photo-Gruppierung: {'JA' if config.get('use_livephoto') else 'NEIN'}\n")
                f.write(f"AAE-Bearbeitungsdateien: {'JA' if config.get('use_aae') else 'NEIN'}\n")
                f.write(f"Live-Photo MOV entfernen: {'JA' if config.get('remove_lp_mov') else 'NEIN'}\n")
                f.write(f"Duplikat-Aktion: {config.get('dup_action', 'skip')}\n")
                f.write(f"send2trash verfügbar: {'JA' if SEND2TRASH_AVAILABLE else 'NEIN'}\n")
                f.write(f"Quelle auf internem LW: "
                        f"{'JA' if config.get('internal') else 'NEIN'}\n")
                f.write("=" * 78 + "\n\n")
                f.write("STATISTIK\n" + "-" * 78 + "\n")
                for k, v in stats.items():
                    f.write(f"  {k:34s}: {v}\n")
                f.write("\n")
                if diagnostics:
                    f.write("DIAGNOSE (Dateien nach Lauf)\n" + "-" * 78 + "\n")
                    for k, v in diagnostics.items():
                        f.write(f"  {k:34s}: {v}\n")
                    f.write("\n")
                self._section(f, "Kein Aufnahmedatum", self._failures["no_date"])
                self._section(f, "Fehler", self._failures["error"])
                self._section(f, "Duplikate (erkannt)", self._failures["duplicate"])
                self._section(f, "Duplikat-Aktionen fehlgeschlagen", self._failures["dup_failed"])
                self._section(f, "Live-Photo-MOVs", self._failures["livephoto_mov"])
                self._section(f, "mtime-Fallback verwendet", self._failures["fs_fallback"])
        except Exception as e:
            logger.error(f"Fehler-Log-Schreibfehler: {e}")

    @staticmethod
    def _section(f, title, items):
        f.write(f"{title} ({len(items)})\n" + "-" * 78 + "\n")
        if not items:
            f.write("  (keine)\n\n")
            return
        for it in items:
            f.write(f"Datei: {it.get('path', '?')}\n")
            for key, label in [("size", "Größe"), ("fs_mtime", "Dateisystem-mtime"),
                               ("date_keys", "Verfügbare Datums-Keys"),
                               ("message", "Details"), ("duplicate_of", "Duplikat von"),
                               ("action", "Aktion"), ("reason", "Grund")]:
                if key in it:
                    val = it[key]
                    if key == "size":
                        val = f"{val:,} Bytes".replace(",", ".")
                    f.write(f"  {label}: {val}\n")
            f.write("\n")
        f.write("\n")

    def close(self):
        try:
            self._fh.close()
            if self._undo_fh:
                self._undo_fh.close()
        except Exception:
            pass


# -----------------------------------------------------------------------------
# Kernlogik
# -----------------------------------------------------------------------------

class FileOrganizer:
    def __init__(self, source_dir: Path, dest_dir: Path,
                 dry_run=True, use_fs_date_fallback=False,
                 use_filename_parser=True, use_live_photo=True,
                 use_aae=True, remove_livephoto_movs=False,
                 dup_action="skip", dup_folder=None,
                 mode="organize", run_logger=None,
                 keep_filename_text=True):
        self.source_dir = source_dir
        self.dest_dir = dest_dir
        self.dry_run = dry_run
        self.use_fs_date_fallback = use_fs_date_fallback
        self.use_filename_parser = use_filename_parser
        self.use_live_photo = use_live_photo
        self.use_aae = use_aae
        self.remove_livephoto_movs = remove_livephoto_movs
        self.dup_action = dup_action
        self.dup_folder = dup_folder
        self.mode = mode
        self.run_logger = run_logger
        self.keep_filename_text = keep_filename_text
        self.stats = {
            "total": 0, "processed": 0, "skipped_no_date": 0,
            "duplicates_detected": 0, "duplicates_removed": 0,
            "duplicate_action_failed": 0,
            "used_fs_date": 0, "used_filename_date": 0,
            "used_livephoto_date": 0, "used_aae_date": 0,
            "livephoto_movs_removed": 0,
            "skipped_already_in_place": 0, "errors": 0,
            "text_set": 0, "text_skipped": 0,
            "cleaned_xptitle": 0, "cleaned_image_description": 0,
            "cleaned_none": 0,
        }
        self.hashes: Dict[str, Path] = {}
        self._planned_targets: set = set()
        self._base_counters: Dict[str, int] = {}

    def _active_exts(self) -> set:
        exts = set(SUPPORTED_EXTS)
        if self.use_aae:
            exts.add(AAE_EXT)
        return exts

    def _scan_files(self) -> Tuple[List[Path], set]:
        src_norm = _norm(self.source_dir)
        exclude_paths = []
        dest_norm = _norm(self.dest_dir)
        if self.mode == "organize" and dest_norm != src_norm:
            exclude_paths.append(dest_norm)
        if self.dup_folder:
            dup_norm = _norm(self.dup_folder)
            if dup_norm != src_norm:
                exclude_paths.append(dup_norm)

        exts = self._active_exts()
        pre_count = 0
        aae_count = 0
        files_list = []
        try:
            for f in self.source_dir.rglob("*"):
                if not f.is_file():
                    continue
                if f.name.startswith("."):
                    continue
                if f.suffix.lower() not in exts:
                    continue
                pre_count += 1
                if f.suffix.lower() == AAE_EXT:
                    aae_count += 1
                try:
                    rel = f.relative_to(self.source_dir)
                    rel_parts = [p.lower() for p in rel.parts[:-1]]
                    if any(p.startswith("_duplikate") or p == "logs"
                           for p in rel_parts):
                        continue
                except ValueError:
                    pass
                f_norm = _norm(f)
                skipped = False
                for ex in exclude_paths:
                    if f_norm == ex or f_norm.startswith(ex + os.sep):
                        skipped = True
                        break
                if skipped:
                    continue
                files_list.append(f)
        except Exception as e:
            logger.error(f"Fehler beim Scannen: {e}")

        logger.info(f"Dateien mit passender Endung: {pre_count} "
                    f"(davon AAE: {aae_count})")
        logger.info(f"Nach Ausschluss-Filter: {len(files_list)}")
        if exclude_paths:
            logger.info(f"Ausgeschlossene Ordner: {exclude_paths}")

        heic_bases = {get_base_name(p) for p in files_list
                      if p.suffix.lower() in (".heic", ".heif")}
        return sorted(files_list), heic_bases

    def scan_and_process(self, progress_callback=None, phase_callback=None,
                         eta_callback=None, cancel_check=None,
                         file_callback=None) -> None:
        if phase_callback:
            phase_callback(PHASE_NAMES[1], 1, 4)
        files, heic_bases = self._scan_files()
        self.stats["total"] = len(files)
        logger.info(f"Gefundene Dateien: {len(files)}")
        if not files:
            logger.warning("Keine Dateien gefunden — Pfade prüfen.")
            return

        metadata: Dict[Path, Dict[str, Any]] = {}
        base_dates: Dict[str, datetime] = {}

        if self.mode == "dedupe_only":
            logger.info("Modus 'dedupe_only' — Metadaten-Phase übersprungen")
            if phase_callback:
                phase_callback(f"{PHASE_NAMES[2]} (übersprungen)", 2, 4)
        else:
            if phase_callback:
                phase_callback(PHASE_NAMES[2], 2, 4)
            metadata = collect_metadata_batch(
                files,
                on_progress=(lambda p: progress_callback(p) if progress_callback else None),
                on_chunk=(lambda d, t: phase_callback(f"{PHASE_NAMES[2]}: {d}/{t}", 2, 4)
                          if phase_callback else None),
                on_eta=eta_callback,
                cancel_check=cancel_check,
            )

            if self.use_live_photo or self.use_aae:
                for p in files:
                    dt = extract_date_from_metadata(metadata.get(p) or {})
                    if dt is None and self.use_filename_parser:
                        dt = parse_filename_date(p.name)
                    if dt:
                        base = get_base_name(p)
                        if base not in base_dates:
                            base_dates[base] = dt

        hashes: Dict[Path, str] = {}
        if self.mode in ("organize", "dedupe_only"):
            if phase_callback:
                phase_callback(PHASE_NAMES[3], 3, 4)
            hashes = compute_hashes_smart(
                files,
                on_progress=(lambda p: progress_callback(p) if progress_callback else None),
                on_chunk=(lambda d, t: phase_callback(f"{PHASE_NAMES[3]}: {d}/{t}", 3, 4)
                          if phase_callback else None),
                on_eta=eta_callback,
                cancel_check=cancel_check,
            )

        if phase_callback:
            phase_callback(PHASE_NAMES[4], 4, 4)
        t_start = time.monotonic()
        last_progress_time = 0.0
        for i, file_path in enumerate(files, 1):
            if cancel_check and cancel_check():
                logger.info("Abbruch durch Benutzer")
                break
            if file_callback:
                file_callback(i, len(files))
            try:
                self._process_file(
                    file_path, metadata.get(file_path),
                    hashes.get(file_path) if hashes else None,
                    base_dates, heic_bases,
                )
            except Exception as e:
                logger.error(f"Fehler bei {file_path.name}: {e}")
                self.stats["errors"] += 1
                if self.run_logger:
                    self.run_logger.add_failure("error",
                        {"path": str(file_path), "message": str(e)})
            self.stats["processed"] = i

            now = time.monotonic()
            if now - last_progress_time >= 0.1 or i == len(files):
                last_progress_time = now
                elapsed = now - t_start
                rate = i / elapsed if elapsed > 0 else 0
                remaining = (len(files) - i) / rate if rate > 0 else 0
                if progress_callback:
                    progress_callback(int(i / len(files) * 100))
                if eta_callback:
                    eta_callback(i, len(files), elapsed, rate, remaining)

    def _process_file(self, file_path, meta, hash_val, base_dates, heic_bases):
        if (self.remove_livephoto_movs
                and self.mode == "organize"
                and file_path.suffix.lower() in (".mov", ".mp4")
                and get_base_name(file_path) in heic_bases):
            self._handle_livephoto_mov(file_path)
            return

        is_dup = False
        dup_of = None
        if hash_val and not hash_val.startswith("__unique__"):
            if hash_val in self.hashes:
                is_dup = True
                dup_of = self.hashes[hash_val]
            else:
                self.hashes[hash_val] = file_path

        if self.mode == "dedupe_only":
            if is_dup and dup_of:
                self.stats["duplicates_detected"] += 1
                logger.info(f"  -> Duplikat: {file_path.name} == {dup_of.name}")
                if self.run_logger:
                    self.run_logger.add_failure("duplicate", {
                        "path": str(file_path), "duplicate_of": str(dup_of)
                    })
                self._handle_duplicate(file_path, dup_of)
            return

        if is_dup and dup_of:
            self.stats["duplicates_detected"] += 1
            logger.info(f"  -> Duplikat: {file_path.name} == {dup_of.name}")
            if self.run_logger:
                self.run_logger.add_failure("duplicate", {
                    "path": str(file_path), "duplicate_of": str(dup_of)
                })
            self._handle_duplicate(file_path, dup_of)
            return

        capture_dt = extract_date_from_metadata(meta or {})
        date_source = "Metadaten"

        if capture_dt is None and self.use_aae and file_path.suffix.lower() == AAE_EXT:
            sibling_dt = base_dates.get(get_base_name(file_path))
            if sibling_dt:
                capture_dt = sibling_dt
                date_source = "AAE-Sibling"
                self.stats["used_aae_date"] += 1

        if capture_dt is None and self.use_live_photo:
            sibling_dt = base_dates.get(get_base_name(file_path))
            if sibling_dt:
                capture_dt = sibling_dt
                date_source = "Live-Photo"
                self.stats["used_livephoto_date"] += 1

        if capture_dt is None and self.use_filename_parser:
            dt = parse_filename_date(file_path.name)
            if dt:
                capture_dt = dt
                date_source = "Dateiname"
                self.stats["used_filename_date"] += 1

        if capture_dt is None and self.use_fs_date_fallback:
            try:
                capture_dt = datetime.fromtimestamp(file_path.stat().st_mtime)
                date_source = "mtime"
                self.stats["used_fs_date"] += 1
                if self.run_logger:
                    self.run_logger.add_failure("fs_fallback", {
                        "path": str(file_path),
                        "message": f"Verwendet: {capture_dt.strftime('%Y-%m-%d %H:%M:%S')}"
                    })
            except Exception:
                pass

        if capture_dt is None:
            self.stats["skipped_no_date"] += 1
            try:
                fs_mtime = datetime.fromtimestamp(
                    file_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                fs_mtime = "?"
            logger.warning(f"  -> Kein Datum. Keys: {get_date_keys(meta or {})}")
            if self.run_logger:
                try:
                    size = file_path.stat().st_size
                except Exception:
                    size = 0
                self.run_logger.add_failure("no_date", {
                    "path": str(file_path), "size": size,
                    "fs_mtime": fs_mtime,
                    "date_keys": get_date_keys(meta or {})
                })
            return

        # Originalname in XPTitle speichern (nur im organize-Modus)
        if self.mode == "organize":
            self._ensure_xptitle(file_path)

        if self.mode == "timestamps_only":
            if self.dry_run:
                logger.info(f"  -> [DRY-RUN] ({date_source}) Zeitstempel: "
                            f"{capture_dt.strftime('%Y-%m-%d %H:%M:%S')}")
            else:
                _, msg = MacTimestampSetter.set_timestamps(file_path, capture_dt)
                logger.info(f"  -> ({date_source}) {msg}")
                if self.run_logger:
                    self.run_logger.undo("touch", str(file_path),
                                         dt=capture_dt.isoformat())
            return

        target_path = self._build_target_path(file_path, capture_dt)

        # Bereits am Zielort?
        if _norm(target_path) == _norm(file_path):
            self.stats["skipped_already_in_place"] += 1
            logger.info(
                f"  -> ({date_source})\n"
                f"     Original: {file_path.name}\n"
                f"     Bereits am Zielort — übersprungen")
            logger.info("-" * 60)
            return

        target_path = self._resolve_collision(target_path, get_base_name(file_path))

        if self.dry_run:
            logger.info(
                f"  -> [DRY-RUN] ({date_source})\n"
                f"     Original: {file_path.name}\n"
                f"     Ziel:     {target_path.name}")
            logger.info("-" * 60)
        else:
            try:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(file_path), str(target_path))
                _, msg = MacTimestampSetter.set_timestamps(target_path, capture_dt)
                logger.info(
                    f"  -> ({date_source})\n"
                    f"     Original: {file_path.name}\n"
                    f"     Neu:      {target_path.name}\n"
                    f"     Zeitstempel: {msg}")
                logger.info("-" * 60)
                if self.run_logger:
                    self.run_logger.undo("move", str(file_path), str(target_path),
                                         dt=capture_dt.isoformat())
            except Exception as e:
                logger.error(
                    f"  ✗ Fehler beim Verschieben\n"
                    f"     Datei: {file_path.name}\n"
                    f"     Grund: {e}")
                logger.info("-" * 60)
                self.stats["errors"] += 1
                if self.run_logger:
                    self.run_logger.add_failure("error", {
                        "path": str(file_path),
                        "message": str(e),
                    })

    def _ensure_xptitle(self, file_path: Path):
        """Stellt sicher, dass XPTitle den Originalnamen enthält.
        Wird nur beim ersten Lauf geschrieben.
        Wichtig: Wenn nach Entfernen des Zeitstempels nichts Sinnvolles
        übrig bleibt, wird XPTitle NICHT gesetzt."""
        try:
            existing = get_xptitle(file_path)
            if existing:
                return  # schon vorhanden

            # Zeitstempel in beliebigem Format entfernen
            stem = file_path.stem
            dt = parse_any_standard_stamp(file_path.name)
            if dt is not None:
                # Alles bis zum Ende des Stempels abschneiden
                # Dazu den Stempel-Text im stem finden
                stamp_variants = [
                    dt.strftime("%Y-%m-%d_%H%M%S"),
                    dt.strftime("%Y-%m-%d_%H-%M-%S"),
                    dt.strftime("%Y-%m-%d_%H.%M.%S"),
                    dt.strftime("%Y-%m-%d %H-%M-%S"),
                    dt.strftime("%Y-%m-%d %H.%M.%S"),
                    dt.strftime("%Y-%m-%d_%H%M"),
                    dt.strftime("%Y-%m-%d %H%M%S"),
                    dt.strftime("%Y-%m-%d"),
                    dt.strftime("%Y_%m_%d_%H-%M-%S"),
                    dt.strftime("%d.%m.%Y_%H-%M-%S"),
                ]
                original = stem
                for v in stamp_variants:
                    if stem.startswith(v):
                        original = stem[len(v):].lstrip("_ -").strip()
                        break

                # Wenn nach Entfernen nichts übrig bleibt: NICHT setzen
                if not original:
                    return

                # Wenn der Rest nur aus Ziffern/Bindestrichen besteht
                # (Rest eines zerlegten Stempels), NICHT setzen
                if re.fullmatch(r"[\d\-_. ]+", original):
                    return
            else:
                # Kein Stempel erkannt → kompletter stem als Original
                original = stem

            # Nur setzen, wenn der Name "sinnvoll" ist
            if not is_meaningful_original_name(original):
                return

            ok, msg = set_metadata_fields(file_path, xptitle=original)
            if ok:
                logger.info(f"     XPTitle gesetzt: {original} "
                            f"(aus: {file_path.name})")
                logger.info(f"     XPTitle gesetzt: {original}")
            else:
                logger.warning(f"     XPTitle-Fehler: {msg}")
        except Exception as e:
            logger.warning(f"     XPTitle-Exception: {e}")

    def _handle_livephoto_mov(self, file_path: Path):
        if self.dry_run:
            logger.info(f"  -> [DRY-RUN] Live-Photo-MOV würde entfernt "
                        f"({self.dup_action}): {file_path.name}")
            self.stats["livephoto_movs_removed"] += 1
            return
        if self.dup_action == "skip":
            logger.info(f"  -> Live-Photo-MOV bleibt (skip): {file_path.name}")
            return
        effective = self.dup_action
        if effective == "trash" and not SEND2TRASH_AVAILABLE:
            logger.warning("  -> ⚠️ send2trash fehlt — Fallback auf 'move'")
            effective = "move"
            if not self.dup_folder:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                self.dup_folder = self.source_dir / f"_Duplikate_{ts}"

        if effective == "move":
            if not self.dup_folder:
                return
            self.dup_folder.mkdir(parents=True, exist_ok=True)
            try:
                rel = file_path.relative_to(self.source_dir)
            except ValueError:
                rel = Path(file_path.name)
            target = self.dup_folder / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                target = self._resolve_collision(target, get_base_name(file_path))
            try:
                shutil.move(str(file_path), str(target))
                self.stats["livephoto_movs_removed"] += 1
                logger.info(f"  -> Live-Photo-MOV verschoben: {target.name}")
                if self.run_logger:
                    self.run_logger.undo("dup_move", str(file_path), str(target))
            except Exception as e:
                logger.warning(f"  -> Fehler: {e}")
        elif effective == "trash":
            try:
                send2trash.send2trash(str(file_path))
                self.stats["livephoto_movs_removed"] += 1
                logger.info(f"  -> Live-Photo-MOV in Papierkorb: {file_path.name}")
                if self.run_logger:
                    self.run_logger.undo("dup_trash", str(file_path))
            except Exception as e:
                logger.warning(f"  -> Papierkorb-Fehler: {e}")
        elif effective == "delete":
            try:
                os.remove(str(file_path))
                self.stats["livephoto_movs_removed"] += 1
                logger.info(f"  -> Live-Photo-MOV gelöscht: {file_path.name}")
                if self.run_logger:
                    self.run_logger.undo("dup_delete", str(file_path))
            except Exception as e:
                logger.warning(f"  -> Lösch-Fehler: {e}")

    def _handle_duplicate(self, file_path, dup_of):
        if self.dup_action == "skip":
            logger.info("     (skip — bleibt liegen)")
            return
        if self.dry_run:
            logger.info(f"     [DRY-RUN] Würde Duplikat behandeln: {self.dup_action}")
            self.stats["duplicates_removed"] += 1
            return

        effective = self.dup_action
        if effective == "trash" and not SEND2TRASH_AVAILABLE:
            logger.warning("     ⚠️ send2trash fehlt — Fallback auf 'move'")
            effective = "move"
            if not self.dup_folder:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                self.dup_folder = self.source_dir / f"_Duplikate_{ts}"

        if effective == "move":
            if not self.dup_folder:
                logger.warning("     Kein Duplikat-Ordner — übersprungen")
                self.stats["duplicate_action_failed"] += 1
                return
            self.dup_folder.mkdir(parents=True, exist_ok=True)
            try:
                rel = file_path.relative_to(self.source_dir)
            except ValueError:
                rel = Path(file_path.name)
            target = self.dup_folder / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                target = self._resolve_collision(target, get_base_name(file_path))
            try:
                shutil.move(str(file_path), str(target))
                self.stats["duplicates_removed"] += 1
                logger.info(f"     -> Duplikat verschoben: {target.name}")
                if self.run_logger:
                    self.run_logger.undo("dup_move", str(file_path), str(target))
            except Exception as e:
                logger.warning(f"     -> Fehler: {e}")
                self.stats["duplicate_action_failed"] += 1
                if self.run_logger:
                    self.run_logger.add_failure("dup_failed", {
                        "path": str(file_path), "action": "move", "reason": str(e)
                    })

        elif effective == "trash":
            try:
                send2trash.send2trash(str(file_path))
                self.stats["duplicates_removed"] += 1
                logger.info("     -> Duplikat in Papierkorb")
                if self.run_logger:
                    self.run_logger.undo("dup_trash", str(file_path))
            except Exception as e:
                logger.warning(f"     -> Papierkorb-Fehler: {e}")
                self.stats["duplicate_action_failed"] += 1
                if self.run_logger:
                    self.run_logger.add_failure("dup_failed", {
                        "path": str(file_path), "action": "trash", "reason": str(e)
                    })

        elif effective == "delete":
            try:
                os.remove(str(file_path))
                self.stats["duplicates_removed"] += 1
                logger.info("     -> Duplikat gelöscht")
                if self.run_logger:
                    self.run_logger.undo("dup_delete", str(file_path))
            except Exception as e:
                logger.warning(f"     -> Lösch-Fehler: {e}")
                self.stats["duplicate_action_failed"] += 1
                if self.run_logger:
                    self.run_logger.add_failure("dup_failed", {
                        "path": str(file_path), "action": "delete", "reason": str(e)
                    })

    def _build_target_path(self, original, dt):
        year_dir = self.dest_dir / f"{dt.year}"
        month_dir = year_dir / f"{dt.year}-{dt.month:02d}"

        # Vorhandenen Text übernehmen — NUR wenn die Option aktiv ist
        base_stem = dt.strftime("%Y-%m-%d_%H%M%S")
        existing_text = self._get_text_to_keep(original)
        # Doppelte Wörter vermeiden: Text nur anhängen, wenn er
        # nicht schon im Basisnamen enthalten ist
        # Normalisierter Vergleich: Whitespace reduzieren, um
        # unsichtbare Zeichen und doppelte Leerzeichen zu ignorieren
        if existing_text:
            norm_existing = re.sub(r"\s+", " ", existing_text.strip()).lower()
            norm_original = re.sub(r"\s+", " ", original.stem.strip()).lower()
            if norm_existing not in norm_original:
                base_stem = f"{base_stem} {existing_text}"


            # 2. Priorität: Text aus aktuellem Dateinamen
            if not existing_text:
                try:
                    _, rest = split_filename_after_stamp(original.stem)
                    # Nur übernehmen, wenn kein Kamera-Automatikname
                    if rest and not re.fullmatch(
                            r"(img|dsc|dscn|dscf|pxl|mvimg)[-_]?\d+",
                            rest, re.IGNORECASE):
                        existing_text = sanitize_text_for_filename(rest)
                except Exception:
                    pass

        if existing_text:
            base_stem = f"{base_stem} {existing_text}"

        new_name = f"{base_stem}{original.suffix.lower()}"
        return month_dir / new_name

    def _resolve_collision(self, target_path, base_name=""):
        counter = self._base_counters.get(base_name, 0)
        while True:
            if counter == 0:
                candidate = target_path
            else:
                suffix = f"_{counter:03d}"
                # Bei Bedarf den Stamm kürzen, damit der Name nicht
                # die macOS-Grenze von 255 Zeichen überschreitet
                max_stem_len = 240 - len(suffix) - len(target_path.suffix)
                stem = target_path.stem[:max_stem_len]
                candidate = target_path.with_name(
                    f"{stem}{suffix}{target_path.suffix}")
            if not candidate.exists() and candidate not in self._planned_targets:
                self._base_counters[base_name] = counter
                self._planned_targets.add(candidate)
                return candidate
            counter += 1
            if counter > 9999:
                raise RuntimeError("Zu viele Kollisionen")

    def _get_text_to_keep(self, file_path: Path) -> str:
        """Ermittelt den Text, der im neuen Namen übernommen werden soll.
        Priorität: Dateiname (weil manuell im Finder geändert) vor
        ImageDescription (vom Programm gesetzter Text)."""
        if not self.keep_filename_text:
            return ""

        # 1. Priorität: Text aus aktuellem Dateinamen
        #    (weil der Nutzer ihn im Finder manuell geändert haben könnte)
        try:
            _, rest = split_filename_after_stamp(file_path.stem)
            if rest and not re.fullmatch(
                    r"(img|dsc|dscn|dscf|pxl|mvimg)[-_]?\d+",
                    rest, re.IGNORECASE):
                text_from_name = sanitize_text_for_filename(rest)
                if text_from_name:
                    return text_from_name
        except Exception:
            pass

        # 2. Fallback: ImageDescription (vom Programm gesetzter Text)
        try:
            desc = get_image_description(file_path)
            if desc:
                return sanitize_text_for_filename(desc)
        except Exception:
            pass

        return ""


    # -------------------------------------------------------------------------
    # Datum-Korrektur
    # -------------------------------------------------------------------------

    def fix_capture_dates(self,
                          set_year=None, set_month=None, set_day=None,
                          set_hour=None, set_minute=None, set_second=None,
                          add_hours=0, add_minutes=0, add_seconds=0,
                          also_fs=True, rename_mode="keep",
                          progress_callback=None,
                          eta_callback=None,
                          cancel_check=None,
                          file_callback=None) -> None:
        files, _ = self._scan_files()
        self.stats["total"] = len(files)
        logger.info(f"Gefundene Dateien: {len(files)}")
        if not files:
            logger.warning("Keine Dateien gefunden.")
            return

        exif_files = [f for f in files if f.suffix.lower() != AAE_EXT]
        aae_files = [f for f in files if f.suffix.lower() == AAE_EXT]
        logger.info(f"Mit EXIF: {len(exif_files)} | AAE (nur FS): "
                    f"{len(aae_files)}")

        t_start = time.monotonic()
        done = 0
        last_update = 0.0

        for i, f in enumerate(exif_files, 1):
            if cancel_check and cancel_check():
                logger.info("Abbruch durch Benutzer")
                break
            if file_callback:
                file_callback(i, len(exif_files))

            try:
                old_dt = exiftool_get_datetime_original(f)
            except Exception as e:
                logger.error(f"  ✗ {f.name}: Lesefehler: {e}")
                self.stats["errors"] += 1
                continue

            if old_dt is None:
                logger.warning(f"  ⊘ {f.name}: kein DateTimeOriginal — "
                               f"übersprungen")
                self.stats["skipped_no_date"] += 1
                if self.run_logger:
                    self.run_logger.add_failure("no_date", {
                        "path": str(f),
                        "message": "Kein DateTimeOriginal",
                    })
                continue

            new_dt, err = apply_datetime_correction(
                old_dt,
                set_year=set_year, set_month=set_month, set_day=set_day,
                set_hour=set_hour, set_minute=set_minute,
                set_second=set_second,
                add_hours=add_hours, add_minutes=add_minutes,
                add_seconds=add_seconds,
            )

            if err:
                logger.warning(f"  ✗ {f.name}: {err} — übersprungen")
                self.stats["errors"] += 1
                if self.run_logger:
                    self.run_logger.add_failure("error", {
                        "path": str(f), "message": err,
                    })
                continue

            try:
                fmtime = datetime.fromtimestamp(f.stat().st_mtime)
            except Exception:
                fmtime = None
            warn = suspicious_date_warning(new_dt, fmtime)
            if warn:
                logger.warning(f"  ⚠ {f.name}: {warn}")

            new_path = self._build_renamed(f, new_dt, rename_mode)
            will_rename = (new_path != f)
            if will_rename and new_path.exists():
                counter = 1
                while new_path.exists():
                    new_path = new_path.with_name(
                        f"{new_path.stem}_{counter:03d}{new_path.suffix}")
                    counter += 1

            if self.dry_run:
                logger.info(
                    f"  [DRY-RUN]\n"
                    f"     Datei:  {f.name}\n"
                    f"     Alt:    {old_dt:%Y-%m-%d %H:%M:%S}\n"
                    f"     Neu:    {new_dt:%Y-%m-%d %H:%M:%S}"
                    + (f"\n     Umbenennen zu: {new_path.name}"
                       if will_rename else ""))
                logger.info("-" * 60)
            else:
                ok, msg = exiftool_set_datetime_original(
                    f, new_dt, also_fs_timestamps=also_fs)
                if ok:
                    final_path = f
                    if will_rename:
                        try:
                            os.rename(str(f), str(new_path))
                            final_path = new_path
                            msg += f"; umbenannt zu {new_path.name}"
                            if self.run_logger:
                                self.run_logger.undo(
                                    "rename_date", str(f),
                                    str(new_path))
                        except Exception as e:
                            msg += f"; Umbenennen-Fehler: {e}"
                            self.stats["errors"] += 1
                            if self.run_logger:
                                self.run_logger.add_failure("error", {
                                    "path": str(f),
                                    "message": f"Umbenennen: {e}",
                                })
                    logger.info(
                        f"  ✓ Datum korrigiert\n"
                        f"     Datei:  {final_path.name}\n"
                        f"     Alt:    {old_dt:%Y-%m-%d %H:%M:%S}\n"
                        f"     Neu:    {new_dt:%Y-%m-%d %H:%M:%S}\n"
                        f"     Info:   {msg}")
                    logger.info("-" * 60)
                    if self.run_logger:
                        self.run_logger.undo(
                            "fix_date", str(final_path),
                            dt=new_dt.isoformat(),
                            old_dt=old_dt.isoformat())
                else:
                    logger.error(f"  ✗ {f.name}: {msg}")
                    self.stats["errors"] += 1
                    if self.run_logger:
                        self.run_logger.add_failure("error", {
                            "path": str(f), "message": msg,
                        })

            done += 1
            self.stats["processed"] = done
            now = time.monotonic()
            if now - last_update >= 0.1 or i == len(exif_files):
                last_update = now
                elapsed = now - t_start
                rate = done / elapsed if elapsed > 0 else 0
                remaining = (len(exif_files) - done) / rate if rate > 0 else 0
                if progress_callback:
                    progress_callback(int(done / len(exif_files) * 100))
                if eta_callback:
                    eta_callback(done, len(exif_files), elapsed,
                                 rate, remaining)

        for a in aae_files:
            if cancel_check and cancel_check():
                break
            try:
                fmtime = datetime.fromtimestamp(a.stat().st_mtime)
            except Exception:
                continue
            new_dt, err = apply_datetime_correction(
                fmtime,
                set_year=set_year, set_month=set_month, set_day=set_day,
                set_hour=set_hour, set_minute=set_minute,
                set_second=set_second,
                add_hours=add_hours, add_minutes=add_minutes,
                add_seconds=add_seconds,
            )
            if err:
                continue
            if self.dry_run:
                logger.info(f"  [DRY-RUN] {a.name} (AAE, nur FS): "
                            f"{fmtime:%Y-%m-%d %H:%M:%S} → "
                            f"{new_dt:%Y-%m-%d %H:%M:%S}")
            else:
                try:
                    MacTimestampSetter.set_timestamps(a, new_dt)
                    logger.info(f"  ✓ {a.name} (AAE, nur FS): "
                                f"{fmtime:%Y-%m-%d %H:%M:%S} → "
                                f"{new_dt:%Y-%m-%d %H:%M:%S}")
                    if self.run_logger:
                        self.run_logger.undo("touch", str(a),
                                             dt=new_dt.isoformat())
                except Exception as e:
                    logger.warning(f"  ✗ {a.name}: {e}")

    def _build_renamed(self, original_path: Path, new_dt: datetime,
                       rename_mode: str) -> Path:
        if rename_mode == "keep":
            return original_path
        stem = original_path.stem
        suffix = original_path.suffix
        new_stamp = new_dt.strftime("%Y-%m-%d_%H%M%S")
        m = _STANDARD_STAMP_RE.match(stem)
        if rename_mode == "replace":
            if m:
                new_stem = new_stamp + stem[m.end():]
            else:
                new_stem = f"{new_stamp}_{stem}"
        elif rename_mode == "prefix":
            new_stem = f"{new_stamp}_{stem}"
        elif rename_mode == "suffix":
            new_stem = f"{stem}_{new_stamp}"
        else:
            return original_path
        return original_path.with_name(f"{new_stem}{suffix}")

    # -------------------------------------------------------------------------
    # Text/Ort nach Zeitstempel setzen
    # -------------------------------------------------------------------------

    def add_text_to_names(self, text: str,
                          keep_old_text: bool = False,
                          keep_meaningful_original: bool = False,
                          keep_filename_text: bool = True,
                          progress_callback=None,
                          eta_callback=None,
                          cancel_check=None,
                          file_callback=None) -> None:
        """Setzt einen Text nach dem Zeitstempel.

        Verhalten:
          - Datei hat Standard-Zeitstempel: Text wird nach Zeitstempel
            gesetzt, alles davor (alter Text, Originalname) wird nach
            den Regeln behandelt.
          - Datei hat keinen Standard-Zeitstempel: wird übersprungen
            (mit Warnung), weil der Zeitstempel fehlt.
        """
        sanitized = sanitize_text_for_filename(text)
        if not sanitized:
            logger.warning("Leerer Text — nichts zu tun.")
            return

        logger.info(f"Text für Dateinamen: '{sanitized}'")
        logger.info(f"Alten Text behalten: "
                    f"{'JA' if keep_old_text else 'NEIN'}")
        logger.info(f"Originalnamen anzeigen (falls sinnvoll): "
                    f"{'JA' if keep_meaningful_original else 'NEIN'}")

        files, _ = self._scan_files()
        self.stats["total"] = len(files)
        logger.info(f"Gefundene Dateien: {len(files)}")
        if not files:
            logger.warning("Keine Dateien gefunden.")
            return

        t_start = time.monotonic()
        done = 0
        last_update = 0.0

        for i, f in enumerate(files, 1):
            if cancel_check and cancel_check():
                logger.info("Abbruch durch Benutzer")
                break
            if file_callback:
                file_callback(i, len(files))

            try:
                self._process_add_text(
                    f, sanitized, keep_old_text,
                    keep_meaningful_original, keep_filename_text,
                )
            except Exception as e:
                logger.error(f"  ✗ {f.name}: {e}")
                self.stats["errors"] += 1
                if self.run_logger:
                    self.run_logger.add_failure("error", {
                        "path": str(f), "message": str(e),
                    })

            done += 1
            self.stats["processed"] = done
            now = time.monotonic()
            if now - last_update >= 0.1 or i == len(files):
                last_update = now
                elapsed = now - t_start
                rate = done / elapsed if elapsed > 0 else 0
                remaining = (len(files) - done) / rate if rate > 0 else 0
                if progress_callback:
                    progress_callback(int(done / len(files) * 100))
                if eta_callback:
                    eta_callback(done, len(files), elapsed, rate, remaining)

    def _process_add_text(self, f: Path, text: str,
                          keep_old_text: bool,
                          keep_meaningful_original: bool,
                          keep_filename_text: bool = True):
        # Zeitstempel in beliebigem Format erkennen
        dt = parse_any_standard_stamp(f.name)
        if dt is None:
            self.stats["text_skipped"] += 1
            logger.warning(
                f"  ⊘ Übersprungen\n"
                f"     Datei: {f.name}\n"
                f"     Grund: kein Zeitstempel erkannt")
            logger.info("-" * 60)
            if self.run_logger:
                self.run_logger.add_failure("no_date", {
                    "path": str(f),
                    "message": "Kein Zeitstempel im Dateinamen erkennbar",
                })
            return

        # Normalisierter Zeitstempel im Standardformat
        stamp = dt.strftime("%Y-%m-%d_%H%M%S")
        _, rest = split_filename_after_stamp(f.stem)

        # Alter Text: aus ImageDescription, Dateiname oder beidem
        old_text = ""
        if keep_old_text or keep_filename_text:
            old_text = self._get_text_to_keep(f)

        # Fallback: Text aus aktuellem Dateinamen, nur wenn erlaubt
        if not old_text and keep_filename_text:
            try:
                _, rest2 = split_filename_after_stamp(f.stem)
                if rest2 and not re.fullmatch(
                        r"(img|dsc|dscn|dscf|pxl|mvimg)[-_]?\d+",
                        rest2, re.IGNORECASE):
                    old_text = sanitize_text_for_filename(rest2)
            except Exception:
                pass

        # Originalname
        original_name = get_xptitle(f)
        if original_name:
            original_name = sanitize_text_for_filename(original_name)
        else:
            original_name = ""

        # XPTitle nachziehen, falls noch nicht vorhanden
        if not original_name:
            original_name = rest if rest and not old_text else ""
            if not original_name:
                original_name = f.stem
            if not self.dry_run:
                set_metadata_fields(f, xptitle=original_name)

        # Neuen Namen zusammenbauen
        parts = [stamp]
        if text:
            parts.append(text)
        if keep_old_text and old_text and old_text != text:
            parts.append(old_text)

        # Originalname nur anhängen, wenn sinnvoll
        if keep_meaningful_original:
            if (original_name
                    and is_meaningful_original_name(original_name)
                    and not re.fullmatch(r"[\d\-_. ]+", original_name)):
                parts.append(original_name)

        new_stem = " ".join(parts)
        new_stem = re.sub(r"\s+", " ", new_stem).strip()

        # Länge begrenzen (macOS: max. 255 Zeichen)
        max_name_len = 200 - len(f.suffix)
        if len(new_stem) > max_name_len:
            new_stem = new_stem[:max_name_len].rstrip()

        new_path = f.with_name(f"{new_stem}{f.suffix}")

        if new_path == f:
            logger.info(
                f"  = Unverändert\n"
                f"     Datei: {f.name}")
            logger.info("-" * 60)
            return

        # Kollisionsbehandlung
        if new_path.exists():
            counter = 1
            while new_path.exists() and counter < 999:
                suffix = f"_{counter:03d}"
                stem_short = new_stem[:200 - len(suffix) - len(f.suffix)]
                new_path = f.with_name(f"{stem_short}{suffix}{f.suffix}")
                counter += 1

        # Zeitstempel-Umformatierung erkennen
        stamp_changed = not f.stem.startswith(stamp)

        if self.dry_run:
            logger.info(
                f"  [DRY-RUN] Umbenennen\n"
                f"     Original: {f.name}\n"
                f"     Neu:      {new_path.name}"
                + ("  (Zeitstempel normalisiert)" if stamp_changed else ""))
            logger.info("-" * 60)
        else:
            try:
                # Zeitstempel für späteres Zurücksetzen merken
                capture_dt = dt

                os.rename(str(f), str(new_path))

                # ImageDescription setzen
                set_metadata_fields(new_path, image_description=text)

                # Zeitstempel wieder auf Aufnahmezeitpunkt setzen
                _, ts_msg = MacTimestampSetter.set_timestamps(
                    new_path, capture_dt)

                logger.info(
                    f"  ✓ Umbenannt\n"
                    f"     Original: {f.name}\n"
                    f"     Neu:      {new_path.name}"
                    + ("  (Zeitstempel normalisiert)" if stamp_changed else "")
                    + f"\n     Zeitstempel: {ts_msg}")
                logger.info("-" * 60)

                self.stats["text_set"] += 1
                if self.run_logger:
                    self.run_logger.undo("rename_date", str(f),
                                         str(new_path))
            except Exception as e:
                logger.error(
                    f"  ✗ Fehler beim Umbenennen\n"
                    f"     Datei: {f.name}\n"
                    f"     Grund: {e}")
                logger.info("-" * 60)
                self.stats["errors"] += 1
                if self.run_logger:
                    self.run_logger.add_failure("error", {
                        "path": str(f),
                        "message": f"Umbenennen: {e}",
                    })


    # -------------------------------------------------------------------------
    # Metadaten bereinigen
    # -------------------------------------------------------------------------

    def clean_metadata_fields(self,
                              clean_xptitle: bool = True,
                              clean_image_description: bool = False,
                              progress_callback=None,
                              eta_callback=None,
                              cancel_check=None,
                              file_callback=None) -> None:
        """Löscht ausgewählte Metadaten-Felder."""
        files, _ = self._scan_files()
        self.stats["total"] = len(files)
        logger.info(f"Gefundene Dateien: {len(files)}")
        if not files:
            logger.warning("Keine Dateien gefunden.")
            return

        logger.info(
            f"Bereinige: XPTitle={'JA' if clean_xptitle else 'NEIN'}, "
            f"ImageDescription={'JA' if clean_image_description else 'NEIN'}")

        t_start = time.monotonic()
        done = 0
        last_update = 0.0
        count_xp = 0
        count_img = 0
        count_none = 0
        count_errors = 0

        for i, f in enumerate(files, 1):
            if cancel_check and cancel_check():
                logger.info("Abbruch durch Benutzer")
                break
            if file_callback:
                file_callback(i, len(files))

            try:
                changed = self._clean_one_file(
                    f, clean_xptitle, clean_image_description)
                if changed is None:
                    count_errors += 1
                elif changed == "none":
                    count_none += 1
                else:
                    count_xp += changed.get("xptitle", 0)
                    count_img += changed.get("image_description", 0)
            except Exception as e:
                logger.error(f"  ✗ {f.name}: {e}")
                count_errors += 1
                if self.run_logger:
                    self.run_logger.add_failure("error", {
                        "path": str(f), "message": str(e),
                    })

            done += 1
            self.stats["processed"] = done
            now = time.monotonic()
            if now - last_update >= 0.1 or i == len(files):
                last_update = now
                elapsed = now - t_start
                rate = done / elapsed if elapsed > 0 else 0
                remaining = (len(files) - done) / rate if rate > 0 else 0
                if progress_callback:
                    progress_callback(int(done / len(files) * 100))
                if eta_callback:
                    eta_callback(done, len(files), elapsed, rate, remaining)

        self.stats["cleaned_xptitle"] = count_xp
        self.stats["cleaned_image_description"] = count_img
        self.stats["cleaned_none"] = count_none
        self.stats["errors"] = count_errors

        logger.info("-" * 60)
        logger.info(f"Bereinigung abgeschlossen:")
        logger.info(f"  XPTitle gelöscht:          {count_xp}")
        logger.info(f"  ImageDescription gelöscht: {count_img}")
        logger.info(f"  Unverändert:               {count_none}")
        logger.info(f"  Fehler:                    {count_errors}")

    def _clean_one_file(self, f: Path,
                        clean_xp: bool, clean_img: bool):
        """Bereinigt eine einzelne Datei. Gibt ein dict mit Zählern zurück
        oder 'none' wenn nichts zu tun war, oder None bei Fehler."""
        to_delete = []   # (field_name, old_value)
        if clean_xp:
            old = get_xptitle(f)
            if old:
                to_delete.append(("XPTitle", old))
        if clean_img:
            old = get_image_description(f)
            if old:
                to_delete.append(("ImageDescription", old))

        if not to_delete:
            return "none"

        counts = {"xptitle": 0, "image_description": 0}

        if self.dry_run:
            for field_name, old in to_delete:
                logger.info(
                    f"  [DRY-RUN] {f.name}: würde {field_name} "
                    f"löschen (war: „{old}")
                if field_name == "XPTitle":
                    counts["xptitle"] = 1
                else:
                    counts["image_description"] = 1
            return counts

        # Alte Werte für Undo speichern
        old_xp = None
        old_img = None
        for field_name, old in to_delete:
            if field_name == "XPTitle":
                old_xp = old
            elif field_name == "ImageDescription":
                old_img = old

        # Löschen via exiftool
        args = [EXIFTOOL_BIN, "-P", "-overwrite_original"]
        if clean_xp:
            args.append("-XPTitle=")
        if clean_img:
            args.append("-ImageDescription=")
        args.append(str(f))

        try:
            r = subprocess.run(args, capture_output=True, text=True,
                               timeout=30, errors="replace")
            if r.returncode != 0:
                raise RuntimeError(
                    f"exiftool-Fehler: {r.stderr.strip()[:200]}")
        except Exception as e:
            logger.error(f"  ✗ {f.name}: {e}")
            return None

        # Log
        parts = []
        for field_name, old in to_delete:
            parts.append(f"{field_name} gelöscht (war: „{old}")
            if field_name == "XPTitle":
                counts["xptitle"] = 1
            else:
                counts["image_description"] = 1
        logger.info(f"  ✓ {f.name}: {'; '.join(parts)}")

        # Undo-Eintrag
        if self.run_logger:
            self.run_logger.undo(
                "restore_meta", str(f),
                dt=None,
                old_xptitle=old_xp,
                old_image_description=old_img)

        return counts

# -----------------------------------------------------------------------------
# Organizer-Worker
# -----------------------------------------------------------------------------

class OrganizerWorker(QThread):
    progress_updated = Signal(int)
    eta_updated      = Signal(str)
    file_counter     = Signal(int, int)
    phase_updated    = Signal(str)
    log_message      = Signal(str)
    finished         = Signal(dict)

    def __init__(self, source_dir, dest_dir, dry_run, use_fs_fallback,
                 use_filename_parser, use_live_photo, use_aae, remove_lp_movs,
                 dup_action, dup_folder, mode, internal_source: bool,
                 fix_data: Optional[Dict[str, Any]] = None,
                 text_data: Optional[Dict[str, Any]] = None,
                 clean_data: Optional[Dict[str, Any]] = None,
                 keep_filename_text: bool = True):
        super().__init__()
        self.source_dir = source_dir
        self.dest_dir = dest_dir
        self.dry_run = dry_run
        self.use_fs_fallback = use_fs_fallback
        self.use_filename_parser = use_filename_parser
        self.use_live_photo = use_live_photo
        self.use_aae = use_aae
        self.remove_lp_movs = remove_lp_movs
        self.dup_action = dup_action
        self.dup_folder = dup_folder
        self.mode = mode
        self.internal_source = internal_source
        self.fix_data = fix_data or {}
        self.text_data = text_data or {}
        self.clean_data = clean_data or {}
        self.keep_filename_text = keep_filename_text
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        handlers = []
        run_logger = None
        start_time = datetime.now()

        # Crash-Log vor jedem Lauf neu schreiben
        try:
            _reset_crash_log()
        except Exception:
            pass

        try:
            run_logger = RunLogger(APP_DATA_DIR, dry_run=self.dry_run)

            class GuiHandler(logging.Handler):
                def __init__(self, sig):
                    super().__init__()
                    self.sig = sig
                def emit(self, record):
                    self.sig.emit(self.format(record))

            class FileHandler(logging.Handler):
                def __init__(self, rl):
                    super().__init__()
                    self.rl = rl
                def emit(self, record):
                    self.rl.main(self.format(record))

            gh = GuiHandler(self.log_message)
            gh.setFormatter(logging.Formatter("%(message)s"))
            fh = FileHandler(run_logger)
            fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
            logger.addHandler(gh)
            logger.addHandler(fh)
            handlers = [gh, fh]
            logger.setLevel(logging.INFO)

            exif_ok, exif_ver = check_exiftool()
            if exif_ok:
                self.log_message.emit(f"✓ exiftool: v{exif_ver}")
            else:
                self.log_message.emit(f"✗ exiftool fehlt: {exif_ver}")

            if SEND2TRASH_AVAILABLE:
                self.log_message.emit("✓ send2trash: verfügbar")
            else:
                self.log_message.emit("⚠️ send2trash NICHT installiert")

            self.log_message.emit(
                f"Quelle intern: {'JA' if self.internal_source else 'NEIN (extern)'}")
            self.log_message.emit(f"Modus: {self.mode} | "
                                  f"{'DRY-RUN' if self.dry_run else 'ECHTER LAUF'}")
            self.log_message.emit(f"AAE mit archivieren: "
                                  f"{'JA' if self.use_aae else 'NEIN'}")
            self.log_message.emit(f"Quelle: {self.source_dir}")
            self.log_message.emit(f"Ziel:   {self.dest_dir}")
            self.log_message.emit(f"Start:  {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
            self.log_message.emit("-" * 60)

            organizer = FileOrganizer(
                self.source_dir, self.dest_dir, self.dry_run,
                self.use_fs_fallback, self.use_filename_parser,
                self.use_live_photo, self.use_aae, self.remove_lp_movs,
                self.dup_action, self.dup_folder, self.mode, run_logger,
                keep_filename_text=self.keep_filename_text,
            )

            def on_phase(msg, cur, total):
                self.phase_updated.emit(f"Phase {cur}/{total}: {msg}")

            def on_eta(processed, total, elapsed, rate, remaining):
                pct = int(processed / total * 100) if total > 0 else 0
                self.eta_updated.emit(
                    f"Restzeit: ca. {format_duration(remaining)}  |  "
                    f"Verstrichen: {format_duration(elapsed)}  |  "
                    f"Rate: {rate:.1f} Dateien/s  |  "
                    f"{processed}/{total} ({pct}%)"
                )

            def on_file(current, total):
                self.file_counter.emit(current, total)

            if self.mode == "fix_capture_date":
                fd = self.fix_data
                logger.info(
                    "Korrektur-Modus: "
                    f"Jahr={fd.get('set_year')} "
                    f"Monat={fd.get('set_month')} "
                    f"Tag={fd.get('set_day')} "
                    f"Stunde={fd.get('set_hour')} "
                    f"Minute={fd.get('set_minute')} "
                    f"Sekunde={fd.get('set_second')} "
                    f"| Verschiebung: {fd.get('add_hours', 0):+d}h "
                    f"{fd.get('add_minutes', 0):+d}min "
                    f"{fd.get('add_seconds', 0):+d}s "
                    f"| FS-Zeitstempel: "
                    f"{'JA' if fd.get('also_fs', True) else 'NEIN'} "
                    f"| Dateiname: {fd.get('rename_mode', 'keep')}")
                logger.info("-" * 60)
                organizer.fix_capture_dates(
                    set_year=fd.get("set_year"),
                    set_month=fd.get("set_month"),
                    set_day=fd.get("set_day"),
                    set_hour=fd.get("set_hour"),
                    set_minute=fd.get("set_minute"),
                    set_second=fd.get("set_second"),
                    add_hours=fd.get("add_hours", 0),
                    add_minutes=fd.get("add_minutes", 0),
                    add_seconds=fd.get("add_seconds", 0),
                    also_fs=fd.get("also_fs", True),
                    rename_mode=fd.get("rename_mode", "keep"),
                    progress_callback=self.progress_updated.emit,
                    eta_callback=on_eta,
                    cancel_check=lambda: self._cancel,
                    file_callback=on_file,
                )
            elif self.mode == "add_text":
                td = self.text_data
                logger.info(
                    f"Text-Modus: Text='{td.get('text', '')}' "
                    f"| Alten Text behalten: "
                    f"{'JA' if td.get('keep_old_text', False) else 'NEIN'} "
                    f"| Originalname anzeigen: "
                    f"{'JA' if td.get('keep_meaningful_original', False) else 'NEIN'}")
                logger.info("-" * 60)
                organizer.add_text_to_names(
                    text=td.get("text", ""),
                    keep_old_text=td.get("keep_old_text", False),
                    keep_meaningful_original=td.get(
                        "keep_meaningful_original", False),
                    keep_filename_text=td.get("keep_filename_text", True),
                    progress_callback=self.progress_updated.emit,
                    eta_callback=on_eta,
                    cancel_check=lambda: self._cancel,
                    file_callback=on_file,
                )
            elif self.mode == "clean_metadata":
                cd = self.clean_data
                logger.info(
                    f"Bereinigungs-Modus: "
                    f"XPTitle={'JA' if cd.get('clean_xptitle', True) else 'NEIN'} "
                    f"| ImageDescription="
                    f"{'JA' if cd.get('clean_image_description', False) else 'NEIN'}")
                logger.info("-" * 60)
                organizer.clean_metadata_fields(
                    clean_xptitle=cd.get("clean_xptitle", True),
                    clean_image_description=cd.get(
                        "clean_image_description", False),
                    progress_callback=self.progress_updated.emit,
                    eta_callback=on_eta,
                    cancel_check=lambda: self._cancel,
                    file_callback=on_file,
                )
            else:
                organizer.scan_and_process(
                    progress_callback=self.progress_updated.emit,
                    phase_callback=on_phase,
                    eta_callback=on_eta,
                    cancel_check=lambda: self._cancel,
                    file_callback=on_file,
                )
                organizer.scan_and_process(
                    progress_callback=self.progress_updated.emit,
                    phase_callback=on_phase,
                    eta_callback=on_eta,
                    cancel_check=lambda: self._cancel,
                    file_callback=on_file,
                )

            diagnostics = {}
            try:
                active_exts = set(SUPPORTED_EXTS)
                if self.use_aae:
                    active_exts.add(AAE_EXT)
                src_files_recursive = 0
                src_files_root = 0
                for f in self.source_dir.rglob("*"):
                    if f.is_file() and f.suffix.lower() in active_exts:
                        src_files_recursive += 1
                for f in self.source_dir.iterdir():
                    if f.is_file() and f.suffix.lower() in active_exts:
                        src_files_root += 1
                diagnostics["Dateien im Quellordner (rekursiv)"] = src_files_recursive
                diagnostics["Dateien direkt im Quellordner (Wurzel)"] = src_files_root
                if (self.mode == "organize"
                        and _norm(self.dest_dir) != _norm(self.source_dir)):
                    dst_count = sum(1 for f in self.dest_dir.rglob("*")
                                    if f.is_file() and f.suffix.lower() in active_exts)
                    diagnostics["Dateien im Zielordner (rekursiv)"] = dst_count
            except Exception as e:
                diagnostics["Diagnose-Fehler"] = str(e)

            end_time = datetime.now()
            duration = end_time - start_time
            s = organizer.stats

            self.log_message.emit("-" * 60)
            self.log_message.emit("Fertig! Statistik:")
            for k, v in s.items():
                self.log_message.emit(f"  {k:34s}: {v}")
            self.log_message.emit(f"  {'Start':34s}: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
            self.log_message.emit(f"  {'Ende':34s}: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
            self.log_message.emit(f"  {'Dauer':34s}: {format_duration(duration.total_seconds())}")
            if diagnostics:
                self.log_message.emit("Diagnose nach Lauf:")
                for k, v in diagnostics.items():
                    self.log_message.emit(f"  {k:34s}: {v}")

            config = {
                "source": str(self.source_dir), "dest": str(self.dest_dir),
                "dry_run": self.dry_run, "fs_fallback": self.use_fs_fallback,
                "use_filename": self.use_filename_parser,
                "use_livephoto": self.use_live_photo,
                "use_aae": self.use_aae,
                "remove_lp_mov": self.remove_lp_movs,
                "dup_action": self.dup_action, "mode": self.mode,
                "internal": self.internal_source,
            }
            run_logger.finalize(s, config, start_time, end_time, diagnostics)
            self.log_message.emit(f"Run-Log:    {run_logger.main_path}")
            self.log_message.emit(f"Fehler-Log: {run_logger.failed_path}")
            if run_logger.undo_path:
                self.log_message.emit(f"Undo-Log:   {run_logger.undo_path}")

            self.finished.emit({
                **s, "duration": format_duration(duration.total_seconds()),
                "run_log": str(run_logger.main_path),
                "failed_log": str(run_logger.failed_path),
                "undo_log": str(run_logger.undo_path) if run_logger.undo_path else None,
                "diagnostics": diagnostics,
            })
        except Exception as e:
            logger.exception("Fataler Fehler")
            self.log_message.emit(f"FATALER FEHLER: {e}")
            self.finished.emit({"error": str(e)})
        finally:
            for h in handlers:
                logger.removeHandler(h)
            if run_logger:
                run_logger.close()


# -----------------------------------------------------------------------------
# Undo-Worker
# -----------------------------------------------------------------------------

class UndoWorker(QThread):
    progress_updated = Signal(int)
    log_message = Signal(str)
    finished = Signal(dict)

    def __init__(self, undo_path: Path):
        super().__init__()
        self.undo_path = undo_path
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            _reset_crash_log()
        except Exception:
            pass
        try:
            entries = []
            with open(self.undo_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

            total = len(entries)
            self.log_message.emit(
                f"Undo: {total} Aktionen aus {self.undo_path.name}")
            if total == 0:
                self.log_message.emit("Undo: Datei enthält keine Aktionen.")
                self.finished.emit(
                    {"total": 0, "ok": 0, "fail": 0, "skip": 0})
                return

            ok, fail, skip = 0, 0, 0
            for i, entry in enumerate(reversed(entries), 1):
                if self._cancel:
                    self.log_message.emit("Undo abgebrochen durch Benutzer.")
                    break
                action = entry.get("action")
                src = Path(entry["source"]) if entry.get("source") else None
                dst = Path(entry["target"]) if entry.get("target") else None

                if action in ("move", "dup_move"):
                    if dst and src and dst.exists() and not src.exists():
                        try:
                            src.parent.mkdir(parents=True, exist_ok=True)
                            shutil.move(str(dst), str(src))
                            ok += 1
                        except Exception as e:
                            self.log_message.emit(
                                f"  ✗ {dst.name} → {src}: {e}")
                            fail += 1
                    else:
                        skip += 1
                elif action == "fix_date":
                    old_str = entry.get("old_datetime")
                    if not old_str or not src or not src.exists():
                        skip += 1
                    else:
                        try:
                            old_dt = datetime.fromisoformat(old_str)
                            ok2, msg = exiftool_set_datetime_original(
                                src, old_dt, also_fs_timestamps=True)
                            if ok2:
                                ok += 1
                                self.log_message.emit(
                                    f"  ↺ {src.name}: zurück auf "
                                    f"{old_dt:%Y-%m-%d %H:%M:%S}")
                            else:
                                fail += 1
                                self.log_message.emit(
                                    f"  ✗ {src.name}: {msg}")
                        except Exception as e:
                            fail += 1
                            self.log_message.emit(
                                f"  ✗ {src.name}: {e}")
                elif action == "rename_date":
                    if src and dst and dst.exists() and not src.exists():
                        try:
                            src.parent.mkdir(parents=True, exist_ok=True)
                            os.rename(str(dst), str(src))
                            ok += 1
                            self.log_message.emit(
                                f"  ↺ {dst.name} → {src.name}")
                        except Exception as e:
                            fail += 1
                            self.log_message.emit(
                                f"  ✗ {dst.name} → {src.name}: {e}")
                    else:
                        skip += 1
                elif action == "restore_meta":
                    if not src or not src.exists():
                        skip += 1
                        continue
                    old_xp = entry.get("old_xptitle")
                    old_img = entry.get("old_image_description")
                    if old_xp is None and old_img is None:
                        skip += 1
                        continue
                    try:
                        args = [EXIFTOOL_BIN, "-P", "-overwrite_original"]
                        if old_xp is not None:
                            args.append(f"-XPTitle={old_xp}")
                        if old_img is not None:
                            args.append(f"-ImageDescription={old_img}")
                        args.append(str(src))
                        r = subprocess.run(
                            args, capture_output=True, text=True,
                            timeout=30, errors="replace")
                        if r.returncode == 0:
                            ok += 1
                            restored = []
                            if old_xp is not None:
                                restored.append("XPTitle")
                            if old_img is not None:
                                restored.append("ImageDescription")
                            self.log_message.emit(
                                f"  ↺ {src.name}: "
                                f"{', '.join(restored)} wiederhergestellt")
                        else:
                            fail += 1
                            self.log_message.emit(
                                f"  ✗ {src.name}: "
                                f"{r.stderr.strip()[:150]}")
                    except Exception as e:
                        fail += 1
                        self.log_message.emit(
                            f"  ✗ {src.name}: {e}")
                    if src and dst and dst.exists() and not src.exists():
                        try:
                            src.parent.mkdir(parents=True, exist_ok=True)
                            os.rename(str(dst), str(src))
                            ok += 1
                            self.log_message.emit(
                                f"  ↺ {dst.name} → {src.name}")
                        except Exception as e:
                            fail += 1
                            self.log_message.emit(
                                f"  ✗ {dst.name} → {src.name}: {e}")
                    else:
                        skip += 1
                elif action == "touch":
                    skip += 1
                elif action in ("dup_trash", "dup_delete"):
                    fail += 1

                if i % 10 == 0 or i == total:
                    self.progress_updated.emit(int(i / total * 100))
                    if i % 100 == 0 or i == total:
                        self.log_message.emit(
                            f"  {i}/{total} (OK: {ok}, Fehler: {fail}, "
                            f"Skip: {skip})")

            self.finished.emit(
                {"total": total, "ok": ok, "fail": fail, "skip": skip})
        except Exception as e:
            logger.exception("Undo-Fehler")
            self.log_message.emit(f"Undo-FATALER FEHLER: {e}")
            self.finished.emit({"error": str(e)})


# -----------------------------------------------------------------------------
# CHECK-DATE-Worker
# -----------------------------------------------------------------------------

class CheckDatesWorker(QThread):
    """Prüft alle Dateien im Quellordner auf verdächtige Datumswerte."""
    progress = Signal(int, int)          # (current, total)
    log_line = Signal(str)               # für Log-Fenster
    finished_with = Signal(dict)         # {total, suspicious, results}

    def __init__(self, source_dir: Path):
        super().__init__()
        self.source_dir = source_dir
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            files = []
            for p in sorted(self.source_dir.rglob("*")):
                if not p.is_file():
                    continue
                if p.suffix.lower() not in SUPPORTED_EXTS:
                    continue
                if p.suffix.lower() == AAE_EXT:
                    continue
                files.append(p)

            total = len(files)
            suspicious = []
            done = 0

            for i, p in enumerate(files, 1):
                if self._cancel:
                    break
                try:
                    dt = exiftool_get_datetime_original(p)
                except Exception:
                    dt = None
                if dt is None:
                    suspicious.append((str(p), "kein DateTimeOriginal"))
                else:
                    try:
                        fmtime = datetime.fromtimestamp(p.stat().st_mtime)
                    except Exception:
                        fmtime = None
                    warn = suspicious_date_warning(dt, fmtime)
                    if warn:
                        suspicious.append((str(p), warn))
                done = i
                self.progress.emit(done, total)

            self.finished_with.emit({
                "total": total,
                "suspicious": suspicious,
                "cancelled": self._cancel,
                "checked": done,
            })
        except Exception as e:
            self.finished_with.emit({
                "error": str(e),
            })


# -----------------------------------------------------------------------------
# Wiederverwendbarer Message-Dialog mit deutschen Buttons
# -----------------------------------------------------------------------------

class MessageDialog(QDialog):
    """Ersatz für QMessageBox mit deutschen Beschriftungen,
    einheitlicher Optik und Header-Bereich (Logo / Titel / Icon)."""

    BG       = "#1a1a1a"
    TEXT     = "#ffffff"
    BOX_BG   = "#2a2a2a"
    BOX_BORD = "#444444"
    ACCENT   = "#4ea1ff"
    BTN_OK   = "#2e9e4f"
    BTN_CANCEL = "#c0392b"
    FONT     = "Menlo, Monaco, monospace"

    INFO     = "info"
    WARN     = "warn"
    ERROR    = "error"
    QUESTION = "question"

    def __init__(self, parent, title: str, message: str,
                 kind: str = INFO,
                 buttons: str = "ok",
                 default: str = "ok",
                 show_progress: bool = False,
                 progress_indeterminate: bool = True,
                 cancel_callback=None,
                 log_path: Optional[Path] = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(520)
        self._result = None
        self._cancel_callback = cancel_callback
        self._msg_label = None
        self._progress = None
        self._log_path = log_path
        self._set_app_icon()
        self.setStyleSheet(self._build_stylesheet())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ---- HEADER (Logo / Titel / Icon) --------------------------
        header = HeaderWidget(
            title="PictureChronoSort",
            logo_file=LOGO_FILE,
            icon_file=RIGHT_ICON_FILE,
            logo_size=56,
            icon_size=42,
        )
        outer.addWidget(header, 0)

        # Trennlinie
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet(f"color: {self.BOX_BORD};")
        outer.addWidget(line, 0)

        # ---- Inhalt (mit Rand) -------------------------------------
        content = QWidget()
        content.setObjectName("msgContent")
        content.setStyleSheet(
            f"#msgContent {{ background-color: {self.BG}; }}")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        outer.addWidget(content, 1)

        # ---- Titelzeile mit Icon + Titel ---------------------------
        title_row = QHBoxLayout()
        icon_label = QLabel(self._icon_for_kind(kind))
        icon_label.setStyleSheet(
            f"font-size: 22pt; color: {self._color_for_kind(kind)};")
        title_row.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)

        title_lbl = QLabel(title)
        title_lbl.setWordWrap(True)
        title_lbl.setStyleSheet(
            f"font-family: {self.FONT}; font-size: 13pt; "
            f"font-weight: bold; color: {self.TEXT};")
        title_row.addWidget(title_lbl, 1, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(title_row)

        # ---- Nachricht (scrollbar, falls sehr lang) ----------------
        msg_area = QScrollArea()
        msg_area.setWidgetResizable(True)
        msg_area.setFrameShape(QFrame.NoFrame)
        msg_area.setMaximumHeight(320)
        msg_area.viewport().setObjectName("msgViewport")
        msg_area.viewport().setStyleSheet(
            f"#msgViewport {{ background-color: {self.BG}; }}")

        msg_widget = QWidget()
        msg_widget.setObjectName("msgWidget")
        msg_widget.setStyleSheet(
            f"#msgWidget {{ background-color: {self.BG}; }}")
        msg_l = QVBoxLayout(msg_widget)
        msg_l.setContentsMargins(0, 0, 0, 0)

        msg_lbl = QLabel(message)
        msg_lbl.setWordWrap(True)
        msg_lbl.setTextFormat(Qt.TextFormat.RichText)
        msg_lbl.setStyleSheet(
            f"font-family: {self.FONT}; font-size: 10.5pt; "
            f"color: {self.TEXT}; background: transparent;")
        msg_l.addWidget(msg_lbl)
        msg_area.setWidget(msg_widget)
        layout.addWidget(msg_area, 1)
        self._msg_label = msg_lbl

        # ---- Optionaler Fortschrittsbalken -------------------------
        if show_progress:
            self._progress = QProgressBar()
            if progress_indeterminate:
                self._progress.setRange(0, 0)
            else:
                self._progress.setRange(0, 100)
                self._progress.setValue(0)
            self._progress.setMaximumHeight(10)
            self._progress.setTextVisible(False)
            self._progress.setStyleSheet(
                "QProgressBar {"
                "  border: 1px solid #444444; border-radius: 3px;"
                f"  background: {self.BOX_BG};"
                "}"
                "QProgressBar::chunk {"
                "  background-color: #4ea1ff;"
                "  border-radius: 3px;"
                "}"
            )
            layout.addWidget(self._progress)

        # ---- Buttons -----------------------------------------------
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        self._buttons = {}
        if self._log_path is not None:
            self._add_button(btn_row, "log", "Log-Ordner öffnen",
                             self.ACCENT, False)
        if buttons == "ok":
            self._add_button(btn_row, "ok", "OK",
                             self.BTN_OK, default == "ok")
        elif buttons == "cancel":
            self._add_button(btn_row, "cancel", "Abbrechen",
                             self.BTN_CANCEL, default == "cancel")
        elif buttons == "okcancel":
            self._add_button(btn_row, "cancel", "Abbrechen",
                             self.BTN_CANCEL, default == "cancel")
            self._add_button(btn_row, "ok", "OK",
                             self.BTN_OK, default == "ok")
        elif buttons == "yesno":
            self._add_button(btn_row, "no", "Nein",
                             self.BTN_CANCEL, default == "no")
            self._add_button(btn_row, "yes", "Ja",
                             self.BTN_OK, default == "yes")
        elif buttons == "yesnocancel":
            self._add_button(btn_row, "cancel", "Abbrechen",
                             self.BTN_CANCEL, default == "cancel")
            self._add_button(btn_row, "no", "Nein",
                             self.BTN_CANCEL, default == "no")
            self._add_button(btn_row, "yes", "Ja",
                             self.BTN_OK, default == "yes")

        layout.addLayout(btn_row)

        for btn in self._buttons.values():
            btn.installEventFilter(self)

        # ---- Fokus-Reihenfolge und Enter-Verhalten -----------------
        self._tab_order = []
        for key in ("log", "cancel", "no", "yes", "ok"):
            if key in self._buttons:
                self._tab_order.append(self._buttons[key])
        for i, btn in enumerate(self._tab_order):
            next_btn = self._tab_order[(i + 1) % len(self._tab_order)]
            QWidget.setTabOrder(btn, next_btn)

        # # FocusFrames für alle Buttons erzeugen
        # for key, btn in self._buttons.items():
        #     self._focus_frames[key] = self._create_focus_frame(btn)

        # Default-Button finden und fokussieren
        default_btn = None
        for key, btn in self._buttons.items():
            if btn.isDefault():
                default_btn = btn
                break
        if default_btn is None and self._tab_order:
            default_btn = self._tab_order[0]
        if default_btn is not None:
            default_btn.setFocus()

    # ------------------------------------------------------------------
    def _set_app_icon(self):
        icon_path = SCRIPT_DIR / APP_ICON_FILE
        if icon_path.exists():
            icon = QIcon(str(icon_path))
            if not icon.isNull():
                self.setWindowIcon(icon)

    def _icon_for_kind(self, kind: str) -> str:
        return {
            "info": "ℹ️",
            "warn": "⚠️",
            "error": "❌",
            "question": "❓",
        }.get(kind, "ℹ️")

    def _color_for_kind(self, kind: str) -> str:
        return {
            "info": "#4ea1ff",
            "warn": "#e0a020",
            "error": "#e74c3c",
            "question": "#4ea1ff",
        }.get(kind, "#4ea1ff")

    def _add_button(self, row, key, label, color, is_default):
        btn = QPushButton(label)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setMinimumWidth(110)
        btn.setMinimumHeight(32)
        # Wichtig: autoDefault AUS, damit unser outline-Fokusrahmen
        # nicht von Qt's Default-Button-Mechanismus überlagert wird.
        btn.setAutoDefault(False)
        btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        btn.setStyleSheet(
            "QPushButton {"
            f"  color: {self.TEXT};"
            f"  background-color: {color};"
            "  border: none;"
            "  border-radius: 4px;"
            "  padding: 6px 18px;"
            "  font-family: Menlo, Monaco, monospace;"
            "  font-size: 10pt;"
            "  font-weight: bold;"
            "}"
            "QPushButton:hover {"
            f"  background-color: {self._lighten(color)};"
            "}"
            "QPushButton:pressed {"
            f"  background-color: {self._darken(color)};"
            "}"
            "QPushButton:focus {"
            "  border: 2px solid #ffffff;"
            "  border-radius: 4px;"
            f"  background-color: {self._lighten(color)};"
            "}"
        )
        btn.clicked.connect(lambda: self._on_button(key))
        if is_default:
            btn.setDefault(True)
        row.addWidget(btn)
        self._buttons[key] = btn

    @staticmethod
    def _lighten(hex_color: str, amount: int = 20) -> str:
        try:
            h = hex_color.lstrip("#")
            r = min(255, int(h[0:2], 16) + amount)
            g = min(255, int(h[2:4], 16) + amount)
            b = min(255, int(h[4:6], 16) + amount)
            return f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            return hex_color

    @staticmethod
    def _darken(hex_color: str, amount: int = 20) -> str:
        try:
            h = hex_color.lstrip("#")
            r = max(0, int(h[0:2], 16) - amount)
            g = max(0, int(h[2:4], 16) - amount)
            b = max(0, int(h[4:6], 16) - amount)
            return f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            return hex_color

    def _on_button(self, key: str):
        if key == "log":
            try:
                self._open_log_folder()
            except Exception as e:
                logger.warning(f"Log-Ordner öffnen fehlgeschlagen: {e}")
            return
        if key == "cancel" and self._cancel_callback is not None:
            try:
                self._cancel_callback()
            except Exception:
                pass
        self._result = key
        self.accept()

    def _open_log_folder(self):
        if self._log_path is None:
            return
        try:
            path = Path(self._log_path)
            if path.is_file():
                path = path.parent
            if path.exists():
                if sys.platform == "darwin":
                    subprocess.Popen(["open", str(path)])
                elif sys.platform == "win32":
                    os.startfile(str(path))
                else:
                    subprocess.Popen(["xdg-open", str(path)])
        except Exception as e:
            MessageDialog.show_error(self, "Log-Ordner öffnen",
                f"Fehler: {e}")

    def eventFilter(self, obj, event):
        """Fängt Pfeiltasten auf Buttons ab, bevor der Button sie verarbeitet."""
        if event.type() == QEvent.KeyPress:
            if obj in self._buttons.values():
                key = event.key()
                if key in (Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_Right, Qt.Key.Key_Down):
                    focused = obj
                    if focused in self._tab_order:
                        idx = self._tab_order.index(focused)
                        if key in (Qt.Key.Key_Right, Qt.Key.Key_Down):
                            new_idx = (idx + 1) % len(self._tab_order)
                        else:
                            new_idx = (idx - 1) % len(self._tab_order)
                        self._tab_order[new_idx].setFocus()
                    return True  # Event "gefressen" – Button sieht ihn nicht mehr
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        """Enter/Return löst den fokussierten Button aus."""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            focused = QApplication.focusWidget()
            if isinstance(focused, QPushButton) and focused in self._buttons.values():
                for key, btn in self._buttons.items():
                    if btn is focused:
                        self._on_button(key)
                        return
            for key, btn in self._buttons.items():
                if btn.isDefault():
                    self._on_button(key)
                    return
        super().keyPressEvent(event)

    def set_message(self, message: str):
        if self._msg_label is not None:
            self._msg_label.setText(message)

    def set_progress_value(self, value: int):
        if self._progress is not None:
            self._progress.setValue(value)

    def set_progress_indeterminate(self, on: bool = True):
        if self._progress is not None:
            if on:
                self._progress.setRange(0, 0)
            else:
                self._progress.setRange(0, 100)

    def _build_stylesheet(self) -> str:
        return f"""
            QDialog {{ background-color: {self.BG}; }}
            QLabel {{ color: {self.TEXT}; background: transparent; }}
            QScrollArea {{ background-color: {self.BG}; border: none; }}
            QScrollBar:vertical {{
                background: {self.BG}; width: 10px; margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: #444444; border-radius: 5px; min-height: 20px;
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{ height: 0; background: none; }}
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {{ background: none; }}
            QProgressBar {{
                border: 1px solid {self.BOX_BORD};
                border-radius: 3px;
                background: {self.BOX_BG};
                max-height: 10px;
            }}
            QProgressBar::chunk {{
                background-color: {self.ACCENT};
                border-radius: 3px;
            }}
        """

    @classmethod
    def show_info(cls, parent, title: str, message: str):
        dlg = cls(parent, title, message, cls.INFO, "ok")
        dlg.exec()

    @classmethod
    def show_warn(cls, parent, title: str, message: str):
        dlg = cls(parent, title, message, cls.WARN, "ok")
        dlg.exec()

    @classmethod
    def show_error(cls, parent, title: str, message: str):
        dlg = cls(parent, title, message, cls.ERROR, "ok")
        dlg.exec()

    @classmethod
    def ask_yesno(cls, parent, title: str, message: str,
                  default: str = "no") -> bool:
        dlg = cls(parent, title, message, cls.QUESTION, "yesno",
                  default=default)
        dlg.exec()
        return dlg._result == "yes"

    @classmethod
    def ask_okcancel(cls, parent, title: str, message: str,
                     default: str = "cancel") -> bool:
        dlg = cls(parent, title, message, cls.QUESTION, "okcancel",
                  default=default)
        dlg.exec()
        return dlg._result == "ok"

    @classmethod
    def ask_yesnocancel(cls, parent, title: str, message: str,
                        default: str = "cancel") -> str:
        dlg = cls(parent, title, message, cls.QUESTION, "yesnocancel",
                  default=default)
        dlg.exec()
        return dlg._result or "cancel"

def make_styled_buttons_row(parent_dialog, buttons_spec, ok_callback=None):
    """Erzeugt eine Zeile mit MessageDialog-Style-Buttons.

    buttons_spec: Liste von Tupeln (key, label, color_kind, is_default)
      key: 'ok' | 'cancel'
      color_kind: 'ok' | 'cancel'
      is_default: True/False

    Verbindet die Buttons automatisch mit accept() / reject().
    Gibt (row_layout, dict) zurück — dict mappt key → QPushButton.
    """
    BTN_OK = MessageDialog.BTN_OK
    BTN_CANCEL = MessageDialog.BTN_CANCEL
    TEXT = MessageDialog.TEXT
    BORDER = MessageDialog.BOX_BORD

    row = QHBoxLayout()
    row.addStretch(1)
    buttons = {}

    def _lighten(hex_color, amount=20):
        try:
            h = hex_color.lstrip("#")
            return "#{:02x}{:02x}{:02x}".format(
                min(255, int(h[0:2], 16) + amount),
                min(255, int(h[2:4], 16) + amount),
                min(255, int(h[4:6], 16) + amount),
            )
        except Exception:
            return hex_color

    def _darken(hex_color, amount=20):
        try:
            h = hex_color.lstrip("#")
            return "#{:02x}{:02x}{:02x}".format(
                max(0, int(h[0:2], 16) - amount),
                max(0, int(h[2:4], 16) - amount),
                max(0, int(h[4:6], 16) - amount),
            )
        except Exception:
            return hex_color

    for key, label, color_kind, is_default in buttons_spec:
        color = BTN_OK if color_kind == "ok" else BTN_CANCEL
        btn = QPushButton(label)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setMinimumWidth(110)
        btn.setMinimumHeight(32)
        btn.setStyleSheet(
            "QPushButton {"
            f"  color: {TEXT};"
            f"  background-color: {color};"
            "  border: none;"
            "  border-radius: 4px;"
            "  padding: 6px 18px;"
            "  font-family: Menlo, Monaco, monospace;"
            "  font-size: 10pt;"
            "  font-weight: bold;"
            "}"
            f"QPushButton:hover {{ background-color: {_lighten(color)}; }}"
            f"QPushButton:pressed {{ background-color: {_darken(color)}; }}"
            "QPushButton:focus {"
            "  border: 2px solid #ffffff;"
            f"  background-color: {_lighten(color)};"
            "}"
        )
        if key == "ok":
            if ok_callback is not None:
                btn.clicked.connect(ok_callback)
            else:
                btn.clicked.connect(parent_dialog.accept)
        elif key == "cancel":
            btn.clicked.connect(parent_dialog.reject)
        if is_default:
            btn.setDefault(True)
            btn.setAutoDefault(True)
        row.addWidget(btn)
        buttons[key] = btn

    return row, buttons


# -----------------------------------------------------------------------------
# Info-Dialog
# -----------------------------------------------------------------------------

APP_VERSION = "6.0"
APP_COPYRIGHT = "© 2026 BinhDiez"
GITHUB_REPO = "BinhDiez/PictureChronoSort"


class InfoDialog(QDialog):
    """Zeigt Version, Copyright, Lizenz und Drittanbieter-Komponenten."""

    BG       = "#1a1a1a"
    TEXT     = "#ffffff"
    BOX_BG   = "#0f0f0f"
    BOX_BORD = "#3a3a3a"
    ACCENT   = "#4ea1ff"
    FONT     = "Menlo, Monaco, monospace"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Info — PictureChronoSort")
        self.setMinimumSize(640, 720)
        self._set_app_icon()
        self.setStyleSheet(self._build_stylesheet())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Header
        header = HeaderWidget(
            title="PictureChronoSort",
            logo_file=LOGO_FILE,
            icon_file=RIGHT_ICON_FILE,
            logo_size=56,
            icon_size=42,
        )
        outer.addWidget(header, 0)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet(f"color: {self.BOX_BORD};")
        outer.addWidget(line, 0)

        # Scrollbarer Inhalt
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setObjectName("infoViewport")
        scroll.viewport().setStyleSheet(
            "#infoViewport { background-color: #1a1a1a; }")

        content = QWidget()
        content.setObjectName("infoContent")
        content.setStyleSheet(
            "#infoContent { background-color: #1a1a1a; }")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Version + Copyright
        version_label = QLabel(
            f"<b>Version:</b> {APP_VERSION}")
        version_label.setStyleSheet(
            f"font-family: {self.FONT}; font-size: 12pt; "
            f"color: {self.TEXT};")
        layout.addWidget(version_label)

        copyright_label = QLabel(APP_COPYRIGHT)
        copyright_label.setStyleSheet(
            f"font-family: {self.FONT}; font-size: 10pt; "
            f"color: {self.TEXT};")
        layout.addWidget(copyright_label)

        # Lizenz-Box
        license_group = QGroupBox("Lizenz")
        lic_layout = QVBoxLayout()

        lic_text = QLabel(
            "Dieses Programm steht unter der <b>MIT-Lizenz</b>.<br>"
            "Eine Kopie der Lizenz ist in der Datei <code>LICENSE</code> "
            "enthalten. Die vollständigen Lizenztexte aller "
            "Drittanbieter-Komponenten findest du in der Datei "
            "<code>THIRD_PARTY_LICENSES.txt</code>."
        )
        lic_text.setWordWrap(True)
        lic_text.setTextFormat(Qt.TextFormat.RichText)
        lic_text.setStyleSheet(
            f"font-family: {self.FONT}; font-size: 10pt; "
            f"color: {self.TEXT};")
        lic_layout.addWidget(lic_text)

        github_link = QLabel(
            '<a href="https://github.com/BinhDiez/PictureChronoSort">'
            'github.com/BinhDiez/PictureChronoSort</a>'
        )
        github_link.setOpenExternalLinks(True)
        github_link.setTextFormat(Qt.TextFormat.RichText)
        github_link.setStyleSheet(
            f"font-family: {self.FONT}; font-size: 10pt; "
            f"color: {self.ACCENT};")
        lic_layout.addWidget(github_link)

        license_group.setLayout(lic_layout)
        layout.addWidget(license_group)

        # Drittanbieter
        third_party_group = QGroupBox("Drittanbieter-Komponenten")
        tp_layout = QVBoxLayout()

        third_party_text = QLabel(
            "<b>PySide6 / Qt</b> — LGPL v3<br>"
            "GUI-Framework. Copyright © The Qt Company.<br><br>"
            "<b>ExifTool</b> — Artistic License / GPL<br>"
            "Metadaten-Extraktion und -Manipulation. "
            "Copyright © Phil Harvey.<br><br>"
            "<b>send2trash</b> — BSD-3-Clause<br>"
            "Papierkorb-Unterstützung. Copyright © Send2Trash-Autoren.<br><br>"
            "<b>Nominatim / OpenStreetMap</b> — ODbL<br>"
            "Reverse-Geocoding für GPS-Vorschläge. "
            "Daten © OpenStreetMap-Mitwirkende."
        )
        third_party_text.setWordWrap(True)
        third_party_text.setTextFormat(Qt.TextFormat.RichText)
        third_party_text.setStyleSheet(
            f"font-family: {self.FONT}; font-size: 9pt; "
            f"color: {self.TEXT};")
        tp_layout.addWidget(third_party_text)

        osm_link = QLabel(
            '<a href="https://www.openstreetmap.org/copyright">'
            'OpenStreetMap-Nutzungsbedingungen</a>'
        )
        osm_link.setOpenExternalLinks(True)
        osm_link.setTextFormat(Qt.TextFormat.RichText)
        osm_link.setStyleSheet(
            f"font-family: {self.FONT}; font-size: 9pt; "
            f"color: {self.ACCENT};")
        tp_layout.addWidget(osm_link)

        third_party_group.setLayout(tp_layout)
        layout.addWidget(third_party_group)

        # Update-Prüfung
        self.update_label = QLabel("Prüfe auf Updates…")
        self.update_label.setStyleSheet(
            f"font-family: {self.FONT}; font-size: 9pt; "
            f"color: #888888;")
        layout.addWidget(self.update_label)

        self.update_btn = QPushButton("Jetzt prüfen")
        self.update_btn.setStyleSheet(
            f"QPushButton {{ background-color: {self.ACCENT}; "
            f"color: #ffffff; border: none; border-radius: 4px; "
            f"padding: 6px 16px; font-weight: bold; }}"
            f"QPushButton:hover {{ background-color: #6bb3ff; }}"
        )
        self.update_btn.clicked.connect(self._check_for_updates)
        layout.addWidget(self.update_btn)

        layout.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(16, 10, 16, 10)
        btn_row.addStretch(1)
        ok_btn = QPushButton("OK")
        ok_btn.setMinimumWidth(110)
        ok_btn.setMinimumHeight(32)
        ok_btn.setStyleSheet(
            f"QPushButton {{ background-color: {MessageDialog.BTN_OK}; "
            f"color: #ffffff; border: none; border-radius: 4px; "
            f"padding: 6px 18px; font-weight: bold; }}"
            f"QPushButton:hover {{ background-color: #3db35f; }}"
        )
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(ok_btn)
        outer.addLayout(btn_row)

        # Update-Prüfung automatisch starten
        QTimer.singleShot(100, self._check_for_updates)

    def _set_app_icon(self):
        icon_path = SCRIPT_DIR / APP_ICON_FILE
        if icon_path.exists():
            icon = QIcon(str(icon_path))
            if not icon.isNull():
                self.setWindowIcon(icon)

    def _build_stylesheet(self) -> str:
        return f"""
            QDialog {{ background-color: {self.BG}; }}
            QLabel {{ color: {self.TEXT}; background: transparent; }}
            QGroupBox {{
                color: {self.ACCENT};
                border: 1px solid {self.BOX_BORD};
                border-radius: 4px;
                margin-top: 10px;
                padding-top: 10px;
                font-weight: bold;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
                background-color: {self.BG};
            }}
            QScrollArea {{ background-color: {self.BG}; border: none; }}
            QScrollBar:vertical {{
                background: #0f0f0f;
                width: 12px;
                margin: 0;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: #444444;
                border-radius: 6px;
                min-height: 24px;
            }}
            QScrollBar::handle:vertical:hover {{ background: #666666; }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{ height: 0; background: none; }}
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {{ background: none; }}
        """

    def _check_for_updates(self):
        """Prüft asynchron auf GitHub nach der neuesten Version."""
        self.update_label.setText("Prüfe auf Updates…")
        self.update_label.setStyleSheet(
            f"font-family: {self.FONT}; font-size: 9pt; "
            f"color: #888888;")
        self.update_btn.setEnabled(False)

        worker = UpdateCheckWorker()
        self._update_worker = worker
        worker.result.connect(self._on_update_result)
        worker.start()

    def _on_update_result(self, latest_tag: str, error: str):
        self.update_btn.setEnabled(True)

        if error:
            self.update_label.setText(f"⚠️ Update-Prüfung fehlgeschlagen: {error}")
            self.update_label.setStyleSheet(
                f"font-family: {self.FONT}; font-size: 9pt; "
                f"color: #e0a020;")
            return

        latest_clean = latest_tag.lstrip("v").strip()
        current_clean = APP_VERSION.lstrip("v").strip()

        if latest_clean == current_clean:
            self.update_label.setText(
                f"✓ Du hast die neueste Version ({APP_VERSION}).")
            self.update_label.setStyleSheet(
                f"font-family: {self.FONT}; font-size: 9pt; "
                f"color: #2e9e4f;")
        else:
            self.update_label.setText(
                f"⬆️ Neue Version verfügbar: {latest_tag} "
                f"(aktuell: {APP_VERSION})")
            self.update_label.setStyleSheet(
                f"font-family: {self.FONT}; font-size: 9pt; "
                f"color: #4ea1ff;")
            self.update_btn.setText("Zur Download-Seite")
            try:
                self.update_btn.clicked.disconnect()
            except Exception:
                pass
            self.update_btn.clicked.connect(self._open_download_page)

    def _open_download_page(self):
        import webbrowser
        webbrowser.open(f"https://github.com/{GITHUB_REPO}/releases")


class UpdateCheckWorker(QThread):
    """Prüft asynchron auf GitHub nach der neuesten Version."""
    result = Signal(str, str)   # (latest_tag, error)

    def run(self):
        try:
            url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
            req = urllib.request.Request(
                url, headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            tag = data.get("tag_name", "")
            if not tag:
                self.result.emit("", "Kein Tag in der Antwort")
            else:
                self.result.emit(tag, "")
        except Exception as e:
            self.result.emit("", str(e))


# -----------------------------------------------------------------------------
# Metadaten-Dialog mit Erklärungen (ToolTip)
# -----------------------------------------------------------------------------

# Erklärungen für bekannte EXIF-Felder
EXIF_FIELD_DESCRIPTIONS = {
    # Datum / Zeit
    "DateTimeOriginal": "Wann das Foto tatsächlich aufgenommen wurde (Kamera-Auslösezeitpunkt). Das wichtigste Datumsfeld.",
    "CreateDate": "Wann die Datei erzeugt wurde. Bei Exporten/Bearbeitungen oft abweichend vom Aufnahmedatum.",
    "ModifyDate": "Wann die Datei zuletzt verändert wurde. Meist Bearbeitungsdatum, nicht Aufnahmedatum.",
    "DateTimeDigitized": "Wann das Bild digitalisiert wurde (bei Scans oder älteren Digitalkameras).",
    "MediaCreateDate": "Erstellungsdatum bei Video-/Audio-Dateien.",
    "TrackCreateDate": "Erstellungsdatum einer Video-Spur.",
    "ContentCreateDate": "Erstellungsdatum des Inhalts (bei manchen Video-Formaten).",
    "CreationDate": "Allgemeines Erstellungsdatum (je nach Dateiformat unterschiedlich belegt).",
    "FileModifyDate": "Wann die Datei auf dem Datenträger zuletzt geändert wurde.",
    "FileAccessDate": "Wann zuletzt auf die Datei zugegriffen wurde.",
    "FileInodeChangeDate": "Wann sich die Datei-Metadaten (Inode) geändert haben.",

    # Kamera / Gerät
    "Make": "Hersteller der Kamera bzw. des Geräts (z. B. Apple, Canon, Nikon).",
    "Model": "Modellbezeichnung der Kamera bzw. des Geräts (z. B. iPhone 15 Pro).",
    "CameraModelName": "Modellname der Kamera (alternative Bezeichnung zu Model).",
    "LensModel": "Bezeichnung des verwendeten Objektivs.",
    "LensMake": "Hersteller des Objektivs.",
    "SerialNumber": "Seriennummer der Kamera.",
    "Software": "Name und Version der Software/Firmware, die das Bild erzeugt oder bearbeitet hat.",
    "FirmwareVersion": "Firmware-Version der Kamera.",
    "OwnerName": "Name des Kamera-Besitzers (falls in der Kamera eingestellt).",
    "Artist": "Name des Fotografen oder Urhebers.",
    "Copyright": "Urheberrechtshinweis.",
    "ImageDescription": "Freitext-Beschreibung des Bildes. Wird von diesem Programm für den gesetzten Text/Ort genutzt.",
    "XPTitle": "Titel des Bildes (Windows-Metadaten). Wird von diesem Programm für den Originalnamen genutzt.",
    "UserComment": "Benutzerkommentar (kann von der Kamera oder vom Nutzer gesetzt sein).",

    # Bild / Technik
    "ExposureTime": "Belichtungszeit (z. B. 1/250 Sekunde).",
    "FNumber": "Blendenzahl (z. B. f/2.8). Kleiner Wert = mehr Licht, geringere Schärfentiefe.",
    "ApertureValue": "Blendenwert (rechnerisch, meist identisch mit FNumber).",
    "ISO": "Lichtempfindlichkeit (z. B. 100, 400, 3200). Höher = mehr Bildrauschen.",
    "ShutterSpeedValue": "Verschlusszeit (rechnerischer Wert, meist identisch mit ExposureTime).",
    "FocalLength": "Brennweite in mm (z. B. 26.0 mm).",
    "FocalLengthIn35mmFormat": "Brennweite umgerechnet auf Kleinbildformat (35mm) – gut zum Vergleichen.",
    "Flash": "Ob der Blitz ausgelöst wurde.",
    "MeteringMode": "Messmethode der Belichtung (z. B. Mehrfeldmessung, Spotmessung).",
    "WhiteBalance": "Weißabgleich (automatisch oder manuell).",
    "ExposureProgram": "Belichtungsprogramm (z. B. Automatik, Blendenpriorität, Manuell).",
    "ExposureCompensation": "Belichtungskorrektur in EV (z. B. +0.3, -1.0).",
    "ColorSpace": "Farbraum (z. B. sRGB, Adobe RGB).",
    "Orientation": "Ausrichtung des Bildes (0° = normal, 90° = gedreht usw.).",
    "XResolution": "Horizontale Auflösung in dpi.",
    "YResolution": "Vertikale Auflösung in dpi.",
    "ResolutionUnit": "Einheit der Auflösung (z. B. Zoll).",
    "ImageWidth": "Bildbreite in Pixeln.",
    "ImageHeight": "Bildhöhe in Pixeln.",
    "Compression": "Kompressionsart (z. B. JPEG, unkomprimiert).",
    "ThumbnailImage": "Eingebettetes Vorschaubild (Thumbnail).",
    "ExifVersion": "Version des EXIF-Standards, der beim Schreiben verwendet wurde.",

    # GPS / Ort
    "GPSLatitude": "Geografische Breite (Rohwert, meist in Grad/Minuten/Sekunden).",
    "GPSLongitude": "Geografische Länge (Rohwert).",
    "GPSLatitudeRef": "Nördliche oder südliche Breite (N/S).",
    "GPSLongitudeRef": "Östliche oder westliche Länge (E/W).",
    "GPSAltitude": "Höhe über dem Meeresspiegel.",
    "GPSAltitudeRef": "Bezugspunkt der Höhenangabe (0 = Meereshöhe, 1 = darunter).",
    "GPSTimeStamp": "UTC-Zeit der GPS-Position.",
    "GPSDateStamp": "UTC-Datum der GPS-Position.",
    "GPSImgDirection": "Blickrichtung der Kamera zum Aufnahmezeitpunkt (in Grad).",
    "GPSProcessingMethod": "Wie die Position ermittelt wurde (z. B. GPS, WLAN, Mobilfunk).",

    # Datei / Format
    "FileName": "Aktueller Dateiname.",
    "Directory": "Ordner, in dem die Datei liegt.",
    "FileSize": "Dateigröße.",
    "FileType": "Dateityp (z. B. JPEG, HEIC, MP4).",
    "FileTypeExtension": "Dateiendung (z. B. jpg, heic, mov).",
    "MIMEType": "MIME-Typ der Datei (z. B. image/jpeg).",
    "ImageSize": "Bildgröße (Breite × Höhe in Pixeln).",
    "Megapixels": "Bildgröße in Megapixeln.",
    "Duration": "Dauer bei Video-/Audio-Dateien.",
    "VideoFrameRate": "Bilder pro Sekunde bei Videos.",
    "AudioChannels": "Anzahl der Audiokanäle (1 = Mono, 2 = Stereo).",
    "AudioSampleRate": "Abtastrate des Audios in Hz.",

    # Sonstiges
    "Rating": "Bewertung (Sterne), falls vergeben.",
    "Keywords": "Schlagwörter (falls vergeben).",
    "Subject": "Thema/Betreff (falls vergeben).",
    "Creator": "Urheber (bei manchen Formaten).",
    "Title": "Titel (bei manchen Formaten).",
    "Headline": "Überschrift (bei manchen Formaten).",
}

def get_field_description(field_name: str) -> Optional[str]:
    """Liefert eine deutsche Kurzerklärung zu einem EXIF-Feldnamen."""
    # Exakter Treffer
    if field_name in EXIF_FIELD_DESCRIPTIONS:
        return EXIF_FIELD_DESCRIPTIONS[field_name]
    # Teilstring-Treffer (z. B. "EXIF:DateTimeOriginal" → "DateTimeOriginal")
    for key, desc in EXIF_FIELD_DESCRIPTIONS.items():
        if field_name.endswith(key):
            return desc
    return None

class MetadataDialog(QDialog):
    """Zeigt die wichtigsten Metadaten einer Datei in einer Tabelle."""

    BG       = "#1a1a1a"
    TEXT     = "#ffffff"
    BOX_BG   = "#0f0f0f"
    BOX_BORD = "#3a3a3a"
    ACCENT   = "#4ea1ff"
    FONT     = "Menlo, Monaco, monospace"

    def __init__(self, parent, file_path: Path, meta: Dict[str, Any],
                 parsed_date: Optional[datetime] = None,
                 filename_date: Optional[datetime] = None,
                 fs_mtime: Optional[str] = None,
                 xptitle: Optional[str] = None,
                 image_description: Optional[str] = None):
        super().__init__(parent)
        self._current_filename = file_path.name
        self.setWindowTitle(f"Metadaten — {file_path.name}")
        self.setMinimumSize(720, 640)
        self._table = None
        self._set_app_icon()
        self.setStyleSheet(self._build_stylesheet())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Header
        header = HeaderWidget(
            title="Metadaten",
            logo_file=LOGO_FILE,
            icon_file=RIGHT_ICON_FILE,
            logo_size=56,
            icon_size=42,
        )
        outer.addWidget(header, 0)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet(f"color: {self.BOX_BORD};")
        outer.addWidget(line, 0)

        content = QWidget()
        content.setObjectName("metaContent")
        content.setStyleSheet(
            f"#metaContent {{ background-color: {self.BG}; }}")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        outer.addWidget(content, 1)

        # Dateiname
        name_label = QLabel(f"<b>Datei:</b> {file_path.name}")
        name_label.setWordWrap(True)
        name_label.setStyleSheet(
            f"font-family: {self.FONT}; font-size: 16pt; "
            f"color: {self.TEXT}; padding-bottom: 4px;")
        layout.addWidget(name_label)

        # Zusammenfassung: Datumswerte
        summary_parts = []
        if parsed_date:
            summary_parts.append(
                f"<b>Metadaten-Datum:</b> "
                f"<span style='color:#2e9e4f'>"
                f"{parsed_date.strftime('%Y-%m-%d %H:%M:%S')}</span>")
        else:
            summary_parts.append(
                "<b>Metadaten-Datum:</b> "
                "<span style='color:#e74c3c'>— nicht gefunden —</span>")
        if filename_date:
            summary_parts.append(
                f"<b>Dateiname-Datum:</b> "
                f"<span style='color:#4ea1ff'>"
                f"{filename_date.strftime('%Y-%m-%d %H:%M:%S')}</span>")
        if fs_mtime:
            summary_parts.append(
                f"<b>Dateisystem-mtime:</b> {fs_mtime}")

        summary_label = QLabel("<br>".join(summary_parts))
        summary_label.setStyleSheet(
            f"font-family: {self.FONT}; font-size: 14pt; "
            f"color: {self.TEXT}; padding: 8px; "
            f"background-color: #141414; "
            f"border: 1px solid {self.BOX_BORD}; "
            f"border-radius: 4px;")
        summary_label.setWordWrap(True)
        layout.addWidget(summary_label)

        # XPTitle / ImageDescription (falls vorhanden)
        if xptitle or image_description:
            extra_parts = []
            if xptitle:
                extra_parts.append(f"<b>XPTitle (Originalname):</b> {xptitle}")
            if image_description:
                extra_parts.append(
                    f"<b>ImageDescription (Text):</b> {image_description}")
            extra_label = QLabel("<br>".join(extra_parts))
            extra_label.setStyleSheet(
                f"font-family: {self.FONT}; font-size: 14pt; "
                f"color: {self.TEXT}; padding: 8px; "
                f"background-color: #141414; "
                f"border: 1px solid {self.BOX_BORD}; "
                f"border-radius: 4px;")
            extra_label.setWordWrap(True)
            layout.addWidget(extra_label)

        # Tabelle
        table_label = QLabel("<b>Alle Metadaten-Felder:</b>")
        table_label.setStyleSheet(
            f"font-family: {self.FONT}; font-size: 14pt; "
            f"color: {self.TEXT}; padding-top: 4px;")
        layout.addWidget(table_label)

        self._table = table = QTableWidget()
        table.setColumnCount(2)
        table.setHorizontalHeaderLabels(["Feld", "Wert"])
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setStyleSheet(
            "QTableWidget {"
            f"  background-color: {self.BOX_BG};"
            f"  alternate-background-color: #141414;"
            f"  color: {self.TEXT};"
            f"  gridline-color: {self.BOX_BORD};"
            f"  border: 1px solid {self.BOX_BORD};"
            "  border-radius: 4px;"
            "  font-family: Menlo, Monaco, monospace;"
            "  font-size: 12pt;"
            "}"
            "QTableWidget::item:selected {"
            "  background-color: #4ea1ff;"
            "  color: #ffffff;"
            "}"
            "QHeaderView::section {"
            "  background-color: #1e1e1e;"
            f"  color: {self.ACCENT};"
            "  padding: 6px;"
            f"  border: 1px solid {self.BOX_BORD};"
            "  font-weight: bold;"
            "  font-family: Menlo, Monaco, monospace;"
            "  font-size: 14pt;"
            "}"
        )
        table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch)

        # Sortierung: Datums-relevante Felder zuerst
        DATE_PRIORITY = (
            "DateTimeOriginal", "CreateDate", "CreationDate",
            "MediaCreateDate", "TrackCreateDate", "ContentCreateDate",
            "DateTimeDigitized", "DateTime", "ModifyDate",
        )

        def sort_key(item):
            key, _ = item
            # Datum-Felder zuerst
            for i, tag in enumerate(DATE_PRIORITY):
                if key.endswith(tag):
                    return (0, i, key)
            # GPS-Felder danach
            if "GPS" in key or "Location" in key:
                return (1, 0, key)
            # Rest alphabetisch
            return (2, 0, key)

        sorted_items = sorted(meta.items(), key=sort_key)
        table.setRowCount(len(sorted_items))
        for row, (key, val) in enumerate(sorted_items):
            # Gruppen-Präfix entfernen (z. B. "EXIF:IFD0:Field" → "Field")
            display_key = key
            if ":" in key:
                display_key = key.rsplit(":", 1)[-1]
            # Wert als String
            if isinstance(val, (list, tuple)):
                display_val = ", ".join(str(v) for v in val)
            else:
                display_val = str(val)
            item_key = QTableWidgetItem(display_key)
            item_val = QTableWidgetItem(display_val)

            # Tooltip: Erklärung + vollständiger Schlüssel
            description = get_field_description(display_key)
            if description:
                tooltip_key = f"<b>{description}</b><br><br>" \
                              f"<i>Schlüssel: {key}</i>"
            else:
                tooltip_key = f"<i>Schlüssel: {key}</i>"

            item_key.setToolTip(tooltip_key)
            item_val.setToolTip(display_val)
            table.setItem(row, 0, item_key)
            table.setItem(row, 1, item_val)

        layout.addWidget(table, 1)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        copy_btn = QPushButton("Tabelle kopieren")
        copy_btn.setMinimumWidth(150)
        copy_btn.setMinimumHeight(32)
        copy_btn.setStyleSheet(
            f"QPushButton {{ background-color: {self.ACCENT}; "
            f"color: #ffffff; border: none; border-radius: 4px; "
            f"padding: 6px 18px; font-weight: bold; }}"
            f"QPushButton:hover {{ background-color: #6bb3ff; }}"
        )
        copy_btn.clicked.connect(self._copy_table_to_clipboard)
        btn_row.addWidget(copy_btn)

        ok_btn = QPushButton("Schließen")
        ok_btn.setMinimumWidth(110)
        ok_btn.setMinimumHeight(32)
        ok_btn.setStyleSheet(
            f"QPushButton {{ background-color: {MessageDialog.BTN_OK}; "
            f"color: #ffffff; border: none; border-radius: 4px; "
            f"padding: 6px 18px; font-weight: bold; }}"
            f"QPushButton:hover {{ background-color: #3db35f; }}"
        )
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

    def _set_app_icon(self):
        icon_path = SCRIPT_DIR / APP_ICON_FILE
        if icon_path.exists():
            icon = QIcon(str(icon_path))
            if not icon.isNull():
                self.setWindowIcon(icon)

    def _build_stylesheet(self) -> str:
        return f"""
            QDialog {{ background-color: {self.BG}; }}
            QLabel {{ color: {self.TEXT}; background: transparent; }}
        """

    def _copy_table_to_clipboard(self):
        """Kopiert die gesamte Tabelle als TSV in die Zwischenablage."""
        if self._table is None:
            return
        try:
            rows = []
            # Kopfzeile
            header_items = []
            for col in range(self._table.columnCount()):
                item = self._table.horizontalHeaderItem(col)
                header_items.append(item.text() if item else "")
            rows.append("\t".join(header_items))
            # Datenzeilen
            for row in range(self._table.rowCount()):
                cells = []
                for col in range(self._table.columnCount()):
                    item = self._table.item(row, col)
                    cells.append(item.text() if item else "")
                rows.append("\t".join(cells))
            text = "\n".join(rows)
            QApplication.clipboard().setText(text)
            # Kurzes Feedback im Dialog (ohne extra MessageBox)
            self.setWindowTitle(
                f"Metadaten — {self._table.rowCount()} Zeilen kopiert")
            QTimer.singleShot(
                2000,
                lambda: self.setWindowTitle(
                    f"Metadaten — {self._current_filename}"))
        except Exception as e:
            MessageDialog.show_error(self, "Kopieren fehlgeschlagen", str(e))

# -----------------------------------------------------------------------------
# Header-Widget
# -----------------------------------------------------------------------------

class HeaderWidget(QWidget):
    def __init__(self, title: str,
                 logo_file: str = LOGO_FILE,
                 icon_file: str = RIGHT_ICON_FILE,
                 logo_size: int = 80,
                 icon_size: int = 80,
                 parent=None):
        super().__init__(parent)
        self.setStyleSheet("background-color: #0d0d0d;")
        hl = QHBoxLayout(self)
        hl.setContentsMargins(12, 8, 12, 8)
        hl.setSpacing(10)

        hl.addStretch(1)

        logo_pm = load_pixmap(logo_file, logo_size)
        if logo_pm is not None:
            logo_label = QLabel()
            logo_label.setPixmap(logo_pm)
            logo_label.setFixedSize(logo_pm.width(), logo_pm.height())
            hl.addWidget(logo_label, 0, Qt.AlignmentFlag.AlignVCenter)
        else:
            placeholder = QLabel()
            placeholder.setFixedWidth(logo_size)
            hl.addWidget(placeholder, 0, Qt.AlignmentFlag.AlignVCenter)

        hl.addStretch(1)

        title_label = QLabel(title)
        title_label.setStyleSheet(
            "font-size: 20px; font-weight: bold; color: #ffffff; "
            "background: transparent;"
        )
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hl.addWidget(title_label, 0, Qt.AlignmentFlag.AlignCenter)

        hl.addStretch(1)

        icon_pm = load_pixmap(icon_file, icon_size)
        if icon_pm is not None:
            icon_label = QLabel()
            icon_label.setPixmap(icon_pm)
            icon_label.setFixedSize(icon_pm.width(), icon_pm.height())
            icon_label.setAlignment(Qt.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            hl.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignVCenter)
        else:
            placeholder = QLabel()
            placeholder.setFixedWidth(icon_size)
            hl.addWidget(placeholder, 0, Qt.AlignmentFlag.AlignVCenter)

        hl.addStretch(1)

        self.setFixedHeight(max(logo_size, icon_size) + 20)


# -----------------------------------------------------------------------------
# Datum-Korrektur-Dialog
# -----------------------------------------------------------------------------


class DatumKorrekturDialog(QDialog):
    """Dialog zum Korrigieren von Aufnahmedatum und Uhrzeit."""

    BG       = "#1a1a1a"
    TEXT     = "#ffffff"
    BOX_BG   = "#0f0f0f"
    BOX_BORD = "#3a3a3a"
    ACCENT   = "#4ea1ff"
    FONT     = "Menlo, Monaco, monospace"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Aufnahmedatum / Uhrzeit korrigieren")
        self.setMinimumWidth(700)
        self.result_data = None

        try:
            screen = QApplication.primaryScreen().availableGeometry()
            max_h = int(screen.height() * 0.9)
            max_w = max(700, min(820, int(screen.width() * 0.8)))
            self.resize(max_w, max_h)
            self.setMaximumHeight(max_h)
        except Exception:
            self.resize(720, 800)

        self.setStyleSheet(self._build_stylesheet())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # Wichtig: EventFilter auf dem Viewport, um Tab abzufangen,
        # bevor die ScrollArea es intern verarbeitet
        scroll.viewport().installEventFilter(self)
        self._scroll_area = scroll

        content = QWidget()
        content.setObjectName("dateContent")
        content.setStyleSheet(
            "#dateContent { background-color: #1a1a1a; }")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        info = QLabel(
            "Hier kannst du für alle Dateien im Quellordner das "
            "Aufnahmedatum (EXIF DateTimeOriginal) korrigieren.<br><br>"
            "• <b>Absolute Werte setzen</b>: nur ausgefüllte Felder "
            "werden überschrieben, leere Felder bleiben unverändert.<br>"
            "• <b>Relative Verschiebung</b>: z. B. −2 Stunden bei "
            "Zeitzonen-Fehler.<br>"
            "• Beide Arten können kombiniert werden."
        )
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(info)

        # ---- A) Absolute Werte ------------------------------------
        abs_group = QGroupBox("A) Absolute Werte setzen (optional)")
        abs_outer = QVBoxLayout()

        def _make_field_row(items):
            row = QHBoxLayout()
            row.setSpacing(6)
            row.setContentsMargins(0, 0, 0, 0)
            for label, edit, width in items:
                lbl = QLabel(label)
                lbl.setMinimumWidth(60)
                edit.setFixedWidth(width)
                row.addWidget(lbl)
                row.addWidget(edit)
                row.addSpacing(12)
            row.addStretch(1)
            return row

        self.edit_year = QLineEdit()
        self.edit_year.setPlaceholderText("z. B. 2008")
        self.edit_month = QLineEdit()
        self.edit_month.setPlaceholderText("1–12")
        self.edit_day = QLineEdit()
        self.edit_day.setPlaceholderText("1–31")
        self.edit_hour = QLineEdit()
        self.edit_hour.setPlaceholderText("0–23")
        self.edit_minute = QLineEdit()
        self.edit_minute.setPlaceholderText("0–59")
        self.edit_second = QLineEdit()
        self.edit_second.setPlaceholderText("0–59")

        abs_outer.addLayout(_make_field_row([
            ("Jahr:",   self.edit_year,  80),
            ("Monat:",  self.edit_month, 60),
            ("Tag:",    self.edit_day,   60),
        ]))
        abs_outer.addLayout(_make_field_row([
            ("Stunde:",  self.edit_hour,   60),
            ("Minute:",  self.edit_minute, 60),
            ("Sekunde:", self.edit_second, 60),
        ]))
        abs_group.setLayout(abs_outer)
        layout.addWidget(abs_group)

        # ---- B) Relative Zeitverschiebung -------------------------
        rel_group = QGroupBox("B) Relative Zeitverschiebung (optional)")
        rel_outer = QVBoxLayout()

        rel_info = QLabel(
            "Verschiebt alle Dateien um den angegebenen Betrag. "
            "Negative Werte = rückwärts.")
        rel_info.setWordWrap(True)
        rel_outer.addWidget(rel_info)

        rel_row = QHBoxLayout()
        rel_row.setSpacing(6)
        rel_row.setContentsMargins(0, 0, 0, 0)

        lbl_h = QLabel("Stunden:")
        lbl_h.setMinimumWidth(lbl_h.sizeHint().width())
        self.spin_hours = QSpinBox()
        self.spin_hours.setRange(-72, 72)
        self.spin_hours.setValue(0)
        self.spin_hours.setSuffix(" h")
        self.spin_hours.setFixedWidth(80)
        rel_row.addWidget(lbl_h)
        rel_row.addWidget(self.spin_hours)
        rel_row.addSpacing(16)

        lbl_m = QLabel("Minuten:")
        lbl_m.setMinimumWidth(lbl_m.sizeHint().width())
        self.spin_minutes = QSpinBox()
        self.spin_minutes.setRange(-59, 59)
        self.spin_minutes.setValue(0)
        self.spin_minutes.setSuffix(" min")
        self.spin_minutes.setFixedWidth(80)
        rel_row.addWidget(lbl_m)
        rel_row.addWidget(self.spin_minutes)
        rel_row.addSpacing(16)

        lbl_s = QLabel("Sekunden:")
        lbl_s.setMinimumWidth(lbl_s.sizeHint().width())
        self.spin_seconds = QSpinBox()
        self.spin_seconds.setRange(-59, 59)
        self.spin_seconds.setValue(0)
        self.spin_seconds.setSuffix(" s")
        self.spin_seconds.setFixedWidth(80)
        rel_row.addWidget(lbl_s)
        rel_row.addWidget(self.spin_seconds)

        rel_row.addStretch(1)
        rel_outer.addLayout(rel_row)

        rel_group.setLayout(rel_outer)
        layout.addWidget(rel_group)

        # ---- C) Dateinamen ----------------------------------------
        name_group = QGroupBox("C) Dateinamen anpassen (optional)")
        name_layout = QVBoxLayout()

        self.cb_rename = QCheckBox(
            "Dateinamen an den neuen Zeitstempel anpassen")
        self.cb_rename.setChecked(False)
        name_layout.addWidget(self.cb_rename)

        self.rename_sub = QWidget()
        sub_layout = QVBoxLayout(self.rename_sub)
        sub_layout.setContentsMargins(24, 4, 0, 4)

        self.radio_replace = QRadioButton(
            "Vorhandenen Zeitstempel im Namen ersetzen")
        self.radio_replace.setChecked(True)
        sub_layout.addWidget(self.radio_replace)

        self.radio_prefix = QRadioButton(
            "Zeitstempel vor den Basisnamen stellen")
        sub_layout.addWidget(self.radio_prefix)

        self.radio_suffix = QRadioButton(
            "Zeitstempel an den Basisnamen anhängen")
        sub_layout.addWidget(self.radio_suffix)

        self.radio_keep = QRadioButton("Dateinamen unverändert lassen")
        sub_layout.addWidget(self.radio_keep)

        name_layout.addWidget(self.rename_sub)
        name_group.setLayout(name_layout)
        layout.addWidget(name_group)

        self.rename_sub.setEnabled(False)
        self.cb_rename.toggled.connect(self.rename_sub.setEnabled)

        # ---- FS-Zeitstempel ---------------------------------------
        self.fs_check = QCheckBox(
            "Auch Finder-Datum (mtime + birthtime) anpassen")
        self.fs_check.setChecked(True)
        layout.addWidget(self.fs_check)

        # ---- Vorschau ---------------------------------------------
        preview_group = QGroupBox("Vorschau")
        preview_layout = QVBoxLayout()
        self.preview_label = QLabel(
            "Werte eingeben, um eine Vorschau zu sehen.")
        self.preview_label.setWordWrap(True)
        self.preview_label.setStyleSheet(
            f"font-family: {self.FONT}; font-size: 10pt; "
            f"padding: 6px; background-color: #141414; "
            f"border: 1px solid {self.BOX_BORD}; border-radius: 4px;")
        preview_layout.addWidget(self.preview_label)
        preview_group.setLayout(preview_layout)
        layout.addWidget(preview_group)

        for w in [self.edit_year, self.edit_month, self.edit_day,
                  self.edit_hour, self.edit_minute, self.edit_second]:
            w.textChanged.connect(self._update_preview)
        for sp in [self.spin_hours, self.spin_minutes, self.spin_seconds]:
            sp.valueChanged.connect(self._update_preview)
        for r in [self.radio_replace, self.radio_prefix,
                  self.radio_suffix, self.radio_keep]:
            r.toggled.connect(self._update_preview)
        self.cb_rename.toggled.connect(self._update_preview)

        layout.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        # ---- Buttons fix am unteren Rand --------------------------
        btn_container = QWidget()
        btn_container.setObjectName("dateBtnContainer")
        btn_container.setStyleSheet(
            "#dateBtnContainer { background-color: #1a1a1a; "
            "border-top: 1px solid #3a3a3a; }")
        btn_layout = QVBoxLayout(btn_container)
        btn_layout.setContentsMargins(16, 10, 16, 10)
        btn_row, buttons_dict = make_styled_buttons_row(
            self,
            [
                ("cancel", "Abbrechen", "cancel", False),
                ("ok",     "OK",        "ok",     True),
            ],
            ok_callback=self._on_accept,
        )
        self.cancel_btn = buttons_dict.get("cancel")
        self.ok_btn = buttons_dict.get("ok")
        btn_layout.addLayout(btn_row)
        outer.addWidget(btn_container, 0)

        # ---- EventFilter für Tab & Pfeiltasten --------------------
        self._tab_order = [self.cancel_btn, self.ok_btn]
        for btn in self._tab_order:
            if btn is not None:
                btn.installEventFilter(self)

        # EventFilter auf alle Felder: Tab vom letzten Feld → OK
        for w in [self.edit_year, self.edit_month, self.edit_day,
                  self.edit_hour, self.edit_minute, self.edit_second]:
            w.installEventFilter(self)

        self._update_preview()

    def _build_stylesheet(self) -> str:
        return f"""
            QDialog {{ background-color: {self.BG}; }}
            QLabel {{ color: {self.TEXT}; background: transparent; }}
            QCheckBox {{ color: {self.TEXT}; background: transparent; }}
            QRadioButton {{ color: {self.TEXT}; background: transparent; }}
            QGroupBox {{
                color: {self.ACCENT};
                border: 1px solid {self.BOX_BORD};
                border-radius: 4px;
                margin-top: 10px;
                padding-top: 10px;
                font-weight: bold;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
                background-color: {self.BG};
            }}
            QLineEdit {{
                background-color: #1e1e1e;
                color: {self.TEXT};
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 4px 6px;
            }}
            QLineEdit:focus {{ border: 1px solid {self.ACCENT}; }}
            QSpinBox {{
                background-color: #1e1e1e;
                color: {self.TEXT};
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 4px 6px;
            }}
            QScrollArea {{ background-color: {self.BG}; border: none; }}
            QScrollBar:vertical {{
                background: #0f0f0f;
                width: 12px;
                margin: 0;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: #444444;
                border-radius: 6px;
                min-height: 24px;
            }}
            QScrollBar::handle:vertical:hover {{ background: #666666; }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{ height: 0; background: none; }}
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {{ background: none; }}
        """

    def showEvent(self, event):
        """Fokus beim Öffnen auf OK setzen."""
        super().showEvent(event)
        QTimer.singleShot(100, lambda: self.ok_btn.setFocus()
                          if self.ok_btn else None)

    def eventFilter(self, obj, event):
        """Fängt Tab auf dem ScrollArea-Viewport ab und leitet es
        manuell durch die gewünschte Reihenfolge."""
        if event.type() == QEvent.KeyPress:
            key = event.key()

            # Pfeiltasten auf Buttons (unverändert)
            if obj in self._tab_order:
                if key in (Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_Right, Qt.Key.Key_Down):
                    idx = self._tab_order.index(obj)
                    if key in (Qt.Key.Key_Right, Qt.Key.Key_Down):
                        new_idx = (idx + 1) % len(self._tab_order)
                    else:
                        new_idx = (idx - 1) % len(self._tab_order)
                    self._tab_order[new_idx].setFocus()
                    return True

            # Tab auf dem Viewport der ScrollArea abfangen
            if obj is self._scroll_area.viewport():
                if key == Qt.Key.Key_Tab:
                    focused = QApplication.focusWidget()
                    # Vom letzten Feld → OK
                    if focused is self.edit_second:
                        if self.ok_btn is not None:
                            self.ok_btn.setFocus()
                            return True
                    # Von OK → erstes Feld
                    elif focused is self.ok_btn:
                        self.edit_year.setFocus()
                        return True
                    # Von Abbrechen → erstes Feld
                    elif focused is self.cancel_btn:
                        self.edit_year.setFocus()
                        return True
                elif key == Qt.Key.Key_Backtab:
                    focused = QApplication.focusWidget()
                    # Vom ersten Feld → OK
                    if focused is self.edit_year:
                        if self.ok_btn is not None:
                            self.ok_btn.setFocus()
                            return True
                    # Von OK → letztes Feld
                    elif focused is self.ok_btn:
                        self.edit_second.setFocus()
                        return True

        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        """Enter löst den fokussierten Button aus."""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            focused = QApplication.focusWidget()
            if focused is self.ok_btn:
                self._on_accept()
                return
            if focused is self.cancel_btn:
                self.reject()
                return
        super().keyPressEvent(event)

    def _parse_int(self, edit: QLineEdit) -> Optional[int]:
        txt = edit.text().strip()
        if not txt:
            return None
        try:
            return int(txt)
        except ValueError:
            return None

    def _current_correction(self):
        sample = datetime(2013, 1, 23, 15, 5, 14)
        return apply_datetime_correction(
            sample,
            set_year=self._parse_int(self.edit_year),
            set_month=self._parse_int(self.edit_month),
            set_day=self._parse_int(self.edit_day),
            set_hour=self._parse_int(self.edit_hour),
            set_minute=self._parse_int(self.edit_minute),
            set_second=self._parse_int(self.edit_second),
            add_hours=self.spin_hours.value(),
            add_minutes=self.spin_minutes.value(),
            add_seconds=self.spin_seconds.value(),
        )

    def _rename_mode(self) -> str:
        if not self.cb_rename.isChecked() or self.radio_keep.isChecked():
            return "keep"
        if self.radio_replace.isChecked():
            return "replace"
        if self.buildradio_prefix.isChecked():
            return "prefix"
        if self.radio_suffix.isChecked():
            return "suffix"
        return "keep"

    def _update_preview(self):
        try:
            result, err = self._current_preview_correction_name()
            if err:
                self.preview_label.setText(f"⚠️ {err}")
                self.preview_label.setStyleSheet(
                    f"font-family: {self.FONT}; font-size: 10pt; "
                    f"padding: 6px; color: #c0392b; "
                    f"background-color: #141414; "
                    f"border: 1px solid {self.BOX_BORD}; "
                    f"border-radius: 4px;")
                return
            lines = [f"Beispiel-Datum (2013-01-23 15:05:14)",
                     f"  → {result.strftime('%Y-%m-%d %H:%M:%S')}"]
            rename_mode = self._rename_mode()
            if rename_mode != "keep":
                example_name = "2013-01-23_150514.jpg"
                base_example = "Geburtstag.jpg"
                from pathlib import Path as _P
                new_name =(_P(example_name),
                                                    result, rename_mode)
                lines.append("")
                lines.append("Dateiname-Beispiel (Zeitstempel im Namen):")
                lines.append(f"  {example_name} → {new_name.name}")
                new_name2 = self._build_preview_name(_P(base_example),
                                                     result, rename_mode)
                lines.append("")
                lines.append("Dateiname-Beispiel (normaler Name):")
                lines.append(f"  {base_example} → {new_name2.name}")
            self.preview_label.setText("\n".join(lines))
            self.preview_label.setStyleSheet(
                f"font-family: {self.FONT}; font-size: 10pt; "
                f"padding: 6px; background-color: #141414; "
                f"border: 1px solid {self.BOX_BORD}; "
                f"border-radius: 4px;")
        except Exception:
            self.preview_label.setText(
                "Werte eingeben, um eine Vorschau zu sehen.")

    @staticmethod
    def _build_preview_name(original_path, new_dt, rename_mode):
        if rename_mode == "keep":
            return original_path
        stem = original_path.stem
        suffix = original_path.suffix
        new_stamp = new_dt.strftime("%Y-%m-%d_%H%M%S")
        m = _STANDARD_STAMP_RE.match(stem)
        if rename_mode == "replace":
            if m:
                new_stem = new_stamp + stem[m.end():]
            else:
                new_stem = f"{new_stamp}_{stem}"
        elif rename_mode == "prefix":
            new_stem = f"{new_stamp}_{stem}"
        elif rename_mode == "suffix":
            new_stem = f"{stem}_{new_stamp}"
        else:
            return original_path
        return original_path.with_name(f"{new_stem}{suffix}")

    def _on_accept(self):
        try:
            set_year = self._parse_int(self.edit_year)
            set_month = self._parse_int(self.edit_month)
            set_day = self._parse_int(self.edit_day)
            set_hour = self._parse_int(self.edit_hour)
            set_minute = self._parse_int(self.edit_minute)
            set_second = self._parse_int(self.edit_second)

            any_absolute = any(v is not None for v in
                               [set_year, set_month, set_day, set_hour,
                                set_minute, set_second])
            any_relative = (self.spin_hours.value() != 0
                            or self.spin_minutes.value() != 0
                            or self.spin_seconds.value() != 0)

            if not any_absolute and not any_relative:
                raise ValueError(
                    "Bitte mindestens ein Feld ausfüllen oder eine "
                    "Zeitverschiebung angeben.")

            if set_year is not None and not (1900 <= set_year <= 2100):
                raise ValueError(f"Jahr {set_year} unplausibel (1900–2100).")
            if set_month is not None and not (1 <= set_month <= 12):
                raise ValueError(f"Monat {set_month} ungültig (1–12).")
            if set_day is not None and not (1 <= set_day <= 31):
                raise ValueError(f"Tag {set_day} ungültig (1–31).")
            if set_hour is not None and not (0 <= set_hour <= 23):
                raise ValueError(f"Stunde {set_hour} ungültig (0–23).")
            if set_minute is not None and not (0 <= set_minute <= 59):
                raise ValueError(f"Minute {set_minute} ungültig (0–59).")
            if set_second is not None and not (0 <= set_second <= 59):
                raise ValueError(f"Sekunde {set_second} ungültig (0–59).")

            _, err = self._current_correction()
            if err:
                raise ValueError(
                    f"Kombination ungültig: {err}\n\n"
                    f"Beispiel: 2013-01-23 15:05:14 mit diesen Werten "
                    f"würde fehlschlagen.")

            self.result_data = {
                "set_year": set_year, "set_month": set_month,
                "set_day": set_day, "set_hour": set_hour,
                "set_minute": set_minute, "set_second": set_second,
                "add_hours": self.spin_hours.value(),
                "add_minutes": self.spin_minutes.value(),
                "add_seconds": self.spin_seconds.value(),
                "also_fs": self.fs_check.isChecked(),
                "rename_mode": self._rename_mode(),
            }
            self.accept()
        except ValueError as e:
            MessageDialog.show_warn(self, "Ungültige Eingabe", str(e))

# -----------------------------------------------------------------------------
# Text/Ort-Dialog
# -----------------------------------------------------------------------------

class GpsSuggestionWorker(QThread):
    """Fragt GPS-Koordinaten im Hintergrund ab, damit die GUI
    nicht einfriert (kein Spinning Wheel)."""
    status = Signal(str)
    result = Signal(str, str)   # (vorschlag, zusammenfassung)
    error = Signal(str)

    def __init__(self, source_dir: Path):
        super().__init__()
        self.source_dir = source_dir
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            if not URLLIB_AVAILABLE:
                self.error.emit("urllib nicht verfügbar")
                return

            self.status.emit("Sammle GPS-Daten…")
            files = []
            try:
                for f in self.source_dir.rglob("*"):
                    if not f.is_file():
                        continue
                    if f.suffix.lower() not in SUPPORTED_EXTS:
                        continue
                    if f.suffix.lower() == AAE_EXT:
                        continue
                    files.append(f)
            except Exception as e:
                self.error.emit(f"Datei-Scan fehlgeschlagen: {e}")
                return

            if not files:
                self.error.emit("Keine Dateien gefunden.")
                return

            coords_map: Dict[Tuple[float, float], int] = {}
            for f in files:
                if self._cancel:
                    return
                try:
                    gps = exiftool_get_gps(f)
                except Exception:
                    continue
                if gps:
                    key = (round(gps[0], 3), round(gps[1], 3))
                    coords_map[key] = coords_map.get(key, 0) + 1

            if not coords_map:
                self.error.emit(
                    "Keine GPS-Daten in den Dateien gefunden.")
                return

            sorted_coords = sorted(coords_map.items(), key=lambda x: -x[1])
            max_queries = min(5, len(sorted_coords))
            self.status.emit(
                f"{len(sorted_coords)} eindeutige Position(en) — "
                f"frage Nominatim ({max_queries}×)…")

            results = []
            for i, ((lat, lon), count) in enumerate(sorted_coords[:max_queries]):
                if self._cancel:
                    return
                if i > 0:
                    # In 1-Sekunden-Schritten warten, aber abbrechbar
                    for _ in range(10):
                        if self._cancel:
                            return
                        time.sleep(0.1)
                self.status.emit(
                    f"Frage Position {i + 1}/{max_queries} ab "
                    f"({count}× vorhanden)…")
                name = nominatim_reverse(lat, lon)
                if name:
                    results.append((name, count))

            if not results:
                self.error.emit(
                    "Kein Ortsname ermittelt "
                    "(Nominatim nicht erreichbar?).")
                return

            best_name = results[0][0]
            summary = "; ".join(f"{n} ({c}×)" for n, c in results)
            self.result.emit(best_name, summary)
        except Exception as e:
            self.error.emit(str(e))

class FileScanWorker(QThread):
    """Sammelt Dateien aus dem Quellordner und liest ihre
    aktuellen Texte/Originalnamen für die Vorschau."""
    progress = Signal(int, int)
    result = Signal(list, str)

    def __init__(self, source_dir: Path):
        super().__init__()
        self.source_dir = source_dir
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            files = []
            for p in sorted(self.source_dir.rglob("*")):
                if not p.is_file():
                    continue
                if p.suffix.lower() not in SUPPORTED_EXTS:
                    continue
                if p.suffix.lower() == AAE_EXT:
                    continue
                files.append(p)

            rows = []
            for i, f in enumerate(files, 1):
                if self._cancel:
                    break
                try:
                    old_text = get_image_description(f) or ""
                except Exception:
                    old_text = ""
                try:
                    original_name = get_xptitle(f) or ""
                except Exception:
                    original_name = ""
                # Text aus Dateinamen extrahieren (falls vorhanden)
                filename_text = ""
                try:
                    _, rest = split_filename_after_stamp(f.stem)
                    if rest and not re.fullmatch(
                            r"(img|dsc|dscn|dscf|pxl|mvimg)[-_]?\d+",
                            rest, re.IGNORECASE):
                        filename_text = sanitize_text_for_filename(rest)
                except Exception:
                    pass

                rows.append({
                    "path": str(f),
                    "name": f.name,
                    "old_text": old_text,
                    "original_name": original_name,
                    "filename_text": filename_text,
                })
                if i % 10 == 0 or i == len(files):
                    self.progress.emit(i, len(files))

            self.result.emit(rows, "")
        except Exception as e:
            self.result.emit([], str(e))

class TextOrtDialog(QDialog):
    """Dialog zum Setzen eines Textes nach dem Zeitstempel.
    Zeigt eine Live-Vorschau der betroffenen Dateien aus dem Quellordner."""

    BG       = "#1a1a1a"
    TEXT     = "#ffffff"
    BOX_BG   = "#0f0f0f"
    BOX_BORD = "#3a3a3a"
    ACCENT   = "#4ea1ff"
    FONT     = "Menlo, Monaco, monospace"

    def __init__(self, parent=None, source_dir: Optional[Path] = None):
        super().__init__(parent)
        self.setWindowTitle("Text/Ort nach Zeitstempel setzen")
        self.setMinimumSize(900, 720)
        self.result_data = None
        self._source_dir = source_dir
        self._gps_worker = None
        self._scan_worker = None
        self._file_rows = []

        self._set_app_icon()
        self.setStyleSheet(self._build_stylesheet())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = HeaderWidget(
            title="Text/Ort nach Zeitstempel setzen",
            logo_file=LOGO_FILE,
            icon_file=RIGHT_ICON_FILE,
            logo_size=56,
            icon_size=42,
        )
        outer.addWidget(header, 0)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet(f"color: {self.BOX_BORD};")
        outer.addWidget(line, 0)

        content = QWidget()
        content.setObjectName("textContent")
        content.setStyleSheet(
            f"#textContent {{ background-color: {self.BG}; }}")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        outer.addWidget(content, 1)

        info = QLabel(
            "Hier kannst du allen Dateien im Quellordner einen Text "
            "oder Ort nach dem Zeitstempel hinzufügen.<br><br>"
            "Der Zeitstempel (Format <code>JJJJ-MM-TT_HHMMSS</code>) muss "
            "bereits am Anfang des Dateinamens stehen. Dateien ohne "
            "Standard-Zeitstempel werden übersprungen."
        )
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setStyleSheet(
            f"font-family: {self.FONT}; font-size: 10pt; "
            f"color: {self.TEXT}; padding: 8px; "
            f"background-color: #141414; "
            f"border: 1px solid {self.BOX_BORD}; border-radius: 4px;")
        layout.addWidget(info)

        # ---- Text/Ort -----------------------------------------------
        text_group = QGroupBox("Text/Ort")
        tg = QVBoxLayout()

        self.text_edit = QLineEdit()
        self.text_edit.setPlaceholderText(
            "z. B. Berlin, Lissabon Urlaub 2024")
        tg.addWidget(self.text_edit)

        gps_row = QHBoxLayout()
        self.gps_btn = QPushButton("Ort aus GPS vorschlagen…")
        self.gps_btn.setObjectName("gpsBtn")
        self.gps_btn.setAutoDefault(False)
        self.gps_btn.clicked.connect(self._suggest_from_gps)
        self.gps_status = QLabel("")
        self.gps_status.setStyleSheet(
            f"color: #a0a0a0; font-size: 9pt;")
        gps_row.addWidget(self.gps_btn)
        gps_row.addWidget(self.gps_status, 1)
        tg.addLayout(gps_row)

        self.gps_progress = QProgressBar()
        self.gps_progress.setRange(0, 0)
        self.gps_progress.setMaximumHeight(8)
        self.gps_progress.setTextVisible(False)
        self.gps_progress.setVisible(False)
        tg.addWidget(self.gps_progress)

        text_group.setLayout(tg)
        layout.addWidget(text_group)

        # ---- Optionen ----------------------------------------------
        opt_group = QGroupBox("Optionen")
        opt_layout = QVBoxLayout()

        self.cb_keep_old = QCheckBox(
            "Vorhandenen Text übernehmen "
            "(aus Metadaten oder Dateiname)")
        self.cb_keep_old.setChecked(True)
        self.cb_keep_old.setToolTip(
            "Wenn angehakt: Der bisherige Text (z. B. 'Ostern' oder "
            "'Castries') bleibt im neuen Dateinamen erhalten.\n"
            "Wenn nicht angehakt: Der bisherige Text wird entfernt.")
        opt_layout.addWidget(self.cb_keep_old)

        self.cb_keep_filename_text = QCheckBox(
            "Text aus Dateiname hat Vorrang vor Metadaten")
        self.cb_keep_filename_text.setChecked(True)
        self.cb_keep_filename_text.setToolTip(
            "Wenn angehakt: Der Text, den du im Finder in den Dateinamen "
            "eingegeben hast, wird verwendet.\n"
            "Wenn nicht angehakt: Der zuletzt vom Programm gesetzte "
            "Text (ImageDescription) wird verwendet.")
        opt_layout.addWidget(self.cb_keep_filename_text)

        self.cb_keep_original = QCheckBox(
            "Sinnvollen Originalnamen anzeigen "
            "(z. B. Geburtstag — nicht IMG_1234)")
        self.cb_keep_original.setChecked(False)
        self.cb_keep_original.setToolTip(
            "Wenn angehakt: Der ursprüngliche Dateiname (ohne Zeitstempel) "
            "wird an den neuen Namen angehängt, sofern er sinnvoll ist.\n"
            "Kamera-Automatiknamen wie IMG_1234 werden nicht angezeigt.")
        opt_layout.addWidget(self.cb_keep_original)

        opt_group.setLayout(opt_layout)
        layout.addWidget(opt_group)

        # ---- Vorschau ----------------------------------------------
        preview_group = QGroupBox("Vorschau — was würde passieren")
        pv_layout = QVBoxLayout()

        self.scan_status = QLabel("Scanne Quellordner…")
        self.scan_status.setStyleSheet(
            f"font-family: {self.FONT}; font-size: 9pt; "
            f"color: #a0a0a0; padding: 4px;")
        pv_layout.addWidget(self.scan_status, 0)

        self.scan_progress = QProgressBar()
        self.scan_progress.setRange(0, 0)
        self.scan_progress.setMaximumHeight(8)
        self.scan_progress.setTextVisible(False)
        self.scan_progress.setVisible(False)
        pv_layout.addWidget(self.scan_progress, 0)

        self.preview_table = QTableWidget()
        self.preview_table.setColumnCount(2)
        self.preview_table.setHorizontalHeaderLabels(
            ["Aktueller Name", "Neuer Name"])
        self.preview_table.verticalHeader().setVisible(False)
        self.preview_table.setAlternatingRowColors(True)
        self.preview_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.preview_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.preview_table.setStyleSheet(
            "QTableWidget {"
            f"  background-color: {self.BOX_BG};"
            "  alternate-background-color: #141414;"
            f"  color: {self.TEXT};"
            f"  gridline-color: {self.BOX_BORD};"
            f"  border: 1px solid {self.BOX_BORD};"
            "  border-radius: 4px;"
            "  font-family: Menlo, Monaco, monospace;"
            "  font-size: 9pt;"
            "}"
            "QTableWidget::item:selected {"
            "  background-color: #4ea1ff; color: #ffffff;"
            "}"
            "QHeaderView::section {"
            "  background-color: #1e1e1e;"
            f"  color: {self.ACCENT};"
            "  padding: 6px;"
            f"  border: 1px solid {self.BOX_BORD};"
            "  font-weight: bold;"
            "  font-family: Menlo, Monaco, monospace;"
            "  font-size: 9pt;"
            "}"
        )
        self.preview_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)
        self.preview_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch)
        self.preview_table.setMinimumHeight(240)
        pv_layout.addWidget(self.preview_table, 1)

        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet(
            f"font-family: {self.FONT}; font-size: 10pt; "
            f"color: {self.TEXT}; padding: 8px; "
            f"background-color: #141414; "
            f"border: 1px solid {self.BOX_BORD}; "
            f"border-radius: 4px;")
        self.summary_label.setWordWrap(True)
        self.summary_label.setMinimumHeight(36)
        pv_layout.addWidget(self.summary_label, 0)

        preview_group.setLayout(pv_layout)
        layout.addWidget(preview_group, 1)

        # ---- Buttons -----------------------------------------------
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        self.cancel_btn = QPushButton("Abbrechen")
        self.cancel_btn.setObjectName("textCancelBtn")
        self.cancel_btn.setMinimumWidth(110)
        self.cancel_btn.setMinimumHeight(32)
        self.cancel_btn.setAutoDefault(False)
        self.cancel_btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self.cancel_btn)

        self.ok_btn = QPushButton("OK")
        self.ok_btn.setObjectName("textOkBtn")
        self.ok_btn.setMinimumWidth(110)
        self.ok_btn.setMinimumHeight(32)
        self.ok_btn.setAutoDefault(False)
        self.ok_btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self._on_accept)
        btn_row.addWidget(self.ok_btn)
        layout.addLayout(btn_row)

        QWidget.setTabOrder(self.cancel_btn, self.ok_btn)
        QWidget.setTabOrder(self.ok_btn, self.cancel_btn)

        # Einheitliche Navigation
        self._buttons = {"cancel": self.cancel_btn, "ok": self.ok_btn}
        self._tab_order = [self.cancel_btn, self.ok_btn]
        self._on_button = lambda key: (
            self.reject() if key == "cancel" else self._on_accept())
        for btn in self._tab_order:
            btn.installEventFilter(self)

        # Live-Update-Trigger
        self.text_edit.textChanged.connect(self._update_preview)
        self.cb_keep_old.toggled.connect(self._update_preview)
        self.cb_keep_original.toggled.connect(self._update_preview)
        self.cb_keep_filename_text.toggled.connect(self._update_preview)

        QTimer.singleShot(100, self._start_scan)
        QTimer.singleShot(150, lambda: self.ok_btn.setFocus())

    # ------------------------------------------------------------------
    def _set_app_icon(self):
        icon_path = SCRIPT_DIR / APP_ICON_FILE
        if icon_path.exists():
            icon = QIcon(str(icon_path))
            if not icon.isNull():
                self.setWindowIcon(icon)

    def _build_stylesheet(self) -> str:
        return f"""
            QDialog {{ background-color: {self.BG}; }}
            QLabel {{ color: {self.TEXT}; background: transparent; }}
            QCheckBox {{ color: {self.TEXT}; background: transparent; }}
            QLineEdit {{
                background-color: #1e1e1e;
                color: {self.TEXT};
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 5px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {self.ACCENT}; }}
            QGroupBox {{
                color: {self.ACCENT};
                border: 1px solid {self.BOX_BORD};
                border-radius: 4px;
                margin-top: 10px;
                padding-top: 10px;
                font-weight: bold;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
                background-color: {self.BG};
            }}
            QPushButton#gpsBtn {{
                background-color: #1e3a5f;
                color: #ffffff;
                border: none;
                border-radius: 4px;
                padding: 5px 12px;
            }}
            QPushButton#gpsBtn:hover {{
                background-color: #2a4a6f;
            }}
            QPushButton#gpsBtn:focus {{
                border: 2px solid #ffffff;
            }}
            QPushButton#textOkBtn {{
                background-color: {MessageDialog.BTN_OK};
                color: #ffffff;
                border: none;
                border-radius: 4px; padding: 6px 18px;
                font-weight: bold;
            }}
            QPushButton#textOkBtn:hover {{ background-color: #3db35f; }}
            QPushButton#textOkBtn:focus {{
                border: 2px solid #ffffff;
                background-color: #3db35f;
            }}
            QPushButton#textCancelBtn {{
                background-color: {MessageDialog.BTN_CANCEL};
                color: #ffffff;
                border: none;
                border-radius: 4px; padding: 6px 18px;
                font-weight: bold;
            }}
            QPushButton#textCancelBtn:hover {{ background-color: #e74c3c; }}
            QPushButton#textCancelBtn:focus {{
                border: 2px solid #ffffff;
                background-color: #e74c3c;
            }}
        """

    # ------------------------------------------------------------------
    def eventFilter(self, obj, event):
        """Fängt Pfeiltasten auf Buttons ab, bevor der Button sie verarbeitet."""
        if event.type() == QEvent.KeyPress:
            if obj in self._tab_order:
                key = event.key()
                if key in (Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_Right, Qt.Key.Key_Down):
                    idx = self._tab_order.index(obj)
                    if key in (Qt.Key.Key_Right, Qt.Key.Key_Down):
                        new_idx = (idx + 1) % len(self._tab_order)
                    else:
                        new_idx = (idx - 1) % len(self._tab_order)
                    self._tab_order[new_idx].setFocus()
                    return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        """Enter/Return löst den fokussierten Button aus."""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            focused = QApplication.focusWidget()
            if isinstance(focused, QPushButton) and focused in self._tab_order:
                key = "cancel" if focused is self.cancel_btn else "ok"
                self._on_button(key)
                return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    def _start_scan(self):
        if not self._source_dir or not self._source_dir.is_dir():
            self.scan_status.setText("⚠️ Kein gültiger Quellordner.")
            return
        self.scan_progress.setVisible(True)
        self.scan_status.setText("Sammle Dateien…")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor.BusyCursor)
        QApplication.processEvents()
        self._scan_worker = FileScanWorker(self._source_dir)
        self._scan_worker.progress.connect(self._on_scan_progress)
        self._scan_worker.result.connect(self._on_scan_result)
        self._scan_worker.start()

    def _on_scan_progress(self, current, total):
        self.scan_status.setText(f"Lese Dateien… {current} von {total}")

    def _on_scan_result(self, rows: list, error: str):
        self.scan_progress.setVisible(False)
        try:
            QApplication.restoreOverrideCursor()
        except Exception:
            pass
        if error:
            self.scan_status.setText(f"⚠️ Fehler: {error}")
            return
        self._file_rows = rows
        self.scan_status.setText(f"{len(rows)} Datei(en) gefunden.")
        self._update_preview()

    # ------------------------------------------------------------------
    def _build_new_name(self, row: dict, text: str) -> Optional[str]:
        name = row["name"]
        dt = parse_any_standard_stamp(name)
        if dt is None:
            return None

        stamp = dt.strftime("%Y-%m-%d_%H%M%S")

        # 1. Vorhandenen Text ermitteln (nur wenn Checkbox an)
        old_text = ""
        if self.cb_keep_old.isChecked():
            fn_text = row.get("filename_text", "") or ""
            meta_text = row.get("old_text", "") or ""
            if self.cb_keep_filename_text.isChecked() and fn_text:
                old_text = fn_text          # Dateiname hat Vorrang
            elif meta_text:
                old_text = meta_text        # Fallback auf ImageDescription
            elif fn_text:
                old_text = fn_text          # Fallback, falls Meta leer

        original_name = row.get("original_name", "") or ""

        # 2. Neuen Namen zusammenbauen
        parts = [stamp]
        if text:
            parts.append(text)
        if old_text and old_text != text:
            parts.append(old_text)
        if (self.cb_keep_original.isChecked()
                and is_meaningful_original_name(original_name)
                and not re.fullmatch(r"[\d\-_. ]+", original_name)):
            parts.append(original_name)

        new_stem = " ".join(parts)
        new_stem = re.sub(r"\s+", " ", new_stem).strip()

        max_name_len = 200 - len(Path(name).suffix)
        if len(new_stem) > max_name_len:
            new_stem = new_stem[:max_name_len].rstrip()

        return f"{new_stem}{Path(name).suffix}"


    def _update_preview(self):
        if not self._file_rows:
            self.preview_table.setRowCount(0)
            return

        raw = self.text_edit.text()
        text = sanitize_text_for_filename(raw)

        # Alle Vorschauen berechnen
        previews = []
        for row in self._file_rows:
            new_name = self._build_new_name(row, text)
            previews.append((row, new_name))

        total = len(previews)
        changed = 0
        skipped = 0
        unchanged = 0
        for row, new_name in previews:
            if new_name is None:
                skipped += 1
            elif new_name == row["name"]:
                unchanged += 1
            else:
                changed += 1

        # Tabelle füllen (max. 30 Zeilen)
        max_rows = 30
        display = previews[:max_rows]
        self.preview_table.setRowCount(len(display))
        for i, (row, new_name) in enumerate(display):
            item_old = QTableWidgetItem(row["name"])
            if new_name is None:
                item_new = QTableWidgetItem(
                    "— übersprungen (kein Standard-Zeitstempel)")
                item_new.setForeground(Qt.gray)
            elif new_name == row["name"]:
                item_new = QTableWidgetItem("— unverändert")
                item_new.setForeground(Qt.gray)
            else:
                item_new = QTableWidgetItem(new_name)
                item_new.setForeground(QColor("#2e9e4f"))
            self.preview_table.setItem(i, 0, item_old)
            self.preview_table.setItem(i, 1, item_new)

        # Zusammenfassung
        parts = [
            f"<b>{changed}</b> Datei(en) werden umbenannt",
            f"<b>{unchanged}</b> unverändert",
            f"<b>{skipped}</b> übersprungen (kein Standard-Zeitstempel)",
            f"von insgesamt <b>{total}</b>",
        ]
        if total > max_rows:
            parts.append(
                f"(nur die ersten {max_rows} in der Tabelle angezeigt)")
        self.summary_label.setText(" | ".join(parts))

    # ------------------------------------------------------------------
    def _suggest_from_gps(self):
        if not self._source_dir or not self._source_dir.is_dir():
            MessageDialog.show_warn(self, "Kein Quellordner",
                "Kein gültiger Quellordner verfügbar.")
            return
        if not URLLIB_AVAILABLE:
            MessageDialog.show_warn(self, "Netzwerk nicht verfügbar",
                "urllib ist nicht verfügbar — GPS-Vorschlag nicht möglich.")
            return
        if self._gps_worker is not None and self._gps_worker.isRunning():
            return

        self.gps_btn.setEnabled(False)
        self.gps_btn.setText("Suche läuft…")
        self.gps_status.setText("Sammle GPS-Daten…")
        self.gps_progress.setVisible(True)
        QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)
        QApplication.processEvents()

        self._gps_worker = GpsSuggestionWorker(self._source_dir)
        self._gps_worker.status.connect(self.gps_status.setText)
        self._gps_worker.result.connect(self._on_gps_result)
        self._gps_worker.error.connect(self._on_gps_error)
        self._gps_worker.finished.connect(self._on_gps_finished)
        self._gps_worker.start()

    def _on_gps_finished(self):
        self.gps_btn.setEnabled(True)
        self.gps_btn.setText("Ort aus GPS vorschlagen…")
        self.gps_progress.setVisible(False)
        try:
            QApplication.restoreOverrideCursor()
        except Exception:
            pass

    def _on_gps_result(self, best_name: str, summary: str):
        self.text_edit.setText(best_name)
        self.gps_status.setText(f"Vorschlag: {summary}")

    def _on_gps_error(self, message: str):
        self.gps_status.setText(f"⚠️ {message}")

    # ------------------------------------------------------------------
    def _on_accept(self):
        raw = self.text_edit.text()
        text = sanitize_text_for_filename(raw)
        if not text:
            MessageDialog.show_warn(self, "Kein Text",
                "Bitte einen Text eingeben.")
            return
        self.result_data = {
            "text": text,
            "keep_old_text": self.cb_keep_old.isChecked(),
            "keep_meaningful_original": self.cb_keep_original.isChecked(),
            "keep_filename_text": self.cb_keep_filename_text.isChecked(),
        }
        self.accept()

    def _cleanup(self):
        if self._gps_worker is not None and self._gps_worker.isRunning():
            self._gps_worker.cancel()
            self._gps_worker.wait(2000)
        if self._scan_worker is not None and self._scan_worker.isRunning():
            self._scan_worker.cancel()
            self._scan_worker.wait(2000)
        try:
            while QApplication.overrideCursor() is not None:
                QApplication.restoreOverrideCursor()
        except Exception:
            pass

    def reject(self):
        self._cleanup()
        super().reject()

    def accept(self):
        self._cleanup()
        super().accept()

    def closeEvent(self, event):
        self._cleanup()
        super().closeEvent(event)

# -----------------------------------------------------------------------------
# Dialog: Metadaten bereinigen
# -----------------------------------------------------------------------------

class CleanMetadataDialog(QDialog):
    """Dialog zum Bereinigen der vom Programm gesetzten Metadaten-Felder."""

    BG       = "#1a1a1a"
    TEXT     = "#ffffff"
    BOX_BG   = "#0f0f0f"
    BOX_BORD = "#3a3a3a"
    ACCENT   = "#4ea1ff"
    FONT     = "Menlo, Monaco, monospace"

    def __init__(self, parent=None, source_dir: Optional[Path] = None):
        super().__init__(parent)
        self.setWindowTitle("Metadaten bereinigen")
        self.setMinimumSize(900, 720)
        self.result_data = None
        self._source_dir = source_dir
        self._scan_worker = None
        self._rows = []

        self._set_app_icon()
        self.setStyleSheet(self._build_stylesheet())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = HeaderWidget(
            title="Metadaten bereinigen",
            logo_file=LOGO_FILE,
            icon_file=RIGHT_ICON_FILE,
            logo_size=56,
            icon_size=42,
        )
        outer.addWidget(header, 0)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet(f"color: {self.BOX_BORD};")
        outer.addWidget(line, 0)

        content = QWidget()
        content.setObjectName("cleanContent")
        content.setStyleSheet(
            f"#cleanContent {{ background-color: {self.BG}; }}")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        outer.addWidget(content, 1)

        info = QLabel(
            "Dieser Modus entfernt ausgewählte Metadaten-Felder, die "
            "das Programm zuvor gesetzt hat.<br><br>"
            "<b>Was wird nicht gelöscht?</b><br>"
            "• Das Aufnahmedatum (DateTimeOriginal, CreateDate, "
            "ModifyDate)<br>"
            "• Das Finder-Datum (mtime, birthtime)<br>"
            "• Alle Felder, die die Kamera selbst gesetzt hat<br><br>"
            "Dateien werden <b>nicht</b> verschoben oder umbenannt."
        )
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setStyleSheet(
            f"font-family: {self.FONT}; font-size: 10pt; "
            f"color: {self.TEXT}; padding: 8px; "
            f"background-color: #141414; "
            f"border: 1px solid {self.BOX_BORD}; border-radius: 4px;")
        layout.addWidget(info)

        # ---- Felder -------------------------------------------------
        fields_group = QGroupBox("Zu bereinigende Felder")
        fields_layout = QVBoxLayout()

        self.cb_xptitle = QCheckBox(
            "Alten Dateinamen löschen (Feld: XPTitle)")
        self.cb_xptitle.setChecked(True)
        fields_layout.addWidget(self.cb_xptitle)

        xptitle_info = QLabel(
            "  → <b>XPTitle</b> enthält den ursprünglichen Dateinamen, "
            "den das Programm beim ersten <i>organize</i>-Lauf gespeichert "
            "hat.<br>"
            "  → Nützlich, um den ursprünglichen Namen wiederzufinden.<br>"
            "  → Kann gelöscht werden, wenn der ursprüngliche Name "
            "nicht mehr gebraucht wird."
        )
        xptitle_info.setWordWrap(True)
        xptitle_info.setTextFormat(Qt.TextFormat.RichText)
        xptitle_info.setStyleSheet(
            f"font-family: {self.FONT}; font-size: 9pt; "
            f"color: #a0a0a0; padding-left: 24px; padding-bottom: 6px;")
        fields_layout.addWidget(xptitle_info)

        self.cb_imgdesc = QCheckBox(
            "Text/Beschreibung löschen (Feld: ImageDescription)")
        self.cb_imgdesc.setChecked(False)
        fields_layout.addWidget(self.cb_imgdesc)

        imgdesc_info = QLabel(
            "  → <b>ImageDescription</b> enthält den Text/Ort, den du "
            "über <i>add_text</i> gesetzt hast (z. B. 'Saint Lucia').<br>"
            "  → Nützlich, wenn du den Text später erneut verwenden "
            "möchtest.<br>"
            "  → <b>Achtung:</b> Wenn du ihn löschst, kann <i>add_text</i> "
            "ihn nicht mehr automatisch übernehmen."
        )
        imgdesc_info.setWordWrap(True)
        imgdesc_info.setTextFormat(Qt.TextFormat.RichText)
        imgdesc_info.setStyleSheet(
            f"font-family: {self.FONT}; font-size: 9pt; "
            f"color: #a0a0a0; padding-left: 24px; padding-bottom: 6px;")
        fields_layout.addWidget(imgdesc_info)

        fields_group.setLayout(fields_layout)
        layout.addWidget(fields_group)

        # ---- Vorschau ----------------------------------------------
        preview_group = QGroupBox("Vorschau — was würde gelöscht")
        pv_layout = QVBoxLayout()

        self.scan_status = QLabel("Scanne Quellordner…")
        self.scan_status.setStyleSheet(
            f"font-family: {self.FONT}; font-size: 9pt; "
            f"color: #a0a0a0; padding: 4px;")
        pv_layout.addWidget(self.scan_status, 0)

        self.scan_progress = QProgressBar()
        self.scan_progress.setRange(0, 0)
        self.scan_progress.setMaximumHeight(8)
        self.scan_progress.setTextVisible(False)
        self.scan_progress.setVisible(False)
        pv_layout.addWidget(self.scan_progress, 0)

        self.preview_table = QTableWidget()
        self.preview_table.setColumnCount(4)
        self.preview_table.setHorizontalHeaderLabels(
            ["Datei", "XPTitle (alter Name)", "ImageDescription (Text)",
             "Aktion"])
        self.preview_table.verticalHeader().setVisible(False)
        self.preview_table.setAlternatingRowColors(True)
        self.preview_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.preview_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.preview_table.setStyleSheet(
            "QTableWidget {"
            f"  background-color: {self.BOX_BG};"
            "  alternate-background-color: #141414;"
            f"  color: {self.TEXT};"
            f"  gridline-color: {self.BOX_BORD};"
            f"  border: 1px solid {self.BOX_BORD};"
            "  border-radius: 4px;"
            "  font-family: Menlo, Monaco, monospace;"
            "  font-size: 9pt;"
            "}"
            "QTableWidget::item:selected {"
            "  background-color: #4ea1ff; color: #ffffff;"
            "}"
            "QHeaderView::section {"
            "  background-color: #1e1e1e;"
            f"  color: {self.ACCENT};"
            "  padding: 6px;"
            f"  border: 1px solid {self.BOX_BORD};"
            "  font-weight: bold;"
            "  font-family: Menlo, Monaco, monospace;"
            "  font-size: 9pt;"
            "}"
        )
        self.preview_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents)
        self.preview_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch)
        self.preview_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.Stretch)
        self.preview_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeToContents)
        self.preview_table.setMinimumHeight(220)
        pv_layout.addWidget(self.preview_table, 1)

        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet(
            f"font-family: {self.FONT}; font-size: 10pt; "
            f"color: {self.TEXT}; padding: 8px; "
            f"background-color: #141414; "
            f"border: 1px solid {self.BOX_BORD}; "
            f"border-radius: 4px;")
        self.summary_label.setWordWrap(True)
        self.summary_label.setMinimumHeight(36)
        pv_layout.addWidget(self.summary_label, 0)

        preview_group.setLayout(pv_layout)
        layout.addWidget(preview_group, 1)

        # ---- Buttons -----------------------------------------------
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        self.cancel_btn = QPushButton("Abbrechen")
        self.cancel_btn.setObjectName("cleanCancelBtn")
        self.cancel_btn.setMinimumWidth(110)
        self.cancel_btn.setMinimumHeight(32)
        self.cancel_btn.setAutoDefault(False)
        self.cancel_btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self.cancel_btn)

        self.ok_btn = QPushButton("OK")
        self.ok_btn.setObjectName("cleanOkBtn")
        self.ok_btn.setMinimumWidth(110)
        self.ok_btn.setMinimumHeight(32)
        self.ok_btn.setAutoDefault(False)
        self.ok_btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self._on_accept)
        btn_row.addWidget(self.ok_btn)
        layout.addLayout(btn_row)

        QWidget.setTabOrder(self.cancel_btn, self.ok_btn)
        QWidget.setTabOrder(self.ok_btn, self.cancel_btn)

        # Einheitliche Navigation
        self._buttons = {"cancel": self.cancel_btn, "ok": self.ok_btn}
        self._tab_order = [self.cancel_btn, self.ok_btn]
        self._on_button = lambda key: (
            self.reject() if key == "cancel" else self._on_accept())
        for btn in self._tab_order:
            btn.installEventFilter(self)

        self.cb_xptitle.toggled.connect(self._update_action_column)
        self.cb_imgdesc.toggled.connect(self._update_action_column)

        QTimer.singleShot(100, self._start_scan)
        QTimer.singleShot(150, lambda: self.ok_btn.setFocus())

    def _set_app_icon(self):
        icon_path = SCRIPT_DIR / APP_ICON_FILE
        if icon_path.exists():
            icon = QIcon(str(icon_path))
            if not icon.isNull():
                self.setWindowIcon(icon)

    def _build_stylesheet(self) -> str:
        return f"""
            QDialog {{ background-color: {self.BG}; }}
            QLabel {{ color: {self.TEXT}; background: transparent; }}
            QCheckBox {{ color: {self.TEXT}; background: transparent; }}
            QGroupBox {{
                color: {self.ACCENT};
                border: 1px solid {self.BOX_BORD};
                border-radius: 4px;
                margin-top: 10px;
                padding-top: 10px;
                font-weight: bold;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
                background-color: {self.BG};
            }}
            QPushButton#cleanOkBtn {{
                background-color: {MessageDialog.BTN_OK};
                color: #ffffff;
                border: none;
                border-radius: 4px; padding: 6px 18px;
                font-weight: bold;
            }}
            QPushButton#cleanOkBtn:hover {{ background-color: #3db35f; }}
            QPushButton#cleanOkBtn:focus {{
                border: 2px solid #ffffff;
                background-color: #3db35f;
            }}
            QPushButton#cleanCancelBtn {{
                background-color: {MessageDialog.BTN_CANCEL};
                color: #ffffff;
                border: none;
                border-radius: 4px; padding: 6px 18px;
                font-weight: bold;
            }}
            QPushButton#cleanCancelBtn:hover {{ background-color: #e74c3c; }}
            QPushButton#cleanCancelBtn:focus {{
                border: 2px solid #ffffff;
                background-color: #e74c3c;
            }}
        """

    def eventFilter(self, obj, event):
        """Fängt Pfeiltasten auf Buttons ab, bevor der Button sie verarbeitet."""
        if event.type() == QEvent.KeyPress:
            if obj in self._tab_order:
                key = event.key()
                if key in (Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_Right, Qt.Key.Key_Down):
                    idx = self._tab_order.index(obj)
                    if key in (Qt.Key.Key_Right, Qt.Key.Key_Down):
                        new_idx = (idx + 1) % len(self._tab_order)
                    else:
                        new_idx = (idx - 1) % len(self._tab_order)
                    self._tab_order[new_idx].setFocus()
                    return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        """Enter/Return löst den fokussierten Button aus."""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            focused = QApplication.focusWidget()
            if isinstance(focused, QPushButton) and focused in self._tab_order:
                key = "cancel" if focused is self.cancel_btn else "ok"
                self._on_button(key)
                return
        super().keyPressEvent(event)

    def _start_scan(self):
        if not self._source_dir or not self._source_dir.is_dir():
            self.scan_status.setText("⚠️ Kein gültiger Quellordner.")
            self.ok_btn.setEnabled(False)
            return
        self.scan_progress.setVisible(True)
        self.scan_status.setText("Sammle Dateien und lese Metadaten…")
        QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)
        QApplication.processEvents()
        self._scan_worker = MetadataScanWorker(self._source_dir)
        self._scan_worker.progress.connect(self._on_scan_progress)
        self._scan_worker.result.connect(self._on_scan_result)
        self._scan_worker.start()

    def _on_scan_progress(self, current, total):
        self.scan_status.setText(
            f"Lese Metadaten… {current} von {total}")

    def _on_scan_result(self, rows: list, error: str):
        self.scan_progress.setVisible(False)
        try:
            QApplication.restoreOverrideCursor()
        except Exception:
            pass
        if error:
            self.scan_status.setText(f"⚠️ Fehler: {error}")
            self.ok_btn.setEnabled(False)
            return
        self.scan_status.setText(f"{len(rows)} Datei(en) gefunden.")
        self._rows = rows
        self.preview_table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            item_name = QTableWidgetItem(row["name"])
            item_xp = QTableWidgetItem(row["xptitle"] or "—")
            item_img = QTableWidgetItem(row["image_description"] or "—")
            item_action = QTableWidgetItem("")
            if not row["xptitle"]:
                item_xp.setForeground(Qt.gray)
            if not row["image_description"]:
                item_img.setForeground(Qt.gray)
            self.preview_table.setItem(i, 0, item_name)
            self.preview_table.setItem(i, 1, item_xp)
            self.preview_table.setItem(i, 2, item_img)
            self.preview_table.setItem(i, 3, item_action)
        self._update_action_column()

    def _update_action_column(self):
        if not self._rows:
            return
        do_xp = self.cb_xptitle.isChecked()
        do_img = self.cb_imgdesc.isChecked()
        count_xp = 0
        count_img = 0
        count_none = 0
        for i, row in enumerate(self._rows):
            actions = []
            if do_xp and row["xptitle"]:
                actions.append("XPTitle löschen")
                count_xp += 1
            if do_img and row["image_description"]:
                actions.append("ImageDescription löschen")
                count_img += 1
            if not actions:
                actions.append("—")
                count_none += 1
            item = QTableWidgetItem(", ".join(actions))
            self.preview_table.setItem(i, 3, item)

        parts = []
        if do_xp:
            parts.append(f"<b>XPTitle:</b> {count_xp} Datei(en)")
        if do_img:
            parts.append(f"<b>ImageDescription:</b> {count_img} Datei(en)")
        if not do_xp and not do_img:
            parts.append("<i>Keine Felder ausgewählt.</i>")
        parts.append(f"<b>Unverändert:</b> {count_none} Datei(en)")
        self.summary_label.setText(" | ".join(parts))

    def _on_accept(self):
        do_xp = self.cb_xptitle.isChecked()
        do_img = self.cb_imgdesc.isChecked()
        if not do_xp and not do_img:
            MessageDialog.show_warn(self, "Keine Auswahl",
                "Bitte mindestens ein Feld zum Bereinigen auswählen.")
            return
        if not self._rows:
            MessageDialog.show_warn(self, "Keine Dateien",
                "Es wurden keine Dateien gefunden.")
            return
        self.result_data = {
            "clean_xptitle": do_xp,
            "clean_image_description": do_img,
        }
        self.accept()

    def _cleanup(self):
        if self._scan_worker is not None and self._scan_worker.isRunning():
            self._scan_worker.cancel()
            self._scan_worker.wait(2000)
        try:
            while QApplication.overrideCursor() is not None:
                QApplication.restoreOverrideCursor()
        except Exception:
            pass

    def reject(self):
        self._cleanup()
        super().reject()

    def accept(self):
        self._cleanup()
        super().accept()

    def closeEvent(self, event):
        self._cleanup()
        super().closeEvent(event)


class MetadataScanWorker(QThread):
    """Liest XPTitle und ImageDescription aus allen Dateien im Ordner."""
    progress = Signal(int, int)
    result = Signal(list, str)   # (rows, error)

    def __init__(self, source_dir: Path):
        super().__init__()
        self.source_dir = source_dir
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            files = []
            for p in sorted(self.source_dir.rglob("*")):
                if not p.is_file():
                    continue
                if p.suffix.lower() not in SUPPORTED_EXTS:
                    continue
                if p.suffix.lower() == AAE_EXT:
                    continue
                files.append(p)

            rows = []
            for i, f in enumerate(files, 1):
                if self._cancel:
                    break
                try:
                    xp = get_xptitle(f)
                except Exception:
                    xp = None
                try:
                    img = get_image_description(f)
                except Exception:
                    img = None
                rows.append({
                    "path": str(f),
                    "name": f.name,
                    "xptitle": xp,
                    "image_description": img,
                })
                if i % 10 == 0 or i == len(files):
                    self.progress.emit(i, len(files))

            self.result.emit(rows, "")
        except Exception as e:
            self.result.emit([], str(e))

# -----------------------------------------------------------------------------
# Anleitung-Dialog
# -----------------------------------------------------------------------------

class AnleitungDialog(QDialog):
    BG       = "#000000"
    TEXT     = "#ffffff"
    BOX_BG   = "#1a1a1a"
    BOX_BORD = "#444444"
    ACCENT   = "#4ea1ff"
    FONT     = "Menlo, Monaco, monospace"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Anleitung — Foto- & Video-Organizer")
        self.setMinimumSize(820, 760)
        self.show_on_start = False
        self._set_app_icon()

        self.setStyleSheet(self._build_stylesheet())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = HeaderWidget(
            title="PictureChronoSort — Anleitung",
            logo_file=LOGO_FILE,
            icon_file=RIGHT_ICON_FILE,
            logo_size=64,
            icon_size=48,
        )
        outer.addWidget(header, 0)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet(f"color: {self.BOX_BORD};")
        outer.addWidget(line, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setObjectName("scrollViewport")
        scroll.viewport().setStyleSheet(
            "#scrollViewport { background-color: %s; }" % self.BG
        )

        content = QWidget()
        content.setObjectName("contentWidget")
        content.setStyleSheet(
            "#contentWidget { background-color: %s; }" % self.BG
        )
        content.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

        c_layout = QVBoxLayout(content)
        c_layout.setContentsMargins(16, 16, 16, 16)
        c_layout.setSpacing(8)

        def add_heading(text):
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(
                "color: {accent}; font-family: {font}; "
                "font-weight: bold; font-size: 12pt; "
                "padding-top: 10px; padding-bottom: 4px; "
                "background: transparent;".format(
                    accent=self.ACCENT, font=self.FONT)
            )
            c_layout.addWidget(lbl)

        def add_text(text):
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setTextFormat(Qt.TextFormat.RichText)
            lbl.setStyleSheet(
                "color: {text}; font-family: {font}; "
                "font-size: 10pt; background: transparent;".format(
                    text=self.TEXT, font=self.FONT)
            )
            c_layout.addWidget(lbl)

        def add_box(text):
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setTextFormat(Qt.TextFormat.RichText)
            lbl.setStyleSheet(
                "color: {text}; font-family: {font}; "
                "background-color: {box_bg}; "
                "border: 1px solid {box_bord}; border-radius: 4px; "
                "padding: 8px; font-size: 10pt;".format(
                    text=self.TEXT, font=self.FONT,
                    box_bg=self.BOX_BG, box_bord=self.BOX_BORD)
            )
            c_layout.addWidget(lbl)

        # Inhalt
        add_heading("📋 Empfohlene Reihenfolge")
        add_text("Der Organizer hat mehrere Modi. Empfohlene Reihenfolge:")
        add_box(
            "1.  <b>dedupe_only</b> — Duplikate bereinigen<br>"
            "&nbsp;&nbsp;&nbsp;&nbsp;→ zuerst Dry-Run, dann echter Lauf<br><br>"
            "2.  <b>organize</b> — Fotos sortieren<br>"
            "&nbsp;&nbsp;&nbsp;&nbsp;→ verschiebt in Jahr/Monat, setzt "
            "Zeitstempel<br>"
            "&nbsp;&nbsp;&nbsp;&nbsp;→ speichert Originalname in Metadaten "
            "(XPTitle)<br>"
            "&nbsp;&nbsp;&nbsp;&nbsp;→ zuerst Dry-Run, dann echter Lauf<br><br>"
            "3.  <b>add_text</b> — Text/Ort setzen (optional)<br>"
            "&nbsp;&nbsp;&nbsp;&nbsp;→ fügt nach dem Zeitstempel einen "
            "Text ein<br>"
            "&nbsp;&nbsp;&nbsp;&nbsp;→ nur Dateien mit Standard-Zeitstempel<br><br>"
            "4.  <b>fix_capture_date</b> — Datum/Uhrzeit korrigieren "
            "(optional)<br>"
            "&nbsp;&nbsp;&nbsp;&nbsp;→ z. B. wenn das Aufnahmedatum falsch "
            "ist<br><br>"
            "5.  <b>timestamps_only</b> — NAS-Zeitstempel (optional)<br>"
            "&nbsp;&nbsp;&nbsp;&nbsp;→ nur nach einem Sync"
        )

        add_heading("⚠️  Der Testlauf (Dry-Run)")
        add_text(
            "Der Testlauf zeigt dir im Log genau, was passieren würde — "
            "ohne eine einzige Datei zu verschieben, umzubenennen oder zu "
            "löschen. Prüfe im Log, wie viele Dateien 'kein Datum' haben "
            "und ob die Zielpfade plausibel aussehen. Erst wenn alles "
            "stimmt: Häkchen entfernen und den echten Lauf starten.")

        add_heading("🎯 Die Modi im Detail")
        add_text("<b>• organize</b> (Standard)")
        add_box(
            "Verschiebt Dateien in die Struktur "
            "<code>Jahr/Jahr-Monat/JJJJ-MM-TT_HHMMSS.ext</code><br>"
            "Setzt Erstell- und Änderungsdatum auf das Aufnahmedatum.<br>"
            "Speichert den ursprünglichen Dateinamen in <code>XPTitle</code> "
            "(für spätere Text-Ergänzung)."
        )
        add_text("<b>• dedupe_only</b>")
        add_box(
            "Sucht Dateien mit identischem Inhalt (SHA-256-Hash) und "
            "behandelt sie je nach Duplikat-Aktion. Verschiebt sonst nichts."
        )
        add_text("<b>• timestamps_only</b>")
        add_box(
            "Lässt Dateien am Ort und setzt nur die Finder-Zeitstempel aus "
            "EXIF-Daten. Nützlich nach einem NAS-Sync."
        )
        add_text("<b>• fix_capture_date</b>")
        add_box(
            "Korrigiert das Aufnahmedatum (EXIF DateTimeOriginal).<br>"
            "Möglichkeiten:<br>"
            "&nbsp;&nbsp;• Jahr, Monat, Tag, Stunde, Minute, Sekunde "
            "einzeln oder kombiniert setzen<br>"
            "&nbsp;&nbsp;• Relative Zeitverschiebung (z. B. −2 Stunden "
            "bei Zeitzonen-Fehler)<br>"
            "&nbsp;&nbsp;• Optional: Dateinamen mit anpassen "
            "(Zeitstempel ersetzen, voranstellen, anhängen)"
        )
        add_text("<b>• add_text</b>")
        add_box(
            "Setzt einen Text oder Ort nach dem Zeitstempel.<br>"
            "Beispiel: <code>2013-01-23_150514.jpg</code> → "
            "<code>2013-01-23_150514 Berlin.jpg</code><br>"
            "Optionen:<br>"
            "&nbsp;&nbsp;• GPS-Vorschlag über Nominatim "
            "(Knopf im Dialog)<br>"
            "&nbsp;&nbsp;• Alten Text behalten oder ersetzen<br>"
            "&nbsp;&nbsp;• Sinnvollen Originalnamen anzeigen "
            "(z. B. Geburtstag.jpg — nicht IMG_1234)"
        )

        add_heading("📝 Metadaten-Schema")
        add_text(
            "Das Programm speichert zwei Felder in den Bild-Metadaten:")
        add_box(
            "<b>ImageDescription</b> — der zuletzt gesetzte Text/Ort<br>"
            "<b>XPTitle</b> — der ursprüngliche Dateiname<br><br>"
            "Der Originalname wird einmalig beim <code>organize</code>-Lauf "
            "gesetzt und danach nie mehr geändert. Bei <code>add_text</code> "
            "wird er nur angezeigt, wenn er 'sinnvoll' ist (also nicht "
            "<code>IMG_1234</code> oder ähnlich)."
        )

        add_heading("🔘 Die Optionen im Detail")
        add_text("<b>• Datum aus Dateinamen rekonstruieren</b>")
        add_box(
            "Erkennt Datum in Dateinamen wie <code>IMG-20240315-WA0001.jpg</code>, "
            "<code>Screenshot 2024-03-15 at 10.30.45.png</code> usw. "
            "Empfohlen: <b>AN</b>."
        )
        add_text("<b>• Live-Photo-Gruppierung</b>")
        add_box(
            "MOV-Dateien von Live-Photos erben das Datum vom HEIC. "
            "Empfohlen: <b>AN</b>."
        )
        add_text("<b>• AAE-Bearbeitungsdateien mit archivieren</b>")
        add_box(
            "AAE-Dateien enthalten Bearbeitungen aus Apple Fotos. "
            "Empfohlen: <b>AN</b>."
        )
        add_text("<b>• Live-Photo MOV entfernen</b>")
        add_box(
            "Verschiebt/löscht die MOV-Dateien von Live-Photos (spart "
            "Platz, entfernt Bewegungseffekte)."
        )
        add_text("<b>• Wenn kein Datum: mtime verwenden</b>")
        add_box(
            "Nutzt das Dateisystem-Änderungsdatum als Notlösung. "
            "Meist das Import-, nicht das Aufnahmedatum — daher "
            "standardmäßig <b>AUS</b>."
        )
        add_text("<b>• Duplikat-Aktion</b>")
        add_box(
            "<b>skip</b> — Duplikat bleibt liegen<br>"
            "<b>move</b> — in sichtbaren <code>_Duplikate</code>-Ordner "
            "(empfohlen)<br>"
            "<b>trash</b> — in den macOS-Papierkorb (nur internes LW)<br>"
            "<b>delete</b> — sofort löschen (nicht umkehrbar)"
        )

        add_heading("🛟 Sicherheitsnetze")
        add_text(
            "<b>Undo-Log:</b> Jeder echte Lauf schreibt eine "
            "<code>undo_*.jsonl</code>-Datei. Über den Button 'Undo…' "
            "kannst du einen Lauf komplett rückgängig machen "
            "(außer trash/delete).")
        add_text(
            "<b>Fehler-Log:</b> Alle Dateien mit Problemen werden in einer "
            "<code>failed_*.txt</code>-Datei im <code>logs/</code>-Ordner "
            "aufgelistet.")
        add_text(
            "<b>Sicherheitskopie:</b> Vor einer Datum-Korrektur kannst du "
            "eine <code>exif_backup_*.txt</code> anlegen lassen — mit allen "
            "alten Datumswerten.")

        add_heading("💡 Erste Schritte")
        add_box(
            "1.  Quellordner wählen<br>"
            "2.  Zielordner wählen (für <code>organize</code>)<br>"
            "3.  Modus wählen<br>"
            "4.  Dry-Run AN lassen<br>"
            "5.  Start → Log prüfen<br>"
            "6.  Häkchen weg → echter Lauf"
        )

        c_layout.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        # Checkbox "bei jedem Start anzeigen"
        self.cb_show_on_start = QCheckBox(
            "Diese Anleitung bei jedem Start anzeigen")
        self.cb_show_on_start.setStyleSheet(
            f"color: {self.TEXT}; background-color: {self.BG}; "
            f"padding: 8px; font-family: {self.FONT};")
        cfg = load_config()
        self.cb_show_on_start.setChecked(
            cfg.get("show_guide_on_start", False))
        outer.addWidget(self.cb_show_on_start, 0)

        btn_row, _ = make_styled_buttons_row(
            self,
            [("ok", "OK", "ok", True)],
            ok_callback=self._on_ok,
        )
        outer.addLayout(btn_row)

    def reject(self):
        try:
            while QApplication.overrideCursor() is not None:
                QApplication.restoreOverrideCursor()
        except Exception:
            pass
        super().reject()

    def _on_ok(self):
        self.show_on_start = self.cb_show_on_start.isChecked()
        self.accept()

    def _set_app_icon(self):
        icon_path = SCRIPT_DIR / APP_ICON_FILE
        if icon_path.exists():
            icon = QIcon(str(icon_path))
            if not icon.isNull():
                self.setWindowIcon(icon)

    def _build_stylesheet(self) -> str:
        return """
            QDialog {{ background-color: {bg}; }}
            QLabel {{ color: {text}; background: transparent; }}
            QCheckBox {{ color: {text}; background: transparent; }}
            QScrollArea {{ background-color: {bg}; border: none; }}
            QScrollBar:vertical {{
                background: {bg}; width: 12px; margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: #444444; border-radius: 6px; min-height: 24px;
            }}
            QScrollBar::handle:vertical:hover {{ background: #666666; }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{ height: 0; background: none; }}
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {{ background: none; }}
            QDialogButtonBox QPushButton {{
                color: {text};
                background-color: {box_bg};
                border: 1px solid {box_bord};
                padding: 6px 18px;
                border-radius: 4px;
                min-width: 80px;
                outline: none;
            }}
            QDialogButtonBox QPushButton:hover {{ background-color: #2a2a2a; }}
            QDialogButtonBox QPushButton:pressed {{ background-color: #333333; }}
            QDialogButtonBox QPushButton:focus {{
                border: 1px solid {accent};
                outline: none;
            }}
        """.format(
            bg=self.BG, text=self.TEXT,
            box_bg=self.BOX_BG, box_bord=self.BOX_BORD,
            accent=self.ACCENT,
        )

# -----------------------------------------------------------------------------
# Hauptfenster
# -----------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(
            "PictureChronoSort — Foto- & Video-Organizer v6.0")
        # Fenstergröße auf max. 90 % der Bildschirmhöhe begrenzen
        try:
            screen = QApplication.primaryScreen().availableGeometry()
            max_h = int(screen.height() * 0.9)
            max_w = min(1100, int(screen.width() * 0.9))
            self.setGeometry(100, 100, max_w, max_h)
        except Exception:
            self.setGeometry(100, 100, 1100, 900)
        self.worker = None
        self.undo_worker = None
        self._current_phase = "Bereit"
        self._pending_fix = None
        self._pending_text = None

        self._set_app_icon()
        self._init_ui()
        self._load_config()

    def _set_app_icon(self):
        icon_path = SCRIPT_DIR / APP_ICON_FILE
        if icon_path.exists():
            icon = QIcon(str(icon_path))
            if not icon.isNull():
                self.setWindowIcon(icon)

    def _init_ui(self):
        cfg = load_config()
        if cfg.get("show_guide_on_start", False):
            QApplication.processEvents()
            try:
                self._show_guide()
            except Exception as e:
                print(f"Anleitung-Dialog-Fehler: {e}")

        # Globales Stylesheet: schwarzer Hintergrund für Container,
        # klar unterscheidbare Widgets mit Rahmen und Farben.
        self.setStyleSheet("""
            /* ---------- Fenster & Container ---------- */
            QMainWindow, QWidget#centralWidget, QWidget#bodyWidget {
                background-color: #000000;
            }

            /* ---------- Gruppen-Boxen ---------- */
            QGroupBox {
                background-color: #141414;
                color: #ffffff;
                border: 1px solid #3a3a3a;
                border-radius: 6px;
                margin-top: 14px;
                padding-top: 12px;
                padding-left: 8px;
                padding-right: 8px;
                padding-bottom: 8px;
                font-weight: bold;
            }
            QGroupBox::title {
                color: #4ea1ff;
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 12px;
                padding: 0 6px;
                background-color: #000000;
            }

            /* ---------- Labels ---------- */
            QLabel {
                color: #e0e0e0;
                background: transparent;
            }

            /* ---------- Eingabefelder ---------- */
            QLineEdit {
                background-color: #1e1e1e;
                color: #ffffff;
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 5px 8px;
                selection-background-color: #4ea1ff;
                selection-color: #ffffff;
            }
            QLineEdit:focus {
                border: 1px solid #4ea1ff;
            }
            QLineEdit:disabled {
                background-color: #111111;
                color: #666666;
                border: 1px solid #333333;
            }

            /* ---------- ComboBox ---------- */
            QComboBox {
                background-color: #1e1e1e;
                color: #ffffff;
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 5px 8px;
                min-width: 120px;
            }
            QComboBox:focus {
                border: 1px solid #4ea1ff;
            }
            QComboBox:disabled {
                background-color: #111111;
                color: #666666;
            }
            QComboBox::drop-down {
                border: none;
                width: 24px;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 6px solid #cccccc;
                width: 0;
                height: 0;
                margin-right: 8px;
            }
            QComboBox QAbstractItemView {
                background-color: #1e1e1e;
                color: #ffffff;
                border: 1px solid #555555;
                selection-background-color: #4ea1ff;
                selection-color: #ffffff;
                outline: none;
            }

            /* ---------- TextEdit (Log) ---------- */
            QTextEdit {
                background-color: #0f0f0f;
                color: #d0d0d0;
                border: 1px solid #3a3a3a;
                border-radius: 4px;
                padding: 4px;
                selection-background-color: #4ea1ff;
                selection-color: #ffffff;
            }

            /* ---------- Standard-Buttons (grau) ---------- */
            QPushButton {
                background-color: #2a2a2a;
                color: #ffffff;
                border: none;
                border-radius: 4px;
                padding: 4px 12px;
                min-height: 20px;
            }
            QPushButton:hover {
                background-color: #3a3a3a;
            }
            QPushButton:pressed {
                background-color: #1a1a1a;
            }
            QPushButton:disabled {
                background-color: #1a1a1a;
                color: #666666;
            }
            QPushButton:focus {
                border: 2px solid #ffffff;
            }

            /* ---------- Spezial-Buttons ---------- */
            QPushButton#startBtn {
                background-color: #1e6b34;
                border: none;
                font-weight: bold;
                padding: 6px 14px;
            }
            QPushButton#startBtn:focus {
                border: 2px solid #ffffff;
            }
            QPushButton#startBtn:hover {
                background-color: #2e8e44;
                border: 1px solid #3db35f;
            }
            QPushButton#startBtn:disabled {
                background-color: #143a1c;
                color: #666666;
                border: 1px solid #1a4a26;
            }

            QPushButton#comboBtn {
                background-color: #1e5a9e;
                border: none;
                font-weight: bold;
                padding: 6px 14px;
            }
            QPushButton#comboBtn:focus {
                border: 2px solid #ffffff;
            }
            QPushButton#comboBtn:hover {
                background-color: #2a6bbf;
                border: 1px solid #6bb3ff;
            }
            QPushButton#comboBtn:disabled {
                background-color: #1a2a3a;
                color: #666666;
                border: 1px solid #2a3a4a;
            }

            QPushButton#cancelBtn {
                background-color: #c0392b;
                border: none;
            }
            QPushButton#cancelBtn:focus {
                border: 2px solid #ffffff;
            }
            QPushButton#cancelBtn:hover {
                background-color: #e74c3c;
                border: 1px solid #e74c3c;
            }
            QPushButton#cancelBtn:disabled {
                background-color: #3a1a15;
                color: #888888;
                border: 1px solid #3a1a15;
            }

            QPushButton#undoBtn {
                background-color: #a06b00;
                border: none;
            }
            QPushButton#undoBtn:focus {
                border: 2px solid #ffffff;
            }
            QPushButton#undoBtn:hover {
                background-color: #c08000;
                border: 1px solid #c08000;
            }

            QPushButton#toolBtn {
                background-color: #1e3a5f;
                border: none;
            }
            QPushButton#toolBtn:focus {
                border: 2px solid #ffffff;
            }
            QPushButton#toolBtn:hover {
                background-color: #2a4a6f;
                border: 1px solid #4ea1ff;
            }

            QPushButton#helpBtn {
                background-color: #4a2a7f;
                border: none;
            }
            QPushButton#helpBtn:focus {
                border: 2px solid #ffffff;
            }
            QPushButton#helpBtn:hover {
                background-color: #5a3a8f;
                border: 1px solid #7a5aaf;
            }

            QPushButton#infoBtn {
                background-color: #7f2a6a;
                border: none;
            }
            QPushButton#infoBtn:focus {
                border: 2px solid #ffffff;
            }
            QPushButton#infoBtn:hover {
                background-color: #8f3a7a;
                border: 1px solid #af5a9a;
            }

            /* ---------- Fortschrittsbalken ---------- */
            QProgressBar {
                background-color: #1e1e1e;
                color: #ffffff;
                border: 1px solid #555555;
                border-radius: 5px;
                text-align: center;
                font-weight: bold;
                font-size: 10pt;
                min-height: 28px;
            }
            QProgressBar::chunk {
                background-color: #4CAF50;
                border-radius: 4px;
            }

            /* ---------- Scrollbars ---------- */
            QScrollBar:vertical {
                background: #0f0f0f;
                width: 12px;
                margin: 0;
                border: none;
            }
            QScrollBar::handle:vertical {
                background: #444444;
                border-radius: 6px;
                min-height: 24px;
            }
            QScrollBar::handle:vertical:hover {
                background: #666666;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0;
                background: none;
            }
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {
                background: none;
            }
            QScrollBar:horizontal {
                background: #0f0f0f;
                height: 12px;
                margin: 0;
                border: none;
            }
            QScrollBar::handle:horizontal {
                background: #444444;
                border-radius: 6px;
                min-width: 24px;
            }
            QScrollBar::handle:horizontal:hover {
                background: #666666;
            }
            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal {
                width: 0;
                background: none;
            }
            QScrollBar::add-page:horizontal,
            QScrollBar::sub-page:horizontal {
                background: none;
            }

            /* ---------- Tooltip ---------- */
            QToolTip {
                background-color: #1e1e1e;
                color: #ffffff;
                border: 1px solid #4ea1ff;
                padding: 4px;
                border-radius: 3px;
            }
        """)

        central = QWidget()
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ---- HEADER (fix, nicht scrollbar) -----------------------------
        header = HeaderWidget(
            title="PictureChronoSort — Foto und Video Organizer",
            logo_file=LOGO_FILE,
            icon_file=RIGHT_ICON_FILE,
            logo_size=80,
            icon_size=80,
        )
        layout.addWidget(header, 0)

        # ---- Body in ScrollArea ----------------------------------------
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setObjectName("mainScrollViewport")
        scroll.viewport().setStyleSheet(
            "#mainScrollViewport { background-color: #000000; }"
        )

        body = QWidget()
        body.setObjectName("bodyWidget")
        body.setStyleSheet("#bodyWidget { background-color: #000000; }")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(12, 12, 12, 12)
        body_layout.setSpacing(6)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)

        # Modus
        mode_group = QGroupBox("Modus")
        ml = QFormLayout()
        self.mode_combo = QComboBox()
        self.mode_combo.addItems([m[1] for m in MODES])
        self.mode_combo.currentIndexChanged.connect(self._on_mode_change)
        ml.addRow("Modus:", self.mode_combo)
        mode_group.setLayout(ml)
        body_layout.addWidget(mode_group)

        # Quelle
        src_group = QGroupBox("Quellordner")
        sl = QHBoxLayout()
        self.source_line = QLineEdit()
        b1 = QPushButton("Durchsuchen…")
        b1.clicked.connect(self._select_source)
        sl.addWidget(self.source_line)
        sl.addWidget(b1)
        src_group.setLayout(sl)
        body_layout.addWidget(src_group)

        # Ziel
        dst_group = QGroupBox("Zielordner (Fotoarchiv)")
        dl = QHBoxLayout()
        self.dest_line = QLineEdit()
        b2 = QPushButton("Durchsuchen…")
        b2.clicked.connect(self._select_dest)
        dl.addWidget(self.dest_line)
        dl.addWidget(b2)
        dst_group.setLayout(dl)
        body_layout.addWidget(dst_group)

        # Dry-Run: separat, damit immer sichtbar
        self.dry_run_check = QCheckBox("Nur Testlauf (Dry-Run)")
        self.dry_run_check.setChecked(True)
        body_layout.addWidget(self.dry_run_check)

        # Optionen
        opt_group = QGroupBox("Optionen")
        self.opt_group = opt_group
        ol = QFormLayout()

        self.filename_check = QCheckBox(
            "Datum aus Dateinamen (WhatsApp, Screenshots, …)")
        self.filename_check.setChecked(True)
        ol.addRow(self.filename_check)

        self.livephoto_check = QCheckBox(
            "Live-Photo-Gruppierung (HEIC+MOV als Paar)")
        self.livephoto_check.setChecked(True)
        ol.addRow(self.livephoto_check)

        self.aae_check = QCheckBox(
            "AAE-Bearbeitungsdateien mit archivieren (empfohlen)")
        self.aae_check.setChecked(True)
        ol.addRow(self.aae_check)

        self.remove_lp_mov_check = QCheckBox(
            "Live-Photo MOV/MP4 entfernen (nutzt Duplikat-Aktion)")
        ol.addRow(self.remove_lp_mov_check)

        self.fs_fallback_check = QCheckBox(
            "Wenn gar kein Datum: mtime verwenden "
            "(Achtung: oft Import-, nicht Aufnahmedatum)")
        ol.addRow(self.fs_fallback_check)

        self.keep_filename_text_check = QCheckBox(
            "Im Finder eingegebenen Text aus Dateinamen übernehmen "
            "(z. B. 'Hallo Fasching' aus '2024-12-25_002813 Hallo Fasching.jpg')")
        self.keep_filename_text_check.setChecked(True)
        ol.addRow(self.keep_filename_text_check)

        self.dup_action_combo = QComboBox()
        self.dup_action_combo.addItems([
            "skip — Duplikat überspringen",
            "move — Duplikat in _Duplikate-Ordner verschieben (empfohlen)",
            "trash — Duplikat in macOS-Papierkorb (nur internes LW)",
            "delete — Duplikat sofort löschen (⚠️ keine Wiederherstellung)",
        ])
        self.dup_action_combo.setCurrentIndex(1)
        ol.addRow("Duplikat-Aktion:", self.dup_action_combo)

        opt_group.setLayout(ol)
        body_layout.addWidget(opt_group)

        # ---- Zeile 1: Hauptaktionen --------------------------------
        self.start_btn = QPushButton("Verarbeitung starten")
        self.start_btn.setObjectName("startBtn")
        self.start_btn.clicked.connect(self._start_processing)

        self.combo_btn = QPushButton("Erst organisieren, dann Text setzen")
        self.combo_btn.setObjectName("comboBtn")
        self.combo_btn.clicked.connect(self._start_combo_organize_text)

        self.cancel_btn = QPushButton("Abbrechen")
        self.cancel_btn.setObjectName("cancelBtn")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel_processing)

        self.undo_btn = QPushButton("Undo…")
        self.undo_btn.setObjectName("undoBtn")
        self.undo_btn.clicked.connect(self._do_undo)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addWidget(self.start_btn, 3)
        btn_row.addWidget(self.combo_btn, 3)
        btn_row.addWidget(self.cancel_btn, 1)
        btn_row.addWidget(self.undo_btn, 1)
        body_layout.addLayout(btn_row)

        # ---- Zeile 2: Werkzeuge -----------------------------------
        self.fix_date_btn = QPushButton("Datum korrigieren…")
        self.fix_date_btn.setObjectName("toolBtn")
        self.fix_date_btn.clicked.connect(self._open_date_fix)
        self.text_btn = QPushButton("Text/Ort setzen…")
        self.text_btn.setObjectName("toolBtn")
        self.text_btn.clicked.connect(self._open_text_dialog)
        self.check_date_btn = QPushButton("Datum prüfen…")
        self.check_date_btn.setObjectName("toolBtn")
        self.check_date_btn.clicked.connect(self._check_dates)
        self.debug_btn = QPushButton("Metadaten prüfen…")
        self.debug_btn.setObjectName("toolBtn")
        self.debug_btn.clicked.connect(self._debug_single_file)
        self.help_btn = QPushButton("Hilfe / Anleitung…")
        self.help_btn.setObjectName("helpBtn")
        self.help_btn.clicked.connect(self._show_guide)
        self.info_btn = QPushButton("Info…")
        self.info_btn.setObjectName("infoBtn")
        self.info_btn.clicked.connect(self._show_info)
        self.clean_meta_btn = QPushButton("Metadaten bereinigen…")
        self.clean_meta_btn.setObjectName("toolBtn")
        self.clean_meta_btn.clicked.connect(self._open_clean_metadata)

        tools_row = QHBoxLayout()
        tools_row.setSpacing(8)
        tools_row.addWidget(self.fix_date_btn, 2)
        tools_row.addWidget(self.text_btn, 2)
        tools_row.addWidget(self.check_date_btn, 2)
        tools_row.addWidget(self.debug_btn, 2)
        tools_row.addWidget(self.help_btn, 2)
        tools_row.addWidget(self.info_btn, 1)
        tools_row.addWidget(self.clean_meta_btn, 2)
        body_layout.addLayout(tools_row)

        # ---- Status --------------------------------------------------
        self.phase_label = QLabel("Bereit.")
        self.phase_label.setStyleSheet(
            "font-weight: bold; font-size: 12pt; padding: 4px; "
            "color: #4ea1ff; background: transparent;")
        body_layout.addWidget(self.phase_label)

        self.file_label = QLabel("Datei 0 von 0")
        self.file_label.setStyleSheet(
            "font-size: 12pt; padding: 2px; color: #a0a0a0; "
            "background: transparent;")
        body_layout.addWidget(self.file_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setMinimumHeight(32)
        self.progress_bar.setFormat("%p%")
        # Styling kommt aus dem globalen Stylesheet
        body_layout.addWidget(self.progress_bar)

        self.eta_label = QLabel("")
        self.eta_label.setStyleSheet(
            "font-family: Menlo, Monaco, monospace; font-size: 12pt; "
            "font-weight: bold; padding: 4px; color: #2e9e4f; "
            "background: transparent;")
        body_layout.addWidget(self.eta_label)

        # ---- Log ----------------------------------------------------
        log_group = QGroupBox("Log (wird in logs/ gespeichert)")
        ll = QVBoxLayout()
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMinimumHeight(150)
        self.log_text.setStyleSheet(
            "font-family: Menlo, Monaco, monospace; font-size: 12pt; "
            "background-color: #0f0f0f; color: #d0d0d0; "
            "border: 1px solid #3a3a3a; border-radius: 4px;")
        ll.addWidget(self.log_text)
        log_group.setLayout(ll)
        body_layout.addWidget(log_group, 1)

    # ------------------------------------------------------------------
    def _show_guide(self):
        dlg = AnleitungDialog(self)
        dlg.exec()
        if dlg.show_on_start:
            cfg = load_config()
            cfg["show_guide_on_start"] = True
            save_config(cfg)
        else:
            cfg = load_config()
            cfg["show_guide_on_start"] = False
            save_config(cfg)

    def _show_info(self):
        dlg = InfoDialog(self)
        dlg.exec()

    def _on_mode_change(self):
        mode = INDEX_MODE[self.mode_combo.currentIndex()]
        self.dest_line.setEnabled(mode == "organize")
        self.remove_lp_mov_check.setEnabled(mode == "organize")

        # Optionen nur bei den Modi zeigen, die sie brauchen
        if mode in ("fix_capture_date", "add_text", "clean_metadata"):
            self.opt_group.setVisible(False)
        else:
            self.opt_group.setVisible(True)

        # ---- Buttons je nach Modus ein-/ausblenden --------------------
        self.start_btn.setVisible(True)
        self.combo_btn.setVisible(mode == "organize")
        self.fix_date_btn.setVisible(
            mode in ("organize", "fix_capture_date", "timestamps_only"))
        self.text_btn.setVisible(mode in ("organize", "add_text"))
        self.check_date_btn.setVisible(True)
        self.debug_btn.setVisible(True)
        self.help_btn.setVisible(True)
        self.undo_btn.setVisible(True)
        self.cancel_btn.setVisible(True)
        self.clean_meta_btn.setVisible(mode in ("organize", "clean_metadata"))

    def _load_config(self):
        cfg = load_config()
        if cfg.get("source"): self.source_line.setText(cfg["source"])
        if cfg.get("dest"): self.dest_line.setText(cfg["dest"])
        if "dry_run" in cfg: self.dry_run_check.setChecked(cfg["dry_run"])
        if "fs_fallback" in cfg: self.fs_fallback_check.setChecked(cfg["fs_fallback"])
        if "use_filename" in cfg: self.filename_check.setChecked(cfg["use_filename"])
        if "use_livephoto" in cfg: self.livephoto_check.setChecked(cfg["use_livephoto"])
        if "use_aae" in cfg: self.aae_check.setChecked(cfg["use_aae"])
        if "remove_lp_mov" in cfg: self.remove_lp_mov_check.setChecked(cfg["remove_lp_mov"])
        if "mode" in cfg:
            self.mode_combo.setCurrentIndex(
                MODE_INDEX.get(cfg["mode"], 0))
        if "dup_action" in cfg:
            idx_map = {"skip": 0, "move": 1, "trash": 2, "delete": 3}
            self.dup_action_combo.setCurrentIndex(
                idx_map.get(cfg["dup_action"], 1))
        self._on_mode_change()

    def _save_config(self):
        dup_map = {0: "skip", 1: "move", 2: "trash", 3: "delete"}
        save_config({
            "source": self.source_line.text().strip(),
            "dest": self.dest_line.text().strip(),
            "dry_run": self.dry_run_check.isChecked(),
            "fs_fallback": self.fs_fallback_check.isChecked(),
            "use_filename": self.filename_check.isChecked(),
            "use_livephoto": self.livephoto_check.isChecked(),
            "use_aae": self.aae_check.isChecked(),
            "remove_lp_mov": self.remove_lp_mov_check.isChecked(),
            "mode": INDEX_MODE[self.mode_combo.currentIndex()],
            "dup_action": dup_map[self.dup_action_combo.currentIndex()],
        })

    def _select_source(self):
        p = QFileDialog.getExistingDirectory(self, "Quellordner")
        if p: self.source_line.setText(p)

    def _select_dest(self):
        p = QFileDialog.getExistingDirectory(self, "Zielordner")
        if p: self.dest_line.setText(p)

    # ------------------------------------------------------------------
    def _prepare_worker_start(self):
        """Gemeinsame Vorbereitung: Buttons deaktivieren, Fortschritt
        zurücksetzen, Log leeren."""
        self.start_btn.setEnabled(False)
        self.combo_btn.setEnabled(False)
        self.undo_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")
        self.phase_label.setText("Starte…")
        self.file_label.setText("Datei 0 von 0")
        self.eta_label.setText("")
        self.log_text.clear()

    def _reset_ui_after_finish(self):
        """Stellt den Ausgangszustand wieder her."""
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")
        self.phase_label.setText("Bereit.")
        self.file_label.setText("Datei 0 von 0")
        self.eta_label.setText("")
        self._current_phase = "Bereit"
        mode = INDEX_MODE[self.mode_combo.currentIndex()]
        self.combo_btn.setEnabled(mode == "organize")
        # Sicherheitsnetz: Cursor zurücksetzen
        try:
            while QApplication.overrideCursor() is not None:
                QApplication.restoreOverrideCursor()
        except Exception:
            pass

    # ------------------------------------------------------------------
    def _start_processing(self):
        source = Path(self.source_line.text().strip())
        dest_text = self.dest_line.text().strip()
        mode = INDEX_MODE[self.mode_combo.currentIndex()]
        dup_action = {0: "skip", 1: "move", 2: "trash", 3: "delete"}[
            self.dup_action_combo.currentIndex()]

        if not source.is_dir():
            MessageDialog.show_warn(self, "Ungültiger Quellordner",
                "Der angegebene Quellordner existiert nicht oder "
                "ist kein Ordner.\n\n"
                "Bitte wähle einen gültigen Quellordner aus.")
            return

        # Zielordner-Logik je nach Modus
        if mode in ("fix_capture_date", "add_text", "dedupe_only",
                    "timestamps_only", "clean_metadata"):
            dest = source
        elif mode == "organize":
            if not dest_text:
                MessageDialog.show_warn(self, "Zielordner fehlt",
                    "Im Modus 'organize' muss ein Zielordner "
                    "angegeben werden.\n\n"
                    "Bitte wähle einen Zielordner aus.")
                return
            dest = Path(dest_text)
            src_n, dst_n = _norm(source), _norm(dest)
            if src_n.startswith(dst_n + os.sep):
                MessageDialog.show_error(self, "Ungültige Ordnerstruktur",
                    "Der Quellordner liegt innerhalb des Zielordners.\n\n"
                    "Das würde dazu führen, dass alle Quelldateien "
                    "ausgeschlossen werden. Bitte wähle einen "
                    "Zielordner, der außerhalb des Quellordners liegt.")
                return
            try:
                dest.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                MessageDialog.show_error(self, "Zielordner-Fehler",
                    f"Der Zielordner konnte nicht angelegt werden:\n\n{e}")
                return
        else:
            dest = source

        # Duplikat-Logik nur für organize und dedupe_only
        dup_folder = None
        internal_source = is_internal_volume(source)
        if mode in ("organize", "dedupe_only"):
            if dup_action == "trash" and not internal_source:
                ok = MessageDialog.ask_okcancel(
                    self, "Papierkorb auf externem Laufwerk",
                    f"Der Quellordner liegt auf einem externen "
                    f"Laufwerk:<br><b>{source}</b><br><br>"
                    f"Der macOS-Papierkorb ist auf externen Laufwerken "
                    f"oft nicht zugänglich (versteckter "
                    f".Trashes-Ordner).<br><br>"
                    f"<b>OK</b> = automatisch auf 'move' umstellen<br>"
                    f"<b>Abbrechen</b> = zurück und Einstellung "
                    f"manuell ändern",
                    default="cancel")
                if not ok:
                    return
                dup_action = "move"
                self.dup_action_combo.setCurrentIndex(1)

            if dup_action == "trash" and not SEND2TRASH_AVAILABLE:
                ok = MessageDialog.ask_okcancel(
                    self, "send2trash fehlt",
                    "Die Bibliothek 'send2trash' ist nicht installiert.<br>"
                    "Duplikate können deshalb nicht in den Papierkorb "
                    "verschoben werden.<br><br>"
                    "<b>OK</b> = mit 'move' als Fallback fortfahren<br>"
                    "<b>Abbrechen</b> = abbrechen<br><br>"
                    "Installation: <code>pip install send2trash</code>",
                    default="cancel")
                if not ok:
                    return
                dup_action = "move"

            if dup_action in ("move", "trash"):
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                dup_folder = source / f"_Duplikate_{ts}"

        # Fix-, Text- oder Clean-Daten
        fix_data = None
        text_data = None
        clean_data = None
        if mode == "fix_capture_date":
            fix_data = self._pending_fix
            if not fix_data:
                self._open_date_fix()
                return
        elif mode == "add_text":
            text_data = self._pending_text
            if not text_data:
                self._open_text_dialog()
                return
        elif mode == "clean_metadata":
            clean_data = getattr(self, "_pending_clean", None)
            if not clean_data:
                self._open_clean_metadata()
                return

        self._save_config()

        # ---- Sicherheitsabfrage bei echtem Lauf --------------------------
        is_dry_run = self.dry_run_check.isChecked()
        if not is_dry_run:
            # Nur bei destruktiven Modi nachfragen
            destructive_modes = ("organize", "dedupe_only",
                                 "fix_capture_date", "add_text")
            if mode in destructive_modes:
                count_hint = ""
                try:
                    active_exts = set(SUPPORTED_EXTS)
                    if self.aae_check.isChecked():
                        active_exts.add(AAE_EXT)
                    count = sum(1 for f in source.rglob("*")
                                if f.is_file()
                                and f.suffix.lower() in active_exts)
                    count_hint = (f"<br><br>Gefundene Dateien: "
                                  f"<b>{count}</b>")
                except Exception:
                    pass
                ok = MessageDialog.ask_yesno(
                    self, "Echter Lauf bestätigen",
                    f"Du hast den <b>Testlauf (Dry-Run) deaktiviert</b>. "
                    f"Das bedeutet, dass Dateien <b>tatsächlich "
                    f"verändert</b> werden."
                    f"{count_hint}<br><br>"
                    f"<b>Modus:</b> {mode}<br>"
                    f"<b>Quelle:</b><br>{source}<br>"
                    + (f"<b>Ziel:</b><br>{dest}<br>"
                       if mode == "organize" else "")
                    + f"<br>Möchtest du den echten Lauf wirklich starten?",
                    default="no")
                if not ok:
                    return

        self._prepare_worker_start()

        self.worker = OrganizerWorker(
            source, dest,
            self.dry_run_check.isChecked(),
            self.fs_fallback_check.isChecked(),
            self.filename_check.isChecked(),
            self.livephoto_check.isChecked(),
            self.aae_check.isChecked(),
            self.remove_lp_mov_check.isChecked(),
            dup_action, dup_folder, mode, internal_source,
            fix_data=fix_data,
            text_data=text_data,
            clean_data=clean_data,
            keep_filename_text=self.keep_filename_text_check.isChecked(),
        )
        self.worker.log_message.connect(self._append_log)
        self.worker.progress_updated.connect(self._on_progress)
        self.worker.phase_updated.connect(self._on_phase)
        self.worker.eta_updated.connect(self._on_eta)
        self.worker.file_counter.connect(self._on_file_counter)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    # ------------------------------------------------------------------
    def _start_combo_organize_text(self):
        """Erst organizieren (Quelle→Ziel), dann Text im Zielordner setzen."""
        source = Path(self.source_line.text().strip())
        dest_text = self.dest_line.text().strip()

        if not source.is_dir():
            MessageDialog.show_warn(self, "Ungültiger Quellordner",
                "Der angegebene Quellordner existiert nicht oder "
                "ist kein Ordner.")
            return
        if not dest_text:
            MessageDialog.show_warn(self, "Zielordner fehlt",
                "Für den kombinierten Lauf wird ein Zielordner "
                "benötigt.")
            return
        dest = Path(dest_text)
        try:
            dest.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            MessageDialog.show_error(self, "Zielordner-Fehler",
                f"Der Zielordner konnte nicht angelegt werden:\n\n{e}")
            return

        # Text abfragen
        dlg = TextOrtDialog(self, source_dir=source)
        if dlg.exec() != QDialog.Accepted:
            return
        self._pending_text = dlg.result_data

        ok = MessageDialog.ask_yesno(
            self, "Kombinierten Lauf starten?",
            f"<b>Schritt 1:</b> organize (verschieben nach "
            f"Jahr/Monat, Zeitstempel setzen)<br>"
            f"<b>Schritt 2:</b> Text '{self._pending_text['text']}' "
            f"im Zielordner ergänzen<br><br>"
            f"<b>Quelle:</b> {source}<br>"
            f"<b>Ziel:</b> {dest}<br>"
            f"<b>Dry-Run:</b> "
            f"{'JA' if self.dry_run_check.isChecked() else 'NEIN'}<br><br>"
            f"Fortfahren?",
            default="no")
        if not ok:
            return

        # Modus auf organize stellen und normal starten.
        # Nach dem organize-Lauf wird add_text im Zielordner
        # automatisch gestartet (über _on_finished mit Kennzeichnung).
        self.mode_combo.setCurrentIndex(MODE_INDEX["organize"])
        self._combo_pending_text = True
        self._combo_dest_dir = dest
        self._start_processing()

    # ------------------------------------------------------------------
    def _open_date_fix(self):
        source = Path(self.source_line.text().strip())
        if not source.is_dir():
            MessageDialog.show_warn(self, "Quellordner fehlt",
                "Bitte zuerst einen gültigen Quellordner auswählen.")
            return

        dlg = DatumKorrekturDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return

        data = dlg.result_data
        self._pending_fix = data
        self.mode_combo.setCurrentIndex(
            MODE_INDEX["fix_capture_date"])

        parts = []
        if data["set_year"] is not None:
            parts.append(f"Jahr={data['set_year']}")
        if data["set_month"] is not None:
            parts.append(f"Monat={data['set_month']}")
        if data["set_day"] is not None:
            parts.append(f"Tag={data['set_day']}")
        if data["set_hour"] is not None:
            parts.append(f"Stunde={data['set_hour']}")
        if data["set_minute"] is not None:
            parts.append(f"Minute={data['set_minute']}")
        if data["set_second"] is not None:
            parts.append(f"Sekunde={data['set_second']}")
        if (data["add_hours"] != 0 or data["add_minutes"] != 0
                or data["add_seconds"] != 0):
            parts.append(f"Verschiebung: {data['add_hours']:+d}h "
                         f"{data['add_minutes']:+d}min "
                         f"{data['add_seconds']:+d}s")

        rename_mode = data.get("rename_mode", "keep")
        rename_text = {
            "keep": "Dateiname unverändert",
            "replace": "Zeitstempel im Namen ersetzen",
            "prefix": "Zeitstempel voranstellen",
            "suffix": "Zeitstempel anhängen",
        }.get(rename_mode, "unbekannt")
        parts.append(f"Dateiname: {rename_text}")

        dry_text = ("DRY-RUN (nichts wird geändert)"
                    if self.dry_run_check.isChecked()
                    else "ECHTER LAUF — Dateien werden verändert!")

        ok = MessageDialog.ask_yesno(
            self, "Datum/Uhrzeit korrigieren bestätigen",
            f"<b>Änderungen:</b> {', '.join(parts) if parts else '—'}<br>"
            f"<b>Dateisystem-Zeitstempel mit anpassen:</b> "
            f"{'JA' if data['also_fs'] else 'NEIN'}<br><br>"
            f"{dry_text}<br><br>"
            f"<b>Quellordner:</b><br>{source}<br><br>"
            f"Möchtest du fortfahren?",
            default="no")
        if not ok:
            return

        backup = MessageDialog.ask_yesno(
            self, "Sicherheitskopie anlegen?",
            "Möchtest du vor der Änderung eine Sicherheitskopie "
            "der alten EXIF-Datumswerte anlegen?<br><br>"
            "Empfohlen: <b>Ja</b> — schreibt eine Textdatei mit "
            "allen alten Datumswerten in den <code>logs/</code>-Ordner.",
            default="yes")
        if backup:
            self._backup_exif_dates(source)

        self._start_processing()

    def _open_text_dialog(self):
        source = Path(self.source_line.text().strip())
        if not source.is_dir():
            MessageDialog.show_warn(self, "Quellordner fehlt",
                "Bitte zuerst einen gültigen Quellordner auswählen.")
            return

        dlg = TextOrtDialog(self, source_dir=source)
        if dlg.exec() != QDialog.Accepted:
            return
        self._pending_text = dlg.result_data
        self.mode_combo.setCurrentIndex(MODE_INDEX["add_text"])

        ok = MessageDialog.ask_yesno(
            self, "Text/Ort setzen bestätigen",
            f"<b>Text:</b> {self._pending_text['text']}<br>"
            f"<b>Alten Text behalten:</b> "
            f"{'JA' if self._pending_text['keep_old_text'] else 'NEIN'}<br>"
            f"<b>Originalname anzeigen:</b> "
            f"{'JA' if self._pending_text['keep_meaningful_original'] else 'NEIN'}"
            f"<br><br>"
            f"<b>Quellordner:</b><br>{source}<br><br>"
            f"Möchtest du fortfahren?",
            default="no")
        if not ok:
            return

        self._start_processing()

    def _open_clean_metadata(self):
        source = Path(self.source_line.text().strip())
        if not source.is_dir():
            MessageDialog.show_warn(self, "Quellordner fehlt",
                "Bitte zuerst einen gültigen Quellordner auswählen.")
            return

        dlg = CleanMetadataDialog(self, source_dir=source)
        if dlg.exec() != QDialog.Accepted:
            return
        self._pending_clean = dlg.result_data
        self.mode_combo.setCurrentIndex(MODE_INDEX["clean_metadata"])

        # Bestätigung mit Vorschau
        xp = "JA" if self._pending_clean["clean_xptitle"] else "NEIN"
        img = ("JA" if self._pending_clean["clean_image_description"]
               else "NEIN")
        dry_text = ("DRY-RUN (nichts wird geändert)"
                    if self.dry_run_check.isChecked()
                    else "ECHTER LAUF — Dateien werden verändert!")
        ok = MessageDialog.ask_yesno(
            self, "Metadaten bereinigen bestätigen",
            f"<b>XPTitle löschen (alter Dateiname):</b> {xp}<br>"
            f"<b>ImageDescription löschen (Text/Beschreibung):</b> {img}"
            f"<br><br>"
            f"{dry_text}<br><br>"
            f"<b>Quellordner:</b><br>{source}<br><br>"
            f"Möchtest du fortfahren?",
            default="no")
        if not ok:
            return

        self._start_processing()

    def _backup_exif_dates(self, source: Path):
        log_dir = LOG_DIR
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        out = log_dir / f"exif_backup_{ts}.txt"
        count = 0
        try:
            with open(out, "w", encoding="utf-8") as f:
                for p in sorted(source.rglob("*")):
                    if not p.is_file():
                        continue
                    if p.suffix.lower() not in SUPPORTED_EXTS:
                        continue
                    if p.suffix.lower() == AAE_EXT:
                        continue
                    try:
                        dt = exiftool_get_datetime_original(p)
                        if dt:
                            f.write(f"{p}\t{dt.isoformat()}\n")
                            count += 1
                    except Exception:
                        continue
            MessageDialog(
                self, "Sicherheitskopie",
                f"<b>{count}</b> Datumswerte gesichert in:<br><br>{out}",
                kind=MessageDialog.INFO, buttons="ok",
                log_path=log_dir,
            ).exec ()

        except Exception as e:
            MessageDialog.show_error(self, "Sicherheitskopie",
                f"Die Sicherheitskopie konnte nicht angelegt werden:"
                f"<br><br>{e}")

    def _check_dates(self):
        source = Path(self.source_line.text().strip())
        if not source.is_dir():
            MessageDialog.show_warn(self, "Quellordner fehlt",
                "Bitte zuerst einen gültigen Quellordner auswählen.")
            return

        self.log_text.append("\n=== Datum-Prüfung ===")

        worker = CheckDatesWorker(source)
        self._check_worker = worker

        # Fortschrittsdialog (nicht-blockierend für Worker)
        dlg = MessageDialog(
            self,
            "Datum-Prüfung läuft",
            "Prüfe Dateien…",
            kind=MessageDialog.INFO,
            buttons="cancel",
            show_progress=True,
            progress_indeterminate=False,
            cancel_callback=worker.cancel,
        )
        dlg.show()

        def on_progress(current, total):
            dlg.set_progress_value(int(current / total * 100) if total > 0 else 0)
            dlg.set_message(
                f"Prüfe Datei <b>{current}</b> von <b>{total}</b>…")

        def on_finished(result):
            try:
                dlg.accept()
            except Exception:
                pass
            if "error" in result:
                MessageDialog.show_error(self, "Datum-Prüfung",
                    f"Fehler bei der Prüfung:<br><br>{result['error']}")
                return
            total = result.get("total", 0)
            suspicious = result.get("suspicious", [])
            cancelled = result.get("cancelled", False)
            checked = result.get("checked", total)

            if cancelled:
                self.log_text.append(
                    f"Prüfung abgebrochen nach {checked} von {total} Dateien.")
                MessageDialog.show_warn(self, "Datum-Prüfung",
                    f"Prüfung abgebrochen.<br><br>"
                    f"Geprüft: {checked} von {total}")
                return

            log_dir = LOG_DIR
            if not suspicious:
                self.log_text.append(
                    f"Alle {total} Dateien haben plausible Daten.")
                MessageDialog(
                    self, "Datum-Prüfung",
                    f"Alle {total} Dateien haben plausible Datumswerte.",
                    kind=MessageDialog.INFO, buttons="ok",
                    log_path=log_dir,
                ).exec_()
            else:
                self.log_text.append(
                    f"{len(suspicious)} von {total} Dateien verdächtig:")
                for path, warn in suspicious:
                    try:
                        name = Path(path).name
                    except Exception:
                        name = path
                    self.log_text.append(f"  ⚠ {name}: {warn}")
                MessageDialog(
                    self, "Datum-Prüfung",
                    f"<b>{len(suspicious)} von {total}</b> Dateien sehen "
                    f"verdächtig aus.<br><br>"
                    f"Die Details stehen im Log-Fenster.",
                    kind=MessageDialog.WARN, buttons="ok",
                    log_path=log_dir,
                ).exec_()

        worker.progress.connect(on_progress)
        worker.finished_with.connect(on_finished)
        worker.start()

    def _debug_single_file(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "Datei", "", "Alle Dateien (*.*)")
        if not p:
            return
        path = Path(p)

        # Ladezustand anzeigen (kurz)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            meta = collect_metadata_batch([path]).get(path, {})
            parsed_date = extract_date_from_metadata(meta)
            filename_date = parse_filename_date(path.name)
            try:
                fs_mtime = datetime.fromtimestamp(
                    path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                fs_mtime = "?"
            xptitle = get_xptitle(path)
            image_description = get_image_description(path)
        finally:
            QApplication.restoreOverrideCursor()

        dlg = MetadataDialog(
            self, path, meta,
            parsed_date=parsed_date,
            filename_date=filename_date,
            fs_mtime=fs_mtime,
            xptitle=xptitle,
            image_description=image_description,
        )
        dlg.exec()

    # ------------------------------------------------------------------
    def _on_progress(self, pct: int):
        self.progress_bar.setValue(pct)
        self.progress_bar.setFormat(f"{self._current_phase}: {pct}%")

    def _on_phase(self, msg: str):
        self.phase_label.setText(msg)
        if ": " in msg:
            self._current_phase = msg.split(": ", 1)[1]
        else:
            self._current_phase = msg
        self.progress_bar.setFormat(f"{self._current_phase}: %p%")

    def _on_eta(self, eta_text: str):
        self.eta_label.setText(f"⏱  {eta_text}")

    def _on_file_counter(self, current: int, total: int):
        self.file_label.setText(f"Datei {current} von {total}")

    def _cancel_processing(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
        if self.undo_worker and self.undo_worker.isRunning():
            self.undo_worker.cancel()
        self.cancel_btn.setEnabled(False)
        self.eta_label.setText("Abbruch angefordert…")

    def _do_undo(self):
        log_dir = LOG_DIR
        start_dir = str(log_dir) if log_dir.exists() else str(SCRIPT_DIR)
        p, _ = QFileDialog.getOpenFileName(
            self, "Undo-Log auswählen", start_dir,
            "Undo-Logs (*.jsonl);;Alle Dateien (*)"
        )
        if not p:
            return
        ok = MessageDialog.ask_yesno(
            self, "Undo bestätigen",
            f"Alle Aktionen aus diesem Log rückgängig machen?<br><br>"
            f"<b>Datei:</b><br>{p}<br><br>"
            f"Hinweis: 'move', 'dup_move', 'rename_date' und "
            f"'fix_date' können rückgängig gemacht werden. "
            f"'trash' und 'delete' <b>nicht</b>.",
            default="no")
        if not ok:
            return
        self.log_text.append(f"\n=== Undo: {p} ===")
        self.start_btn.setEnabled(False)
        self.combo_btn.setEnabled(False)
        self.undo_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        self.phase_label.setText("Undo läuft…")
        self._current_phase = "Undo"

        self.undo_worker = UndoWorker(Path(p))
        self.undo_worker.log_message.connect(self._append_log)
        self.undo_worker.progress_updated.connect(self._on_progress)
        self.undo_worker.finished.connect(self._on_undo_finished)
        self.undo_worker.start()

    def _on_undo_finished(self, result: dict):
        self.start_btn.setEnabled(True)
        self.combo_btn.setEnabled(True)
        self.undo_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.phase_label.setText("Undo fertig.")
        if "error" in result:
            MessageDialog.show_error(self, "Undo-Fehler",
                f"Das Undo ist fehlgeschlagen:<br><br>{result['error']}")
        else:
            self.log_text.append(
                f"Undo abgeschlossen: {result['ok']} wiederhergestellt, "
                f"{result['fail']} fehlgeschlagen, "
                f"{result['skip']} übersprungen (von {result['total']})")
            MessageDialog(
                self, "Undo abgeschlossen",
                f"<b>Total:</b> {result['total']} Aktionen<br><br>"
                f"✓ Wiederhergestellt: {result['ok']}<br>"
                f"✗ Fehlgeschlagen: {result['fail']}<br>"
                f"– Übersprungen: {result['skip']}<br><br>"
                f"Hinweis: 'trash' und 'delete' können nicht "
                f"rückgängig gemacht werden.",
                kind=MessageDialog.INFO, buttons="ok",
                log_path=LOG_DIR,
            ).exec()
        self._reset_ui_after_finish()

    def _append_log(self, msg: str):
        self.log_text.append(msg)
        sb = self.log_text.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_finished(self, stats: dict):
        self.start_btn.setEnabled(True)
        self.undo_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)

        if "error" in stats:
            MessageDialog.show_error(self, "Verarbeitung fehlgeschlagen",
                f"Die Verarbeitung wurde mit einem Fehler beendet:"
                f"<br><br>{stats['error']}")
        else:
            msg = (f"<b>Dauer:</b> {stats.get('duration', '?')}<br><br>"
                   f"<b>Run-Log:</b><br>{stats.get('run_log', '?')}<br><br>"
                   f"<b>Fehler-Log:</b><br>"
                   f"{stats.get('failed_log', '?')}")
            if stats.get('undo_log'):
                msg += f"<br><br><b>Undo-Log:</b><br>{stats['undo_log']}"
            diag = stats.get("diagnostics") or {}
            if diag:
                msg += "<br><br><b>Diagnose nach Lauf:</b><br>"
                for k, v in diag.items():
                    msg += f"&nbsp;&nbsp;{k}: {v}<br>"
            # Log-Ordner-Pfad für den Button ermitteln
            log_dir_for_button = None
            run_log = stats.get("run_log")
            if run_log:
                try:
                    log_dir_for_button = Path(run_log).parent
                except Exception:
                    pass
            if log_dir_for_button is None:
                log_dir_for_button = LOG_DIR
            MessageDialog(
                self, "Verarbeitung abgeschlossen", msg,
                kind=MessageDialog.INFO, buttons="ok",
                log_path=log_dir_for_button,
            ).exec_()

        # Wenn Kombi-Lauf: nach organize noch add_text im Zielordner starten
        combo = getattr(self, "_combo_pending_text", False)
        if combo and "error" not in stats:
            self._combo_pending_text = False
            dest_dir = getattr(self, "_combo_dest_dir", None)
            text_data = self._pending_text
            if dest_dir and text_data and dest_dir.is_dir():
                self.log_text.append(
                    "\n=== Schritt 2: Text im Zielordner setzen ===")
                self._start_add_text_in_dir(dest_dir, text_data)
                return

        self._reset_ui_after_finish()

    def _start_add_text_in_dir(self, target_dir: Path,
                               text_data: Dict[str, Any]):
        """Startet einen add_text-Lauf direkt im Zielordner."""
        self._prepare_worker_start()
        # Modus auf add_text umstellen
        self.mode_combo.setCurrentIndex(MODE_INDEX["add_text"])
        # Quellordner temporär auf Zielordner setzen
        old_source = self.source_line.text()
        self.source_line.setText(str(target_dir))

        self.worker = OrganizerWorker(
            target_dir, target_dir,
            self.dry_run_check.isChecked(),
            False,  # use_fs_fallback
            False,  # use_filename_parser
            False,  # use_live_photo
            False,  # use_aae
            False,  # remove_lp_movs
            "skip", None, "add_text",
            is_internal_volume(target_dir),
            fix_data=None,
            text_data=text_data,
        )
        self.worker.log_message.connect(self._append_log)
        self.worker.progress_updated.connect(self._on_progress)
        self.worker.phase_updated.connect(self._on_phase)
        self.worker.eta_updated.connect(self._on_eta)
        self.worker.file_counter.connect(self._on_file_counter)

        # Wrapper, damit nach dem zweiten Lauf die Quelle zurückgesetzt wird
        def on_finished_second(stats2):
            self._on_finished(stats2)
            self.source_line.setText(old_source)

        self.worker.finished.connect(on_finished_second)
        self.worker.start()


# -----------------------------------------------------------------------------
# Programmstart
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    # Absturz-Logging installieren (vor QApplication, damit auch
    # Fehler beim Start erfasst werden)
    _install_global_exception_logger()

    # Logs aufräumen, bevor irgendetwas geschrieben wird
    try:
        cleanup_old_logs(LOG_DIR)
    except Exception as e:
        print(f"Log-Aufräumen fehlgeschlagen: {e}")
    print("[start] Aufräumen fertig, starte QApplication")
    app = QApplication(sys.argv)
    # Fusion-Style verwenden: zeichnet Fokusrahmen auch bei gestylten Buttons
    try:
        from PySide6.QtWidgets import QStyleFactory
        fusion = QStyleFactory.create("Fusion")
        if fusion is not None:
            app.setStyle(fusion)
    except Exception:
        pass

    icon_path = SCRIPT_DIR / APP_ICON_FILE
    if icon_path.exists():
        app_icon = QIcon(str(icon_path))
        if not app_icon.isNull():
            app.setWindowIcon(app_icon)

    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
