//! `SelStr` must be indistinguishable from the `String` it replaced wherever
//! text is compared, ordered, hashed or read back.
use sel_lang::text::SelStr;
use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};

fn hash_of<T: Hash + ?Sized>(t: &T) -> u64 {
    let mut h = DefaultHasher::new();
    t.hash(&mut h);
    h.finish()
}

fn check(s: &str) {
    let t = SelStr::new(s);
    assert_eq!(t.as_str(), s);
    assert_eq!(t.len(), s.len());
    assert_eq!(t.chars().count(), s.chars().count());
    assert_eq!(hash_of(&t), hash_of(s), "hash of {s:?}");
    assert_eq!(t.clone().as_str(), s);
    assert_eq!(SelStr::from(s.to_string()), t);
    assert_eq!(format!("{t:?}"), format!("{s:?}"));
    assert_eq!(t.is_inline(), s.len() <= 22, "{s:?}");
}

#[test]
fn sizes_and_boundaries() {
    assert_eq!(std::mem::size_of::<SelStr>(), 24);
    for s in ["", "a", "COMPLETED", &"x".repeat(21), &"x".repeat(22), &"x".repeat(23), &"y".repeat(200)] {
        check(s);
    }
    // Multi-byte text around the inline limit: whole strings only, never cut.
    check(&"é".repeat(11)); // 22 bytes, inline
    check(&format!("{}a", "é".repeat(11))); // 23 bytes, shared
    check(&format!("{}ab", "😀".repeat(5))); // 22 bytes, inline
    check(&"😀".repeat(6)); // 24 bytes, shared
    check("\u{0}nul\u{0}");
}

#[test]
fn ordering_matches_str() {
    let words = ["", "a", "b", "ab", "é", "e", "😀", &"z".repeat(30), &"z".repeat(22), "Z"];
    for a in words {
        for b in words {
            assert_eq!(SelStr::new(a).cmp(&SelStr::new(b)), a.cmp(b), "{a:?} vs {b:?}");
            assert_eq!(SelStr::new(a) == SelStr::new(b), a == b);
        }
    }
}

#[test]
fn shared_clones_do_not_copy() {
    let long = SelStr::new(&"q".repeat(64));
    let copy = long.clone();
    assert_eq!(long.as_str().as_ptr(), copy.as_str().as_ptr());
}

#[test]
fn random_utf8_round_trips() {
    // A small deterministic generator over ASCII, 2-, 3- and 4-byte characters.
    let alphabet: Vec<char> = "abc XYZ09_é漢ß😀\u{7f}\u{80}\u{7ff}\u{800}\u{ffff}\u{10000}".chars().collect();
    let mut seed: u64 = 0x5e1_2026;
    for _ in 0..10_000 {
        seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
        let len = (seed >> 33) as usize % 40;
        let mut s = String::new();
        for _ in 0..len {
            seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
            s.push(alphabet[(seed >> 33) as usize % alphabet.len()]);
        }
        check(&s);
    }
}
