//! Web Access Framework (Rust). The design lives in `../spec`; module names mirror it.
pub mod config;
pub mod engine;
pub mod frontier;
pub mod goals;
pub mod limits;
pub mod model;
pub mod robots;
pub mod safety;
pub mod sinks;
pub mod urls;

pub use config::Config;
pub use engine::Engine;
pub use goals::{CrawlGoal, FetchGoal, Goal};
pub use model::{Output, Response, Task};
