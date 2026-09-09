"""Phase 5: verify a prepared comment and optionally attempt one submission.

Default mode only checks the current composer and saves evidence. --send is an
explicit instruction to submit the exact prepared comment. A durable attempt
record is written before the click; uncertain attempts cannot be retried by
rerunning this script. UI confirmation is recorded separately from uncertainty.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid
import xml.etree.ElementTree as ET

from observation import capture_observation
from prepare_comment import read_editing_composer, composer_field, load_selection, node_xpath, reveal_composer
from print_profile_prompts import read_profile
from profile_items import bounds, contains


def load_prepared(path):
    receipt = json.loads(Path(path).read_text(encoding="utf-8"))
    if receipt.get("status") != "text_verified_not_submitted" or receipt.get("submitted") is not False:
        raise ValueError("Expected a successful, unsent Phase 4 preparation receipt.")
    profile, target, candidate = load_selection(receipt["drafts_path"], receipt["candidate_id"])
    if (receipt.get("source_scan_id"), receipt.get("item_id"), receipt.get("comment")) != (
            profile["scan_id"], target["item_id"], candidate["comment"]):
        raise ValueError("Preparation receipt no longer matches the selected draft.")
    return profile, target, candidate


def send_control(root, target, size):
    field, _ = composer_field(root, target)
    parents = {child: parent for parent in root.iter() for child in parent}
    ancestor = parents[field]
    while ancestor is not None:
        if any(n.get("content-desc", "").startswith("Skip ") for n in ancestor.iter()):
            break
        controls = [n for n in ancestor.iter() if n.get("content-desc") == "Send like with message"]
        if controls:
            if len(controls) != 1:
                raise RuntimeError("Ambiguous Send Like control.")
            control = controls[0]
            rect = bounds(control.get("bounds"))
            if control.get("class") != "android.widget.Button" or control.get("enabled") != "true" or control.get("displayed") != "true":
                raise RuntimeError("Send Like control is not ready.")
            # Exclude sticky header, skip overlay, and bottom navigation.
            safe_area = [int(size["width"] * .22), int(size["height"] * .12), size["width"], int(size["height"] * .85)]
            if not contains(safe_area, rect):
                raise RuntimeError("Send Like control is clipped or outside the safe content area.")
            return node_xpath(root, list(root.iter()).index(control)), rect
        ancestor = parents.get(ancestor)
    raise RuntimeError("No comment-bearing Send Like control in the selected composer.")


def verify_text(driver, profile, target, comment):
    root, selector = reveal_composer(driver, profile, target)
    original_clipboard = driver.get_clipboard_text()
    try:
        driver.set_clipboard_text("phase5-readback-" + uuid.uuid4().hex)
        fresh, _ = read_profile(driver, profile["profile_label"])
        field, fresh_selector = composer_field(fresh, target)
        old_field, _ = composer_field(root, target)
        if fresh_selector != selector or field.get("bounds") != old_field.get("bounds"):
            raise RuntimeError("Composer changed before verification.")
        elements = driver.find_elements("xpath", fresh_selector)
        if len(elements) != 1 or elements[0].get_attribute("content-desc") != "Edit comment":
            raise RuntimeError("Cannot uniquely focus the prepared comment.")
        driver.execute_script("mobile: clickGesture", {"elementId": elements[0].id})
        time.sleep(.3)
        read_editing_composer(driver, target)
        driver.press_keycode(29, metastate=4096)
        driver.press_keycode(31, metastate=4096)
        time.sleep(.3)
        actual = driver.get_clipboard_text()
        driver.press_keycode(22)
        if actual != comment:
            raise RuntimeError("Current field text differs from the prepared draft. Nothing sent.")
    finally:
        driver.set_clipboard_text(original_clipboard)
    return reveal_composer(driver, profile, target)[0]


def attempt_key(profile, target):
    # Intentionally independent of scan/candidate ID to block duplicate target sends
    # after regenerating drafts. Display names are not unique account identifiers.
    content = [profile["profile_label"], target["title"], target["response"]]
    return hashlib.sha256(json.dumps(content, ensure_ascii=False).encode()).hexdigest()


def claim_attempt(path, record):
    """Exclusive creation blocks concurrent/repeated attempts; persist before click."""
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(record, stream, indent=2, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(str(Path(path).parent), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def save_record(path, record):
    temp = Path(str(path) + ".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        json.dump(record, stream, indent=2, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)


def outcome(root, label):
    values = {n.get("text", "").strip() for n in root.iter()} | {
        n.get("content-desc", "").strip() for n in root.iter()}
    if {"Send a Rose instead?", "Send Like anyway"} <= values:
        return "pending_rose_upsell_not_confirmed"
    if "Edit comment" in values or "Send like with message" in values:
        return "uncertain_composer_still_present"
    # Provisional English confirmation vocabulary; validate against live evidence.
    if values & {"Like sent", "Your like was sent"}:
        return "confirmed_by_ui"
    if any(v.startswith("Skip ") and v != label for v in values):
        return "uncertain_profile_advanced"
    return "uncertain_no_confirmation"


def submit(driver, receipt_path, send=False, output_root="captures"):
    profile, target, candidate = load_prepared(receipt_path)
    ledger = Path(output_root) / "submissions"
    ledger.mkdir(parents=True, exist_ok=True, mode=0o700)
    attempt_path = ledger / (attempt_key(profile, target) + ".json")
    if attempt_path.exists():
        raise RuntimeError("A submission attempt is already recorded for this target. Inspect its outcome; automatic retry is blocked.")
    root = verify_text(driver, profile, target, candidate["comment"])
    size = driver.get_window_size()
    selector, rect = send_control(root, target, size)
    before, metadata = capture_observation(driver, ledger, "co.hinge.app")
    if metadata["consistency"] != "unchanged":
        raise RuntimeError("Screen changed during the final check. Nothing sent.")
    record = {"schema_version": 1, "preparation": str(Path(receipt_path).resolve()),
              "profile_label": profile["profile_label"], "target": target,
              "comment": candidate["comment"], "before_capture": str(before.resolve()),
              "status": "ready_not_sent", "send_authorized": send}
    if not send:
        check = ledger / ("check_" + uuid.uuid4().hex + ".json")
        save_record(check, record)
        print(f"Ready, not sent: {check.resolve()}\nComment: {candidate['comment']}")
        return record
    fresh, _ = read_profile(driver, profile["profile_label"])
    fresh_selector, fresh_rect = send_control(fresh, target, size)
    if (selector, rect) != (fresh_selector, fresh_rect):
        raise RuntimeError("Send target changed. Nothing sent.")
    controls = driver.find_elements("xpath", fresh_selector)
    if len(controls) != 1 or controls[0].get_attribute("content-desc") != "Send like with message" or not controls[0].is_enabled() or not controls[0].is_displayed():
        raise RuntimeError("Send control is no longer ready. Nothing sent.")
    record["status"] = "attempted_outcome_unknown"
    claim_attempt(attempt_path, record)
    try:
        # This is the only submission operation. Never retry it, even on timeout.
        driver.execute_script("mobile: clickGesture", {"elementId": controls[0].id})
        deadline = time.monotonic() + 10
        record["status"] = "uncertain_no_confirmation"
        while time.monotonic() < deadline:
            if driver.current_package != "co.hinge.app":
                break
            root = ET.fromstring(driver.page_source)
            record["status"] = outcome(root, profile["profile_label"])
            if record["status"] == "pending_rose_upsell_not_confirmed":
                if record.get("standard_like_confirmation_attempted"):
                    break
                # Only continue the known upsell generated by our own first tap.
                # Never buy/send a Rose, and never resume this step after a crash.
                options = driver.find_elements("xpath", '//*[@text="Send Like anyway"]')
                if len(options) != 1 or not options[0].is_displayed() or not options[0].is_enabled():
                    break
                record["standard_like_confirmation_attempted"] = True
                record["status"] = "confirmation_attempted_outcome_unknown"
                save_record(attempt_path, record)
                driver.execute_script("mobile: clickGesture", {"elementId": options[0].id})
            if record["status"] == "confirmed_by_ui":
                break
            time.sleep(.5)
        after, _ = capture_observation(driver, ledger, "co.hinge.app")
        record["after_capture"] = str(after.resolve())
    except (Exception, KeyboardInterrupt) as exc:
        record["status"] = "uncertain_after_attempt"
        record["error_type"] = type(exc).__name__
    finally:
        save_record(attempt_path, record)
    print(f"{record['status']}: {attempt_path.resolve()}")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("preparation", type=Path)
    parser.add_argument("--send", action="store_true", help="Authorize one real like/comment submission")
    parser.add_argument("--server", default="http://127.0.0.1:4723")
    parser.add_argument("--udid", default="127.0.0.1:6555")
    args = parser.parse_args()
    driver = None
    try:
        load_prepared(args.preparation)
        from appium import webdriver
        from appium.options.android import UiAutomator2Options
        options = UiAutomator2Options().load_capabilities({"platformName": "Android",
            "appium:automationName": "UiAutomator2", "appium:udid": args.udid,
            "appium:noReset": True, "appium:autoLaunch": False})
        driver = webdriver.Remote(args.server, options=options)
        record = submit(driver, args.preparation, send=args.send)
        return 0 if record["status"] in {"ready_not_sent", "confirmed_by_ui"} else 2
    except (Exception, KeyboardInterrupt) as exc:
        print(f"Submission stopped: {exc}", file=sys.stderr)
        return 1
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                print("Session cleanup failed.", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
