import { test } from "node:test";
import assert from "node:assert/strict";
import { isPrivateHost } from "../src/hosts.js";
import { assetFetchPolicy } from "../src/popup-logic.js";

test("isPrivateHost_private", () => {
  for (const h of [
    "localhost", "a.localhost", "nas.local", "x.lan", "x.home", "svc.internal", "box.tail1.ts.net", "nas", "NAS.",
    "127.0.0.1", "10.1.2.3", "172.16.0.1", "172.31.255.1", "192.168.1.5", "169.254.1.1", "100.64.0.1", "100.127.255.255",
    "::1", "[::1]", "fc00::1", "fd12::1", "fe80::1", "[fe80::1]", "::ffff:10.0.0.1",
  ]) assert.equal(isPrivateHost(h), true, h);
});

test("isPrivateHost_public", () => {
  for (const h of ["example.com", "a.b.co.uk", "8.8.8.8", "172.32.0.1", "172.15.0.1", "100.63.0.1", "100.128.0.1", "192.169.0.1", "2001:db8::1", "[2606:4700::1]", "tsnet.example", "locally.com", ""]) {
    assert.equal(isPrivateHost(h), false, h);
  }
});

test("assetFetchPolicy_uses_shared_helper", () => {
  for (const u of ["http://nas/x.png", "http://box.ts.net/x", "http://100.64.1.1/x", "http://printer.lan/x", "http://[fd00::1]/x"]) {
    assert.equal(assetFetchPolicy("https://example.com/p", u).allow, false, u);
  }
  assert.equal(assetFetchPolicy("http://nas/p", "http://nas2/x").allow, true);
});
