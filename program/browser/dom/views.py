from dataclasses import dataclass,field
from textwrap import shorten
import json
from typing import Any, Optional

@dataclass
class BoundingBox:
    left:int
    top:int
    width:int
    height:int

    def to_string(self):
        return f'({self.left},{self.top},{self.width},{self.height})'
    
    def to_dict(self):
        return {'left':self.left,'top':self.top,'width':self.width,'height':self.height}

@dataclass
class CenterCord:
    x:int
    y:int

    def to_string(self)->str:
        return f'({self.x},{self.y})'
    
    def to_dict(self):
        return {'x':self.x,'y':self.y}

@dataclass
class DOMNode:
    tag: str
    role: str
    element_type: str  # 'interactive', 'scrollable', 'informative', 'structural'
    name: Optional[str] = None  # For interactive and scrollable
    content: Optional[str] = None  # For informative
    bounding_box: Optional[BoundingBox] = None  # For interactive
    center: Optional[CenterCord] = None  # For interactive and informative
    attributes: dict[str,str] = field(default_factory=dict)
    xpath: dict[str,str] = field(default_factory=dict)
    viewport: tuple[int,int] = (0, 0)
    interactive_id: Optional[int] = None  # For tree rendering
    href: Optional[str] = None  # For tree rendering links
    children: list['DOMNode'] = field(default_factory=list)

    def add_child(self, child: 'DOMNode') -> None:
        self.children.append(child)

    def __repr__(self):
        if self.element_type == 'interactive':
            return f"DOMNode(tag='{self.tag}', type='interactive', name='{self.name}', bbox={self.bounding_box}, xpath='{self.xpath}')"
        elif self.element_type == 'scrollable':
            return f"DOMNode(tag='{self.tag}', type='scrollable', name='{shorten(self.name or '',width=50)}', xpath='{self.xpath}')"
        elif self.element_type == 'informative':
            content_preview = shorten(self.content or '', width=50)
            return f"DOMNode(tag='{self.tag}', type='informative', content='{content_preview}', xpath='{self.xpath}')"
        else:
            return f"DOMNode(tag='{self.tag}', type='structural', xpath='{self.xpath}')"

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {'tag': self.tag, 'role': self.role, 'type': self.element_type}
        if self.name:
            result['name'] = self.name
        if self.content:
            result['content'] = self.content
        if self.bounding_box:
            result['bounding_box'] = self.bounding_box.to_dict()
        if self.center:
            result['center'] = self.center.to_dict()
        if self.attributes:
            result['attributes'] = self.attributes
        return result

@dataclass
class DOMState:
    interactive_nodes: list[DOMNode] = field(default_factory=list)
    informative_nodes: list[DOMNode] = field(default_factory=list)
    scrollable_nodes: list[DOMNode] = field(default_factory=list)
    selector_map: dict[int, DOMNode] = field(default_factory=dict)
    semantic_tree_root: Optional[DOMNode] = field(default=None)

    def semantic_tree_to_string(self) -> str:
        if not self.semantic_tree_root:
            return 'No elements'

        lines: list[str] = []
        self._render_tree(self.semantic_tree_root, lines, '', is_last=True)
        return '\n'.join(lines)

    def _render_tree(self, node: DOMNode, lines: list[str], prefix: str, is_last: bool) -> None:
        if node.tag == 'document':
            lines.append(f"{node.tag}  [role: {node.role}]")
        else:
            connector = '└── ' if is_last else '├── '
            line = self._format_node(node)
            lines.append(f"{prefix}{connector}{line}")

        extension = '    ' if is_last else '│   '
        new_prefix = prefix + extension

        for i, child in enumerate(node.children):
            is_last_child = i == len(node.children) - 1
            self._render_tree(child, lines, new_prefix, is_last_child)

    def _selector(self, node: DOMNode) -> str:
        id_val = node.attributes.get('id', '')
        classes = node.attributes.get('class', '').split()[:3]  # cap at 3 classes
        id_part = f'#{id_val}' if id_val else ''
        class_part = ''.join(f'.{c}' for c in classes)
        return f'{node.tag}{id_part}{class_part}'

    def _format_node(self, node: DOMNode) -> str:
        sel = self._selector(node)
        role_suffix = f" [{node.role}]" if node.role and node.role != node.tag else ""
        if node.element_type == 'interactive':
            label = f"[#{node.interactive_id}]"
            if node.href:
                return f"{label} {sel}{role_suffix} \"{node.name}\"  → {node.href}"
            else:
                return f"{label} {sel}{role_suffix} \"{node.name}\""
        elif node.element_type == 'scrollable':
            return f"{sel}{role_suffix}  [scrollable] \"{node.name}\""
        elif node.element_type == 'informative':
            content_preview = shorten(node.content or '', width=50)
            return f"{sel}{role_suffix}  \"{content_preview}\""
        else:
            name_part = f" \"{node.name}\"" if node.name else ""
            return f"{sel}{role_suffix}{name_part}"

    def interactive_elements_to_string(self)->str:
        if not self.interactive_nodes:
            return 'No interactive elements'
        header = '# id|tag|role|name|coords|attributes'
        rows = [header]
        for index, node in enumerate(self.interactive_nodes):
            center = node.center.to_string() if node.center else ''
            row = f'{index}|{node.tag}|{node.role}|{node.name}|{center}|{json.dumps(node.attributes)}'
            rows.append(row)
        return '\n'.join(rows)

    def informative_elements_to_string(self)->str:
        return  '\n'.join([f'Tag: {node.tag} Role: {node.role} Content: {node.content}' for node in self.informative_nodes])

    def scrollable_elements_to_string(self)->str:
        if not self.scrollable_nodes:
            return 'No scrollable elements'
        header = '# id|tag|role|name|attributes'
        rows = [header]
        base_index = len(self.interactive_nodes)
        for index, node in enumerate(self.scrollable_nodes):
            row = f'{base_index + index}|{node.tag}|{node.role}|{shorten(node.name or "", width=500)}|{json.dumps(node.attributes)}'
            rows.append(row)
        return '\n'.join(rows)
    
