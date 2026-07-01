from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from dynamic.runtime_extractors.utils import parse_json_value, write_json

if TYPE_CHECKING:
    from browser_use.actor.page import Page


RUNTIME_INSTRUMENTATION_BODY = r"""
const A11Y_MAX_EVENTS = 200;
const A11Y_MAX_TEXT = 500;
function a11yTruncate(value, maxLength = A11Y_MAX_TEXT) {
  if (value === null || value === undefined) {
    return null;
  }
  const text = String(value);
  return text.length > maxLength ? text.slice(0, maxLength) + "...[truncated]" : text;
}
function a11yStore() {
  if (!window.__a11yRuntime) {
    window.__a11yRuntime = {
      installed: false,
      installedAt: new Date().toISOString(),
      events: {
        keyboard: [],
        pointer: [],
        focus: [],
        input: [],
        change: [],
        submit: [],
        mutations: [],
        liveRegionMutations: [],
        media: [],
        listenerRegistrations: []
      },
      counters: {},
      mediaPreviousTimes: {},
      mediaKnown: {},
      lastKeyboardBefore: null
    };
  }
  return window.__a11yRuntime;
}
function a11yPush(name, payload) {
  const store = a11yStore();
  if (!store.events[name]) {
    store.events[name] = [];
  }
  store.counters[name] = (store.counters[name] || 0) + 1;
  store.events[name].push(payload);
  if (store.events[name].length > A11Y_MAX_EVENTS) {
    store.events[name] = store.events[name].slice(-A11Y_MAX_EVENTS);
  }
}
function a11yXPath(node) {
  if (!node || node.nodeType !== 1) {
    return "/";
  }
  let path = "";
  let current = node;
  while (current && current.nodeType === 1) {
    let index = 1;
    let sibling = current.previousSibling;
    while (sibling) {
      if (sibling.nodeType === 1 && sibling.nodeName === current.nodeName) {
        index += 1;
      }
      sibling = sibling.previousSibling;
    }
    path = `/${current.nodeName.toLowerCase()}[${index}]${path}`;
    current = current.parentNode;
  }
  return path || "/";
}
function a11yElementName(el) {
  if (!el || el.nodeType !== 1) {
    return null;
  }
  return (
    el.getAttribute("aria-label") ||
    el.getAttribute("alt") ||
    el.getAttribute("title") ||
    el.getAttribute("name") ||
    a11yTruncate((el.textContent || "").trim(), 120)
  );
}
function a11yTarget(el) {
  if (!el || el.nodeType !== 1) {
    return {};
  }
  const rect = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
  return {
    tag: el.tagName ? el.tagName.toLowerCase() : null,
    id: el.id || null,
    role: el.getAttribute ? el.getAttribute("role") : null,
    name: a11yElementName(el),
    xpath: a11yXPath(el),
    type: el.getAttribute ? el.getAttribute("type") : null,
    autocomplete: el.getAttribute ? el.getAttribute("autocomplete") : null,
    required: Boolean(el.required || (el.getAttribute && el.getAttribute("aria-required") === "true")),
    aria_invalid: el.getAttribute ? el.getAttribute("aria-invalid") : null,
    aria_describedby: el.getAttribute ? el.getAttribute("aria-describedby") : null,
    aria_errormessage: el.getAttribute ? el.getAttribute("aria-errormessage") : null,
    form_xpath: el.form ? a11yXPath(el.form) : (el.closest && el.closest("form") ? a11yXPath(el.closest("form")) : null),
    rect: rect ? {
      x: Math.round(rect.x),
      y: Math.round(rect.y),
      width: Math.round(rect.width),
      height: Math.round(rect.height)
    } : null,
    visible: Boolean(rect && rect.width > 0 && rect.height > 0)
  };
}
function a11yLinkedText(el, attributeName) {
  if (!el || !el.getAttribute) {
    return [];
  }
  const ids = String(el.getAttribute(attributeName) || "").split(/\s+/).filter(Boolean);
  return ids.map((id) => {
    const node = document.getElementById(id);
    if (!node) {
      return null;
    }
    return {
      id,
      role: node.getAttribute("role"),
      class_name: a11yTruncate(node.className || "", 160),
      text: a11yTruncate((node.textContent || "").trim(), 300),
      visible: Boolean(node.getClientRects && node.getClientRects().length)
    };
  }).filter(Boolean);
}
function a11yFormControlState(el) {
  if (!el || el.nodeType !== 1) {
    return {};
  }
  const described = a11yLinkedText(el, "aria-describedby");
  const errorLinks = a11yLinkedText(el, "aria-errormessage");
  const linked = described.concat(errorLinks);
  const errorText = linked.filter((item) => {
    const marker = `${item.id || ""} ${item.role || ""} ${item.class_name || ""}`.toLowerCase();
    return item.role === "alert" || item.role === "status" || /(error|invalid|danger|required)/.test(marker);
  });
  const helpText = described.filter((item) => {
    const marker = `${item.id || ""} ${item.class_name || ""}`.toLowerCase();
    return /(help|hint|instruction|description|assist)/.test(marker) && !/(error|invalid|danger)/.test(marker);
  });
  const validity = el.validity ? {
    valid: el.validity.valid,
    value_missing: el.validity.valueMissing,
    type_mismatch: el.validity.typeMismatch,
    pattern_mismatch: el.validity.patternMismatch,
    too_short: el.validity.tooShort,
    too_long: el.validity.tooLong,
    range_underflow: el.validity.rangeUnderflow,
    range_overflow: el.validity.rangeOverflow,
    step_mismatch: el.validity.stepMismatch,
    custom_error: el.validity.customError
  } : null;
  const serializedErrors = errorText.map((item) => item.text || "").join(" ").toLowerCase();
  return {
    target: a11yTarget(el),
    form: a11yTarget(el.form || (el.closest ? el.closest("form") : null)),
    validity,
    invalid: Boolean(
      (validity && validity.valid === false) ||
      (el.getAttribute && el.getAttribute("aria-invalid") === "true")
    ),
    described_by: described,
    error_messages: errorText,
    help_messages: helpText,
    error_feedback_present: errorText.some((item) => item.visible !== false),
    help_present: helpText.some((item) => item.visible !== false),
    suggestion_present: /(enter|choose|select|use|must|should|format|example|at least|try)/.test(serializedErrors)
  };
}
function a11yCollectFormControlStates() {
  return Array.from(document.querySelectorAll("input, select, textarea, button"))
    .slice(0, 150)
    .map((el) => a11yFormControlState(el));
}
function a11yRelatedControl(node) {
  const element = node && node.nodeType === 3 ? node.parentElement : node;
  if (!element || element.nodeType !== 1) {
    return null;
  }
  if (element.matches && element.matches("input, select, textarea, button")) {
    return element;
  }
  const labelled = element.closest ? element.closest("label") : null;
  if (labelled && labelled.htmlFor) {
    const labelledControl = document.getElementById(labelled.htmlFor);
    if (labelledControl) {
      return labelledControl;
    }
  }
  if (element.id) {
    for (const control of Array.from(document.querySelectorAll("[aria-describedby], [aria-errormessage]")).slice(0, 300)) {
      const ids = `${control.getAttribute("aria-describedby") || ""} ${control.getAttribute("aria-errormessage") || ""}`
        .split(/\s+/)
        .filter(Boolean);
      if (ids.includes(element.id)) {
        return control;
      }
    }
  }
  const form = element.closest ? element.closest("form") : null;
  if (form) {
    return form.querySelector('[aria-invalid="true"], input:invalid, select:invalid, textarea:invalid');
  }
  return null;
}
function a11yIsLiveRegion(node) {
  if (!node || node.nodeType !== 1) {
    return false;
  }
  const role = (node.getAttribute("role") || "").toLowerCase();
  const ariaLive = (node.getAttribute("aria-live") || "").toLowerCase();
  return Boolean(ariaLive) || ["status", "alert", "log", "progressbar", "timer"].includes(role);
}
function a11yClosestLiveRegion(node) {
  let current = node && node.nodeType === 3 ? node.parentElement : node;
  while (current && current.nodeType === 1) {
    if (a11yIsLiveRegion(current)) {
      return current;
    }
    current = current.parentElement;
  }
  return null;
}
function a11yInstallMediaListeners(root = document) {
  const media = Array.from(root.querySelectorAll ? root.querySelectorAll("video, audio") : []);
  for (const el of media) {
    const key = el.__a11yMediaKey || `${el.tagName || "media"}:${Math.random().toString(36).slice(2)}`;
    el.__a11yMediaKey = key;
    if (el.__a11yMediaInstrumented) {
      continue;
    }
    el.__a11yMediaInstrumented = true;
    for (const eventName of ["play", "pause", "timeupdate", "seeking", "seeked", "ended"]) {
      el.addEventListener(eventName, () => {
        a11yPush("media", {
          type: eventName,
          timestamp: new Date().toISOString(),
          media_key: key,
          tag: el.tagName ? el.tagName.toLowerCase() : null,
          current_time: Number.isFinite(el.currentTime) ? el.currentTime : null,
          paused: el.paused,
          muted: el.muted,
          volume: el.volume,
          src: el.currentSrc || el.src || null
        });
      }, true);
    }
  }
}
function a11yInstallRuntime() {
  const store = a11yStore();
  if (store.installed) {
    a11yInstallMediaListeners(document);
    return { installed: true, already_installed: true, href: location.href };
  }
  store.installed = true;

  const originalAddEventListener = EventTarget.prototype.addEventListener;
  if (!EventTarget.prototype.__a11yAddEventListenerPatched) {
    EventTarget.prototype.__a11yAddEventListenerPatched = true;
    EventTarget.prototype.addEventListener = function(type, listener, options) {
      try {
        const eventType = String(type || "");
        if (/^(pointer|mouse|touch|drag|drop|click|keydown|keyup|input|change|submit)/.test(eventType)) {
          a11yPush("listenerRegistrations", {
            type: eventType,
            timestamp: new Date().toISOString(),
            target: a11yTarget(this),
            capture: Boolean(options && typeof options === "object" ? options.capture : options)
          });
        }
      } catch (e) {
      }
      return originalAddEventListener.call(this, type, listener, options);
    };
  }

  window.addEventListener("keydown", (event) => {
    const before = a11yTarget(document.activeElement);
    store.lastKeyboardBefore = {
      key: event.key,
      code: event.code,
      active_element_before: before,
      timestamp: new Date().toISOString()
    };
    a11yPush("keyboard", {
      type: "keydown",
      key: event.key,
      code: event.code,
      alt_key: event.altKey,
      ctrl_key: event.ctrlKey,
      shift_key: event.shiftKey,
      meta_key: event.metaKey,
      repeat: event.repeat,
      timestamp: new Date().toISOString(),
      target: a11yTarget(event.target),
      active_element_before: before
    });
  }, true);
  window.addEventListener("keyup", (event) => {
    const after = a11yTarget(document.activeElement);
    const before = store.lastKeyboardBefore && store.lastKeyboardBefore.key === event.key
      ? store.lastKeyboardBefore.active_element_before
      : null;
    a11yPush("keyboard", {
      type: "keyup",
      key: event.key,
      code: event.code,
      alt_key: event.altKey,
      ctrl_key: event.ctrlKey,
      shift_key: event.shiftKey,
      meta_key: event.metaKey,
      repeat: event.repeat,
      timestamp: new Date().toISOString(),
      target: a11yTarget(event.target),
      active_element_before: before,
      active_element_after: after,
      focus_changed: Boolean(before && before.xpath && after && after.xpath && before.xpath !== after.xpath)
    });
  }, true);

  for (const eventName of ["pointerdown", "pointerup", "pointercancel", "pointermove", "mousedown", "mouseup", "click", "touchstart", "touchend", "dragstart", "dragend", "drop"]) {
    window.addEventListener(eventName, (event) => a11yPush("pointer", {
      type: eventName,
      timestamp: new Date().toISOString(),
      pointer_type: event.pointerType || (eventName.startsWith("touch") ? "touch" : "mouse"),
      button: typeof event.button === "number" ? event.button : null,
      buttons: typeof event.buttons === "number" ? event.buttons : null,
      client_x: typeof event.clientX === "number" ? Math.round(event.clientX) : null,
      client_y: typeof event.clientY === "number" ? Math.round(event.clientY) : null,
      target: a11yTarget(event.target)
    }), true);
  }

  window.addEventListener("focus", (event) => a11yPush("focus", {
    type: "focus",
    timestamp: new Date().toISOString(),
    target: a11yTarget(event.target)
  }), true);
  window.addEventListener("blur", (event) => a11yPush("focus", {
    type: "blur",
    timestamp: new Date().toISOString(),
    target: a11yTarget(event.target)
  }), true);
  for (const eventName of ["input", "change", "submit"]) {
    window.addEventListener(eventName, (event) => a11yPush(eventName, {
      type: eventName,
      timestamp: new Date().toISOString(),
      target: a11yTarget(event.target),
      form: a11yTarget(event.target && (event.target.form || (event.target.closest ? event.target.closest("form") : null))),
      submitter: a11yTarget(event.submitter),
      value_length: event.target && typeof event.target.value === "string" ? event.target.value.length : null
    }), true);
  }

  const observer = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      const target = mutation.target;
      const liveRegion = a11yClosestLiveRegion(target);
      const relatedControl = a11yRelatedControl(target);
      const relatedForm = relatedControl && (relatedControl.form || (relatedControl.closest ? relatedControl.closest("form") : null));
      let addedText = "";
      if (mutation.addedNodes && mutation.addedNodes.length) {
        addedText = Array.from(mutation.addedNodes).map((node) => node.textContent || "").join(" ").trim();
      }
      const record = {
        type: mutation.type,
        timestamp: new Date().toISOString(),
        target: a11yTarget(target.nodeType === 1 ? target : target.parentElement),
        attribute_name: mutation.attributeName || null,
        old_value: a11yTruncate(mutation.oldValue),
        new_value: a11yTruncate(mutation.type === "characterData" ? target.data : (mutation.attributeName ? target.getAttribute(mutation.attributeName) : addedText)),
        added_text: a11yTruncate(addedText),
        mutation_type: liveRegion ? "observed_live_region_change" : "observed_dom_mutation",
        live_region: liveRegion ? a11yTarget(liveRegion) : null,
        related_control: relatedControl ? a11yFormControlState(relatedControl) : null,
        related_form: relatedForm ? a11yTarget(relatedForm) : null
      };
      a11yPush("mutations", record);
      if (liveRegion) {
        a11yPush("liveRegionMutations", record);
      }
    }
    a11yInstallMediaListeners(document);
  });
  observer.observe(document.documentElement || document, {
    attributes: true,
    attributeOldValue: true,
    characterData: true,
    characterDataOldValue: true,
    childList: true,
    subtree: true
  });
  store.observer = observer;
  a11yInstallMediaListeners(document);
  return { installed: true, already_installed: false, href: location.href };
}
"""

RUNTIME_INSTRUMENTATION_EVALUATE_SCRIPT = (
    f"() => {{\n{RUNTIME_INSTRUMENTATION_BODY}\nreturn a11yInstallRuntime();\n}}"
)
RUNTIME_INSTRUMENTATION_INIT_SOURCE = f"(() => {{\n{RUNTIME_INSTRUMENTATION_BODY}\na11yInstallRuntime();\n}})();"


def runtime_script(return_body: str) -> str:
    return f"() => {{\n{RUNTIME_INSTRUMENTATION_BODY}\na11yInstallRuntime();\n{return_body}\n}}"


INTERACTION_DRAIN_SCRIPT = runtime_script(
    r"""
const store = a11yStore();
const payload = {
  pointer_events: (store.events.pointer || []).slice(-100),
  focus_events: (store.events.focus || []).slice(-100),
  input_events: (store.events.input || []).slice(-100),
  change_events: (store.events.change || []).slice(-100),
  submit_events: (store.events.submit || []).slice(-100),
  form_control_states: a11yCollectFormControlStates(),
  event_listener_registrations: (store.events.listenerRegistrations || []).slice(-100),
  counters: Object.assign({}, store.counters || {}),
  installed: Boolean(store.installed)
};
store.events.pointer = [];
store.events.focus = [];
store.events.input = [];
store.events.change = [];
store.events.submit = [];
store.events.listenerRegistrations = [];
return payload;
"""
)


class RuntimeInstrumentationExtractor:
    name = "runtime_instrumentation"

    async def install(self, page: "Page") -> dict[str, Any]:
        raw = await page.evaluate(RUNTIME_INSTRUMENTATION_EVALUATE_SCRIPT)
        data = parse_json_value(raw)
        return data if isinstance(data, dict) else {}

    async def extract(self, page: "Page", output_dir: Path, step_number: int) -> dict[str, Any]:
        raw = await page.evaluate(INTERACTION_DRAIN_SCRIPT)
        data = parse_json_value(raw) or {}
        write_json(output_dir / f"runtime_instrumentation_step_{step_number}.json", data)
        return {"runtime_instrumentation": data}
