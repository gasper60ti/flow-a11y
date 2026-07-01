from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from dynamic.runtime_extractors.utils import parse_json_value, write_json

if TYPE_CHECKING:
    from browser_use.actor.page import Page

FOCUS_SCRIPT = """
() => {
  const el = document.activeElement;
  if (!el) {
    return null;
  }
  const attrs = {};
  if (el.attributes) {
    for (const attr of Array.from(el.attributes)) {
      attrs[attr.name] = attr.value;
    }
  }
  const text = (el.textContent || "").trim();
  const style = window.getComputedStyle ? window.getComputedStyle(el) : null;
  const rect = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
  const computed = style ? {
    outline: style.outline,
    outlineWidth: style.outlineWidth,
    outlineStyle: style.outlineStyle,
    outlineColor: style.outlineColor,
    boxShadow: style.boxShadow,
    border: style.border,
    borderWidth: style.borderWidth,
    borderStyle: style.borderStyle,
    borderColor: style.borderColor,
    backgroundColor: style.backgroundColor,
    color: style.color,
    textDecoration: style.textDecoration
  } : {};
  function cssPixels(value) {
    const parsed = Number.parseFloat(String(value || "0"));
    return Number.isFinite(parsed) ? parsed : 0;
  }
  function accessibleName(node) {
    if (!node || node.nodeType !== 1) {
      return null;
    }
    return (
      node.getAttribute("aria-label") ||
      node.getAttribute("alt") ||
      node.getAttribute("title") ||
      node.getAttribute("name") ||
      (node.textContent || "").trim().slice(0, 200) ||
      null
    );
  }
  function xpath(node) {
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
  function visualFocusDeclarations(styleDeclaration) {
    if (!styleDeclaration) {
      return {};
    }
    const names = [
      "outline", "outline-width", "outline-style", "outline-color",
      "box-shadow", "border", "border-width", "border-style", "border-color",
      "background", "background-color", "text-decoration"
    ];
    const declarations = {};
    for (const name of names) {
      const value = styleDeclaration.getPropertyValue ? styleDeclaration.getPropertyValue(name) : "";
      if (value) {
        declarations[name] = value.trim();
      }
    }
    return declarations;
  }
  function hasVisualFocusDeclaration(declarations) {
    function present(name) {
      return String(declarations[name] || "").trim().toLowerCase();
    }
    function nonNone(value) {
      return Boolean(value) && !["none", "initial", "inherit", "unset"].includes(value);
    }
    function nonTransparent(value) {
      return nonNone(value) && !["transparent", "rgba(0, 0, 0, 0)"].includes(value);
    }
    const outline = present("outline");
    const outlineWidth = present("outline-width");
    const outlineStyle = present("outline-style");
    const border = present("border");
    const borderWidth = present("border-width");
    const borderStyle = present("border-style");
    return Boolean(
      (nonNone(outline) && !/^0(?:px)?(?:\\s|$)/.test(outline)) ||
      (cssPixels(outlineWidth) > 0 && nonNone(outlineStyle)) ||
      nonTransparent(present("outline-color")) ||
      nonNone(present("box-shadow")) ||
      (nonNone(border) && !/^0(?:px)?(?:\\s|$)/.test(border)) ||
      (cssPixels(borderWidth) > 0 && nonNone(borderStyle)) ||
      nonTransparent(present("border-color")) ||
      nonTransparent(present("background")) ||
      nonTransparent(present("background-color")) ||
      nonNone(present("text-decoration"))
    );
  }
  function matchingFocusRules(node, requiredPseudo = null) {
    if (!node || !node.matches) {
      return [];
    }
    const matches = [];
    function visit(rules) {
      if (!rules || matches.length >= 20) {
        return;
      }
      for (const rule of Array.from(rules)) {
        if (matches.length >= 20) {
          break;
        }
        if (rule.cssRules) {
          try {
            visit(rule.cssRules);
          } catch (error) {
          }
          continue;
        }
        const selector = String(rule.selectorText || "");
        if (!selector || !selector.includes(":focus")) {
          continue;
        }
        if (requiredPseudo && !selector.includes(requiredPseudo)) {
          continue;
        }
        try {
          if (!node.matches(selector)) {
            continue;
          }
        } catch (error) {
          continue;
        }
        const declarations = visualFocusDeclarations(rule.style);
        if (hasVisualFocusDeclaration(declarations)) {
          matches.push({ selector, declarations });
        }
      }
    }
    for (const sheet of Array.from(document.styleSheets || [])) {
      try {
        visit(sheet.cssRules);
      } catch (error) {
      }
    }
    return matches;
  }
  const outlineVisible = computed.outlineStyle && computed.outlineStyle !== "none" && cssPixels(computed.outlineWidth) > 0;
  const shadowVisible = computed.boxShadow && computed.boxShadow !== "none";
  const borderVisible = (
    cssPixels(computed.borderWidth) > 0 &&
    computed.borderStyle &&
    !["none", "hidden"].includes(computed.borderStyle)
  );
  const focusRuleMatches = matchingFocusRules(el);
  const ancestorFocusWithin = [];
  let ancestor = el.parentElement;
  while (ancestor && ancestorFocusWithin.length < 4) {
    const rules = matchingFocusRules(ancestor, ":focus-within");
    if (rules.length) {
      const ancestorStyle = window.getComputedStyle ? window.getComputedStyle(ancestor) : null;
      ancestorFocusWithin.push({
        tag: ancestor.tagName ? ancestor.tagName.toLowerCase() : null,
        id: ancestor.id || null,
        xpath: xpath(ancestor),
        matched_rules: rules,
        computed_style: ancestorStyle ? {
          outline: ancestorStyle.outline,
          boxShadow: ancestorStyle.boxShadow,
          border: ancestorStyle.border,
          backgroundColor: ancestorStyle.backgroundColor,
          textDecoration: ancestorStyle.textDecoration
        } : {}
      });
    }
    ancestor = ancestor.parentElement;
  }
  const focusVisibleMatch = Boolean(el.matches && el.matches(":focus-visible"));
  const classHint = String(el.className || "").toLowerCase();
  const focusRuleVisual = focusRuleMatches.length > 0;
  const ancestorFocusWithinVisual = ancestorFocusWithin.length > 0;
  const visibleFocusCandidate = Boolean(
    outlineVisible || shadowVisible || focusRuleVisual || ancestorFocusWithinVisual
  );
  return {
    tag: el.tagName ? el.tagName.toLowerCase() : null,
    id: el.id || null,
    class_name: el.className || null,
    name: el.getAttribute("name"),
    role: el.getAttribute("role"),
    aria_label: el.getAttribute("aria-label"),
    text: text ? text.slice(0, 200) : null,
    active_element_name: accessibleName(el),
    active_element_text: text ? text.slice(0, 200) : null,
    attributes: attrs,
    xpath: xpath(el),
    computed_style: computed,
    bounding_client_rect: rect ? {
      x: Math.round(rect.x),
      y: Math.round(rect.y),
      width: Math.round(rect.width),
      height: Math.round(rect.height),
      top: Math.round(rect.top),
      right: Math.round(rect.right),
      bottom: Math.round(rect.bottom),
      left: Math.round(rect.left)
    } : null,
    viewport: {
      width: window.innerWidth,
      height: window.innerHeight,
      deviceScaleFactor: window.devicePixelRatio || 1
    },
    visible_focus_candidate: visibleFocusCandidate,
    focus_visible_match: focusVisibleMatch,
    focus_css_rules: focusRuleMatches,
    ancestor_focus_within: ancestorFocusWithin,
    visible_focus_signals: {
      outline: Boolean(outlineVisible),
      boxShadow: Boolean(shadowVisible),
      border: Boolean(borderVisible),
      focusRule: Boolean(focusRuleVisual),
      ancestorFocusWithin: Boolean(ancestorFocusWithinVisual),
      classHint: Boolean(classHint.includes("focus") || classHint.includes("active") || classHint.includes("selected"))
    }
  };
}
"""


class FocusRuntimeExtractor:
    name = "focus"

    async def extract(self, page: "Page", output_dir: Path, step_number: int) -> dict[str, Any]:
        raw = await page.evaluate(FOCUS_SCRIPT)
        data = parse_json_value(raw)
        write_json(output_dir / f"focus_state_step_{step_number}.json", data or {})
        return {"focused_element": data}
