from operator_use.web.dom.views import (
    DOMElementNode,
    DOMTextualNode,
    ScrollElementNode,
    DOMState,
    CenterCord,
    BoundingBox,
)
from typing import TYPE_CHECKING
from asyncio import sleep
from time import perf_counter
import asyncio
import json
import logging

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from operator_use.web.browser import Browser


COMPUTED_STYLES = ["display", "visibility", "opacity", "cursor", "overflow-y", "position"]
_D, _V, _O, _C, _OY, _P = range(6)

INTERACTIVE_ROLES = frozenset(
    {
        "button",
        "link",
        "checkbox",
        "radio",
        "textbox",
        "combobox",
        "listbox",
        "menuitem",
        "menuitemcheckbox",
        "menuitemradio",
        "option",
        "tab",
        "treeitem",
        "slider",
        "spinbutton",
        "searchbox",
        "switch",
        "gridcell",
        "columnheader",
        "rowheader",
        "tooltip",
        "tree",
        "tabpanel",
        "progressbar",
        "scrollbar",
    }
)

INTERACTIVE_TAGS = frozenset(
    {
        "a",
        "button",
        "input",
        "select",
        "textarea",
        "option",
        "summary",
        "menu",
        "menuitem",
        "embed",
        "canvas",
        "object",
    }
)

INFORMATIVE_ROLES = frozenset(
    {
        "heading",
        "article",
        "note",
        "paragraph",
        "status",
        "alert",
        "log",
        "term",
        "definition",
        "region",
        "tooltip",
        "text",
        "contentinfo",
        "presentation",
    }
)

INFORMATIVE_TAGS = frozenset(
    {
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "p",
        "label",
        "code",
        "pre",
        "th",
        "td",
        "article",
        "dl",
        "dt",
        "dd",
        "img",
        "table",
    }
)

EXCLUDED_TAGS = frozenset(
    {
        "style",
        "script",
        "noscript",
        "link",
        "meta",
        "head",
        "br",
        "hr",
    }
)

SAFE_ATTRIBUTES = frozenset(
    {
        # Identity
        "id",
        "name",
        "role",
        "type",
        # Content / labels
        "value",
        "placeholder",
        "alt",
        "title",
        "aria-label",
        "aria-placeholder",
        "aria-autocomplete",
        # State (is it checked? expanded? disabled?)
        "checked",
        "selected",
        "expanded",
        "pressed",
        "disabled",
        "required",
        "invalid",
        "aria-checked",
        "aria-selected",
        "aria-expanded",
        "aria-pressed",
        "aria-disabled",
        "aria-hidden",
        "data-state",
        # Input constraints (help agent avoid wrong formats)
        "pattern",
        "min",
        "max",
        "minlength",
        "maxlength",
        "step",
        "accept",
        "multiple",
        "inputmode",
        "autocomplete",
        # Range / slider values
        "aria-valuemin",
        "aria-valuemax",
        "aria-valuenow",
        # Date / time format hints
        "data-date-format",
        "data-datepicker",
        # Rich text / popup hints
        "contenteditable",
        "haspopup",
        "multiselectable",
        # Test / automation hooks
        "data-testid",
        # Clickability signals
        "onclick",
        "href",
        "tabindex",
        "data-tooltip",
        "data-id",
        "data-qa",
        "data-cy",
    }
)

_MARK_PAGE_JS = """(function(boxes){
    var ls=window.__wu_labels__=window.__wu_labels__||[];
    boxes.forEach(function(b,i){
        var c='#'+Math.floor(Math.random()*16777215).toString(16).padStart(6,'0');
        var d=document.createElement('div');
        d.style.cssText='position:fixed;left:'+b.left+'px;top:'+b.top+'px;width:'+b.width+'px;height:'+b.height+'px;outline:2px solid '+c+';pointer-events:none;z-index:9999;';
        var s=document.createElement('span');
        s.textContent=i;
        s.style.cssText='position:absolute;top:-19px;right:0;background:'+c+';color:white;padding:2px 4px;font-size:12px;border-radius:2px;';
        d.appendChild(s);document.body.appendChild(d);ls.push(d);
    });
})(BOXES)"""

_UNMARK_PAGE_JS = """(function(){
    (window.__wu_labels__||[]).forEach(function(el){if(el.parentNode)el.parentNode.removeChild(el);});
    window.__wu_labels__=[];
})()"""

# Batch coverage check: walk up from elementFromPoint to see if our element
# (matched by tag + bounding-box top-left) is in the hit path.
_CHECK_COVERAGE_JS = """(function(els){
    return els.map(function(e){
        var top=document.elementFromPoint(e.cx,e.cy);
        if(!top) return false;
        var cur=top;
        while(cur){
            if(cur.tagName&&cur.tagName.toLowerCase()===e.tag){
                var r=cur.getBoundingClientRect();
                if(Math.abs(Math.round(r.left)-e.left)<=4&&Math.abs(Math.round(r.top)-e.top)<=4)
                    return true;
            }
            cur=cur.parentElement;
        }
        return false;
    });
})(ELEMENTS)"""


class DOM:
    def __init__(self, session: "Browser"):
        self.session = session

    async def get_state(self, use_vision: bool = False) -> tuple[bytes | None, DOMState]:
        start_time = perf_counter()
        try:
            await self.session._wait_for_page(timeout=10.0)
            sid = self.session._get_current_session_id()

            snapshot, ax_result, viewport, dpr = await asyncio.gather(
                self.session.send(
                    "DOMSnapshot.captureSnapshot",
                    {
                        "computedStyles": COMPUTED_STYLES,
                        "includePaintOrder": True,
                        "includeDOMRects": True,
                    },
                    session_id=sid,
                ),
                self.session.send("Accessibility.getFullAXTree", {}, session_id=sid),
                self.session.get_viewport(),
                self.session.execute_script("window.devicePixelRatio || 1"),
            )

            dpr = float(dpr or 1.0)
            interactive, informative, scrollable = self._parse(snapshot, ax_result, viewport, dpr)

            # Coverage check: remove elements hidden behind other elements
            if interactive:
                payload = [
                    {
                        "tag": n.tag,
                        "cx": n.center.x,
                        "cy": n.center.y,
                        "left": n.bounding_box.left,
                        "top": n.bounding_box.top,
                    }
                    for n in interactive
                ]
                try:
                    visible = await self.session.execute_script(
                        _CHECK_COVERAGE_JS.replace("ELEMENTS", json.dumps(payload))
                    )
                    if isinstance(visible, list) and len(visible) == len(interactive):
                        interactive = [n for n, v in zip(interactive, visible) if v]
                except Exception:
                    pass  # keep all if JS fails

            screenshot = None
            if use_vision and interactive:
                boxes = [n.bounding_box.to_dict() for n in interactive]
                await self.session.execute_script(_MARK_PAGE_JS.replace("BOXES", json.dumps(boxes)))
                await sleep(0.1)
                screenshot = await self.session.get_screenshot()
                await self.session.execute_script(_UNMARK_PAGE_JS)

        except Exception as e:
            logger.debug("Failed to get DOM state: %s", e)
            interactive, informative, scrollable, screenshot = [], [], [], None

        logger.info("[DOM] DOM State capture took %.2f seconds", perf_counter() - start_time)

        selector_map = dict(enumerate(interactive + scrollable))
        return screenshot, DOMState(
            interactive_nodes=interactive,
            informative_nodes=informative,
            scrollable_nodes=scrollable,
            selector_map=selector_map,
        )

    def _parse(
        self,
        snapshot: dict,
        ax_result: dict,
        viewport: tuple[int, int],
        dpr: float,
    ) -> tuple[list, list, list]:
        strings = snapshot.get("strings", [])
        docs = snapshot.get("documents", [])
        if not docs:
            return [], [], []

        doc = docs[0]
        nodes = doc.get("nodes", {})
        layout = doc.get("layout", {})
        vw, vh = viewport

        def s(idx: int) -> str:
            return strings[idx] if isinstance(idx, int) and 0 <= idx < len(strings) else ""

        # -- Node arrays --
        node_names = nodes.get("nodeName", [])
        node_types = nodes.get("nodeType", [])
        node_parent = nodes.get("parentIndex", [])
        node_backend = nodes.get("backendNodeId", [])
        node_attrs = nodes.get("attributes", [])
        node_values = nodes.get("nodeValue", [])
        clickable_set = set(nodes.get("isClickable", {}).get("index", []))

        # -- Layout arrays --
        layout_nodes = layout.get("nodeIndex", [])
        layout_bounds_raw = layout.get("bounds", [])
        layout_styles_raw = layout.get("styles", [])

        # DOM node index -> layout index
        node_to_layout = {ni: li for li, ni in enumerate(layout_nodes)}

        # innerText map: element node index -> concatenated text from direct text-node children
        element_text: dict[int, str] = {}
        for i, parent_idx in enumerate(node_parent):
            if i < len(node_types) and node_types[i] == 3 and parent_idx >= 0:
                val_idx = node_values[i] if i < len(node_values) else -1
                text = s(val_idx).strip() if val_idx >= 0 else ""
                if text:
                    element_text[parent_idx] = (
                        element_text.get(parent_idx, "") + " " + text
                    ).strip()

        def get_bounds(li: int):
            if li >= len(layout_bounds_raw):
                return None
            rect = layout_bounds_raw[li]
            if not rect or len(rect) < 4:
                return None
            return (rect[0] / dpr, rect[1] / dpr, rect[2] / dpr, rect[3] / dpr)

        def get_style(li: int, si: int) -> str:
            row = layout_styles_raw[li] if li < len(layout_styles_raw) else []
            return s(row[si]) if si < len(row) else ""

        # -- Accessibility map: backendNodeId -> {role, name, props} --
        ax_map: dict[int, dict] = {}
        for ax_node in ax_result.get("nodes", []):
            if ax_node.get("ignored"):
                continue
            bid = ax_node.get("backendDOMNodeId")
            if not bid:
                continue
            ax_map[bid] = {
                "role": ax_node.get("role", {}).get("value", ""),
                "name": ax_node.get("name", {}).get("value", ""),
                "props": {
                    p["name"]: p.get("value", {}).get("value")
                    for p in ax_node.get("properties", [])
                },
            }

        # -- Pre-compute sibling positions for O(n) XPath construction --
        sibling_pos: dict[int, int] = {}
        tag_count: dict[tuple[int, str], int] = {}
        for i, p in enumerate(node_parent):
            if p < 0:
                continue
            tag_i = s(node_names[i]).lower() if i < len(node_names) else ""
            if not tag_i or tag_i.startswith("#"):
                continue
            key = (p, tag_i)
            tag_count[key] = tag_count.get(key, 0) + 1
            sibling_pos[i] = tag_count[key]

        def build_xpath(ni: int) -> str:
            parts = []
            cur = ni
            while 0 <= cur < len(node_names):
                tag = s(node_names[cur]).lower()
                if not tag or tag.startswith("#"):
                    break
                par = node_parent[cur] if cur < len(node_parent) else -1
                pos = sibling_pos.get(cur, 1)
                parts.insert(0, f"{tag}[{pos}]")
                if par < 0:
                    break
                cur = par
            return "/" + "/".join(parts) if parts else ""

        interactive: list[DOMElementNode] = []
        informative: list[DOMTextualNode] = []
        scrollable: list[ScrollElementNode] = []
        # node index -> name for every interactive element added so far
        interactive_name_by_ni: dict[int, str] = {}

        for ni in range(len(node_names)):
            # Element nodes only (nodeType 1)
            if ni < len(node_types) and node_types[ni] != 1:
                continue

            tag = s(node_names[ni]).lower()
            if not tag or tag in EXCLUDED_TAGS or tag.startswith("#"):
                continue

            # Must have layout (excludes display:none etc.)
            li = node_to_layout.get(ni)
            if li is None:
                continue

            bounds = get_bounds(li)
            if bounds is None:
                continue
            x, y, w, h = bounds
            if w < 10 or h < 10:
                continue

            # Computed style visibility checks
            if get_style(li, _D) == "none":
                continue
            if get_style(li, _V) == "hidden":
                continue
            try:
                if float(get_style(li, _O) or "1") <= 0:
                    continue
            except ValueError:
                pass

            # Viewport check — fixed/sticky elements are always on-screen
            position = get_style(li, _P)
            if position not in ("fixed", "sticky"):
                if y + h < -200 or y > vh + 200 or x + w < -200 or x > vw + 200:
                    continue

            # AX info
            bid = node_backend[ni] if ni < len(node_backend) else None
            ax = ax_map.get(bid, {}) if bid else {}
            ax_role = ax.get("role", "")
            ax_name = ax.get("name", "")
            ax_props = ax.get("props", {})

            # Attributes (safe subset only)
            raw_attrs = node_attrs[ni] if ni < len(node_attrs) else []
            attrs: dict[str, str] = {}
            for j in range(0, len(raw_attrs) - 1, 2):
                k = s(raw_attrs[j])
                if k in SAFE_ATTRIBUTES:
                    attrs[k] = s(raw_attrs[j + 1])

            cursor = get_style(li, _C)
            overflow_y = get_style(li, _OY)
            cx = round(x + w / 2)
            cy = round(y + h / 2)

            is_interactive = (
                tag in INTERACTIVE_TAGS
                or ax_role in INTERACTIVE_ROLES
                or cursor == "pointer"
                or ni in clickable_set
                or ax_props.get("focusable") is True
                or bool(ax_props.get("editable"))
                or "onclick" in attrs
                or "href" in attrs
                or attrs.get("contenteditable") in ("true", "", "plaintext-only")
                or (attrs.get("tabindex", "-1") not in ("-1", ""))
            )

            is_scrollable = overflow_y in ("auto", "scroll", "overlay") and h >= vh * 0.4

            xpath = build_xpath(ni)
            inner_text = element_text.get(ni, "")
            name = (
                ax_name
                or attrs.get("aria-label")
                or attrs.get("title")
                or attrs.get("placeholder")
                or attrs.get("name")
                or inner_text
                or ""
            )
            role = ax_role or attrs.get("role") or tag

            # Discard elements hidden from assistive technology
            if attrs.get("aria-hidden") == "true":
                continue

            if not name:
                continue

            if is_interactive:
                # Discard child if an interactive ancestor already has the same name
                dominated = False
                cur = node_parent[ni] if ni < len(node_parent) else -1
                while cur >= 0:
                    if interactive_name_by_ni.get(cur) == name:
                        dominated = True
                        break
                    cur = node_parent[cur] if cur < len(node_parent) else -1
                if dominated:
                    continue

                interactive_name_by_ni[ni] = name
                interactive.append(
                    DOMElementNode(
                        tag=tag,
                        role=role,
                        name=name,
                        attributes=attrs,
                        center=CenterCord(x=cx, y=cy),
                        bounding_box=BoundingBox(
                            left=round(x), top=round(y), width=round(w), height=round(h)
                        ),
                        xpath={"frame": "", "element": xpath},
                        viewport=(vw, vh),
                    )
                )
            elif is_scrollable:
                scrollable.append(
                    ScrollElementNode(
                        tag=tag,
                        role=role,
                        name=name,
                        attributes=attrs,
                        xpath={"frame": "", "element": xpath},
                        viewport=(vw, vh),
                    )
                )
            else:
                if (tag in INFORMATIVE_TAGS or ax_role in INFORMATIVE_ROLES) and (
                    ax_name or inner_text
                ):
                    informative.append(
                        DOMTextualNode(
                            tag=tag,
                            role=ax_role,
                            content=ax_name or inner_text,
                            center=CenterCord(x=cx, y=cy),
                            xpath={"frame": "", "element": xpath},
                            viewport=(vw, vh),
                        )
                    )

        return interactive, informative, scrollable
