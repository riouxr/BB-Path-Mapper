# BB Path Mapper

A Blender 5 extension for remapping broken file paths when moving `.blend` files between Windows and macOS.

**By Blender Bob and Claude.ai**

---

## The Problem

When you open a `.blend` file on a different OS or machine, textures, libraries, and other assets go missing because their paths no longer make sense. Windows uses `E:\Library\file.hdr`, macOS mounts the same drive as `/Volumes/amd-1/`, and relative paths like `../../../../Work/file.jpg` resolve to the wrong place entirely.

---

## Features

- **Scan** — detects all broken path roots in the scene, including relative paths with `../` noise
- **Automatic Mapping** — instantly remaps drive letters that have an obvious local match (e.g. `E:` on Windows ↔ the matching SMB share on Mac)
- **Manual Mapping** — pick from a dropdown of available volumes, or type a UNC path like `\\mac.local\Work\` directly
- **Revert** — original paths are saved inside the `.blend` file itself, so you can always get back to them on the original machine
- Handles all path formats: `E:\`, `E:/`, `/e/`, `/Volumes/name/`, `\\server\share\`, mixed-slash paths, and dot-segment relative paths

---

## Installation

1. Download the latest `.zip` from [Releases](../../releases)
2. Drag and drop in viewport

---

## Usage

Open **Edit → Preferences → Add-ons → BB Path Mapper**.

### Typical workflow on a new machine

1. Click **Automatic Mapping** — handles drive letters and known share names in one click
2. For anything left over, click **Scan** to refresh the missing roots list
3. Select a missing root from the dropdown
4. Pick a target volume from the list, or type a path manually (useful for UNC paths on Windows)
5. Click **Apply Mapping**
6. Repeat for each remaining root

### Going back

If a backup exists in the file (i.e. you've applied a mapping before), a **Revert to Original Paths** button appears at the top of the panel. Click it to restore all paths to what they were on the original machine.

---

## Notes

- The backup is stored as a hidden text block inside the `.blend` file — it travels with the file automatically
- On macOS, network drives may not appear in Blender's file browser; the extension scans `/Volumes` directly so they show up in the dropdown
- On Windows, unmapped UNC shares (e.g. `\\mac.local\Work`) can be typed in manually

---

## Requirements

- Blender 5.0 or later
- macOS, Windows, or Linux
