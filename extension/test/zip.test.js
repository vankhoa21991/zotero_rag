import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { mkdtempSync, writeFileSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createZip, crc32 } from "../src/zip.js";

const enc = new TextEncoder();
const dec = new TextDecoder();

function readZip(z) {
  const v = new DataView(z.buffer, z.byteOffset, z.byteLength);
  let e = z.length - 22;
  assert.equal(v.getUint32(e, true), 0x06054b50);
  const n = v.getUint16(e + 10, true);
  let p = v.getUint32(e + 16, true);
  const out = [];
  for (let i = 0; i < n; i++) {
    assert.equal(v.getUint32(p, true), 0x02014b50);
    const flags = v.getUint16(p + 8, true), method = v.getUint16(p + 10, true);
    const crc = v.getUint32(p + 16, true), size = v.getUint32(p + 24, true);
    const nl = v.getUint16(p + 28, true), xl = v.getUint16(p + 30, true), cl = v.getUint16(p + 32, true);
    const off = v.getUint32(p + 42, true);
    const name = dec.decode(z.subarray(p + 46, p + 46 + nl));
    const lnl = v.getUint16(off + 26, true), lxl = v.getUint16(off + 28, true);
    const data = z.subarray(off + 30 + lnl + lxl, off + 30 + lnl + lxl + size);
    out.push({ name, flags, method, crc, size, data });
    p += 46 + nl + xl + cl;
  }
  return out;
}

test("crc32 check value", () => {
  assert.equal(crc32(enc.encode("123456789")), 0xcbf43926);
  assert.equal(crc32(new Uint8Array(0)), 0);
});

test("round trip two files, non-ASCII name, empty file", () => {
  const files = [
    { name: "index.html", data: enc.encode("<p>hi</p>") },
    { name: "assets/é-图.png", data: new Uint8Array([0, 1, 2, 255]) },
    { name: "empty.txt", data: new Uint8Array(0) },
  ];
  const got = readZip(createZip(files));
  assert.equal(got.length, 3);
  files.forEach((f, i) => {
    assert.equal(got[i].name, f.name);
    assert.equal(got[i].size, f.data.length);
    assert.equal(got[i].crc, crc32(f.data));
    assert.equal(got[i].method, 0);
    assert.ok(got[i].flags & 0x800);
    assert.deepEqual([...got[i].data], [...f.data]);
  });
});

test("rejects bad input", () => {
  assert.throws(() => createZip([{ name: "", data: new Uint8Array(1) }]), /name/i);
  assert.throws(() => createZip([{ name: "a", data: "str" }]), /Uint8Array/);
  const big = new (class extends Uint8Array { get length() { return 0x7fffffff; } })(1);
  assert.throws(() => createZip([{ name: "a", data: big }, { name: "b", data: big }]), /too large/i);
  assert.throws(() => createZip([{ name: "a", data: new Uint8Array(10) }], 50), /too large/i);
  for (const bad of ["/abs", "a/../b", "..", "a\\b", "a\0b"]) {
    assert.throws(() => createZip([{ name: bad, data: new Uint8Array(0) }]), /unsafe/i, bad);
  }
  assert.throws(
    () => createZip([{ name: "a", data: new Uint8Array(0) }, { name: "a", data: new Uint8Array(0) }]),
    /duplicate/i,
  );
  assert.throws(() => createZip(new Array(70000).fill({ name: "a", data: new Uint8Array(0) })), /too many/i);
});

test("python zipfile reads it", (t) => {
  if (spawnSync("python3", ["--version"]).status !== 0) return t.skip("no python3");
  const dir = mkdtempSync(join(tmpdir(), "zip-"));
  const bin = new Uint8Array(70000).map((_, i) => (i * 7) & 255);
  const files = [
    { name: "index.html", data: enc.encode("<html>é</html>") },
    { name: "assets/a.png", data: bin },
    { name: "assets/ünï.txt", data: new Uint8Array(0) },
  ];
  const zp = join(dir, "t.zip");
  writeFileSync(zp, createZip(files));
  const script = `
import sys, zipfile
z = zipfile.ZipFile(sys.argv[1])
assert z.testzip() is None
print("|".join(f"{i.filename}:{len(z.read(i))}" for i in z.infolist()))
`;
  const out = execFileSync("python3", ["-c", script, zp]).toString().trim();
  assert.equal(out, "index.html:" + files[0].data.length + "|assets/a.png:70000|assets/ünï.txt:0");
  execFileSync("python3", ["-m", "zipfile", "-t", zp]);
  const bytes = execFileSync("python3", ["-c", "import sys,zipfile;sys.stdout.buffer.write(zipfile.ZipFile(sys.argv[1]).read('assets/a.png'))", zp]);
  assert.deepEqual(new Uint8Array(bytes), bin);
});
