"""robots.txt parsing and matching (spec/04-politeness.md)."""
import re

DENY_ALL = "User-agent: *\nDisallow: /"


def _compile(pattern):
    """robots pattern -> regex: `*` = any run of characters, trailing `$` = end anchor, else prefix."""
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    return re.compile("".join(".*" if c == "*" else re.escape(c) for c in body) + ("$" if anchored else ""), re.S)


class Robots:
    def __init__(self, text=""):
        self.groups, group, in_agents = [], None, False  # group = (agents, rules, [crawl_delay])
        for line in text.removeprefix("\ufeff").splitlines():  # a leading BOM is not part of the first key
            key, sep, val = line.split("#", 1)[0].partition(":")
            key, val = key.strip().lower(), val.strip()
            if not sep:
                continue
            if key == "user-agent":
                if not in_agents:  # a user-agent after rules starts a new group
                    group = ([], [], [None])
                    self.groups.append(group)
                group[0].append(val.lower())
                in_agents = True
                continue
            in_agents = False
            if group is None:
                continue  # rules before any user-agent are ignored
            if key in ("allow", "disallow") and val:
                group[1].append((len(val), key == "allow", _compile(val)))
            elif key == "crawl-delay":
                try:
                    group[2][0] = float(val)
                except ValueError:
                    pass

    def _select(self, agent):
        agent = agent.lower()
        return [g for g in self.groups if agent in g[0]] or [g for g in self.groups if "*" in g[0]]

    def allowed(self, agent, target):
        if target == "/robots.txt":
            return True
        # longest pattern wins; on a tie (len, True) > (len, False) so allow wins
        best = max(((n, allow) for g in self._select(agent) for n, allow, rx in g[1] if rx.match(target)), default=None)
        return best is None or best[1]

    def crawl_delay(self, agent):
        return next((g[2][0] for g in self._select(agent) if g[2][0] is not None), None)
