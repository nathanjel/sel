use crate::args::Args;
use crate::dec::{
    dec_abs, dec_ceil, dec_cmp, dec_floor, dec_power, dec_round, dec_sign, dec_trim_scale,
    dec_trunc, Dec,
};
use crate::math_plan::{check_sized_int, MAX_POWER, MAX_SCALE};
use crate::utf8::SelError;
use crate::value::Value;

pub fn fn_abs(args: &mut Args) -> Result<Value, SelError> {
    let d = args.dec(0)?;
    Ok(Value::num_trusted(dec_abs(&d)))
}

pub fn fn_sign(args: &mut Args) -> Result<Value, SelError> {
    let d = args.dec(0)?;
    Ok(Value::int(dec_sign(&d)))
}

pub fn fn_ceil(args: &mut Args) -> Result<Value, SelError> {
    let d = args.dec(0)?;
    Ok(Value::num_trusted(dec_ceil(&d, args.pos())?))
}

pub fn fn_floor(args: &mut Args) -> Result<Value, SelError> {
    let d = args.dec(0)?;
    Ok(Value::num_trusted(dec_floor(&d, args.pos())?))
}

pub fn fn_trunc(args: &mut Args) -> Result<Value, SelError> {
    let d = args.dec(0)?;
    Ok(Value::num_trusted(dec_trunc(&d)))
}

pub fn fn_canon(args: &mut Args) -> Result<Value, SelError> {
    let d = args.dec(0)?;
    Ok(Value::num_trusted(dec_trim_scale(&d)))
}

pub fn fn_round(args: &mut Args) -> Result<Value, SelError> {
    let d = args.dec(0)?;
    let scale_dec = args.dec(1)?;
    let scale = check_sized_int(
        &scale_dec,
        "ROUND",
        2,
        MAX_SCALE,
        "ROUND scale",
        args.pos_at(1),
    )?;
    Ok(Value::num_trusted(dec_round(&d, scale, args.pos())?))
}

pub fn fn_power(args: &mut Args) -> Result<Value, SelError> {
    let d = args.dec(0)?;
    let exp_dec = args.dec(1)?;
    let exp = check_sized_int(
        &exp_dec,
        "POWER",
        2,
        MAX_POWER,
        "POWER exponent",
        args.pos_at(1),
    )?;
    Ok(Value::num_trusted(dec_power(&d, exp, args.pos())?))
}

pub fn fn_min(args: &mut Args) -> Result<Value, SelError> {
    let mut best: Dec = args.dec(0)?;
    for i in 1..args.count() {
        let d = args.dec(i)?;
        if dec_cmp(&d, &best).is_lt() {
            best = d;
        }
    }
    Ok(Value::num_trusted(best))
}

pub fn fn_max(args: &mut Args) -> Result<Value, SelError> {
    let mut best: Dec = args.dec(0)?;
    for i in 1..args.count() {
        let d = args.dec(i)?;
        if dec_cmp(&d, &best).is_gt() {
            best = d;
        }
    }
    Ok(Value::num_trusted(best))
}

pub fn fn_isnum(args: &mut Args) -> Result<Value, SelError> {
    Ok(Value::bool(args.val(0)?.looks_numeric()))
}
