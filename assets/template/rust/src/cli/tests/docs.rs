//! The quick guide (docs/quick-guide{,.es,.fr}.md) covers what the binary offers: every documented subcommand,
//! every long flag of the five common tasks, every GUI button (by its label in i18n/<lang>.json) and every exit
//! code. The Spanish and French guides keep the shape of the English one. Facts come from the binary itself
//! (`docs-cli`, `--help`, `describe --json`), so a new command, flag, button or exit code fails this test until
//! the guide documents it.

use assert_cmd::Command;
use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

const TOOL: &str = "rv-hashinv";
/// The subcommands behind the five common tasks: scan, scan into a case folder, verify-log, verify-case, timestamp.
const TASK_COMMANDS: &[&str] = &["scan", "verify-log", "verify-case", "timestamp"];
/// The GUI's clickable controls, by i18n key (`ui.<key>`). `tsa` is the label of the timestamp tick box.
const GUI_ACTIONS: &[&str] = &[
    "add_files",
    "add_folder",
    "add_path",
    "subfolders",
    "browse",
    "tsa",
    "start",
    "cancel",
    "open_report",
    "open_logs",
];
const LANGS: &[&str] = &["en", "es", "fr"];

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../..")
}

fn read(rel: &str) -> String {
    std::fs::read_to_string(root().join(rel)).unwrap_or_else(|e| panic!("{}: {}", rel, e))
}

fn guide(lang: &str) -> String {
    match lang {
        "en" => read("docs/quick-guide.md"),
        l => read(&format!("docs/quick-guide.{}.md", l)),
    }
}

fn run(args: &[&str]) -> String {
    let out = Command::cargo_bin(TOOL)
        .unwrap()
        .args(args)
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(0), "{:?}: {:?}", args, out);
    String::from_utf8(out.stdout).unwrap()
}

/// The subcommands documented in usage-cli.md, read from the generator (`## rv-hashinv <name>` headings).
fn documented_commands() -> Vec<String> {
    let prefix = format!("## {} ", TOOL);
    run(&["docs-cli"])
        .lines()
        .filter_map(|l| l.strip_prefix(&prefix).map(str::to_string))
        .collect()
}

/// The long flags of a subcommand, from the option lines of its `--help` (`-i, --input <PATH>`, `--rehash`).
fn long_flags(command: &str) -> Vec<String> {
    run(&[command, "--help"])
        .lines()
        .filter_map(|l| {
            let t = l.trim_start();
            // Option lines are indented by 2 (`-i, --input`) or 6 (`--tsa-url`); their help text by 10.
            if l.len() - t.len() > 6 {
                return None;
            }
            let t = match t.as_bytes() {
                [b'-', c, b',', b' ', ..] if c.is_ascii_alphanumeric() => &t[4..],
                _ => t,
            };
            let flag = t.strip_prefix("--")?.split([' ', '=', '[']).next()?;
            let word = flag
                .chars()
                .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-');
            (word && !flag.is_empty() && flag != "help" && flag != "version")
                .then(|| format!("--{}", flag))
        })
        .collect()
}

/// True when `flag` appears as a whole flag (`--case` does not match inside `--case-dir`).
fn has_flag(text: &str, flag: &str) -> bool {
    text.match_indices(flag).any(|(i, _)| {
        !text[i + flag.len()..]
            .chars()
            .next()
            .is_some_and(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
    })
}

/// The exit codes of every documented command: describe's table, plus each command's EXIT CODES section.
fn exit_codes() -> BTreeSet<String> {
    let v: serde_json::Value = serde_json::from_str(&run(&["describe", "--json"])).unwrap();
    let mut codes: BTreeSet<String> = v["exit_codes"]
        .as_object()
        .unwrap()
        .keys()
        .cloned()
        .collect();
    for c in documented_commands() {
        let help = run(&[&c, "--help"]);
        let Some(section) = help.split("EXIT CODES:\n").nth(1) else {
            continue;
        };
        for line in section
            .lines()
            .take_while(|l| l.is_empty() || l.starts_with(' '))
        {
            if let Some(code) = line.split_whitespace().next()
                && line.starts_with("  ")
                && !line.starts_with("   ")
                && code.chars().all(|c| c.is_ascii_digit())
            {
                codes.insert(code.to_string());
            }
        }
    }
    codes
}

fn ui_label(lang: &str, key: &str) -> String {
    let v: serde_json::Value = serde_json::from_str(&read(&format!("i18n/{}.json", lang))).unwrap();
    let label = v["ui"][key]
        .as_str()
        .unwrap_or_else(|| panic!("i18n/{}.json has no ui.{}", lang, key));
    label.trim_end_matches('…').to_string()
}

#[test]
fn quick_guide_documents_every_command_flag_button_and_exit_code() {
    let en = guide("en");
    let commands = documented_commands();
    assert!(commands.len() >= 5, "{:?}", commands);
    for c in &commands {
        assert!(
            en.contains(&format!("{} {}", TOOL, c)),
            "docs/quick-guide.md does not mention `{} {}`",
            TOOL,
            c
        );
    }
    for c in TASK_COMMANDS {
        assert!(commands.iter().any(|d| d == c), "{} is not a subcommand", c);
        let flags = long_flags(c);
        assert!(!flags.is_empty(), "{} --help lists no flag", c);
        for f in flags {
            for lang in LANGS {
                assert!(
                    has_flag(&guide(lang), &f),
                    "the {} quick guide does not document `{} {}`",
                    lang,
                    c,
                    f
                );
            }
        }
    }
    let codes = exit_codes();
    assert!(codes.contains("0") && codes.contains("130"), "{:?}", codes);
    for code in &codes {
        for lang in LANGS {
            assert!(
                guide(lang)
                    .lines()
                    .any(|l| l.starts_with(&format!("| {} |", code))),
                "the {} quick guide has no exit-code row `| {} |`",
                lang,
                code
            );
        }
    }
    for key in GUI_ACTIONS {
        for lang in LANGS {
            let label = ui_label(lang, key);
            assert!(
                guide(lang).contains(&format!("**{}", label)),
                "the {} quick guide does not describe the GUI control ui.{} (**{}**)",
                lang,
                key,
                label
            );
        }
    }
}

/// Every button and tick box the GUI draws with a translated label is in GUI_ACTIONS (so it is checked above).
#[test]
fn gui_actions_list_every_button_of_the_window() {
    let app = read("src/gui/src/app.rs");
    let lines: Vec<&str> = app.lines().collect();
    let mut drawn = BTreeSet::new();
    for (i, l) in lines.iter().enumerate() {
        if !(l.contains("button(") || l.contains("checkbox(") || l.contains("Button::new(")) {
            continue;
        }
        let near = format!("{}{}", l, lines.get(i + 1).unwrap_or(&""));
        for part in near.split("tr(\"").skip(1) {
            if let Some(key) = part.split('"').next() {
                drawn.insert(key.to_string());
            }
        }
    }
    assert!(drawn.contains("start"), "{:?}", drawn);
    for key in &drawn {
        assert!(
            GUI_ACTIONS.contains(&key.as_str()),
            "the GUI draws a control labelled ui.{}: add it to GUI_ACTIONS and to docs/quick-guide*.md",
            key
        );
    }
    for key in GUI_ACTIONS {
        assert!(
            app.contains(&format!("tr(\"{}\")", key)),
            "ui.{} is not used by the GUI any more",
            key
        );
    }
}

/// Headings and numbered steps outside code blocks.
fn shape(text: &str) -> (usize, usize) {
    let (mut headings, mut steps, mut fenced) = (0, 0, false);
    for line in text.lines() {
        if line.trim_start().starts_with("```") {
            fenced = !fenced;
            continue;
        }
        if fenced {
            continue;
        }
        let t = line.trim_start();
        let digits = t.chars().take_while(char::is_ascii_digit).count();
        if line.starts_with('#') && line.trim_start_matches('#').starts_with(' ') {
            headings += 1;
        } else if digits > 0 && t[digits..].starts_with(". ") {
            steps += 1;
        }
    }
    (headings, steps)
}

#[test]
fn translated_quick_guides_have_the_same_headings_and_steps() {
    let (headings, steps) = shape(&guide("en"));
    assert!(headings >= 8 && steps >= 10, "{} {}", headings, steps);
    for lang in ["es", "fr"] {
        assert_eq!(
            shape(&guide(lang)),
            (headings, steps),
            "docs/quick-guide.{}.md: (headings, numbered steps) differ from the English guide",
            lang
        );
    }
}
