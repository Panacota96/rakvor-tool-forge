//! Domain logic — THE PART EACH NEW TOOL REPLACES.
//!
//! This reference analyzer inventories evidence: it detects the real file type from its
//! signature (magic bytes), flags extension mismatches, executables under other names and
//! empty files. Duplicates are found later, across items, by `run.rs`.
//! A new tool keeps the same `analyze` signature and `Finding` shape so the rest of the
//! pipeline (audit, reports, CLI, GUI) works unchanged.

use serde::Serialize;
use std::collections::BTreeMap;

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Severity {
    Info,
    Low,
    Medium,
    High,
}

impl Severity {
    pub fn key(&self) -> &'static str {
        match self {
            Severity::Info => "info",
            Severity::Low => "low",
            Severity::Medium => "medium",
            Severity::High => "high",
        }
    }
}

/// One finding: a stable code (worded in i18n/*.json), a severity and named parameters.
#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct Finding {
    pub code: String,
    pub severity: Severity,
    pub params: BTreeMap<String, String>,
}

impl Finding {
    pub fn new(code: &str, severity: Severity) -> Finding {
        Finding {
            code: code.into(),
            severity,
            params: BTreeMap::new(),
        }
    }
    pub fn with(mut self, k: &str, v: impl Into<String>) -> Finding {
        self.params.insert(k.into(), v.into());
        self
    }
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct Analysis {
    pub detected_type: Option<String>,
    pub extension: Option<String>,
    pub findings: Vec<Finding>,
}

struct Signature {
    name: &'static str,
    offset: usize,
    magic: &'static [u8],
    extensions: &'static [&'static str],
    executable: bool,
    /// A second check for a short `magic` that random data also matches (`MZ`, `1F 8B`: 1 in 65,536). Given the
    /// first bytes and the file size, it returns the detected name, or None when the file is not this type.
    confirm: Option<Confirm>,
}

/// A signature's second check: (first bytes, file size) → the detected name, or None.
type Confirm = fn(&[u8], u64) -> Option<&'static str>;

fn le16(h: &[u8], at: usize) -> Option<u64> {
    Some(u64::from(u16::from_le_bytes(
        h.get(at..at + 2)?.try_into().ok()?,
    )))
}

/// `MZ`: a Windows (PE) executable when `PE\0\0` sits at the offset stored at 0x3C, or when that offset points past
/// the first bytes read but inside the file (the Windows loader accepts up to 256 MiB, so a moved header cannot hide
/// an executable); else a DOS executable when the DOS header is self-consistent (image length within the file, header
/// paragraphs within the image); else not an executable.
fn confirm_mz(h: &[u8], size: u64) -> Option<&'static str> {
    if let Some(b) = h.get(0x3C..0x40) {
        let lfanew = u64::from(u32::from_le_bytes(b.try_into().ok()?));
        let at = usize::try_from(lfanew).ok();
        match at.and_then(|a| h.get(a..a.checked_add(4)?)) {
            Some(sig) if sig == b"PE\0\0" && lfanew >= 0x40 => {
                return Some("Windows executable (PE)");
            }
            None if (0x40..0x1000_0000).contains(&lfanew)
                && lfanew + 4 <= size
                && lfanew + 4 > h.len() as u64 =>
            {
                return Some("Windows executable (PE, header after the first 4 KiB, not checked)");
            }
            _ => {}
        }
    }
    let (last, pages, hdr_par) = (le16(h, 2)?, le16(h, 4)?, le16(h, 8)?);
    let image = pages.checked_sub(1)? * 512 + if last == 0 { 512 } else { last };
    (last < 512 && hdr_par >= 2 && hdr_par * 16 <= image && image <= size)
        .then_some("DOS executable (MZ)")
}

/// `1F 8B`: gzip only with compression method 8 (deflate, the only one defined) and the reserved flag bits clear
/// (RFC 1952).
fn confirm_gzip(h: &[u8], _size: u64) -> Option<&'static str> {
    (h.get(2) == Some(&8) && h.get(3).is_some_and(|f| f & 0xE0 == 0)).then_some("GZIP archive")
}

const SIGNATURES: &[Signature] = &[
    Signature {
        name: "PDF document",
        offset: 0,
        magic: b"%PDF-",
        extensions: &["pdf"],
        executable: false,
        confirm: None,
    },
    Signature {
        name: "PNG image",
        offset: 0,
        magic: b"\x89PNG\r\n\x1a\n",
        extensions: &["png"],
        executable: false,
        confirm: None,
    },
    Signature {
        name: "JPEG image",
        offset: 0,
        magic: b"\xFF\xD8\xFF",
        extensions: &["jpg", "jpeg", "jpe", "jfif"],
        executable: false,
        confirm: None,
    },
    Signature {
        name: "GIF image",
        offset: 0,
        magic: b"GIF8",
        extensions: &["gif"],
        executable: false,
        confirm: None,
    },
    Signature {
        name: "ZIP / Office Open XML",
        offset: 0,
        magic: b"PK\x03\x04",
        extensions: &[
            "zip", "docx", "xlsx", "pptx", "odt", "ods", "jar", "apk", "epub",
        ],
        executable: false,
        confirm: None,
    },
    Signature {
        name: "OLE2 (legacy Office, MSG)",
        offset: 0,
        magic: b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1",
        extensions: &["doc", "xls", "ppt", "msg"],
        executable: false,
        confirm: None,
    },
    Signature {
        name: "Windows executable (PE)",
        offset: 0,
        magic: b"MZ",
        extensions: &["exe", "dll", "sys", "scr", "com", "efi"],
        executable: true,
        confirm: Some(confirm_mz),
    },
    Signature {
        name: "Linux executable (ELF)",
        offset: 0,
        magic: b"\x7fELF",
        extensions: &["so", "bin", "elf", "o"],
        executable: true,
        confirm: None,
    },
    Signature {
        name: "SQLite database",
        offset: 0,
        magic: b"SQLite format 3\0",
        extensions: &["db", "sqlite", "sqlite3"],
        executable: false,
        confirm: None,
    },
    Signature {
        name: "7-Zip archive",
        offset: 0,
        magic: b"7z\xBC\xAF\x27\x1C",
        extensions: &["7z"],
        executable: false,
        confirm: None,
    },
    Signature {
        name: "RAR archive",
        offset: 0,
        magic: b"Rar!\x1A\x07",
        extensions: &["rar"],
        executable: false,
        confirm: None,
    },
    Signature {
        name: "GZIP archive",
        offset: 0,
        magic: b"\x1F\x8B",
        extensions: &["gz", "tgz"],
        executable: false,
        confirm: Some(confirm_gzip),
    },
    Signature {
        name: "Windows event log (EVTX)",
        offset: 0,
        magic: b"ElfFile\0",
        extensions: &["evtx"],
        executable: false,
        confirm: None,
    },
    Signature {
        name: "Windows registry hive",
        offset: 0,
        magic: b"regf",
        extensions: &["dat", "hve"],
        executable: false,
        confirm: None,
    },
    Signature {
        name: "MP4 / QuickTime video",
        offset: 4,
        magic: b"ftyp",
        extensions: &["mp4", "m4v", "mov", "m4a", "3gp", "heic"],
        executable: false,
        confirm: None,
    },
];

/// The signature the first bytes match, with the detected name (a `confirm` check may refine or reject it).
fn detect(head: &[u8], size: u64) -> Option<(&'static Signature, &'static str)> {
    SIGNATURES.iter().find_map(|s| {
        let prefix = head.len() >= s.offset + s.magic.len()
            && &head[s.offset..s.offset + s.magic.len()] == s.magic;
        if !prefix {
            return None;
        }
        match s.confirm {
            Some(check) => check(head, size).map(|name| (s, name)),
            None => Some((s, s.name)),
        }
    })
}

fn known_extension(ext: &str) -> bool {
    SIGNATURES.iter().any(|s| s.extensions.contains(&ext))
}

/// Analyzes one item from its name, size and first bytes (no second read of the evidence).
pub fn analyze(file_name: &str, size: u64, head: &[u8]) -> Analysis {
    let extension = std::path::Path::new(file_name)
        .extension()
        .map(|e| e.to_string_lossy().to_ascii_lowercase());
    let sig = detect(head, size);
    let mut findings = Vec::new();
    if size == 0 {
        findings.push(Finding::new("EMPTY_FILE", Severity::Info));
    }
    if let (Some((s, name)), Some(ext)) = (sig, extension.as_deref())
        && !s.extensions.contains(&ext)
    {
        if s.executable {
            findings.push(
                Finding::new("EXECUTABLE_CONTENT", Severity::High)
                    .with("detected", name)
                    .with("extension", ext),
            );
        } else if known_extension(ext)
            || matches!(ext, "txt" | "csv" | "log" | "xml" | "json" | "html")
        {
            findings.push(
                Finding::new("EXTENSION_MISMATCH", Severity::Medium)
                    .with("detected", name)
                    .with("extension", ext),
            );
        }
    }
    Analysis {
        detected_type: sig.map(|(_, name)| name.to_string()),
        extension,
        findings,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detects_types_and_mismatches() {
        let a = analyze("report.pdf", 10, b"%PDF-1.7\n");
        assert_eq!(a.detected_type.as_deref(), Some("PDF document"));
        assert!(a.findings.is_empty());

        let b = analyze("holiday.jpg", 10, b"%PDF-1.7\n");
        assert_eq!(b.findings[0].code, "EXTENSION_MISMATCH");

        let c = analyze("invoice.pdf", 4096, &pe_head(0x80));
        assert_eq!(c.findings[0].code, "EXECUTABLE_CONTENT");
        assert_eq!(c.findings[0].severity, Severity::High);

        let d = analyze("empty.txt", 0, b"");
        assert_eq!(d.findings[0].code, "EMPTY_FILE");

        let e = analyze("notes.md", 10, b"hello");
        assert!(e.detected_type.is_none() && e.findings.is_empty());

        let f = analyze("clip.mov", 12, b"\0\0\0\x18ftypqt  ");
        assert!(f.findings.is_empty());
    }

    /// A 4 KiB head of a PE file whose NT header sits at `lfanew` (as linkers write it: DOS header, stub, `PE\0\0`).
    fn pe_head(lfanew: u32) -> Vec<u8> {
        let mut h = vec![0u8; 4096];
        h[..2].copy_from_slice(b"MZ");
        h[2..4].copy_from_slice(&0x90u16.to_le_bytes()); // bytes on the last page
        h[4..6].copy_from_slice(&3u16.to_le_bytes()); // pages
        h[8..10].copy_from_slice(&4u16.to_le_bytes()); // header paragraphs
        h[0x3C..0x40].copy_from_slice(&lfanew.to_le_bytes());
        if let Some(p) = h.get_mut(lfanew as usize..lfanew as usize + 4) {
            p.copy_from_slice(b"PE\0\0");
        }
        h
    }

    /// QA 2026-11-23, F1: `MZ` and `1F 8B` alone (1 in 65,536 for random data) are no longer executables or gzip;
    /// real PE, DOS and gzip headers still are, and a PE header moved past the first 4 KiB is still flagged.
    #[test]
    fn short_magics_are_confirmed() {
        // A real PE, as a .pdf: high.
        let a = analyze("invoice.pdf", 70_000, &pe_head(0xE8));
        assert_eq!(a.detected_type.as_deref(), Some("Windows executable (PE)"));
        assert_eq!(
            (a.findings[0].code.as_str(), a.findings[0].severity),
            ("EXECUTABLE_CONTENT", Severity::High)
        );
        // Its NT header after the first 4 KiB (legal): still flagged, never a way to hide an executable.
        let mut moved = pe_head(0x2000);
        moved[0x3C..0x40].copy_from_slice(&0x2000u32.to_le_bytes());
        let b = analyze("invoice.pdf", 70_000, &moved);
        assert!(
            b.detected_type
                .as_deref()
                .unwrap()
                .starts_with("Windows executable (PE"),
            "{b:?}"
        );
        assert_eq!(b.findings[0].code, "EXECUTABLE_CONTENT");
        // A DOS executable (no PE header): still an executable.
        // (a head without `PE\0\0`: built with the NT header past 4 KiB, then the offset cleared)
        let mut dos = pe_head(0x2000);
        dos[0x3C..0x40].copy_from_slice(&0u32.to_le_bytes());
        let c = analyze("game.dat", 1536, &dos);
        assert_eq!(c.detected_type.as_deref(), Some("DOS executable (MZ)"));
        assert_eq!(c.findings[0].code, "EXECUTABLE_CONTENT");
        // The QA data: random bytes that start with "MZ" (index 0x5A4D) or 1F 8B (index 0x8B1F).
        let mut rnd: Vec<u8> = (0..4096u32)
            .map(|i| (i.wrapping_mul(2_654_435_761) >> 13) as u8)
            .collect();
        rnd[..2].copy_from_slice(b"MZ");
        let d = analyze("f23117.dat", 33_000, &rnd);
        assert!(d.detected_type.is_none() && d.findings.is_empty(), "{d:?}");
        rnd[..2].copy_from_slice(b"\x1F\x8B");
        let e = analyze("f35615.dat", 33_000, &rnd);
        assert!(e.detected_type.is_none() && e.findings.is_empty(), "{e:?}");
        // A real gzip member header: detected, and a mismatch under another known extension.
        let gz = b"\x1F\x8B\x08\x00\0\0\0\0\0\x03rest";
        assert_eq!(
            analyze("a.gz", 30, gz).detected_type.as_deref(),
            Some("GZIP archive")
        );
        assert_eq!(
            analyze("a.dat", 30, gz).findings[0].code,
            "EXTENSION_MISMATCH"
        );
        // Reserved flag bits set, or another method: not gzip.
        assert!(
            analyze("a.dat", 30, b"\x1F\x8B\x08\xE0")
                .detected_type
                .is_none()
        );
        assert!(
            analyze("a.dat", 30, b"\x1F\x8B\x07\x00")
                .detected_type
                .is_none()
        );
        // Too short to check: a 2-byte "MZ" file is not an executable.
        assert!(analyze("x.pdf", 2, b"MZ").findings.is_empty());
    }
}
