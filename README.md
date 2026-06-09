# zed-projects

Localhost project launchpad for Zed.

`zed-projects` reuses the project registry shape from the VS Code Recent
Projects extension and exposes it as a local web launchpad. Zed extensions do
not currently support custom Webview-style editor UI, so the extension starts a
small `127.0.0.1` web server and opens the launchpad in the browser.

## Install

Install this repository as a local dev extension:

1. Open Zed.
2. Open the Command Palette.
3. Run `zed: install dev extension`.
4. Select the checked-out `zed-projects` repository folder.

If installation fails with `failed to compile Rust extension`, install Rust and
the Wasm targets:

```sh
rustup target add wasm32-wasip2
rustup target add wasm32-wasip1
cargo build --target wasm32-wasip2
cargo build --target wasm32-wasip1
```

The extension uses Python 3 through Zed's `process:exec` capability.

## Open The Launchpad In Zed

If you configure the optional keybindings below, this shortcut opens the
launchpad directly:

```text
cmd-shift-p
```

The keybinding runs the global Zed task `zed-projects: open launchpad`.

With that keymap, Zed's normal recent-project panel is bound to:

```text
cmd-r
```

Because `cmd-shift-p` is rebound to the launchpad, the Command Palette fallback
is:

```text
cmd-alt-p
```

Zed extension commands are Agent slash commands, not normal Command Palette
commands. In a clean Zed install, `cmd-shift-p` opens the Command Palette. On
machines using the keymap below, it opens the launchpad instead.

In Zed:

1. Open the Command Palette with `cmd-alt-p`.
2. Run `agent: focus agent` or `agent: toggle`.
3. In the Agent input, type `/projects` and press Enter.

```text
/projects
```

This starts the local launchpad server if needed and opens:

```text
http://127.0.0.1:8765
```

If port `8765` is busy, the helper uses the next available local port.

Aliases:

```text
/project-launchpad
/project-open-launchpad
```

You can still open a project directly from the slash command:

```text
/projects 1
/projects zed-projects
/projects ~/GitHub/my-app
```

## Launchpad Features

- Zed-style dark localhost UI.
- Compact Zed-style project rows and groups.
- Project search.
- Open projects in Zed.
- Add projects manually.
- Scan repository roots.
- Pin and unpin projects.
- Set project colors and icons.
- Create, rename, collapse, color, icon, and delete groups.
- Assign projects to groups.
- Remove projects from the launchpad list.
- `cmd-1` through `cmd-9` open the first visible projects.

## Slash Command Manual

### `/projects [project]`

Without an argument, open the localhost launchpad. With an argument, open a
matching project.

```text
/projects
/projects 1
/projects zed-projects
```

### `/project-launchpad [query]`

Open the localhost launchpad, optionally with a search query.

```text
/project-launchpad
/project-launchpad api
```

### `/project-list [query]`

Show a compact text list in the Agent Panel.

```text
/project-list
/project-list api
```

### `/project-add <path>`

Add a project folder to the registry.

```text
/project-add ~/GitHub/my-app
```

### `/project-icon <project> <icon-or-url|clear>`

Set or clear a project icon.

```text
/project-icon my-app M
/project-icon my-app https://example.com/icon.png
/project-icon my-app clear
```

### `/project-color <project> <#RRGGBB|clear>`

Set or clear a project color.

```text
/project-color my-app #4A90D9
/project-color my-app clear
```

### `/project-pin <project> [on|off|true|false|toggle]`

Pin, unpin, or toggle a project.

```text
/project-pin my-app
/project-pin my-app on
/project-pin my-app off
```

### `/project-group <project> <group-name|clear>`

Assign a project to a group or remove it from its current group.

```text
/project-group my-app Work
/project-group my-app clear
```

### `/project-scan [root ...]`

Scan project roots and add discovered repositories.

```text
/project-scan
/project-scan ~/GitHub ~/GitLab
```

### `/project-config`

Show active paths and environment settings.

```text
/project-config
```

### `/project-help`

Show the short usage guide.

```text
/project-help
```

## Local CLI

Open the web launchpad:

```sh
bin/zed-projects-launchpad
```

Start Zed and the launchpad:

```sh
bin/zed-projects-start
```

Run the server without opening a browser:

```sh
bin/zed-projects serve --port 8765
```

Print the URL without opening the browser:

```sh
bin/zed-projects web --no-open
```

Other helper commands:

```sh
bin/zed-projects scan ~/GitHub ~/GitLab
bin/zed-projects render platform
bin/zed-projects open zed-projects
bin/zed-projects icon zed-projects Z
bin/zed-projects color zed-projects '#4A90D9'
bin/zed-projects pin zed-projects on
bin/zed-projects group zed-projects Tools
bin/zed-projects help
```

## Optional Zed Keybindings

Create a global Zed task in `~/.config/zed/tasks.json`:

```json
[
  {
    "label": "zed-projects: open launchpad",
    "command": "/path/to/zed-projects/bin/zed-projects-launchpad",
    "cwd": "/path/to/zed-projects",
    "use_new_terminal": false,
    "allow_concurrent_runs": true,
    "reveal": "never",
    "hide": "always",
    "show_summary": false,
    "show_command": false,
    "save": "none"
  }
]
```

Then add these bindings to `~/.config/zed/keymap.json`:

```json
[
  {
    "bindings": {
      "cmd-shift-p": [
        "task::Spawn",
        {
          "task_name": "zed-projects: open launchpad"
        }
      ],
      "cmd-r": "projects::OpenRecent",
      "cmd-alt-p": "command_palette::Toggle"
    }
  }
]
```

## Data Files

Read order:

1. `ZED_PROJECTS_CONFIG`, if set.
2. `~/.zed-projects.json`.
3. `~/.vscode-recent-projects.json`.

Writes go to `~/.zed-projects.json` by default. This imports the VS Code list on
first write without modifying the VS Code file.

## Environment

```text
ZED_PROJECTS_CONFIG
ZED_PROJECTS_LAUNCHPAD_HOST
ZED_PROJECTS_LAUNCHPAD_PORT
ZED_PROJECTS_ZED_BIN
ZED_PROJECTS_OPEN_MODE
ZED_PROJECTS_OPEN_LAUNCHPAD
```

`ZED_PROJECTS_OPEN_MODE` controls how projects are opened through the `zed` CLI:

- `new` opens a new Zed workspace.
- `existing` opens in an existing Zed window.
- `add` adds the folder to the current workspace.
- `default` calls `zed <path>` without an extra mode flag.

## Development

```sh
python3 -m py_compile src/zed_projects.py
cargo fmt --check
cargo check
cargo build --target wasm32-wasip2
cargo build --target wasm32-wasip1
```

After changing `src/zed_projects.py`, reinstall or reload the dev extension in
Zed because the helper is embedded into the Rust extension with `include_str!`.
