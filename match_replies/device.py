"""Exclusive emulator sessions using the profile runner's existing device lock."""
from contextlib import contextmanager
import fcntl
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

@contextmanager
def session(udid='127.0.0.1:6555', server='http://127.0.0.1:4723'):
    from appium import webdriver
    from appium.options.android import UiAutomator2Options
    folder = ROOT / 'captures'
    folder.mkdir(exist_ok=True)
    with (folder / ('runner_' + hashlib.sha256(udid.encode()).hexdigest() + '.lock')).open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Emulator busy. Stop run.py before using match replies.') from None
        driver = None
        try:
            options = UiAutomator2Options().load_capabilities({'platformName':'Android',
                'appium:automationName':'UiAutomator2','appium:udid':udid,
                'appium:noReset':True,'appium:autoLaunch':False})
            driver = webdriver.Remote(server, options=options)
            yield driver
        finally:
            if driver is not None:
                driver.quit()
