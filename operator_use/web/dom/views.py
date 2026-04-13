from dataclasses import dataclass, field
from textwrap import shorten
import json


@dataclass
class BoundingBox:
    left: int
    top: int
    width: int
    height: int

    def to_string(self):
        return f"({self.left},{self.top},{self.width},{self.height})"

    def to_dict(self):
        return {"left": self.left, "top": self.top, "width": self.width, "height": self.height}


@dataclass
class CenterCord:
    x: int
    y: int

    def to_string(self) -> str:
        return f"({self.x},{self.y})"

    def to_dict(self):
        return {"x": self.x, "y": self.y}


@dataclass
class DOMElementNode:
    tag: str
    role: str
    name: str
    bounding_box: BoundingBox
    center: CenterCord
    attributes: dict[str, str] = field(default_factory=dict)
    xpath: dict[str, str] = field(default_factory=dict)
    viewport: tuple[int, int] = field(default_factory=tuple)

    def __repr__(self):
        return f"DOMElementNode(tag='{self.tag}', role='{self.role}', name='{self.name}', attributes={self.attributes}, cordinates={self.center}, bounding_box={self.bounding_box}, xpath='{self.xpath}')"

    def to_dict(self) -> dict[str, str]:
        return {
            "tag": self.tag,
            "role": self.role,
            "name": self.name,
            "bounding_box": self.bounding_box.to_dict(),
            "attributes": self.attributes,
            "cordinates": self.center.to_dict(),
        }


@dataclass
class ScrollElementNode:
    tag: str
    role: str
    name: str
    attributes: dict[str, str] = field(default_factory=dict)
    xpath: dict[str, str] = field(default_factory=dict)
    viewport: tuple[int, int] = field(default_factory=tuple)

    def __repr__(self):
        return f"ScrollableElementNode(tag='{self.tag}', role='{self.role}', name='{shorten(self.name, width=500)}', attributes={self.attributes}, xpath='{self.xpath}')"

    def to_dict(self) -> dict[str, str]:
        return {
            "tag": self.tag,
            "role": self.role,
            "name": self.name,
            "attributes": self.attributes,
        }


@dataclass
class DOMTextualNode:
    tag: str
    role: str
    content: str
    center: CenterCord
    xpath: dict[str, str] = field(default_factory=dict)
    viewport: tuple[int, int] = field(default_factory=tuple)

    def __repr__(self):
        return f"DOMTextualNode(tag={self.tag}, role={self.role}, content={self.content}, cordinates={self.center}, xpath={self.xpath})"

    def to_dict(self) -> dict[str, str]:
        return {
            "tag": self.tag,
            "role": self.role,
            "content": self.content,
            "center": self.center.to_dict(),
        }


@dataclass
class DOMState:
    interactive_nodes: list[DOMElementNode] = field(default_factory=list)
    informative_nodes: list[DOMTextualNode] = field(default_factory=list)
    scrollable_nodes: list[ScrollElementNode] = field(default_factory=list)
    selector_map: dict[str, DOMElementNode | ScrollElementNode] = field(default_factory=dict)

    def interactive_elements_to_string(self) -> str:
        if not self.interactive_nodes:
            return "No interactive elements"
        header = "# id|tag|role|name|coords|attributes"
        rows = [header]
        for idx, node in enumerate(self.interactive_nodes):
            attrs = json.dumps(node.attributes) if node.attributes else "{}"
            rows.append(
                f"{idx}|{node.tag}|{node.role}|{node.name}|{node.center.to_string()}|{attrs}"
            )
        return "\n".join(rows)

    def informative_elements_to_string(self) -> str:
        if not self.informative_nodes:
            return "No informative elements"
        header = "# tag|role|content"
        rows = [header]
        for node in self.informative_nodes:
            rows.append(f"{node.tag}|{node.role}|{node.content}")
        return "\n".join(rows)

    def scrollable_elements_to_string(self) -> str:
        if not self.scrollable_nodes:
            return "No scrollable elements"
        n = len(self.interactive_nodes)
        header = "# id|tag|role|name|attributes"
        rows = [header]
        for idx, node in enumerate(self.scrollable_nodes):
            attrs = json.dumps(node.attributes) if node.attributes else "{}"
            rows.append(f"{n + idx}|{node.tag}|{node.role}|{shorten(node.name, width=500)}|{attrs}")
        return "\n".join(rows)
