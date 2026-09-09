"""Prepare a selected Phase 3 comment in Hinge's composer without submitting it.

Validate draft provenance, compare the current profile's prompts with its saved
scan, and resolve the target card from fresh XML. Open its composer, verify the
target, and enter the selected comment. Save evidence and stop before sending.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import uuid
import xml.etree.ElementTree as ET

from comment_drafts import prompt_items, validate_drafts
from observation import capture_observation
from print_profile_prompts import collect_prompts, read_profile
from profile_items import extract_items


def load_selection(path, candidate_id):
    draft = json.loads(Path(path).read_text(encoding="utf-8"))
    if draft.get("status") != "drafts_pending_review":
        raise ValueError("Expected a saved Phase 3 draft set.")
    source = Path(draft["source_profile"]).read_bytes()
    if hashlib.sha256(source).hexdigest() != draft["source_sha256"]:
        raise ValueError("Source profile changed since draft generation.")
    profile = json.loads(source)
    if profile["scan_id"] != draft["source_scan_id"]:
        raise ValueError("Draft and source scan do not match.")
    items = prompt_items(profile)
    validate_drafts(draft["drafts"], items, draft["settings"]["max_chars"])
    if candidate_id == "recommended":
        candidate_id = draft["drafts"]["recommended_candidate_id"]
    candidates = [c for c in draft["drafts"]["candidates"] if c["candidate_id"] == candidate_id]
    if len(candidates) != 1:
        raise ValueError("Unknown candidate ID.")
    candidate = candidates[0]
    if any(ord(char) < 32 or ord(char) == 127 or 0xE000 <= ord(char) <= 0xE05D
           for char in candidate["comment"]):
        raise ValueError("Comment contains control characters or WebDriver key codes; refusing to type it.")
    target = next(i for i in items if i["item_id"] == candidate["item_id"])
    return profile, target, candidate


def node_xpath(root, node_id):
    """Build an absolute path valid only for this freshly read XML hierarchy."""
    nodes = list(root.iter())
    parents = {child: parent for parent in nodes for child in parent}
    node = nodes[node_id]
    segments = []
    while node is not root:
        parent = parents[node]
        siblings = [child for child in parent if child.tag == node.tag]
        segments.append(f"{node.tag}[{siblings.index(node) + 1}]")
        node = parent
    return "/" + root.tag + "/" + "/".join(reversed(segments))


def open_target(driver, profile, target, max_scrolls=30):
    label = profile["profile_label"]
    read_profile(driver, label)
    current = collect_prompts(driver, max_scrolls=max_scrolls, verify_return=False)
    expected = [(i["title"], i["response"]) for i in prompt_items(profile)]
    if [(i.title, i.response) for i in current] != expected:
        raise RuntimeError("Current profile prompts do not match the saved profile.")
    size = driver.get_window_size()
    for step in range(max_scrolls + 1):
        root, _ = read_profile(driver, label)
        matches = [i for i in extract_items(root, "fresh", size)
                   if (i["title"], i["response"]) == (target["title"], target["response"])]
        if len(matches) > 1 or any(i["status"] == "ambiguous" for i in matches):
            raise RuntimeError("Target card is ambiguous.")
        if matches and matches[0]["status"] == "control_visible_in_observation":
            item = matches[0]
            x1, y1, x2, y2 = item["control"]["bounds"]
            # Keep the control clear of the sticky header and bottom navigation.
            if y1 > size["height"] * .12 and y2 < size["height"] * .78:
                selector = node_xpath(root, item["control"]["node_id"])
                fresh, _ = read_profile(driver, label)
                if ET.tostring(root) != ET.tostring(fresh):
                    raise RuntimeError("Screen changed before opening the target; rerun.")
                elements = driver.find_elements("xpath", selector)
                if len(elements) != 1 or elements[0].get_attribute("content-desc") != "Like prompt":
                    raise RuntimeError("Target control could not be resolved uniquely.")
                if not elements[0].is_displayed() or not elements[0].is_enabled():
                    raise RuntimeError("Target control is no longer ready.")
                elements[0].click()
                return
        if step == max_scrolls:
            break
        driver.execute_script("mobile: swipeGesture", {
            "left": int(size["width"] * .25), "top": int(size["height"] * .2),
            "width": int(size["width"] * .5), "height": int(size["height"] * .55),
            "direction": "down", "percent": .35, "speed": 400})
        time.sleep(.3)
    raise RuntimeError("Could not expose the target control within the scroll limit.")


SEND_LABELS = {"Send like", "Send like with message"}


def matches_prompt(description, target):
    """Use the same edge-whitespace normalization as prompt extraction."""
    if not description or not description.startswith("Prompt: "):
        return False
    title, separator, response = description[len("Prompt: "):].partition(". Answer: ")
    return bool(separator) and (title.strip(), response.strip()) == (target['title'], target['response'])


def content_bottom(root, size):
    """Use the observed bottom navigation, with a conservative fallback."""
    from profile_items import bounds
    edges = [rect[1] for n in root.iter()
             if n.get('content-desc') == 'Discover'
             and (rect := bounds(n.get('bounds')))
             and rect[1] > size['height'] * .7]
    return min(edges) if edges else int(size['height'] * .78)


def composer_field(root, target, require_send=True):
    """Resolve the edit field only inside the selected prompt's inline composer."""
    nodes = list(root.iter())
    parents = {child: parent for parent in nodes for child in parent}
    fields = [n for n in nodes if n.get("content-desc") == "Edit comment"]
    if len(fields) != 1:
        raise RuntimeError("Expected exactly one comment composer.")
    field = fields[0]
    ancestor = parents.get(field)
    while ancestor is not None:
        descendants = list(ancestor.iter())
        if any(n.get("content-desc", "").startswith("Skip ") for n in descendants):
            break
        prompts = [n.get("content-desc") for n in descendants
                   if n.get("content-desc", "").startswith("Prompt: ")]
        if prompts:
            if len(prompts) != 1 or not matches_prompt(prompts[0], target):
                raise RuntimeError("Composer belongs to a different or ambiguous prompt.")
            if require_send and not any(n.get("content-desc") in SEND_LABELS for n in descendants):
                raise RuntimeError("Composer submission control is not exposed yet.")
            return field, node_xpath(root, nodes.index(field))
        ancestor = parents.get(ancestor)
    raise RuntimeError("Could not associate the comment field with its prompt card.")


def reveal_composer(driver, profile, target):
    """Scroll the inline composer into view without activating its send controls."""
    size = driver.get_window_size()
    for attempt in range(8):
        root, _ = read_unobscured_profile(driver, profile["profile_label"])
        # Validate the target even when its send button is below the viewport.
        descriptions = [n.get("content-desc") for n in root.iter()]
        target_visible = any(matches_prompt(d, target) for d in descriptions)
        direction = "up"
        if "Edit comment" not in descriptions or not target_visible:
            if target_visible and "Edit comment" not in descriptions:
                # A long expanded prompt can fill the viewport with its editor
                # and send row still below it. Continue down the same card.
                direction = "up"
            elif not any(d in SEND_LABELS for d in descriptions) and "Edit comment" not in descriptions:
                raise RuntimeError("Selected prompt/composer disappeared; stopping.")
            else:
                direction = "down"  # Prompt/field can be clipped above the send row.
        elif any(d in SEND_LABELS for d in descriptions):
            field, selector = composer_field(root, target)
            from profile_items import bounds
            rect = bounds(field.get("bounds"))
            if rect and rect[1] > size["height"] * .12 and rect[3] < content_bottom(root, size):
                return root, selector
            if rect and rect[1] <= size["height"] * .12:
                direction = "down"
        if attempt < 7:
            driver.execute_script("mobile: swipeGesture", {
                "left": int(size["width"] * .25), "top": int(size["height"] * .2),
                "width": int(size["width"] * .5), "height": int(size["height"] * .55),
                "direction": direction, "percent": .25, "speed": 400})
            time.sleep(.3)
    raise RuntimeError("Could not fully expose the composer within the scroll limit.")


def fill_composer(driver, profile, target, comment, *, verify=True):
    """Paste into the verified field and compare a fresh copied value exactly.

Use native key events because the inspected Compose view hides editable text
from accessibility. A unique clipboard marker prevents stale clipboard contents
from passing read-back validation. No Enter, editor action, or send tap is used.
"""
    root, selector = reveal_composer(driver, profile, target)
    previous_clipboard = driver.get_clipboard_text()
    try:
        driver.set_clipboard_text(comment)
        fresh, _ = read_unobscured_profile(driver, profile["profile_label"])
        fresh_field, fresh_selector = composer_field(fresh, target)
        original_field, _ = composer_field(root, target)
        # Clipboard notifications can change window IDs or unrelated UI nodes.
        # Compare the target-associated field and its geometry, not those extras.
        if selector != fresh_selector or original_field.get("bounds") != fresh_field.get("bounds"):
            raise RuntimeError("Composer changed before text entry; rerun.")
        fields = driver.find_elements("xpath", selector)
        if len(fields) != 1 or fields[0].get_attribute("content-desc") != "Edit comment":
            raise RuntimeError("Comment field is no longer uniquely available.")
        driver.execute_script("mobile: clickGesture", {"elementId": fields[0].id})
        time.sleep(.3)
        read_editing_composer(driver, target)
        driver.press_keycode(29, metastate=4096)  # Ctrl+A inside the focused comment.
        driver.press_keycode(279)  # Android KEYCODE_PASTE, including Unicode text.
        time.sleep(.5)
        read_editing_composer(driver, target)
        if verify:
            marker = "phase4-readback-" + uuid.uuid4().hex
            driver.set_clipboard_text(marker)
            driver.press_keycode(29, metastate=4096)  # Select the actual field contents.
            driver.press_keycode(31, metastate=4096)  # Ctrl+C; no clipboard value is trusted until now.
            time.sleep(.3)
            actual = driver.get_clipboard_text()
            if actual != comment:
                raise RuntimeError("Entered text could not be verified exactly; inspect manually. No retry was attempted.")
            driver.press_keycode(22)  # Collapse text selection without submitting.
    finally:
        driver.set_clipboard_text(previous_clipboard)
    # Leave both target and send control visible for the user, without pressing it.
    reveal_composer(driver, profile, target)


def read_unobscured_profile(driver, label):
    """Handle delayed keyboard opening after a tap or composer scroll.

    Retry only a missing identity control, never a changed profile or package.
    Each dismissal retains the single-Back guard in dismiss_keyboard.
    """
    for attempt in range(4):
        dismiss_keyboard(driver)
        try:
            return read_profile(driver, label)
        except RuntimeError as exc:
            if str(exc) != "Cannot identify the current Discover profile; stopping." or attempt == 3:
                raise
            time.sleep(.3)


def dismiss_keyboard(driver):
    """Dismiss a visible IME before requiring the full Discover hierarchy."""
    if driver.current_package != "co.hinge.app":
        return
    root = ET.fromstring(driver.page_source)
    # The IME flag can remain true after dismissal on this emulator. Never send
    # another Back once Discover's Skip control is exposed: it can exit Hinge.
    if any(n.get("content-desc", "").startswith("Skip ") for n in root.iter()):
        return
    if driver.is_keyboard_shown():
        driver.press_keycode(4)
        for _ in range(10):
            time.sleep(.2)
            root = ET.fromstring(driver.page_source)
            if any(n.get("content-desc", "").startswith("Skip ") for n in root.iter()):
                return
        raise RuntimeError("Discover controls did not return after keyboard dismissal; stopping without another Back.")


def read_editing_composer(driver, target):
    """Validate the same target while the keyboard hides Discover's Skip label.

    Used only after the full profile was verified immediately before focus.
    Full profile-label validation resumes after dismissing the keyboard.
    """
    if driver.current_package != "co.hinge.app":
        raise RuntimeError("Hinge is no longer foreground while editing.")
    root = ET.fromstring(driver.page_source)
    check_focused_composer(root, target)
    return root


def check_focused_composer(root, target):
    """Stop before key events when focusing scrolls the composer out of XML."""
    if not any(n.get("content-desc") == "Edit comment" for n in root.iter()):
        raise RuntimeError(
            "The comment field disappeared from accessibility after focusing it. "
            "Hinge may have scrolled it off-screen and lost input focus. "
            "No typing or submission was attempted at this step. Enable Show "
            "virtual keyboard in Android's physical-keyboard settings and check "
            "manual input before retrying; see README.md.")
    return composer_field(root, target, require_send=False)


def prepare(driver, drafts_path, candidate_id, *, max_scrolls=30, inspect_only=False,
            resume_composer=False, output_root="captures", defer_readback=False):
    profile, target, candidate = load_selection(drafts_path, candidate_id)
    receipt_dir = Path(output_root) / ("preparation_" + uuid.uuid4().hex)
    receipt_dir.mkdir(parents=True, mode=0o700)
    receipt = {"schema_version": 1, "status": "incomplete", "drafts_path": str(Path(drafts_path).resolve()),
               "source_scan_id": profile["scan_id"], "candidate_id": candidate["candidate_id"],
               "item_id": target["item_id"], "comment": candidate["comment"], "submitted": False,
               "approved_for_submission": False, "resume_composer": resume_composer}
    receipt["preparation_path"] = str((receipt_dir / "preparation.json").resolve())
    try:
        receipt["stage"] = "checking_profile"
        root, _ = read_unobscured_profile(driver, profile["profile_label"])
        existing = any(n.get("content-desc") == "Edit comment" or n.get("content-desc") in SEND_LABELS
                       for n in root.iter())
        if existing and not resume_composer:
            raise RuntimeError("A composer is already open. Close it manually or use --resume-composer to replace its text after target verification.")
        if not existing and not resume_composer:
            receipt["stage"] = "opening_target"
            open_target(driver, profile, target, max_scrolls)
        receipt["stage"] = "revealing_composer"
        reveal_composer(driver, profile, target)
        before, _ = capture_observation(driver, receipt_dir, "co.hinge.app")
        receipt["before_capture"] = str(before.resolve())
        if inspect_only:
            receipt["status"] = "composer_opened_no_text_entered"
        else:
            receipt["stage"] = "entering_and_verifying_text"
            fill_composer(driver, profile, target, candidate["comment"], verify=not defer_readback)
            receipt["status"] = "text_entered_pending_verification" if defer_readback else "text_verified_not_submitted"
            receipt["text_verification"] = "pending_submission_preflight" if defer_readback else "exact_clipboard_readback_with_unique_marker"
        receipt["stage"] = "capturing_prepared_comment"
        after, metadata = capture_observation(driver, receipt_dir, "co.hinge.app")
        receipt["after_capture"] = str(after.resolve())
        if metadata["consistency"] != "unchanged":
            receipt["status"] = "final_capture_changed_requires_review"
            raise RuntimeError("Final screen changed during capture; inspect manually.")
        print(f"{receipt['status']}: {receipt_dir.resolve()}")
        return receipt
    except (Exception, KeyboardInterrupt) as exc:
        receipt["status"] = "preparation_stopped_requires_review"
        receipt["error"] = str(exc)
        # Preserve the actual failing screen, not only the pre-focus screenshot.
        try:
            failure, _ = capture_observation(driver, receipt_dir, "co.hinge.app")
            receipt["failure_capture"] = str(failure.resolve())
        except Exception as capture_error:
            receipt["failure_capture_error_type"] = type(capture_error).__name__
        print(f"Preparation stopped; evidence: {receipt_dir.resolve()}", file=sys.stderr)
        raise
    finally:
        (receipt_dir / "preparation.json").write_text(json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("drafts", type=Path)
    parser.add_argument("--candidate", required=True, help="c1, c2, c3, or recommended")
    parser.add_argument("--server", default="http://127.0.0.1:4723")
    parser.add_argument("--udid", default="127.0.0.1:6555")
    parser.add_argument("--max-scrolls", type=int, default=30)
    parser.add_argument("--inspect-only", action="store_true", help="Open composer and capture without typing")
    parser.add_argument("--resume-composer", action="store_true", help="Verify an already-open target composer and replace its existing comment")
    args = parser.parse_args()
    if args.max_scrolls < 2:
        parser.error("--max-scrolls must be at least 2")
    driver = None
    try:
        profile, target, candidate = load_selection(args.drafts, args.candidate)
        from appium import webdriver
        from appium.options.android import UiAutomator2Options
        options = UiAutomator2Options().load_capabilities({
            "platformName": "Android", "appium:automationName": "UiAutomator2",
            "appium:udid": args.udid, "appium:noReset": True, "appium:autoLaunch": False})
        driver = webdriver.Remote(args.server, options=options)
        prepare(driver, args.drafts, args.candidate, max_scrolls=args.max_scrolls,
                inspect_only=args.inspect_only, resume_composer=args.resume_composer)
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        print(f"Preparation stopped: {exc}", file=sys.stderr)
        return 1
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception as exc:
                print(f"Session cleanup failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
