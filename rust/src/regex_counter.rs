//! Counted-repeat fallback for portable expressions too large for regex's NFA.
//! Repeats retain counters instead of expanding their bodies. Execution uses an
//! explicit ordered work stack, preserving leftmost-first and greedy/lazy order.
use regex_syntax::hir::{Class, Hir, HirKind, Look, Repetition};

pub(crate) struct CounterRegex {
    hir: Hir,
    pub captures_len: usize,
}

type Groups = Vec<(Option<usize>, Option<usize>)>;

#[derive(Clone)]
enum Task<'a> {
    Node(&'a Hir),
    EndCapture(usize),
    Repeat(&'a Repetition, usize),
}

#[derive(Clone)]
struct State<'a> {
    tasks: Vec<Task<'a>>,
    offset: usize,
    groups: Groups,
}

impl CounterRegex {
    pub fn new(pattern: &str) -> Result<Self, regex_syntax::Error> {
        let hir = regex_syntax::Parser::new().parse(pattern)?;
        let mut captures_len = 1;
        let mut todo = vec![&hir];
        while let Some(node) = todo.pop() {
            if let HirKind::Capture(cap) = node.kind() {
                captures_len = captures_len.max(cap.index as usize + 1);
            }
            todo.extend(node.kind().subs());
        }
        Ok(Self { hir, captures_len })
    }

    pub fn captures_at(&self, text: &str, scan: usize) -> Option<Groups> {
        let minimum = self.hir.properties().minimum_len()?;
        let mut start = scan;
        while start <= text.len() && minimum <= text.len() - start {
            if let Some(mut groups) = self.at(text, start) {
                groups[0].0 = Some(start);
                return Some(groups);
            }
            start += text[start..].chars().next().map_or(1, char::len_utf8);
        }
        None
    }

    fn at(&self, text: &str, start: usize) -> Option<Groups> {
        let mut choices = vec![State {
            tasks: vec![Task::Node(&self.hir)],
            offset: start,
            groups: vec![(None, None); self.captures_len],
        }];
        while let Some(mut state) = choices.pop() {
            let mut failed = false;
            while let Some(task) = state.tasks.pop() {
                let remaining = text.len() - state.offset;
                match task {
                    Task::EndCapture(index) => state.groups[index].1 = Some(state.offset),
                    Task::Repeat(rep, count) => {
                        let required = (rep.min as usize).saturating_sub(count);
                        let minimum = rep.sub.properties().minimum_len();
                        if required > 0
                            && minimum.is_none_or(|n| n.saturating_mul(required) > remaining)
                        {
                            failed = true;
                            break;
                        }
                        let can_stop = count >= rep.min as usize;
                        let can_repeat = rep.max.is_none_or(|max| count < max as usize)
                            && minimum.is_some_and(|n| n <= remaining);
                        if !can_repeat {
                            if !can_stop {
                                failed = true;
                                break;
                            }
                            continue;
                        }
                        // Portable validation forbids repeated nullable bodies.
                        // A nullable body can only reach this branch for ?/{0,1}.
                        if !can_stop {
                            state.tasks.push(Task::Repeat(rep, count + 1));
                            state.tasks.push(Task::Node(&rep.sub));
                        } else {
                            let mut repeat = state.clone();
                            repeat.tasks.push(Task::Repeat(rep, count + 1));
                            repeat.tasks.push(Task::Node(&rep.sub));
                            if rep.greedy {
                                choices.push(state);
                                state = repeat;
                            } else {
                                choices.push(repeat);
                            }
                        }
                    }
                    Task::Node(node) => {
                        if node
                            .properties()
                            .minimum_len()
                            .is_none_or(|n| n > remaining)
                        {
                            failed = true;
                            break;
                        }
                        match node.kind() {
                            HirKind::Empty => {}
                            HirKind::Literal(lit) => {
                                if !text.as_bytes()[state.offset..].starts_with(&lit.0) {
                                    failed = true;
                                    break;
                                }
                                state.offset += lit.0.len();
                            }
                            HirKind::Class(class) => {
                                let Some(ch) = text[state.offset..].chars().next() else {
                                    failed = true;
                                    break;
                                };
                                let contains = match class {
                                    Class::Unicode(c) => {
                                        c.ranges().iter().any(|r| r.start() <= ch && ch <= r.end())
                                    }
                                    Class::Bytes(c) => {
                                        ch.is_ascii()
                                            && c.ranges().iter().any(|r| {
                                                r.start() <= ch as u8 && ch as u8 <= r.end()
                                            })
                                    }
                                };
                                if !contains {
                                    failed = true;
                                    break;
                                }
                                state.offset += ch.len_utf8();
                            }
                            HirKind::Look(look) => {
                                let matches = match look {
                                    Look::Start => state.offset == 0,
                                    Look::End => state.offset == text.len(),
                                    _ => unreachable!(
                                        "portable validator only permits whole-subject anchors"
                                    ),
                                };
                                if !matches {
                                    failed = true;
                                    break;
                                }
                            }
                            HirKind::Capture(cap) => {
                                state.groups[cap.index as usize] = (Some(state.offset), None);
                                state.tasks.push(Task::EndCapture(cap.index as usize));
                                state.tasks.push(Task::Node(&cap.sub));
                            }
                            HirKind::Concat(nodes) => {
                                state.tasks.extend(nodes.iter().rev().map(Task::Node))
                            }
                            HirKind::Alternation(nodes) => {
                                for node in nodes[1..].iter().rev() {
                                    let mut alternative = state.clone();
                                    alternative.tasks.push(Task::Node(node));
                                    choices.push(alternative);
                                }
                                state.tasks.push(Task::Node(&nodes[0]));
                            }
                            HirKind::Repetition(rep) => state.tasks.push(Task::Repeat(rep, 0)),
                        }
                    }
                }
            }
            if !failed {
                state.groups[0].1 = Some(state.offset);
                return Some(state.groups);
            }
        }
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn counter_matches_engine_captures_and_search_order() {
        let atoms = ["a", "[ab]", "[^a]", ".", "é", "(a)", "(?:ab|a)", "(?:a|ab)"];
        let mut patterns = vec![
            "".to_owned(),
            "(a)?(b)?".into(),
            "^a$".into(),
            "(a)|(b)".into(),
        ];
        for atom in atoms {
            for repeat in [
                "", "?", "*", "+", "{2}", "{1,3}", "??", "*?", "+?", "{1,3}?",
            ] {
                patterns.push(format!("{atom}{repeat}"));
                patterns.push(format!("({atom}{repeat})b"));
            }
        }
        let mut subjects = vec![String::new()];
        for _ in 0..4 {
            let previous = subjects.clone();
            for subject in previous {
                for ch in ['a', 'b', 'é', '\n'] {
                    subjects.push(format!("{subject}{ch}"));
                }
            }
        }
        subjects.sort();
        subjects.dedup();
        for pattern in patterns {
            let pattern = format!("(?s){pattern}");
            let counter = CounterRegex::new(&pattern).unwrap();
            let engine = regex::Regex::new(&pattern).unwrap();
            for subject in &subjects {
                for scan in subject
                    .char_indices()
                    .map(|(i, _)| i)
                    .chain(std::iter::once(subject.len()))
                {
                    let expected = engine.captures_at(subject, scan).map(|caps| {
                        caps.iter()
                            .map(|m| m.map_or((None, None), |m| (Some(m.start()), Some(m.end()))))
                            .collect::<Groups>()
                    });
                    assert_eq!(
                        counter.captures_at(subject, scan),
                        expected,
                        "pattern={pattern:?} subject={subject:?} scan={scan}"
                    );
                }
            }
        }
    }
}
