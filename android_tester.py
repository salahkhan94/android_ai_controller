"""Smoke-test the Appium connection to the Android emulator.

Launch Hinge while preserving its login data, wait for it to open, and print
the current package, activity, and XML UI hierarchy. Keep the session open
until Enter is pressed, then disconnect. No likes or messages are sent.
"""

from appium import webdriver
from appium.options.android import UiAutomator2Options
import time

DEVICE_UDID = "127.0.0.1:6555"
HINGE_PACKAGE = "co.hinge.app"

options = UiAutomator2Options()
options.platform_name = "Android"
options.automation_name = "UiAutomator2"
options.device_name = DEVICE_UDID
options.udid = DEVICE_UDID

# Don't reset app data/login state.
options.no_reset = True

driver = webdriver.Remote(
    "http://127.0.0.1:4723",
    options=options,
)

try:
    print("Connected to Android.")

    print("Launching Hinge...")
    driver.activate_app(HINGE_PACKAGE)

    time.sleep(5)

    print("Current package:", driver.current_package)
    print("Current activity:", driver.current_activity)

    print("\nUI hierarchy:")
    print(driver.page_source)

    input("\nHinge should now be open. Press Enter to quit...")

finally:
    driver.quit()
