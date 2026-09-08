"""Print the current Hinge profile's written prompt titles and responses.

Parse the English prompt accessibility descriptions in Appium's XML, scroll
from top to bottom, and deduplicate repeated text across viewports. Support
reading only the current viewport, bounded scrolling, observation callbacks,
and an optional return-to-top continuity check used by extract_profile.py.
No screenshots, LLM calls, likes, or messages are produced.
"""

import argparse
from dataclasses import dataclass
import sys
import time
import xml.etree.ElementTree as ET


@dataclass(frozen=True)
class Prompt:
    title: str
    response: str


def extract_prompts(root):
    """Parse the accessibility format observed in Hinge's English UI."""
    prompts = []
    for node in root.iter():
        description = node.get("content-desc", "")
        if not description.startswith("Prompt: "):
            continue
        title, separator, response = description[len("Prompt: "):].partition(". Answer: ")
        if not separator or not title.strip() or not response.strip():
            raise ValueError("Unrecognized or empty prompt description; extraction incomplete.")
        prompt = Prompt(title.strip(), response.strip())
        if prompt not in prompts:
            prompts.append(prompt)
    return prompts


def read_profile(driver, expected_label=None):
    if driver.current_package != "co.hinge.app":
        raise RuntimeError("Open a Hinge Discover profile before running this script.")
    root = ET.fromstring(driver.page_source)
    labels = {n.get("content-desc") for n in root.iter()
              if n.get("class") == "android.widget.Button"
              and n.get("content-desc", "").startswith("Skip ")}
    if len(labels) != 1:
        raise RuntimeError("Cannot identify the current Discover profile; stopping.")
    label = labels.pop()
    if expected_label is not None and label != expected_label:
        raise RuntimeError("Profile changed during reading; stopping.")
    return root, label


def collect_prompts(driver, max_scrolls=30, visible_only=False, on_observation=None,
                    verify_return=False):
    root, label = read_profile(driver)
    if visible_only:
        if on_observation:
            on_observation(root)
        return extract_prompts(root)

    size = driver.get_window_size()
    # Central vertical region avoids the header, navigation, skip and like controls.
    region = {"left": int(size["width"] * .25), "top": int(size["height"] * .2),
              "width": int(size["width"] * .5), "height": int(size["height"] * .55)}

    def scroll(direction):
        previous, _ = read_profile(driver, label)
        driver.execute_script("mobile: swipeGesture", {
            **region, "direction": "down" if direction == "up" else "up",
            "percent": .65, "speed": 400,
        })
        time.sleep(.3)
        current, _ = read_profile(driver, label)
        # Hinge's Compose UI reported false scroll boundaries in live testing.
        # Compare app content/bounds instead; ignore system clock and window IDs.
        def fingerprint(root):
            return tuple(tuple(n.get(key, "") for key in
                               ("class", "text", "content-desc", "bounds"))
                         for n in root.iter() if n.get("package") == "co.hinge.app")
        return current, fingerprint(previous) != fingerprint(current)

    # Start at the top so output follows profile order, regardless of initial position.
    unchanged = 0
    for _ in range(max_scrolls):
        root, more = scroll("up")
        unchanged = 0 if more else unchanged + 1
        if unchanged >= 2:
            break
    else:
        raise RuntimeError("Scroll limit reached before profile top; results incomplete.")

    prompts = list(extract_prompts(root))
    def content_signature(tree):
        # Compare profile content, not collapsible filter/navigation controls.
        # The English accessibility format is the same one used by extraction.
        return tuple((n.get("text", ""), n.get("content-desc", "")) for n in tree.iter()
                     if n.get("package") == "co.hinge.app"
                     and (n.get("text")
                          or n.get("content-desc", "").startswith(("Prompt: ", "Skip "))
                          or n.get("content-desc", "").endswith("'s photo")))
    initial_signature = content_signature(root)
    if on_observation:
        on_observation(root)
    unchanged = 0
    for _ in range(max_scrolls):
        root, more = scroll("down")
        if on_observation:
            on_observation(root)
        # Read every viewport, including the one at the apparent bottom.
        for prompt in extract_prompts(root):
            if prompt not in prompts:
                prompts.append(prompt)
        unchanged = 0 if more else unchanged + 1
        if unchanged >= 2:
            if verify_return:
                stationary = 0
                for _ in range(max_scrolls):
                    top, moved = scroll("up")
                    stationary = 0 if moved else stationary + 1
                    if stationary >= 2:
                        if content_signature(top) != initial_signature:
                            raise RuntimeError("Profile top content changed; scan identity is uncertain.")
                        break
                else:
                    raise RuntimeError("Could not return to profile top for continuity verification.")
            return prompts
    raise RuntimeError("Scroll limit reached before profile bottom; results incomplete.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default="http://127.0.0.1:4723")
    parser.add_argument("--udid", default="127.0.0.1:6555")
    parser.add_argument("--visible-only", action="store_true", help="Read current XML without scrolling")
    parser.add_argument("--max-scrolls", type=int, default=30, help="Maximum gestures per direction")
    args = parser.parse_args()
    if args.max_scrolls < 1:
        parser.error("--max-scrolls must be positive")
    try:
        from appium import webdriver
        from appium.options.android import UiAutomator2Options
    except ImportError:
        parser.exit(1, "Install dependencies: python -m pip install -r requirements.txt\n")

    driver = None
    try:
        options = UiAutomator2Options().load_capabilities({
            "platformName": "Android", "appium:automationName": "UiAutomator2",
            "appium:udid": args.udid, "appium:noReset": True, "appium:autoLaunch": False,
        })
        driver = webdriver.Remote(args.server, options=options)
        print("Reading current profile XML... Keep the emulator untouched.", file=sys.stderr)
        prompts = collect_prompts(driver, args.max_scrolls, args.visible_only)
        print(f"Found {len(prompts)} written prompt(s)" +
              (" in the current viewport's XML." if args.visible_only else " across the profile's XML."))
        for index, prompt in enumerate(prompts, 1):
            print(f"\n{index}. {prompt.title}\n   {prompt.response}")
        if not prompts:
            print("No supported written-prompt descriptions were exposed.")
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        print(f"Could not complete profile reading: {exc}", file=sys.stderr)
        return 1
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception as exc:
                print(f"Session cleanup failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
