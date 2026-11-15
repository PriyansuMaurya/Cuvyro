"""Build a Microsoft Store MSIX from the frozen Windows application."""

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from PIL import Image


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
LOGOS = {"StoreLogo": 50, "Square150x150Logo": 150, "Square44x44Logo": 44}


def store_version(value):
    parts = value.split(".")
    if (
        len(parts) != 4
        or any(not re.fullmatch(r"0|[1-9][0-9]{0,4}", part) for part in parts)
        or any(int(part) > 65535 for part in parts)
        or parts[0] == "0"
        or parts[-1] != "0"
    ):
        raise argparse.ArgumentTypeError("Use major.minor.patch.0 with a nonzero major")
    return value


def find_makeappx():
    executable = shutil.which("makeappx.exe")
    if executable:
        return executable
    kits = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    candidates = (kits / "Windows Kits" / "10" / "bin").glob("10.*/x64/makeappx.exe")
    latest = max(
        candidates,
        key=lambda path: tuple(map(int, path.parents[1].name.split("."))),
        default=None,
    )
    if latest is None:
        raise SystemExit("Install the Windows SDK or add MakeAppx.exe to PATH.")
    return str(latest)


def write_store_assets(directory):
    directory.mkdir()
    with Image.open(HERE / "cuvyro.ico") as image:
        icon = image.convert("RGBA")
        for name, size in LOGOS.items():
            icon.resize((size, size), Image.Resampling.LANCZOS).save(directory / f"{name}.png")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", type=store_version)
    args = parser.parse_args()
    dist = ROOT / "dist"
    frozen = dist / "Cuvyro"
    if not (frozen / "Cuvyro.exe").is_file() or not (frozen / "_internal").is_dir():
        parser.error("Create the x64 frozen tree with build-installer.bat <version> --freeze-only.")
    models = frozen / "model"
    for relative in ("sherpa/tokens.txt", "silero_vad.onnx", "face_landmarker.task"):
        if not (models / relative).is_file():
            parser.error(f"Frozen application is missing model/{relative}")
    for component in ("encoder", "decoder", "joiner"):
        if not any((models / "sherpa").glob(f"*{component}*.onnx")):
            parser.error(f"Frozen application is missing the Sherpa {component}")

    makeappx = find_makeappx()
    filename = f"Cuvyro-{args.version}-x64.msix"
    output = dist / filename
    with tempfile.TemporaryDirectory(dir=dist, prefix="store-") as directory:
        work = Path(directory)
        stage = work / "package"
        # The app leaves support logs beside its executable, and a local build
        # that was run once keeps a cuvyro_log.txt there whose first line is the
        # install path. None of that belongs in a package the Store serves.
        shutil.copytree(
            frozen, stage, ignore=shutil.ignore_patterns("cuvyro*.log*", "cuvyro*.txt")
        )
        # Vosk is only the fallback engine and sherpa was verified above, so its
        # four model folders would add ~140 MB to every download for nothing.
        for legacy in ("am", "conf", "graph", "ivector"):
            shutil.rmtree(stage / "model" / legacy, ignore_errors=True)
        manifest = (HERE / "AppxManifest.xml").read_text(encoding="utf-8")
        manifest, count = re.subn(
            r'(<Identity\b[^>]*\bVersion=)"[^"]+"', rf'\g<1>"{args.version}"', manifest
        )
        if count != 1:
            raise SystemExit("AppxManifest.xml must declare one package identity version.")
        (stage / "AppxManifest.xml").write_text(manifest, encoding="utf-8")
        write_store_assets(stage / "StoreAssets")
        package = work / filename
        subprocess.run([makeappx, "pack", "/d", str(stage), "/p", str(package)], check=True)
        package.replace(output)

    with output.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    print(f"{output}\nSHA-256 {digest}")


if __name__ == "__main__":
    main()
