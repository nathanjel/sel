use crate::args::Args;
use crate::regex::{compile_sel_regex, expand_repl, find_matches, fold_subject, CompiledRegex};
use crate::utf8::{cap_text, Pos, SelError};
use crate::value::Value;

fn regex_args(
    args: &mut Args,
    pat_idx: usize,
    subj_idx: usize,
    flag_idx: usize,
) -> Result<(CompiledRegex, Vec<char>, Vec<char>), SelError> {
    let pat = args.text(pat_idx)?;
    let subj = args.text(subj_idx)?;
    let mut flags = String::new();
    let mut flag_pos = args.pos();
    if args.count() > flag_idx {
        flags = args.text(flag_idx)?;
        flag_pos = args.pos_at(flag_idx);
    }
    let cr = compile_sel_regex(&pat, &flags, flag_pos, args.pos_at(pat_idx))?;
    let orig_chars: Vec<char> = subj.chars().collect();
    let search_chars = if cr.ignore_case {
        fold_subject(&orig_chars)
    } else {
        orig_chars.clone()
    };
    Ok((cr, orig_chars, search_chars))
}

pub fn fn_rmatch(args: &mut Args) -> Result<Value, SelError> {
    let (cr, orig, search) = regex_args(args, 0, 1, 2)?;
    let matches = find_matches(&cr, &orig, &search);
    Ok(Value::bool(!matches.is_empty()))
}

pub fn fn_rfind(args: &mut Args) -> Result<Value, SelError> {
    let (cr, orig, search) = regex_args(args, 0, 1, 2)?;
    let matches = find_matches(&cr, &orig, &search);
    if matches.is_empty() {
        return Ok(Value::int(0));
    }
    Ok(Value::int((matches[0].start_cp + 1) as i64))
}

pub fn fn_rgroups(args: &mut Args) -> Result<Value, SelError> {
    let (cr, orig, search) = regex_args(args, 0, 1, 2)?;
    let matches = find_matches(&cr, &orig, &search);
    if matches.is_empty() {
        return Ok(Value::none());
    }
    let first = &matches[0];
    let mut items = Vec::with_capacity(first.groups.len());
    for &(s_opt, e_opt) in &first.groups {
        if let (Some(s), Some(e)) = (s_opt, e_opt) {
            let part: String = orig[s..e].iter().collect();
            items.push(Value::text_owned(part));
        } else {
            items.push(Value::text_owned(String::new()));
        }
    }
    Ok(Value::list_owned(items))
}

pub fn fn_rreplace(args: &mut Args) -> Result<Value, SelError> {
    let pat = args.text(0)?;
    let repl = args.text(1)?;
    let subj = args.text(2)?;
    let mut flags = String::new();
    let mut flag_pos = args.pos();
    if args.count() > 3 {
        flags = args.text(3)?;
        flag_pos = args.pos_at(3);
    }
    let cr = compile_sel_regex(&pat, &flags, flag_pos, args.pos_at(0))?;
    let orig_chars: Vec<char> = subj.chars().collect();
    let search_chars = if cr.ignore_case {
        fold_subject(&orig_chars)
    } else {
        orig_chars.clone()
    };
    let matches = find_matches(&cr, &orig_chars, &search_chars);
    if matches.is_empty() {
        return Ok(Value::text_owned(subj));
    }

    let num_groups = cr.captures_len();

    let mut total_cps: u128 = 0;
    let mut last = 0;
    let mut expanded_pieces = Vec::with_capacity(matches.len());
    for m in &matches {
        total_cps += (m.start_cp - last) as u128;
        let expanded = expand_repl(&repl, m, num_groups, &orig_chars, args.pos_at(1))?;
        total_cps += expanded.chars().count() as u128;
        last = m.end_cp;
        expanded_pieces.push(expanded);
    }
    total_cps += (orig_chars.len() - last) as u128;
    cap_text(total_cps, args.pos())?;

    let mut out = String::new();
    let mut last = 0;
    for (m, exp) in matches.iter().zip(expanded_pieces.into_iter()) {
        let prefix: String = orig_chars[last..m.start_cp].iter().collect();
        out.push_str(&prefix);
        out.push_str(&exp);
        last = m.end_cp;
    }
    let suffix: String = orig_chars[last..].iter().collect();
    out.push_str(&suffix);
    Ok(Value::text_owned(out))
}
