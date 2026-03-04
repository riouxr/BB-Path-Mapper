import bpy
import json
import os
import re
import sys
import string
import subprocess
from bpy.types import AddonPreferences, Operator
from bpy.props import EnumProperty, StringProperty

# ---------------------------------------------------------------------------
# Platform
# ---------------------------------------------------------------------------

IS_WINDOWS = sys.platform.startswith("win")
IS_MAC     = sys.platform == "darwin"
IS_LINUX   = sys.platform.startswith("linux")

# ---------------------------------------------------------------------------
# Backup — JSON text block embedded in the .blend, travels with the file
# ---------------------------------------------------------------------------

BACKUP_BLOCK_NAME = "_pathmapper_backup"
_COLLECTIONS = ("images", "libraries", "sounds", "fonts", "movieclips", "volumes")


def _backup_exists() -> bool:
    return BACKUP_BLOCK_NAME in bpy.data.texts

def _write_backup(entries: list[dict]) -> None:
    txt = bpy.data.texts.get(BACKUP_BLOCK_NAME) or bpy.data.texts.new(BACKUP_BLOCK_NAME)
    txt.clear()
    txt.write(json.dumps(entries, indent=2))

def _read_backup() -> list[dict]:
    txt = bpy.data.texts.get(BACKUP_BLOCK_NAME)
    if not txt:
        return []
    try:
        return json.loads(txt.as_string())
    except (json.JSONDecodeError, ValueError):
        return []

def _delete_backup() -> None:
    txt = bpy.data.texts.get(BACKUP_BLOCK_NAME)
    if txt:
        bpy.data.texts.remove(txt)


# ---------------------------------------------------------------------------
# Module-level enum cache  (avoids Blender's dynamic EnumProperty instability)
# ---------------------------------------------------------------------------

_missing_cache:   list[tuple] = [("NONE", "Click  ▸ Scan  first", "")]
_available_cache: list[tuple] = [("NONE", "Click  ▸ Scan  first", "")]

def _missing_items(self, context):   return _missing_cache
def _available_items(self, context): return _available_cache


# ---------------------------------------------------------------------------
# Windows: enumerate drive letters AND UNC network paths via `net use`
#
# `net use` output example:
#   OK           Z:        \\mac.local\Work     Microsoft Windows Network
#   OK                     \\nas\Media          Microsoft Windows Network
#
# We collect both mapped drive letters (already caught by the A-Z scan)
# and bare UNC connections that have no drive letter assigned.
# ---------------------------------------------------------------------------

_NET_USE_RE = re.compile(
    r"^\S+\s+"                 # Status  (OK / Disconnected / …)
    r"([A-Za-z]:)?\s+"         # optional Local (drive letter)
    r"(\\\\[^\s]+)",           # Remote  (UNC path — always starts with \\)
    re.MULTILINE
)

def _get_windows_roots() -> list[tuple[str, str, str]]:
    """Drive letters + UNC shares visible to Windows."""
    results = []

    # 1. Local / mapped drive letters
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        if os.path.exists(root):
            results.append((root, root, root))

    # 2. UNC paths from `net use` (includes shares not mapped to a letter)
    already_roots = {r[0] for r in results}
    try:
        out = subprocess.check_output(
            ["net", "use"], text=True, stderr=subprocess.DEVNULL,
            creationflags=0x08000000   # CREATE_NO_WINDOW
        )
        for m in _NET_USE_RE.finditer(out):
            local = m.group(1)    # e.g. "Z:" or None
            unc   = m.group(2)    # e.g. \\mac.local\Work

            # Normalise to trailing backslash
            unc_root = unc.rstrip("\\") + "\\"

            if local:
                # Already in drive-letter list — annotate the label
                drive_root = local.upper() + "\\"
                # Update the label to show the UNC name too
                results = [
                    (r, f"{r}  ({unc})", unc) if r == drive_root else (r, lbl, tip)
                    for r, lbl, tip in results
                ]
            else:
                # No drive letter — surface the raw UNC path
                if unc_root not in already_roots:
                    share_name = unc.rstrip("\\").rsplit("\\", 1)[-1]
                    results.append((unc_root, share_name + f"  ({unc})", unc_root))
                    already_roots.add(unc_root)
    except Exception:
        pass

    return results


# ---------------------------------------------------------------------------
# macOS: /Volumes scan + SMB share-name lookup via `mount`
# ---------------------------------------------------------------------------

_SMB_RE = re.compile(
    r"^//[^/]*/(\S+)\s+on\s+(/Volumes/\S+)\s+\(smbfs", re.IGNORECASE
)

def _smb_share_names() -> dict[str, str]:
    """/Volumes/mountpoint/  →  ShareName"""
    mapping: dict[str, str] = {}
    try:
        out = subprocess.check_output(
            ["mount"], text=True, stderr=subprocess.DEVNULL
        )
        for line in out.splitlines():
            m = _SMB_RE.match(line)
            if m:
                share  = m.group(1)
                mpoint = m.group(2).rstrip("/") + "/"
                mapping[mpoint] = share
    except Exception:
        pass
    return mapping

def _peek(path: str, limit: int = 5) -> str:
    ignore = {".Spotlight-V100", ".fseventsd", ".TemporaryItems", ".DS_Store"}
    try:
        entries = sorted(
            e for e in os.listdir(path)
            if not e.startswith(".") and e not in ignore
        )
    except OSError:
        return "(unreadable)"
    if not entries:
        return "(empty)"
    suffix = f"  +{len(entries)-limit} more" if len(entries) > limit else ""
    return ",  ".join(entries[:limit]) + suffix

def _get_mac_roots() -> list[tuple[str, str, str]]:
    base        = "/Volumes"
    share_names = _smb_share_names()
    results     = []
    try:
        for name in sorted(os.listdir(base)):
            full = os.path.join(base, name) + "/"
            if not os.path.isdir(full.rstrip("/")):
                continue
            share = share_names.get(full, "")
            if re.fullmatch(r"[A-Za-z]", share):
                label   = share.upper() + ":"
                tooltip = f"{full}  (Windows share {share.upper()}:)  ▸  {_peek(full)}"
            else:
                label   = share if share else name
                tooltip = f"{full}  ▸  {_peek(full)}"
            results.append((full, label, tooltip))
    except OSError:
        pass
    return results

def _get_linux_roots() -> list[tuple[str, str, str]]:
    results = []
    for base in ("/mnt", "/media"):
        if os.path.exists(base):
            try:
                for name in sorted(os.listdir(base)):
                    full = os.path.join(base, name) + "/"
                    if os.path.isdir(full.rstrip("/")):
                        results.append((full, name, f"{full}  ▸  {_peek(full)}"))
            except OSError:
                pass
    return results

def _get_available_roots() -> list[tuple[str, str, str]]:
    if IS_WINDOWS: return _get_windows_roots()
    if IS_MAC:     return _get_mac_roots()
    return _get_linux_roots()


# ---------------------------------------------------------------------------
# Missing-root extraction
#
# Special cases beyond drive letters and /Volumes:
#
#   /Users/<name>/…   Mac home directory — capture 2 levels so different
#   /home/<name>/…    users don't all collapse to the same root.
#
#   /Volumes\amd\…    Mac path opened on Windows (slashes flipped by OS).
#
# The returned root must EXACTLY match the stored prefix so startswith()
# works in Apply — we never normalise slashes here.
# ---------------------------------------------------------------------------

_COLON_DRIVE_RE = re.compile(r"^([A-Za-z]):/")
_SLASH_DRIVE_RE = re.compile(r"^/([A-Za-z])/")
_UNC_RE         = re.compile(r"^(\\\\[^\\]+\\[^\\]+\\)")   # \\server\share\


def _missing_root_of(raw: str) -> str | None:
    if not raw:
        return None

    # 1. UNC path  \\server\share\  (Windows, or Mac path seen from Windows)
    m = _UNC_RE.match(raw)
    if m:
        return m.group(1)

    # 2. Windows drive letter  E:\  or  E:/
    #    Walk segments until the first missing directory, but cap at depth 2.
    #    This handles two cases:
    #      C:\Work\file.jpg        C:\ exists, C:\Work\ missing  →  C:\Work\
    #      C:\Users\riouxr\x.jpg  C:\ and C:\Users\ and C:\Users\riouxr\ all
    #                               exist locally, cap at 2  →  C:\Users\riouxr\
    m = re.match(r"^([A-Za-z]):([/\\])", raw)
    if m:
        drive = m.group(1).upper() + ":" + m.group(2)
        if not os.path.exists(drive.rstrip("/\\")):
            return drive   # drive doesn't exist at all
        parts = [p for p in re.split(r"[/\\]", raw) if p]
        # parts[0]="C:"  parts[1]=first_dir  parts[2]=second_dir ...
        built = parts[0]
        depth = 0
        for part in parts[1:]:
            candidate = built + "\\" + part
            if not os.path.isdir(candidate) or depth >= 1:
                return built + "\\" + part + "\\"
            built = candidate
            depth += 1
        return drive

    # 3. /e/…  — Blender's older Unix encoding of a Windows drive letter
    m = _SLASH_DRIVE_RE.match(raw)
    if m:
        candidate = "/" + m.group(1).lower() + "/"
        return candidate if not os.path.exists(candidate) else None

    # 4. /Volumes/name/  or  /Volumes\name\  (mixed slashes when Windows
    #    opens a Mac .blend — OS converts separators but keeps "/Volumes")
    m = re.match(r"^/Volumes([/\\])([^/\\]+)[/\\]", raw)
    if m:
        sep  = m.group(1)
        name = m.group(2)
        return "/Volumes" + sep + name + sep

    # 5. User home directories — always go 2 levels deep to capture username.
    #    Forward slashes (Mac native):   /Users/Bob/Desktop/…  →  /Users/Bob/
    #    Mixed slashes (Windows opens Mac .blend, OS flips them):
    #      /Users\Bob\Desktop\…  →  /Users\Bob\
    #    Both cases: split on either separator to extract the username.
    for base in ("/Users", "/home"):
        if raw.startswith(base):
            rest = raw[len(base):]
            if not rest or rest[0] not in ("/", "\\"):
                continue
            sep = rest[0]
            after = rest[1:]
            username = re.split(r"[/\\]", after)[0]
            if username:
                return base + sep + username + sep   # /Users/Bob/ or /Users\Bob\
            # No username segment — fall back to just the base
            return base + sep

    # 6. Other fixed-depth Unix mounts  /mnt/<n>/  /media/<n>/
    for prefix in ("/mnt/", "/media/"):
        if raw.startswith(prefix):
            name = raw[len(prefix):].split("/")[0]
            if name:
                return prefix + name + "/"

    # 7. Bare  /something/…  — single segment only
    parts = raw.lstrip("/").split("/")
    if parts and parts[0]:
        return "/" + parts[0] + "/"

    return None



def _has_dotdot(raw: str) -> bool:
    """
    True if the path contains any .. segments — meaning it's a relative
    path masquerading as absolute (e.g. /../../../../Work/file.jpg).
    os.path.isabs() returns True for these on Windows, so we can't use it.
    """
    return bool(re.search(r"(^|[/\\])\.\.([$\\/])", raw))


def _relative_root(raw: str) -> str | None:
    """
    Strip all dot-segments from a relative path and return the meaningful
    root prefix, backslash-wrapped.
      /../../../../Work/brokenrocks.jpg      ->  \\Work\\
      /../../Users/riouxr/Desktop/Ice.png   ->  \\Users\\riouxr\\
      ..\\..\\home\\bob\\file.png         ->  \\home\\bob\\
    Users/home go two levels deep (like absolute paths) so the username
    is included and doesn't get doubled when building the remapped path.
    """
    parts = [p for p in re.split(r"[/\\]", raw)
             if p and not re.fullmatch(r"\.+", p)]
    if not parts:
        return None
    if parts[0].lower() in ("users", "home") and len(parts) >= 2:
        return "\\" + parts[0] + "\\" + parts[1] + "\\"
    return "\\" + parts[0] + "\\"


def _collect_missing_roots() -> list[str]:
    roots: set[str] = set()
    for col_name in _COLLECTIONS:
        for block in getattr(bpy.data, col_name):
            raw = getattr(block, "filepath", None)
            if not raw:
                continue

            abs_path = bpy.path.abspath(raw)
            if os.path.isfile(abs_path):
                continue

            # Relative path — don't trust abspath (resolves to wrong place
            # on a different OS). Strip the ../ noise, surface first real
            # segment as  \ShareName\  so the user can remap it.
            if _has_dotdot(raw):
                root = _relative_root(raw)
            else:
                root = _missing_root_of(raw)

            if root:
                roots.add(root)
    return sorted(roots)



# ---------------------------------------------------------------------------
# Smart target suggestions & automatic mapping
# ---------------------------------------------------------------------------

def _suggest_target(missing_root: str) -> str | None:
    """
    Return the best local target for a given missing root, or None.
    Works on all platforms — Mac uses SMB share names to match drive letters.
    """
    # Extract a drive letter from the missing root if present.
    # Handles:  E:/   E:\   /e/   /E/
    m = (re.match(r"^([A-Za-z]):[/\\]", missing_root) or
         re.match(r"^/([A-Za-z])/", missing_root))
    missing_letter = m.group(1).upper() if m else None

    if IS_WINDOWS:
        if missing_letter:
            candidate = missing_letter + ":\\"
            return candidate if os.path.exists(candidate) else None
        if missing_root.rstrip("/\\") in ("/Users", "\\Users"):
            for letter in ["C"] + list("ABDEFGHIJKLMNOPQRSTUVWXYZ"):
                candidate = f"{letter}:\\Users\\"
                if os.path.exists(candidate):
                    return candidate
        return None

    if IS_MAC:
        if missing_letter:
            # Ask `mount` which /Volumes/… is the share with that letter
            share_map = _smb_share_names()   # /Volumes/x/ -> "E"
            for mount_path, share_name in share_map.items():
                if share_name.upper() == missing_letter:
                    return mount_path
            # No SMB match — fall back: is there a /Volumes/<letter>/ ?
            candidate = f"/Volumes/{missing_letter}/"
            if os.path.exists(candidate):
                return candidate
            candidate = f"/Volumes/{missing_letter.lower()}/"
            if os.path.exists(candidate):
                return candidate
        # /Users/bob/  — already local, shouldn't be missing unless
        # this is someone else's file; no suggestion possible
        return None

    # Linux
    if missing_letter:
        for base in ("/mnt", "/media"):
            for name in (missing_letter, missing_letter.lower()):
                candidate = os.path.join(base, name) + "/"
                if os.path.exists(candidate):
                    return candidate
    return None


def _auto_map_all() -> list[tuple[str, str]]:
    """
    Return list of (source, target) pairs that can be mapped automatically.
    Only includes pairs where _suggest_target returns a confident local path.
    """
    pairs = []
    for root in _collect_missing_roots():
        target = _suggest_target(root)
        if target:
            pairs.append((root, target))
    return pairs


# ---------------------------------------------------------------------------
# Human-readable labels for the missing-root dropdown
# ---------------------------------------------------------------------------

def _label_missing(root: str) -> str:
    # \\server\share\  →  share  (\\server\share)
    m = _UNC_RE.match(root)
    if m:
        parts = root.rstrip("\\").split("\\")
        return f"{parts[-1]}  ({root.rstrip(chr(92))})"

    # E:/  or  E:\  →  E:
    m = re.match(r"^([A-Za-z]):[/\\]", root)
    if m:
        return m.group(1).upper() + ":"

    # /e/  →  E:
    m = _SLASH_DRIVE_RE.match(root)
    if m:
        return m.group(1).upper() + ":"

    # /Volumes/amd-1/  →  amd-1      /Volumes\Work\  →  Work
    m = re.match(r"^/Volumes[/\\](.+?)[/\\]?$", root)
    if m:
        return m.group(1)

    # /Users/bob/  →  ~/bob  (makes it obvious it's a home dir)
    m = re.match(r"^/Users/(.+?)/?$", root)
    if m:
        return "~/" + m.group(1)

    m = re.match(r"^/home/(.+?)/?$", root)
    if m:
        return "~/" + m.group(1)

    return root.rstrip("/\\")


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------


class PATHMAPPER_OT_auto_map(Operator):
    bl_idname      = "pathmapper.auto_map"
    bl_label       = "Automatic Mapping"
    bl_description = (
        "Automatically remap roots with an obvious local equivalent: "
        "same drive letter, or /Users mapped to C:\\Users\\"
    )

    def execute(self, context):
        pairs = _auto_map_all()
        if not pairs:
            self.report({'WARNING'},
                "No automatic mappings found — "
                "use Scan then map manually.")
            return {'CANCELLED'}

        total_changed = 0
        messages = []

        for source, target in pairs:
            # Snapshot originals
            snapshot = []
            for col_name in _COLLECTIONS:
                for block in getattr(bpy.data, col_name):
                    raw = getattr(block, "filepath", None)
                    if not raw:
                        continue
                    abs_path = bpy.path.abspath(raw)
                    if abs_path.startswith(source):
                        snapshot.append({"col": col_name, "name": block.name,
                                         "path": raw, "abs": abs_path})

            if not snapshot:
                continue

            # Merge into backup
            existing = _read_backup()
            index = {(e["col"], e["name"]): e for e in existing}
            for entry in snapshot:
                key = (entry["col"], entry["name"])
                if key not in index:
                    index[key] = {"col": entry["col"], "name": entry["name"],
                                  "path": entry["path"]}
            _write_backup(list(index.values()))

            # Apply using absolute path so relative paths are expanded
            for entry in snapshot:
                block = getattr(bpy.data, entry["col"]).get(entry["name"])
                if block:
                    block.filepath = entry["abs"].replace(source, target, 1)
            total_changed += len(snapshot)
            messages.append(f"{source} → {target} ({len(snapshot)})")

        if total_changed:
            self.report({'INFO'},
                f"Auto-mapped {total_changed} path(s): " + "  |  ".join(messages))
        else:
            self.report({'WARNING'}, "Mappings found but no paths were updated.")

        # Refresh the dropdowns so they reflect the newly remapped state
        bpy.ops.pathmapper.scan()
        return {'FINISHED'}


class PATHMAPPER_OT_scan(Operator):
    bl_idname      = "pathmapper.scan"
    bl_label       = "Scan Scene"
    bl_description = ("Detect broken file-path roots in this scene "
                      "and refresh all mounted / connected volumes")

    def execute(self, context):
        global _missing_cache, _available_cache

        missing   = _collect_missing_roots()
        available = _get_available_roots()

        _missing_cache = (
            [(r, _label_missing(r), r) for r in missing]
            if missing else [("NONE", "No broken paths found", "")]
        )
        _available_cache = (
            available if available else [("NONE", "No volumes found", "")]
        )

        prefs = context.preferences.addons[__package__].preferences
        prefs.missing_root = _missing_cache[0][0]
        prefs.target_root  = _available_cache[0][0]

        self.report({'INFO'},
            f"Scan complete — {len(missing)} missing root(s), "
            f"{len(available)} volume(s) found.")
        return {'FINISHED'}


class PATHMAPPER_OT_apply(Operator):
    bl_idname      = "pathmapper.apply"
    bl_label       = "Apply Mapping"
    bl_description = ("Remap the missing root to the chosen volume and "
                      "save original paths inside the .blend for later revert")

    def execute(self, context):
        prefs  = context.preferences.addons[__package__].preferences
        source = prefs.missing_root
        # Manual path overrides the enum when set
        target = prefs.manual_target.strip() or prefs.target_root

        if not source or source == "NONE":
            self.report({'WARNING'}, "No missing root selected — run Scan first.")
            return {'CANCELLED'}
        if not target or target == "NONE":
            self.report({'WARNING'},
                "No target selected — pick from the list or browse manually.")
            return {'CANCELLED'}
        # Ensure target ends with a separator so startswith replacement works cleanly
        if target and target[-1] not in ("/", "\\"):
            target = target + ("/" if "/" in target else "\\")

        # Snapshot originals before touching anything.
        # For relative paths we match via _relative_root(raw) since abspath
        # resolves to the wrong location on a foreign OS.
        snapshot: list[dict] = []
        for col_name in _COLLECTIONS:
            for block in getattr(bpy.data, col_name):
                raw = getattr(block, "filepath", None)
                if not raw:
                    continue
                if _has_dotdot(raw):
                    if _relative_root(raw) == source:
                        snapshot.append({"col": col_name, "name": block.name,
                                         "path": raw, "abs": None})
                else:
                    abs_path = bpy.path.abspath(raw)
                    if abs_path.startswith(source):
                        snapshot.append({"col": col_name, "name": block.name,
                                         "path": raw, "abs": abs_path})

        if not snapshot:
            self.report({'WARNING'},
                f"Nothing remapped — no paths start with '{source}'.  "
                "Run Scan again after checking the correct root is selected.")
            return {'CANCELLED'}

        # Merge into backup keeping the oldest original for each block
        existing = _read_backup()
        index = {(e["col"], e["name"]): e for e in existing}
        for entry in snapshot:
            key = (entry["col"], entry["name"])
            if key not in index:
                index[key] = {"col": entry["col"], "name": entry["name"],
                               "path": entry["path"]}   # store raw original
        _write_backup(list(index.values()))

        # Apply
        for entry in snapshot:
            block = getattr(bpy.data, entry["col"]).get(entry["name"])
            if not block:
                continue
            if entry["abs"] is None:
                # Relative path — strip dot-segments, skip as many leading
                # segments as the source root consumed, then append the rest
                raw = entry["path"]
                parts = [p for p in re.split(r"[/\\]", raw)
                         if p and not re.fullmatch(r"\.+", p)]
                source_depth = len([p for p in re.split(r"[/\\]", source.strip("/\\")) if p])
                remaining = parts[source_depth:]
                rest = "\\" + "\\".join(remaining) if remaining else ""
                block.filepath = target.rstrip("/\\") + rest
            else:
                block.filepath = entry["abs"].replace(source, target, 1)

        self.report({'INFO'},
            f"Done — {len(snapshot)} path(s) remapped  ({source}  →  {target}).  "
            "Revert button now available.")
        bpy.ops.pathmapper.scan()
        return {'FINISHED'}


class PATHMAPPER_OT_revert(Operator):
    bl_idname      = "pathmapper.revert"
    bl_label       = "Revert to Original Paths"
    bl_description = ("Restore all file paths to exactly what they were "
                      "before Apply was used on another platform")

    def execute(self, context):
        entries = _read_backup()
        if not entries:
            self.report({'WARNING'}, "No backup found in this .blend file.")
            return {'CANCELLED'}

        restored = skipped = 0
        for entry in entries:
            col   = getattr(bpy.data, entry["col"], None)
            block = col.get(entry["name"]) if col else None
            if block:
                block.filepath = entry["path"]
                restored += 1
            else:
                skipped += 1

        _delete_backup()
        msg = f"Reverted {restored} path(s) to original."
        if skipped:
            msg += f"  ({skipped} block(s) no longer exist — skipped.)"
        self.report({'INFO'}, msg)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Preferences panel
# ---------------------------------------------------------------------------


class PATHMAPPER_OT_clear_manual(Operator):
    bl_idname      = "pathmapper.clear_manual"
    bl_label       = "Clear Manual Path"
    bl_description = "Clear the manually entered target path"

    def execute(self, context):
        prefs = context.preferences.addons[__package__].preferences
        prefs.manual_target = ""
        return {'FINISHED'}


class PATHMAPPER_Preferences(AddonPreferences):
    bl_idname = __package__

    missing_root: EnumProperty(
        name="Missing Root",
        description="Broken root detected in this scene's file paths",
        items=_missing_items,
    )
    target_root: EnumProperty(
        name="Remap To",
        description="Local volume or network share to point those paths at",
        items=_available_items,
    )
    manual_target: StringProperty(
        name="Manual Target",
        description=(
            "Type a path directly — overrides the dropdown when set.  "
            "Use this for UNC paths like \\\\mac.local\\Work\\ "
            "that Windows cannot show in a file browser"
        ),
        default="",
    )

    def draw(self, context):
        layout = self.layout

        # Revert banner — only shown when a backup lives in this .blend
        if _backup_exists():
            box = layout.box()
            box.alert = True
            box.label(
                text="Original paths from another platform are saved in this file.",
                icon="FILE_TICK"
            )
            box.operator("pathmapper.revert", icon="LOOP_BACK")
            layout.separator()

        row = layout.row(align=True)
        row.operator("pathmapper.auto_map", icon="SHADERFX")
        row.operator("pathmapper.scan", icon="FILE_REFRESH")
        layout.separator()

        col = layout.column(align=True)
        col.label(text="Missing root  (detected from broken paths):")
        col.prop(self, "missing_root", text="")
        # Show suggestion if this root has an obvious local equivalent
        suggestion = _suggest_target(self.missing_root) if self.missing_root not in ("", "NONE") else None
        if suggestion:
            hint = col.row()
            hint.alert = False
            hint.label(text=f"Suggested target: {suggestion}", icon="LIGHT")

        col.separator()
        col.label(text="Remap to:")

        # Auto-detected list
        col.prop(self, "target_root", text="")

        col.separator()
        # Manual folder browser — always available, especially useful on
        # Windows where UNC / network paths don't appear in the enum list
        box = col.box()
        box.label(
            text="Or type a path manually (overrides list above when set):",
            icon="SYNTAX_OFF"
        )
        if IS_WINDOWS:
            box.label(text="e.g.   \\\\mac.local\\Work\\   or   E:\\",
                      icon="INFO")
        row = box.row(align=True)
        row.prop(self, "manual_target", text="")
        if self.manual_target:
            row.operator("pathmapper.clear_manual", text="", icon="X")

        col.separator()
        col.operator("pathmapper.apply", icon="CHECKMARK")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

classes = (
    PATHMAPPER_OT_auto_map,
    PATHMAPPER_OT_scan,
    PATHMAPPER_OT_apply,
    PATHMAPPER_OT_revert,
    PATHMAPPER_OT_clear_manual,
    PATHMAPPER_Preferences,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
