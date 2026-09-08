"""Convert profile XML into structured written-prompt items and control evidence.

Extract titles and responses, associate Like prompt buttons within matching
card containers, and record bounds, node references, and control state. Mark
missing or ambiguous controls explicitly. ProfileInventory merges repeated
prompt text while retaining its observation history and scan-local item ID.
This module does not connect to Android or act on the recorded controls.
"""

import re
import xml.etree.ElementTree as ET

from print_profile_prompts import extract_prompts


def bounds(value):
    match = re.fullmatch(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]", value or "")
    return list(map(int, match.groups())) if match else None


def contains(outer, inner):
    return bool(outer and inner and outer[0] <= inner[0] < inner[2] <= outer[2]
                and outer[1] <= inner[1] < inner[3] <= outer[3])


def extract_items(root, observation_id, window_size):
    """Associate only within a card-sized ancestor, never the whole profile."""
    nodes = list(root.iter())
    ids = {node: index for index, node in enumerate(nodes)}
    parents = {child: parent for parent in nodes for child in parent}
    viewport = [0, 0, window_size["width"], window_size["height"]]
    items = []
    for node in nodes:
        description = node.get("content-desc", "")
        if not description.startswith("Prompt: "):
            continue
        parsed = extract_prompts(ET.fromstring(ET.tostring(node)))[0]
        card_bounds = bounds(node.get("bounds"))
        ancestor = parents.get(node)
        card = node
        buttons = []
        ambiguous = False
        # Observed description covers its card. Only climb equal-bounds wrappers.
        # This prevents a clipped card borrowing a neighbouring card's button.
        while ancestor is not None and card_bounds and bounds(ancestor.get("bounds")) == card_bounds:
            descriptions = [n for n in ancestor.iter()
                            if n.get("content-desc", "").startswith("Prompt: ")]
            if len(descriptions) != 1:
                ambiguous = True
                break
            card = ancestor
            buttons = [n for n in card.iter() if n.get("content-desc") == "Like prompt"
                       and n.get("class") == "android.widget.Button"]
            if buttons:
                break
            ancestor = parents.get(ancestor)
        if len(buttons) > 1:
            ambiguous = True
        button = buttons[0] if len(buttons) == 1 and not ambiguous else None
        control = None
        if button is not None:
            control = {"node_id": ids[button], "bounds": bounds(button.get("bounds")),
                       "accessibility_id": "Like prompt", "resource_id": button.get("resource-id"),
                       "enabled": button.get("enabled") == "true",
                       "displayed": button.get("displayed") == "true",
                       "clickable": button.get("clickable") == "true"}
        status = "ambiguous" if ambiguous else "control_not_exposed"
        if control:
            status = "control_not_ready"
            if (control["enabled"] and control["displayed"] and control["clickable"]
                    and contains(card_bounds, control["bounds"])
                    and contains(viewport, control["bounds"])):
                status = "control_visible_in_observation"
        items.append({"type": "written_prompt", "title": parsed.title,
                      "response": parsed.response, "raw_description": description,
                      "observation_id": observation_id, "node_id": ids[node],
                      "card_node_id": ids[card], "card_bounds": card_bounds,
                      "control": control, "status": status,
                      "requires_fresh_resolution": True})
    return items


class ProfileInventory:
    """Deduplicate text across observations, retaining every piece of evidence."""

    def __init__(self, scan_id):
        self.scan_id = scan_id
        self.items = []

    def add(self, items):
        keys = [(item["title"], item["response"]) for item in items]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate prompt cards in one observation; identity is ambiguous.")
        for item in items:
            existing = next((entry for entry in self.items
                             if (entry["title"], entry["response"]) == (item["title"], item["response"])), None)
            if existing is None:
                existing = {"item_id": f"{self.scan_id}:prompt-{len(self.items) + 1}",
                            "type": "written_prompt", "title": item["title"],
                            "response": item["response"], "observations": []}
                self.items.append(existing)
            existing["observations"].append(item)
