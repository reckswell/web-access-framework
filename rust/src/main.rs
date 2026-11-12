//! Command line: webaccess run <config.toml> [--input FILE] [--output FILE] (spec/08-cli.md).
use std::process::ExitCode;
use webaccess::{config::Config, engine::Engine, goals::make_goal, sinks};

const USAGE: &str = "usage: webaccess run <config.toml> [--input FILE] [--output FILE]";

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let flag = |name: &str| args.iter().position(|a| a == name).and_then(|i| args.get(i + 1)).map(String::as_str);
    let (Some("run"), Some(config)) = (args.first().map(String::as_str), args.get(1)) else {
        eprintln!("{USAGE}");
        return ExitCode::from(2);
    };
    let setup = || -> Result<_, String> {
        let cfg = Config::load(config)?;
        let goal = make_goal(&cfg, flag("--input"))?;
        let sinks = match flag("--output") {
            Some(path) => vec![Box::new(sinks::JsonlSink::new(Some(path)).map_err(|e| format!("{path}: {e}"))?) as _],
            None => sinks::make_sinks(&cfg.sinks).map_err(|e| e.to_string())?,
        };
        Ok((cfg, goal, sinks))
    };
    let (cfg, goal, sinks) = match setup() {
        Ok(v) => v,
        Err(e) => {
            eprintln!("error: {e}");
            return ExitCode::from(2);
        }
    };
    let threads = cfg.resources.resolve(None).threads;
    let runtime = tokio::runtime::Builder::new_multi_thread().worker_threads(threads).enable_all().build()
        .expect("tokio runtime");
    let engine = match Engine::new(cfg, goal, sinks) {
        Ok(e) => e,
        Err(e) => {
            eprintln!("error: {e}");
            return ExitCode::from(2);
        }
    };
    let (_, interrupted) = runtime.block_on(engine.run());
    if interrupted { ExitCode::from(130) } else { ExitCode::SUCCESS }
}
