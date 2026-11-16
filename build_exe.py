#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сборка одного .exe для Windows с помощью PyInstaller.

Запуск:

    python build_exe.py            # dist/rename_for_plex.exe (один файл)
    python build_exe.py --onedir   # dist/rename_for_plex/ (папка, стартует быстрее)

Требуется пакет PyInstaller (см. requirements-dev.txt). Сам rename_for_plex.py
работает и без него — PyInstaller нужен только для сборки.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENTRY = os.path.join(HERE, "rename_for_plex.py")
ICON = os.path.join(HERE, "assets", "icon.ico")
DIST = os.path.join(HERE, "dist")
BUILD = os.path.join(HERE, "build")

DEFAULT_NAME = "rename_for_plex"


def _configure_stdio() -> None:
    """UTF-8 в консоли, чтобы эмодзи и кириллица не роняли сборку."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

VERSION_TEMPLATE = """# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={filevers},
    prodvers={prodvers},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        u'040904B0',
        [StringStruct(u'CompanyName', u'rename-files-for-plex'),
         StringStruct(u'FileDescription', u'{description}'),
         StringStruct(u'FileVersion', u'{version}'),
         StringStruct(u'InternalName', u'{name}'),
         StringStruct(u'LegalCopyright', u'MIT License'),
         StringStruct(u'OriginalFilename', u'{name}.exe'),
         StringStruct(u'ProductName', u'Rename for Plex'),
         StringStruct(u'ProductVersion', u'{version}')])
    ]),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
"""


def read_version() -> str:
    """Читает ``__version__`` из rename_for_plex.py, не импортируя модуль."""
    with open(ENTRY, encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("__version__"):
                return line.split("=", 1)[1].strip().strip("\"'")
    raise RuntimeError(f"В {ENTRY} не найден __version__")


def version_tuple(version: str) -> tuple:
    """'2.1.0' -> (2, 1, 0, 0) для ресурса версии Windows."""
    numbers = []
    for part in version.split("."):
        digits = "".join(char for char in part if char.isdigit())
        numbers.append(int(digits) if digits else 0)
    while len(numbers) < 4:
        numbers.append(0)
    return tuple(numbers[:4])


def write_version_file(path: str, version: str, name: str) -> None:
    """Готовит version_info.txt, чтобы у .exe были свойства файла."""
    numbers = version_tuple(version)
    content = VERSION_TEMPLATE.format(
        filevers=numbers,
        prodvers=numbers,
        version=version,
        name=name,
        description="Rename TV episode files to the Plex naming scheme",
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def ensure_pyinstaller() -> bool:
    """Проверяет наличие PyInstaller и подсказывает, как поставить."""
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("❌ PyInstaller не установлен.")
        print("   Установите: python -m pip install -r requirements-dev.txt")
        print("   Или запустите build_exe.bat — он поставит зависимость сам.")
        return False
    return True


def build(name: str, onefile: bool, clean: bool) -> int:
    """Запускает PyInstaller и возвращает код возврата."""
    version_file = os.path.join(BUILD, "version_info.txt")
    write_version_file(version_file, read_version(), name)

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
    ]
    if clean:
        command.append("--clean")
    command += [
        "--onefile" if onefile else "--onedir",
        "--console",
        "--name",
        name,
        "--distpath",
        DIST,
        "--workpath",
        BUILD,
        "--specpath",
        BUILD,
    ]
    # Иконка и ресурс версии применяются только Windows-сборкой.
    if os.name == "nt":
        command += ["--version-file", version_file]
        if os.path.exists(ICON):
            command += ["--icon", ICON]
    command.append(ENTRY)

    print("🔧 Запуск:", " ".join(command[2:]))
    result = subprocess.run(command, cwd=HERE)
    if result.returncode != 0:
        print("❌ PyInstaller завершился с ошибкой.")
        return result.returncode

    target = os.path.join(DIST, f"{name}.exe" if onefile else name)
    if not os.path.exists(target):
        print(f"❌ Ожидался файл «{target}», но его нет.")
        return 1

    if onefile:
        size_mb = os.path.getsize(target) / (1024 * 1024)
        print(f"\n✅ Готово: {target} ({size_mb:.1f} МБ)")
        print("   Файл можно переносить на другой компьютер без Python 3.8+.")
    else:
        print(f"\n✅ Готово: {target}{os.sep}rename_for_plex.exe")
    return 0


def main(argv=None) -> int:
    """Разбор аргументов и запуск сборки."""
    _configure_stdio()
    parser = argparse.ArgumentParser(
        prog="build_exe.py",
        description="Сборка rename_for_plex.exe через PyInstaller.",
    )
    parser.add_argument(
        "--onedir", action="store_true",
        help="собрать папку вместо одного файла (быстрее запуск, но файлов много)",
    )
    parser.add_argument(
        "--name", default=DEFAULT_NAME,
        help=f"имя .exe без расширения (по умолчанию: {DEFAULT_NAME})",
    )
    parser.add_argument(
        "--no-clean", action="store_true",
        help="не очищать кэш сборки (быстрее повторная сборка)",
    )
    args = parser.parse_args(argv)

    if not os.path.exists(ENTRY):
        print(f"❌ Не найден {ENTRY}")
        return 1
    if not ensure_pyinstaller():
        return 1
    if os.name != "nt":
        print("ℹ️  Иконка и свойства версии применяются только в Windows-сборке.")

    return build(args.name, onefile=not args.onedir, clean=not args.no_clean)


if __name__ == "__main__":
    sys.exit(main())
