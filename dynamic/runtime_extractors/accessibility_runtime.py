from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from dynamic.runtime_extractors.utils import write_json

try:
    from browser_use import DomService
except (ImportError, ModuleNotFoundError):
    DomService = None

if TYPE_CHECKING:
    from browser_use.browser import BrowserSession
    from browser_use.dom.views import EnhancedDOMTreeNode
    from browser_use.actor.page import Page


class AccessibilityRuntimeExtractor:
    name = "accessibility_tree"

    def __init__(self, max_nodes: int = 2500, max_depth: int = 12) -> None:
        self.max_nodes = max_nodes
        self.max_depth = max_depth

    async def extract(
        self,
        browser_session: "BrowserSession",
        page: "Page" | None,
        output_dir: Path,
        step_number: int,
    ) -> dict[str, Any]:
        timing_info: dict[str, float] = {}
        try:
            if DomService is None:
                raise ImportError("browser_use is required for DomService extraction")
            async with DomService(browser_session) as service:
                _, enhanced_tree, timing_info = await service.get_serialized_dom_tree()
        except Exception:
            if page is None:
                raise
            title = await page.evaluate("() => document.title || ''")
            tree = {
                "role": {"value": "document"},
                "name": {"value": title},
                "children": [],
            }
            payload = {"tree": tree, "node_count": 1, "timing": timing_info}
            write_json(output_dir / f"accessibility_tree_step_{step_number}.json", payload)
            return {"accessibility_tree": tree, "ax_timing": timing_info}

        counter = {"count": 0}
        tree = self._build_ax_tree(enhanced_tree, counter, 0)
        if tree is None:
            tree = {
                "role": {"value": "document"},
                "name": {"value": None},
                "children": [],
            }

        payload = {
            "tree": tree,
            "node_count": counter["count"],
            "timing": timing_info,
        }
        write_json(output_dir / f"accessibility_tree_step_{step_number}.json", payload)
        return {"accessibility_tree": tree, "ax_timing": timing_info}

    def _build_ax_tree(
        self,
        node: "EnhancedDOMTreeNode",
        counter: dict[str, int],
        depth: int,
    ) -> dict[str, Any] | None:
        if depth > self.max_depth or counter["count"] >= self.max_nodes:
            return None

        children: list[dict[str, Any]] = []
        for child in node.children_nodes or []:
            child_tree = self._build_ax_tree(child, counter, depth + 1)
            if child_tree:
                children.append(child_tree)
        for shadow_root in node.shadow_roots or []:
            shadow_tree = self._build_ax_tree(shadow_root, counter, depth + 1)
            if shadow_tree:
                children.append(shadow_tree)
        if node.content_document:
            content_tree = self._build_ax_tree(node.content_document, counter, depth + 1)
            if content_tree:
                children.append(content_tree)

        ax_node = node.ax_node
        if ax_node and not ax_node.ignored:
            counter["count"] += 1
            return {
                "role": {"value": ax_node.role},
                "name": {"value": ax_node.name},
                "children": children,
            }

        if children:
            return {
                "role": {"value": "generic"},
                "name": {"value": None},
                "children": children,
            }
        return None
