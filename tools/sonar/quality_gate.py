#!/usr/bin/env python3
"""
SonarQube Quality Gate Checker
Uses SonarQube's built-in quality gate status.
Exits with code 1 if quality gate fails.

IMPORTANT: SonarQube needs time to process analysis reports (5-30 seconds).
           Use --wait flag to poll until processing completes.
"""
import sys
import time
from tools.sonar.config import SONAR_URL, SONAR_TOKEN, PROJECT_KEY

# Constants
NO_MESSAGE_TEXT = 'No message'
METRIC_NAMES = {
    'new_coverage': 'New Code Coverage',
    'new_duplicated_lines_density': 'New Code Duplication',
    'new_violations': 'New Issues',
    'new_reliability_rating': 'New Reliability Rating',
    'new_security_rating': 'New Security Rating',
    'new_maintainability_rating': 'New Maintainability Rating',
}

def _get_task_url():
    """Read task URL from report file."""
    try:
        with open('.sonar/report-task.txt', 'r') as f:
            for line in f:
                if line.startswith('ceTaskUrl='):
                    return line.strip().split('=', 1)[1]
    except FileNotFoundError:
        pass
    return None

def _poll_task_status(task_url, max_wait):
    """Poll SonarQube task status until complete."""
    import requests

    start_time = time.time()
    while (time.time() - start_time) < max_wait:
        try:
            response = requests.get(task_url, auth=(SONAR_TOKEN, ""))
            if response.status_code != 200:
                continue

            task_status = response.json().get('task', {}).get('status')

            if task_status == 'SUCCESS':
                elapsed = time.time() - start_time
                print(f"   Processing complete ({elapsed:.1f}s)")
                time.sleep(2)
                return True
            elif task_status == 'FAILED':
                print("   Processing failed")
                return False
            elif task_status in ['PENDING', 'IN_PROGRESS']:
                print(f"   Still processing... ({task_status})")
                time.sleep(2)
            else:
                print(f"   Unknown status: {task_status}")
                return False
        except Exception as e:
            print(f"   Error checking status: {e}")
            return False

    print(f"   Timeout after {max_wait}s - proceeding anyway")
    return False

def wait_for_processing(max_wait=60):
    """Wait for SonarQube to finish processing the analysis report."""
    print("\nWaiting for SonarQube to finish processing analysis report...")

    task_url = _get_task_url()
    if not task_url:
        print("   Could not find task URL, proceeding with current status")
        return

    _poll_task_status(task_url, max_wait)

def display_conditions(conditions):
    """Display quality gate conditions."""
    if not conditions:
        return

    print("\nConditions:")
    for condition in conditions:
        metric_key = condition['metricKey']
        cond_status = condition['status']
        actual = condition.get('actualValue', 'N/A')
        threshold = condition.get('errorThreshold', 'N/A')
        comparator = condition.get('comparator', 'N/A')

        status_icon = "PASS" if cond_status == "OK" else "FAIL"
        metric_name = METRIC_NAMES.get(metric_key, metric_key)

        print(f"   [{status_icon}] {metric_name}: {actual} (threshold: {comparator} {threshold})")

def display_failed_conditions(conditions):
    """Display which conditions failed."""
    print("\nFailed Conditions:")
    for condition in conditions:
        if condition['status'] == 'ERROR':
            metric_key = condition['metricKey']
            actual = condition.get('actualValue', 'N/A')
            threshold = condition.get('errorThreshold', 'N/A')
            metric_name = METRIC_NAMES.get(metric_key, metric_key)
            print(f"   - {metric_name}: {actual} (required: {threshold})")

def _get_line_number(issue):
    """Extract line number from issue."""
    if 'line' in issue:
        return issue['line']

    text_range = issue.get('textRange', {})
    if text_range and 'startLine' in text_range:
        return text_range['startLine']

    return 'N/A'

def _format_issue_display(issue, index):
    """Format a single issue for display."""
    file = issue['component'].split(':')[-1]
    line = _get_line_number(issue)
    severity = issue['severity']
    message = issue.get('message', NO_MESSAGE_TEXT)
    rule = issue.get('rule', 'N/A')
    issue_type = issue.get('type', 'N/A')

    print(f"{index}. [{severity}] {file}:{line}")
    print(f"   Type: {issue_type}")
    print(f"   Rule: {rule}")
    print(f"   {message}\n")

def display_new_violations(conditions):
    """Display new violations causing quality gate failure."""
    import requests

    for condition in conditions:
        if condition['status'] != 'ERROR' or condition['metricKey'] != 'new_violations':
            continue

        num_new = int(float(condition.get('actualValue', 0)))
        print(f"\nNEW ISSUES (the {num_new} new issues):")
        print('-' * 80)

        url = f"{SONAR_URL}/api/issues/search"
        response = requests.get(
            url,
            params={
                "componentKeys": PROJECT_KEY,
                "createdInLast": "2h",
                "issueStatuses": "OPEN,CONFIRMED",
                "s": "CREATION_DATE",
                "asc": "false",
                "ps": max(num_new, 10)
            },
            auth=(SONAR_TOKEN, "")
        )
        new_issues = response.json().get('issues', [])

        for i, issue in enumerate(new_issues[:num_new], 1):
            _format_issue_display(issue, i)
        break

def _display_action_required(conditions):
    """Display action required message with direct links."""
    print(f"\n{'=' * 80}")
    print("\nAction Required:")

    for condition in conditions:
        if condition['status'] != 'ERROR':
            continue

        metric_key = condition['metricKey']

        if metric_key == 'new_violations':
            print("\n   View NEW CODE issues directly:")
            print(f"      {SONAR_URL}/project/issues?issueStatuses=OPEN,CONFIRMED&inNewCodePeriod=true&id={PROJECT_KEY}")

        elif metric_key == 'new_coverage':
            print("\n   View files with LOW COVERAGE:")
            print(f"      {SONAR_URL}/component_measures?id={PROJECT_KEY}&metric=new_coverage&view=list")

        elif metric_key == 'new_duplicated_lines_density':
            print("\n   View DUPLICATED CODE:")
            print(f"      {SONAR_URL}/component_measures?id={PROJECT_KEY}&metric=new_duplicated_lines_density&view=list")

    print("\n   Or use make commands:")
    print("      make sonar-new          # Review new code issues")
    print("      make sonar-bugs         # Review all bugs")
    print("      make sonar-critical     # Review critical issues")

    print(f"\n   Dashboard: {SONAR_URL}/dashboard?id={PROJECT_KEY}")

def _get_quality_gate_status():
    """Fetch quality gate status from SonarQube API."""
    import requests

    url = f"{SONAR_URL}/api/qualitygates/project_status"
    response = requests.get(
        url,
        params={"projectKey": PROJECT_KEY},
        auth=(SONAR_TOKEN, "")
    )
    return response.json()

def check_quality_gate():
    """Check if project passes SonarQube's quality gate."""
    qg_status = _get_quality_gate_status()

    project_status = qg_status['projectStatus']
    status = project_status['status']
    conditions = project_status.get('conditions', [])

    print("=" * 80)
    print("SONARQUBE QUALITY GATE CHECK")
    print("=" * 80)
    print(f"\nQuality Gate Status: {status}")

    display_conditions(conditions)

    print(f"\n{'=' * 80}")

    if status == "OK":
        print("QUALITY GATE PASSED")
        print("=" * 80)
        print(f"\nDashboard: {SONAR_URL}/dashboard?id={PROJECT_KEY}")
        return 0

    print("QUALITY GATE FAILED")
    print("=" * 80)

    display_failed_conditions(conditions)

    if any(c['status'] == 'ERROR' and c['metricKey'] == 'new_violations' for c in conditions):
        print(f"\n{'=' * 80}")
        print("WHAT'S CAUSING THE QUALITY GATE TO FAIL")
        print("=" * 80)
        display_new_violations(conditions)

    _display_action_required(conditions)
    return 1

if __name__ == "__main__":
    wait_for_completion = '--wait' in sys.argv

    if wait_for_completion:
        wait_for_processing(max_wait=60)

    sys.exit(check_quality_gate())
