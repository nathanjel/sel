//! The sizes the performance work relies on (docs/interim/2026-09-29/
//! rust-performance-plan.md). A value cell is allocated for every scalar a
//! program touches; `Args` sits on the evaluator's recursive path.
use std::mem::size_of;

#[test]
fn value_cells_stay_small() {
    // 192 B before the plan's phase 3 (rarely used fields now sit behind one
    // box), 128 B before 3b (the decimal cache packed to 8-byte alignment).
    assert!(size_of::<sel_lang::value::ValueInner>() <= 104, "{}", size_of::<sel_lang::value::ValueInner>());
    assert_eq!(std::mem::align_of::<sel_lang::value::ValueInner>(), 8);
    assert_eq!(size_of::<Option<sel_lang::value::CellDec>>(), 32);
    assert_eq!(size_of::<sel_lang::text::SelStr>(), 24);
    assert_eq!(size_of::<sel_lang::Value>(), 8);
}

#[test]
fn packed_decimals_round_trip() {
    use sel_lang::dec::{dec_format, dec_parse};
    use sel_lang::value::CellDec;
    let pos = sel_lang::Pos::default();
    for text in [
        "0", "0.00", "-0.5", "1", "-1", "12.50", "548051.69",
        "170141183460469231731687303715884105727",   // i128::MAX's digits
        "-170141183460469231731687303715884105727",
        "170141183460469231731687303715884105728",   // one past: large repr
        "123456789012345678901234567890123456789012345.678901",
        "0.000000000000000000000000000000000000001",
    ] {
        let d = dec_parse(text, pos).unwrap();
        let back = CellDec::pack(d.clone()).unpack();
        assert_eq!(back, d, "{text}");
        assert_eq!(dec_format(&back), dec_format(&d));
    }
}

#[test]
fn argument_frames_stay_small() {
    // 104 B before the inline argument cache (phase 1).
    assert!(size_of::<sel_lang::Args>() <= 160, "{}", size_of::<sel_lang::Args>());
}
