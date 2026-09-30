//! The sizes the performance work relies on (docs/interim/2026-09-29/
//! rust-performance-plan.md). A value cell is allocated for every scalar a
//! program touches; `Args` sits on the evaluator's recursive path.
use std::mem::size_of;

#[test]
fn value_cells_stay_small() {
    // 192 B before the plan's phase 3; rarely used fields now sit behind one box.
    assert!(size_of::<sel_lang::value::ValueInner>() <= 128, "{}", size_of::<sel_lang::value::ValueInner>());
    assert_eq!(size_of::<sel_lang::text::SelStr>(), 24);
    assert_eq!(size_of::<sel_lang::Value>(), 8);
}

#[test]
fn argument_frames_stay_small() {
    // 104 B before the inline argument cache (phase 1).
    assert!(size_of::<sel_lang::Args>() <= 160, "{}", size_of::<sel_lang::Args>());
}
