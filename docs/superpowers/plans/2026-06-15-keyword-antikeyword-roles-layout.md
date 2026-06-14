# Keyword / Anti-keyword / Roles Layout Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace accordion and tag-chip UX for keywords, anti-keywords, and roles with compact table views and a `<dialog>` modal for editing keywords per group.

**Architecture:** All changes are in `app/templates/project.html` (HTML, CSS, JS). No backend changes. One shared `<dialog>` per group type (keyword / anti-keyword) is populated dynamically on open. Roles become a plain table in place of the flex-chip container.

**Tech Stack:** Vanilla JS, PicoCSS 2 (already in use), native `<dialog>` API (already used for the import modal on the same page).

---

### Task 1: Roles — replace tag chips with a table

**Files:**
- Modify: `app/templates/project.html`

- [ ] **Step 1: Update `renderRoles()`**

Find `renderRoles()` (~line 1264) and replace the entire function:

```javascript
function renderRoles() {
    const container = document.getElementById("roles-list");
    if (!projectRoles.length) {
        container.innerHTML = '<p style="color:var(--pico-muted-color); font-size:0.9em;">No target roles set. Add roles to enable contact enrichment.</p>';
        return;
    }
    container.innerHTML = `<table style="margin-bottom:0.5em;">
        <tbody>
            ${projectRoles.map((r, idx) => `
            <tr>
                <td>${escapeHtml(r)}</td>
                <td style="width:2em; text-align:right;">
                    <button onclick="removeRole(${idx})" title="Remove"
                            style="background:none;border:none;cursor:pointer;color:var(--pico-color-red-500);padding:0;margin:0;width:auto;line-height:1;">&times;</button>
                </td>
            </tr>`).join("")}
        </tbody>
    </table>`;
}
```

- [ ] **Step 2: Remove `class="roles-list"` from the container div**

Find (~line 406):
```html
<div id="roles-list" class="roles-list"></div>
```
Replace with:
```html
<div id="roles-list"></div>
```

- [ ] **Step 3: Remove `.role-tag` and `.roles-list` CSS**

Find and delete (~lines 41–50):
```css
    .role-tag {
        display: inline-flex; align-items: center; gap: 0.3em;
        background: var(--pico-secondary-background); padding: 0.25em 0.6em;
        border-radius: 4px; font-size: 0.85em; color: white;
    }
    .role-tag button {
        background: none; border: none; cursor: pointer; padding: 0; margin: 0;
        color: var(--pico-color-red-500); font-size: 1em; line-height: 1; width: auto;
    }
    .roles-list { display: flex; flex-wrap: wrap; gap: 0.4em; margin: 0.5em 0; }
```

- [ ] **Step 4: Verify in browser**

Open the Roles tab. Roles should display as a table, one role per row, with a × on the right. The × removes the role. Empty state shows the "No target roles set" message. Adding via the existing input appends a new row.

- [ ] **Step 5: Commit**

```bash
git add app/templates/project.html
git commit -m "feat: roles list as table rows instead of tag chips"
```

---

### Task 2: Keywords — main panel as group summary table

**Files:**
- Modify: `app/templates/project.html`

Replace `loadKeywordGroups()` to render a summary `<table>` (group name | count | Delete) instead of `<details>` accordions. The group name becomes a button that opens the modal (wired in Task 3).

- [ ] **Step 1: Update `loadKeywordGroups()`**

Find `loadKeywordGroups()` (~line 897) and replace the entire function:

```javascript
async function loadKeywordGroups() {
    const container = document.getElementById("keyword-groups-container");
    try {
        const resp = await fetch(`/api/projects/${PROJECT_ID}/keyword-groups`);
        const groups = await resp.json();

        if (!groups.length) {
            container.innerHTML = '<p style="color:var(--pico-muted-color); font-size:0.9em;">No keyword groups yet. Create one above.</p>';
            return;
        }

        container.innerHTML = `<table>
            <thead>
                <tr><th>Group</th><th># Keywords</th><th></th></tr>
            </thead>
            <tbody>
                ${groups.map(g => `
                <tr>
                    <td>
                        <button data-group-id="${g.id}" data-group-name="${escapeHtml(g.name)}"
                                onclick="openKwGroupModal(this)"
                                style="background:none;border:none;cursor:pointer;padding:0;margin:0;width:auto;color:var(--pico-primary);text-decoration:underline;font-size:inherit;font-weight:inherit;">
                            ${escapeHtml(g.name)}
                        </button>
                    </td>
                    <td>${g.keywords.length}</td>
                    <td>
                        <button onclick="deleteKeywordGroup('${g.id}')"
                                style="background:none;border:none;cursor:pointer;color:var(--pico-color-red-500);font-size:0.85em;padding:0;margin:0;width:auto;">
                            Delete
                        </button>
                    </td>
                </tr>`).join("")}
            </tbody>
        </table>`;
    } catch (err) {
        console.error("Failed to load keyword groups:", err);
    }
}
```

- [ ] **Step 2: Verify in browser**

Open the Keywords tab. Groups appear as a compact table: name (underlined link) | count | Delete button. Clicking the group name does nothing yet (modal added in Task 3). Clicking Delete removes the group and refreshes.

- [ ] **Step 3: Commit**

```bash
git add app/templates/project.html
git commit -m "feat: keyword groups as summary table"
```

---

### Task 3: Keywords — group modal

**Files:**
- Modify: `app/templates/project.html`

Add `<dialog id="kw-group-modal">` HTML. Add JS: `activeKwGroupId`, `openKwGroupModal`, `closeKwGroupModal`, `renderKwGroupModal`, `addKeywordFromModal`. Update `deleteKeyword()` to refresh the modal if open. Remove now-dead `addKeyword()`.

- [ ] **Step 1: Add modal HTML inside `panel-keywords`**

Find the closing tag of the keyword panel (~line 398):
```html
                </article>

            </div><!-- /panel-keywords -->
```
Insert the dialog between `</article>` and `</div><!-- /panel-keywords -->`:

```html
            <!-- Keyword group modal -->
            <dialog id="kw-group-modal">
                <article>
                    <header>
                        <button aria-label="Close" rel="prev" onclick="closeKwGroupModal()"></button>
                        <h3 id="kw-group-modal-title"></h3>
                    </header>
                    <div id="kw-group-modal-body"></div>
                    <footer>
                        <button class="secondary" onclick="closeKwGroupModal()">Close</button>
                    </footer>
                </article>
            </dialog>
```

- [ ] **Step 2: Add modal JS — insert after `let kwScanPollInterval = null;` (~line 1117)**

```javascript
        let activeKwGroupId = null;

        function openKwGroupModal(btn) {
            activeKwGroupId = btn.dataset.groupId;
            document.getElementById("kw-group-modal-title").textContent = btn.dataset.groupName;
            renderKwGroupModal(activeKwGroupId);
            document.getElementById("kw-group-modal").showModal();
        }

        function closeKwGroupModal() {
            activeKwGroupId = null;
            document.getElementById("kw-group-modal").close();
        }

        async function renderKwGroupModal(groupId) {
            const body = document.getElementById("kw-group-modal-body");
            try {
                const resp = await fetch(`/api/projects/${PROJECT_ID}/keyword-groups`);
                const groups = await resp.json();
                const group = groups.find(g => String(g.id) === String(groupId));
                if (!group) { body.innerHTML = '<p>Group not found.</p>'; return; }

                const rows = group.keywords.length
                    ? group.keywords.map(k => `
                        <tr>
                            <td>${escapeHtml(k.keyword)}</td>
                            <td style="width:2em; text-align:right;">
                                <button onclick="deleteKeyword('${k.id}')" title="Remove"
                                        style="background:none;border:none;cursor:pointer;color:var(--pico-color-red-500);padding:0;margin:0;width:auto;line-height:1;">&times;</button>
                            </td>
                        </tr>`).join("")
                    : `<tr><td colspan="2" style="color:var(--pico-muted-color);font-size:0.9em;">No keywords yet.</td></tr>`;

                body.innerHTML = `<table>
                    <tbody>
                        ${rows}
                        <tr>
                            <td><input type="text" id="kw-modal-input" placeholder="Add keyword..."
                                       style="margin:0;"
                                       onkeydown="if(event.key==='Enter'){addKeywordFromModal('${groupId}')}"></td>
                            <td style="width:5em;">
                                <button onclick="addKeywordFromModal('${groupId}')"
                                        style="margin:0;padding:0.3em 0.8em;font-size:0.85em;white-space:nowrap;">Add</button>
                            </td>
                        </tr>
                    </tbody>
                </table>`;

                document.getElementById("kw-modal-input").focus();
            } catch (err) {
                console.error("Failed to render keyword modal:", err);
                body.innerHTML = '<p style="color:var(--pico-color-red-500);">Failed to load keywords.</p>';
            }
        }

        async function addKeywordFromModal(groupId) {
            const input = document.getElementById("kw-modal-input");
            const keyword = input.value.trim();
            if (!keyword) return;
            const resp = await fetch(`/api/keyword-groups/${groupId}/keywords`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ keyword }),
            });
            if (resp.ok) {
                await renderKwGroupModal(groupId);
                loadKeywordGroups();
                refreshTabStates();
            }
        }
```

- [ ] **Step 3: Update `deleteKeyword()` to refresh modal if open**

Find `deleteKeyword()` (~line 967) and replace the entire function:

```javascript
        async function deleteKeyword(keywordId) {
            await fetch(`/api/keywords/${keywordId}`, { method: "DELETE" });
            if (activeKwGroupId) await renderKwGroupModal(activeKwGroupId);
            loadKeywordGroups();
            refreshTabStates();
        }
```

- [ ] **Step 4: Remove the old `addKeyword()` function**

Find and delete the `addKeyword(groupId)` function (~line 953). It used `kw-input-${groupId}` inputs that no longer exist in the DOM and is fully replaced by `addKeywordFromModal`.

```javascript
        async function addKeyword(groupId) {
            const input = document.getElementById(`kw-input-${groupId}`);
            const keyword = input.value.trim();
            if (!keyword) return;
            await fetch(`/api/keyword-groups/${groupId}/keywords`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ keyword }),
            });
            input.value = "";
            loadKeywordGroups();
            refreshTabStates();
        }
```

- [ ] **Step 5: Verify in browser**

Click a keyword group name → dialog opens with group name as title. Keywords list as table rows, each with ×. Last row is an inline input + Add button. Type a keyword and press Enter or click Add → keyword appears in table without closing the dialog. Count in the main table updates. Click × on a keyword → it disappears from the modal. Dialog Close button dismisses the dialog.

- [ ] **Step 6: Commit**

```bash
git add app/templates/project.html
git commit -m "feat: keyword group modal with inline add"
```

---

### Task 4: Anti-keywords — main panel as group summary table

**Files:**
- Modify: `app/templates/project.html`

Same as Task 2, for anti-keyword groups.

- [ ] **Step 1: Update `loadAntiKeywordGroups()`**

Find `loadAntiKeywordGroups()` (~line 1013) and replace the entire function:

```javascript
        async function loadAntiKeywordGroups() {
            const container = document.getElementById("anti-keyword-groups-container");
            try {
                const resp = await fetch(`/api/projects/${PROJECT_ID}/anti-keyword-groups`);
                const groups = await resp.json();

                if (!groups.length) {
                    container.innerHTML = '<p style="color:var(--pico-muted-color); font-size:0.9em;">No anti-keyword groups yet. Create one above.</p>';
                    return;
                }

                container.innerHTML = `<table>
                    <thead>
                        <tr><th>Group</th><th># Keywords</th><th></th></tr>
                    </thead>
                    <tbody>
                        ${groups.map(g => `
                        <tr>
                            <td>
                                <button data-group-id="${g.id}" data-group-name="${escapeHtml(g.name)}"
                                        onclick="openAntiKwGroupModal(this)"
                                        style="background:none;border:none;cursor:pointer;padding:0;margin:0;width:auto;color:var(--pico-primary);text-decoration:underline;font-size:inherit;font-weight:inherit;">
                                    ${escapeHtml(g.name)}
                                </button>
                            </td>
                            <td>${g.keywords.length}</td>
                            <td>
                                <button onclick="deleteAntiKeywordGroup('${g.id}')"
                                        style="background:none;border:none;cursor:pointer;color:var(--pico-color-red-500);font-size:0.85em;padding:0;margin:0;width:auto;">
                                    Delete
                                </button>
                            </td>
                        </tr>`).join("")}
                    </tbody>
                </table>`;
            } catch (err) {
                console.error("Failed to load anti-keyword groups:", err);
            }
        }
```

- [ ] **Step 2: Verify in browser**

Open the Anti Keywords tab. Groups appear as a table: name | count | Delete. Clicking name does nothing yet (modal in Task 5).

- [ ] **Step 3: Commit**

```bash
git add app/templates/project.html
git commit -m "feat: anti-keyword groups as summary table"
```

---

### Task 5: Anti-keywords — group modal

**Files:**
- Modify: `app/templates/project.html`

Mirror of Task 3 for anti-keywords.

- [ ] **Step 1: Add modal HTML inside `panel-antikeywords`**

Find the closing tag of the anti-keywords panel (~line 364):
```html
                </article>

            </div><!-- /panel-antikeywords -->
```
Insert between `</article>` and `</div><!-- /panel-antikeywords -->`:

```html
            <!-- Anti-keyword group modal -->
            <dialog id="anti-kw-group-modal">
                <article>
                    <header>
                        <button aria-label="Close" rel="prev" onclick="closeAntiKwGroupModal()"></button>
                        <h3 id="anti-kw-group-modal-title"></h3>
                    </header>
                    <div id="anti-kw-group-modal-body"></div>
                    <footer>
                        <button class="secondary" onclick="closeAntiKwGroupModal()">Close</button>
                    </footer>
                </article>
            </dialog>
```

- [ ] **Step 2: Add modal JS — insert after the `addKeywordFromModal` function added in Task 3**

```javascript
        let activeAntiKwGroupId = null;

        function openAntiKwGroupModal(btn) {
            activeAntiKwGroupId = btn.dataset.groupId;
            document.getElementById("anti-kw-group-modal-title").textContent = btn.dataset.groupName;
            renderAntiKwGroupModal(activeAntiKwGroupId);
            document.getElementById("anti-kw-group-modal").showModal();
        }

        function closeAntiKwGroupModal() {
            activeAntiKwGroupId = null;
            document.getElementById("anti-kw-group-modal").close();
        }

        async function renderAntiKwGroupModal(groupId) {
            const body = document.getElementById("anti-kw-group-modal-body");
            try {
                const resp = await fetch(`/api/projects/${PROJECT_ID}/anti-keyword-groups`);
                const groups = await resp.json();
                const group = groups.find(g => String(g.id) === String(groupId));
                if (!group) { body.innerHTML = '<p>Group not found.</p>'; return; }

                const rows = group.keywords.length
                    ? group.keywords.map(k => `
                        <tr>
                            <td>${escapeHtml(k.keyword)}</td>
                            <td style="width:2em; text-align:right;">
                                <button onclick="deleteAntiKeyword('${k.id}')" title="Remove"
                                        style="background:none;border:none;cursor:pointer;color:var(--pico-color-red-500);padding:0;margin:0;width:auto;line-height:1;">&times;</button>
                            </td>
                        </tr>`).join("")
                    : `<tr><td colspan="2" style="color:var(--pico-muted-color);font-size:0.9em;">No keywords yet.</td></tr>`;

                body.innerHTML = `<table>
                    <tbody>
                        ${rows}
                        <tr>
                            <td><input type="text" id="anti-kw-modal-input" placeholder="Add keyword..."
                                       style="margin:0;"
                                       onkeydown="if(event.key==='Enter'){addAntiKeywordFromModal('${groupId}')}"></td>
                            <td style="width:5em;">
                                <button onclick="addAntiKeywordFromModal('${groupId}')"
                                        style="margin:0;padding:0.3em 0.8em;font-size:0.85em;white-space:nowrap;">Add</button>
                            </td>
                        </tr>
                    </tbody>
                </table>`;

                document.getElementById("anti-kw-modal-input").focus();
            } catch (err) {
                console.error("Failed to render anti-keyword modal:", err);
                body.innerHTML = '<p style="color:var(--pico-color-red-500);">Failed to load keywords.</p>';
            }
        }

        async function addAntiKeywordFromModal(groupId) {
            const input = document.getElementById("anti-kw-modal-input");
            const keyword = input.value.trim();
            if (!keyword) return;
            const resp = await fetch(`/api/keyword-groups/${groupId}/keywords`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ keyword }),
            });
            if (resp.ok) {
                await renderAntiKwGroupModal(groupId);
                loadAntiKeywordGroups();
                refreshTabStates();
            }
        }
```

- [ ] **Step 3: Update `deleteAntiKeyword()` to refresh modal if open**

Find `deleteAntiKeyword()` (~line 973) and replace the entire function:

```javascript
        async function deleteAntiKeyword(keywordId) {
            await fetch(`/api/keywords/${keywordId}`, { method: "DELETE" });
            if (activeAntiKwGroupId) await renderAntiKwGroupModal(activeAntiKwGroupId);
            loadAntiKeywordGroups();
            refreshTabStates();
        }
```

- [ ] **Step 4: Remove old `addAntiKeyword()` function**

Find and delete `addAntiKeyword(groupId)` (~line 1069). It used `anti-kw-input-${groupId}` inputs that no longer exist and is fully replaced by `addAntiKeywordFromModal`.

```javascript
        async function addAntiKeyword(groupId) {
            const input = document.getElementById(`anti-kw-input-${groupId}`);
            const keyword = input.value.trim();
            if (!keyword) return;
            await fetch(`/api/keyword-groups/${groupId}/keywords`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ keyword }),
            });
            input.value = "";
            loadAntiKeywordGroups();
            refreshTabStates();
        }
```

- [ ] **Step 5: Verify in browser**

Click an anti-keyword group name → dialog opens. Add and delete keywords inline. Count in main table updates. Enter triggers add.

- [ ] **Step 6: Commit**

```bash
git add app/templates/project.html
git commit -m "feat: anti-keyword group modal with inline add"
```

---

### Task 6: CSS cleanup — remove accordion and chip styles

**Files:**
- Modify: `app/templates/project.html`

Remove CSS rules that only served the now-deleted `<details>` accordions and role chips. **Keep** `.kw-tag` and `.kw-tag button` — still used by the Stop Words panel.

- [ ] **Step 1: Delete accordion and chip CSS rules**

Find and delete these rules from the `<style>` block (~lines 24–40, 41–50):

```css
        .kw-group { border: 1px solid var(--pico-muted-border-color); border-radius: 8px; padding: 1em; margin-bottom: 1em; }
        .kw-group summary { font-weight: 600; cursor: pointer; }
        .kw-group .kw-list { display: flex; flex-wrap: wrap; gap: 0.4em; margin: 0.8em 0; }
        .kw-add-row { display: flex; gap: 0.5em; align-items: center; }
        .kw-add-row input { margin: 0; padding: 0.3em 0.5em; font-size: 0.9em; }
        .kw-add-row button { margin: 0; padding: 0.3em 0.8em; font-size: 0.85em; white-space: nowrap; }
        .kw-group-header { display: flex; justify-content: space-between; align-items: center; }
        .kw-group-header button { background: none; border: none; cursor: pointer; color: var(--pico-color-red-500); font-size: 0.85em; padding: 0; margin: 0; width: auto; }
    .role-tag {
        display: inline-flex; align-items: center; gap: 0.3em;
        background: var(--pico-secondary-background); padding: 0.25em 0.6em;
        border-radius: 4px; font-size: 0.85em; color: white;
    }
    .role-tag button {
        background: none; border: none; cursor: pointer; padding: 0; margin: 0;
        color: var(--pico-color-red-500); font-size: 1em; line-height: 1; width: auto;
    }
    .roles-list { display: flex; flex-wrap: wrap; gap: 0.4em; margin: 0.5em 0; }
```

Do **not** delete `.kw-tag` or `.kw-tag button` — those remain for Stop Words.

- [ ] **Step 2: Verify in browser**

All three panels (keywords, anti-keywords, roles) look correct. Stop Words panel still shows tag chips with × buttons correctly.

- [ ] **Step 3: Commit**

```bash
git add app/templates/project.html
git commit -m "style: remove obsolete accordion and role-chip CSS"
```
