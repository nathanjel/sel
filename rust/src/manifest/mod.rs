pub mod builtins;

use std::collections::HashMap;
use std::sync::OnceLock;

pub use builtins::Entry;

pub fn lookup_builtin(name: &str) -> Option<&'static Entry> {
    static MAP: OnceLock<HashMap<&'static str, &'static Entry>> = OnceLock::new();
    let map = MAP.get_or_init(|| {
        let mut m = HashMap::with_capacity(builtins::BUILTINS.len());
        for (k, v) in builtins::BUILTINS {
            m.insert(*k, v);
        }
        m
    });
    map.get(name).copied()
}

pub struct BindingFormResult {
    pub scopes: &'static [builtins::Scope],
    pub binds: Vec<String>,
}

static FORMS_MAP: OnceLock<HashMap<&'static str, &'static [builtins::Form]>> = OnceLock::new();

fn forms_of(name: &str) -> Option<&'static [builtins::Form]> {
    let map = FORMS_MAP.get_or_init(|| {
        let mut m = HashMap::with_capacity(builtins::BINDING_FORMS.len());
        for (k, v) in builtins::BINDING_FORMS {
            m.insert(*k, *v);
        }
        m
    });
    map.get(name).copied()
}

fn guard_holds(f: &builtins::Form, args: &[crate::ast::Node]) -> bool {
    let Some(arg_idx) = f.when_arg else { return true };
    let Some(a) = args.get(arg_idx) else { return false };
    match f.when_kind {
        builtins::WhenKind::Name => a.t == crate::ast::NodeType::Var && !a.grouped,
        builtins::WhenKind::Text => a.t == crate::ast::NodeType::Text,
        builtins::WhenKind::None => true,
    }
}

/// The manifest form a call of `name` (canonical, upper case) with these
/// arguments takes: the forms for its count are tried in the manifest's
/// order, a guarded one only when its guard holds. That order is where
/// "a text literal in the direction's place wins over a bare name in the
/// binder's" (SORT_BY, TOP_BY) is decided -- the guarded text form comes
/// first. No allocation: the evaluator asks once per call.
pub fn match_form(name: &str, args: &[crate::ast::Node]) -> Option<&'static builtins::Form> {
    forms_of(name)?.iter().find(|f| f.count == args.len() && guard_holds(f, args))
}

/// Which argument of a call is what, from the form it matches
/// (spec/builtins.md, "Binding forms"): the binder, the first and second
/// arguments that run inside the binder scope, and the outer arguments other
/// than the source, in order (a sort's direction, a TOP's count, a LINK's
/// right side).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct ArgRoles {
    pub binder: Option<usize>,
    pub body: Option<usize>,
    pub extra: Option<usize>,
    outer: [usize; 4],
    outer_len: u8,
}

impl ArgRoles {
    pub fn outer(&self) -> &[usize] {
        &self.outer[..self.outer_len as usize]
    }
}

pub fn arg_roles(name: &str, args: &[crate::ast::Node]) -> Option<ArgRoles> {
    use builtins::Role;
    let form = match_form(name, args)?;
    let mut r = ArgRoles::default();
    for (i, role) in form.roles.iter().enumerate() {
        match role {
            Role::Source => {}
            Role::Binder => {
                if r.binder.is_none() {
                    r.binder = Some(i);
                }
            }
            Role::Outer => {
                r.outer[r.outer_len as usize] = i;
                r.outer_len += 1;
            }
            Role::Body | Role::Key | Role::Proj | Role::Pred => {
                if r.body.is_none() {
                    r.body = Some(i);
                } else if r.extra.is_none() {
                    r.extra = Some(i);
                }
            }
        }
    }
    Some(r)
}

/// The roles of a SORT/SORT_DESC/SORT_BY/TOP/TOP_DESC/TOP_BY call: a TOP's
/// count is its last outer argument, and an outer argument before it (or a
/// sort's only one) is the direction.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct SortRoles {
    pub binder: Option<usize>,
    pub key: Option<usize>,
    pub dir: Option<usize>,
    pub limit: Option<usize>,
}

pub fn sort_roles(name: &str, args: &[crate::ast::Node]) -> Option<SortRoles> {
    let r = arg_roles(name, args)?;
    let outer = r.outer();
    let top = name.starts_with("TOP");
    let (dir, limit) = if top {
        (if outer.len() == 2 { Some(outer[0]) } else { None }, outer.last().copied())
    } else {
        (outer.first().copied(), None)
    };
    Some(SortRoles { binder: r.binder, key: r.body, dir, limit })
}

/// Whether a text literal at `index` is what selects one of the call's forms
/// at its count -- the direction of SORT_BY/TOP_BY when argument 2 is also a
/// bare name, so the binder form is the alternative. Folding a constant into
/// a text literal there would change the form the call takes.
pub fn text_selects_form(name: &str, args: &[crate::ast::Node], index: usize) -> bool {
    let Some(forms) = forms_of(name) else { return false };
    let count = args.len();
    let text_guard = forms.iter().any(|f| {
        f.count == count && f.when_arg == Some(index) && f.when_kind == builtins::WhenKind::Text
    });
    text_guard
        && forms.iter().any(|f| {
            f.count == count && f.when_kind == builtins::WhenKind::Name && guard_holds(f, args)
        })
}

pub fn binding_form(name: &str, args: &[crate::ast::Node], spec_binds: bool) -> Option<BindingFormResult> {
    let key = name.to_ascii_uppercase();
    if let Some(forms) = forms_of(key.as_str()) {
        let f = forms.iter().find(|f| f.count == args.len() && guard_holds(f, args))?;
        let mut bound: Vec<String> = f.binds.iter().map(|&s| s.to_string()).collect();
        for (i, sc) in f.scopes.iter().enumerate() {
            if *sc == builtins::Scope::Binder && i < args.len() && args[i].t == crate::ast::NodeType::Var {
                bound.push(args[i].s.clone());
            }
        }
        return Some(BindingFormResult {
            scopes: f.scopes,
            binds: bound,
        });
    }

    if !spec_binds {
        return None;
    }
    if args.len() == 2 {
        static TWO_SCOPES: &[builtins::Scope] = &[builtins::Scope::Outer, builtins::Scope::Inner];
        return Some(BindingFormResult {
            scopes: TWO_SCOPES,
            binds: vec!["_".to_string(), "_K".to_string()],
        });
    }
    if args.len() == 3 && args[1].t == crate::ast::NodeType::Var && !args[1].grouped {
        static THREE_SCOPES: &[builtins::Scope] = &[builtins::Scope::Outer, builtins::Scope::Binder, builtins::Scope::Inner];
        return Some(BindingFormResult {
            scopes: THREE_SCOPES,
            binds: vec!["_K".to_string(), args[1].s.clone()],
        });
    }
    None
}
