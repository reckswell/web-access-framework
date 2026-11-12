//! Output sinks: jsonl, csv, stdout (spec/02-config.md).
use crate::config::SinkConfig;
use serde_json::{Map, Value};
use std::fs::{create_dir_all, File};
use std::io::{self, BufWriter, Write};
use std::path::Path;

pub type Record = Map<String, Value>;

pub trait Sink: Send {
    fn write(&mut self, record: &Record) -> io::Result<()>;
    fn close(&mut self) -> io::Result<()>;
}

fn open(path: &str) -> io::Result<Box<dyn Write + Send>> {
    if let Some(dir) = Path::new(path).parent().filter(|d| !d.as_os_str().is_empty()) {
        create_dir_all(dir)?;
    }
    Ok(Box::new(BufWriter::new(File::create(path)?)))
}

pub struct JsonlSink(Box<dyn Write + Send>);

impl JsonlSink {
    pub fn new(path: Option<&str>) -> io::Result<Self> {
        Ok(Self(match path { Some(p) => open(p)?, None => Box::new(io::stdout()) }))
    }
}

impl Sink for JsonlSink {
    fn write(&mut self, record: &Record) -> io::Result<()> {
        serde_json::to_writer(&mut self.0, record)?;
        self.0.write_all(b"\n") // "\n" on every OS
    }
    fn close(&mut self) -> io::Result<()> {
        self.0.flush()
    }
}

pub struct CsvSink {
    out: Box<dyn Write + Send>,
    fields: Option<Vec<String>>,
    header_done: bool,
}

/// CSV cell: strings as-is, null empty, everything else JSON; quoted when needed.
fn cell(v: Option<&Value>) -> String {
    let s = match v {
        None | Some(Value::Null) => String::new(),
        Some(Value::String(s)) => s.clone(),
        Some(other) => other.to_string(),
    };
    if s.contains([',', '"', '\n', '\r']) { format!("\"{}\"", s.replace('"', "\"\"")) } else { s }
}

impl Sink for CsvSink {
    fn write(&mut self, record: &Record) -> io::Result<()> {
        let fields = self.fields.get_or_insert_with(|| record.keys().cloned().collect());
        if !self.header_done {
            let header: Vec<_> = fields.iter().map(|f| cell(Some(&Value::String(f.clone())))).collect();
            writeln!(self.out, "{}", header.join(","))?;
            self.header_done = true;
        }
        let row: Vec<_> = fields.iter().map(|f| cell(record.get(f))).collect();
        writeln!(self.out, "{}", row.join(","))
    }
    fn close(&mut self) -> io::Result<()> {
        self.out.flush()
    }
}

/// Build sinks from [[sinks]] tables; stdout when none are configured.
pub fn make_sinks(specs: &[SinkConfig]) -> io::Result<Vec<Box<dyn Sink>>> {
    if specs.is_empty() {
        return Ok(vec![Box::new(JsonlSink::new(None)?)]);
    }
    specs.iter().map(|s| -> io::Result<Box<dyn Sink>> {
        Ok(match s.kind.as_str() {
            "jsonl" => Box::new(JsonlSink::new(Some(&s.path))?),
            "csv" => Box::new(CsvSink { out: open(&s.path)?, fields: s.fields.clone(), header_done: false }),
            "stdout" => Box::new(JsonlSink::new(None)?),
            other => return Err(io::Error::other(format!("unknown sink type: {other}"))),
        })
    }).collect()
}
