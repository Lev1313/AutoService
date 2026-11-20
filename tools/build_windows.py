"""Воспроизводимая Windows-сборка. Запускать Python из .venv проекта."""

import argparse
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path


def build(output: Path, compiler: Path):
    project = Path(__file__).resolve().parents[1]
    version = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    version_file = output / "version-info.txt"
    numbers = tuple(int(value) for value in version.split(".")) + (0,)
    version_file.write_text(
        "VSVersionInfo(ffi=FixedFileInfo(filevers="
        + repr(numbers)
        + ", prodvers="
        + repr(numbers)
        + ", mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0,0)),"
        " kids=[StringFileInfo([StringTable('041904B0', ["
        f"StringStruct('FileDescription', 'AutoService'),"
        f"StringStruct('FileVersion', '{version}'), StringStruct('ProductVersion', '{version}'),"
        "StringStruct('ProductName', 'AutoService'), "
        "StringStruct('OriginalFilename', 'AutoService.exe')"
        "])]),VarFileInfo([VarStruct('Translation', [1049, 1200])])])",
        encoding="utf-8",
    )
    # Не захватывать DLL из установленного рядом Poppler/других программ.
    # Qt в этих Windows-колёсах использует системную ICU с другими именами функций.
    environment = os.environ.copy()
    environment["PATH"] = os.pathsep.join(
        [
            str(Path(os.environ["WINDIR"]) / "System32"),
            os.environ["WINDIR"],
            str(Path(sys.base_prefix)),
            str(Path(sys.prefix) / "Scripts"),
        ]
    )
    for name in ("PYTHONHOME", "PYTHONPATH", "QT_PLUGIN_PATH", "QML2_IMPORT_PATH"):
        environment.pop(name, None)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--onedir",
            "--windowed",
            "--noupx",
            "--name",
            "AutoService",
            "--icon",
            str(project / "app" / "assets" / "autoservice.ico"),
            "--version-file",
            str(version_file),
            "--distpath",
            str(output / "dist"),
            "--workpath",
            str(output / "build"),
            "--specpath",
            str(output),
            "--collect-data",
            "reportlab",
            "--add-data",
            f"{project / 'app' / 'assets'}:app/assets",
            "--exclude-module",
            "pytest",
            "--exclude-module",
            "ruff",
            str(project / "main.py"),
        ],
        cwd=project,
        env=environment,
        check=True,
    )
    bundle = output / "dist" / "AutoService"
    if list((bundle / "_internal").glob("icu*.dll")):
        raise RuntimeError("Упаковщик захватил стороннюю ICU вместо системной. Выпуск остановлен.")
    for source, destination in (
        ("OWNER-GUIDE.txt", "Инструкция.txt"),
        ("QUICK-START.txt", "Быстрый старт.txt"),
    ):
        shutil.copy2(project / "docs" / source, bundle / destination)
    (bundle / "Демонстрационный режим.cmd").write_bytes(
        b'@echo off\r\nstart "" "%~dp0AutoService.exe" --demo\r\n'
    )
    shutil.copy2(project / "docs" / "THIRD-PARTY.txt", bundle / "Сторонние компоненты.txt")
    license_root = bundle / "licenses"
    license_root.mkdir()
    # Сохраняем поставленные авторами тексты лицензий без изменения.
    for name in (
        "PySide6",
        "PySide6_Essentials",
        "PySide6_Addons",
        "PySide6_Pdf",
        "shiboken6",
        "reportlab",
        "pillow",
        "charset-normalizer",
    ):
        distribution = importlib.metadata.distribution(name)
        for item in distribution.files or []:
            if "license" in item.name.casefold() or "copying" in item.name.casefold():
                source = Path(distribution.locate_file(item))
                target = license_root / name / item.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    shutil.copy2(python_license, license_root / "Python-LICENSE.txt")
    shutil.copytree(project / "packaging" / "licenses", license_root / "Qt", dirs_exist_ok=True)
    (bundle / "version.txt").write_text(version + "\n", encoding="utf-8")
    manifest = {
        str(file.relative_to(bundle)).replace(os.sep, "/"): hashlib.sha256(
            file.read_bytes()
        ).hexdigest()
        for file in sorted(bundle.rglob("*"))
        if file.is_file()
    }
    (bundle / "manifest-sha256.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    subprocess.run(
        [
            str(compiler),
            f"/DAppVersion={version}",
            f"/DBundleDir={bundle}",
            f"/DReleaseDir={output}",
            str(project / "packaging" / "autoservice.iss"),
        ],
        check=True,
    )
    shutil.make_archive(
        str(output / f"AutoService-{version}-Windows-x64-portable"),
        "zip",
        bundle.parent,
        bundle.name,
    )
    files = sorted(output.glob("*.exe")) + sorted(output.glob("*.zip"))
    (output / "SHA256SUMS.txt").write_text(
        "\n".join(f"{hashlib.sha256(file.read_bytes()).hexdigest()}  {file.name}" for file in files)
        + "\n",
        encoding="utf-8",
    )
    print(f"Готово: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="Новый каталог выпуска")
    parser.add_argument("--compiler", required=True, type=Path, help="Путь к Inno Setup ISCC.exe")
    options = parser.parse_args()
    build(options.output, options.compiler)
