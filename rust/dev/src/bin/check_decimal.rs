use std::env;
use std::fs::File;
use std::io::{BufRead, BufReader};
use std::process;

use sel_lang::dec::{
    dec_add, dec_ceil, dec_cmp, dec_div, dec_floor, dec_format, dec_mod, dec_mul, dec_parse,
    dec_round, dec_sub, dec_trunc,
};
use sel_lang::utf8::Pos;

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() < 2 {
        eprintln!("usage: check-decimal <oracle.txt>");
        process::exit(2);
    }

    let file = match File::open(&args[1]) {
        Ok(f) => f,
        Err(e) => {
            eprintln!("cannot read {}: {}", args[1], e);
            process::exit(2);
        }
    };

    let reader = BufReader::new(file);
    let mut failures = Vec::new();
    let mut mismatches = 0;
    let mut total_cases = 0;
    let dummy_pos = Pos::new(1, 1, 0);

    for line_res in reader.lines() {
        let line = match line_res {
            Ok(l) => l,
            Err(e) => {
                eprintln!("error reading file: {}", e);
                process::exit(2);
            }
        };
        if line.is_empty() {
            continue;
        }
        let parts: Vec<&str> = line.split('|').collect();
        if parts.len() != 4 {
            continue;
        }
        total_cases += 1;
        let (op, a_str, b_str, want) = (parts[0], parts[1], parts[2], parts[3]);

        let a = match dec_parse(a_str, dummy_pos) {
            Ok(d) => d,
            Err(e) => {
                let got = format!("THREW {}", e.code);
                if got != want {
                    mismatches += 1;
                    if failures.len() < 20 {
                        failures.push(format!("parse({}) => {}, want {}", a_str, got, want));
                    }
                }
                continue;
            }
        };

        let b = dec_parse(b_str, dummy_pos).ok();

        let got = match op {
            "+" => match dec_add(&a, b.as_ref().unwrap(), dummy_pos) {
                Ok(res) => dec_format(&res),
                Err(e) => format!("THREW {}", e.code),
            },
            "-" => match dec_sub(&a, b.as_ref().unwrap(), dummy_pos) {
                Ok(res) => dec_format(&res),
                Err(e) => format!("THREW {}", e.code),
            },
            "*" => match dec_mul(&a, b.as_ref().unwrap(), dummy_pos) {
                Ok(res) => dec_format(&res),
                Err(e) => format!("THREW {}", e.code),
            },
            "/" => match dec_div(&a, b.as_ref().unwrap(), dummy_pos) {
                Ok(res) => dec_format(&res),
                Err(e) => format!("THREW {}", e.code),
            },
            "%" => match dec_mod(&a, b.as_ref().unwrap(), dummy_pos) {
                Ok(res) => dec_format(&res),
                Err(e) => format!("THREW {}", e.code),
            },
            "cmp" => {
                let ord = dec_cmp(&a, b.as_ref().unwrap());
                match ord {
                    std::cmp::Ordering::Less => "-1".to_string(),
                    std::cmp::Ordering::Equal => "0".to_string(),
                    std::cmp::Ordering::Greater => "1".to_string(),
                }
            }
            "round" => {
                let places: usize = b_str.parse().unwrap_or(0);
                match dec_round(&a, places, dummy_pos) {
                    Ok(res) => dec_format(&res),
                    Err(e) => format!("THREW {}", e.code),
                }
            }
            "floor" => dec_floor(&a, dummy_pos)
                .map(|d| dec_format(&d))
                .unwrap_or_else(|e| format!("THREW {}", e.code)),
            "ceil" => dec_ceil(&a, dummy_pos)
                .map(|d| dec_format(&d))
                .unwrap_or_else(|e| format!("THREW {}", e.code)),
            "trunc" => dec_format(&dec_trunc(&a)),
            _ => panic!("unknown op {}", op),
        };

        if got != want {
            mismatches += 1;
            if failures.len() < 20 {
                failures.push(format!(
                    "{} {} {} => {}, oracle says {}",
                    a_str, op, b_str, got, want
                ));
            }
        }
    }

    println!("rust: {} cases, {} mismatches", total_cases, mismatches);
    for f in &failures {
        println!("  {}", f);
    }
    if mismatches > failures.len() {
        println!("  ... and {} more", mismatches - failures.len());
    }

    if total_cases == 0 {
        eprintln!("no cases were run: {} holds none", args[1]);
        process::exit(1);
    }
    if mismatches > 0 {
        process::exit(1);
    }
}
