#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rename_for_plex.py — приведение имён видеофайлов к формату Plex.

Скрипт находит в имени файла маркер серии (S01E01, s01ep01, 1x01),
вытаскивает название сериала и название эпизода, выбрасывает релизный
мусор (1080p, WEB-DL, x264, RUS, ...) и переименовывает файл по схеме:

    <Название сериала> - sXXeYY - <Название эпизода>.<ext>

Пример:
    Roadhouse.Blues.S01E01.Pilot.1080p.WEB-DL.x264-GROUP.mkv
    ->  Roadhouse Blues - s01e01 - Pilot.mkv

Быстрый старт:
    python rename_for_plex.py "D:\\Serials" --dry-run
    python rename_for_plex.py "D:\\Serials" --backup

Сборка одного .exe для Windows (не требует Python на машине пользователя):
    python build_exe.py

Запуск без аргументов открывает интерактивный мастер.

Лицензия: MIT.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterator, Optional, Sequence, Set

__version__ = "2.1.0"

# ----------------------------------------------------------------------------
# Настройки
# ----------------------------------------------------------------------------

VIDEO_EXTENSIONS = frozenset(
    {
        ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm",
        ".m4v", ".ts", ".m2ts", ".mpg", ".mpeg", ".divx",
    }
)

#: Префикс папки с бэкапом; такие папки никогда не сканируются и не переименовываются.
BACKUP_PREFIX = "_backup_"

#: Минимальная длина названия эпизода, которую ещё имеет смысл писать в имя.
MIN_TITLE_LENGTH = 3

#: Ограничение длины базового имени файла (без расширения).
MAX_BASENAME_LENGTH = 150

#: Релизные теги. Всё, что начинается с одного из них, отрезается от названия.
#: Внимание: теги ищутся как отдельные слова, поэтому "Voyage" не пострадает.
RELEASE_TAGS = (
    # источники и качество
    "webrip", "web-dl", "webdl", "web-rip", "bluray", "blu-ray", "bdrip",
    "brrip", "bdremux", "remux", "dvdrip", "dvdscr", "dvd", "hdrip", "hdtv",
    "hdtc", "hdcam", "camrip", "telesync", "hdts",
    # кодеки и битность
    "x264", "x265", "h264", "h265", "hevc", "avc", "xvid", "divx", "vp9",
    "av1", "10bit", "8bit", "hi10p",
    # звук
    "aac", "aac2", "ac3", "eac3", "dts", "dtshd", "truehd", "atmos", "flac",
    "mp3", "opus", "ddp51", "ddp5", "ddp", "dd51", "dd5", "dd2", "lfddp51",
    "2ch", "6ch",
    # релиз-группы и площадки
    "nf", "amzn", "atvp", "dsnp", "hmax", "pcok", "itunes", "appletv",
    # языки / озвучки / субтитры
    "rus", "eng", "ukr", "multi", "mvo", "dvo", "avo", "pvo", "vo", "dub",
    "sub", "subs", "subbed", "dubbed",
    # прочее
    "proper", "repack", "rerip", "internal", "limited", "unrated",
    "extended", "remastered", "directors", "uncut", "hdr10", "hdr",
    "dolbyvision", "dovi", "sdr", "3d",
    # популярные русские озвучки
    "lostfilm", "hdrezka", "kravec", "newstudio", "baibako", "alexfilm",
    "jaskier", "coldfilm", "kerob",
)

#: Неразрешённые в Windows символы.
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

#: Зарезервированные в Windows имена (без расширения).
RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

# ----------------------------------------------------------------------------
# Регулярные выражения
# ----------------------------------------------------------------------------

# Маркер серии: S01E01, S01.E01, S01EP01, 1x01, "Season 1 Episode 2".
# Для "1x01" стоит защита от разрешений вида 1920x1080.
EPISODE_RE = re.compile(
    r"""
    (?:
        (?<![0-9])s(?P<s1>[0-9]{1,2})[\s._-]?e(?P<e1>[0-9]{1,3})
      | (?<![0-9])s(?P<s2>[0-9]{1,2})[\s._-]?ep(?P<e2>[0-9]{1,3})
      | (?<![0-9A-Za-z])(?P<s3>[0-9]{1,2})x(?P<e3>[0-9]{1,3})(?![0-9])
      | (?<![0-9A-Za-z])season[\s._-]*(?P<s4>[0-9]{1,2})[\s._-]*
        (?:episode|ep|e)[\s._-]*(?P<e4>[0-9]{1,3})(?![0-9])
    )
    (?![0-9])
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Год выпуска: 1900–2099, но не часть разрешения (1920x1080).
YEAR_RE = re.compile(r"(?<![0-9])(?P<year>19[0-9]{2}|20[0-9]{2})(?![0-9])(?!\s*[xX×]\s*[0-9])")

# Год вместе с обрамляющими скобками и разделителями: " (2019)", "[2019]".
_YEAR_TOKEN_RE = re.compile(
    r"(?i)[\s._\-]*[\(\[]?[\s._\-]*(?:19[0-9]{2}|20[0-9]{2})[\s._\-]*[\)\]]?"
    r"(?![0-9])(?!\s*[xX×]\s*[0-9])"
)

# Пустые скобки, оставшиеся после удаления года: "( )".
_EMPTY_BRACKETS_RE = re.compile(r"\(\s*\)|\[\s*\]|\{\s*\}")

# Склейка CamelCase: "RoadhouseBlues" -> "Roadhouse Blues".
_CAMEL_LOWER_UPPER = re.compile(r"(?<=[a-zа-яё0-9])(?=[A-ZА-ЯЁ])")
_CAMEL_ACRONYM = re.compile(r"(?<=[A-ZА-ЯЁ])(?=[A-ZА-ЯЁ][a-zа-яё])")

# Аббревиатуры, разбитые точками: "S H I E L D" -> "SHIELD".
_SINGLE_LETTER_RUN_RE = re.compile(r"\b(?:[A-ZА-ЯЁ][\s.]+){2,}[A-ZА-ЯЁ]\b")

# Продолжение мультисерии: "S01E01E02" -> остаток "E02".
_EXTRA_EPISODE_RE = re.compile(r"(?i)^[\s._\-]*e[0-9]{1,3}(?![0-9])")

_WS_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+")

# Отрезание релизного мусора: теги + разрешения (1080p, 4K) как отдельные слова.
_TAG_ALTERNATION = "|".join(
    re.escape(tag) for tag in sorted(RELEASE_TAGS, key=len, reverse=True)
)
RELEASE_CUT_RE = re.compile(
    r"(?i)[\s._\-\[\](){}]*(?:\b(?:" + _TAG_ALTERNATION + r")\b"
    r"|\b[0-9]{3,4}[pi]\b"
    r"|\b[48]k\b"
    r"|\b[0-9]{3,4}[pi][\s._-]*[0-9]{2,4}\b)"
)

_BRACKET_GROUPS_RE = re.compile(r"\[[^\]]*\]|\{[^}]*\}")
_PAREN_GROUPS_RE = re.compile(r"\(([^)]*)\)")
_LEADING_SEPARATORS_RE = re.compile(r"^[\s._\-:|]+")
_TRAILING_SEPARATORS_RE = re.compile(r"[\s._\-]+$")


# ----------------------------------------------------------------------------
# Вспомогательные функции
# ----------------------------------------------------------------------------

def normalize_spaces(text: str) -> str:
    """Схлопывает повторяющиеся пробелы и убирает пробелы по краям."""
    return _WS_RE.sub(" ", text).strip()


def split_camel(text: str) -> str:
    """Разбивает слипшиеся слова: ``RoadhouseBlues`` -> ``Roadhouse Blues``."""
    if not text:
        return ""
    text = _CAMEL_LOWER_UPPER.sub(" ", text)
    text = _CAMEL_ACRONYM.sub(" ", text)
    text = _SINGLE_LETTER_RUN_RE.sub(lambda m: re.sub(r"[\s.]+", "", m.group(0)), text)
    return normalize_spaces(text)


def _drop_bracket_groups(text: str) -> str:
    """Удаляет [..] и {..}, а также (..), если внутри нет года."""
    text = _BRACKET_GROUPS_RE.sub(" ", text)

    def _replace(match: "re.Match[str]") -> str:
        return match.group(0) if YEAR_RE.search(match.group(1)) else " "

    return _PAREN_GROUPS_RE.sub(_replace, text)


def extract_year(text: str) -> str:
    """Возвращает год выпуска из строки или пустую строку."""
    match = YEAR_RE.search(text)
    return match.group("year") if match else ""


def cut_release_noise(text: str) -> str:
    """Обрезает строку на первом релизном теге или разрешении."""
    match = RELEASE_CUT_RE.search(text)
    return text[: match.start()] if match else text


def _is_video_file(filename: str) -> bool:
    """Проверяет, что файл скрытый/видео и подлежит обработке."""
    if filename.startswith("."):
        return False
    return os.path.splitext(filename)[1].lower() in VIDEO_EXTENSIONS


def sanitize_basename(name: str) -> str:
    """Убирает недопустимые для файловой системы символы и хвостовые точки."""
    name = INVALID_FILENAME_CHARS.sub("", name)
    name = normalize_spaces(name)
    name = _TRAILING_SEPARATORS_RE.sub("", name)
    name = _LEADING_SEPARATORS_RE.sub("", name)

    if len(name) > MAX_BASENAME_LENGTH:
        name = name[:MAX_BASENAME_LENGTH]
        name = _TRAILING_SEPARATORS_RE.sub("", name)
        name = name.strip(" -_")

    if not name:
        return ""

    # Windows не позволяет создавать файлы с именами CON, PRN, NUL, COM1 ...
    if name.split(".")[0].upper() in RESERVED_NAMES:
        name = "_" + name

    return name


# ----------------------------------------------------------------------------
# Разбор имён
# ----------------------------------------------------------------------------

def clean_series_name(text: str) -> str:
    """Очищает название сериала от служебных символов, года и релизного мусора."""
    if not text:
        return ""

    name = text.replace("_", " ").replace(".", " ")
    name = _drop_bracket_groups(name)

    year = extract_year(name)
    if year:
        name = _YEAR_TOKEN_RE.sub(" ", name, count=1)
        name = _EMPTY_BRACKETS_RE.sub(" ", name)

    name = cut_release_noise(name)
    name = split_camel(name)
    name = INVALID_FILENAME_CHARS.sub("", name)
    name = normalize_spaces(name).strip(" -_")

    if not name:
        return ""
    return f"{name} ({year})" if year else name


def _series_patterns(series_name: str) -> list[list[str]]:
    """Возвращает варианты слов названия сериала (с годом и без) для дедупликации."""
    words = _WORD_RE.findall(series_name)
    if not words:
        return []

    patterns = [words]
    without_year = [w for w in words if not (len(w) == 4 and w.isdigit())]
    if without_year and without_year != words:
        patterns.append(without_year)
    return patterns


def strip_leading_series(text: str, series_name: str) -> str:
    """Убирает из начала названия эпизода повтор названия сериала.

    Дубликат ищется по словам, поэтому не возникает сдвига на длину пробелов
    (прежняя версия резала строку по длине названия без пробелов).
    """
    if not text or not series_name:
        return text

    for words in _series_patterns(series_name):
        # Одно короткое слово ("The", "IT") слишком легко совпадает со словом
        # из названия эпизода — такие дубликаты не трогаем.
        if len(words) == 1 and len(words[0]) < 4:
            continue

        separator = r"[\s._\-:()\[\]]*"
        pattern = re.compile(
            r"^\W*" + separator.join(re.escape(word) for word in words)
            + r"(?=$|[\s._\-:()\[\]])" + separator,
            re.IGNORECASE,
        )
        stripped = pattern.sub("", text, count=1)
        if stripped != text:
            return stripped
    return text


def clean_episode_title(text: str, series_name: str = "") -> str:
    """Превращает остаток имени файла в аккуратное название эпизода."""
    if not text:
        return ""

    text = text.replace("_", " ").replace(".", " ")
    text = _drop_bracket_groups(text)
    text = cut_release_noise(text)
    text = _LEADING_SEPARATORS_RE.sub("", text)

    if series_name:
        text = strip_leading_series(text, series_name)

    text = split_camel(text)
    text = _LEADING_SEPARATORS_RE.sub("", text)
    text = _TRAILING_SEPARATORS_RE.sub("", text)
    text = normalize_spaces(text).strip(" -_.:|")

    if len(text) < MIN_TITLE_LENGTH or text.isdigit():
        return ""
    return text


def extract_clean_name(filename: str, series_fallback: str = "") -> Optional[str]:
    """Возвращает новое базовое имя файла (без расширения) или ``None``.

    ``series_fallback`` используется, когда в имени нет названия сериала
    (например, файл ``s01e01.mkv`` внутри папки ``Show Name``) — тогда
    берётся имя родительской папки.
    """
    stem = os.path.splitext(filename)[0]
    match = EPISODE_RE.search(stem)
    if not match:
        return None

    season = int(match.group("s1") or match.group("s2") or match.group("s3") or match.group("s4"))
    episode = int(match.group("e1") or match.group("e2") or match.group("e3") or match.group("e4"))

    prefix = stem[: match.start()]
    suffix = stem[match.end():]

    # "S01E01E02" / "S01E01-E02": вторую серию из названия убираем,
    # в имя файла попадает только первая.
    while True:
        stripped = _EXTRA_EPISODE_RE.sub("", suffix, count=1)
        if stripped == suffix:
            break
        suffix = stripped

    series_name = clean_series_name(prefix) or clean_series_name(series_fallback)
    if not series_name:
        return None

    title = clean_episode_title(suffix, series_name)
    base_name = f"{series_name} - s{season:02d}e{episode:02d}"
    if title:
        base_name = f"{base_name} - {title}"

    return sanitize_basename(base_name) or None


# ----------------------------------------------------------------------------
# Файловые операции
# ----------------------------------------------------------------------------

def _is_same_path(first: str, second: Optional[str]) -> bool:
    """Сравнивает пути без учёта регистра (Windows)."""
    if not second:
        return False
    return os.path.normcase(os.path.abspath(first)) == os.path.normcase(
        os.path.abspath(second)
    )


def unique_target_path(
    root: str,
    base_name: str,
    ext: str,
    source_path: Optional[str] = None,
    used: Optional[Set[str]] = None,
) -> tuple[str, str]:
    """Подбирает свободное имя, добавляя суффикс ``(1)``, ``(2)`` и т. д.

    Переименование только за счёт регистра не считается конфликтом, иначе
    на Windows файл ``show - s01e01.mkv`` превращался бы в
    ``Show - s01e01 (1).mkv``.
    """
    used = used if used is not None else set()

    index = 0
    while True:
        candidate_name = base_name + ext if index == 0 else f"{base_name} ({index}){ext}"
        candidate_path = os.path.join(root, candidate_name)
        key = os.path.normcase(os.path.abspath(candidate_path))

        conflict = key in used
        if not conflict and os.path.exists(candidate_path):
            conflict = not _is_same_path(candidate_path, source_path)

        if not conflict:
            used.add(key)
            return candidate_path, candidate_name

        index += 1
        if index > 9999:  # страховка от бесконечного цикла
            raise RuntimeError(f"Не удалось подобрать имя для '{base_name}{ext}'")


def iter_video_files(directory: str, recursive: bool = True) -> Iterator[str]:
    """Перебирает видеофайлы, пропуская скрытые и папки бэкапа."""
    if not recursive:
        with os.scandir(directory) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                if entry.is_file() and _is_video_file(entry.name):
                    yield entry.path
        return

    for root, dirs, files in os.walk(directory):
        # dirs меняем на месте, иначе os.walk зайдёт в папку бэкапа и
        # переименует файлы уже внутри бэкапа.
        dirs[:] = sorted(
            name for name in dirs
            if not name.startswith(BACKUP_PREFIX) and not name.startswith(".")
        )
        for name in sorted(files):
            if _is_video_file(name):
                yield os.path.join(root, name)


# ----------------------------------------------------------------------------
# Основная логика
# ----------------------------------------------------------------------------

@dataclass
class Stats:
    """Статистика одного прогона."""

    total: int = 0
    renamed: int = 0
    unchanged: int = 0
    unmatched: int = 0
    errors: int = 0


def rename_files(
    directory_path: str,
    dry_run: bool = False,
    backup: bool = False,
    recursive: bool = True,
    infer_series: bool = True,
    out: Callable[[str], None] = print,
) -> Stats:
    """Переименовывает видеофайлы в папке ``directory_path``.

    :param directory_path: папка с видеофайлами.
    :param dry_run: только показать план, ничего не менять.
    :param backup: скопировать оригиналы в ``_backup_<дата>`` перед переименованием.
    :param recursive: обходить вложенные папки.
    :param infer_series: брать название сериала из имени папки, если его нет в файле.
    :param out: функция вывода (удобно подменять в тестах).
    :return: :class:`Stats` со счётчиками.
    """
    stats = Stats()
    directory_path = os.path.abspath(directory_path)

    out(f"📁 Папка: {directory_path}")
    if dry_run:
        out("🔍 РЕЖИМ ПРОСМОТРА (файлы не изменяются)")
    if backup and not dry_run:
        out("💾 Будет создан бэкап")
    out("=" * 70)

    # Список файлов собираем до создания бэкапа, чтобы бэкап не попал в обход.
    files = list(iter_video_files(directory_path, recursive=recursive))

    backup_dir: Optional[str] = None
    if backup and not dry_run and files:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = os.path.join(directory_path, f"{BACKUP_PREFIX}{stamp}")
        os.makedirs(backup_dir, exist_ok=True)
        out(f"💾 Бэкап: {backup_dir}\n")

    used_targets: Set[str] = set()

    for filepath in files:
        stats.total += 1
        filename = os.path.basename(filepath)
        ext = os.path.splitext(filename)[1]
        old_base = os.path.splitext(filename)[0]

        parent_name = os.path.basename(os.path.dirname(filepath))
        fallback = parent_name if infer_series else ""

        try:
            new_base = extract_clean_name(filename, fallback)
        except Exception as error:  # защита от неожиданных имён
            out(f"   ❌ {filename}: ошибка разбора ({error})")
            stats.errors += 1
            continue

        if not new_base:
            out(f"   ⏭️  {filename} -> серия не определена")
            stats.unmatched += 1
            continue

        if new_base == old_base:
            out(f"   ⏭️  {filename} -> без изменений")
            stats.unchanged += 1
            continue

        target_path, target_name = unique_target_path(
            os.path.dirname(filepath), new_base, ext, filepath, used_targets
        )

        out(f"   📄 {filename}")
        out(f"      -> {target_name}")

        if dry_run:
            stats.renamed += 1
            continue

        try:
            if backup_dir:
                relative = os.path.relpath(filepath, directory_path)
                backup_path = os.path.join(backup_dir, relative)
                os.makedirs(os.path.dirname(backup_path), exist_ok=True)
                shutil.copy2(filepath, backup_path)

            os.rename(filepath, target_path)
            stats.renamed += 1
            out("      ✅ Готово")
        except OSError as error:
            out(f"      ❌ Ошибка: {error}")
            stats.errors += 1

    out("")
    out("=" * 70)
    out("📊 СТАТИСТИКА:")
    out(f"   📂 Всего видеофайлов: {stats.total}")
    out(f"   ✅ Переименовано: {stats.renamed}")
    out(f"   ⏭️  Без изменений: {stats.unchanged}")
    out(f"   ❓ Не распознано: {stats.unmatched}")
    if stats.errors:
        out(f"   ❌ Ошибок: {stats.errors}")

    if dry_run:
        out("\n🔍 Пробный запуск: ни один файл не изменён.")
    elif backup_dir:
        out(f"\n💾 Оригиналы сохранены в: {backup_dir}")

    return stats


# ----------------------------------------------------------------------------
# Консольный интерфейс
# ----------------------------------------------------------------------------

def _configure_stdio() -> None:
    """Включает UTF-8 в консоли Windows, чтобы эмодзи не роняли скрипт."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass


def _is_frozen() -> bool:
    """True, если скрипт запущен как собранный .exe (PyInstaller)."""
    return bool(getattr(sys, "frozen", False))


def app_name() -> str:
    """Имя программы для ``--help``/, ``--version`` и заголовка окна."""
    if _is_frozen():
        return os.path.basename(sys.executable)
    return "rename_for_plex.py"


def _set_console_title(title: str) -> None:
    """Ставит заголовок окна консоли (только Windows)."""
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleTitleW(title)
    except Exception:
        pass


def _ask_yes_no(question: str, default: bool = False) -> bool:
    """Спрашивает да/нет; пустой ввод даёт значение по умолчанию."""
    hint = "Y/n" if default else "y/N"
    try:
        answer = input(f"  {question} ({hint}): ").strip().lower()
    except EOFError:
        return default
    if not answer:
        return default
    return answer in {"y", "yes", "д", "да"}


def _prompt_path() -> str:
    """Запрашивает папку у пользователя."""
    while True:
        try:
            raw = input("\n📂 Введите путь к папке: ").strip().strip('"').strip("'")
        except EOFError:
            print("\n❌ Ввод недоступен: укажите папку аргументом командной строки")
            return ""
        if not raw:
            print("❌ Путь не может быть пустым")
            continue
        if not os.path.isdir(raw):
            print(f"❌ Папка '{raw}' не найдена")
            continue
        return raw


def build_parser() -> argparse.ArgumentParser:
    """Собирает парсер аргументов командной строки."""
    name = app_name()
    call = name if _is_frozen() else f"python {name}"
    parser = argparse.ArgumentParser(
        prog=name,
        description="Переименование видеофайлов в формат Plex: 'Сериал - s01e01 - Эпизод.mkv'.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Примеры:\n"
            f'  {call} "D:\\Serials"\n'
            f'  {call} "D:\\Serials" --dry-run\n'
            f'  {call} "D:\\Serials" --backup --yes\n'
        ),
    )
    parser.add_argument("path", nargs="?", help="папка с видеофайлами")
    parser.add_argument(
        "-n", "--dry-run", action="store_true",
        help="только показать, что будет сделано",
    )
    parser.add_argument(
        "-b", "--backup", action="store_true",
        help="скопировать оригиналы в папку _backup_<дата>",
    )
    parser.add_argument(
        "-y", "--yes", action="store_true",
        help="не спрашивать подтверждение",
    )
    parser.add_argument(
        "--no-recursive", action="store_true",
        help="не заходить во вложенные папки",
    )
    parser.add_argument(
        "--no-infer-series", action="store_true",
        help="не подставлять имя папки как название сериала",
    )
    parser.add_argument(
        "--no-pause", action="store_true",
        help="не ждать Enter в конце (для запуска из скриптов)",
    )
    parser.add_argument(
        "-V", "--version", action="version", version=f"%(prog)s {__version__}",
    )
    return parser


def _run(args: argparse.Namespace) -> int:
    """Основной сценарий после разбора аргументов."""
    interactive = args.path is None

    print("🔄 ПЕРЕИМЕНОВАНИЕ ФАЙЛОВ ДЛЯ PLEX")
    print("=" * 70)
    print("  • ищет s01e01 / s01ep01 / 1x01 в любом мусоре")
    print("  • режет релизные теги: 1080p, WEB-DL, x264, RUS ...")
    print("  • убирает дублирование названия сериала и CamelCase")
    print("=" * 70)

    directory = args.path if args.path else _prompt_path()
    if not directory:
        return 2
    if not os.path.isdir(directory):
        if os.path.isfile(directory):
            print(f"❌ '{directory}' — это файл, а не папка")
            print("   Перетащите на значок программы папку с сериалом.")
        else:
            print(f"❌ Папка '{directory}' не найдена")
        return 2

    dry_run = args.dry_run
    backup = args.backup

    if interactive:
        dry_run = _ask_yes_no(
            "Режим просмотра (только показать, что будет сделано)?", default=False
        )
        if not dry_run:
            backup = _ask_yes_no(
                "Создать бэкап файлов перед переименованием?", default=True
            )
    elif dry_run and backup:
        print("ℹ️  В режиме просмотра бэкап не создаётся.")
        backup = False

    print(f"\n📂 Папка: {os.path.abspath(directory)}")
    print(f"   Режим просмотра: {'ДА' if dry_run else 'НЕТ'}")
    print(f"   Бэкап файлов: {'ДА' if backup else 'НЕТ'}")

    if not args.yes and not _ask_yes_no("Продолжить?", default=True):
        print("❌ Операция отменена")
        return 0

    try:
        stats = rename_files(
            directory,
            dry_run=dry_run,
            backup=backup,
            recursive=not args.no_recursive,
            infer_series=not args.no_infer_series,
        )
    except KeyboardInterrupt:
        print("\n\n❌ Операция прервана пользователем")
        return 130
    except Exception as error:  # noqa: BLE001 — верхнеуровневая страховка
        print(f"\n❌ Критическая ошибка: {error}")
        import traceback

        traceback.print_exc()
        return 1

    print("\n✅ Готово!")
    return 1 if stats.errors else 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Точка входа. Возвращает код возврата (0 — успех)."""
    _configure_stdio()
    _set_console_title(f"Rename for Plex {__version__}")
    args = build_parser().parse_args(argv)

    stdin_tty = False
    try:
        stdin_tty = sys.stdin.isatty()
    except (AttributeError, ValueError):
        pass

    # В .exe пауза нужна всегда, иначе окно закроется до того, как прочитают
    # отчёт. Из скриптов её отключают флагом --no-pause.
    pause = (args.path is None or _is_frozen()) and stdin_tty and not args.no_pause

    try:
        return _run(args)
    finally:
        if pause:
            try:
                input("\nНажмите Enter для выхода...")
            except EOFError:
                pass


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        # Ctrl+C во время ввода пути или подтверждения.
        print("\n\n❌ Операция прервана пользователем")
        sys.exit(130)
