// Single definition of "private host" shared by api.js (URL scheme default, options note) and
// popup-logic.js (asset fetch policy). Pure. Names/addresses only: a public DNS name that resolves
// to a LAN address cannot be recognised here.

const PRIVATE_SUFFIXES = [".localhost", ".local", ".lan", ".home", ".internal", ".ts.net"];

function privateIPv4(h) {
  const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(h);
  if (!m) return null;
  const [a, b] = [Number(m[1]), Number(m[2])];
  return a === 0 || a === 10 || a === 127 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168)
    || (a === 169 && b === 254) || (a === 100 && b >= 64 && b <= 127);
}

export function isPrivateHost(host) {
  const h = String(host ?? "").trim().toLowerCase().replace(/^\[|\]$/g, "").replace(/\.$/, "");
  if (!h) return false;
  if (h.includes(":")) {
    if (h === "::1" || h === "::") return true;
    const first = parseInt(h.split(":")[0] || "0", 16);
    if ((first & 0xfe00) === 0xfc00 || (first & 0xffc0) === 0xfe80) return true;
    return /^::ffff:/.test(h); // IPv4-mapped: be conservative
  }
  const v4 = privateIPv4(h);
  if (v4 !== null) return v4;
  if (h === "localhost" || PRIVATE_SUFFIXES.some((s) => h.endsWith(s))) return true;
  return !h.includes("."); // single-label hostname (http://nas/)
}
