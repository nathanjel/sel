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

pub fn binding_form(name: &str, args: &[crate::ast::Node], spec_binds: bool) -> Option<BindingFormResult> {
    let key = name.to_ascii_uppercase();
    static FORMS_MAP: OnceLock<HashMap<&'static str, &'static [builtins::Form]>> = OnceLock::new();
    let map = FORMS_MAP.get_or_init(|| {
        let mut m = HashMap::with_capacity(builtins::BINDING_FORMS.len());
        for (k, v) in builtins::BINDING_FORMS {
            m.insert(*k, *v);
        }
        m
    });

    if let Some(forms) = map.get(key.as_str()) {
        for f in *forms {
            if f.count != args.len() {
                continue;
            }
            if let Some(arg_idx) = f.when_arg {
                if arg_idx >= args.len() {
                    continue;
                }
                let a = &args[arg_idx];
                let matches = match f.when_kind {
                    builtins::WhenKind::Name => a.t == crate::ast::NodeType::Var && !a.grouped,
                    builtins::WhenKind::Text => a.t == crate::ast::NodeType::Text,
                    builtins::WhenKind::None => true,
                };
                if !matches {
                    continue;
                }
            }
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
        return None;
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
