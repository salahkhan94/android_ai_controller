"""Capture the current Hinge screen without taps, typing, or scrolling."""

import argparse
import sys

from observation import capture_observation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default="http://127.0.0.1:4723")
    parser.add_argument("--udid", default="127.0.0.1:6555")
    parser.add_argument("--package", default="co.hinge.app")
    parser.add_argument("--output", default="captures")
    parser.add_argument("--now", action="store_true", help="Capture without waiting for Enter")
    args = parser.parse_args()
    try:
        from appium import webdriver
        from appium.options.android import UiAutomator2Options
    except ImportError:
        parser.exit(1, "Install dependencies: python -m pip install -r requirements.txt\n")

    driver = None
    try:
        options = UiAutomator2Options()
        options.platform_name = "Android"
        options.automation_name = "UiAutomator2"
        options.device_name = args.udid
        options.udid = args.udid
        options.no_reset = True
        options.set_capability("appium:autoLaunch", False)
        options.set_capability("appium:newCommandTimeout", 600)
        driver = webdriver.Remote(args.server, options=options)
        if not args.now:
            input("Open a Hinge profile manually with a written prompt visible. "
                  "Stop scrolling, then press Enter to capture: ")
        folder, metadata = capture_observation(driver, args.output, args.package)
        print(f"Saved observation: {folder.resolve()}")
        print(f"Consistency: {metadata['consistency']}; screen type requires inspection.")
        if metadata["consistency"] == "changed":
            print("Screen changed during capture. Keep still and recapture before using it.")
            return 2
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        print(f"Capture failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception as exc:
                print(f"Session cleanup failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
