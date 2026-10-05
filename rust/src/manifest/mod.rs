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

impl BindingFormResult {
    /// The scope argument `i` is evaluated in.
    pub fn scope(&self, i: usize) -> builtins::Scope {
        self.scopes.get(i).copied().unwrap_or(builtins::Scope::Outer)
    }
}

/// The scope argument `i` of a call is evaluated in, given the call's binding
/// form: every argument of a call with none is outer.
pub fn arg_scope(form: Option<&BindingFormResult>, i: usize) -> builtins::Scope {
    form.map_or(builtins::Scope::Outer, |f| f.scope(i))
}

/// The binding form of a call node: the manifest's for a builtin, the generic
/// shapes for an application's function registered as binding (its spec,
/// else the registry's). The one classifier every scope-aware walker asks
/// (crate::ast, "Traversal policies").
pub fn call_binding_form(node: &crate::ast::Node) -> Option<BindingFormResult> {
    let spec_binds = match &node.spec {
        Some(spec) => spec.binds,
        None => crate::builtins::lookup_spec(&node.s).is_some_and(|s| s.binds),
    };
    binding_form(&node.s, &node.items, spec_binds)
}

static FORMS_MAP: OnceLock<HashMap<&'static str, &'static [builtins::Form]>> = OnceLock::new();

/// The forms of a builtin named at run time (canonical, upper case).
pub fn forms_of(name: &str) -> Option<&'static [builtins::Form]> {
    let map = FORMS_MAP.get_or_init(|| {
        let mut m = HashMap::with_capacity(builtins::BINDING_FORMS.len());
        for (k, v) in builtins::BINDING_FORMS {
            m.insert(*k, *v);
        }
        m
    });
    map.get(name).copied()
}

/// What the form matcher asks of an argument: the two guards of
/// spec/builtins.md "Binding forms" and a binder's name. The evaluator's AST
/// and the SQL layer's answer the text guard differently: there a text a
/// helper inlined was not written in the call, so it does not select the
/// direction form (`SNode::is_written_text`).
pub trait FormArg {
    /// A bare name -- a variable, not parenthesised: what a binder slot holds.
    fn is_bare_name(&self) -> bool;
    /// A text literal that selects a text-guarded form.
    fn is_form_text(&self) -> bool;
    /// The name a bare name binds.
    fn bare_name(&self) -> &str;
}

impl FormArg for crate::ast::Node {
    fn is_bare_name(&self) -> bool {
        self.t == crate::ast::NodeType::Var && !self.grouped
    }
    fn is_form_text(&self) -> bool {
        self.t == crate::ast::NodeType::Text
    }
    fn bare_name(&self) -> &str {
        &self.s
    }
}

/// The rendered forms of a builtin, looked up at compile time: a builtin that
/// decodes its own call names its forms as a constant
/// (`const FORMS: &[Form] = manifest::forms("MAP")`), so the evaluator pays no
/// lookup per call, and a name the manifest has no forms for fails the build.
pub const fn forms(name: &str) -> &'static [builtins::Form] {
    const fn same(a: &str, b: &str) -> bool {
        let (a, b) = (a.as_bytes(), b.as_bytes());
        if a.len() != b.len() {
            return false;
        }
        let mut i = 0;
        while i < a.len() {
            if a[i] != b[i] {
                return false;
            }
            i += 1;
        }
        true
    }
    let mut i = 0;
    while i < builtins::BINDING_FORMS.len() {
        if same(builtins::BINDING_FORMS[i].0, name) {
            return builtins::BINDING_FORMS[i].1;
        }
        i += 1;
    }
    panic!("the manifest has no binding forms for this builtin")
}

fn guard_holds<A: FormArg>(f: &builtins::Form, args: &[A]) -> bool {
    let Some(arg_idx) = f.when_arg else { return true };
    let Some(a) = args.get(arg_idx) else { return false };
    match f.when_kind {
        builtins::WhenKind::Name => a.is_bare_name(),
        builtins::WhenKind::Text => a.is_form_text(),
        builtins::WhenKind::None => true,
    }
}

/// The form a call with these arguments takes, among `forms` (one builtin's):
/// the forms for its count are tried in the manifest's order, a guarded one
/// only when its guard holds. That order is where "a text literal in the
/// direction's place wins over a bare name in the binder's" (SORT_BY, TOP_BY)
/// is decided -- the guarded text form comes first. No allocation.
pub fn form_in<A: FormArg>(forms: &'static [builtins::Form], args: &[A]) -> Option<&'static builtins::Form> {
    forms.iter().find(|f| f.count == args.len() && guard_holds(f, args))
}

/// `form_in` for a builtin named at run time (canonical, upper case).
pub fn match_form<A: FormArg>(name: &str, args: &[A]) -> Option<&'static builtins::Form> {
    form_in(forms_of(name)?, args)
}

/// Which argument of a call is what, from the form it matches
/// (spec/builtins.md, "Binding forms"): the binders (LINK has two), the first
/// and second arguments that run inside the binder scope (a body, key,
/// predicate or projection), and the outer arguments other than the sources,
/// in order (a sort's direction, a TOP's count).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct ArgRoles {
    pub binder: Option<usize>,
    pub binder2: Option<usize>,
    pub body: Option<usize>,
    pub extra: Option<usize>,
    outer: [usize; 4],
    outer_len: u8,
}

impl ArgRoles {
    pub fn of(form: &builtins::Form) -> ArgRoles {
        use builtins::Role;
        let mut r = ArgRoles::default();
        for (i, role) in form.roles.iter().enumerate() {
            match role {
                Role::Source => {}
                Role::Binder => {
                    if r.binder.is_none() {
                        r.binder = Some(i);
                    } else {
                        r.binder2 = Some(i);
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
        r
    }

    pub fn outer(&self) -> &[usize] {
        &self.outer[..self.outer_len as usize]
    }

    /// The name the call binds its element to: `_` when the form has no
    /// binder slot, else the slot's bare name; `Err(index)` when the slot
    /// holds anything else (SEL raises E_EXPECT_SYMBOL there).
    pub fn binder_name<'a, A: FormArg>(&self, args: &'a [A]) -> Result<&'a str, usize> {
        match self.binder {
            None => Ok("_"),
            Some(i) if args[i].is_bare_name() => Ok(args[i].bare_name()),
            Some(i) => Err(i),
        }
    }
}

pub fn roles_in<A: FormArg>(forms: &'static [builtins::Form], args: &[A]) -> Option<ArgRoles> {
    form_in(forms, args).map(ArgRoles::of)
}

pub fn arg_roles<A: FormArg>(name: &str, args: &[A]) -> Option<ArgRoles> {
    match_form(name, args).map(ArgRoles::of)
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

impl SortRoles {
    pub fn of(form: &builtins::Form) -> SortRoles {
        let r = ArgRoles::of(form);
        let outer = r.outer();
        let (dir, limit) = if form.name.starts_with("TOP") {
            (if outer.len() == 2 { Some(outer[0]) } else { None }, outer.last().copied())
        } else {
            (outer.first().copied(), None)
        };
        SortRoles { binder: r.binder, key: r.body, dir, limit }
    }
}

pub fn sort_roles_in<A: FormArg>(forms: &'static [builtins::Form], args: &[A]) -> Option<SortRoles> {
    form_in(forms, args).map(SortRoles::of)
}

pub fn sort_roles<A: FormArg>(name: &str, args: &[A]) -> Option<SortRoles> {
    match_form(name, args).map(SortRoles::of)
}

/// Whether a text literal at `index` is what selects one of the call's forms
/// at its count -- the direction of SORT_BY/TOP_BY when argument 2 is also a
/// bare name, so the binder form is the alternative. Folding a constant into
/// a text literal there would change the form the call takes.
pub fn text_selects_form<A: FormArg>(name: &str, args: &[A], index: usize) -> bool {
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
        let f = form_in(forms, args)?;
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
