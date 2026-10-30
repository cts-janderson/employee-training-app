const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const vm = require("node:vm");

const source = readFileSync(join(__dirname, "../static/training/passkeys.js"), "utf8");
// Objects made inside the vm context have its prototypes, so compare plain copies.
const plain = value => JSON.parse(JSON.stringify(value));

// Load the script without a browser; with no PublicKeyCredential it wires nothing and only exports helpers.
function load(fetchImpl) {
  const context = { btoa, atob, Uint8Array, FormData, JSON, fetch: fetchImpl, globalThis: {} };
  context.globalThis = context;
  context.window = {};
  context.document = { querySelectorAll: () => [], querySelector: () => null };
  context.navigator = {};
  vm.runInNewContext(source, context);
  return context.TrainingPasskeys;
}

test("base64url round-trips bytes without padding", () => {
  const passkeys = load();
  const bytes = new Uint8Array([0, 250, 251, 252, 253, 254, 255]);
  const encoded = passkeys.toBase64url(bytes.buffer);
  assert.equal(encoded, "APr7_P3-_w");
  assert.deepEqual([...new Uint8Array(passkeys.fromBase64url(encoded))], [...bytes]);
});

test("creation and request options turn base64url fields into buffers", () => {
  const passkeys = load();
  const creation = passkeys.creationOptions({
    challenge: "AQID", user: { id: "BAU", name: "a@example.com" }, excludeCredentials: [{ id: "Bg", type: "public-key" }],
  });
  assert.deepEqual([...new Uint8Array(creation.challenge)], [1, 2, 3]);
  assert.deepEqual([...new Uint8Array(creation.user.id)], [4, 5]);
  assert.deepEqual([...new Uint8Array(creation.excludeCredentials[0].id)], [6]);
  const request = passkeys.requestOptions({ challenge: "AQID", userVerification: "required" });
  assert.deepEqual(plain(request.allowCredentials), []);
  assert.equal(request.userVerification, "required");
});

test("an assertion is serialized with its user handle", () => {
  const passkeys = load();
  const buffer = values => new Uint8Array(values).buffer;
  const json = passkeys.credentialJson({
    id: "AQ", rawId: buffer([1]), type: "public-key", getClientExtensionResults: () => ({}),
    response: { clientDataJSON: buffer([2]), authenticatorData: buffer([3]), signature: buffer([4]), userHandle: buffer([5]) },
  });
  assert.deepEqual(plain(json.response), { clientDataJSON: "Ag", authenticatorData: "Aw", signature: "BA", userHandle: "BQ" });
});

test("requests send the CSRF token and surface the server's error", async () => {
  const calls = [];
  const passkeys = load(async (url, init) => {
    calls.push({ url, init });
    return { ok: false, json: async () => ({ error: "That password isn't correct." }) };
  });
  await assert.rejects(passkeys.post("/options/", "token-1", { a: 1 }), /That password isn't correct/);
  assert.equal(calls[0].init.headers["X-CSRFToken"], "token-1");
  assert.equal(calls[0].init.headers["Content-Type"], "application/json");
  assert.equal(calls[0].init.body, JSON.stringify({ a: 1 }));
});
