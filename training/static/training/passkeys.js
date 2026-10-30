"use strict";
// Passkey sign-in and registration; see docs/plans/33-sign-in-attack-protection.md section D.
(() => {
  function toBase64url(buffer) {
    const bytes = new Uint8Array(buffer);
    let text = "";
    for (const byte of bytes) text += String.fromCharCode(byte);
    return btoa(text).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function fromBase64url(value) {
    const text = atob(value.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((value.length + 3) % 4));
    return Uint8Array.from(text, character => character.charCodeAt(0)).buffer;
  }

  function creationOptions(json) {
    return {
      ...json,
      challenge: fromBase64url(json.challenge),
      user: { ...json.user, id: fromBase64url(json.user.id) },
      excludeCredentials: (json.excludeCredentials || []).map(item => ({ ...item, id: fromBase64url(item.id) })),
    };
  }

  function requestOptions(json) {
    return {
      ...json,
      challenge: fromBase64url(json.challenge),
      allowCredentials: (json.allowCredentials || []).map(item => ({ ...item, id: fromBase64url(item.id) })),
    };
  }

  function credentialJson(credential) {
    if (typeof credential.toJSON === "function") return credential.toJSON();
    const response = credential.response;
    const json = {
      id: credential.id,
      rawId: toBase64url(credential.rawId),
      type: credential.type,
      clientExtensionResults: credential.getClientExtensionResults ? credential.getClientExtensionResults() : {},
      response: { clientDataJSON: toBase64url(response.clientDataJSON) },
    };
    if (response.attestationObject) {
      json.response.attestationObject = toBase64url(response.attestationObject);
      json.response.transports = response.getTransports ? response.getTransports() : [];
    } else {
      json.response.authenticatorData = toBase64url(response.authenticatorData);
      json.response.signature = toBase64url(response.signature);
      if (response.userHandle) json.response.userHandle = toBase64url(response.userHandle);
    }
    return json;
  }

  function csrfToken(root) {
    const input = root.querySelector("input[name=csrfmiddlewaretoken]")
      || document.querySelector("input[name=csrfmiddlewaretoken]");
    return input ? input.value : "";
  }

  async function post(url, token, body) {
    const isForm = body instanceof FormData;
    const response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: isForm ? { "X-CSRFToken": token } : { "X-CSRFToken": token, "Content-Type": "application/json" },
      body: isForm ? body : JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "The passkey request failed. Try again.");
    return data;
  }

  function showError(root, message) {
    const error = root.querySelector("[data-passkey-error]");
    if (!error) return;
    error.textContent = message;
    error.hidden = !message;
  }

  async function signIn(root) {
    const token = csrfToken(root);
    const options = await post(root.dataset.optionsUrl, token, {});
    const credential = await navigator.credentials.get({ publicKey: requestOptions(options) });
    const result = await post(root.dataset.verifyUrl, token, { credential: credentialJson(credential) });
    window.location.assign(result.redirect);
  }

  async function register(form) {
    const token = csrfToken(form);
    const options = await post(form.dataset.optionsUrl, token, new FormData(form));
    const credential = await navigator.credentials.create({ publicKey: creationOptions(options) });
    await post(form.dataset.verifyUrl, token, { credential: credentialJson(credential), name: form.elements.name.value });
    window.location.reload();
  }

  function supported() {
    return typeof window !== "undefined" && window.PublicKeyCredential && navigator.credentials;
  }

  if (supported()) {
    document.querySelectorAll("[data-passkey-sign-in]").forEach(root => {
      root.hidden = false;
      root.querySelector("button").addEventListener("click", () => {
        showError(root, "");
        signIn(root).catch(error => showError(root, error.name === "NotAllowedError"
          ? "The passkey request was cancelled." : error.message));
      });
    });
    document.querySelectorAll("[data-passkey-register]").forEach(form => {
      form.hidden = false;
      form.addEventListener("submit", event => {
        event.preventDefault();
        showError(form, "");
        register(form).catch(error => showError(form, error.name === "NotAllowedError"
          ? "The passkey request was cancelled." : error.message));
      });
    });
  }

  globalThis.TrainingPasskeys = { toBase64url, fromBase64url, creationOptions, requestOptions, credentialJson, post };
})();
