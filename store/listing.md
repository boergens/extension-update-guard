# Chrome Web Store submission: Update Guard 1.0.0

Everything the developer dashboard asks for, in the order of its tabs. Requirements were checked on
2026-11-06 against Google's own pages under https://developer.chrome.com/docs/webstore/ (register,
prepare, publish, images, cws-dashboard-listing, cws-dashboard-privacy, cws-dashboard-distribution,
review-process, program-policies/policies).

## Before the first upload (account owner only)

- Register the developer account at https://chrome.google.com/webstore/devconsole and pay the one-time
  registration fee. The account's email address can't be changed afterwards.
- Turn on 2-Step Verification for that Google account. It is required before publishing.
- On the Account page: set the publisher name (shown as "Offered by") and verify the contact email.

## Package tab

Upload `dist/update-guard-1.0.0.zip`. Rebuild it after any change to `extension/`:

```bash
rm -f dist/update-guard-*.zip
(cd extension && zip -r -X ../dist/update-guard-$(python3 -c "import json;print(json.load(open('manifest.json'))['version'])").zip .)
```

Every later upload needs a higher `version` in `extension/manifest.json`.

## Store listing tab

| Field | Value |
|---|---|
| Title | Update Guard (from the manifest) |
| Summary | Turns extensions off when they update and asks you to review the new version before it runs again. (from the manifest, 98 of 132 characters) |
| Category | Privacy & Security, or the closest the dashboard offers |
| Language | English |
| Store icon | `extension/icons/icon128.png` |
| Screenshots | `store/screenshots/1-updates-held.png`, `store/screenshots/2-extension-list.png` (1280x800) |
| Small promo tile | `store/promo-small-440x280.png` (optional) |
| Marquee promo tile, video | none |
| Homepage URL, support URL | none yet (optional) |
| Mature content | No |

### Description

```
Update Guard turns an extension off as soon as it updates, tells you, and keeps it off until you have approved the new version.

WHY
An extension you trusted when you installed it can change. Extensions are sold and developer accounts are taken over, and the next update can then carry code you never agreed to. Chrome installs extension updates in the background without asking. Update Guard puts you back in the loop.

HOW IT WORKS
• When any installed extension changes version, Update Guard turns it off and shows a notification.
• Its review page lists each held update: old and new version, and what Chrome reports as changed: new permissions, new site access, a changed name, description, homepage or update source.
• You decide: approve the update and turn the extension back on, or leave it off. Update Guard only ever turns extensions off and on; it never removes or changes them.
• An update that is waiting for review stays off. If the extension is switched on some other way, Update Guard turns it off again.
• Updates that arrive while Chrome is closed are held the next time it starts.
• Extensions you trust can be set to "Allow without asking". Their updates are only logged.

WHAT IT DOES NOT DO
• It cannot stop Chrome from installing an update. It acts right afterwards, so a new version can run for a moment before it is turned off.
• It cannot read another extension's code. The review page tells you where the files are on your computer so you can inspect them yourself.
• It cannot turn off extensions installed by your organization's policy. It still tells you when they update.

WHAT IT CAN ACCESS
Update Guard asks for only three permissions: managing extensions (only to turn them off and back on — never to remove or change them), local storage, and notifications. It requests no access to web pages, tabs, cookies, or browsing history, so it cannot read or change what you do on any website.

PRIVACY
Update Guard works entirely on your computer. It makes no network requests and collects no data. The list of approved versions, your settings and a short history are kept in the browser's local extension storage.
```

## Privacy tab

**Single purpose**

```
Turn an installed extension off when it updates, notify the user, and keep it off until the user has reviewed and approved the new version.
```

**Permission justifications**

`management`

```
This is the extension's core function. It reads the list of installed extensions with their versions and permissions to notice when one has updated and to show the user what changed, and it turns the updated extension off and back on once the user approves it. It never uninstalls an extension or changes it in any other way. It listens to management.onInstalled, onEnabled and onUninstalled to do this.
```

`storage`

```
Stores on the device, in chrome.storage.local: the last approved version of each extension, the updates waiting for review, the user's per-extension settings, and a history of the last 200 events. Nothing is synced or sent anywhere.
```

`notifications`

```
Shows a notification when an extension has updated and was turned off, so the user knows a review is waiting.
```

**Remote code:** No, I am not using remote code. (All code is in the package. The extension makes no network requests.)

**Data usage:** tick none of the data categories. The extension transmits nothing; what it stores stays in
local extension storage. Tick the three certifications (no sale of data, no use unrelated to the single
purpose, no use for creditworthiness).

**Privacy policy URL:** the program policy requires one "if your Product handles any user data". Update
Guard keeps extension names and versions on the device only. The text in `store/privacy-policy.md` is
ready to be put on a public page if the dashboard or a reviewer asks for it.

## Distribution tab

- Payments: free.
- Regions: all.
- Visibility: **Unlisted** for the first submission (installable by link, not shown in search), then
  Public once it has been used from the store in Google Chrome. All visibility settings go through the
  same review.

## Test instructions tab (optional)

```
No account or setup is needed. To see it work without waiting for a real update: load any unpacked extension, open Update Guard's page (toolbar icon), tick "Also hold unpacked (developer mode) extensions when their version changes", raise "version" in the unpacked extension's manifest.json and reload it on chrome://extensions. Update Guard turns it off, shows a notification and lists it under "Waiting for review".
```

## What to expect from review

Google's review-process page says most reviews finish within a few days, some take a few weeks, and
that new developers and "dangerous permission requests" get closer attention. `management` lets an
extension turn other extensions off, so expect this to be looked at by a person.

## Similar extensions already in the store (searched 2026-11-06)

From their store summaries, these notify about updates; none of the summaries mentions turning the
updated extension off until it is approved. (Summaries only, their full listings were not read.)

- Extension Update Monitor: "Notify users when extensions are updated and log extension installs,
  uninstalls, and updates."
- Extensions Update Tracker: "Track and manage your Chrome extension updates in one place."
- Extension Guard - Manage and secure extensions: "Access your extension's risk level, stats, and
  trends, powered by Chrome-Stats data. Get notifications on extension updates."

No extension named exactly "Update Guard" showed up in the first page of results for that search.
