use std::io::{self, BufRead};
use sel_lang::{regex::compile_sel_regex, Pos};

fn main() {
    let flags = std::env::args().nth(1).unwrap_or_default();
    for line in io::stdin().lock().lines() {
        let pattern = line.expect("read pattern");
        match compile_sel_regex(&pattern, &flags, Pos::default(), Pos::default()) {
            Ok(_) => println!("A"),
            Err(e) if e.code == "E_REGEX_SYNTAX" || e.code == "E_BAD_ARG" => println!("R"),
            Err(e) => panic!("unexpected validation error: {e:?}"),
        }
    }
}
