"""Scan the current profile into structured JSON and supporting XML evidence."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid
import xml.etree.ElementTree as ET

from print_profile_prompts import collect_prompts, read_profile
from profile_items import ProfileInventory, extract_items


def scan_profile(driver, output_root="captures", max_scrolls=30):
    _, label = read_profile(driver)
    scan_id = "profile_" + uuid.uuid4().hex
    folder = Path(output_root) / scan_id
    folder.mkdir(parents=True, mode=0o700)
    inventory = ProfileInventory(scan_id)
    observations = []
    size = driver.get_window_size()

    def record(root):
        observation_id = f"observation-{len(observations) + 1:03d}"
        xml_file = f"{observation_id}.xml"
        (folder / xml_file).write_bytes(ET.tostring(root, encoding="utf-8"))
        inventory.add(extract_items(root, observation_id, size))
        observations.append({"observation_id": observation_id, "xml_file": xml_file,
                             "observed_at": datetime.now(timezone.utc).isoformat()})

    report = {"schema_version": 1, "scan_id": scan_id, "profile_label": label,
              "identity_assurance": "unverified",
              "window_size": size, "observations": observations, "items": inventory.items,
              "status": "incomplete", "boundary_method": "two_unchanged_swipes",
              "requires_fresh_resolution": True}
    try:
        collect_prompts(driver, max_scrolls=max_scrolls, on_observation=record, verify_return=True)
        read_profile(driver, label)
        report["identity_assurance"] = "skip_label_and_return_to_top_content_match_not_unique_identity"
        report["status"] = "scan_finished_with_heuristic_boundaries"
    except Exception as exc:
        report["error"] = str(exc)
        raise
    finally:
        (folder / "profile.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Profile evidence: {folder.resolve()}")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default="http://127.0.0.1:4723")
    parser.add_argument("--udid", default="127.0.0.1:6555")
    parser.add_argument("--output", default="captures")
    parser.add_argument("--max-scrolls", type=int, default=30)
    args = parser.parse_args()
    if args.max_scrolls < 2:
        parser.error("--max-scrolls must be at least 2")
    driver = None
    try:
        from appium import webdriver
        from appium.options.android import UiAutomator2Options
        options = UiAutomator2Options().load_capabilities({
            "platformName": "Android", "appium:automationName": "UiAutomator2",
            "appium:udid": args.udid, "appium:noReset": True, "appium:autoLaunch": False,
        })
        driver = webdriver.Remote(args.server, options=options)
        print("Scanning profile. Keep the emulator untouched.")
        report = scan_profile(driver, args.output, args.max_scrolls)
        print(f"Extracted {len(report['items'])} written prompts.")
        for item in report["items"]:
            ready = any(o["status"] == "control_visible_in_observation" for o in item["observations"])
            print(f"{item['item_id']}: {item['title']} (like control observed: {ready})")
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        print(f"Extraction stopped: {exc}", file=sys.stderr)
        return 1
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception as exc:
                print(f"Session cleanup failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
