#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

CONFIG_VERSION = 1
DEFAULT_ROOTS = ("~/GitHub", "~/GitLab", "~/Code", "~/Projects")
DEFAULT_LAUNCHPAD_HOST = "127.0.0.1"
DEFAULT_LAUNCHPAD_PORT = 8765
DEFAULT_MARKDOWN_PATH = "~/.zed-projects/launchpad.md"
PROJECT_MARKERS = (
    ".git",
    ".zed",
    ".vscode",
    "Cargo.toml",
    "package.json",
    "pyproject.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "composer.json",
)
SKIP_DIRS = {
    ".cache",
    ".git",
    ".hg",
    ".svn",
    ".terraform",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "out",
    "target",
    "vendor",
}


def zed_config_path() -> Path:
    configured = os.environ.get("ZED_PROJECTS_CONFIG")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".zed-projects.json"


def vscode_config_path() -> Path:
    return Path.home() / ".vscode-recent-projects.json"


def default_data() -> dict:
    return {"version": CONFIG_VERSION, "projects": [], "groups": []}


def normalize_path(path: str) -> str:
    return str(Path(path).expanduser().resolve(strict=False)).rstrip("/")


def shorten_path(path: str) -> str:
    home = str(Path.home())
    if path == home:
        return "~"
    if path.startswith(home + "/"):
        return "~" + path[len(home) :]
    return path


def read_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    projects = data.get("projects", [])
    groups = data.get("groups", [])
    if not isinstance(projects, list) or not isinstance(groups, list):
        raise ValueError(f"{path} must contain projects[] and groups[]")
    return {
        "version": int(data.get("version", CONFIG_VERSION)),
        "projects": [p for p in projects if isinstance(p, dict) and p.get("path")],
        "groups": [g for g in groups if isinstance(g, dict) and g.get("id")],
    }


def load_data() -> tuple[dict, Path | None]:
    zed_path = zed_config_path()
    if zed_path.exists():
        return read_json(zed_path), zed_path

    vscode_path = vscode_config_path()
    if vscode_path.exists():
        return read_json(vscode_path), vscode_path

    return default_data(), None


def save_data(data: dict) -> Path:
    target = zed_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": CONFIG_VERSION,
        "projects": data.get("projects", []),
        "groups": data.get("groups", []),
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def project_name(path: str) -> str:
    name = Path(path).name
    return name or path


def project_sort_key(project: dict) -> tuple[int, int, str]:
    pinned = 0 if project.get("pinned") else 1
    return (pinned, -int(project.get("lastOpened") or 0), str(project.get("name") or "").lower())


def group_by_id(data: dict) -> dict[str, dict]:
    return {str(group.get("id")): group for group in data.get("groups", [])}


def project_icon(project: dict) -> str:
    icon = str(project.get("icon") or "").strip()
    if icon.startswith("http://") or icon.startswith("https://"):
        return "[img]"
    return icon or "[ ]"


def is_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def relative_time(timestamp: object) -> str:
    try:
        seconds = max(0, int(time.time() - (int(timestamp) / 1000)))
    except Exception:
        return "never"
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days < 7:
        return f"{days}d ago"
    weeks = days // 7
    if weeks < 4:
        return f"{weeks}w ago"
    return f"{days // 30}mo ago"


def filtered_projects(data: dict, query: str = "") -> list[dict]:
    projects = list(data.get("projects", []))
    query = query.strip().lower()
    if query:
        projects = [
            project
            for project in projects
            if query in str(project.get("name") or "").lower()
            or query in str(project.get("path") or "").lower()
            or query in str(project.get("icon") or "").lower()
        ]
    return projects


def launchpad_order(data: dict, query: str = "") -> list[dict]:
    projects = filtered_projects(data, query)
    ordered: list[dict] = []
    seen: set[str] = set()

    for group in data.get("groups", []):
        group_id = str(group.get("id"))
        for project in projects:
            project_path = normalize_path(str(project.get("path")))
            if project_path in seen:
                continue
            if str(project.get("groupId") or "") == group_id:
                ordered.append(project)
                seen.add(project_path)

    for project in projects:
        project_path = normalize_path(str(project.get("path")))
        if project_path not in seen:
            ordered.append(project)
            seen.add(project_path)

    return ordered


def find_project(data: dict, selector: str) -> dict:
    selector = selector.strip()
    if not selector:
        raise SystemExit("Usage: /projects <project-name-or-path>")

    projects = list(data.get("projects", []))
    selector_lower = selector.lower()
    selector_path = normalize_path(selector)

    if selector.isdigit():
        index = int(selector) - 1
        ordered = launchpad_order(data)
        if 0 <= index < len(ordered):
            return ordered[index]
        raise SystemExit(f"No project exists at launchpad index: {selector}")

    for project in projects:
        if normalize_path(str(project.get("path"))) == selector_path:
            return project

    for project in projects:
        if str(project.get("name") or "").lower() == selector_lower:
            return project

    matches = [
        project
        for project in projects
        if selector_lower in str(project.get("name") or "").lower()
        or selector_lower in str(project.get("path") or "").lower()
    ]
    if not matches:
        raise SystemExit(f"No project matches: {selector}")

    matches.sort(key=project_sort_key)
    return matches[0]


def add_or_update_project(data: dict, path: str, pinned: bool, move_to_top: bool = True) -> dict:
    normalized = normalize_path(path)
    projects = data.setdefault("projects", [])
    now_ms = int(time.time() * 1000)

    for index, project in enumerate(projects):
        if normalize_path(str(project.get("path"))) == normalized:
            project["name"] = project.get("name") or project_name(normalized)
            project["lastOpened"] = now_ms
            if pinned:
                project["pinned"] = True
            if move_to_top and not project.get("pinned") and not project.get("groupId"):
                projects.pop(index)
                projects.insert(0, project)
            return project

    project = {
        "path": normalized,
        "name": project_name(normalized),
        "lastOpened": now_ms,
    }
    if pinned:
        project["pinned"] = True
    if move_to_top:
        projects.insert(0, project)
    else:
        projects.append(project)
    return project


def touch_project(data: dict, project: dict) -> None:
    add_or_update_project(data, str(project.get("path")), pinned=bool(project.get("pinned")))


def render_project(project: dict, groups: dict[str, dict], shortcut: int | None = None) -> str:
    pinned = "*" if project.get("pinned") else " "
    name = str(project.get("name") or project_name(str(project.get("path"))))
    path = shorten_path(str(project.get("path")))
    color = str(project.get("color") or "").strip()
    group = groups.get(str(project.get("groupId") or ""))
    group_suffix = f" [{group.get('name')}]" if group else ""
    color_suffix = f" {color}" if color else ""
    shortcut_suffix = f"    [{shortcut}]" if shortcut and shortcut <= 99 else ""
    return (
        f"  {pinned} {project_icon(project)} {name}{group_suffix}{shortcut_suffix}\n"
        f"      {path} - {relative_time(project.get('lastOpened'))}{color_suffix}"
    )


def cmd_render(args: list[str], launchpad: bool = False) -> str:
    query = " ".join(args)
    data, source = load_data()
    projects = filtered_projects(data, query)
    groups = group_by_id(data)
    lines: list[str] = []

    if launchpad:
        lines.append("Project Launchpad")
        lines.append("Welcome back to Zed")
        lines.append("")
        lines.append("GET STARTED")
        lines.append("  + New File")
        lines.append("  folder Open Project      /project-add <path>")
        lines.append("  search Scan Repositories /project-scan [root]")
        lines.append("  command Settings         /project-config")
        lines.append("")
    else:
        lines.append("Projects")

    if not launchpad:
        lines.append(f"Source: {shorten_path(str(source or zed_config_path()))}")
        lines.append(f"Projects: {len(projects)} of {len(data.get('projects', []))}")
        lines.append("")

    if not data.get("projects"):
        lines.append("No projects yet. Use /project-scan or /project-add <path>.")
        return "\n".join(lines)

    if launchpad:
        lines.append("RECENT PROJECTS" if not query else f'PROJECTS MATCHING "{query}"')
    else:
        lines.append("Open: /projects <name-or-path>. Completion opens immediately.")
        lines.append("Manage: /project-icon <project> <icon-or-url>, /project-color <project> <#RRGGBB>, /project-pin <project> [on|off|toggle].")
    lines.append("")

    rendered_any = False
    shortcut = 1
    for group in data.get("groups", []):
        group_projects = [p for p in projects if str(p.get("groupId") or "") == str(group.get("id"))]
        if not group_projects:
            continue
        rendered_any = True
        icon = str(group.get("icon") or "").strip()
        if icon.startswith("http://") or icon.startswith("https://"):
            icon = "[img]"
        color = f" {group.get('color')}" if group.get("color") else ""
        lines.append(f"{icon + ' ' if icon else ''}{group.get('name')} ({len(group_projects)}){color}")
        for project in group_projects:
            lines.append(render_project(project, groups, shortcut))
            shortcut += 1
        lines.append("")

    ungrouped = [p for p in projects if not p.get("groupId")]
    if ungrouped:
        rendered_any = True
        if query:
            header = "Matching Projects"
        elif launchpad:
            header = "OTHER PROJECTS" if data.get("groups") else ""
        else:
            header = "Other Projects" if data.get("groups") else "Projects"
        if header:
            lines.append(header)
        for project in ungrouped:
            lines.append(render_project(project, groups, shortcut))
            shortcut += 1

    if not rendered_any:
        lines.append(f"No projects match: {query}")

    return "\n".join(lines).rstrip()


def cmd_completions(args: list[str]) -> str:
    mode = args[0] if args else "run"
    query = " ".join(args[1:])
    data, _source = load_data()
    groups = group_by_id(data)
    projects = filtered_projects(data, query)
    projects.sort(key=project_sort_key)

    completions = []
    for project in projects[:80]:
        name = str(project.get("name") or project_name(str(project.get("path"))))
        path = str(project.get("path"))
        group = groups.get(str(project.get("groupId") or ""))
        group_suffix = f" - {group.get('name')}" if group else ""
        pin = "* " if project.get("pinned") else ""
        completions.append(
            {
                "label": f"{pin}{project_icon(project)} {name}{group_suffix} - {shorten_path(path)}",
                "new_text": path,
                "run_command": mode == "run",
            }
        )

    return json.dumps(completions, ensure_ascii=False)


def zed_binary() -> str:
    configured = os.environ.get("ZED_PROJECTS_ZED_BIN")
    if configured:
        return configured
    found = shutil.which("zed")
    if found:
        return found
    raise SystemExit("Could not find the zed CLI. Set ZED_PROJECTS_ZED_BIN to the zed binary path.")


def zed_open_command(path: str | None = None, new_file: bool = False) -> list[str]:
    mode = os.environ.get("ZED_PROJECTS_OPEN_MODE", "new").strip().lower()
    command = [zed_binary()]
    if new_file:
        command.append("--new")
        return command
    if mode == "new":
        command.append("--new")
    elif mode == "existing":
        command.append("--existing")
    elif mode == "add":
        command.append("--add")
    elif mode not in ("default", ""):
        raise SystemExit("ZED_PROJECTS_OPEN_MODE must be one of: new, existing, add, default")
    if path:
        command.append(path)
    return command


def open_project(data: dict, project: dict) -> str:
    path = str(project.get("path"))
    if not Path(path).exists():
        raise SystemExit(f"Project path does not exist: {path}")

    touch_project(data, project)
    save_data(data)
    subprocess.Popen(zed_open_command(path), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return f"Opening {project.get('name') or project_name(path)}"


def cmd_open(args: list[str]) -> str:
    selector = " ".join(args)
    data, _source = load_data()
    project = find_project(data, selector)
    message = open_project(data, project)
    return f"{message}\n{project.get('path')}"


def cmd_add(args: list[str], pinned: bool = True, move_to_top: bool = True) -> str:
    path = " ".join(args).strip()
    if not path:
        raise SystemExit("Usage: /project-add <path>")
    normalized = normalize_path(path)
    if not Path(normalized).is_dir():
        raise SystemExit(f"Project path is not a directory: {normalized}")

    data, _source = load_data()
    project = add_or_update_project(data, normalized, pinned=pinned, move_to_top=move_to_top)
    saved = save_data(data)
    return f"Added {project.get('name')}\n{project.get('path')}\nSaved: {shorten_path(str(saved))}"


def cmd_icon(args: list[str]) -> str:
    if len(args) < 2:
        raise SystemExit("Usage: /project-icon <project> <emoji-or-image-url|clear>")
    icon = args[-1]
    selector = " ".join(args[:-1])
    data, _source = load_data()
    project = find_project(data, selector)
    if icon.lower() in ("clear", "none", "reset", "-"):
        project.pop("icon", None)
        icon = "default"
    else:
        project["icon"] = icon
    save_data(data)
    return f"Set icon for {project.get('name')} to {icon}"


def cmd_color(args: list[str]) -> str:
    if len(args) < 2:
        raise SystemExit("Usage: /project-color <project> <#RRGGBB|clear>")
    color = args[-1]
    selector = " ".join(args[:-1])
    data, _source = load_data()
    project = find_project(data, selector)
    if color.lower() in ("clear", "none", "reset", "-"):
        project.pop("color", None)
        color = "default"
    else:
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            raise SystemExit("Color must be a hex value like #4A90D9, or clear.")
        project["color"] = color
    save_data(data)
    return f"Set color for {project.get('name')} to {color}"


def cmd_pin(args: list[str]) -> str:
    if not args:
        raise SystemExit("Usage: /project-pin <project> [on|off|toggle]")
    state = "toggle"
    if args[-1].lower() in ("on", "off", "true", "false", "toggle"):
        state = args[-1].lower()
        selector = " ".join(args[:-1])
    else:
        selector = " ".join(args)
    data, _source = load_data()
    project = find_project(data, selector)

    if state in ("on", "true"):
        project["pinned"] = True
    elif state in ("off", "false"):
        project.pop("pinned", None)
    else:
        if project.get("pinned"):
            project.pop("pinned", None)
        else:
            project["pinned"] = True

    save_data(data)
    return f"{'Pinned' if project.get('pinned') else 'Unpinned'} {project.get('name')}"


def cmd_group(args: list[str]) -> str:
    if len(args) < 2:
        raise SystemExit("Usage: /project-group <project> <group-name|clear>")
    group_name = args[-1]
    selector = " ".join(args[:-1])
    data, _source = load_data()
    project = find_project(data, selector)

    if group_name.lower() in ("clear", "none", "ungroup", "-"):
        project.pop("groupId", None)
        save_data(data)
        return f"Removed {project.get('name')} from its group"

    groups = data.setdefault("groups", [])
    existing = next((g for g in groups if str(g.get("name") or "").lower() == group_name.lower()), None)
    if not existing:
        existing = {"id": str(int(time.time() * 1000)), "name": group_name}
        groups.append(existing)
    project["groupId"] = existing["id"]
    save_data(data)
    return f"Assigned {project.get('name')} to {existing.get('name')}"


def remove_project_entry(data: dict, selector: str) -> str:
    project = find_project(data, selector)
    path = normalize_path(str(project.get("path")))
    before = len(data.get("projects", []))
    data["projects"] = [
        entry
        for entry in data.get("projects", [])
        if normalize_path(str(entry.get("path"))) != path
    ]
    if len(data["projects"]) == before:
        raise SystemExit(f"No project matches: {selector}")
    save_data(data)
    return f"Removed {project.get('name') or project_name(path)}"


def set_project_meta(data: dict, selector: str, **meta: object) -> str:
    project = find_project(data, selector)
    for key, value in meta.items():
        if value is None or value == "":
            project.pop(key, None)
        else:
            project[key] = value
    save_data(data)
    return f"Updated {project.get('name') or project_name(str(project.get('path')))}"


def set_project_group_id(data: dict, selector: str, group_id: str | None) -> str:
    project = find_project(data, selector)
    if group_id:
        if not any(str(group.get("id")) == str(group_id) for group in data.get("groups", [])):
            raise SystemExit(f"No group exists with id: {group_id}")
        project["groupId"] = str(group_id)
        group = next(group for group in data.get("groups", []) if str(group.get("id")) == str(group_id))
        message = f"Assigned {project.get('name')} to {group.get('name')}"
    else:
        project.pop("groupId", None)
        message = f"Removed {project.get('name')} from its group"
    save_data(data)
    return message


def create_group(data: dict, name: str) -> dict:
    name = name.strip()
    if not name:
        raise SystemExit("Group name cannot be empty.")
    groups = data.setdefault("groups", [])
    existing = next((group for group in groups if str(group.get("name") or "").lower() == name.lower()), None)
    if existing:
        return existing
    group = {"id": str(int(time.time() * 1000)), "name": name}
    groups.append(group)
    save_data(data)
    return group


def find_group(data: dict, group_id: str) -> dict:
    for group in data.get("groups", []):
        if str(group.get("id")) == str(group_id):
            return group
    raise SystemExit(f"No group exists with id: {group_id}")


def update_group(data: dict, group_id: str, **meta: object) -> str:
    group = find_group(data, group_id)
    for key, value in meta.items():
        if value is None or value == "":
            group.pop(key, None)
        else:
            group[key] = value
    save_data(data)
    return f"Updated group {group.get('name')}"


def delete_group(data: dict, group_id: str) -> str:
    group = find_group(data, group_id)
    data["groups"] = [entry for entry in data.get("groups", []) if str(entry.get("id")) != str(group_id)]
    for project in data.get("projects", []):
        if str(project.get("groupId") or "") == str(group_id):
            project.pop("groupId", None)
    save_data(data)
    return f"Deleted group {group.get('name')}"


def toggle_group_collapsed(data: dict, group_id: str) -> str:
    group = find_group(data, group_id)
    group["collapsed"] = not bool(group.get("collapsed"))
    save_data(data)
    return f"{'Collapsed' if group.get('collapsed') else 'Expanded'} {group.get('name')}"


def has_project_marker(path: Path) -> bool:
    return any((path / marker).exists() for marker in PROJECT_MARKERS)


def iter_project_dirs(root: Path, max_depth: int) -> list[Path]:
    found: list[Path] = []
    stack: list[tuple[Path, int]] = [(root, 0)]
    seen: set[Path] = set()

    while stack:
        path, depth = stack.pop()
        try:
            resolved = path.resolve(strict=False)
        except Exception:
            continue
        if resolved in seen or not path.is_dir():
            continue
        seen.add(resolved)

        if path != root and has_project_marker(path):
            found.append(path)
            continue

        if depth >= max_depth:
            continue

        try:
            children = sorted(path.iterdir(), key=lambda child: child.name.lower())
        except OSError:
            continue
        for child in reversed(children):
            if not child.is_dir():
                continue
            if child.name in SKIP_DIRS:
                continue
            if child.name.startswith(".") and child.name not in (".config",):
                continue
            stack.append((child, depth + 1))

    return found


def cmd_scan(args: list[str]) -> str:
    roots = args or list(DEFAULT_ROOTS)
    data, _source = load_data()
    before = len(data.get("projects", []))
    scanned_roots: list[str] = []

    for root_arg in roots:
        root = Path(root_arg).expanduser()
        if not root.is_dir():
            continue
        scanned_roots.append(shorten_path(str(root.resolve(strict=False))))
        for project_dir in iter_project_dirs(root, max_depth=6):
            add_or_update_project(data, str(project_dir), pinned=False, move_to_top=False)

    saved = save_data(data)
    after = len(data.get("projects", []))
    added = after - before
    if not scanned_roots:
        return "No scan roots exist. Pass a root path, for example /project-scan ~/GitHub."
    return (
        f"Scanned: {', '.join(scanned_roots)}\n"
        f"Added: {added}\n"
        f"Total projects: {after}\n"
        f"Saved: {shorten_path(str(saved))}"
    )


def cmd_config() -> str:
    data, source = load_data()
    lines = [
        "zed-projects config",
        f"Active source: {shorten_path(str(source or zed_config_path()))}",
        f"Zed config: {shorten_path(str(zed_config_path()))}",
        f"Web launchpad: {launchpad_url(launchpad_host(), launchpad_port())}",
        f"VSCode import source: {shorten_path(str(vscode_config_path()))}",
        f"Projects: {len(data.get('projects', []))}",
        f"Groups: {len(data.get('groups', []))}",
        "",
        "Environment:",
        "  ZED_PROJECTS_CONFIG: custom registry path",
        "  ZED_PROJECTS_LAUNCHPAD_HOST: launchpad host, defaults to 127.0.0.1",
        "  ZED_PROJECTS_LAUNCHPAD_PORT: launchpad port, defaults to 8765",
        "  ZED_PROJECTS_ZED_BIN: custom zed CLI path",
        "  ZED_PROJECTS_OPEN_MODE: new, existing, add, or default",
        "  ZED_PROJECTS_OPEN_LAUNCHPAD: set to 0 to skip opening the web launchpad from bin/zed-projects-start",
    ]
    return "\n".join(lines)


def cmd_help() -> str:
    return "\n".join(
        [
            "zed-projects usage",
            "",
            "This extension opens a local web Project Launchpad because Zed extensions cannot render custom editor UI.",
            "Run /projects without arguments to start/open the localhost launchpad.",
            "",
            "Open the Agent Panel, start a message with '/', and run one of these commands:",
            "",
            "  /projects",
            "      Open the localhost Project Launchpad.",
            "",
            "  /projects 1",
            "      Open launchpad entry 1 in a new Zed window.",
            "",
            "  /projects <name-or-path>",
            "      Open a project by name, partial name, or absolute path.",
            "",
            "  /project-open-launchpad",
            "      Open the localhost Project Launchpad.",
            "",
            "  /project-scan ~/GitHub",
            "      Scan a root folder and add discovered projects.",
            "",
            "  /project-add <path>",
            "      Add one project folder manually.",
            "",
            "  /project-icon <project> <icon-or-url|clear>",
            "  /project-color <project> <#RRGGBB|clear>",
            "  /project-pin <project> [on|off|toggle]",
            "  /project-group <project> <group-name|clear>",
            "      Manage project metadata.",
            "",
            "Terminal launcher:",
            "  bin/zed-projects-launchpad",
            "      Open the localhost Project Launchpad.",
            "",
            "  bin/zed-projects-start",
            "      Start Zed and open the localhost Project Launchpad.",
        ]
    )


def launchpad_markdown_path() -> Path:
    configured = os.environ.get("ZED_PROJECTS_MARKDOWN_PATH", DEFAULT_MARKDOWN_PATH)
    return Path(configured).expanduser().resolve(strict=False)


def command_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def inline_code(value: str) -> str:
    return value.replace("`", "\\`")


def launchpad_markdown() -> str:
    data, source = load_data()
    ordered = launchpad_order(data)
    groups = group_by_id(data)
    lines = [
        "# Welcome back to Zed",
        "",
        "_The editor for what's next_",
        "",
        "## Get Started",
        "",
        "- New File: `zed --new`",
        "- Open Project: `/project-add <path>`",
        "- Scan Repositories: `/project-scan [root]`",
        "- Command Palette: `cmd-shift-p`",
        "",
        "## Recent Projects",
        "",
    ]

    if not ordered:
        lines.extend(
            [
                "No projects yet.",
                "",
                "Use `/project-scan ~/GitHub` or `/project-add <path>` from the Zed Assistant to add projects.",
                "",
            ]
        )
    else:
        for index, project in enumerate(ordered, start=1):
            path = str(project.get("path"))
            name = str(project.get("name") or project_name(path))
            icon = project_icon(project)
            group = groups.get(str(project.get("groupId") or ""))
            group_suffix = f" [{group.get('name')}]" if group else ""
            pinned = " pinned" if project.get("pinned") else ""
            color = str(project.get("color") or "").strip()
            color_suffix = f", {color}" if color else ""
            lines.extend(
                [
                    f"{index}. **{icon} {name}**{group_suffix}",
                    f"   - Path: `{inline_code(shorten_path(path))}`",
                    f"   - Open: `zed --new {command_quote(path)}`",
                    f"   - Slash command: `/projects {index}`",
                    f"   - Last used: {relative_time(project.get('lastOpened'))}{pinned}{color_suffix}",
                    "",
                ]
            )

    lines.extend(
        [
            "## Registry",
            "",
            f"- Source: `{inline_code(shorten_path(str(source or zed_config_path())))}`",
            f"- Projects: {len(data.get('projects', []))}",
            "",
        ]
    )
    return "\n".join(lines)


def write_launchpad_markdown() -> Path:
    target = launchpad_markdown_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(launchpad_markdown(), encoding="utf-8")
    return target


def cmd_markdown(args: list[str]) -> str:
    if args and args[0] == "--print":
        return launchpad_markdown()
    return str(write_launchpad_markdown())


def launchpad_payload() -> dict:
    data, source = load_data()
    ordered = launchpad_order(data)
    projects = []
    groups = []
    index_by_path = {
        normalize_path(str(project.get("path"))): index + 1 for index, project in enumerate(ordered)
    }

    for group in data.get("groups", []):
        groups.append(
            {
                "id": str(group.get("id")),
                "name": str(group.get("name") or "Group"),
                "color": str(group.get("color") or ""),
                "icon": str(group.get("icon") or ""),
                "collapsed": bool(group.get("collapsed")),
            }
        )

    for project in ordered:
        path = str(project.get("path"))
        icon = str(project.get("icon") or "").strip()
        projects.append(
            {
                "index": index_by_path.get(normalize_path(path)),
                "name": str(project.get("name") or project_name(path)),
                "path": path,
                "shortPath": shorten_path(path),
                "icon": icon,
                "iconIsUrl": is_url(icon),
                "color": str(project.get("color") or "#4A90D9"),
                "pinned": bool(project.get("pinned")),
                "groupId": str(project.get("groupId") or ""),
                "lastOpened": relative_time(project.get("lastOpened")),
            }
        )

    return {
        "source": shorten_path(str(source or zed_config_path())),
        "count": len(data.get("projects", [])),
        "groups": groups,
        "projects": projects,
    }


def launchpad_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Zed Projects</title>
	  <style>
	    :root {
	      color-scheme: dark;
	      --bg: #101010;
	      --surface: #151515;
	      --surface-raised: #1a1a1a;
	      --surface-hover: #202020;
	      --text: #d6d6d6;
	      --muted: #9a9a9a;
	      --faint: #747474;
	      --line: #303030;
	      --line-soft: #242424;
	      --focus: #5ea7f7;
	      --danger: #d66b6b;
	      --ok: #78b883;
	      --yellow: #c9ad5c;
	    }
	    * { box-sizing: border-box; }
	    html { min-height: 100%; }
	    body {
	      margin: 0;
	      min-height: 100vh;
	      background: var(--bg);
	      color: var(--text);
	      font: 13px/1.42 -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", sans-serif;
	    }
	    button, input {
	      font: inherit;
	    }
	    button {
	      appearance: none;
	    }
	    .shell {
	      min-height: 100vh;
	      padding: clamp(28px, 6vw, 76px) 22px 56px;
	    }
	    .wrap {
	      width: min(760px, 100%);
	      margin: 0 auto;
	    }
	    .top {
	      display: grid;
	      grid-template-columns: auto minmax(0, 1fr) auto;
	      align-items: center;
	      gap: 16px;
	      margin: 0 0 34px;
	    }
	    .brand-mark {
	      width: 46px;
	      height: 46px;
	      display: grid;
	      place-items: center;
	      color: #cacaca;
	      border: 2px solid #9a9a9a;
	      border-radius: 4px;
	      font-size: 22px;
	      font-weight: 700;
	      line-height: 1;
	    }
	    .title-block {
	      min-width: 0;
	    }
	    h1 {
	      margin: 0;
	      font-size: 24px;
	      font-weight: 600;
	      letter-spacing: 0;
	    }
	    .subtitle {
	      margin: 3px 0 0;
	      color: var(--muted);
	      font-size: 13px;
	      font-style: italic;
	    }
	    .count {
	      color: var(--muted);
	      font-family: "SF Mono", ui-monospace, Menlo, Consolas, monospace;
	      font-size: 12px;
	      white-space: nowrap;
	    }
	    .toolbar {
	      display: grid;
	      grid-template-columns: minmax(240px, 1fr) repeat(5, auto);
	      gap: 8px;
	      margin-bottom: 30px;
	    }
	    .search, .select {
	      min-width: 0;
	      height: 32px;
	      color: var(--text);
	      background: var(--surface);
	      border: 1px solid var(--line);
	      border-radius: 4px;
	      padding: 0 10px;
	      outline: none;
	    }
	    .search::placeholder {
	      color: var(--faint);
	    }
	    .search:focus, .select:focus {
	      border-color: var(--focus);
	    }
	    .btn {
	      height: 30px;
	      display: flex;
	      align-items: center;
	      justify-content: center;
	      gap: 5px;
	      color: var(--muted);
	      background: transparent;
	      border: 1px solid transparent;
	      border-radius: 4px;
	      padding: 0 8px;
	      cursor: pointer;
	      white-space: nowrap;
	    }
	    .btn:hover {
	      color: var(--text);
	      background: var(--surface-hover);
	      border-color: var(--line);
	    }
	    .btn.primary {
	      color: var(--text);
	      background: var(--surface-raised);
	      border-color: var(--line);
	    }
	    .btn.primary:hover {
	      color: #ffffff;
	      border-color: #3d3d3d;
	    }
	    .btn.danger:hover {
	      color: #ffd8d8;
	      border-color: rgba(214, 107, 107, .42);
	      background: rgba(214, 107, 107, .12);
	    }
	    .btn.icon {
	      width: 26px;
	      height: 26px;
	      padding: 0;
	      border-radius: 4px;
	      font-size: 12px;
	    }
	    .section {
	      margin-bottom: 26px;
	    }
	    .group-header {
	      display: flex;
	      align-items: center;
	      gap: 8px;
	      min-height: 32px;
	      margin-bottom: 0;
	      padding: 0;
	      border-bottom: 1px solid var(--line);
	      cursor: pointer;
	      user-select: none;
    }
    .chevron {
      width: 14px;
      color: var(--muted);
      transition: transform 120ms ease;
    }
	    .section.collapsed .chevron { transform: rotate(-90deg); }
	    .section.collapsed .grid { display: none; }
	    .group-dot {
	      width: 8px;
	      height: 8px;
	      border-radius: 999px;
	      background: var(--line);
	      flex: 0 0 auto;
	    }
	    .group-icon {
	      width: 18px;
	      display: inline-flex;
	      justify-content: center;
	      line-height: 1;
    }
    .group-icon img {
      width: 18px;
      height: 18px;
      object-fit: contain;
      border-radius: 4px;
    }
	    .group-name {
	      flex: 1;
	      color: var(--muted);
	      font-size: 11px;
	      font-weight: 600;
	      letter-spacing: .05em;
	      text-transform: uppercase;
	    }
	    .group-count {
	      color: var(--muted);
	      font-size: 11px;
	    }
	    .group-actions {
	      display: flex;
      gap: 4px;
      opacity: 0;
      transition: opacity 100ms ease;
    }
	    .group-header:hover .group-actions { opacity: 1; }
	    .grid {
	      display: flex;
	      flex-direction: column;
	      gap: 0;
	    }
	    .tile {
	      position: relative;
	      min-width: 0;
	      min-height: 54px;
	      display: grid;
	      grid-template-columns: 3px minmax(0, 1fr) auto;
	      align-items: stretch;
	      background: transparent;
	      border: 0;
	      border-bottom: 1px solid var(--line-soft);
	      border-radius: 0;
	      overflow: hidden;
	      cursor: pointer;
	      transition: background 90ms ease;
	    }
	    .tile:hover {
	      background: var(--surface-hover);
	    }
	    .stripe {
	      width: 3px;
	      height: auto;
	      background: #4a90d9;
	      opacity: .9;
	    }
	    .tile-body {
	      display: grid;
	      grid-template-columns: 28px minmax(0, 1fr);
	      align-items: center;
	      gap: 10px;
	      padding: 8px 10px 8px 12px;
	      min-width: 0;
	    }
	    .tile-icon {
	      width: 24px;
	      height: 24px;
	      display: grid;
	      place-items: center;
	      flex: 0 0 auto;
	      color: var(--muted);
	      font-size: 17px;
	      line-height: 1;
	    }
	    .tile-icon.default {
	      opacity: .75;
	    }
	    .tile-icon img {
	      width: 22px;
	      height: 22px;
	      object-fit: contain;
	      border-radius: 4px;
	    }
	    .svg-icon {
	      width: 18px;
	      height: 18px;
	      display: block;
	      fill: none;
	      stroke: currentColor;
	      stroke-width: 1.7;
	      stroke-linecap: round;
	      stroke-linejoin: round;
	    }
	    .tile-info {
	      min-width: 0;
	      display: grid;
	      grid-template-columns: minmax(0, 1fr) auto;
	      align-items: center;
	      column-gap: 12px;
	      row-gap: 1px;
	    }
	    .tile-name {
	      grid-column: 1;
	      color: var(--text);
	      font-size: 13px;
	      font-weight: 520;
	      white-space: nowrap;
	      overflow: hidden;
	      text-overflow: ellipsis;
	    }
	    .tile-path {
	      grid-column: 1;
	      color: var(--muted);
	      font-size: 11px;
	      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
	    .tile-meta {
	      grid-column: 2;
	      grid-row: 1 / span 2;
	      display: flex;
	      align-items: center;
	      justify-content: flex-end;
	      gap: 8px;
	      color: var(--faint);
	      font-size: 11px;
	      white-space: nowrap;
	    }
	    .pill {
	      color: var(--yellow);
    }
	    .shortcut {
	      color: var(--faint);
	      font-family: "SF Mono", ui-monospace, Menlo, Consolas, monospace;
	      font-size: 11px;
	    }
	    .tile-actions {
	      grid-column: 3;
	      display: flex;
	      align-items: center;
	      justify-content: flex-end;
	      gap: 2px;
	      padding: 8px 4px 8px 0;
	      border-top: 0;
	      opacity: 0;
	      transition: opacity 100ms ease;
	    }
	    .tile:hover .tile-actions { opacity: 1; }
	    .tile-actions .btn {
	      height: 26px;
	      padding: 0 7px;
	      font-size: 12px;
	    }
	    .tile-add {
	      min-height: 48px;
	      display: flex;
	      align-items: center;
	      justify-content: flex-start;
	      gap: 10px;
	      padding: 0 12px;
	      color: var(--muted);
	      background: transparent;
	      border: 0;
	      border-bottom: 1px solid var(--line-soft);
	      border-radius: 0;
	      cursor: pointer;
	    }
	    .tile-add:hover {
	      color: var(--focus);
	      background: var(--surface-hover);
	    }
	    .empty {
	      display: grid;
	      place-items: center;
      min-height: 320px;
      color: var(--muted);
      text-align: center;
    }
    .empty strong {
      display: block;
      margin-bottom: 10px;
	      color: var(--text);
	      font-size: 16px;
	    }
	    .status {
	      position: fixed;
      left: 50%;
      bottom: 18px;
      transform: translateX(-50%);
      max-width: min(720px, calc(100vw - 32px));
      min-height: 31px;
	      display: none;
	      align-items: center;
	      padding: 6px 12px;
	      color: var(--text);
	      background: #202020;
	      border: 1px solid var(--line);
	      border-radius: 5px;
	      box-shadow: 0 10px 35px rgba(0,0,0,.35);
	    }
    .status.show { display: flex; }
    .status.ok { color: var(--ok); }
    .status.error { color: var(--danger); }
	    .hidden { display: none !important; }
	    @media (max-width: 760px) {
	      .shell { padding: 24px 16px 48px; }
	      .top {
	        grid-template-columns: auto minmax(0, 1fr);
	      }
	      .count {
	        grid-column: 1 / -1;
	      }
	      .toolbar { grid-template-columns: 1fr 1fr; }
	      .search { grid-column: 1 / -1; }
	      .tile {
	        grid-template-columns: 3px minmax(0, 1fr);
	      }
	      .tile-body {
	        grid-column: 2;
	        padding-right: 8px;
	      }
	      .tile-info {
	        grid-template-columns: minmax(0, 1fr);
	      }
	      .tile-meta {
	        grid-column: 1;
	        grid-row: auto;
	        justify-content: flex-start;
	      }
	      .tile-actions {
	        grid-column: 2;
	        justify-content: flex-start;
	        padding: 0 8px 8px 12px;
	        flex-wrap: wrap;
	      }
	      .tile-actions { opacity: 1; }
	      .group-header {
	        flex-wrap: wrap;
	        padding: 7px 0;
	      }
	      .group-name {
	        flex: 1 1 calc(100% - 112px);
	      }
	      .group-actions {
	        flex: 1 1 100%;
	        opacity: 1;
	        flex-wrap: wrap;
	        justify-content: flex-start;
	        padding-left: 30px;
	      }
	    }
  </style>
</head>
<body>
	  <main class="shell">
	    <div class="wrap">
	      <header class="top">
	        <div class="brand-mark" aria-hidden="true">Z</div>
	        <div class="title-block">
	          <h1>Project Launchpad</h1>
	          <p class="subtitle">The editor for what's next</p>
	        </div>
	        <span class="count" id="count"></span>
	      </header>
	      <div class="toolbar">
	        <input class="search" id="search" autocomplete="off" placeholder="Filter projects">
	        <button class="btn primary" data-action="add-project" type="button">Add Project</button>
	        <button class="btn" data-action="create-group" type="button">New Group</button>
	        <button class="btn" data-action="scan" type="button">Scan</button>
	        <button class="btn" data-action="refresh" type="button">Refresh</button>
	        <button class="btn" data-action="new-file" type="button">New File</button>
      </div>
      <div id="root"></div>
    </div>
  </main>
  <div class="status" id="status"></div>

  <script>
    const state = { projects: [], groups: [], source: "", visible: [] };
    const rootEl = document.getElementById("root");
    const countEl = document.getElementById("count");
	    const searchEl = document.getElementById("search");
	    const statusEl = document.getElementById("status");
	    const folderIcon = `<svg class="svg-icon" viewBox="0 0 18 18" aria-hidden="true"><path d="M2.75 5.25h5l1.4 1.65h6.1v8.1H2.75z"/><path d="M2.75 5.25v-1.6h4.1l1.35 1.6"/></svg>`;
	    window.folderFallback = folderIcon;

    function toast(text, kind = "") {
      statusEl.textContent = text;
      statusEl.className = `status show ${kind}`.trim();
      window.clearTimeout(toast.timer);
      toast.timer = window.setTimeout(() => statusEl.classList.remove("show"), 4200);
    }

    function escapeHtml(value) {
      return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
    }

    function isUrl(value) {
      return typeof value === "string" && (value.startsWith("http://") || value.startsWith("https://"));
    }

	    function iconMarkup(icon, klass = "") {
	      if (isUrl(icon)) {
	        return `<img src="${escapeHtml(icon)}" alt="" onerror="this.outerHTML=window.folderFallback">`;
	      }
	      if (!icon) return folderIcon;
	      return `<span class="glyph">${escapeHtml(icon)}</span>`;
	    }

    function groupById(id) {
      return state.groups.find((group) => String(group.id) === String(id));
    }

    function matches(project, query) {
      if (!query) return true;
      const group = groupById(project.groupId);
      return project.name.toLowerCase().includes(query) ||
        project.path.toLowerCase().includes(query) ||
        project.shortPath.toLowerCase().includes(query) ||
        (group && group.name.toLowerCase().includes(query));
    }

    function render() {
      const query = searchEl.value.trim().toLowerCase();
      const visible = state.projects.filter((project) => matches(project, query));
      state.visible = visible;
      countEl.textContent = `${visible.length} of ${state.projects.length} projects`;

      let html = "";
      let renderedPaths = new Set();
      for (const group of state.groups) {
        const groupProjects = visible.filter((project) => String(project.groupId) === String(group.id));
        if (!groupProjects.length) continue;
        html += groupSection(group, groupProjects);
        groupProjects.forEach((project) => renderedPaths.add(project.path));
      }
	      const other = visible.filter((project) => !renderedPaths.has(project.path));
	      if (other.length) {
	        html += section(state.groups.length === 0 ? "Recent Projects" : "Other Projects", other, null);
	      }
      if (!html) {
        html = emptyState();
      }
      rootEl.innerHTML = html;
    }

    function emptyState() {
      return `
        <div class="empty">
          <div>
            <strong>No projects found</strong>
            <button class="btn primary" data-action="add-project" type="button">Add Project</button>
          </div>
        </div>
      `;
    }

    function section(title, projects, group, omitTitle = false) {
      const collapsed = group && group.collapsed;
      const header = omitTitle ? "" : groupHeader(title, projects.length, group);
      return `
        <section class="section ${collapsed ? "collapsed" : ""}">
          ${header}
          <div class="grid">
            ${projects.map(projectTile).join("")}
            ${group ? "" : addTile()}
          </div>
        </section>
      `;
    }

    function groupSection(group, projects) {
      return section(group.name, projects, group);
    }

    function groupHeader(title, count, group) {
      const groupId = group ? `data-group-id="${escapeHtml(group.id)}"` : "";
      const color = group && group.color ? group.color : "#4a90d9";
      const icon = group && group.icon ? `<span class="group-icon">${iconMarkup(group.icon)}</span>` : "";
	      const actions = group ? `
	        <div class="group-actions">
	          <button class="btn" data-action="group-color" ${groupId}>Color</button>
	          <button class="btn" data-action="group-icon" ${groupId}>Icon</button>
	          <button class="btn" data-action="rename-group" ${groupId}>Rename</button>
	          <button class="btn danger" data-action="delete-group" ${groupId}>Delete</button>
	        </div>
      ` : "";
      return `
        <div class="group-header" data-action="${group ? "toggle-group" : ""}" ${groupId}>
          ${group ? `<span class="chevron">▼</span>` : ""}
          <span class="group-dot" style="background:${escapeHtml(color)}"></span>
          ${icon}
          <span class="group-name">${escapeHtml(title)}</span>
          <span class="group-count">${count} ${count === 1 ? "project" : "projects"}</span>
          ${actions}
        </div>
      `;
    }

	    function addTile() {
	      return `
	        <button class="tile-add" data-action="add-project" type="button">
	          <span class="tile-icon default">${folderIcon}</span>
	          <span>Add Project</span>
	        </button>
	      `;
	    }

	    function projectTile(project) {
	      const color = project.color || "#3a3a3a";
	      const key = project.index && project.index <= 9 ? `⌘${project.index}` : "";
	      return `
	        <article class="tile" data-path="${escapeHtml(project.path)}">
	          <div class="stripe" style="background:${escapeHtml(color)}"></div>
          <div class="tile-body" data-action="open" data-path="${escapeHtml(project.path)}">
            <span class="tile-icon ${project.icon ? "" : "default"}">${iconMarkup(project.icon)}</span>
            <div class="tile-info">
              <div class="tile-name" title="${escapeHtml(project.name)}">${escapeHtml(project.name)}</div>
	              <div class="tile-path" title="${escapeHtml(project.path)}">${escapeHtml(project.shortPath)}</div>
	              <div class="tile-meta">
	                ${project.pinned ? `<span class="pill">Pinned</span>` : ""}
	                <span>${escapeHtml(project.lastOpened)}</span>
	                <span class="shortcut">${escapeHtml(key)}</span>
	              </div>
            </div>
          </div>
          <div class="tile-actions">
	            <button class="btn primary" data-action="open" data-path="${escapeHtml(project.path)}" type="button">Open</button>
	            <button class="btn" data-action="pin" data-path="${escapeHtml(project.path)}" type="button">${project.pinned ? "Unpin" : "Pin"}</button>
	            <button class="btn" data-action="color" data-path="${escapeHtml(project.path)}" type="button">Color</button>
	            <button class="btn" data-action="icon" data-path="${escapeHtml(project.path)}" type="button">Icon</button>
	            <button class="btn" data-action="assign-group" data-path="${escapeHtml(project.path)}" type="button">Group</button>
	            <button class="btn danger" data-action="remove" data-path="${escapeHtml(project.path)}" type="button">Remove</button>
          </div>
        </article>
      `;
    }

    async function api(path, body) {
      const response = await fetch(path, {
        method: body ? "POST" : "GET",
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.error || "Request failed");
      }
      return data;
    }

    async function load() {
      const data = await api("/api/projects");
      state.projects = data.projects;
      state.groups = data.groups || [];
      state.source = data.source;
      render();
      toast(`${data.count} projects · ${data.source}`);
    }

    async function openProject(path) {
      toast("Opening project...");
      const data = await api("/api/open", { path });
      await load();
      toast(data.message, "ok");
    }

    function projectForPath(path) {
      return state.projects.find((project) => project.path === path);
    }

    async function updateProject(path, payload) {
      const data = await api("/api/project", { path, ...payload });
      await load();
      toast(data.message, "ok");
    }

    async function updateGroup(groupId, payload) {
      const data = await api("/api/group", { groupId, ...payload });
      await load();
      toast(data.message, "ok");
    }

    document.addEventListener("click", async (event) => {
      const target = event.target.closest("[data-action]");
      if (!target) return;
      if (target.closest(".group-actions") && target.dataset.action !== "toggle-group") {
        event.stopPropagation();
      }
      const action = target.dataset.action;
      const path = target.dataset.path;
      const groupId = target.dataset.groupId;
      try {
        if (action === "open") {
          await openProject(path);
        } else if (action === "new-file") {
          await api("/api/new-file", {});
          toast("Opening a new Zed window.", "ok");
        } else if (action === "add-project") {
          const path = prompt("Project folder path");
          if (path) {
            const data = await api("/api/add", { path });
            toast(data.message, "ok");
            await load();
          }
        } else if (action === "scan") {
          const rootsRaw = prompt("Scan roots (optional, comma separated)", "~/GitHub, ~/GitLab, ~/Code, ~/Projects");
          const roots = rootsRaw ? rootsRaw.split(",").map((item) => item.trim()).filter(Boolean) : [];
          toast("Scanning repositories...");
          const data = await api("/api/scan", { roots });
          toast(data.message, "ok");
          await load();
        } else if (action === "refresh") {
          await load();
        } else if (action === "remove" && path) {
          const project = projectForPath(path);
          if (!confirm(`Remove "${project ? project.name : path}" from the launchpad?`)) return;
          const data = await api("/api/remove", { path });
          toast(data.message, "ok");
          await load();
        } else if (action === "pin" && path) {
          const project = projectForPath(path);
          await updateProject(path, { pinned: !(project && project.pinned) });
        } else if (action === "color" && path) {
          const project = projectForPath(path);
          const color = prompt("Project color (#RRGGBB, blank clears)", project ? project.color || "" : "");
          if (color !== null) await updateProject(path, { color });
        } else if (action === "icon" && path) {
          const project = projectForPath(path);
          const icon = prompt("Project icon or image URL (blank clears)", project ? project.icon || "" : "");
          if (icon !== null) await updateProject(path, { icon });
        } else if (action === "assign-group" && path) {
          const project = projectForPath(path);
          const current = project && project.groupId ? (groupById(project.groupId) || {}).name || "" : "";
          const name = prompt("Group name (blank removes from group)", current);
          if (name !== null) {
            const data = await api("/api/assign-group", { path, name });
            toast(data.message, "ok");
            await load();
          }
        } else if (action === "create-group") {
          const name = prompt("New group name");
          if (name) {
            const data = await api("/api/group", { action: "create", name });
            toast(data.message, "ok");
            await load();
          }
        } else if (action === "rename-group" && groupId) {
          const group = groupById(groupId);
          const name = prompt("Group name", group ? group.name : "");
          if (name) await updateGroup(groupId, { action: "update", name });
        } else if (action === "group-color" && groupId) {
          const group = groupById(groupId);
          const color = prompt("Group color (#RRGGBB, blank clears)", group ? group.color || "" : "");
          if (color !== null) await updateGroup(groupId, { action: "update", color });
        } else if (action === "group-icon" && groupId) {
          const group = groupById(groupId);
          const icon = prompt("Group icon or image URL (blank clears)", group ? group.icon || "" : "");
          if (icon !== null) await updateGroup(groupId, { action: "update", icon });
        } else if (action === "delete-group" && groupId) {
          const group = groupById(groupId);
          if (!confirm(`Delete group "${group ? group.name : groupId}"? Projects stay in the launchpad.`)) return;
          const data = await api("/api/group", { action: "delete", groupId });
          toast(data.message, "ok");
          await load();
        } else if (action === "toggle-group" && groupId) {
          const data = await api("/api/group", { action: "toggle", groupId });
          await load();
          toast(data.message, "ok");
        }
      } catch (error) {
        toast(error.message, "error");
      }
    });

    searchEl.addEventListener("input", render);
    document.addEventListener("keydown", async (event) => {
      if (event.metaKey && /^[1-9]$/.test(event.key)) {
        event.preventDefault();
        const project = state.visible[Number(event.key) - 1];
        if (project) {
          try {
            await openProject(project.path);
          } catch (error) {
            toast(error.message, "error");
          }
        }
      }
      if (event.key === "/" && document.activeElement !== searchEl) {
        event.preventDefault();
        searchEl.focus();
      }
      if (event.key === "Escape" && document.activeElement === searchEl) {
        searchEl.value = "";
        render();
      }
    });

    const params = new URLSearchParams(window.location.search);
    if (params.get("q")) {
      searchEl.value = params.get("q");
    }
    load().catch((error) => toast(error.message, "error"));
</script>
</body>
</html>"""


class LaunchpadHandler(BaseHTTPRequestHandler):
    server_version = "ZedProjectsLaunchpad/0.1"

    def log_message(self, format: str, *args: object) -> None:
        if os.environ.get("ZED_PROJECTS_LOG_REQUESTS"):
            super().log_message(format, *args)

    def send_text(self, text: str, content_type: str = "text/plain", status: int = 200) -> None:
        payload = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_json(self, data: dict, status: int = 200) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("Request body must be a JSON object")
        return data

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/":
                self.send_text(launchpad_html(), "text/html")
            elif path == "/api/health":
                self.send_json({"ok": True})
            elif path == "/api/projects":
                self.send_json(launchpad_payload())
            else:
                self.send_json({"error": "Not found"}, status=404)
        except Exception as error:
            self.send_json({"error": str(error)}, status=500)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            body = self.read_json()
            if path == "/api/open":
                data, _source = load_data()
                if "index" in body:
                    project = find_project(data, str(body["index"]))
                else:
                    project = find_project(data, str(body.get("path") or ""))
                self.send_json({"message": open_project(data, project)})
            elif path == "/api/new-file":
                subprocess.Popen(zed_open_command(new_file=True), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.send_json({"message": "Opening a new Zed window."})
            elif path == "/api/add":
                message = cmd_add([str(body.get("path") or "")])
                self.send_json({"message": message})
            elif path == "/api/scan":
                roots = body.get("roots")
                args = roots if isinstance(roots, list) else []
                message = cmd_scan([str(root) for root in args])
                self.send_json({"message": message})
            elif path == "/api/remove":
                data, _source = load_data()
                message = remove_project_entry(data, str(body.get("path") or ""))
                self.send_json({"message": message})
            elif path == "/api/project":
                data, _source = load_data()
                selector = str(body.get("path") or "")
                meta: dict[str, object] = {}
                if "pinned" in body:
                    meta["pinned"] = bool(body.get("pinned"))
                if "color" in body:
                    color = str(body.get("color") or "").strip()
                    if color and not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
                        raise SystemExit("Color must be a hex value like #4A90D9.")
                    meta["color"] = color
                if "icon" in body:
                    meta["icon"] = str(body.get("icon") or "").strip()
                message = set_project_meta(data, selector, **meta)
                self.send_json({"message": message})
            elif path == "/api/assign-group":
                data, _source = load_data()
                selector = str(body.get("path") or "")
                name = str(body.get("name") or "").strip()
                if name:
                    group = create_group(data, name)
                    message = set_project_group_id(data, selector, str(group.get("id")))
                else:
                    message = set_project_group_id(data, selector, None)
                self.send_json({"message": message})
            elif path == "/api/group":
                data, _source = load_data()
                action = str(body.get("action") or "update")
                group_id = str(body.get("groupId") or "")
                if action == "create":
                    group = create_group(data, str(body.get("name") or ""))
                    self.send_json({"message": f"Created group {group.get('name')}", "group": group})
                elif action == "delete":
                    self.send_json({"message": delete_group(data, group_id)})
                elif action == "toggle":
                    self.send_json({"message": toggle_group_collapsed(data, group_id)})
                else:
                    meta = {}
                    if "name" in body:
                        name = str(body.get("name") or "").strip()
                        if not name:
                            raise SystemExit("Group name cannot be empty.")
                        meta["name"] = name
                    if "color" in body:
                        color = str(body.get("color") or "").strip()
                        if color and not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
                            raise SystemExit("Color must be a hex value like #4A90D9.")
                        meta["color"] = color
                    if "icon" in body:
                        meta["icon"] = str(body.get("icon") or "").strip()
                    self.send_json({"message": update_group(data, group_id, **meta)})
            else:
                self.send_json({"error": "Not found"}, status=404)
        except SystemExit as error:
            self.send_json({"error": str(error)}, status=400)
        except Exception as error:
            self.send_json({"error": str(error)}, status=500)


def launchpad_host() -> str:
    return os.environ.get("ZED_PROJECTS_LAUNCHPAD_HOST", DEFAULT_LAUNCHPAD_HOST)


def launchpad_port() -> int:
    raw = os.environ.get("ZED_PROJECTS_LAUNCHPAD_PORT", str(DEFAULT_LAUNCHPAD_PORT))
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_LAUNCHPAD_PORT


def launchpad_url(host: str, port: int) -> str:
    return f"http://{host}:{port}"


def launchpad_browser_url(host: str, port: int, query: str = "") -> str:
    url = launchpad_url(host, port)
    if query.strip():
        url += f"/?q={quote(query.strip())}"
    return url


def healthcheck(host: str, port: int) -> bool:
    try:
        with urllib.request.urlopen(f"{launchpad_url(host, port)}/api/health", timeout=0.3) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def pick_launchpad_port(host: str, preferred: int) -> int:
    if healthcheck(host, preferred) or port_available(host, preferred):
        return preferred
    for port in range(preferred + 1, preferred + 40):
        if healthcheck(host, port) or port_available(host, port):
            return port
    raise SystemExit("No available local port found for the project launchpad.")


def current_script_path() -> Path:
    configured = os.environ.get("ZED_PROJECTS_HELPER_PATH")
    if configured:
        script = Path(configured).expanduser().resolve(strict=False)
        if script.exists():
            return script
    script = Path(__file__).resolve()
    if not script.exists():
        raise SystemExit("The launchpad server can only be started from the checked-out helper script.")
    return script


def ensure_launchpad_server(query: str = "", open_page: bool = True) -> str:
    host = launchpad_host()
    port = pick_launchpad_port(host, launchpad_port())
    if not healthcheck(host, port):
        subprocess.Popen(
            [sys.executable, str(current_script_path()), "serve", "--port", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.time() + 5
        while time.time() < deadline:
            if healthcheck(host, port):
                break
            time.sleep(0.1)
        else:
            raise SystemExit("Project launchpad server did not start.")

    url = launchpad_browser_url(host, port, query)
    if open_page:
        webbrowser.open_new_tab(url)
    return url


def cmd_web(args: list[str]) -> str:
    open_page = True
    filtered_args = []
    for arg in args:
        if arg in ("--no-open", "--no-browser"):
            open_page = False
        else:
            filtered_args.append(arg)
    query = " ".join(filtered_args).strip()
    url = ensure_launchpad_server(query=query, open_page=open_page)
    return f"Project launchpad: {url}"


def cmd_serve(args: list[str]) -> str:
    host = launchpad_host()
    port = launchpad_port()
    if args:
        if args[0] == "--port" and len(args) > 1:
            port = int(args[1])
        elif args[0].isdigit():
            port = int(args[0])
    server = ThreadingHTTPServer((host, port), LaunchpadHandler)
    print(launchpad_url(host, port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return ""
    finally:
        server.server_close()
    return ""


def cmd_start(args: list[str]) -> str:
    start_zed = os.environ.get("ZED_PROJECTS_START_ZED", "1") != "0"
    open_launchpad = os.environ.get("ZED_PROJECTS_OPEN_LAUNCHPAD", "1") != "0"
    zed_args: list[str] = []
    for arg in args:
        if arg == "--no-zed":
            start_zed = False
        elif arg == "--no-launchpad":
            open_launchpad = False
        elif arg in ("--browser", "--web"):
            open_launchpad = True
        elif arg == "--no-browser":
            open_launchpad = False
        else:
            zed_args.append(arg)

    if start_zed:
        subprocess.Popen([zed_binary(), *zed_args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if open_launchpad:
        url = ensure_launchpad_server(open_page=True)
        return f"Project launchpad: {url}"

    if start_zed:
        return "Zed started."
    return "No action requested. Remove --no-zed or --no-launchpad."


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(cmd_help())
        return 0

    command, args = argv[0], argv[1:]
    if command == "render":
        print(cmd_render(args))
    elif command == "launchpad":
        print(cmd_render(args, launchpad=True))
    elif command == "web":
        print(cmd_web(args))
    elif command == "completions":
        print(cmd_completions(args))
    elif command == "open":
        print(cmd_open(args))
    elif command == "add":
        print(cmd_add(args))
    elif command == "icon":
        print(cmd_icon(args))
    elif command == "color":
        print(cmd_color(args))
    elif command == "pin":
        print(cmd_pin(args))
    elif command == "group":
        print(cmd_group(args))
    elif command == "scan":
        print(cmd_scan(args))
    elif command == "config":
        print(cmd_config())
    elif command == "help":
        print(cmd_help())
    elif command == "markdown":
        print(cmd_markdown(args))
    elif command == "serve":
        result = cmd_serve(args)
        if result:
            print(result)
    elif command == "start":
        print(cmd_start(args))
    else:
        raise SystemExit(f"Unknown command: {command}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except BrokenPipeError:
        raise SystemExit(0)
