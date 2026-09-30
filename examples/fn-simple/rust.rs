// Goes in the matching rust/src/builtins/*.rs, and its define() line in
// registry() in rust/src/builtins/mod.rs. Not a runnable file: this fragment
// compiles only in place.
// EXAMPLE-BEGIN
pub fn fn_ord_suffix(args: &mut Args) -> Result<Value, SelError> {
    let n = args.non_neg_int(0)?;
    let suffix = match (n % 100, n % 10) {
        (11..=13, _) => "th",
        (_, 1) => "st",
        (_, 2) => "nd",
        (_, 3) => "rd",
        _ => "th",
    };
    Ok(Value::text_owned(format!("{n}{suffix}")))
}

// in registry():
define(&mut m, "ORD_SUFFIX", 1, Some(1), false, false, text::fn_ord_suffix);
// EXAMPLE-END
