"""Validate every shipped byte against the release inventory, without extracting it."""

import argparse
import hashlib
import json
import zipfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    archive = args.archive.resolve()
    root = archive.name.removesuffix(".zip")
    with zipfile.ZipFile(archive) as release:
        names = release.namelist()
        assert len(names) == len(set(names)), "Duplicate ZIP entries"
        assert all(name.startswith(root + "/") and ".." not in Path(name).parts for name in names)
        assert not any(
            Path(name).name.lower() == ".env"
            or f"{root}/user-data/" in name
            or f"{root}/data/" in name
            for name in names
        ), "Private profile in release"
        manifest = json.loads(release.read(f"{root}/SHA256SUMS.json"))
        expected = {f"{root}/{name}" for name in manifest} | {f"{root}/SHA256SUMS.json"}
        assert set(names) == expected, "Archive inventory mismatch"
        for name, digest in manifest.items():
            computed = hashlib.sha256()
            with release.open(f"{root}/{name}") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    computed.update(chunk)
            assert computed.hexdigest() == digest, f"Checksum mismatch: {name}"
        unpacked = sum(info.file_size for info in release.infolist())
    checksum = hashlib.sha256()
    with archive.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            checksum.update(chunk)
    sha = checksum.hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{sha}  {archive.name}\n", encoding="utf-8")
    report = {
        "passed": True,
        "files": len(names),
        "archive": str(archive),
        "sha256": sha,
        "archive_mib": round(archive.stat().st_size / 1024**2, 1),
        "unpacked_mib": round(unpacked / 1024**2, 1),
        "private_profiles_included": False,
    }
    if args.report:
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
