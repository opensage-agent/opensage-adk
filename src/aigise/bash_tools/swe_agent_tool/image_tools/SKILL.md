---
name: image_tools
description: Collection of tools for image_tools
should_run_in_sandbox: main
tools:
- name: view_image
  description: view an image file
  script: view_image.py
  parameters:
  - name: image_file
    description: the path to the image file to view
    type: string
    required: true
    positional: true
    position: 0
  returns_json: false
---

# image_tools

## Requires Sandbox
main

## Tools

### view_image

view an image file

#### Usage

```bash
view_image <IMAGE_FILE>
```

#### Parameters

| Name | Type | Description | Required |
| :--- | :--- | :--- | :--- |
| image_file | string | the path to the image file to view | true |
