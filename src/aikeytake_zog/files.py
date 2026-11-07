"""Content-addressed file store for aikeytake_zog attachments.

Contract: files live at `<files_dir>/<first 2 hex of sha256>/<sha256>.<ext>`;
`put` is idempotent (identical content is written to disk only once) and never
leaves a partial file behind (temp file + os.replace). Snapshots (HTML plus
assets) are stored as one zip under the same scheme; `read_zip_member`
refuses path traversal and absolute member names.
"""
import hashlib
import io
import mimetypes
import os
import zipfile
from pathlib import Path, PurePosixPath

_CHUNK = 1024 * 1024


def _normalize_ext(ext: str) -> str:
    return (ext or "").lstrip(".").lower()


def path_for(files_dir: Path, sha256: str, ext: str) -> Path:
    return files_dir / sha256[:2] / f"{sha256}.{_normalize_ext(ext)}"


def exists(files_dir: Path, sha256: str, ext: str) -> bool:
    return path_for(files_dir, sha256, ext).is_file()


def put(files_dir: Path, data: bytes | io.BinaryIO, ext: str) -> tuple[str, int]:
    """Store `data` by content hash; returns (sha256, size in bytes)."""
    if isinstance(data, (bytes, bytearray)):
        blob = bytes(data)
        digest = hashlib.sha256(blob)
        chunks: list[bytes] = [blob]
    else:
        digest = hashlib.sha256()
        chunks = []
        while chunk := data.read(_CHUNK):
            digest.update(chunk)
            chunks.append(chunk)
    sha = digest.hexdigest()
    size = sum(len(c) for c in chunks)
    target = path_for(files_dir, sha, ext)
    if not target.is_file():
        _write(target, chunks)
    return sha, size


def _write(target: Path, chunks: list[bytes]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".part")
    with open(tmp, "wb") as f:
        for chunk in chunks:
            f.write(chunk)
    os.replace(tmp, target)


def zip_directory(src_dir: Path) -> bytes:
    """Zip a directory (e.g. an HTML snapshot with assets), relative paths kept."""
    src_dir = Path(src_dir)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(src_dir.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(src_dir).as_posix())
    return buf.getvalue()


def read_zip_member(files_dir: Path, sha256: str, member: str) -> tuple[bytes, str]:
    """Read one member of a stored snapshot zip; returns (bytes, content type)."""
    _check_member(member)
    path = path_for(files_dir, sha256, "zip")
    with zipfile.ZipFile(path) as zf:
        try:
            data = zf.read(member)
        except KeyError:
            raise FileNotFoundError(member) from None
    ctype = mimetypes.guess_type(member)[0] or "application/octet-stream"
    return data, ctype


def _check_member(member: str) -> None:
    if member.startswith("/") or ".." in PurePosixPath(member).parts:
        raise ValueError(f"invalid zip member name: {member!r}")
