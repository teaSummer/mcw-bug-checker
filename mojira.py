import logging
import os
from pathlib import Path
from typing import Callable, Literal, Sequence

from dotenv import load_dotenv
import orjson
import requests
from requests.cookies import RequestsCookieJar

from checker import Config

type Need = Literal[
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


def jira_api(
    *,
    project: str,
    bugs: Sequence[str] = (),
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

    def public(bugs: Sequence[str]) -> list[dict]:
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
        def authenticate() -> RequestsCookieJar:
            response = requests.post(
                "https://report.bugs.mojang.com/jsd-login/v1/authentication/authenticate",
                json={
                    "email": os.getenv("SDJIRA_ACCOUNT_EMAIL"),
                    "password": os.getenv("SDJIRA_ACCOUNT_PASSWORD"),
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
    except Exception:
        return [servicedesk(bug) for bug in bugs]


def convert(
    *,
    issues: Sequence[dict],
    needs: Sequence[Need] | Literal["all"] = "all",
) -> dict:
    result = {}
    if needs == "all":
        for i in issues:
            result[i["key"]] = i
        return result
    for i in issues:
        result[i["key"]] = {}

    def field(name: Need, method: Callable) -> None:
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
    load_dotenv()
    fix_version = "26.1 Snapshot 10"  # Example value: "26.1 Snapshot 1"

    base_dir = Path(__file__).parent
    config_file = base_dir / os.getenv("CONFIG_FILE", "./config.json")
    config = Config.model_validate(orjson.loads(config_file.read_bytes()))
    output_dir = base_dir / os.getenv("OUTPUT_DIR", "./output")
    output_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger()
    sdjira_portals = config.sdjira_portals
    sdjira_cookies = None

    result = jira_api(
        project="MC",
        # bugs=["MC-4"],
        search='fixVersions in ("{}") AND resolution = "Fixed"'.format(fix_version),
    )
    result = convert(issues=result, needs=("title", "affects_versions"))

    output_dir.joinpath("jira_api.json").write_bytes(
        orjson.dumps(result, option=orjson.OPT_INDENT_2)
    )
