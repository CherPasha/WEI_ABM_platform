# Design: Keyword / Anti-keyword / Roles Layout Redesign

**Date:** 2026-06-15
**Status:** Approved

---

## Overview

Replace the current accordion (`<details>`) and tag-chip layouts for Keywords, Anti-keywords, and Roles with compact table-based views. Keyword and anti-keyword groups are summarised in a table on the panel; clicking a group name opens a `<dialog>` modal listing the group's keywords one per row with inline add.

---

## 1. Keywords Panel (panel-keywords)

### Main view

The `<div id="keyword-groups-container">` is replaced by a `<table>`:

| Group Name (clickable) | # Keywords | Delete |
|---|---|---|
| ABM | 12 | [Delete group] |
| Growth | 3 | [Delete group] |

- Group Name cell is a `<button>` (or styled link) that opens the keyword-group modal.
- # Keywords is an integer count from `g.keywords.length`.
- Delete column has a "Delete group" text button (same `deleteKeywordGroup` function as today).
- Empty state: single-row message "No keyword groups yet."

The "Add Group" input + button and "Import from file" button above the table are **unchanged**.

### Keyword Group Modal

A single `<dialog id="kw-group-modal">` shared across all groups. Populated dynamically on open.

**Header:** group name  
**Body:** `<table>` with columns: Keyword | × (delete button)  
**Last row:** inline add — `<input placeholder="Add keyword...">` + "Add" button spanning both columns  
**Footer:** "Close" button

JS function `openKwGroupModal(groupId, groupName)`:
1. Sets dialog header to group name.
2. Fetches current keyword list from already-loaded group data (no extra API call needed; re-render from the in-memory groups array or re-fetch `/api/projects/${PROJECT_ID}/keyword-groups`).
3. Shows the dialog via `.showModal()`.

Add keyword action calls existing `addKeyword(groupId)` logic; delete calls existing `deleteKeyword(keywordId)` logic. Both reload the modal contents and the main table after the action.

---

## 2. Anti-keywords Panel (panel-antikeywords)

Identical structure to Keywords Panel above, using:
- `<div id="anti-keyword-groups-container">` → table
- `<dialog id="anti-kw-group-modal">` for the per-group modal
- Existing `addAntiKeyword`, `deleteAntiKeyword`, `deleteAntiKeywordGroup` functions

---

## 3. Roles Panel (panel-roles)

The `.roles-list` flex-chip container is replaced by a `<table>`:

| Role | |
|---|---|
| CEO | × |
| CTO | × |

- Delete button calls existing `removeRole(idx)`.
- Empty state: single-row message "No target roles set."

The existing Add role input + button and "Import from xlsx" button below the table are **unchanged**.

---

## 4. CSS Changes

- Remove `.role-tag`, `.roles-list` rules (no longer needed).
- Remove `.kw-group`, `.kw-group summary`, `.kw-list`, `.kw-tag`, `.kw-add-row`, `.kw-group-header` rules (accordion-specific styles, no longer needed).
- Add minimal table styling for the inline-add row in modals (input fills width, button fixed-width).

---

## 5. No Backend Changes

All data is already available via existing API endpoints. No new routes needed.

---

## 6. Out of Scope

- Stop Words panel — unchanged (tag chips remain).
- Import buttons — unchanged.
- Scan controls — unchanged.
