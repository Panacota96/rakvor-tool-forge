//! The quick guide (docs/quick-guide{,.es,.fr}.md) covers what the binary offers: every documented subcommand,
//! every long flag of the five common tasks (in that task's own section), every GUI button and tick box (by its
//! label in i18n/<lang>.json, read from src/gui/src/app.rs) and every exit code, in all three languages. The
//! Spanish and French guides keep the shape of the English one. Facts come from the binary itself (`docs-cli`,
//! `--help`, `describe --json`) and from the GUI source, so a new command, flag, button or exit code fails this
//! test until the guide documents it.

use assert_cmd::Command;
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

const TOOL: &str = "rv-hashinv";
/// The subcommands behind the five common tasks, with the task sections ("## Task <N>: …") that document their
/// flags: 1 scan, 2 scan into a case folder, 3 verify-log, 4 verify-case, 5 timestamp after offline work.
const TASK_COMMANDS: &[(&str, &[u32])] = &[
    ("scan", &[1, 2]),
    ("verify-log", &[3]),
    ("verify-case", &[4]),
    ("timestamp", &[5]),
];
/// GUI controls whose label is not an i18n string by design: the hash algorithm tick boxes show the algorithm
/// names (md5, sha256, …) as they are.
const UNTRANSLATED_LABELS: &[&str] = &["a.name()"];
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

fn name_char(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '-' || c == '_'
}

/// True when `token` appears as a whole word: `--case` does not match inside `--case-dir`, and
/// `rv-hashinv verify` does not match inside `rv-hashinv verify-log`.
fn has_token(text: &str, token: &str) -> bool {
    text.match_indices(token).any(|(i, _)| {
        let before = text[..i].chars().next_back();
        let after = text[i + token.len()..].chars().next();
        !before.is_some_and(name_char) && !after.is_some_and(name_char)
    })
}

/// The text of each task section, by task number: a `## ` heading whose second word is the number
/// ("## Task 3: …", "## Tarea 3: …", "## Tâche 3 : …"), up to the next `## ` heading.
fn task_sections(text: &str) -> BTreeMap<u32, String> {
    let mut sections = BTreeMap::new();
    let mut current: Option<u32> = None;
    for line in text.lines() {
        if let Some(h) = line.strip_prefix("## ") {
            current = h
                .split_whitespace()
                .nth(1)
                .map(|w| w.trim_end_matches(':'))
                .and_then(|w| w.parse().ok());
            continue;
        }
        if let Some(n) = current {
            let s: &mut String = sections.entry(n).or_default();
            s.push_str(line);
            s.push('\n');
        }
    }
    sections
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

/// The `tr("key")` keys in a piece of source, in order.
fn tr_keys(src: &str) -> Vec<String> {
    src.split("tr(\"")
        .skip(1)
        .filter_map(|p| p.split('"').next().map(str::to_string))
        .collect()
}

/// Label variables bound to translated strings: `let l_x = tr("k");` and
/// `let (l_a, l_b) = (tr("ka"), tr("kb"));` (also over several lines).
fn label_bindings(app: &str) -> BTreeMap<String, String> {
    let mut vars = BTreeMap::new();
    for part in app.split("let ").skip(1) {
        let Some((lhs, rhs)) = part.split_once(" = ") else {
            continue;
        };
        let rhs = rhs.split(";").next().unwrap_or("");
        let names: Vec<String> = lhs
            .trim_matches(|c| c == '(' || c == ')')
            .split(',')
            .map(|n| n.trim().to_string())
            .collect();
        let keys = tr_keys(rhs);
        if !keys.is_empty() && names.len() == keys.len() {
            for (n, k) in names.into_iter().zip(keys) {
                vars.insert(n, k);
            }
        }
    }
    vars
}

/// A control's label: an i18n key, a literal string, or None when it is untranslated by design.
#[derive(Debug, PartialEq)]
enum Label {
    Key(String),
    Literal(String),
}

/// Every button and tick box drawn by the GUI, resolved to its label. A control whose label cannot be resolved
/// fails the test, so a new control always has to be documented (or listed in UNTRANSLATED_LABELS).
fn gui_controls() -> Vec<Label> {
    let app = read("src/gui/src/app.rs");
    let vars = label_bindings(&app);
    let lines: Vec<&str> = app.lines().collect();
    let var_key = |src: &str| {
        vars.iter()
            .find(|(n, _)| has_token(src, n))
            .map(|(_, k)| k.clone())
    };
    let mut controls = vec![];
    for (i, l) in lines.iter().enumerate() {
        let lower = l.to_ascii_lowercase();
        if !(lower.contains("button(")
            || lower.contains("checkbox(")
            || lower.contains("button::new(")
            || lower.contains("checkbox::new("))
        {
            continue;
        }
        if UNTRANSLATED_LABELS.iter().any(|u| l.contains(u)) {
            continue;
        }
        let near = format!("{} {}", l, lines.get(i + 1).unwrap_or(&""));
        let label = if let Some(k) = tr_keys(&near).into_iter().next() {
            Label::Key(k)
        } else if let Some(k) = var_key(l) {
            Label::Key(k)
        } else if l.contains(", \"\")") {
            // An unlabelled tick box: its label is the `ui.label(..)` drawn just before it in the same row.
            let k = lines[i.saturating_sub(6)..i]
                .iter()
                .rev()
                .filter(|p| p.contains("ui.label("))
                .find_map(|p| var_key(p).or_else(|| tr_keys(p).into_iter().next()));
            Label::Key(k.unwrap_or_else(|| {
                panic!(
                    "app.rs:{}: cannot resolve the label of `{}`",
                    i + 1,
                    l.trim()
                )
            }))
        } else if let Some(lit) = l
            .split("(\"")
            .nth(1)
            .and_then(|r| r.split('"').next())
            .filter(|s| !s.is_empty())
        {
            Label::Literal(lit.to_string())
        } else {
            panic!(
                "app.rs:{}: cannot resolve the label of the GUI control `{}`: label it with tr(\"key\") \
                 or extend gui_controls()",
                i + 1,
                l.trim()
            );
        };
        controls.push(label);
    }
    controls
}

#[test]
fn quick_guide_documents_every_command_flag_button_and_exit_code() {
    let commands = documented_commands();
    assert!(commands.len() >= 5, "{:?}", commands);
    for lang in LANGS {
        let text = guide(lang);
        for c in &commands {
            assert!(
                has_token(&text, &format!("{} {}", TOOL, c)),
                "the {} quick guide does not mention `{} {}`",
                lang,
                TOOL,
                c
            );
        }
        let sections = task_sections(&text);
        assert_eq!(
            sections.keys().copied().collect::<Vec<_>>(),
            [1, 2, 3, 4, 5],
            "the {} quick guide must have the five task sections",
            lang
        );
        for (c, tasks) in TASK_COMMANDS {
            assert!(commands.iter().any(|d| d == c), "{} is not a subcommand", c);
            let flags = long_flags(c);
            assert!(!flags.is_empty(), "{} --help lists no flag", c);
            let own: String = tasks.iter().map(|n| sections[n].as_str()).collect();
            for f in flags {
                assert!(
                    has_token(&own, &f),
                    "the {} quick guide does not document `{} {}` in task {:?}",
                    lang,
                    c,
                    f,
                    tasks
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
}

#[test]
fn quick_guide_describes_every_gui_button_and_tick_box() {
    let controls = gui_controls();
    assert!(
        controls.contains(&Label::Key("start".into()))
            && controls.contains(&Label::Key("tsa".into()))
            && controls.contains(&Label::Key("browse".into())),
        "{:?}",
        controls
    );
    for c in &controls {
        for lang in LANGS {
            let label = match c {
                Label::Key(k) => ui_label(lang, k),
                Label::Literal(s) => s.clone(),
            };
            assert!(
                guide(lang).contains(&format!("**{}", label)),
                "the {} quick guide does not describe the GUI control {:?} (**{}**)",
                lang,
                c,
                label
            );
        }
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
