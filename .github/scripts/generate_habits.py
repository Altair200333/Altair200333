#!/usr/bin/env python3
"""Generate a privacy-safe GitHub coding-habits SVG."""

from __future__ import annotations

import html
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


API_URL = "https://api.github.com/search/commits"
API_VERSION = "2022-11-28"
WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as error:
        raise SystemExit(f"{name} must be an integer, got {raw!r}") from error
    return max(minimum, min(maximum, value))


def request_json(url: str, token: str | None) -> tuple[dict, object]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Altair200333-profile-metrics",
        "X-GitHub-Api-Version": API_VERSION,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    for attempt in range(1, 5):
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response), response.headers
        except urllib.error.HTTPError as error:
            retryable = error.code in {403, 429, 500, 502, 503, 504}
            if not retryable or attempt == 4:
                details = error.read().decode("utf-8", errors="replace")[:500]
                raise SystemExit(f"GitHub API returned HTTP {error.code}: {details}") from error
            retry_after = error.headers.get("Retry-After")
            delay = int(retry_after) if retry_after and retry_after.isdigit() else 15 * attempt
            print(f"GitHub API retry {attempt}/4 in {delay}s", file=sys.stderr)
            time.sleep(delay)
        except urllib.error.URLError as error:
            if attempt == 4:
                raise SystemExit(f"GitHub API request failed: {error}") from error
            delay = 5 * attempt
            print(f"Network retry {attempt}/4 in {delay}s", file=sys.stderr)
            time.sleep(delay)

    raise AssertionError("unreachable")


def fetch_commits(
    user: str,
    since_date: str,
    until_date: str,
    token: str | None,
    max_commits: int,
) -> tuple[list[dict], dict[str, str]]:
    query = f"author:{user} author-date:{since_date}..{until_date}"
    commits: dict[str, dict] = {}
    diagnostics = {
        "scope": "unknown",
        "sso": "ok",
        "incomplete": "false",
        "total": "0",
    }

    for page in range(1, 11):
        params = urllib.parse.urlencode({
            "q": query,
            "per_page": 100,
            "page": page,
        })
        payload, headers = request_json(f"{API_URL}?{params}", token)

        if page == 1:
            diagnostics["total"] = str(payload.get("total_count", 0))
            scopes = {scope.strip() for scope in headers.get("X-OAuth-Scopes", "").split(",") if scope.strip()}
            diagnostics["scope"] = "repo" if "repo" in scopes else ("public-only" if scopes else "unknown")
            if headers.get("X-GitHub-SSO", "").startswith("partial-results"):
                diagnostics["sso"] = "partial"

        if payload.get("incomplete_results"):
            diagnostics["incomplete"] = "true"

        items = payload.get("items", [])
        for item in items:
            sha = item.get("sha")
            if sha:
                commits[sha] = item
            if len(commits) >= max_commits:
                break

        if len(commits) >= max_commits or len(items) < 100:
            break

    return list(commits.values())[:max_commits], diagnostics


def parse_commit_time(item: dict, timezone: ZoneInfo) -> datetime | None:
    commit = item.get("commit") or {}
    author = commit.get("author") or {}
    committer = commit.get("committer") or {}
    timestamp = author.get("date") or committer.get("date")
    if not timestamp:
        return None
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(timezone)
    except ValueError:
        return None


def chart_bars(
    values: list[int],
    labels: list[str],
    left: float,
    right: float,
    top: float,
    baseline: float,
    label_y: float,
    label_every: int = 1,
) -> str:
    width = right - left
    step = width / len(values)
    bar_width = min(30.0, step * 0.58)
    maximum = max(values, default=0)
    peak_index = values.index(maximum) if maximum else -1
    available_height = baseline - top
    output = [f'<line class="axis" x1="{left:.1f}" y1="{baseline:.1f}" x2="{right:.1f}" y2="{baseline:.1f}"/>']

    for index, value in enumerate(values):
        x = left + step * index + step / 2
        height = (value / maximum) * available_height if maximum else 0
        y = baseline - height
        css_class = "bar peak" if index == peak_index else "bar"
        output.append(
            f'<rect class="{css_class}" x="{x - bar_width / 2:.1f}" y="{y:.1f}" '
            f'width="{bar_width:.1f}" height="{height:.1f}" rx="2"><title>{value} commits</title></rect>'
        )
        if index % label_every == 0:
            output.append(f'<text class="tick" x="{x:.1f}" y="{label_y:.1f}" text-anchor="middle">{html.escape(labels[index])}</text>')

    if peak_index >= 0:
        peak_x = left + step * peak_index + step / 2
        peak_y = baseline - available_height
        output.append(f'<text class="peak-value" x="{peak_x:.1f}" y="{max(11.0, peak_y - 5):.1f}" text-anchor="middle">{maximum}</text>')

    return "\n".join(output)


def render_svg(
    user: str,
    days: int,
    timezone_name: str,
    hours: list[int],
    weekdays: list[int],
    commit_count: int,
    repository_count: int,
) -> str:
    peak_hour = hours.index(max(hours)) if any(hours) else None
    peak_day = weekdays.index(max(weekdays)) if any(weekdays) else None
    peak_summary = (
        f"Peak: {WEEKDAYS[peak_day]} / {peak_hour:02d}:00"
        if peak_hour is not None and peak_day is not None
        else "No commits found"
    )
    hour_labels = [f"{hour:02d}" for hour in range(24)]

    hour_chart = chart_bars(hours, hour_labels, 24, 460, 86, 157, 174, label_every=3)
    weekday_chart = chart_bars(weekdays, WEEKDAYS, 24, 460, 219, 284, 302)

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="480" height="330" viewBox="0 0 480 330" role="img" aria-labelledby="title description">
  <title id="title">Recent coding habits for GitHub user {html.escape(user)}</title>
  <desc id="description">Commit activity by hour and weekday over the last {days} days.</desc>
  <style>
    text {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; fill: #24292f; font-size: 12px; }}
    .title {{ fill: #0969da; font-size: 14px; font-weight: 500; }}
    .muted, .tick {{ fill: #57606a; font-size: 10px; }}
    .section {{ fill: #24292f; font-size: 12px; font-weight: 500; }}
    .axis {{ stroke: #d0d7de; stroke-width: 1; }}
    .bar {{ fill: #2da44e; opacity: .66; }}
    .bar.peak {{ opacity: 1; }}
    .peak-value {{ fill: #24292f; font-size: 10px; font-weight: 500; }}
    @media (prefers-color-scheme: dark) {{
      text, .section, .peak-value {{ fill: #c9d1d9; }}
      .title {{ fill: #58a6ff; }}
      .muted, .tick {{ fill: #8b949e; }}
      .axis {{ stroke: #30363d; }}
      .bar {{ fill: #3fb950; }}
    }}
  </style>

  <text class="title" x="20" y="25">Recent coding habits</text>
  <text class="muted" x="20" y="43">Last {days} days · {commit_count} commits · {repository_count} repositories</text>

  <text class="section" x="20" y="72">Commit activity per hour of day</text>
  {hour_chart}

  <text class="section" x="20" y="205">Commit activity per day of week</text>
  {weekday_chart}

  <text class="muted" x="20" y="322">{html.escape(peak_summary)} · {html.escape(timezone_name)}</text>
</svg>
'''


def main() -> None:
    user = os.getenv("HABITS_USER", "Altair200333")
    days = env_int("HABITS_DAYS", 30, 1, 365)
    max_commits = env_int("HABITS_MAX_COMMITS", 1000, 1, 1000)
    timezone_name = os.getenv("HABITS_TIMEZONE", "Asia/Novosibirsk")
    output_path = Path(os.getenv("HABITS_OUTPUT", "metrics.habits.svg"))
    token = os.getenv("METRICS_TOKEN") or os.getenv("GH_TOKEN")
    excluded = {
        value.strip().lower()
        for value in os.getenv("HABITS_EXCLUDE_REPOS", f"{user}/{user}").replace("\n", ",").split(",")
        if value.strip()
    }

    try:
        timezone = ZoneInfo(timezone_name)
    except Exception as error:
        raise SystemExit(f"Unknown timezone {timezone_name!r}: {error}") from error

    now = datetime.now(timezone)
    since = now - timedelta(days=days)
    commits, diagnostics = fetch_commits(user, since.date().isoformat(), now.date().isoformat(), token, max_commits)

    hours = [0] * 24
    weekdays = [0] * 7
    repositories: set[str] = set()
    private_commits = 0
    organization_commits = 0
    analyzed = 0

    for item in commits:
        repository = item.get("repository") or {}
        full_name = str(repository.get("full_name") or "")
        if full_name.lower() in excluded:
            continue
        authored_at = parse_commit_time(item, timezone)
        if authored_at is None or authored_at < since or authored_at > now:
            continue

        analyzed += 1
        repositories.add(full_name)
        hours[authored_at.hour] += 1
        weekdays[(authored_at.weekday() + 1) % 7] += 1
        private_commits += int(bool(repository.get("private")))
        organization_commits += int((repository.get("owner") or {}).get("type") == "Organization")

    svg = render_svg(user, days, timezone_name, hours, weekdays, analyzed, len(repositories))
    output_path.write_text(svg, encoding="utf-8")

    print(f"Generated {output_path} from {analyzed} commits in {len(repositories)} accessible repositories")
    print(f"Visibility summary: {private_commits} private-repository commits; {organization_commits} organization-repository commits")
    if diagnostics["scope"] == "public-only":
        print("Warning: token does not advertise classic 'repo' scope; private repositories may be absent", file=sys.stderr)
    elif diagnostics["scope"] == "unknown":
        print("Token scope type is not advertised; search results reflect repositories accessible to the token")
    if diagnostics["sso"] == "partial":
        print("Warning: some SAML SSO organizations were omitted; authorize the token for those organizations", file=sys.stderr)
    if diagnostics["incomplete"] == "true":
        print("Warning: GitHub marked the search results as incomplete", file=sys.stderr)
    if int(diagnostics["total"]) > max_commits:
        print(f"Warning: search matched {diagnostics['total']} commits; capped at {max_commits}", file=sys.stderr)


if __name__ == "__main__":
    main()
