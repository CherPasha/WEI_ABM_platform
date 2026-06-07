# Project Page Tabs — Design Spec

## Context

The project page (`app/templates/project.html`) currently stacks all sections vertically: Target Roles, Upload File, Progress, Downloads, Stop Words, Keyword Groups, Files History. As the keyword and stop-word sections grow, this becomes hard to navigate. The goal is to reorganise the page into three focused tabs so users can work in each area without scrolling past unrelated content.

---

## Tab Structure

| Tab | Contents |
|-----|----------|
| **Upload** | Target Roles · Upload Form · Progress · Downloads · Files History |
| **Keywords** | Keyword Groups · Scan button |
| **Stop Words** | Stop Words section |

Upload is the default active tab on page load.

---

## Implementation Approach: Pure CSS tabs

No JavaScript. Three hidden `<input type="radio" name="tab">` elements act as state. Three `<label>` elements form the visible tab bar. Three content panels follow as siblings inside a `.tabs` wrapper.

### HTML skeleton

```html
<div class="tabs">
  <input type="radio" id="tab-upload"    name="tab" checked>
  <input type="radio" id="tab-keywords"  name="tab">
  <input type="radio" id="tab-stopwords" name="tab">

  <nav class="tab-nav">
    <label for="tab-upload">Upload</label>
    <label for="tab-keywords">Keywords</label>
    <label for="tab-stopwords">Stop Words</label>
  </nav>

  <div id="panel-upload">
    <!-- Target Roles article -->
    <!-- Upload Form article -->
    <!-- Progress article (hidden until upload starts) -->
    <!-- Downloads article (hidden until completed) -->
    <!-- Files History article -->
  </div>

  <div id="panel-keywords">
    <!-- Keyword Groups article -->
  </div>

  <div id="panel-stopwords">
    <!-- Stop Words article -->
  </div>
</div>
```

### CSS rules to add

```css
/* Hide radio controls */
.tabs > input[type="radio"] { display: none; }

/* Hide all panels by default */
.tabs > div[id^="panel-"] { display: none; }

/* Show active panel */
#tab-upload:checked    ~ #panel-upload    { display: block; }
#tab-keywords:checked  ~ #panel-keywords  { display: block; }
#tab-stopwords:checked ~ #panel-stopwords { display: block; }

/* Tab bar */
.tab-nav {
    display: flex;
    border-bottom: 2px solid var(--pico-muted-border-color);
    margin-bottom: 1.5em;
    gap: 0;
}
.tab-nav label {
    padding: 0.6em 1.5em;
    cursor: pointer;
    font-weight: 500;
    border-bottom: 3px solid transparent;
    margin-bottom: -2px;
    color: var(--pico-muted-color);
}
.tab-nav label:hover { color: var(--pico-color); }

/* Active tab label */
#tab-upload:checked    ~ .tab-nav label[for="tab-upload"],
#tab-keywords:checked  ~ .tab-nav label[for="tab-keywords"],
#tab-stopwords:checked ~ .tab-nav label[for="tab-stopwords"] {
    border-bottom-color: var(--pico-primary);
    color: var(--pico-primary);
}
```

---

## Scope

- **Only `app/templates/project.html` changes.** No backend, no JS logic, no API changes.
- All existing JavaScript functions (`loadKeywordGroups`, `loadStopWords`, `pollStatus`, etc.) stay identical — they just run inside their respective panels.
- The `.progress-section` and `.download-section` `display:none` rules remain as-is; the tab CSS only controls which panel is visible, not the progress/download articles inside the upload panel.

---

## Files Modified

| File | Change |
|------|--------|
| `app/templates/project.html` | Add CSS rules · Wrap content in `.tabs` · Move articles into correct panels |

---

## Verification

1. Open `http://localhost:8000/projects/<id>`
2. Upload tab is active by default — Target Roles, Upload Form, History visible
3. Click Keywords tab — keyword groups visible, upload form hidden
4. Click Stop Words tab — stop words visible, keyword groups hidden
5. Upload a file while on Upload tab — progress and download sections appear as before
6. Switching to another tab and back preserves upload progress display
7. No JS errors in browser console
