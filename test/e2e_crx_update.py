#!/usr/bin/env python3
"""Tests Update Guard against a real packed-extension update.

e2e.py fakes updates by reloading an unpacked extension. This test goes through
Chromium's actual updater instead: a packed "victim" extension (CRX) is installed
from a local update server, the server then offers a newer version, and Chromium
downloads and installs it.

Chromium only installs a CRX from a private server without prompts when a policy
says so. So this test writes /etc/chromium/policies/managed/update-guard-test.json
with sudo and removes it at the end. That policy applies to every Chromium started
on this machine while the test runs (not to Google Chrome).

Needs the Debian packages chromium, chromium-driver, python3-selenium, xvfb, openssl.

Run:  xvfb-run -a python3 test/e2e_crx_update.py [folder for screenshots]
"""

import functools
import hashlib
import http.server
import json
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e import GUARD_DIR, SHOTS, call, passed, wait_for, write_victim  # noqa: E402

POLICY_FILE = Path("/etc/chromium/policies/managed/update-guard-test.json")
EXTENSIONS_INFO = (
    "new Promise((r) => chrome.developerPrivate.getExtensionsInfo({includeDisabled: true, includeTerminated: true}, r))"
)


def extension_id(key_file):
    """The ID Chromium derives from the extension's public key."""
    der = subprocess.run(
        ["openssl", "rsa", "-in", str(key_file), "-pubout", "-outform", "DER"],
        check=True,
        capture_output=True,
    ).stdout
    digest = hashlib.sha256(der).hexdigest()[:32]
    return "".join(chr(ord("a") + int(char, 16)) for char in digest)


def publish(work, site, key_file, version, update_url, **victim_options):
    """Packs the victim at this version and makes the update server offer it. Returns the key file."""
    source = work / "victim"
    write_victim(source, version, **victim_options)
    manifest = json.loads((source / "manifest.json").read_text())
    manifest["update_url"] = update_url
    (source / "manifest.json").write_text(json.dumps(manifest, indent=2))

    command = ["chromium", f"--user-data-dir={work / 'packer-profile'}", f"--pack-extension={source}"]
    if key_file.exists():
        command.append(f"--pack-extension-key={key_file}")
    subprocess.run(command, check=True, capture_output=True, timeout=60)
    if not key_file.exists():
        shutil.move(work / "victim.pem", key_file)
    shutil.move(work / "victim.crx", site / f"victim-{version}.crx")

    base = update_url.rsplit("/", 1)[0]
    (site / "updates.xml").write_text(
        "<?xml version='1.0' encoding='UTF-8'?>\n"
        "<gupdate xmlns='http://www.google.com/update2/response' protocol='2.0'>\n"
        f"  <app appid='{extension_id(key_file)}'>\n"
        f"    <updatecheck codebase='{base}/victim-{version}.crx' version='{version}' />\n"
        "  </app>\n"
        "</gupdate>\n"
    )


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class Browser:
    """A Chromium with Update Guard loaded and the victim installed by policy from the update server."""

    def __init__(self, profile, victim, update_url, installation_mode):
        policy = {"ExtensionSettings": {victim: {"installation_mode": installation_mode, "update_url": update_url}}}
        subprocess.run(["sudo", "mkdir", "-p", str(POLICY_FILE.parent)], check=True)
        subprocess.run(["sudo", "tee", str(POLICY_FILE)], input=json.dumps(policy).encode(), check=True, capture_output=True)

        options = Options()
        options.binary_location = "/usr/bin/chromium"
        options.add_argument(f"--user-data-dir={profile}")
        options.add_argument(f"--load-extension={GUARD_DIR}")
        options.add_argument("--no-first-run")
        # chromedriver normally turns the extension updater off with this switch.
        options.add_experimental_option("excludeSwitches", ["disable-background-networking"])
        self.victim = victim
        self.driver = webdriver.Chrome(options=options, service=Service("/usr/bin/chromedriver"))
        self.driver.get("chrome://extensions")
        self.extensions_tab = self.driver.current_window_handle
        infos = wait_for(
            "the victim CRX to be installed from the update server",
            lambda: (lambda found: found if any(i["id"] == victim for i in found) else None)(
                call(self.driver, EXTENSIONS_INFO)
            ),
            timeout=60,
        )
        guard = next(info["id"] for info in infos if info["name"] == "Update Guard")
        self.driver.switch_to.new_window("tab")
        self.driver.get(f"chrome-extension://{guard}/review.html")
        self.review_tab = self.driver.current_window_handle
        wait_for("baseline", lambda: self.state().get("baseline", {}).get(victim))

    def state(self):
        self.driver.switch_to.window(self.review_tab)
        return call(self.driver, "chrome.storage.local.get()")

    def info(self):
        self.driver.switch_to.window(self.review_tab)
        return call(self.driver, f"chrome.management.get('{self.victim}')")

    def held(self):
        return self.state().get("pending", {}).get(self.victim)

    def check_for_updates(self):
        """Asks Chromium to check for extension updates now, as it does by itself every few hours."""
        self.driver.switch_to.window(self.extensions_tab)
        call(self.driver, "new Promise((r) => chrome.developerPrivate.autoUpdate(r))")
        self.driver.switch_to.window(self.review_tab)

    def wait_held(self, version):
        return wait_for(
            f"the update to {version} to wait for review",
            lambda: (lambda held: held if held and held["to"]["version"] == version else None)(self.held()),
            timeout=60,
        )

    def card(self):
        self.driver.switch_to.window(self.review_tab)
        return wait_for(
            "review card", lambda: self.driver.find_elements(By.CSS_SELECTOR, f"article[data-id='{self.victim}']")
        )[0]

    def quit(self):
        self.driver.quit()


def main():
    work = Path(tempfile.mkdtemp(prefix="update-guard-crx-test-"))
    site = work / "site"
    site.mkdir()
    key_file = work / "key.pem"
    more_access = {"permissions": ("storage", "tabs"), "hosts": ("https://*/*",)}

    handler = functools.partial(QuietHandler, directory=str(site))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    update_url = f"http://127.0.0.1:{server.server_port}/updates.xml"

    publish(work, site, key_file, "1.0.0", update_url)
    victim = extension_id(key_file)

    browser = None
    try:
        # --- 1. an ordinary update ----------------------------------------------
        browser = Browser(work / "profile-1", victim, update_url, "normal_installed")
        first = browser.info()
        print(f"      installed: version {first['version']}, installType {first['installType']}, "
              f"mayDisable {first['mayDisable']}")
        assert first["version"] == "1.0.0" and first["enabled"] and first["mayDisable"]
        assert browser.state()["baseline"][victim]["version"] == "1.0.0"
        passed("packed extension 1.0.0 installed from the update server and recorded")

        publish(work, site, key_file, "1.1.0", update_url)
        browser.check_for_updates()
        held = browser.wait_held("1.1.0")
        wait_for("victim off", lambda: not browser.info()["enabled"])
        assert held["from"]["version"] == "1.0.0" and held["disabledByGuard"] is True, held
        assert call(browser.driver, "chrome.action.getBadgeText({})") == "1"
        assert victim in call(browser.driver, "chrome.notifications.getAll()")
        assert "Turned off by Update Guard" in browser.card().text
        assert not browser.card().find_elements(By.CSS_SELECTOR, "button.danger"), "no uninstall button"
        passed("real CRX update 1.0.0 -> 1.1.0 (same permissions) was turned off by Update Guard")

        browser.card().find_element(By.CSS_SELECTOR, "button.primary").click()
        wait_for("approved and on", lambda: browser.info()["enabled"] and not browser.held())
        passed("approved: 1.1.0 runs again")

        # --- 2. an update that asks for more access -------------------------------
        publish(work, site, key_file, "1.2.0", update_url, **more_access)
        browser.check_for_updates()
        held = browser.wait_held("1.2.0")
        info = browser.info()
        print(f"      after update: enabled {info['enabled']}, disabledReason {info.get('disabledReason')}, "
              f"disabledByGuard {held['disabledByGuard']}")
        print("      permissions reported:", held["from"]["permissions"], "->", held["to"]["permissions"])
        print("      site access reported:", held["from"]["hostPermissions"], "->", held["to"]["hostPermissions"])
        assert not info["enabled"] and info.get("disabledReason") == "permissions_increase", info
        assert "tabs" in held["to"]["permissions"] and held["to"]["hostPermissions"] == ["https://*/*"]
        assert victim in call(browser.driver, "chrome.notifications.getAll()")
        assert browser.state()["log"][-1]["event"] == "more-access"
        card = browser.card()
        assert "Turned off by Chrome" in card.text and "tabs" in card.text, card.text
        assert card.find_element(By.CSS_SELECTOR, "button.primary").text == "Approve and turn on"
        if SHOTS:
            browser.driver.save_screenshot(str(SHOTS / "4-more-access.png"))
        passed("real CRX update 1.1.0 -> 1.2.0 asking for more access: off, listed with the new access, notified")
        browser.quit()

        # --- 3. an extension forced by policy can't be turned off ------------------
        browser = Browser(work / "profile-2", victim, update_url, "force_installed")
        first = browser.info()
        assert first["version"] == "1.2.0" and first["enabled"] and not first["mayDisable"], first
        publish(work, site, key_file, "1.3.0", update_url, **more_access)
        browser.check_for_updates()
        held = browser.wait_held("1.3.0")
        assert browser.info()["enabled"] and held["disabledByGuard"] is False
        assert "still-running" in [entry["event"] for entry in browser.state()["log"]]
        assert victim in call(browser.driver, "chrome.notifications.getAll()")
        card = browser.card()
        assert "Still running" in card.text and "policy" in card.text, card.text
        button = card.find_element(By.CSS_SELECTOR, "button.primary")
        assert button.text == "Mark as reviewed", button.text
        assert not card.find_elements(By.CSS_SELECTOR, "button.danger"), "no uninstall button"
        if SHOTS:
            browser.driver.save_screenshot(str(SHOTS / "5-policy-installed.png"))
        button.click()
        wait_for("marked as reviewed", lambda: not browser.held())
        assert browser.state()["baseline"][victim]["version"] == "1.3.0" and browser.info()["enabled"]
        passed("policy-forced extension: update can't be turned off, user is told, 'Mark as reviewed' clears it")

        print("\nALL PASSED")
    finally:
        if browser:
            browser.quit()
        subprocess.run(["sudo", "rm", "-f", str(POLICY_FILE)], check=False)
        # Remove the policy folders too if this test created them and they are empty.
        subprocess.run(["sudo", "rmdir", str(POLICY_FILE.parent), str(POLICY_FILE.parent.parent)],
                       check=False, capture_output=True)
        server.shutdown()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
