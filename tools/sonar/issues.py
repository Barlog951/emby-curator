#!/usr/bin/env python3
"""
Helper script to fetch and display SonarQube issues for the Emby Dedupe project.
"""
import sys
from collections import Counter
from tools.sonar.config import SONAR_URL, SONAR_TOKEN, PROJECT_KEY

# Constant for default message when issue has no message
NO_MESSAGE_TEXT = 'No message'


def _get_line_number(issue):
    """Extract line number from issue (handles both 'line' and 'textRange' formats)."""
    if 'line' in issue:
        return issue['line']

    text_range = issue.get('textRange', {})
    if text_range and 'startLine' in text_range:
        return text_range['startLine']

    return 'N/A'


def fetch_issues(severity_filter=None, type_filter=None, new_code_only=False):
    """Fetch issues from SonarQube.

    Note: Uses requests directly because impactSeverities doesn't accept
          comma-separated values, so we need separate calls per severity.
    """
    import requests

    url = f"{SONAR_URL}/api/issues/search"

    base_params = {
        "componentKeys": PROJECT_KEY,
        "ps": 500,
        "issueStatuses": "OPEN,CONFIRMED"
    }

    if type_filter:
        base_params["types"] = type_filter
    if new_code_only:
        base_params["inNewCodePeriod"] = "true"

    if severity_filter and ',' in severity_filter:
        all_issues = []
        for sev in severity_filter.split(','):
            params = base_params.copy()
            params["impactSeverities"] = sev.strip()
            response = requests.get(url, params=params, auth=(SONAR_TOKEN, ""))
            all_issues.extend(response.json().get('issues', []))

        seen = set()
        unique_issues = []
        for issue in all_issues:
            if issue['key'] not in seen:
                seen.add(issue['key'])
                unique_issues.append(issue)
        return unique_issues
    elif severity_filter:
        base_params["impactSeverities"] = severity_filter

    response = requests.get(url, params=base_params, auth=(SONAR_TOKEN, ""))
    return response.json().get('issues', [])


def display_summary():
    """Display issue summary."""
    issues = list(fetch_issues())

    print("=" * 80)
    print("SonarQube Analysis - Emby Dedupe")
    print("=" * 80)
    print(f"\nTotal Issues: {len(issues)}\n")

    severities = Counter(i['severity'] for i in issues)
    print("By Severity:")
    for sev in ['BLOCKER', 'CRITICAL', 'MAJOR', 'MINOR', 'INFO']:
        if sev in severities:
            print(f"  {sev:10} {severities[sev]:>3}")

    types = Counter(i['type'] for i in issues)
    print("\nBy Type:")
    for typ, count in sorted(types.items(), key=lambda x: x[1], reverse=True):
        print(f"  {typ:15} {count:>3}")

    files = Counter(i['component'].split(':')[-1] for i in issues)
    print("\nTop 10 Files with Most Issues:")
    for file, count in files.most_common(10):
        print(f"  {count:>3} {file}")


def display_bugs():
    """Display all bugs."""
    bugs = list(fetch_issues(type_filter="BUG"))

    print("=" * 80)
    print(f"BUGS ({len(bugs)})")
    print("=" * 80)

    if not bugs:
        print("\nNo bugs found!")
        return

    blocker = [b for b in bugs if b['severity'] == 'BLOCKER']
    major = [b for b in bugs if b['severity'] == 'MAJOR']
    minor = [b for b in bugs if b['severity'] == 'MINOR']

    for severity_name, bug_list in [('BLOCKER', blocker), ('MAJOR', major), ('MINOR', minor)]:
        if not bug_list:
            continue

        print(f"\n{'=' * 80}")
        print(f"{severity_name} ({len(bug_list)})")
        print('=' * 80)

        for bug in bug_list:
            file = bug['component'].split(':')[-1]
            line = _get_line_number(bug)
            message = bug.get('message', NO_MESSAGE_TEXT)
            rule = bug.get('rule', 'N/A')
            effort = bug.get('effort', 'N/A')
            tags = ', '.join(bug.get('tags', []))

            print(f"\n  {file}:{line}")
            print(f"   Rule: {rule}")
            print(f"   Effort: {effort}")
            if tags:
                print(f"   Tags: {tags}")
            print(f"   Issue: {message}")


def display_critical():
    """Display critical and blocker issues."""
    critical = list(fetch_issues(severity_filter="BLOCKER,CRITICAL"))

    print("=" * 80)
    print(f"CRITICAL & BLOCKER ISSUES ({len(critical)})")
    print("=" * 80)

    if not critical:
        print("\nNo critical issues found!")
        return

    by_file = {}
    for issue in critical:
        file = issue['component'].split(':')[-1]
        if file not in by_file:
            by_file[file] = []
        by_file[file].append(issue)

    print(f"\nShowing top 10 files (out of {len(by_file)} total)")

    for file, file_issues in sorted(by_file.items(), key=lambda x: len(x[1]), reverse=True)[:10]:
        print(f"\n{'=' * 80}")
        print(f"  {file} ({len(file_issues)} issues)")
        print('=' * 80)

        for i, issue in enumerate(file_issues[:10], 1):
            line = _get_line_number(issue)
            severity = issue['severity']
            message = issue.get('message', NO_MESSAGE_TEXT)
            rule = issue.get('rule', 'N/A')
            effort = issue.get('effort', 'N/A')

            print(f"\n{i}. Line {line} [{severity}] ({rule})")
            print(f"   {message}")
            print(f"   Effort: {effort}")


def _display_issues_by_severity(issue_list, type_name):
    """Display issues grouped by severity."""
    print(f"\n{'=' * 80}")
    print(f"{type_name} ({len(issue_list)})")
    print('=' * 80)

    by_severity = {}
    for issue in issue_list:
        sev = issue['severity']
        if sev not in by_severity:
            by_severity[sev] = []
        by_severity[sev].append(issue)

    for sev in ['BLOCKER', 'CRITICAL', 'MAJOR', 'MINOR', 'INFO']:
        if sev not in by_severity:
            continue

        print(f"\n{sev} ({len(by_severity[sev])})")
        print('-' * 80)

        for i, issue in enumerate(by_severity[sev][:20], 1):
            file = issue['component'].split(':')[-1]
            line = _get_line_number(issue)
            message = issue.get('message', NO_MESSAGE_TEXT)
            rule = issue.get('rule', 'N/A')

            print(f"{i}. {file}:{line} [{rule}]")
            print(f"   {message}")


def display_all():
    """Display all issues in structured format."""
    issues = list(fetch_issues())

    print("=" * 80)
    print(f"ALL ISSUES ({len(issues)})")
    print("=" * 80)

    bugs = [i for i in issues if i['type'] == 'BUG']
    code_smells = [i for i in issues if i['type'] == 'CODE_SMELL']
    vulnerabilities = [i for i in issues if i['type'] == 'VULNERABILITY']

    for type_name, issue_list in [('BUGS', bugs), ('CODE SMELLS', code_smells), ('VULNERABILITIES', vulnerabilities)]:
        if issue_list:
            _display_issues_by_severity(issue_list, type_name)


def display_new_code_issues():
    """Display issues in new code only (since leak period)."""
    new_issues = list(fetch_issues(new_code_only=True))

    print("=" * 80)
    print(f"NEW CODE ISSUES ({len(new_issues)})")
    print("=" * 80)

    if not new_issues:
        print("\nNo issues in new code!")
        return

    bugs = [i for i in new_issues if i['type'] == 'BUG']
    code_smells = [i for i in new_issues if i['type'] == 'CODE_SMELL']
    vulnerabilities = [i for i in new_issues if i['type'] == 'VULNERABILITY']

    severities = Counter(i['severity'] for i in new_issues)
    print("\nBy Severity:")
    for sev in ['BLOCKER', 'CRITICAL', 'MAJOR', 'MINOR', 'INFO']:
        if sev in severities:
            print(f"  {sev:10} {severities[sev]:>3}")

    print("\nBy Type:")
    print(f"  BUGS: {len(bugs)}")
    print(f"  CODE_SMELLS: {len(code_smells)}")
    print(f"  VULNERABILITIES: {len(vulnerabilities)}")

    for type_name, issue_list in [('BUGS', bugs), ('CODE SMELLS', code_smells), ('VULNERABILITIES', vulnerabilities)]:
        if not issue_list:
            continue

        print(f"\n{'=' * 80}")
        print(f"{type_name} IN NEW CODE ({len(issue_list)})")
        print('=' * 80)

        for i, issue in enumerate(issue_list[:20], 1):
            file = issue['component'].split(':')[-1]
            line = _get_line_number(issue)
            severity = issue['severity']
            message = issue.get('message', NO_MESSAGE_TEXT)
            rule = issue.get('rule', 'N/A')

            print(f"\n{i}. [{severity}] {file}:{line} ({rule})")
            print(f"   {message}")


def display_metrics():
    """Fetch and display project metrics."""
    import requests

    url = f"{SONAR_URL}/api/measures/component"
    metrics = [
        'bugs', 'vulnerabilities', 'code_smells', 'coverage',
        'duplicated_lines_density', 'ncloc', 'sqale_rating',
        'reliability_rating', 'security_rating', 'cognitive_complexity'
    ]

    response = requests.get(
        url,
        params={
            "component": PROJECT_KEY,
            "metricKeys": ','.join(metrics)
        },
        auth=(SONAR_TOKEN, "")
    )
    measures = response.json().get('component', {}).get('measures', [])

    print("=" * 80)
    print("PROJECT METRICS")
    print("=" * 80)

    metric_map = {
        'ncloc': 'Lines of Code',
        'coverage': 'Code Coverage (%)',
        'duplicated_lines_density': 'Duplications (%)',
        'bugs': 'Bugs',
        'vulnerabilities': 'Vulnerabilities',
        'code_smells': 'Code Smells',
        'cognitive_complexity': 'Cognitive Complexity',
        'sqale_rating': 'Maintainability Rating',
        'reliability_rating': 'Reliability Rating',
        'security_rating': 'Security Rating',
    }

    for measure in measures:
        metric = measure['metric']
        value = measure.get('value', 'N/A')
        name = metric_map.get(metric, f"Unknown ({metric})")
        print(f"  {name:30} {value}")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "summary"

    if command == "summary":
        display_summary()
    elif command == "bugs":
        display_bugs()
    elif command == "critical":
        display_critical()
    elif command == "all":
        display_all()
    elif command == "new":
        display_new_code_issues()
    elif command == "metrics":
        display_metrics()
    else:
        print("Usage: python -m tools.sonar.issues [summary|bugs|critical|all|new|metrics]")
        print("  summary  - Show issue summary (default)")
        print("  bugs     - Show all bugs grouped by severity")
        print("  critical - Show critical and blocker issues")
        print("  all      - Show all issues grouped by type and severity")
        print("  new      - Show issues in NEW CODE ONLY (since last version)")
        print("  metrics  - Show project quality metrics")
