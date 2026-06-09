use zed_extension_api::{
    self as zed, SlashCommand, SlashCommandArgumentCompletion, SlashCommandOutput,
    SlashCommandOutputSection, Worktree,
};

const HELPER: &str = include_str!("zed_projects.py");
const HELPER_PATH: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/src/zed_projects.py");

struct ProjectsExtension;

impl zed::Extension for ProjectsExtension {
    fn new() -> Self {
        Self
    }

    fn complete_slash_command_argument(
        &self,
        command: SlashCommand,
        args: Vec<String>,
    ) -> Result<Vec<SlashCommandArgumentCompletion>, String> {
        match command.name.as_str() {
            "projects" => completions(args, true),
            "project-icon" | "project-color" | "project-pin" | "project-group" => {
                completions(args, false)
            }
            _ => Ok(Vec::new()),
        }
    }

    fn run_slash_command(
        &self,
        command: SlashCommand,
        args: Vec<String>,
        _worktree: Option<&Worktree>,
    ) -> Result<SlashCommandOutput, String> {
        let helper_args = match command.name.as_str() {
            "projects" => {
                if args.is_empty() {
                    vec!["web".to_string()]
                } else {
                    vec!["open".to_string(), args.join(" ")]
                }
            }
            "project-launchpad" => prepend("web", args),
            "project-open-launchpad" => vec!["web".to_string()],
            "project-list" => prepend("render", args),
            "project-add" => prepend("add", args),
            "project-icon" => prepend("icon", args),
            "project-color" => prepend("color", args),
            "project-pin" => prepend("pin", args),
            "project-group" => prepend("group", args),
            "project-scan" => prepend("scan", args),
            "project-config" => vec!["config".to_string()],
            "project-help" => vec!["help".to_string()],
            other => return Err(format!("unknown slash command: {other}")),
        };

        let text = run_helper(&helper_args)?;
        output(command.name, text)
    }
}

fn completions(
    args: Vec<String>,
    run_command: bool,
) -> Result<Vec<SlashCommandArgumentCompletion>, String> {
    let mode = if run_command { "run" } else { "edit" };
    let raw = run_helper(&prepend("completions", prepend(mode, args)))?;
    let values: Vec<zed::serde_json::Value> =
        zed::serde_json::from_str(&raw).map_err(|err| format!("invalid completion JSON: {err}"))?;

    let mut items = Vec::with_capacity(values.len());
    for value in values {
        let label = value
            .get("label")
            .and_then(|value| value.as_str())
            .unwrap_or_default()
            .to_string();
        let new_text = value
            .get("new_text")
            .and_then(|value| value.as_str())
            .unwrap_or_default()
            .to_string();
        let run_command = value
            .get("run_command")
            .and_then(|value| value.as_bool())
            .unwrap_or(false);

        if !label.is_empty() && !new_text.is_empty() {
            items.push(SlashCommandArgumentCompletion {
                label,
                new_text,
                run_command,
            });
        }
    }

    Ok(items)
}

fn prepend(head: &str, tail: Vec<String>) -> Vec<String> {
    let mut args = Vec::with_capacity(tail.len() + 1);
    args.push(head.to_string());
    args.extend(tail);
    args
}

fn run_helper(args: &[String]) -> Result<String, String> {
    let mut command = zed::process::Command::new("python3")
        .arg("-c")
        .arg(HELPER)
        .env("ZED_PROJECTS_HELPER_PATH", HELPER_PATH);

    for arg in args {
        command = command.arg(arg.clone());
    }

    let output = command.output()?;
    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();

    match output.status {
        Some(0) => Ok(stdout),
        Some(status) => {
            if stderr.is_empty() {
                Err(format!(
                    "zed-projects helper exited with status {status}: {stdout}"
                ))
            } else {
                Err(stderr)
            }
        }
        None => Err(if stderr.is_empty() {
            "zed-projects helper was terminated before it could finish".to_string()
        } else {
            stderr
        }),
    }
}

fn output(label: String, text: String) -> Result<SlashCommandOutput, String> {
    let sections = if text.is_empty() {
        Vec::new()
    } else {
        vec![SlashCommandOutputSection {
            range: (0..text.len()).into(),
            label,
        }]
    };

    Ok(SlashCommandOutput { text, sections })
}

zed::register_extension!(ProjectsExtension);
