"""Collect and save read-only Android UI observations for later inspection.

Read app state and XML before and after a screenshot, flag changes, and save
the evidence in a unique capture directory. Also index XML nodes with parent
relationships and create a readable text/control inventory. This reusable
module does not classify prompts, call an LLM, or execute UI actions.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import struct
import uuid
import xml.etree.ElementTree as ET


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def index_nodes(xml):
    """Retain parent relationships and raw attributes; infer no Hinge semantics."""
    nodes = []

    def visit(element, parent):
        node_id = len(nodes)
        nodes.append({"id": node_id, "parent_id": parent,
                      "tag": element.tag, "attributes": dict(element.attrib)})
        for child in element:
            visit(child, node_id)

    visit(ET.fromstring(xml), None)
    return nodes


def capture_observation(driver, output_root, expected_package):
    """Bracket screenshot with XML/state reads. Equality is evidence, not proof."""
    started = utc_now()
    before = {"package": driver.current_package, "activity": driver.current_activity,
              "window_size": driver.get_window_size()}
    if before["package"] != expected_package:
        raise RuntimeError(f"Expected {expected_package}, found {before['package']}. "
                           "Open the intended profile manually and try again.")
    xml = driver.page_source
    screenshot_at = utc_now()
    png = driver.get_screenshot_as_png()
    xml_after = driver.page_source
    after = {"package": driver.current_package, "activity": driver.current_activity,
             "window_size": driver.get_window_size()}
    finished = utc_now()
    if len(png) < 24 or png[:8] != b"\x89PNG\r\n\x1a\n" or png[12:16] != b"IHDR":
        raise ValueError("Appium returned an invalid PNG screenshot")
    width, height = struct.unpack(">II", png[16:24])
    nodes = index_nodes(xml)
    unchanged = before == after and xml == xml_after
    observation_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ") + "_" + uuid.uuid4().hex[:8]
    folder = Path(output_root) / observation_id
    folder.mkdir(parents=True, mode=0o700)
    (folder / "screen.png").write_bytes(png)
    (folder / "hierarchy.xml").write_text(xml, encoding="utf-8")
    (folder / "hierarchy_after.xml").write_text(xml_after, encoding="utf-8")
    (folder / "nodes.json").write_text(json.dumps(nodes, indent=2, ensure_ascii=False), encoding="utf-8")
    metadata = {
        "schema_version": 1, "observation_id": observation_id,
        "started_at": started, "screenshot_requested_at": screenshot_at,
        "finished_at": finished, "before": before, "after": after,
        "screenshot_size": {"width": width, "height": height},
        "hierarchy_unchanged": xml == xml_after, "state_unchanged": before == after,
        "consistency": "unchanged" if unchanged else "changed",
        "screen_type": "unclassified",
        "note": "Sequential capture, not atomic. Unchanged XML does not prove visual stability.",
    }
    lines = ["UI inspection (raw evidence; no prompt or like-button classification)",
             f"Observation: {observation_id}", f"Consistency: {metadata['consistency']}",
             "Node IDs are local to this observation, not reusable selectors.", ""]
    for node in nodes:
        attrs = node["attributes"]
        if attrs.get("text") or attrs.get("content-desc") or attrs.get("clickable") == "true":
            lines.append(json.dumps(node, ensure_ascii=False))
    (folder / "inspection.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Written last: its presence indicates all artifact writes completed.
    (folder / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return folder, metadata
