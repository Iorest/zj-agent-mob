//! Footer key hints. Ribbons are Zellij's own mode-indicator component, so they
//! follow the user's theme rather than the fixed colours the panel once used.

/// One footer entry: the key to press, and what it does.
pub(crate) struct Hint {
    pub(crate) key: &'static str,
    pub(crate) action: &'static str,
}

impl Hint {
    pub(crate) const fn new(key: &'static str, action: &'static str) -> Self {
        Hint { key, action }
    }

    /// No angle brackets: they crowd the enter glyph into an unreadable smudge.
    /// Colour separates the key from the action instead.
    pub(crate) fn text(&self) -> String {
        format!("{} {}", self.key, self.action)
    }

    /// **Character** offsets, which is what Zellij's colour ranges index. The
    /// enter glyph is 3 bytes, so `key.len()` would bleed the colour into the
    /// action word.
    pub(crate) fn key_range(&self) -> std::ops::Range<usize> {
        0..crate::style::chars(self.key)
    }
}

/// The default footer, at 83 of the 84-column ribbon budget. `/ find` was paid
/// for by tightening three labels: the digit fast path lost its slot because
/// every row prints its own number, so `g` is the only goto spelling that needs
/// advertising. `N new` is the one entry with no room left to spare - adding a
/// key here means trimming a label, which is the point of the budget: a footer
/// that silently drops a segment is a key the user cannot find.
pub(crate) const LIST_HINTS: &[Hint] = &[
    Hint::new("\u{21b5}", "jmp"),
    Hint::new("g", "go"),
    Hint::new("/", "find"),
    Hint::new("x", "kill"),
    Hint::new("d", "clr"),
    Hint::new("s", "sort"),
    Hint::new("N", "new"),
    Hint::new("q", "hide"),
    Hint::new("t", "sh"),
];

/// Shown while the selected agent is blocked on you, so the keys that type into
/// its pane appear only when there is a prompt there to answer.
pub(crate) const REPLY_HINTS: &[Hint] = &[
    Hint::new("y", "yes"),
    Hint::new("n", "no"),
    Hint::new("m", "message"),
    Hint::new("f", "queue"),
    Hint::new("\u{21b5}", "jump"),
    Hint::new("x", "kill"),
    Hint::new("q", "hide"),
];

/// The one-line editor owns the keyboard while it is up.
pub(crate) const REPLY_EDIT_HINTS: &[Hint] = &[Hint::new("\u{21b5}", "send"), Hint::new("esc", "cancel")];

/// Composing an instruction the agent receives when its current turn ends.
pub(crate) const FOLLOWUP_EDIT_HINTS: &[Hint] = &[Hint::new("\u{21b5}", "queue"), Hint::new("esc", "cancel")];

/// Shown only while the selected agent has a permission prompt parked. Keeping
/// approve and reject out of the default footer means they cannot be pressed
/// by muscle memory when no prompt is waiting.
pub(crate) const ASK_HINTS: &[Hint] = &[
    Hint::new("a", "approve"),
    Hint::new("r", "reject"),
    Hint::new("A", "always"),
    Hint::new("\u{21b5}", "jump"),
    Hint::new("x", "kill"),
    Hint::new("q", "hide"),
];

/// A space each side of every segment, plus the two joining arrow glyphs.
const RIBBON_PADDING: usize = 4;

pub(crate) fn ribbon_width(hints: &[Hint]) -> usize {
    hints.iter().map(|h| h.text().chars().count() + RIBBON_PADDING).sum()
}

/// Fallback for panes too narrow for ribbons, which drop whole segments from
/// the middle rather than truncating, silently losing a key.
pub(crate) fn plain_line(hints: &[Hint]) -> String {
    let joined: Vec<String> = hints.iter().map(|h| format!("{} {}", h.key, h.action)).collect();
    format!(" {}", joined.join("  "))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// No angle brackets: they crowded the enter glyph into an unreadable
    /// smudge. Colour separates the key from the action instead.
    #[test]
    fn hint_text_is_the_bare_key_and_action() {
        assert_eq!(Hint::new("x", "kill").text(), "x kill");
        assert_eq!(Hint::new("1-9/g", "goto").text(), "1-9/g goto");
        assert_eq!(Hint::new("\u{21b5}", "jump").text(), "\u{21b5} jump");
    }

    /// The highlighted range is in characters and must cover exactly the key,
    /// stopping before the separating space. A byte-length range would spill
    /// into the action word for the multi-byte enter glyph.
    #[test]
    fn key_range_covers_the_key_only() {
        for h in LIST_HINTS
            .iter()
            .chain(ASK_HINTS)
            .chain(REPLY_HINTS)
            .chain(REPLY_EDIT_HINTS)
            .chain(FOLLOWUP_EDIT_HINTS)
        {
            let text = h.text();
            let r = h.key_range();
            let covered: String = text.chars().skip(r.start).take(r.end - r.start).collect();
            assert_eq!(covered, h.key, "range must cover exactly the key in {:?}", text);
            assert_eq!(
                text.chars().nth(r.end),
                Some(' '),
                "the char after the range must be the separating space in {:?}",
                text
            );
        }
    }

    #[test]
    fn every_hint_has_a_distinct_key() {
        for set in [LIST_HINTS, ASK_HINTS, REPLY_HINTS, REPLY_EDIT_HINTS] {
            let mut keys: Vec<&str> = set.iter().map(|h| h.key).collect();
            keys.sort_unstable();
            let before = keys.len();
            keys.dedup();
            assert_eq!(keys.len(), before, "duplicate key in a hint set");
        }
    }

    /// The plain fallback is what a narrow pane actually shows, so it has to be
    /// narrower than the ribbons it replaces and still name every key.
    #[test]
    fn plain_fallback_is_narrower_and_keeps_every_key() {
        for set in [LIST_HINTS] {
            let plain = plain_line(set);
            assert!(
                plain.chars().count() < ribbon_width(set),
                "the fallback must save columns: {:?}",
                plain
            );
            for h in set {
                assert!(plain.contains(h.key), "missing key {:?} in {:?}", h.key, plain);
                assert!(plain.contains(h.action), "missing action {:?}", h.action);
            }
        }
    }

    /// Dropping the angle brackets bought back two columns per hint, which is
    /// what lets the full list footer render as ribbons in a typical floating
    /// pane instead of falling back to plain text.
    #[test]
    fn every_hint_row_fits_a_typical_pane_as_ribbons() {
        for (name, set) in [
            ("list", LIST_HINTS),
            ("ask", ASK_HINTS),
            ("reply", REPLY_HINTS),
            ("reply-edit", REPLY_EDIT_HINTS),
            ("followup-edit", FOLLOWUP_EDIT_HINTS),
        ] {
            assert!(
                ribbon_width(set) <= 84,
                "{} hints need {} columns; Zellij would silently drop one",
                name,
                ribbon_width(set)
            );
        }
    }

    /// The footer is the only discoverability surface for these keys, so every
    /// key the list screen handles should appear in it.
    #[test]
    fn list_hints_cover_the_documented_keys() {
        let keys: Vec<&str> = LIST_HINTS.iter().map(|h| h.key).collect();
        for expect in ["x", "d", "s", "q", "g", "/"] {
            assert!(keys.contains(&expect), "missing hint for {:?}", expect);
        }
    }

    /// Keys kept out of every footer on purpose, each with the surface that
    /// advertises it instead. The guard below is the only thing between a new
    /// binding and shipping invisible, so an excuse has to name where the key
    /// *is* discoverable rather than stating that it is not.
    const EXCUSED: &[(char, &str)] = &[
        // Shift-variants: one slipped finger must not reach the whole-fleet
        // action. Checked against the lowercase hint that is in the footer.
        ('D', "shift-variant of d"),
        ('G', "shift-variant of g"),
        // Vim motions for the action the "jmp" hint already names.
        ('j', "motion for the jump hint"),
        ('k', "motion for the jump hint"),
        // Only does anything on a row whose subagent fan-out is already on
        // screen, where the branch badge and the expanded detail line name it.
        ('o', "advertised by the badge it expands"),
    ];

    /// Every list key has to be reachable from the footer. This test used to
    /// carry its own copy of the key list, so `N` - bound in the handler, absent
    /// from `LIST_HINTS`, absent from that copy - passed every check while the
    /// "new agent" action had no way to be found. It now iterates the inventory
    /// the handler itself is tested against.
    #[test]
    fn no_list_key_is_undiscoverable() {
        let footers = [LIST_HINTS, REPLY_HINTS, ASK_HINTS];
        let advertised = |set: &[Hint], key: char| set.iter().any(|h| h.key == key.to_string());
        for key in crate::keys::LIST_KEYS.iter().copied() {
            if footers.iter().any(|set| advertised(set, key)) {
                continue;
            }
            // A shift-variant is only excused when its base spelling is in the
            // default footer: otherwise nothing names the action at all.
            if let Some(base) = key.to_lowercase().next().filter(|base| *base != key) {
                assert!(
                    advertised(LIST_HINTS, base),
                    "{base:?} is not in the default footer either, so {key:?} is unreachable"
                );
                continue;
            }
            assert!(
                EXCUSED
                    .iter()
                    .any(|(excused, reason)| *excused == key && !reason.is_empty()),
                "list key {key:?} has no discoverability surface: put it in a footer hint, \
                 or in EXCUSED with the surface that advertises it instead"
            );
        }
    }
}
