import requests
import re
import time

from datetime import datetime, timedelta
from threading import Timer
from bot import config

headers = {"Private-Token": config.GITLAB_TOKEN}
project = 9202919
options = {"version": [], "device": []}

label_data = {
    "device": {
        "data": True,
        "error": "- A device is required. (include /device devicecodename)",
    },
    "version": {
        "data": True,
        "error": "- The version of LineageOS running on your device is required (include /version lineage-xx.x).",
    },
    "date": {
        "data": False,
        "error": "- Build date is required (include /date YYYY-MM-DD).",
    },
}


def validate(description):
    errors = []
    labels = []
    seen = []
    for line in re.sub("(<!--.*?-->)", "", description, flags=re.DOTALL).splitlines():
        if line.startswith("/") and " " in line:
            str_list = list(filter(None, line.split("/")))
            for pair in str_list:
                if " " not in pair:
                    continue
                spaced = list(filter(None, pair.split(" ")))
                if len(spaced) < 2:
                    continue
                label, value = spaced[0:2]
                if value:
                    if label in seen:
                        errors.append(
                            f"{label} is duplicated, please specify only one {label}"
                        )
                    else:
                        seen.append(label)
                if label in label_data.keys():
                    if label_data[label]["data"]:
                        already_valid, value = validate_version(label, value)
                        if value in options[label] or already_valid:
                            labels.append(f"{label}:{value}")
                        elif value:
                            errors.append(
                                f"- {value} is not a valid {label}. Supported values are {options[label]}"
                            )
                    else:
                        labels.append(f"{label}")
    missing_labels = label_data.keys() - set(seen)
    for label in missing_labels:
        errors.append(label_data[label]["error"])
    return labels, errors


def validate_version(label, value):
    if label != "version" or not value:
        return False, value
    match = re.search(r"(lineage-)?(\d{2}\.\d)(-20\d{6}-NIGHTLY-.+(\.zip)?)?", value)
    version = None
    if match:
        version = match.group(2)
    if version in options[label]:
        return True, f"lineage-{version}"
    return False, value


def post_reply(iid, reply):
    try:
        resp = requests.post(
            f"https://gitlab.com/api/v4/projects/{project}/issues/{iid}/notes",
            json={"body": "\n".join(reply)},
            headers=headers,
        )
    except requests.exceptions.RequestException as e:
        print(e)
        return

    if resp.status_code != 201:
        print(f"Error replying - ${resp.json()}")


def edit_issue(iid, edits):
    try:
        resp = requests.put(
            f"https://gitlab.com/api/v4/projects/{project}/issues/{iid}",
            json=edits,
            headers=headers,
        )
    except requests.exceptions.RequestException as e:
        print(e)
        return

    if resp.status_code != 200:
        print(f"Error updating labels - ${resp.json()}")


def process_new():
    try:
        resp = requests.get(
            f"https://gitlab.com/api/v4/projects/{project}/issues?state=opened&labels=None",
            headers=headers,
        )
    except requests.exceptions.RequestException as e:
        print(e)
        return

    if resp.status_code != 200:
        print(f"Error getting issues - {resp.json()}")
        return
    for issue in resp.json():
        labels, errors = validate(issue["description"])
        reply = None
        if errors:
            labels.append("invalid")
            reply = (
                [
                    "Hi! It appears you didn't read or follow the provided issue template."
                    "Your issue has been marked as invalid. Please edit your issue to include "
                    "the requested fields and follow the provided template."
                    "For more information please see https://wiki.lineageos.org/how-to/bugreport",
                    "",
                    "Problems:",
                    "",
                ]
                + errors
                + ["", "(this action was performed by a bot)"]
            )
        if reply:
            post_reply(issue["iid"], reply)
        # edit issue
        edits = {"labels": ",".join(labels)}
        if "invalid" in labels:
            edits["state_event"] = "close"
        edit_issue(issue["iid"], edits)
        print(f"new: {issue['web_url']}")


def process_invalid():
    try:
        resp = requests.get(
            f"https://gitlab.com/api/v4/projects/{project}/issues?state=opened&labels=invalid",
            headers=headers,
        )
    except requests.exceptions.RequestException as e:
        print(e)
        return

    if resp.status_code != 200:
        print(f"Error getting invalid issues - {resp.json()}")
        return
    for issue in resp.json():
        labels, errors = validate(issue["description"])
        reply = None
        if errors:
            labels.append("invalid")
            reply = (
                [
                    "Hi! It appears this issue still has problems - please fix the things below and reopen it!",
                    "",
                    "Problems:",
                    "",
                ]
                + errors
                + ["", "(this action was performed by a bot)"]
            )
        if reply:
            post_reply(issue["iid"], reply)
        edits = {"labels": ",".join(labels)}
        if "invalid" in labels:
            edits["state_event"] = "close"
        edit_issue(issue["iid"], edits)
        print(f"invalid: {issue['web_url']}")


def load_valid_devices():
    global options
    try:
        new_options = [
            x["model"]
            for x in requests.get(
                "https://raw.githubusercontent.com/LineageOS/hudson/master/updater/devices.json"
            ).json()
        ]
    except requests.exceptions.RequestException as e:
        print(e)
        return

    if new_options:
        options["device"] = new_options


def load_valid_versions():
    global options
    try:
        r = requests.get(
            "https://raw.githubusercontent.com/LineageOS/hudson/master/lineage-build-targets"
        )
    except requests.exceptions.RequestException as e:
        print(e)
        return

    new_options = []
    for line in r.text.splitlines():
        if line is None or line == "" or line.startswith("#"):
            continue
        result = re.match(r"^([\w\d]*?) (\w*?) ([\w\d\-.]*) (\w*)", line)
        if result:
            branch = result.group(3).replace("lineage-", "")
            if branch not in new_options:
                new_options.append(branch)
    if new_options:
        options["version"] = new_options


def load_options():
    load_valid_versions()
    load_valid_devices()

    # Do this again one day later once we got valid data
    if not options["version"] or not options["device"]:
        return

    x = datetime.today()
    y = x + timedelta(days=1)
    delta_t = y - x
    secs = delta_t.total_seconds()
    t = Timer(secs, load_options)
    t.start()


if __name__ == "__main__":
    # Load the options and make sure we only start processing if we have valid data
    while True:
        load_options()
        if options["version"] and options["device"]:
            break
        time.sleep(60)

    while True:
        process_new()
        process_invalid()
        time.sleep(60)
