---
name: web_browser
description: Collection of tools for web_browser
should_run_in_sandbox: main
tools:
- name: open_site
  description: Open the specified website URL or local file path
  script: open_site.py
  parameters:
  - name: url
    description: The URL to open (can be a web URL or file path)
    type: string
    required: true
    positional: true
    position: 0
  returns_json: false
- name: close_site
  description: Close the currently open browser window
  script: close_site.py
  parameters: []
  returns_json: false
- name: screenshot_site
  description: Take a screenshot of the current page
  script: screenshot_site.py
  parameters: []
  returns_json: false
- name: click_mouse
  description: Click at the specified coordinates (shown as a red crosshair) on the
    current page
  script: click_mouse.py
  parameters:
  - name: x
    description: X coordinate
    type: integer
    required: true
    positional: true
    position: 0
  - name: y
    description: Y coordinate
    type: integer
    required: true
    positional: true
    position: 1
  - name: button
    description: 'Mouse button to click (left or right, default: left)'
    type: string
    required: false
    positional: true
    position: 2
  returns_json: false
- name: double_click_mouse
  description: Double-click at the specified coordinates (shown as a red crosshair)
    on the current page
  script: double_click_mouse.py
  parameters:
  - name: x
    description: X coordinate
    type: integer
    required: true
    positional: true
    position: 0
  - name: y
    description: Y coordinate
    type: integer
    required: true
    positional: true
    position: 1
  returns_json: false
- name: move_mouse
  description: Move mouse to the specified coordinates (shown as a red crosshair)
    on the current page
  script: move_mouse.py
  parameters:
  - name: x
    description: X coordinate
    type: integer
    required: true
    positional: true
    position: 0
  - name: y
    description: Y coordinate
    type: integer
    required: true
    positional: true
    position: 1
  returns_json: false
- name: drag_mouse
  description: 'Drag mouse along a path (JSON format: [[x1,y1],[x2,y2],...]) on the
    current page'
  script: drag_mouse.py
  parameters:
  - name: path
    description: JSON array of coordinate pairs for the drag path (e.g., '[[0,0],[100,100]]')
    type: string
    required: true
    positional: true
    position: 0
  returns_json: false
- name: type_text
  description: Type the given text at the current focused element on the current page
  script: type_text.py
  parameters:
  - name: text
    description: Text to type
    type: string
    required: true
    positional: true
    position: 0
  returns_json: false
- name: scroll_on_page
  description: Scroll by the specified number of pixels on the current page
  script: scroll_on_page.py
  parameters:
  - name: scroll_x
    description: Horizontal scroll amount in pixels
    type: integer
    required: true
    positional: true
    position: 0
  - name: scroll_y
    description: Vertical scroll amount in pixels
    type: integer
    required: true
    positional: true
    position: 1
  returns_json: false
- name: execute_script_on_page
  description: Execute a custom JavaScript code snippet on the current page
  script: execute_script_on_page.py
  parameters:
  - name: script
    description: JavaScript code to execute
    type: string
    required: true
    positional: true
    position: 0
  returns_json: false
- name: navigate_back
  description: Navigate back in the browser history
  script: navigate_back.py
  parameters: []
  returns_json: false
- name: navigate_forward
  description: Navigate forward in the browser history
  script: navigate_forward.py
  parameters: []
  returns_json: false
- name: reload_page
  description: Reload the current webpage
  script: reload_page.py
  parameters: []
  returns_json: false
- name: wait_time
  description: Wait for the specified number of milliseconds
  script: wait_time.py
  parameters:
  - name: ms
    description: Time to wait in milliseconds
    type: integer
    required: true
    positional: true
    position: 0
  returns_json: false
- name: press_keys_on_page
  description: 'Press the specified keys (JSON format: ["key1", "key2"]) on the current
    page'
  script: press_keys_on_page.py
  parameters:
  - name: keys
    description: JSON array of keys to press (e.g., '["ctrl", "c"]')
    type: string
    required: true
    positional: true
    position: 0
  returns_json: false
- name: set_browser_window_size
  description: Set the browser window size to the specified dimensions
  script: set_browser_window_size.py
  parameters:
  - name: width
    description: Window width in pixels
    type: integer
    required: true
    positional: true
    position: 0
  - name: height
    description: Window height in pixels
    type: integer
    required: true
    positional: true
    position: 1
  returns_json: false
- name: get_console_output
  description: Get console output messages from the browser (logs, errors, warnings,
    etc.)
  script: get_console_output.py
  parameters: []
  returns_json: false
---

# web_browser

> [!NOTE]
> This tool group includes an `install.sh` script which may need to be run to set up dependencies.

## Requires Sandbox
main

## Tools

### open_site

Open the specified website URL or local file path

#### Usage

```bash
open_site <URL>
```

#### Parameters

| Name | Type | Description | Required |
| :--- | :--- | :--- | :--- |
| url | string | The URL to open (can be a web URL or file path) | true |

### close_site

Close the currently open browser window

#### Usage

```bash
close_site
```

### screenshot_site

Take a screenshot of the current page

#### Usage

```bash
screenshot_site
```

### click_mouse

Click at the specified coordinates (shown as a red crosshair) on the current page

#### Usage

```bash
click_mouse <X> <Y> [BUTTON]
```

#### Parameters

| Name | Type | Description | Required |
| :--- | :--- | :--- | :--- |
| x | integer | X coordinate | true |
| y | integer | Y coordinate | true |
| button | string | Mouse button to click (left or right, default: left) | false |

### double_click_mouse

Double-click at the specified coordinates (shown as a red crosshair) on the current page

#### Usage

```bash
double_click_mouse <X> <Y>
```

#### Parameters

| Name | Type | Description | Required |
| :--- | :--- | :--- | :--- |
| x | integer | X coordinate | true |
| y | integer | Y coordinate | true |

### move_mouse

Move mouse to the specified coordinates (shown as a red crosshair) on the current page

#### Usage

```bash
move_mouse <X> <Y>
```

#### Parameters

| Name | Type | Description | Required |
| :--- | :--- | :--- | :--- |
| x | integer | X coordinate | true |
| y | integer | Y coordinate | true |

### drag_mouse

Drag mouse along a path (JSON format: [[x1,y1],[x2,y2],...]) on the current page

#### Usage

```bash
drag_mouse <PATH>
```

#### Parameters

| Name | Type | Description | Required |
| :--- | :--- | :--- | :--- |
| path | string | JSON array of coordinate pairs for the drag path (e.g., '[[0,0],[100,100]]') | true |

### type_text

Type the given text at the current focused element on the current page

#### Usage

```bash
type_text <TEXT>
```

#### Parameters

| Name | Type | Description | Required |
| :--- | :--- | :--- | :--- |
| text | string | Text to type | true |

### scroll_on_page

Scroll by the specified number of pixels on the current page

#### Usage

```bash
scroll_on_page <SCROLL_X> <SCROLL_Y>
```

#### Parameters

| Name | Type | Description | Required |
| :--- | :--- | :--- | :--- |
| scroll_x | integer | Horizontal scroll amount in pixels | true |
| scroll_y | integer | Vertical scroll amount in pixels | true |

### execute_script_on_page

Execute a custom JavaScript code snippet on the current page

#### Usage

```bash
execute_script_on_page <SCRIPT>
```

#### Parameters

| Name | Type | Description | Required |
| :--- | :--- | :--- | :--- |
| script | string | JavaScript code to execute | true |

### navigate_back

Navigate back in the browser history

#### Usage

```bash
navigate_back
```

### navigate_forward

Navigate forward in the browser history

#### Usage

```bash
navigate_forward
```

### reload_page

Reload the current webpage

#### Usage

```bash
reload_page
```

### wait_time

Wait for the specified number of milliseconds

#### Usage

```bash
wait_time <MS>
```

#### Parameters

| Name | Type | Description | Required |
| :--- | :--- | :--- | :--- |
| ms | integer | Time to wait in milliseconds | true |

### press_keys_on_page

Press the specified keys (JSON format: ["key1", "key2"]) on the current page

#### Usage

```bash
press_keys_on_page <KEYS>
```

#### Parameters

| Name | Type | Description | Required |
| :--- | :--- | :--- | :--- |
| keys | string | JSON array of keys to press (e.g., '["ctrl", "c"]') | true |

### set_browser_window_size

Set the browser window size to the specified dimensions

#### Usage

```bash
set_browser_window_size <WIDTH> <HEIGHT>
```

#### Parameters

| Name | Type | Description | Required |
| :--- | :--- | :--- | :--- |
| width | integer | Window width in pixels | true |
| height | integer | Window height in pixels | true |

### get_console_output

Get console output messages from the browser (logs, errors, warnings, etc.)

#### Usage

```bash
get_console_output
```
