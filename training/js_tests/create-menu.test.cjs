const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const vm = require("node:vm");

const source = readFileSync(join(__dirname, "../static/training/create-menu.js"), "utf8");

// Test event behavior without opening or automating a browser. Native <button>
// supplies mouse/Enter/Space click events; the script must toggle explicitly.
class Element extends EventTarget {
  constructor(parent = null, hidden = false) {
    super();
    this.parent = parent;
    this.hidden = hidden;
    this.attributes = {};
  }
  setAttribute(name, value) { this.attributes[name] = value; }
  contains(element) {
    while (element) {
      if (element === this) return true;
      element = element.parent;
    }
    return false;
  }
  focus() { this.focused = true; }
}

function setup({ complete = true } = {}) {
  const menu = new Element();
  const trigger = new Element(menu, true);
  const fallback = new Element(menu);
  const options = new Element(menu, true);
  const action = new Element(options);
  const outside = new Element();
  const document = new EventTarget();
  menu.querySelector = selector => ({
    ".create-menu-trigger": trigger,
    ".create-menu-fallback": fallback,
    ".create-menu-options": complete ? options : null,
  })[selector];
  document.querySelectorAll = () => [menu];
  vm.runInNewContext(source, { document });
  return { menu, trigger, fallback, options, action, outside, document };
}

function dispatch(target, type, properties = {}) {
  const event = new Event(type, { cancelable: true });
  for (const [key, value] of Object.entries(properties)) Object.defineProperty(event, key, { value });
  target.dispatchEvent(event);
  return event;
}

test("Create content opens choices on click and closes on a second click", () => {
  const { trigger, fallback, options } = setup();
  assert.equal(trigger.hidden, false);
  assert.equal(fallback.hidden, true);
  assert.equal(options.hidden, true);
  dispatch(trigger, "click");
  assert.equal(options.hidden, false);
  assert.equal(trigger.attributes["aria-expanded"], "true");
  dispatch(trigger, "click");
  assert.equal(options.hidden, true);
  assert.equal(trigger.attributes["aria-expanded"], "false");
});

test("clicks and focus within the choices keep them available", () => {
  const { trigger, menu, options, action, document } = setup();
  dispatch(trigger, "click");
  dispatch(document, "click", { target: action });
  dispatch(menu, "focusout", { relatedTarget: action });
  assert.equal(options.hidden, false);
});

test("Escape closes the choices and restores trigger focus", () => {
  const { trigger, menu, options } = setup();
  dispatch(trigger, "click");
  const event = dispatch(menu, "keydown", { key: "Escape" });
  assert.equal(options.hidden, true);
  assert.equal(trigger.focused, true);
  assert.equal(event.defaultPrevented, true);
});

test("clicking or moving focus outside dismisses the choices", () => {
  const { trigger, menu, options, outside, document } = setup();
  dispatch(trigger, "click");
  dispatch(document, "click", { target: outside });
  assert.equal(options.hidden, true);
  dispatch(trigger, "click");
  dispatch(menu, "focusout", { relatedTarget: outside });
  assert.equal(options.hidden, true);
});

test("an incomplete control leaves its normal navigation fallback available", () => {
  const { trigger, fallback } = setup({ complete: false });
  assert.equal(trigger.hidden, true);
  assert.equal(fallback.hidden, false);
});
