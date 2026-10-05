//! SPEC 7.8 position-automaton ambiguity analysis. The AST is deliberately
//! unsimplified: duplicate alternatives are significant to this analysis.
use crate::limits::{
    REGEX_AMBIGUITY_BUDGET, REGEX_ANALYSIS_EDGES, REGEX_ANALYSIS_PAIR_WORK,
    REGEX_ANALYSIS_POSITIONS, REGEX_ANALYSIS_RANGES,
};
use regex_syntax::ast::{
    self, Ast, ClassSet, ClassSetItem as C, RepetitionKind as R, RepetitionRange as RR,
};
use std::collections::{HashMap, HashSet};
type Ranges = Vec<(u32, u32)>;
type Result<T> = std::result::Result<T, &'static str>;
const SAT: u64 = 1 << 40;
#[derive(Clone)]
enum Kind {
    Eps,
    Letter(Ranges),
    Cat(Vec<Tree>),
    Alt(Vec<Tree>),
    Repeat(Box<Tree>, u32, Option<u32>),
    Optional(Box<Tree>),
}
#[derive(Clone)]
struct Tree {
    kind: Kind,
    nullable: bool,
    min: u64,
    max: u64,
}
impl Tree {
    fn new(kind: Kind) -> Self {
        let (nullable, min, max) = match &kind {
            Kind::Eps => (true, 0, 0),
            Kind::Letter(_) => (false, 1, 1),
            Kind::Cat(xs) => (
                xs.iter().all(|x| x.nullable),
                xs.iter().map(|x| x.min).sum::<u64>().min(SAT),
                xs.iter().map(|x| x.max).sum::<u64>().min(SAT),
            ),
            Kind::Alt(xs) => (
                xs.iter().any(|x| x.nullable),
                xs.iter().map(|x| x.min).min().unwrap_or(0),
                xs.iter().map(|x| x.max).max().unwrap_or(0),
            ),
            Kind::Repeat(x, lo, hi) => (
                *lo == 0 || x.nullable,
                (x.min * *lo as u64).min(SAT),
                if *hi == Some(0) || x.max == 0 {
                    0
                } else {
                    hi.map_or(SAT, |h| (x.max * h as u64).min(SAT))
                },
            ),
            Kind::Optional(x) => (true, 0, x.max),
        };
        Self {
            kind,
            nullable,
            min,
            max,
        }
    }
}
fn norm(mut rs: Ranges) -> Ranges {
    rs.sort_unstable();
    let mut out: Ranges = vec![];
    for (a, b) in rs {
        if let Some(last) = out.last_mut() {
            if a <= last.1 + 1 {
                last.1 = last.1.max(b);
                continue;
            }
        }
        out.push((a, b));
    }
    out
}
fn fold(rs: Ranges, icase: bool) -> Ranges {
    if !icase {
        return norm(rs);
    }
    let mut out = rs.clone();
    for (a, b) in rs {
        for (lo, hi, delta) in [(65, 90, 32i32), (97, 122, -32)] {
            let l = a.max(lo);
            let h = b.min(hi);
            if l <= h {
                out.push(((l as i32 + delta) as u32, (h as i32 + delta) as u32));
            }
        }
    }
    if out.iter().any(|&(a, b)| a <= 107 && 107 <= b) {
        out.push((0x212a, 0x212a));
    }
    if out.iter().any(|&(a, b)| a <= 115 && 115 <= b) {
        out.push((0x17f, 0x17f));
    }
    norm(out)
}
fn negate(rs: Ranges) -> Ranges {
    let mut out = vec![];
    let mut next = 0;
    for (a, b) in norm(rs) {
        if a > next {
            out.push((next, a - 1));
        }
        next = b + 1;
    }
    if next <= 0x10ffff {
        out.push((next, 0x10ffff));
    }
    out
}
fn class(c: &ast::ClassBracketed, icase: bool) -> Result<Ranges> {
    fn item(c: &C) -> Result<Ranges> {
        Ok(match c {
            C::Empty(_) => vec![],
            C::Literal(l) => vec![(l.c as u32, l.c as u32)],
            C::Range(r) => vec![(r.start.c as u32, r.end.c as u32)],
            C::Union(u) => {
                let mut out = vec![];
                for i in &u.items {
                    out.extend(item(i)?)
                }
                out
            }
            _ => return Err("unexpected class in portable ambiguity tree"),
        })
    }
    let rs = match &c.kind {
        ClassSet::Item(i) => fold(item(i)?, icase),
        _ => return Err("unexpected class operation"),
    };
    Ok(if c.negated { negate(rs) } else { rs })
}
fn tree(a: &Ast, icase: bool) -> Result<Tree> {
    Ok(Tree::new(match a {
        Ast::Empty(_) | Ast::Assertion(_) => Kind::Eps,
        Ast::Literal(l) => Kind::Letter(fold(vec![(l.c as u32, l.c as u32)], icase)),
        Ast::Dot(_) => Kind::Letter(vec![(0, 0x10ffff)]),
        Ast::ClassBracketed(c) => Kind::Letter(class(c, icase)?),
        Ast::Group(g) => return tree(&g.ast, icase),
        Ast::Concat(c) => Kind::Cat(
            c.asts
                .iter()
                .map(|a| tree(a, icase))
                .collect::<Result<_>>()?,
        ),
        Ast::Alternation(c) => Kind::Alt(
            c.asts
                .iter()
                .map(|a| tree(a, icase))
                .collect::<Result<_>>()?,
        ),
        Ast::Repetition(r) => {
            let (lo, hi) = match r.op.kind {
                R::ZeroOrOne => (0, Some(1)),
                R::ZeroOrMore => (0, None),
                R::OneOrMore => (1, None),
                R::Range(RR::Exactly(n)) => (n, Some(n)),
                R::Range(RR::AtLeast(n)) => (n, None),
                R::Range(RR::Bounded(l, h)) => (l, Some(h)),
            };
            Kind::Repeat(Box::new(tree(&r.ast, icase)?), lo, hi)
        }
        _ => return Err("unexpected node in portable ambiguity tree"),
    }))
}
fn log2(n: usize) -> usize {
    usize::BITS as usize - (n - 1).leading_zeros() as usize
}
#[derive(Default)]
struct Analysis {
    classes: Vec<Ranges>,
    succ: Vec<Vec<usize>>,
    tags: HashMap<(usize, usize), bool>,
    edges: usize,
    ranges: usize,
    budget: usize,
}
type Facts = (bool, Vec<usize>, Vec<usize>);
impl Analysis {
    fn eps(&mut self, n: usize, in_loop: bool) -> Result<()> {
        if n > 0 {
            if in_loop {
                return Err("nullable choice inside a loop");
            }
            self.budget += n;
            if self.budget > REGEX_AMBIGUITY_BUDGET {
                return Err("ambiguity budget");
            }
        }
        Ok(())
    }
    fn join(&mut self, l: &[usize], f: &[usize], fixed: bool) -> Result<()> {
        self.edges += l.len() * f.len();
        self.ranges += l.len() * f.iter().map(|&q| self.classes[q].len()).sum::<usize>();
        if self.edges > REGEX_ANALYSIS_EDGES || self.ranges > REGEX_ANALYSIS_RANGES {
            return Err("analysis edge cap");
        }
        for &p in l {
            for &q in f {
                if let Some(old) = self.tags.get(&(p, q)) {
                    if *old && fixed {
                        continue;
                    }
                    return Err("follow edge generated twice");
                }
                self.tags.insert((p, q), fixed);
                self.succ[p].push(q);
            }
        }
        Ok(())
    }
    fn walk(&mut self, t: &Tree, in_loop: bool) -> Result<Facts> {
        match &t.kind {
            Kind::Eps => Ok((true, vec![], vec![])),
            Kind::Letter(c) => {
                if self.classes.len() >= REGEX_ANALYSIS_POSITIONS {
                    return Err("position cap");
                }
                let p = self.classes.len();
                self.classes.push(c.clone());
                self.succ.push(vec![]);
                Ok((false, vec![p], vec![p]))
            }
            Kind::Optional(x) => {
                let (_, f, l) = self.walk(x, in_loop)?;
                Ok((true, f, l))
            }
            Kind::Alt(xs) => {
                let n = xs.iter().filter(|x| x.nullable).count();
                if n >= 2 {
                    self.eps(log2(n), in_loop)?
                }
                let (mut f, mut l) = (vec![], vec![]);
                for x in xs {
                    let (_, ff, ll) = self.walk(x, in_loop)?;
                    f.extend(ff);
                    l.extend(ll)
                }
                Ok((n > 0, f, l))
            }
            Kind::Cat(xs) => {
                let (mut nullable, mut f, mut l) = (true, vec![], vec![]);
                for x in xs {
                    let (n, ff, ll) = self.walk(x, in_loop)?;
                    self.join(&l, &ff, false)?;
                    if nullable {
                        f.extend(ff)
                    }
                    if n {
                        l.extend(ll)
                    } else {
                        l = ll
                    }
                    nullable &= n;
                }
                Ok((nullable, f, l))
            }
            Kind::Repeat(x, lo, hi) => {
                if *hi == Some(0) {
                    return Ok((true, vec![], vec![]));
                }
                if let Some(h) = hi.filter(|h| *h <= 8) {
                    if *lo == 0 && h == 1 && x.nullable {
                        self.eps(1, in_loop)?
                    }
                    let mut tail = Tree::new(Kind::Eps);
                    for _ in 0..h - *lo {
                        tail = Tree::new(Kind::Optional(Box::new(Tree::new(Kind::Cat(vec![
                            (**x).clone(),
                            tail,
                        ])))));
                    }
                    let mut xs = vec![(**x).clone(); *lo as usize];
                    xs.push(tail);
                    return self.walk(&Tree::new(Kind::Cat(xs)), in_loop);
                }
                let (_, f, l) = self.walk(x, true)?;
                self.join(&l, &f, x.min == x.max && x.min > 0 && x.max < SAT)?;
                Ok((*lo == 0 || x.nullable, f, l))
            }
        }
    }
}
fn intersects(a: &Ranges, b: &Ranges) -> bool {
    let (mut i, mut j) = (0, 0);
    while i < a.len() && j < b.len() {
        if a[i].1 < b[j].0 {
            i += 1
        } else if b[j].1 < a[i].0 {
            j += 1
        } else {
            return true;
        }
    }
    false
}
// Iterative Kosaraju, avoiding the host stack for long chains of positions.
fn components(succ: &[Vec<usize>]) -> (Vec<usize>, Vec<bool>) {
    let n = succ.len();
    let mut seen = vec![false; n];
    let mut order = vec![];
    for root in 0..n {
        if seen[root] {
            continue;
        }
        seen[root] = true;
        let mut todo = vec![(root, 0)];
        while let Some((v, i)) = todo.last_mut() {
            if *i < succ[*v].len() {
                let q = succ[*v][*i];
                *i += 1;
                if !seen[q] {
                    seen[q] = true;
                    todo.push((q, 0));
                }
            } else {
                order.push(*v);
                todo.pop();
            }
        }
    }
    let mut rev = vec![vec![]; n];
    for (p, qs) in succ.iter().enumerate() {
        for &q in qs {
            rev[q].push(p)
        }
    }
    let mut comp = vec![usize::MAX; n];
    let mut sizes = vec![];
    for &root in order.iter().rev() {
        if comp[root] != usize::MAX {
            continue;
        }
        let id = sizes.len();
        let mut size = 0;
        let mut todo = vec![root];
        comp[root] = id;
        while let Some(p) = todo.pop() {
            size += 1;
            for &q in &rev[p] {
                if comp[q] == usize::MAX {
                    comp[q] = id;
                    todo.push(q);
                }
            }
        }
        sizes.push(size);
    }
    let cyc = (0..n)
        .map(|p| sizes[comp[p]] > 1 || succ[p].contains(&p))
        .collect();
    (comp, cyc)
}
pub fn analyse(pattern: &str, icase: bool) -> Result<()> {
    let ast = ast::parse::ParserBuilder::new()
        .nest_limit(1000)
        .build()
        .parse(pattern)
        .map_err(|_| "ambiguity tree parse failed")?;
    let t = tree(&ast, icase)?;
    let mut a = Analysis::default();
    a.classes.push(vec![]);
    a.succ.push(vec![]);
    let (_, first, _) = a.walk(&t, false)?;
    a.succ[0] = first;
    let (comp, cyc) = components(&a.succ);
    for p in 0..a.succ.len() {
        if !cyc[p] && a.succ[p].len() >= 2 {
            let mut events = vec![];
            for &q in &a.succ[p] {
                for &(l, h) in &a.classes[q] {
                    events.push((l, 1i32));
                    events.push((h + 1, -1));
                }
            }
            events.sort_unstable();
            let (mut cur, mut best) = (0, 0);
            for (_, d) in events {
                cur += d;
                best = best.max(cur)
            }
            if best >= 2 {
                a.eps(log2(best as usize), false)?
            }
        }
    }
    let inside: Vec<Vec<usize>> = a
        .succ
        .iter()
        .enumerate()
        .map(|(p, qs)| qs.iter().copied().filter(|&q| comp[p] == comp[q]).collect())
        .collect();
    let mut reached = HashSet::new();
    let mut todo = vec![];
    for p in 0..a.succ.len() {
        if cyc[p] && !a.classes[p].is_empty() {
            reached.insert((p, p));
            todo.push((p, p));
        }
    }
    let mut reverse: HashMap<(usize, usize), Vec<(usize, usize)>> = HashMap::new();
    let mut work = 0;
    while let Some((p, r)) = todo.pop() {
        work += inside[p].len() * inside[r].len();
        if work > REGEX_ANALYSIS_PAIR_WORK {
            return Err("pair-graph cap");
        }
        for &pp in &inside[p] {
            for &rr in &inside[r] {
                if intersects(&a.classes[pp], &a.classes[rr]) {
                    reverse.entry((pp, rr)).or_default().push((p, r));
                    if reached.insert((pp, rr)) {
                        todo.push((pp, rr));
                    }
                }
            }
        }
    }
    let mut back: HashSet<_> = reached.iter().copied().filter(|(p, r)| p == r).collect();
    let mut todo: Vec<_> = back.iter().copied().collect();
    while let Some(v) = todo.pop() {
        if let Some(prev) = reverse.get(&v) {
            for &u in prev {
                if u.0 != u.1 {
                    return Err("exponential ambiguity (EDA)");
                }
                if back.insert(u) {
                    todo.push(u);
                }
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::analyse;

    #[test]
    fn ambiguity_is_not_the_same_as_nested_or_overlapping_repetition() {
        for pattern in ["(?:foo|foobar)*", "^(?:ab|a)*$", "a*a*$", "(a{300}){300}"] {
            assert!(analyse(pattern, false).is_ok(), "{pattern}");
        }
        for pattern in ["(a+)+", "(a|a)*", "(?:a{300}b?){300}"] {
            assert!(analyse(pattern, false).is_err(), "{pattern}");
        }
        assert!(analyse("(?:k|K)+", false).is_ok());
        assert!(analyse("(?:k|K)+", true).is_err());
    }

    #[test]
    fn literal_ambiguity_is_checked_in_dead_branches_with_its_flags() {
        let error = crate::compile("IF(FALSE, RMATCH('(?:k|K)+', \"k\", \"i\"), TRUE)").unwrap_err();
        assert_eq!(error.code, "E_REGEX_SYNTAX");
        assert!(crate::compile("IF(FALSE, RMATCH('(?:k|K)+', \"k\"), TRUE)").is_ok());
    }
}
