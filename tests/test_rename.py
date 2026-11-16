# -*- coding: utf-8 -*-
"""Модульные тесты для rename_for_plex.py.

Запуск из корня проекта:

    python -m unittest discover -s tests -v
"""

import io
import os
import shutil
import sys
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Временные папки создаём рядом с проектом: системный temp в некоторых
# окружениях (песочницы, ограниченные ACL) недоступен для записи.
_TMP_ROOT = Path(__file__).resolve().parents[1] / ".tmp-tests"
_TMP_ROOT.mkdir(parents=True, exist_ok=True)


class TempDir:
    """Простая замена ``tempfile.TemporaryDirectory``.

    Папка создаётся обычным ``os.makedirs``: ``tempfile.mkdtemp`` на Windows
    использует режим 0o700, и Python 3.14+ выставляет под такой каталог ACL,
    из-за которого запись в него может оказаться запрещена.
    """

    def __enter__(self) -> str:
        self.path = os.path.join(str(_TMP_ROOT), "case_" + uuid.uuid4().hex)
        os.makedirs(self.path)
        return self.path

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        shutil.rmtree(self.path, ignore_errors=True)
        return False


import rename_for_plex as rp  # noqa: E402


def tearDownModule():
    """Убирает временные папки после прогона тестов."""
    shutil.rmtree(_TMP_ROOT, ignore_errors=True)


class TestExtractCleanName(unittest.TestCase):
    """Разбор имён файлов."""

    def test_standard_scene_name(self):
        self.assertEqual(
            rp.extract_clean_name(
                "Roadhouse.Blues.S01E01.Pilot.1080p.WEB-DL.x264-GROUP.mkv"
            ),
            "Roadhouse Blues - s01e01 - Pilot",
        )

    def test_lowercase_marker(self):
        self.assertEqual(
            rp.extract_clean_name("show.name.s01e02.some.title.mkv"),
            "show name - s01e02 - some title",
        )

    def test_resolution_is_not_episode(self):
        self.assertIsNone(rp.extract_clean_name("Movie.2019.1920x1080.mkv"))
        self.assertIsNone(rp.extract_clean_name("Movie.1280x720.mp4"))

    def test_1x01_format(self):
        self.assertEqual(
            rp.extract_clean_name("Show.1x03.Title.mkv"),
            "Show - s01e03 - Title",
        )

    def test_s01ep01_format(self):
        self.assertEqual(
            rp.extract_clean_name("Show.s01ep02.Some.Title.mkv"),
            "Show - s01e02 - Some Title",
        )

    def test_season_episode_words(self):
        self.assertEqual(
            rp.extract_clean_name("Show Season 2 Episode 5 Title.mkv"),
            "Show - s02e05 - Title",
        )

    def test_multi_episode_takes_first(self):
        self.assertEqual(
            rp.extract_clean_name("Show.S01E01E02.Title.mkv"),
            "Show - s01e01 - Title",
        )

    def test_no_episode_returns_none(self):
        self.assertIsNone(rp.extract_clean_name("Holiday.2020.mp4"))

    def test_year_kept_for_plex(self):
        self.assertEqual(
            rp.extract_clean_name("Show.Name.2021.S02E05.The.Voyage.Home.1080p.mkv"),
            "Show Name (2021) - s02e05 - The Voyage Home",
        )

    def test_year_in_parentheses(self):
        self.assertEqual(
            rp.extract_clean_name("Show (2019) S01E04 Episode Title.mkv"),
            "Show (2019) - s01e04 - Episode Title",
        )

    def test_garbage_before_marker_removed(self):
        self.assertEqual(
            rp.extract_clean_name("Show.1080p.WEB-DL.S01E01.Pilot.mkv"),
            "Show - s01e01 - Pilot",
        )

    def test_title_is_not_cut_at_short_tag_inside_word(self):
        # Прежняя версия резала "Voyage" по тегу "vo" и теряла название серии.
        self.assertEqual(
            rp.extract_clean_name("Show.S01E01.Voyage.mkv"),
            "Show - s01e01 - Voyage",
        )
        self.assertEqual(
            rp.extract_clean_name("Show.S01E01.Voyage.Home.1080p.mkv"),
            "Show - s01e01 - Voyage Home",
        )

    def test_duplicated_series_removed(self):
        self.assertEqual(
            rp.extract_clean_name("Dexter.S01E01.Dexter.Pilot.720p.mkv"),
            "Dexter - s01e01 - Pilot",
        )

    def test_duplicated_series_with_year_removed(self):
        self.assertEqual(
            rp.extract_clean_name("Show (2019) S01E05 Show (2019) Pilot.mkv"),
            "Show (2019) - s01e05 - Pilot",
        )

    def test_short_series_name_not_deduped(self):
        # Одно короткое слово ("The") не должно вырезаться из названия серии.
        self.assertEqual(
            rp.extract_clean_name("The.S01E01.The.Big.Bang.mkv"),
            "The - s01e01 - The Big Bang",
        )

    def test_fallback_series_from_folder(self):
        self.assertEqual(
            rp.extract_clean_name("s01e01.mkv", "Show Name"),
            "Show Name - s01e01",
        )
        self.assertEqual(
            rp.extract_clean_name("Show.S01E01.mkv", "Ignored Folder"),
            "Show - s01e01",
        )

    def test_fallback_needed_but_missing(self):
        self.assertIsNone(rp.extract_clean_name("s01e01.mkv"))

    def test_invalid_characters_removed(self):
        self.assertEqual(
            rp.extract_clean_name("Show.S01E01.What: Now?.mkv"),
            "Show - s01e01 - What Now",
        )

    def test_language_tag_removed(self):
        self.assertEqual(
            rp.extract_clean_name("Show.S01E01.[RUS].Title.720p.mkv"),
            "Show - s01e01 - Title",
        )

    def test_digit_only_title_dropped(self):
        self.assertEqual(
            rp.extract_clean_name("Show.S01E01.1080p.mkv"),
            "Show - s01e01",
        )

    def test_short_title_dropped(self):
        self.assertEqual(
            rp.extract_clean_name("Show.S01E01.OK.mkv"),
            "Show - s01e01",
        )

    def test_acronym_joined(self):
        self.assertEqual(
            rp.extract_clean_name("Marvels.Agents.of.S.H.I.E.L.D.S01E01.Pilot.mkv"),
            "Marvels Agents of SHIELD - s01e01 - Pilot",
        )

    def test_cyrillic(self):
        self.assertEqual(
            rp.extract_clean_name("Сериал.Название.S02E10.Серия.Одна.mkv"),
            "Сериал Название - s02e10 - Серия Одна",
        )

    def test_already_clean_name_unchanged(self):
        self.assertEqual(
            rp.extract_clean_name("Show Name - s01e01 - Pilot.mkv"),
            "Show Name - s01e01 - Pilot",
        )

    def test_long_title_truncated(self):
        name = "Show.S01E01." + ("VeryLongWord" * 30) + ".mkv"
        result = rp.extract_clean_name(name)
        self.assertIsNotNone(result)
        self.assertLessEqual(len(result), rp.MAX_BASENAME_LENGTH)

    def test_reserved_windows_name(self):
        self.assertEqual(rp.sanitize_basename("CON"), "_CON")
        # Зарезервированные имена в Windows не зависят от регистра.
        self.assertEqual(rp.sanitize_basename("com1"), "_com1")


class TestUniqueTargetPath(unittest.TestCase):
    """Подбор свободного имени."""

    def test_no_conflict(self):
        with TempDir() as tmp:
            path, name = rp.unique_target_path(tmp, "Show - s01e01", ".mkv")
            self.assertEqual(name, "Show - s01e01.mkv")
            self.assertEqual(path, os.path.join(tmp, "Show - s01e01.mkv"))

    def test_conflict_gets_suffix(self):
        with TempDir() as tmp:
            Path(tmp, "Show - s01e01.mkv").write_bytes(b"x")
            _, name = rp.unique_target_path(tmp, "Show - s01e01", ".mkv")
            self.assertEqual(name, "Show - s01e01 (1).mkv")

    def test_conflicts_use_counter(self):
        with TempDir() as tmp:
            Path(tmp, "Show - s01e01.mkv").write_bytes(b"x")
            Path(tmp, "Show - s01e01 (1).mkv").write_bytes(b"x")
            _, name = rp.unique_target_path(tmp, "Show - s01e01", ".mkv")
            self.assertEqual(name, "Show - s01e01 (2).mkv")

    def test_case_only_rename_is_not_a_conflict(self):
        with TempDir() as tmp:
            source = os.path.join(tmp, "show - s01e01.mkv")
            Path(source).write_bytes(b"x")
            path, _ = rp.unique_target_path(
                tmp, "SHOW - s01e01", ".mkv", source_path=source
            )
            self.assertEqual(os.path.basename(path), "SHOW - s01e01.mkv")

    def test_used_set_prevents_duplicates(self):
        with TempDir() as tmp:
            used = set()
            first, _ = rp.unique_target_path(tmp, "A - s01e01", ".mkv", used=used)
            second, name = rp.unique_target_path(tmp, "A - s01e01", ".mkv", used=used)
            self.assertNotEqual(first, second)
            self.assertEqual(name, "A - s01e01 (1).mkv")


class TestAppIdentity(unittest.TestCase):
    """Имя программы и режим собранного .exe."""

    def test_app_name_for_script(self):
        self.assertEqual(rp.app_name(), "rename_for_plex.py")
        self.assertFalse(rp._is_frozen())

    def test_app_name_for_frozen_exe(self):
        exe = os.path.join("C:" + os.sep, "tools", "rename_for_plex.exe")
        with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(
            sys, "executable", exe
        ):
            self.assertTrue(rp._is_frozen())
            self.assertEqual(rp.app_name(), "rename_for_plex.exe")

    def test_parser_uses_app_name(self):
        self.assertEqual(rp.build_parser().prog, rp.app_name())


class TestRenameFiles(unittest.TestCase):
    """Сквозные проверки переименования."""

    def _make_tree(self, tmp):
        subdir = os.path.join(tmp, "Show Name")
        os.makedirs(subdir)
        Path(subdir, "Show.Name.S01E01.Pilot.1080p.WEB-DL.mkv").write_bytes(b"a")
        Path(subdir, "Show.Name.S01E02.Voyage.720p.mp4").write_bytes(b"b")
        Path(tmp, "Ignored.2020.mkv").write_bytes(b"c")
        return subdir

    def test_dry_run_does_not_touch_files(self):
        with TempDir() as tmp:
            subdir = self._make_tree(tmp)
            before = sorted(os.listdir(subdir))
            with redirect_stdout(io.StringIO()):
                stats = rp.rename_files(tmp, dry_run=True)
            self.assertEqual(stats.renamed, 2)
            self.assertEqual(stats.unmatched, 1)
            self.assertEqual(sorted(os.listdir(subdir)), before)
            self.assertEqual(
                [d for d in os.listdir(tmp) if d.startswith(rp.BACKUP_PREFIX)], []
            )

    def test_rename_with_backup(self):
        with TempDir() as tmp:
            subdir = self._make_tree(tmp)
            with redirect_stdout(io.StringIO()):
                stats = rp.rename_files(tmp, backup=True)
            self.assertEqual(stats.errors, 0)
            self.assertEqual(stats.renamed, 2)
            self.assertEqual(
                sorted(os.listdir(subdir)),
                [
                    "Show Name - s01e01 - Pilot.mkv",
                    "Show Name - s01e02 - Voyage.mp4",
                ],
            )

            backups = [d for d in os.listdir(tmp) if d.startswith(rp.BACKUP_PREFIX)]
            self.assertEqual(len(backups), 1)
            backup_files = sorted(os.listdir(os.path.join(tmp, backups[0], "Show Name")))
            self.assertEqual(
                backup_files,
                [
                    "Show.Name.S01E01.Pilot.1080p.WEB-DL.mkv",
                    "Show.Name.S01E02.Voyage.720p.mp4",
                ],
            )

    def test_already_clean_files_are_counted_as_unchanged(self):
        with TempDir() as tmp:
            Path(tmp, "Show - s01e01 - Pilot.mkv").write_bytes(b"a")
            with redirect_stdout(io.StringIO()):
                stats = rp.rename_files(tmp)
            self.assertEqual(stats.unchanged, 1)
            self.assertEqual(stats.renamed, 0)

    def test_duplicate_targets_get_number(self):
        with TempDir() as tmp:
            Path(tmp, "Show.S01E01.Title.mkv").write_bytes(b"a")
            Path(tmp, "Show - S01E01 - Title.mkv").write_bytes(b"b")
            with redirect_stdout(io.StringIO()):
                stats = rp.rename_files(tmp)
            self.assertEqual(stats.renamed, 2)
            self.assertEqual(
                sorted(os.listdir(tmp)),
                ["Show - s01e01 - Title (1).mkv", "Show - s01e01 - Title.mkv"],
            )

    def test_backup_folder_is_not_scanned(self):
        with TempDir() as tmp:
            backup = os.path.join(tmp, rp.BACKUP_PREFIX + "20200101_000000")
            os.makedirs(backup)
            Path(backup, "Show.S01E01.Pilot.mkv").write_bytes(b"a")
            files = list(rp.iter_video_files(tmp))
            self.assertEqual(files, [])

    def test_non_recursive_mode(self):
        with TempDir() as tmp:
            subdir = os.path.join(tmp, "Nested")
            os.makedirs(subdir)
            Path(subdir, "Show.S01E01.Pilot.mkv").write_bytes(b"a")
            Path(tmp, "Show.S01E02.Voyage.mkv").write_bytes(b"b")
            files = list(rp.iter_video_files(tmp, recursive=False))
            self.assertEqual(len(files), 1)
            self.assertEqual(os.path.dirname(files[0]), tmp)


if __name__ == "__main__":
    unittest.main(verbosity=2)
