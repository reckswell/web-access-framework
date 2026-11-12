//! robots.txt parsing and matching (spec/04-politeness.md).
use regex::Regex;

pub const DENY_ALL: &str = "User-agent: *\nDisallow: /";

struct Group {
    agents: Vec<String>,
    rules: Vec<(usize, bool, Regex)>, // (pattern length, allow, compiled pattern)
    delay: Option<f64>,
}

#[derive(Default)]
pub struct Robots {
    groups: Vec<Group>,
}

/// robots pattern -> regex: `*` = any run of characters, trailing `$` = end anchor, else prefix.
fn compile(pattern: &str) -> Regex {
    let (body, anchored) = pattern.strip_suffix('$').map_or((pattern, false), |b| (b, true));
    let rx: Vec<String> = body.split('*').map(regex::escape).collect();
    Regex::new(&format!("(?s)^{}{}", rx.join(".*"), if anchored { "$" } else { "" })).expect("escaped pattern")
}

impl Robots {
    pub fn parse(text: &str) -> Self {
        let (mut groups, mut in_agents) = (Vec::<Group>::new(), false);
        for line in text.lines() {
            let Some((key, val)) = line.split('#').next().unwrap_or("").split_once(':') else { continue };
            let (key, val) = (key.trim().to_ascii_lowercase(), val.trim());
            if key == "user-agent" {
                if !in_agents {
                    groups.push(Group { agents: vec![], rules: vec![], delay: None }); // new group
                }
                groups.last_mut().unwrap().agents.push(val.to_lowercase());
                in_agents = true;
                continue;
            }
            in_agents = false;
            let Some(g) = groups.last_mut() else { continue }; // rules before any user-agent are ignored
            match key.as_str() {
                "allow" | "disallow" if !val.is_empty() => g.rules.push((val.chars().count(), key == "allow", compile(val))),
                "crawl-delay" => g.delay = val.parse().ok().or(g.delay),
                _ => {}
            }
        }
        Robots { groups }
    }

    fn select(&self, agent: &str) -> Vec<&Group> {
        let agent = agent.to_lowercase();
        let mine: Vec<_> = self.groups.iter().filter(|g| g.agents.contains(&agent)).collect();
        if mine.is_empty() { self.groups.iter().filter(|g| g.agents.iter().any(|a| a == "*")).collect() } else { mine }
    }

    pub fn allowed(&self, agent: &str, target: &str) -> bool {
        if target == "/robots.txt" {
            return true;
        }
        // longest pattern wins; on a tie (len, true) > (len, false) so allow wins
        let best = self.select(agent).iter().flat_map(|g| &g.rules).filter(|(_, _, rx)| rx.is_match(target))
            .map(|(n, allow, _)| (*n, *allow)).max();
        best.is_none_or(|(_, allow)| allow)
    }

    pub fn crawl_delay(&self, agent: &str) -> Option<f64> {
        self.select(agent).iter().find_map(|g| g.delay)
    }
}
