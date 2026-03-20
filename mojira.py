from pathlib import Path
from typing import Callable, Iterable, Literal
import json
import logging
import requests


def jira_api(
    *,
    project: str,
    bugs: Iterable[str] = [],
    search: str | None = None,
    bugs_size_per_chunk: int = 200,
    legacy_mode: bool = False,
) -> list[dict]:
    def legacy(bug: str) -> dict:
        response = requests.get(
            "https://bugs-legacy.mojang.com/rest/api/2/issue/{}".format(bug),
            headers={"Content-Type": "application/json"},
        )
        return response.json()

    def public(bugs: Iterable[str]) -> list[dict]:
        response = requests.post(
            "https://bugs.mojang.com/api/jql-search-post",
            json={
                "advanced": True,
                "project": project,
                "search": "key in ({})".format(",".join(bugs)) if bugs else search,
                "maxResults": bugs_size_per_chunk,
            },
            headers={"Content-Type": "application/json"},
        )
        return response.json()["issues"]

    def servicedesk(bug: str) -> dict:
        def authenticate() -> requests.cookies.RequestsCookieJar:
            response = requests.post(
                "https://report.bugs.mojang.com/jsd-login/v1/authentication/authenticate",
                json={
                    "email": sdjira_email,
                    "password": sdjira_password,
                },
                headers={"Content-Type": "application/json"},
            )
            assert response.cookies
            return response.cookies

        global sdjira_cookies
        if not sdjira_cookies:
            sdjira_cookies = authenticate()
        portal = sdjira_portals[bug.split("-")[0]]
        response = requests.post(
            "https://report.bugs.mojang.com/rest/servicedesk/1/customer/models",
            json={
                "models": ["reqDetails"],
                "options": {
                    "reqDetails": {
                        "key": bug,
                        "portalId": portal,
                    },
                    "portalId": portal,
                },
            },
            headers={"Content-Type": "application/json"},
            cookies=sdjira_cookies,
        )
        if response.status_code == 403:
            sdjira_cookies = authenticate()
            assert False
        issue = response.json()["reqDetails"]["issue"]
        resolution = {"fields": {}}
        if "resolution" in issue:
            resolution["fields"]["resolution"] = {"name": issue["resolution"]}
        assert issue["resolution"] != "Duplicate"
        return resolution

    if legacy_mode:
        return [legacy(bug) for bug in bugs]
    try:
        return public(bugs)
    except:
        return [servicedesk(bug) for bug in bugs]


def convert(
    *,
    issues: Iterable[dict],
    needs: (
        Iterable[
            Literal[
                "title",
                "description_html",
                "description_json",
                "resolution",
                "status",
                "confirmation",
                "fix_versions",
                "affects_versions",
                "issue_links",
                "attachments",
                "created_at",
                "updated_at",
                "resolved_at",
                "key",
            ]
        ]
        | Literal["all"]
    ) = "all",
) -> dict:
    result = {}
    if needs == "all":
        for i in issues:
            result[i["key"]] = i
        return result
    for i in issues:
        result[i["key"]] = {}

    def field(name: str, method: Callable | None = None) -> None:
        if name not in needs:
            return
        for i in issues:
            result[i["key"]][name] = method(i)

    field("title", lambda i: i["fields"]["summary"])
    field("description_html", lambda i: i["renderedFields"]["description"])
    field("description_json", lambda i: i["fields"]["description"]["content"])
    field("resolution", lambda i: i["fields"]["resolution"].get("name"))
    field("status", lambda i: i["fields"]["status"]["name"])
    field("confirmation", lambda i: i["fields"]["customfield_10054"]["value"])
    field("fix_versions", lambda i: [x["name"] for x in i["fields"]["fixVersions"]])
    field("affects_versions", lambda i: [x["name"] for x in i["fields"]["versions"]])
    field("issue_links", lambda i: i["fields"]["issuelinks"])
    field("attachments", lambda i: i["fields"]["attachment"])
    field("created_at", lambda i: i["fields"]["created"])
    field("updated_at", lambda i: i["fields"]["updated"])
    field("resolved_at", lambda i: i["fields"]["resolutiondate"])
    field("key", lambda i: i["key"])
    return result


if __name__ == "__main__":
    sdjira_email = ""  # If needs Service Desk Jira, please fill in.
    sdjira_password = ""  # If needs Service Desk Jira, please fill in.
    fix_version = "26.1 Snapshot 10"  # Example value: "26.1 Snapshot 1"

    base_dir = Path(__file__).parent
    logger = logging.getLogger()
    sdjira_cookies = None  # Do not change.
    sdjira_portals = {
        "MC": 2,
        "MCPE": 6,
        "MCL": 7,
        "REALMS": 9,
        "WEB": 10,
        "BDS": 4,
    }

    result = jira_api(
        project="MC",
        # bugs=["MC-4"],
        search='fixVersions in ("{}") AND resolution = "Fixed"'.format(fix_version),
    )
    result = convert(issues=result, needs=("title", "affects_versions"))

    with open(base_dir / "jira_api.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
