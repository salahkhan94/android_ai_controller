"""Print the current Hinge profile's written prompt titles and responses.

Parse the English prompt accessibility descriptions in Appium's XML, scroll
from top to bottom, and deduplicate repeated text across viewports. Support
reading only the current viewport, bounded scrolling, observation callbacks,
and an optional return-to-top continuity check used by extract_profile.py.
No screenshots, LLM calls, likes, or messages are produced.
"""

import argparse
import re
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
        if not separator:
            # Polls and other non-written cards also use the Prompt prefix.
            # A title alone is not an authored response we can comment on.
            continue
        if not title.strip() or not response.strip():
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


def profile_content_signature(tree, label):
    # Compare profile content, not collapsible filter/navigation controls.
    # The English accessibility format is the same one used by extraction.
    # Observed transient Hinge banner, not profile-authored content. Match
    # the exact current-name sentence; do not discard arbitrary text nodes.
    transient_banner = label.removeprefix("Skip ") + " shows thoughtful signals"
    # The observed scheduling card is clipped at the viewport bottom. Its fixed
    # labels can appear/disappear while the same profile remains on screen.
    ignored=set()
    for card in tree.iter():
        if not any(c.get('text')=="Let’s get together" for c in card):
            continue
        if not any(c.get('text')=='Choose a time' for c in card.iter()):
            continue
        ignored.update(n for n in card.iter() if n.get('text') in
                       {"Let’s get together", 'Choose a time', 'for', 'our first date'})
    return tuple((n.get("text", ""), n.get("content-desc", "")) for n in tree.iter()
                 if n not in ignored and n.get("package") == "co.hinge.app"
                 and n.get("text", "") != transient_banner
                 and (n.get("text")
                      or n.get("content-desc", "").startswith(("Prompt: ", "Skip "))
                      or n.get("content-desc", "").endswith("'s photo")))

def top_content_matches(initial, final, label):
    """Compare shared visible content when a collapsing banner resizes the list."""
    if profile_content_signature(initial, label)==profile_content_signature(final, label):
        return True
    import copy
    def rect(node):
        nums=re.findall(r'-?\d+', node.get('bounds',''))
        return tuple(map(int,nums)) if len(nums)==4 else None
    def region(root):
        nodes=[n for n in root.iter() if n.get('scrollable')=='true'
               and n.get('class')=='android.view.View' and rect(n)]
        return nodes[0] if len(nodes)==1 else None
    first, last=region(initial),region(final)
    if first is None or last is None: return False
    a,b=rect(first),rect(last)
    if a[0]!=b[0] or a[2:]!=b[2:]: return False
    # Require the same fully exposed first photo and the exact list translation.
    def photo(node):
        return next((n for n in node.iter() if n.get('content-desc','').endswith("'s photo") and rect(n)),None)
    pa,pb=photo(first),photo(last)
    if pa is None or pb is None or pa.get('content-desc')!=pb.get('content-desc'): return False
    ra,rb=rect(pa),rect(pb)
    if (ra[0],ra[2],ra[3]-ra[1])!=(rb[0],rb[2],rb[3]-rb[1]): return False
    if ra[1]-a[1]!=rb[1]-b[1]: return False
    height=min(a[3]-a[1],b[3]-b[1])
    def shared(root):
        root=copy.deepcopy(root)
        content=region(root); area=rect(content)
        for n in content.iter():
            box=rect(n)
            if box and box[1]>=area[1]+height:
                n.attrib.pop('text',None)
                n.attrib.pop('content-desc',None)
        return profile_content_signature(root,label)
    return shared(initial)==shared(final)


def scroll_fingerprint(root):
    """Track geometry/content without counting an autoplay timer as scrolling."""
    rows=[]
    for node in root.iter():
        if node.get('package')!='co.hinge.app':
            continue
        values=[node.get(key,'') for key in ('class','text','content-desc','bounds')]
        if re.fullmatch(r'Elapsed time: [0-9]+ seconds?', values[2]):
            values[2]='Elapsed time: <timer>'
        rows.append(tuple(values))
    return tuple(rows)


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
        moved = scroll_fingerprint(previous) != scroll_fingerprint(current)
        if not moved:
            # Confirm a suspected boundary by observing again, not by issuing
            # another futile swipe against the end of the list.
            time.sleep(.3)
            settled, _ = read_profile(driver, label)
            return settled, scroll_fingerprint(current) != scroll_fingerprint(settled)
        return current, True

    # Start at the top so output follows profile order, regardless of initial position.
    unchanged = 0
    for _ in range(max_scrolls):
        root, more = scroll("up")
        unchanged = 0 if more else unchanged + 1
        if unchanged >= 1:
            break
    else:
        raise RuntimeError("Scroll limit reached before profile top; results incomplete.")

    prompts = list(extract_prompts(root))
    initial_top = root
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
        if unchanged >= 1:
            if verify_return:
                stationary = 0
                for _ in range(max_scrolls):
                    top, moved = scroll("up")
                    stationary = 0 if moved else stationary + 1
                    if stationary >= 1:
                        if on_observation:
                            on_observation(top)
                        if not top_content_matches(initial_top, top, label):
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
