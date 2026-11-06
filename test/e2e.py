#!/usr/bin/env python3
"""End-to-end test of Update Guard in a real Chromium.

It loads Update Guard next to a throwaway "victim" extension, "updates" the
victim by raising its version on disk and reloading it, and checks what Update
Guard does about it.

Needs the Debian packages chromium, chromium-driver, python3-selenium and xvfb.
(Google Chrome itself no longer accepts --load-extension, so this uses Chromium.)

Run:  xvfb-run -a python3 test/e2e.py [folder for screenshots]
"""

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select

GUARD_DIR = Path(__file__).resolve().parent.parent / "extension"
SHOTS = Path(sys.argv[1]) if len(sys.argv) > 1 else None


def write_victim(folder, version, permissions=("storage",), hosts=()):
    folder.mkdir(exist_ok=True)
    shutil.copy(GUARD_DIR / "icons" / "icon48.png", folder / "icon.png")
    (folder / "sw.js").write_text("// does nothing\n")
    manifest = {
        "manifest_version": 3,
        "name": "Test Victim",
        "version": version,
        "description": "Stands in for an extension that updates.",
        "icons": {"48": "icon.png"},
        "background": {"service_worker": "sw.js"},
        "permissions": list(permissions),
        "host_permissions": list(hosts),
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2))


def launch(profile, extensions):
    options = Options()
    options.binary_location = "/usr/bin/chromium"
    options.add_argument(f"--user-data-dir={profile}")
    options.add_argument("--load-extension=" + ",".join(str(path) for path in extensions))
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--window-size=1000,1100")
    return webdriver.Chrome(options=options, service=Service("/usr/bin/chromedriver"))


def call(driver, expression):
    """Evaluates a JavaScript expression in the current page and waits for its promise."""
    result = driver.execute_async_script(
        "const done = arguments[arguments.length - 1];"
        f"Promise.resolve().then(() => ({expression}))"
        ".then((value) => done({value}), (err) => done({error: String(err && err.message || err)}));"
    )
    if "error" in result:
        raise RuntimeError(f"{expression}: {result['error']}")
    return result.get("value")


def wait_for(what, check, timeout=20):
    deadline = time.time() + timeout
    while True:
        value = check()
        if value:
            return value
        if time.time() > deadline:
            raise AssertionError(f"timed out waiting for: {what}")
        time.sleep(0.25)


def passed(text):
    print(f"PASS  {text}", flush=True)


class Session:
    """One browser run with a chrome://extensions tab and an Update Guard review tab."""

    def __init__(self, profile, extensions):
        self.driver = launch(profile, extensions)
        self.driver.get("chrome://extensions")
        self.extensions_tab = self.driver.current_window_handle
        # Without developer mode Chromium turns an unpacked extension off when it is reloaded.
        call(
            self.driver,
            "new Promise((r) => chrome.developerPrivate.updateProfileConfiguration({inDeveloperMode: true}, r))",
        )
        infos = wait_for(
            "extensions to load",
            lambda: (lambda found: found if any(i["name"] == "Update Guard" for i in found) else None)(
                call(
                    self.driver,
                    "new Promise((r) => chrome.developerPrivate.getExtensionsInfo("
                    "{includeDisabled: true, includeTerminated: true}, r))",
                )
            ),
        )
        self.ids = {info["name"]: info["id"] for info in infos}
        self.guard = self.ids["Update Guard"]
        self.victim = self.ids.get("Test Victim")
        self.driver.switch_to.new_window("tab")
        self.driver.get(f"chrome-extension://{self.guard}/review.html")
        self.review_tab = self.driver.current_window_handle

    def state(self):
        self.driver.switch_to.window(self.review_tab)
        return call(self.driver, "chrome.storage.local.get()")

    def victim_info(self):
        self.driver.switch_to.window(self.review_tab)
        return call(self.driver, f"chrome.management.get('{self.victim}')")

    def reload_victim(self):
        self.driver.switch_to.window(self.extensions_tab)
        call(
            self.driver,
            f"new Promise((ok, fail) => chrome.developerPrivate.reload('{self.victim}', {{failQuietly: true}}, "
            "() => chrome.runtime.lastError ? fail(chrome.runtime.lastError.message) : ok()))",
        )
        self.driver.switch_to.window(self.review_tab)

    def stop_guard_worker(self):
        """Stops Update Guard's service worker, as Chrome does after 30 s of idleness.

        (Under chromedriver the worker never idles out by itself.)
        """
        self.driver.switch_to.new_window("tab")
        self.driver.get("chrome://serviceworker-internals")
        wait_for(
            "the Stop button for Update Guard's worker",
            lambda: self.driver.execute_script(
                """
                const scope = 'Scope: chrome-extension://' + arguments[0] + '/';
                for (const button of document.querySelectorAll('cr-button[data-command="stop"]')) {
                  let item = button;
                  while (item && !item.innerText.includes('Scope: ')) item = item.parentElement;
                  if (item && item.innerText.includes(scope)) { button.click(); return true; }
                }
                return false;
                """,
                self.guard,
            ),
        )
        self.driver.close()
        self.driver.switch_to.window(self.review_tab)
        wait_for(
            "Update Guard's worker to stop",
            lambda: not call(self.driver, "chrome.runtime.getContexts({contextTypes: ['BACKGROUND']})"),
        )

    def wait_held(self, version):
        wait_for(
            f"victim {version} to be held and off",
            lambda: self.state().get("pending", {}).get(self.victim, {}).get("to", {}).get("version") == version
            and not self.victim_info()["enabled"],
        )

    def events(self):
        return [entry["event"] for entry in self.state().get("log", [])]

    def shot(self, name):
        if SHOTS:
            self.driver.switch_to.window(self.review_tab)
            time.sleep(0.5)
            self.driver.save_screenshot(str(SHOTS / f"{name}.png"))

    def quit(self):
        self.driver.quit()


def main():
    work = Path(tempfile.mkdtemp(prefix="update-guard-test-"))
    profile = work / "profile"
    victim_dir = work / "victim"
    write_victim(victim_dir, "1.0.0")
    session = Session(profile, [GUARD_DIR, victim_dir])
    try:
        driver, victim = session.driver, session.victim

        # --- first run -------------------------------------------------------
        wait_for("baseline", lambda: session.state().get("baseline", {}).get(victim))
        state = session.state()
        assert state["baseline"][victim]["version"] == "1.0.0"
        assert not state.get("pending")
        assert session.guard not in state["baseline"], "Update Guard must not watch itself"
        passed("first run records installed versions and holds nothing")

        row = driver.find_element(By.CSS_SELECTOR, f"tr[data-id='{victim}']")
        assert "Not held (unpacked)" in row.text, row.text
        icon = row.find_element(By.CSS_SELECTOR, "img.icon")
        wait_for("victim icon to load", lambda: driver.execute_script("return arguments[0].naturalWidth", icon) > 0)
        passed("review page lists the extension with its icon; unpacked extensions are not held by default")

        # --- unpacked update is let through by default ---------------------------
        write_victim(victim_dir, "1.0.1")
        session.reload_victim()
        wait_for("1.0.1 allowed", lambda: session.state()["baseline"][victim]["version"] == "1.0.1")
        assert session.victim_info()["enabled"] and not session.state().get("pending")
        passed("with the default setting an unpacked extension's update is allowed and logged")

        driver.find_element(By.ID, "guard-unpacked").click()
        wait_for("setting saved", lambda: session.state().get("settings", {}).get("guardUnpacked"))
        session.shot("1-nothing-waiting")

        # --- an update is turned off -----------------------------------------
        write_victim(victim_dir, "1.1.0", permissions=("storage", "tabs"), hosts=("https://*/*",))
        session.reload_victim()
        session.wait_held("1.1.0")
        held = session.state()["pending"][victim]
        assert held["from"]["version"] == "1.0.1" and held["disabledByGuard"] is True, held
        print("      permissions reported:", held["from"]["permissions"], "->", held["to"]["permissions"])
        print("      site access reported:", held["from"]["hostPermissions"], "->", held["to"]["hostPermissions"])
        assert "tabs" in held["to"]["permissions"] and "tabs" not in held["from"]["permissions"]
        assert held["to"]["hostPermissions"] and not held["from"]["hostPermissions"]
        passed("update 1.0.1 -> 1.1.0 is turned off and waits for review, with its new permissions recorded")

        assert call(driver, "chrome.action.getBadgeText({})") == "1"
        notifications = call(driver, "chrome.notifications.getAll()")
        assert victim in notifications, notifications
        passed("toolbar badge shows 1 and a notification is up")

        card = wait_for("review card", lambda: driver.find_elements(By.CSS_SELECTOR, f"article[data-id='{victim}']"))[0]
        assert "tabs" in card.text and "Turned off by Update Guard" in card.text, card.text
        session.shot("2-waiting-for-review")
        passed("review page shows the held update and what changed")

        # --- turning it on without approving doesn't stick ---------------------
        call(driver, f"chrome.management.setEnabled('{victim}', true)")
        wait_for("held again", lambda: "held-again" in session.events() and not session.victim_info()["enabled"])
        passed("turned on without approval -> turned off again")

        # --- approve ---------------------------------------------------------
        card = driver.find_element(By.CSS_SELECTOR, f"article[data-id='{victim}']")
        button = card.find_element(By.CSS_SELECTOR, "button.primary")
        assert button.text == "Approve and turn on", button.text
        button.click()
        wait_for("approved and on", lambda: session.victim_info()["enabled"] and not session.state().get("pending"))
        time.sleep(1)  # anything that would turn it off again has had its chance
        assert session.victim_info()["enabled"]
        state = session.state()
        assert state["baseline"][victim]["version"] == "1.1.0"
        assert call(driver, "chrome.action.getBadgeText({})") == ""
        assert victim not in call(driver, "chrome.notifications.getAll()")
        assert "approved" in session.events()
        passed("Approve and turn on: extension runs again, badge and notification cleared")

        # --- an update while Update Guard's worker is asleep -------------------
        session.stop_guard_worker()
        write_victim(victim_dir, "1.2.0", permissions=("storage", "tabs"), hosts=("https://*/*",))
        session.reload_victim()
        session.wait_held("1.2.0")
        passed("update while the service worker was stopped wakes it and is held")

        # --- "Allow without asking" ------------------------------------------
        select = Select(driver.find_element(By.CSS_SELECTOR, f"tr[data-id='{victim}'] select"))
        select.select_by_value("allow")
        wait_for("released", lambda: session.victim_info()["enabled"] and not session.state().get("pending"))
        assert session.state()["baseline"][victim]["version"] == "1.2.0"
        write_victim(victim_dir, "1.3.0", permissions=("storage", "tabs"), hosts=("https://*/*",))
        session.reload_victim()
        wait_for("1.3.0 allowed", lambda: session.state()["baseline"][victim]["version"] == "1.3.0")
        time.sleep(1)
        assert session.victim_info()["enabled"] and not session.state().get("pending")
        assert session.events().count("auto-approved") == 3, session.events()
        passed("'Allow without asking' releases the held update and lets later updates through")

        select = Select(driver.find_element(By.CSS_SELECTOR, f"tr[data-id='{victim}'] select"))
        select.select_by_value("hold")
        wait_for("hold again", lambda: victim not in session.state()["settings"]["trusted"])
        session.shot("3-history")

        # --- an update that happens while the browser is closed ----------------
        session.quit()
        write_victim(victim_dir, "1.4.0", permissions=("storage", "tabs"), hosts=("https://*/*",))
        session = Session(profile, [GUARD_DIR, victim_dir])
        assert session.victim == victim
        session.wait_held("1.4.0")
        assert session.state()["pending"][victim]["from"]["version"] == "1.3.0"
        assert call(session.driver, "chrome.action.getBadgeText({})") == "1"
        passed("update applied while the browser was closed is held at startup")

        # --- the extension disappears while waiting ----------------------------
        session.quit()
        session = Session(profile, [GUARD_DIR])
        wait_for("victim forgotten", lambda: "removed" in session.events())
        state = session.state()
        assert not state.get("pending") and victim not in state["baseline"]
        assert call(session.driver, "chrome.action.getBadgeText({})") == ""
        passed("an extension removed while waiting is dropped from the list")

        print("\nALL PASSED")
    finally:
        session.quit()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
