"""The extension's zip writer must produce archives the server's reader accepts."""

import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

EXT = Path(__file__).resolve().parents[2] / "extension"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def test_extension_zip_is_readable_by_zipfile(tmp_path):
    out = tmp_path / "snap.zip"
    script = (
        f"import {{createZip}} from {str(EXT / 'src/zip.js')!r};"
        "import {writeFileSync} from 'node:fs';"
        "const e=new TextEncoder();"
        f"writeFileSync({str(out)!r}, createZip(["
        "{name:'index.html',data:e.encode('<p>é</p>')},"
        "{name:'assets/a.png',data:new Uint8Array([0,1,2,255])},"
        "{name:'assets/ünï.txt',data:new Uint8Array(0)}]));"
    )
    subprocess.run(["node", "--input-type=module", "-e", script], check=True)
    with zipfile.ZipFile(out) as z:
        assert z.testzip() is None
        assert z.namelist() == ["index.html", "assets/a.png", "assets/ünï.txt"]
        assert z.read("index.html") == "<p>é</p>".encode()
        assert z.read("assets/a.png") == bytes([0, 1, 2, 255])
        assert z.read("assets/ünï.txt") == b""
