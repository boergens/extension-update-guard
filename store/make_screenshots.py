#!/usr/bin/env python3
"""Makes the Chrome Web Store screenshots (1280x800) from the real extension.

It installs three made-up sample extensions from a local update server, lets two
of them update, and photographs what Update Guard shows. Like
test/e2e_crx_update.py it needs sudo for a temporary Chromium policy file
(/etc/chromium/policies/managed/update-guard-test.json), which it removes again.

Run on a 1280x800 screen:
    xvfb-run -a -s "-screen 0 1280x800x24" python3 store/make_screenshots.py
"""

import functools
import http.server
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "test"))
from e2e import GUARD_DIR, call, wait_for  # noqa: E402
from e2e_crx_update import EXTENSIONS_INFO, POLICY_FILE, QuietHandler, extension_id  # noqa: E402

OUT = Path(__file__).resolve().parent / "screenshots"

# Made-up extensions, not real products.
SAMPLES = {
    "tab-sorter": {"name": "Tab Sorter", "color": "#0f9d58", "glyph": "T", "version": "2.3.1", "permissions": ["tabs"]},
    "quick-notes": {"name": "Quick Notes", "color": "#f4a100", "glyph": "N", "version": "1.8.0", "permissions": ["storage"]},
    "page-ruler": {"name": "Page Ruler", "color": "#7b3fe4", "glyph": "R", "version": "3.0.2", "permissions": ["activeTab"]},
}


def publish(work, site, base_url, slug, version, permissions, hosts=()):
    """Packs one sample extension at this version and offers it on the update server."""
    sample = SAMPLES[slug]
    source = work / slug
    source.mkdir(exist_ok=True)
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 128 128'>"
        f"<rect x='12' y='12' width='104' height='104' rx='22' fill='{sample['color']}'/>"
        "<text x='64' y='90' font-family='DejaVu Sans, sans-serif' font-size='72' font-weight='bold' "
        f"text-anchor='middle' fill='#ffffff'>{sample['glyph']}</text></svg>"
    )
    subprocess.run(
        ["rsvg-convert", "-w", "128", "-h", "128", "-o", str(source / "icon.png")], input=svg.encode(), check=True
    )
    manifest = {
        "manifest_version": 3,
        "name": sample["name"],
        "version": version,
        "description": "A made-up extension for Update Guard's screenshots.",
        "icons": {"128": "icon.png"},
        "permissions": list(permissions),
        "host_permissions": list(hosts),
        "update_url": f"{base_url}/{slug}/updates.xml",
    }
    (source / "manifest.json").write_text(json.dumps(manifest, indent=2))

    key_file = work / f"{slug}.key.pem"
    command = ["chromium", f"--user-data-dir={work / 'packer-profile'}", f"--pack-extension={source}"]
    if key_file.exists():
        command.append(f"--pack-extension-key={key_file}")
    subprocess.run(command, check=True, capture_output=True, timeout=60)
    if not key_file.exists():
        shutil.move(work / f"{slug}.pem", key_file)
    (site / slug).mkdir(exist_ok=True)
    shutil.move(work / f"{slug}.crx", site / slug / f"{slug}-{version}.crx")
    (site / slug / "updates.xml").write_text(
        "<?xml version='1.0' encoding='UTF-8'?>\n"
        "<gupdate xmlns='http://www.google.com/update2/response' protocol='2.0'>\n"
        f"  <app appid='{extension_id(key_file)}'>\n"
        f"    <updatecheck codebase='{base_url}/{slug}/{slug}-{version}.crx' version='{version}' />\n"
        "  </app>\n"
        "</gupdate>\n"
    )
    return extension_id(key_file)


def main():
    OUT.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="update-guard-shots-"))
    site = work / "site"
    site.mkdir()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(site)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_port}"

    ids = {slug: publish(work, site, base_url, slug, s["version"], s["permissions"]) for slug, s in SAMPLES.items()}
    policy = {
        "ExtensionSettings": {
            ext_id: {"installation_mode": "normal_installed", "update_url": f"{base_url}/{slug}/updates.xml"}
            for slug, ext_id in ids.items()
        }
    }

    driver = None
    try:
        subprocess.run(["sudo", "mkdir", "-p", str(POLICY_FILE.parent)], check=True)
        subprocess.run(["sudo", "tee", str(POLICY_FILE)], input=json.dumps(policy).encode(), check=True, capture_output=True)

        options = Options()
        options.binary_location = "/usr/bin/chromium"
        options.add_argument(f"--user-data-dir={work / 'profile'}")
        options.add_argument(f"--load-extension={GUARD_DIR}")
        options.add_argument("--no-first-run")
        options.add_argument("--window-position=0,0")
        options.add_argument("--window-size=1280,800")
        # Keep the updater on, and leave out the "controlled by automated test software" bar.
        options.add_experimental_option("excludeSwitches", ["disable-background-networking", "enable-automation"])
        driver = webdriver.Chrome(options=options, service=Service("/usr/bin/chromedriver"))

        driver.get("chrome://extensions")
        extensions_tab = driver.current_window_handle
        infos = wait_for(
            "the sample extensions to be installed",
            lambda: (lambda found: found if set(ids.values()) <= {i["id"] for i in found} else None)(
                call(driver, EXTENSIONS_INFO)
            ),
            timeout=60,
        )
        guard = next(info["id"] for info in infos if info["name"] == "Update Guard")
        call(
            driver,
            "new Promise((r) => chrome.developerPrivate.updateExtensionConfiguration("
            f"{{extensionId: '{guard}', pinnedToToolbar: true}}, r))",
        )
        driver.get(f"chrome-extension://{guard}/review.html")

        def state():
            return call(driver, "chrome.storage.local.get()")

        wait_for("baseline", lambda: set(ids.values()) <= set(state().get("baseline", {})))

        # Two updates arrive: one asks for much more access, one changes nothing visible.
        publish(work, site, base_url, "tab-sorter", "2.4.0", ["tabs", "cookies", "webRequest"], hosts=["<all_urls>"])
        publish(work, site, base_url, "quick-notes", "1.9.0", ["storage"])
        driver.switch_to.new_window("tab")
        driver.get("chrome://extensions")
        call(driver, "new Promise((r) => chrome.developerPrivate.autoUpdate(r))")
        driver.close()
        driver.switch_to.window(extensions_tab)
        wait_for(
            "both updates to wait for review",
            lambda: {ids["tab-sorter"], ids["quick-notes"]} <= set(state().get("pending", {})),
            timeout=60,
        )
        driver.find_element(By.TAG_NAME, "h1").click()  # take the focus out of the address bar
        time.sleep(2)

        # 1. The whole browser window: review page and toolbar badge.
        subprocess.run(["import", "-window", "root", str(OUT / "1-updates-held.png")], check=True)

        # 2. The extension list and history, after approving one and exempting another.
        card = driver.find_element(By.CSS_SELECTOR, f"article[data-id='{ids['quick-notes']}']")
        card.find_element(By.CSS_SELECTOR, "button.primary").click()
        wait_for("Quick Notes approved", lambda: ids["quick-notes"] not in state().get("pending", {}))
        Select(driver.find_element(By.CSS_SELECTOR, f"tr[data-id='{ids['page-ruler']}'] select")).select_by_value("allow")
        wait_for("Page Ruler exempt", lambda: ids["page-ruler"] in state()["settings"]["trusted"])
        call(driver, "chrome.notifications.getAll().then((all) => Promise.all(Object.keys(all).map((id) => chrome.notifications.clear(id))))")
        time.sleep(1)
        driver.execute_script("document.querySelector('table').scrollIntoView({block: 'center'})")
        time.sleep(1)
        subprocess.run(["import", "-window", "root", str(OUT / "2-extension-list.png")], check=True)

        for shot in sorted(OUT.glob("*.png")):
            size = subprocess.run(["identify", "-format", "%wx%h", str(shot)], capture_output=True, text=True).stdout
            print(shot.name, size)
    finally:
        if driver:
            driver.quit()
        subprocess.run(["sudo", "rm", "-f", str(POLICY_FILE)], check=False)
        subprocess.run(
            ["sudo", "rmdir", str(POLICY_FILE.parent), str(POLICY_FILE.parent.parent)], check=False, capture_output=True
        )
        server.shutdown()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
