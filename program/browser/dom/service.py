from program.browser.dom.views import DOMNode, DOMState, CenterCord, BoundingBox
from typing import TYPE_CHECKING
from asyncio import sleep
import asyncio
import json
import time

if TYPE_CHECKING:
    from program.browser.client import Browser


COMPUTED_STYLES = ['display', 'visibility', 'opacity', 'cursor', 'overflow-y', 'position']
_D, _V, _O, _C, _OY, _P = range(6)

INTERACTIVE_ROLES = frozenset({
    'button', 'link', 'checkbox', 'radio', 'textbox', 'combobox', 'listbox',
    'menuitem', 'menuitemcheckbox', 'menuitemradio', 'option', 'tab', 'treeitem',
    'slider', 'spinbutton', 'searchbox', 'switch', 'gridcell',
    'columnheader', 'rowheader',
    'tooltip', 'tree', 'tabpanel', 'progressbar', 'scrollbar',
})

INTERACTIVE_TAGS = frozenset({
    'a', 'button', 'input', 'select', 'textarea', 'option',
    'summary', 'menu', 'menuitem',
    'embed', 'canvas', 'object',
})

INFORMATIVE_ROLES = frozenset({
    'heading', 'article', 'note', 'paragraph', 'status',
    'alert', 'log', 'term', 'definition', 'region',
    'tooltip', 'text', 'contentinfo', 'presentation',
})

INFORMATIVE_TAGS = frozenset({
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'label',
    'code', 'pre', 'th', 'td', 'article',
    'dl', 'dt', 'dd', 'img', 'table',
    'li', 'figcaption', 'caption', 'blockquote', 'legend',
})

INLINE_ELEMENTS = frozenset({
    'span', 'em', 'strong', 'b', 'i', 'small', 'abbr', 'code',
    'mark', 'sub', 'sup', 'cite', 'q', 'u', 's', 'del', 'ins',
    'time', 'kbd', 'var', 'samp', 'a',
})

EXCLUDED_TAGS = frozenset({
    'style', 'script', 'noscript', 'link', 'meta',
    'head', 'br', 'hr',
})

STRUCTURAL_CONTAINER_TAGS = frozenset({
    'nav', 'header', 'footer', 'main', 'section', 'article',
    'form', 'ul', 'ol', 'aside', 'dialog', 'fieldset', 'details',
    'thead', 'tbody', 'tfoot', 'tr',
})

SAFE_ATTRIBUTES = frozenset({
    # Identity
    'id', 'name', 'role', 'type',
    # Content / labels
    'value', 'placeholder', 'alt', 'title',
    'aria-label', 'aria-placeholder', 'aria-autocomplete',
    # State (is it checked? expanded? disabled?)
    'checked', 'selected', 'expanded', 'pressed',
    'disabled', 'required', 'invalid',
    'aria-checked', 'aria-selected', 'aria-expanded',
    'aria-pressed', 'aria-disabled', 'aria-hidden',
    'data-state',
    # Input constraints (help agent avoid wrong formats)
    'pattern', 'min', 'max', 'minlength', 'maxlength',
    'step', 'accept', 'multiple', 'inputmode', 'autocomplete',
    # Range / slider values
    'aria-valuemin', 'aria-valuemax', 'aria-valuenow',
    # Date / time format hints
    'data-date-format', 'data-datepicker',
    # Rich text / popup hints
    'contenteditable', 'haspopup', 'multiselectable',
    # Test / automation hooks
    'data-testid',
    # Clickability signals
    'onclick', 'href', 'tabindex',
    'data-tooltip', 'data-id', 'data-qa', 'data-cy',
    # Identity / styling hooks
    'class',
})

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
    def __init__(self, session: 'Browser'):
        self.session = session

    async def get_state(self, use_vision: bool = False, within_viewport: bool = True) -> tuple[bytes | None, DOMState]:
        try:
            await self.session._wait_for_page(timeout=10.0)
            sid = self.session._get_current_session_id()

            t0 = time.perf_counter()

            snapshot, ax_result, viewport, dpr, scroll_pos = await asyncio.gather(
                self.session.send('DOMSnapshot.captureSnapshot', {
                    'computedStyles': COMPUTED_STYLES,
                    'includePaintOrder': True,
                    'includeDOMRects': True,
                }, session_id=sid),
                self.session.send('Accessibility.getFullAXTree', {}, session_id=sid),
                self.session.get_viewport(),
                self.session.execute_script('window.devicePixelRatio || 1'),
                self.session.get_scroll_position(),
            )

            dpr = float(dpr or 1.0)
            scroll_x = float(scroll_pos.get('scrollX', 0)) if scroll_pos else 0
            scroll_y = float(scroll_pos.get('scrollY', 0)) if scroll_pos else 0
            interactive, informative, scrollable, tree_root = self._parse(snapshot, ax_result, viewport, dpr, scroll_x, scroll_y, within_viewport=within_viewport)

            # Coverage check only makes sense for elements currently in the viewport
            if within_viewport and interactive:
                payload = [
                    {'tag': n.tag,
                     'cx': n.center.x - scroll_x, 'cy': n.center.y - scroll_y,
                     'left': n.bounding_box.left - round(scroll_x), 'top': n.bounding_box.top - round(scroll_y)}
                    for n in interactive
                ]
                try:
                    visible = await self.session.execute_script(
                        _CHECK_COVERAGE_JS.replace('ELEMENTS', json.dumps(payload))
                    )
                    if isinstance(visible, list) and len(visible) == len(interactive):
                        interactive = [n for n, v in zip(interactive, visible) if v]
                except Exception:
                    pass  # keep all if JS fails

            state_capture_ms = (time.perf_counter() - t0) * 1000

            screenshot = None
            screenshot_capture_ms = 0.0
            if use_vision and interactive:
                t1 = time.perf_counter()
                # position:fixed in the mark JS is viewport-relative, so subtract scroll offset
                boxes = [
                    {'left': n.bounding_box.left - round(scroll_x),
                     'top': n.bounding_box.top - round(scroll_y),
                     'width': n.bounding_box.width, 'height': n.bounding_box.height}
                    for n in interactive
                ]
                await self.session.execute_script(_MARK_PAGE_JS.replace('BOXES', json.dumps(boxes)))
                await sleep(0.1)
                screenshot = await self.session.get_screenshot()
                await self.session.execute_script(_UNMARK_PAGE_JS)
                screenshot_capture_ms = (time.perf_counter() - t1) * 1000

            print(
                f'DOM state: state_capture_ms={state_capture_ms:.1f} '
                f'screenshot_capture_ms={screenshot_capture_ms:.1f} '
                f'total_ms={state_capture_ms + screenshot_capture_ms:.1f} '
                f'interactive={len(interactive)} scrollable={len(scrollable)} use_vision={use_vision}'
            )

        except Exception as e:
            print(f'Failed to get DOM state: {e}')
            interactive, informative, scrollable, screenshot, tree_root = [], [], [], None, None

        selector_map = dict(enumerate(interactive + scrollable))
        return screenshot, DOMState(
            interactive_nodes=interactive,
            informative_nodes=informative,
            scrollable_nodes=scrollable,
            selector_map=selector_map,
            semantic_tree_root=tree_root,
        )

    def _parse(
        self,
        snapshot: dict,
        ax_result: dict,
        viewport: tuple[int, int],
        dpr: float,
        scroll_x: float = 0,
        scroll_y: float = 0,
        within_viewport: bool = True,
    ) -> tuple[list, list, list, DOMNode | None]:
        strings = snapshot.get('strings', [])
        docs    = snapshot.get('documents', [])
        if not docs:
            return [], [], [], None

        doc    = docs[0]
        nodes  = doc.get('nodes', {})
        layout = doc.get('layout', {})
        vw, vh = viewport

        def s(idx: int) -> str:
            return strings[idx] if isinstance(idx, int) and 0 <= idx < len(strings) else ''

        # -- Node arrays --
        node_names   = nodes.get('nodeName', [])
        node_types   = nodes.get('nodeType', [])
        node_parent  = nodes.get('parentIndex', [])
        node_backend = nodes.get('backendNodeId', [])
        node_attrs   = nodes.get('attributes', [])
        node_values  = nodes.get('nodeValue', [])
        clickable_set = set(nodes.get('isClickable', {}).get('index', []))

        # -- Layout arrays --
        layout_nodes      = layout.get('nodeIndex', [])
        layout_bounds_raw = layout.get('bounds', [])
        layout_styles_raw = layout.get('styles', [])

        # DOM node index -> layout index
        node_to_layout = {ni: li for li, ni in enumerate(layout_nodes)}

        # children_map: element index -> ordered list of child indices (document order)
        children_map: dict[int, list[int]] = {}
        for i, p in enumerate(node_parent):
            if p >= 0:
                children_map.setdefault(p, []).append(i)

        # inline_text: text content of an element including nested inline children,
        # in document order. Descends into INLINE_ELEMENTS only — block children are
        # excluded so containers don't absorb their children's text.
        _it_cache: dict[int, str] = {}

        def inline_text(ni: int) -> str:
            if ni in _it_cache:
                return _it_cache[ni]
            parts: list[str] = []
            for c in children_map.get(ni, []):
                if c >= len(node_types):
                    continue
                if node_types[c] == 3:  # text node
                    idx = node_values[c] if c < len(node_values) else -1
                    t = s(idx).strip() if idx >= 0 else ''
                    if t:
                        parts.append(t)
                elif node_types[c] == 1:
                    ctag = s(node_names[c]).lower() if c < len(node_names) else ''
                    if ctag in INLINE_ELEMENTS:
                        t = inline_text(c)
                        if t:
                            parts.append(t)
            result = ' '.join(parts)
            _it_cache[ni] = result
            return result

        element_text: dict[int, str] = {
            ni: t for ni in range(len(node_names))
            if node_types[ni] == 1 and (t := inline_text(ni))
        }

        def get_bounds(li: int):
            if li >= len(layout_bounds_raw):
                return None
            rect = layout_bounds_raw[li]
            if not rect or len(rect) < 4:
                return None
            return (rect[0] / dpr, rect[1] / dpr, rect[2] / dpr, rect[3] / dpr)

        def get_style(li: int, si: int) -> str:
            row = layout_styles_raw[li] if li < len(layout_styles_raw) else []
            return s(row[si]) if si < len(row) else ''

        # -- Accessibility map: backendNodeId -> {role, name, props} --
        ax_map: dict[int, dict] = {}
        for ax_node in ax_result.get('nodes', []):
            if ax_node.get('ignored'):
                continue
            bid = ax_node.get('backendDOMNodeId')
            if not bid:
                continue
            ax_map[bid] = {
                'role':  ax_node.get('role', {}).get('value', ''),
                'name':  ax_node.get('name', {}).get('value', ''),
                'props': {p['name']: p.get('value', {}).get('value')
                          for p in ax_node.get('properties', [])},
            }

        # -- Pre-compute sibling position index for O(1) XPath construction --
        # sibling_pos[ni] = 1-based position among same-tag siblings under the same parent
        sibling_pos: dict[int, int] = {}
        tag_count: dict[tuple[int, str], int] = {}  # (parent_idx, tag) -> count so far
        for i, p in enumerate(node_parent):
            if p < 0:
                continue
            tag_i = s(node_names[i]).lower() if i < len(node_names) else ''
            if not tag_i or tag_i.startswith('#'):
                continue
            key = (p, tag_i)
            tag_count[key] = tag_count.get(key, 0) + 1
            sibling_pos[i] = tag_count[key]

        def build_xpath(ni: int) -> str:
            parts = []
            cur = ni
            while 0 <= cur < len(node_names):
                tag = s(node_names[cur]).lower()
                if not tag or tag.startswith('#'):
                    break
                pos = sibling_pos.get(cur, 1)
                parts.insert(0, f'{tag}[{pos}]')
                par = node_parent[cur] if cur < len(node_parent) else -1
                if par < 0:
                    break
                cur = par
            return '/' + '/'.join(parts) if parts else ''

        interactive: list[DOMNode] = []
        informative: list[DOMNode] = []
        scrollable:  list[DOMNode] = []
        # node index -> name for every interactive element added so far
        interactive_name_by_ni: dict[int, str] = {}
        # node index -> DOMNode for tree construction
        ni_to_domnode: dict[int, DOMNode] = {}

        for ni in range(len(node_names)):
            # Element nodes only (nodeType 1)
            if ni < len(node_types) and node_types[ni] != 1:
                continue

            tag = s(node_names[ni]).lower()
            if not tag or tag in EXCLUDED_TAGS or tag.startswith('#'):
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
            if get_style(li, _D) == 'none':
                continue
            if get_style(li, _V) == 'hidden':
                continue
            try:
                if float(get_style(li, _O) or '1') <= 0:
                    continue
            except ValueError:
                pass

            # Viewport check — skip when caller wants the full DOM
            if within_viewport:
                position = get_style(li, _P)
                if position not in ('fixed', 'sticky'):
                    viewport_y = y - scroll_y
                    viewport_x = x - scroll_x
                    if viewport_y + h < -200 or viewport_y > vh + 200 or viewport_x + w < -200 or viewport_x > vw + 200:
                        continue

            # AX info
            bid     = node_backend[ni] if ni < len(node_backend) else None
            ax      = ax_map.get(bid, {}) if bid else {}
            ax_role  = ax.get('role', '')
            ax_name  = ax.get('name', '')
            ax_props = ax.get('props', {})

            # Attributes (safe subset only)
            raw_attrs = node_attrs[ni] if ni < len(node_attrs) else []
            attrs: dict[str, str] = {}
            for j in range(0, len(raw_attrs) - 1, 2):
                k = s(raw_attrs[j])
                if k in SAFE_ATTRIBUTES:
                    attrs[k] = s(raw_attrs[j + 1])

            cursor     = get_style(li, _C)
            overflow_y = get_style(li, _OY)
            cx = round(x + w / 2)
            cy = round(y + h / 2)

            is_interactive = (
                tag in INTERACTIVE_TAGS
                or ax_role in INTERACTIVE_ROLES
                or cursor == 'pointer'
                or ni in clickable_set
                or ax_props.get('focusable') is True
                or bool(ax_props.get('editable'))
                or 'onclick' in attrs
                or 'href' in attrs
                or attrs.get('contenteditable') in ('true', '', 'plaintext-only')
                or (attrs.get('tabindex', '-1') not in ('-1', ''))
            )

            is_scrollable = overflow_y in ('auto', 'scroll', 'overlay') and h >= vh * 0.4

            xpath = build_xpath(ni)
            inner_text = element_text.get(ni, '')
            name  = ax_name or attrs.get('aria-label') or attrs.get('title') or attrs.get('placeholder') or attrs.get('name') or inner_text or ''
            role  = ax_role or attrs.get('role') or tag

            # Discard elements hidden from assistive technology
            if attrs.get('aria-hidden') == 'true':
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
                node = DOMNode(
                    tag=tag, role=role, element_type='interactive',
                    name=name,
                    interactive_id=len(interactive),
                    href=attrs.get('href'),
                    attributes=attrs,
                    center=CenterCord(x=cx, y=cy),
                    bounding_box=BoundingBox(left=round(x), top=round(y), width=round(w), height=round(h)),
                    xpath={'frame': '', 'element': xpath},
                    viewport=(vw, vh),
                )
                interactive.append(node)
                ni_to_domnode[ni] = node
            elif is_scrollable:
                node = DOMNode(
                    tag=tag, role=role, element_type='scrollable',
                    name=name,
                    attributes=attrs,
                    xpath={'frame': '', 'element': xpath},
                    viewport=(vw, vh),
                )
                scrollable.append(node)
                ni_to_domnode[ni] = node
            else:
                if (tag in INFORMATIVE_TAGS or ax_role in INFORMATIVE_ROLES) and (ax_name or inner_text):
                    node = DOMNode(
                        tag=tag, role=ax_role, element_type='informative',
                        content=ax_name or inner_text,
                        attributes=attrs,
                        center=CenterCord(x=cx, y=cy),
                        xpath={'frame': '', 'element': xpath},
                        viewport=(vw, vh),
                    )
                    informative.append(node)
                    ni_to_domnode[ni] = node

        # Build semantic tree from actual DOM parent-child relationships
        tree_nodes: dict[int, DOMNode] = dict(ni_to_domnode)

        # Walk ancestors of each collected node; add structural containers on demand
        for ni in list(ni_to_domnode.keys()):
            cur = node_parent[ni] if ni < len(node_parent) else -1
            while cur >= 0:
                if cur in tree_nodes:
                    break
                cur_tag = s(node_names[cur]).lower() if cur < len(node_names) else ''
                if cur_tag in STRUCTURAL_CONTAINER_TAGS:
                    cur_bid = node_backend[cur] if cur < len(node_backend) else None
                    cur_ax = ax_map.get(cur_bid, {}) if cur_bid else {}
                    cur_name = cur_ax.get('name') or element_text.get(cur) or None
                    cur_raw = node_attrs[cur] if cur < len(node_attrs) else []
                    cur_attrs: dict[str, str] = {}
                    for j in range(0, len(cur_raw) - 1, 2):
                        k = s(cur_raw[j])
                        if k in SAFE_ATTRIBUTES:
                            cur_attrs[k] = s(cur_raw[j + 1])
                    tree_nodes[cur] = DOMNode(
                        tag=cur_tag,
                        role=cur_ax.get('role', cur_tag),
                        element_type='structural',
                        name=cur_name,
                        attributes=cur_attrs,
                    )
                cur = node_parent[cur] if cur < len(node_parent) else -1

        # Attach each tree node to its nearest ancestor in tree_nodes
        tree_root = DOMNode(tag='document', role='document', element_type='structural')
        for ni in sorted(tree_nodes.keys()):
            node = tree_nodes[ni]
            parent_node = tree_root
            cur = node_parent[ni] if ni < len(node_parent) else -1
            while cur >= 0:
                if cur in tree_nodes:
                    parent_node = tree_nodes[cur]
                    break
                cur = node_parent[cur] if cur < len(node_parent) else -1
            parent_node.add_child(node)

        return interactive, informative, scrollable, tree_root
