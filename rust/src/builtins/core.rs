use crate::args::Args;
use crate::utf8::SelError;
use crate::value::Value;

pub fn fn_if(args: &mut Args) -> Result<Value, SelError> {
    if args.bool(0)? {
        args.val(1)
    } else if args.count() == 3 {
        args.val(2)
    } else {
        Ok(Value::text_owned(String::new()))
    }
}

pub fn fn_cond(args: &mut Args) -> Result<Value, SelError> {
    let last = args.count() - 1;
    for i in (0..last).step_by(2) {
        if args.bool(i)? {
            return args.val(i + 1);
        }
    }
    args.val(last)
}

pub fn fn_abort(args: &mut Args) -> Result<Value, SelError> {
    let msg = args.text(0)?;
    let pos = args.pos_at(0);
    Err(SelError::new("E_ABORT", msg, pos))
}

pub fn fn_is_null(args: &mut Args) -> Result<Value, SelError> {
    Ok(Value::bool(args.val(0)?.is_null()))
}

pub fn fn_is_not_null(args: &mut Args) -> Result<Value, SelError> {
    Ok(Value::bool(!args.val(0)?.is_null()))
}

pub fn fn_coalesce(args: &mut Args) -> Result<Value, SelError> {
    for i in 0..args.count() {
        let v = args.val(i)?;
        if !v.is_null() {
            return Ok(v);
        }
    }
    Ok(Value::null())
}

pub fn fn_get(args: &mut Args) -> Result<Value, SelError> {
    let target = args.val(0)?;
    let key = args.text(1)?;
    if !target.is_null() && target.has(&key) {
        if let Some(v) = target.get(&key) {
            return Ok(v);
        }
    }
    if args.count() > 2 {
        return args.val(2);
    }
    Ok(Value::null())
}

pub fn fn_path(args: &mut Args) -> Result<Value, SelError> {
    let target = args.val(0)?;
    let path_str = args.text(1)?;
    if path_str.is_empty() {
        return Ok(target);
    }
    let segments: Vec<&str> = path_str.split('.').collect();
    let mut cur = target;
    for seg in segments {
        if cur.is_null() || !cur.has(seg) {
            if args.count() > 2 {
                return args.val(2);
            }
            return Ok(Value::null());
        }
        cur = cur.get(seg).unwrap();
    }
    Ok(cur)
}

pub fn fn_is_blank(args: &mut Args) -> Result<Value, SelError> {
    Ok(Value::bool(args.val(0)?.is_vacuous()))
}

pub fn fn_is_present(args: &mut Args) -> Result<Value, SelError> {
    Ok(Value::bool(!args.val(0)?.is_vacuous()))
}
