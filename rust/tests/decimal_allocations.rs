//! The small decimal path must not allocate mantissas or temporary strings.
use sel_lang::{dec::*, Pos};
use std::alloc::{GlobalAlloc, Layout, System};
use std::cell::Cell;

struct TrackingAllocator;
thread_local! {
    static TRACKING: Cell<bool> = const { Cell::new(false) };
    static MAX_ALLOCATION: Cell<usize> = const { Cell::new(0) };
    static ALLOCATIONS: Cell<usize> = const { Cell::new(0) };
}
fn record(size: usize) {
    if TRACKING.try_with(Cell::get).unwrap_or(false) {
        let _ = MAX_ALLOCATION.try_with(|n| n.set(n.get().max(size)));
        let _ = ALLOCATIONS.try_with(|n| n.set(n.get() + 1));
    }
}
unsafe impl GlobalAlloc for TrackingAllocator {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        record(layout.size());
        System.alloc(layout)
    }
    unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
        record(layout.size());
        System.alloc_zeroed(layout)
    }
    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, size: usize) -> *mut u8 {
        record(size);
        System.realloc(ptr, layout, size)
    }
    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        System.dealloc(ptr, layout)
    }
}
#[global_allocator]
static ALLOCATOR: TrackingAllocator = TrackingAllocator;

#[test]
fn small_parsing_and_arithmetic_do_not_allocate() {
    let pos = Pos::default();
    ALLOCATIONS.with(|n| n.set(0));
    TRACKING.with(|flag| flag.set(true));
    let a = dec_parse("12345.67", pos).unwrap();
    let b = dec_parse("3.00", pos).unwrap();
    let large_small = dec_parse("170141183460469231731687303715884105727", pos).unwrap();
    let ordering = dec_cmp(&a, &b);
    let results = [
        dec_add(&a, &b, pos).unwrap(),
        dec_sub(&a, &b, pos).unwrap(),
        dec_mul(&a, &b, pos).unwrap(),
        dec_div(&a, &b, pos).unwrap(),
        dec_mod(&a, &b, pos).unwrap(),
        dec_round(&a, 1, pos).unwrap(),
        dec_round(&a, 4, pos).unwrap(),
        dec_floor(&a, pos).unwrap(),
        dec_ceil(&a, pos).unwrap(),
        dec_trunc(&a),
        dec_trim_scale(&b),
    ];
    TRACKING.with(|flag| flag.set(false));
    assert_eq!(ALLOCATIONS.with(Cell::get), 0);
    assert_eq!(ordering, std::cmp::Ordering::Greater);
    assert_eq!(large_small.repr, DecRepr::Small(i128::MAX));
    let expected = [
        "12348.67",
        "12342.67",
        "37037.0100",
        "4115.2233333333",
        "0.67",
        "12345.7",
        "12345.6700",
        "12345",
        "12346",
        "12345",
        "3",
    ];
    for (result, expected) in results.iter().zip(expected) {
        assert!(matches!(result.repr, DecRepr::Small(_)));
        assert_eq!(dec_format(result), expected);
    }
}

#[test]
fn oversized_products_are_refused_before_allocating_operand_sized_storage() {
    use sel_lang::limits::{MAX_FRAC_DIGITS, MAX_INT_DIGITS};
    let pos = Pos::new(7, 9, 23);
    let integer = dec_parse(&"9".repeat(MAX_INT_DIGITS), pos).unwrap();
    let mut fraction = integer.clone();
    fraction.scale = MAX_FRAC_DIGITS as u32;
    MAX_ALLOCATION.with(|n| n.set(0));
    TRACKING.with(|flag| flag.set(true));
    let integer_error = dec_mul(&integer, &integer, pos).unwrap_err();
    let fraction_error = dec_mul(&fraction, &fraction, pos).unwrap_err();
    let zero_error = dec_mul(
        &Dec::from_small(false, 0, MAX_FRAC_DIGITS as u32),
        &Dec::from_small(false, 0, 1),
        pos,
    )
    .unwrap_err();
    TRACKING.with(|flag| flag.set(false));
    // Only short error messages may allocate; neither operands nor results.
    assert!(MAX_ALLOCATION.with(Cell::get) < 1024);
    for error in [integer_error, fraction_error, zero_error] {
        assert_eq!(error.code, "E_RANGE");
        assert_eq!(error.pos, pos);
    }
    assert!(dec_mul(&integer, &Dec::zero(), pos).unwrap().is_zero());
    // This carry is not provable from the lower digit bound; final guard wins.
    assert_eq!(
        dec_mul(&integer, &Dec::from_i64(9), pos).unwrap_err().code,
        "E_RANGE"
    );
    let boundary = Dec::from_large(false, LargeDec::pow10(MAX_INT_DIGITS - 2), 0);
    let result = dec_mul(&boundary, &Dec::from_i64(10), pos).unwrap();
    assert_eq!(dec_format(&result).len(), MAX_INT_DIGITS);
}

#[test]
fn tiny_small_mantissas_do_not_allocate_scale_sized_divisors() {
    let pos = Pos::default();
    for scale in [39, 40, sel_lang::limits::MAX_FRAC_DIGITS as u32] {
        for magnitude in [0, 1, i128::MAX] {
            for negative in [false, true] {
                let value = Dec::from_small(negative, magnitude, scale);
                ALLOCATIONS.with(|n| n.set(0));
                TRACKING.with(|flag| flag.set(true));
                let integer = value.is_integer();
                let truncated = dec_trunc(&value);
                let rounded = dec_round(&value, 0, pos).unwrap();
                let floor = dec_floor(&value, pos).unwrap();
                let ceil = dec_ceil(&value, pos).unwrap();
                TRACKING.with(|flag| flag.set(false));
                assert_eq!(ALLOCATIONS.with(Cell::get), 0);
                assert_eq!(integer, magnitude == 0);
                assert!(truncated.is_zero() && rounded.is_zero());
                assert_eq!(
                    floor.to_i64(),
                    Some(if negative && magnitude != 0 { -1 } else { 0 })
                );
                assert_eq!(
                    ceil.to_i64(),
                    Some(if !negative && magnitude != 0 { 1 } else { 0 })
                );
            }
        }
    }
}
