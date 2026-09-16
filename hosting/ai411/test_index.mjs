import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const html = readFileSync(new URL("./index.html", import.meta.url), "utf8");
const scriptMatch = html.match(/<script>\s*([\s\S]*?)\s*<\/script>/);
assert.ok(scriptMatch, "AI411 landing must contain its client script");
const script = scriptMatch[1];

const CALLBACK_ERROR =
  "We couldn't register your request right now. Please try again in a moment.";
const PERSONAL_PAGE_ERROR =
  "We couldn't create your page right now. Please try again in a moment.";

class FakeElement {
  constructor(id, value = "") {
    this.id = id;
    this.value = value;
    this.checked = false;
    this.className = "";
    this.textContent = "";
    this.disabled = false;
    this.listeners = new Map();
    this.resetCount = 0;
    this.children = [];
  }

  getAttribute(name) {
    return name === "data-api" ? null : undefined;
  }

  addEventListener(name, handler) {
    this.listeners.set(name, handler);
  }

  dispatch(name) {
    const handler = this.listeners.get(name);
    assert.ok(handler, `${this.id} should register a ${name} handler`);
    return handler({ preventDefault() {} });
  }

  reset() {
    this.resetCount += 1;
  }

  append(...parts) {
    this.children.push(...parts);
  }
}

function response(body, ok = true) {
  return { ok, json: async () => body };
}

function makePage(fetchImpl) {
  const elements = new Map([
    ["cb-form", new FakeElement("cb-form")],
    ["msg", new FakeElement("msg")],
    ["submit", new FakeElement("submit")],
    ["phone", new FakeElement("phone", "3525550100")],
    ["business_name", new FakeElement("business_name", "Cool Cafe")],
    ["email", new FakeElement("email", "owner@example.test")],
    ["pp-form", new FakeElement("pp-form")],
    ["pp_msg", new FakeElement("pp_msg")],
    ["pp_submit", new FakeElement("pp_submit")],
    ["pp_phone", new FakeElement("pp_phone", "3525550101")],
    ["pp_name", new FakeElement("pp_name", "Alex")],
    ["pp_headline", new FakeElement("pp_headline", "Local music")],
    ["pp_consent", new FakeElement("pp_consent")],
  ]);
  const document = {
    getElementById(id) {
      const element = elements.get(id);
      assert.ok(element, `missing fake element ${id}`);
      return element;
    },
    createElement(tag) {
      assert.equal(tag, "a");
      return new FakeElement(tag);
    },
  };
  const context = {
    window: {
      AI411_API: "https://example.test/register",
      AI411_PERSONAL_PAGE_API: "https://example.test/personal-page",
    },
    document,
    fetch: fetchImpl,
  };
  vm.runInNewContext(script, context);
  return { elements };
}

async function testCallbackErrorHidesServerDetails() {
  const page = makePage(async () => response({
    ok: false,
    detail: "database host=db.internal password=secret",
    error: "provider timeout",
    message: "internal trace id 123",
  }, false));
  const form = page.elements.get("cb-form");
  const msg = page.elements.get("msg");
  const button = page.elements.get("submit");

  await form.dispatch("submit");

  assert.equal(msg.className, "msg err");
  assert.equal(msg.textContent, CALLBACK_ERROR);
  assert.equal(button.disabled, false);
  assert.equal(form.resetCount, 0);
}

async function testNetworkErrorHidesExceptionDetails() {
  const page = makePage(async () => {
    throw new Error("provider secret and internal stack");
  });
  const form = page.elements.get("cb-form");
  const msg = page.elements.get("msg");

  await form.dispatch("submit");

  assert.equal(msg.textContent, CALLBACK_ERROR);
  assert.equal(msg.className, "msg err");
}

async function testCallbackSuccessIsPreserved() {
  const page = makePage(async () => response({
    ok: true,
    message: "You're on the list.",
    voice_number: "+135****0100",
  }));
  const form = page.elements.get("cb-form");
  const msg = page.elements.get("msg");

  await form.dispatch("submit");

  assert.equal(msg.className, "msg ok");
  assert.match(msg.textContent, /You're on the list/);
  assert.match(msg.textContent, /\+135\*\*\*\*0100/);
  assert.equal(form.resetCount, 1);
}

async function testPersonalPageErrorHidesServerDetails() {
  const page = makePage(async () => response({
    ok: false,
    detail: "traceback: internal provider failure",
    error: "provider=secret",
    message: "raw internal message",
  }, false));
  const consent = page.elements.get("pp_consent");
  consent.checked = true;
  const form = page.elements.get("pp-form");
  const msg = page.elements.get("pp_msg");
  const button = page.elements.get("pp_submit");

  await form.dispatch("submit");

  assert.equal(msg.className, "msg err");
  assert.equal(msg.textContent, PERSONAL_PAGE_ERROR);
  assert.equal(button.disabled, false);
  assert.equal(form.resetCount, 0);
}

async function testConsentAndSafePersonalSuccess() {
  let fetchCalls = 0;
  const responses = [
    response({
      ok: true,
      message: "<img src=x onerror=alert(1)>",
      url: "javascript:alert(1)",
    }),
    response({
      ok: true,
      message: "Your page is ready.",
      url: "https://example.test/me/p-1234/",
    }),
  ];
  const page = makePage(async () => {
    fetchCalls += 1;
    return responses.shift();
  });
  const form = page.elements.get("pp-form");
  const msg = page.elements.get("pp_msg");

  await form.dispatch("submit");
  assert.equal(msg.textContent, "Check the opt-in box to continue.");
  assert.equal(fetchCalls, 0);

  page.elements.get("pp_consent").checked = true;
  await form.dispatch("submit");

  assert.equal(msg.className, "msg ok");
  assert.equal(msg.textContent, "<img src=x onerror=alert(1)>");
  assert.equal(msg.children.length, 0, "unsafe URLs must not become links");
  assert.equal(form.resetCount, 1);

  await form.dispatch("submit");
  assert.equal(msg.textContent, "Your page is ready.");
  assert.equal(msg.children.length, 3);
  assert.equal(msg.children[1].textContent, "Open your page");
  assert.equal(msg.children[1].href, "https://example.test/me/p-1234/");
  assert.equal(msg.children[1].target, "_blank");
  assert.equal(msg.children[1].rel, "noopener");
  assert.equal(form.resetCount, 2);
}

await testCallbackErrorHidesServerDetails();
await testNetworkErrorHidesExceptionDetails();
await testCallbackSuccessIsPreserved();
await testPersonalPageErrorHidesServerDetails();
await testConsentAndSafePersonalSuccess();
console.log("AI411 landing client checks passed (5 scenarios)");
