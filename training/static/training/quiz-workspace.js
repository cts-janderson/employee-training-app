"use strict";
(() => {
  const root = document.getElementById("quiz-editor-workspace");
  if (!root) return;
  const enhanced = typeof window.fetch === "function" && typeof FormData === "function" && typeof DOMParser === "function" && typeof AbortController === "function";
  let busy = false;
  let nativeSubmitting = false;
  let dirty = root.querySelector("#editor-form")?.dataset.unsaved === "true";

  function markDirty() {
    dirty = true;
    const form = root.querySelector("#editor-form");
    if (form) form.dataset.unsaved = "true";
    const state = root.querySelector("#save-state");
    if (state) state.textContent = "Unsaved changes.";
    const notice = root.querySelector("[data-quiz-review-unsaved]");
    if (notice) notice.hidden = false;
  }
  function updateRanks(group) {
    const warning = group.querySelector("[data-rank-warning]");
    if (!warning) return;
    const filled = [...group.querySelectorAll('input[type="number"]')].map(input => input.value.trim()).filter(Boolean);
    warning.textContent = new Set(filled).size === filled.length ? "" : "Each ordering rank must be used only once.";
  }
  function updateThreshold(slider) {
    const source = root.querySelector("[data-threshold-input]");
    const number = root.querySelector("[data-threshold-number]");
    source.value = slider.value === "0" ? "" : slider.value;
    number.value = source.value;
    number.setCustomValidity("");
    number.removeAttribute("aria-invalid");
    number.placeholder = "Not set";
    const label = source.value ? `${source.value}%` : "Not set";
    slider.setAttribute("aria-valuetext", label);
  }
  function updateThresholdNumber(number) {
    const source = root.querySelector("[data-threshold-input]");
    const slider = root.querySelector("[data-threshold-slider]");
    const raw = number.value;
    const valid = raw !== "" && Number.isInteger(Number(raw)) && Number(raw) >= 1 && Number(raw) <= 100;
    const blank = raw === "" && !number.validity.badInput;
    source.value = valid ? String(Number(raw)) : raw;
    number.setCustomValidity(valid || blank ? "" : "Enter a whole percentage from 1 to 100.");
    if (valid || blank) {
      slider.value = blank ? "0" : source.value;
      slider.setAttribute("aria-valuetext", blank ? "Not set" : `${source.value}%`);
      number.removeAttribute("aria-invalid");
      number.placeholder = "Not set";
    } else {
      number.setAttribute("aria-invalid", "true");
    }
  }
  function initializeThreshold() {
    const source = root.querySelector("[data-threshold-input]");
    const field = root.querySelector("[data-threshold-number]");
    if (!source || !field || source.type === "hidden") return;
    // Keep one canonical posted value, including blank or invalid submitted input.
    const raw = source.getAttribute("value") || "";
    const number = Number(raw);
    const valid = raw.trim() !== "" && String(number) === raw.trim() && Number.isInteger(number) && number >= 1 && number <= 100;
    const slider = document.createElement("input");
    slider.type = "range";
    slider.min = "0";
    slider.max = "100";
    slider.step = "1";
    slider.value = valid ? String(number) : "0";
    slider.id = source.id;
    slider.className = "quiz-threshold-slider";
    slider.dataset.thresholdSlider = "true";
    slider.setAttribute("aria-describedby", source.getAttribute("aria-describedby") || "threshold-help");
    if (source.hasAttribute("aria-invalid")) slider.setAttribute("aria-invalid", source.getAttribute("aria-invalid"));
    const label = valid ? `${number}%` : raw ? "Invalid value" : "Not set";
    slider.setAttribute("aria-valuetext", label);
    source.type = "hidden";
    source.id = "passing-percent-value";
    source.value = raw;
    source.before(slider);
    field.value = valid ? String(number) : raw;
    field.placeholder = raw && !valid ? "Invalid" : "Not set";
    if (raw && !valid) field.setAttribute("aria-invalid", "true");
    root.querySelectorAll("[data-threshold-scale], [data-threshold-hint], [data-threshold-number-wrap]").forEach(element => { element.hidden = false; });
  }
  function initialize() {
    initializeThreshold();
    root.querySelectorAll("[data-ordering-editor]").forEach(updateRanks);
  }
  function focusWithoutScroll(element) {
    if (element) element.focus({preventScroll:true});
  }
  function restoreScroll(x, y) {
    // Override the site's smooth-scrolling rule for this single restoration.
    const html = document.documentElement;
    const previous = html.style.scrollBehavior;
    html.style.scrollBehavior = "auto";
    window.scrollTo(x, y);
    html.style.scrollBehavior = previous;
  }
  function controlsFor(container, action) {
    return [...container.querySelectorAll('button[name="action"]')].filter(button => button.value === action);
  }
  function questionFor(container, id) {
    return [...container.querySelectorAll("[data-question-id]")].find(card => card.dataset.questionId === id);
  }
  function visible(element) {
    if (!element) return false;
    const rect = element.getBoundingClientRect();
    return rect.bottom > 0 && rect.top < window.innerHeight;
  }
  root.addEventListener("input", event => {
    if (event.target.closest("#editor-form")) {
      if (event.target.matches("[data-threshold-slider]")) updateThreshold(event.target);
      if (event.target.matches("[data-threshold-number]")) updateThresholdNumber(event.target);
      markDirty();
      const group = event.target.closest("[data-ordering-editor]");
      if (group) updateRanks(group);
    }
  });
  root.addEventListener("change", event => {
    if (event.target.closest("#editor-form")) {
      if (event.target.matches("[data-threshold-slider]")) updateThreshold(event.target);
      if (event.target.matches("[data-threshold-number]")) updateThresholdNumber(event.target);
      markDirty();
    }
  });
  root.addEventListener("click", event => {
    if (busy && event.target.closest("a")) event.preventDefault();
  });
  window.addEventListener("beforeunload", event => {
    if ((dirty || busy) && !nativeSubmitting) {
      event.preventDefault();
      event.returnValue = "";
    }
  });

  root.addEventListener("submit", async event => {
    const form = event.target;
    if (form.id !== "editor-form") return;
    if (!enhanced) { nativeSubmitting = true; return; }
    event.preventDefault();
    if (busy) return;
    const submitter = event.submitter;
    const action = submitter?.value || "save";
    const percentage = root.querySelector("[data-threshold-number]");
    if (percentage && action !== "cancel_remove" && !action.startsWith("review_remove") && !percentage.checkValidity()) {
      percentage.reportValidity();
      focusWithoutScroll(percentage);
      return;
    }
    busy = true;
    const data = new FormData(form);
    data.set("action", action);
    const x = window.scrollX, y = window.scrollY;
    const clickedCard = submitter?.closest("[data-question-id]");
    const anchor = clickedCard || [...root.querySelectorAll("[data-question-id]")].find(visible);
    const anchorId = anchor?.dataset.questionId;
    const anchorTop = anchor?.getBoundingClientRect().top;
    const actionIndex = Math.max(0, controlsFor(root, action).indexOf(submitter));
    const previousIds = new Set([...root.querySelectorAll("[id]")].map(element => element.id));
    const outlineOpen = root.querySelector(".quiz-outline")?.open;
    const disclosureScroll = root.querySelector(".quiz-outline nav")?.scrollTop || 0;
    const disabled = [...form.elements].map(element => [element, element.disabled]);
    // Snapshot first, then block edits and repeat submissions until the response arrives.
    disabled.forEach(([element]) => { element.disabled = true; });
    root.querySelectorAll('button[form="editor-form"]').forEach(button => {
      if (!disabled.some(([element]) => element === button)) {
        disabled.push([button, button.disabled]); button.disabled = true;
      }
    });
    root.setAttribute("aria-busy", "true");
    const status = root.querySelector("#quiz-request-status");
    if (status) { status.classList.remove("error"); status.setAttribute("role", "status"); status.textContent = "Updating quiz…"; }
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 30000);
    try {
      const endpoint = new URL(form.action || window.location.href, window.location.href);
      const response = await window.fetch(endpoint.href, {
        method:"POST", body:data, signal:controller.signal, credentials:"same-origin", headers:{Accept:"text/html"},
      });
      if (![200, 400, 409].includes(response.status) || !response.headers.get("content-type")?.includes("text/html")) {
        throw new Error("Unexpected quiz response");
      }
      const finalURL = new URL(response.url || endpoint.href, endpoint.href);
      if (finalURL.origin !== endpoint.origin || finalURL.pathname !== endpoint.pathname) throw new Error("Unexpected quiz destination");
      const next = new DOMParser().parseFromString(await response.text(), "text/html").getElementById("quiz-editor-workspace");
      if (!next?.querySelector("#editor-form")) throw new Error("Missing quiz response");
      next.querySelectorAll("script").forEach(script => script.remove());
      // Keep confirmations and errors next to the action that opened them.
      const nextAnchor = anchorId ? questionFor(next, anchorId) : null;
      const review = next.querySelector("#remove-review");
      if (review && nextAnchor) {
        const optionId = action.startsWith("review_remove_option:") ? action.split(":")[1] : null;
        const option = optionId && [...nextAnchor.querySelectorAll('input[type="hidden"]')].find(input => input.value === optionId)?.closest(".option-row");
        (option || nextAnchor).after(review);
      }
      const error = next.querySelector("#quiz-editor-errors, #quiz-conflict");
      if (error && nextAnchor) nextAnchor.append(error);
      root.innerHTML = next.innerHTML;
      const newOutline = root.querySelector(".quiz-outline");
      if (newOutline && outlineOpen !== undefined) newOutline.open = outlineOpen;
      const outlineNav = root.querySelector(".quiz-outline nav");
      if (outlineNav) outlineNav.scrollTop = disclosureScroll;
      dirty = root.querySelector("#editor-form").dataset.unsaved === "true";
      initialize();
      const currentAnchor = anchorId ? questionFor(root, anchorId) : null;
      const targetY = currentAnchor ? window.scrollY + currentAnchor.getBoundingClientRect().top - anchorTop : y;
      const newField = [...root.querySelectorAll('input[id^="option-"], textarea[id^="prompt-"]')].find(input => !previousIds.has(input.id));
      const actionControl = controlsFor(root, action)[actionIndex] || controlsFor(root, action)[0];
      const attention = root.querySelector("#quiz-editor-errors, #quiz-conflict, #remove-review");
      const fallback = currentAnchor?.querySelector("h2") || root.querySelector("#quiz-actions, #editor-form");
      restoreScroll(x, targetY);
      focusWithoutScroll(attention || (visible(newField) ? newField : actionControl) || fallback);
      restoreScroll(x, targetY);
      const updatedStatus = root.querySelector("#quiz-request-status");
      if (updatedStatus) updatedStatus.textContent = response.status === 409 ? "Changes were not saved. Review the conflict." :
        response.status === 400 ? "Changes were not saved. Check the highlighted fields." :
        action.startsWith("review_remove") ? "Review removal before confirming." :
        action === "cancel_remove" ? "Removal canceled. Your field edits are still unsaved." : "Quiz updated.";
    } catch {
      // The server may have committed before the connection failed. Never retry automatically.
      markDirty();
      const message = root.querySelector("#quiz-request-status");
      if (message) {
        message.classList.add("error"); message.setAttribute("role", "alert");
        message.textContent = "Could not confirm the update. Your fields are still here. Check the saved quiz before retrying.";
        focusWithoutScroll(message);
      }
      restoreScroll(x, y);
    } finally {
      window.clearTimeout(timeout);
      disabled.forEach(([element, wasDisabled]) => { element.disabled = wasDisabled; });
      root.removeAttribute("aria-busy");
      busy = false;
    }
  });
  initialize();
  // Initial full-page responses still support validation links and server redirects.
  let hash = "";
  try { hash = decodeURIComponent(window.location.hash.slice(1)); } catch { /* Ignore malformed anchors. */ }
  const initialFocus = root.querySelector("#quiz-editor-errors, #remove-review") || (hash && document.getElementById(hash));
  if (initialFocus) initialFocus.focus();
})();
