// Store-only zip writer with CRC32. Pure: no DOM access.

const MAX_TOTAL = 0xfffffffe; // classic zip (no zip64): offsets and sizes are 32-bit
const MAX_ENTRIES = 0xffff;

let TABLE;
function table() {
  if (!TABLE) {
    TABLE = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      TABLE[n] = c >>> 0;
    }
  }
  return TABLE;
}

export function crc32(bytes) {
  const t = table();
  let c = 0xffffffff;
  for (let i = 0; i < bytes.length; i++) c = t[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

export function createZip(files) {
  if (!Array.isArray(files)) throw new TypeError("files must be an array");
  if (files.length > MAX_ENTRIES) throw new RangeError(`Too many files for a zip (${files.length}).`);
  const enc = new TextEncoder();
  const entries = [];
  let total = 22;
  for (const f of files) {
    if (!f || typeof f.name !== "string" || !f.name) throw new TypeError("Each file needs a non-empty name.");
    if (!(f.data instanceof Uint8Array)) throw new TypeError(`File "${f.name}": data must be a Uint8Array.`);
    const name = enc.encode(f.name);
    if (name.length > 0xffff) throw new RangeError(`File name too long: ${f.name.slice(0, 50)}...`);
    total += 30 + name.length + 46 + name.length + f.data.length;
    if (total > MAX_TOTAL) throw new RangeError("Snapshot too large to zip (over 4 GiB).");
    entries.push({ name, data: f.data, crc: crc32(f.data) });
  }
  const out = new Uint8Array(total);
  const v = new DataView(out.buffer);
  let p = 0;
  const now = new Date();
  const dosTime = (now.getHours() << 11) | (now.getMinutes() << 5) | (now.getSeconds() >> 1);
  const dosDate = (Math.max(now.getFullYear() - 1980, 0) << 9) | ((now.getMonth() + 1) << 5) | now.getDate();

  for (const e of entries) {
    e.offset = p;
    v.setUint32(p, 0x04034b50, true);
    v.setUint16(p + 4, 20, true);
    v.setUint16(p + 6, 0x0800, true); // UTF-8 names
    v.setUint16(p + 8, 0, true); // store
    v.setUint16(p + 10, dosTime, true);
    v.setUint16(p + 12, dosDate, true);
    v.setUint32(p + 14, e.crc, true);
    v.setUint32(p + 18, e.data.length, true);
    v.setUint32(p + 22, e.data.length, true);
    v.setUint16(p + 26, e.name.length, true);
    v.setUint16(p + 28, 0, true);
    out.set(e.name, p + 30);
    out.set(e.data, p + 30 + e.name.length);
    p += 30 + e.name.length + e.data.length;
  }
  const cdStart = p;
  for (const e of entries) {
    v.setUint32(p, 0x02014b50, true);
    v.setUint16(p + 4, 20, true);
    v.setUint16(p + 6, 20, true);
    v.setUint16(p + 8, 0x0800, true);
    v.setUint16(p + 10, 0, true);
    v.setUint16(p + 12, dosTime, true);
    v.setUint16(p + 14, dosDate, true);
    v.setUint32(p + 16, e.crc, true);
    v.setUint32(p + 20, e.data.length, true);
    v.setUint32(p + 24, e.data.length, true);
    v.setUint16(p + 28, e.name.length, true);
    v.setUint32(p + 42, e.offset, true);
    out.set(e.name, p + 46);
    p += 46 + e.name.length;
  }
  v.setUint32(p, 0x06054b50, true);
  v.setUint16(p + 8, entries.length, true);
  v.setUint16(p + 10, entries.length, true);
  v.setUint32(p + 12, p - cdStart, true);
  v.setUint32(p + 16, cdStart, true);
  return out;
}
