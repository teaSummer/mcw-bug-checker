from dacite import from_dict
from dataclasses import dataclass, field
from mwclient import Site
from pathlib import Path
from typing import Any, Iterable, Literal
import colorlog
import logging
import regex as re
import orjson
import os
import requests

base_dir = Path(__file__).parent
_Lang = Literal[
    "de",
    "en",
    "es",
    "fr",
    "it",
    "ja",
    "ko",
    "lzh",
    "nl",
    "pt",
    "ru",
    "th",
    "uk",
    "zh",
]


@dataclass
class Config:
    @dataclass
    class _Site:
        enabled: bool
        namespaces: list[int]
        record_bug_hole: Literal[False] | str
        bug_template: str = "bug"
        read_wiki: bool = True
        check_bug: bool = True
        edit_wiki: bool = True
        search_exclusives: list[str] = field(default_factory=list)

    @dataclass
    class _SaveFor:
        bugs: Path | str = base_dir / "bugs.txt"
        bug_data: Path | str = base_dir / "data.txt"

    sites: dict[_Lang, _Site]
    wiki_bot_username: str
    sdjira_portals: dict[str, int]
    locales: dict[_Lang, dict[str, str]]
    wiki_useragent: str | None = None
    wiki_bot_password: str | None = None
    sdjira_account_email: str | None = None
    sdjira_account_password: str | None = None
    bugs_size_per_chunk: int = 200
    save_for: _SaveFor = field(default_factory=_SaveFor)
    max_tries: int = 5


def locale(lang: _Lang, key: str, *replacements: Any | None) -> str:
    value = config.locales.get(lang, {}).get(key)
    if value is None:
        value = config.locales["en"][key]
    return value.format(*replacements)


def get_logger(level: int = logging.INFO, prefix: bool = True) -> logging.Logger:
    logger = logging.getLogger()
    logger.setLevel(level)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    color_formatter1 = colorlog.ColoredFormatter(
        "%(log_color)s%(levelname)s: %(message)s",
        log_colors={
            "DEBUG": "cyan",
            "INFO": "green",
            "WARNING": "yellow",
            "ERROR": "red",
            "CRITICAL": "bold_red",
        },
    )
    color_formatter2 = colorlog.ColoredFormatter(
        "%(log_color)sINFO: %(message)s",
        log_colors={
            "DEBUG": "cyan",
            "INFO": "green",
            "WARNING": "yellow",
            "ERROR": "red",
            "CRITICAL": "bold_red",
        },
    )
    console_handler.setFormatter(color_formatter1 if prefix else color_formatter2)
    for handler in logger.handlers:
        logger.removeHandler(handler)
    logger.addHandler(console_handler)
    return logger


def I(string: str) -> str:
    def r(match: re.Match) -> str:
        return "[" + match.group(1).upper() + match.group(1).lower() + "]"

    return re.sub("([a-zA-Z])", r, string)


def mojira(
    project: str, bugs: Iterable[str], legacy_mode: bool = False, tries: int = 0
):
    def legacy(bug: str) -> dict:
        response = requests.get(
            f"https://bugs-legacy.mojang.com/rest/api/2/issue/{bug}",
            headers={"Content-Type": "application/json"},
        )
        return response.json()

    def public(bugs: Iterable[str]) -> list[dict]:
        response = requests.post(
            "https://bugs.mojang.com/api/jql-search-post",
            json={
                "advanced": True,
                "project": project,
                "search": f"key in ({",".join(bugs)})",
                "maxResults": config.bugs_size_per_chunk,
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
        portal = config.sdjira_portals[bug.split("-")[0]]
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
        if tries >= max_tries:
            logger.error(locale(lang, "log.error.switch_to_single", len(bugs), project))
            return [servicedesk(bug) for bug in bugs]
        logger.warning(
            locale(lang, "log.warning.retry", len(bugs), tries + 1, max_tries)
        )
        return mojira(project, bugs, tries=tries + 1)


def main(lang):
    site = Site(
        f"{lang + '.' if lang != 'en' else ''}minecraft.wiki",
        path="/",
        clients_useragent=config.wiki_useragent,
    )
    logger = get_logger()
    if config.sites[lang].edit_wiki:
        bot_password = config.wiki_bot_password or os.getenv("WIKI_BOT_PASSWORD")
        if not config.wiki_bot_username or not bot_password:
            raise ValueError(locale(lang, "log.error.bot_config"))
        # site.clientlogin(username="", password="")
        site.clientlogin(username=config.wiki_bot_username, password=bot_password)
        isbot = site.username.lower().endswith("bot")
    pages = []
    nsl = config.sites[lang].namespaces
    conditions = [
        r'insource:/\<ref( *name=".*?")? *>\{\{bug\|([A-Za-z0-9-]+?)(\|[^\<]+?}}\<\/ref>|}}\<\/ref>)|\<ref( *name=".*?")? *>\{\{bug\|[^{}]+\|(res|text|title|[2-4])=[^}]+?}}\<\/ref>|\<ref( *name=".*?")? *>\{\{bug\|[^}]+?\{(\<\/code>|[^\<])+?}}\<\/ref>/'
    ]
    conditions.extend(config.sites[lang].search_exclusives)
    for i in range(len(nsl)):
        nsl[i] = str(nsl[i])
    for ns in nsl:
        for page in site.search(
            " -".join(conditions),
            namespace=ns,
        ):
            if ns == 4 and page.get("title").find("/") == -1:
                continue
            pages.append(page.get("title"))
    pages = sorted(list(set(pages)))

    def wiki(page_title: str) -> list[str]:
        nonlocal tries
        page_title = page_title.strip()
        if page_title == "":
            logger.error(locale(lang, "log.error.no_wiki_page", current, total))
            return []
        try:
            page = site.pages[page_title]
            if page.exists:
                r = page.text()
                redirect = re.findall(
                    r"^#.+?\[\[(.+?)\]\]$", r.strip().split("\n")[0].strip()
                )
                if redirect:
                    page_backup = page_title
                    page_title = redirect[0]
                    assert False
                if r == "":
                    raise SyntaxError
                logger.info(locale(lang, "log.info", current, total, page_title))
            else:
                logger.error(locale(lang, "log.error.no_wiki_page", current, total))
                return []
            bugs = []
            for ref in re.findall(
                r'(<ref( *name=".*?")? *>\{\{bug\|([A-Za-z0-9-]+)(\|[^<]+?}}</ref>|}}</ref>))',
                r,
                re.I,
            ):
                if ref[0].find("#") == -1:
                    bugs.append(ref[2].strip())
            for ref1 in re.findall(
                r'(<ref( *name=".*?")? *>\{\{bug\|[^}]+?\{(</code>|[^<])+?}}</ref>)',
                r,
                re.I,
            ):
                ref1 = list(ref1)
                ref1[0] = (
                    ref1[0]
                    .replace("{{bug|", "@@@@@A@@@@@")
                    .replace("{{Bug|", "@@@@@A@@@@@")
                    .replace("}}</ref>", "@@@@@B@@@@@")
                    .replace("{", "@@@@@L@@@@@")
                    .replace("}", "@@@@@R@@@@@")
                    .replace("@@@@@A@@@@@", "{{bug|")
                    .replace("@@@@@B@@@@@", "}}</ref>")
                    .replace("<code>", "@@@@@M@@@@@")
                    .replace("</code>", "@@@@@N@@@@@")
                )
                bug, _ = re.search(
                    r"\{\{bug\|([A-Za-z0-9-]+)\|([^}]+)}}</ref>",
                    ref1[0],
                    re.I,
                ).groups()
                if ref1[0].find("#") == -1:
                    bugs.append(bug.strip())
            for ref1 in re.findall(
                r'(<ref( *name=".*?")? *>\{\{bug\|([A-Za-z0-9-]+)[^{}]*\|(res|text|title|[2-4])=[^}]+?}}</ref>)',
                r,
                re.I,
            ):
                bug = ref1[2].strip()
                if ref1[0].find("#") == -1:
                    bugs.append(bug)
            return bugs
        except Exception as err:
            if type(err) == AssertionError:
                logger.warning(
                    locale(lang, "log.warning.redirect", page_backup, page_title)
                )
                return wiki(page_title)
            else:
                tries += 1
                logger.warning(
                    locale(lang, "log.warning.retry", page_title, tries, max_tries)
                )
                if tries >= max_tries:
                    logger.error(locale(lang, "log.error", current, total, page_title))
                    return []
                return wiki(page_title)

    def check(project: str, bugs: Iterable[str]) -> list[tuple[str, str]]:
        nonlocal current, tries
        try:
            key = project
            if project not in config.sdjira_portals.keys():
                tries = max_tries
                current += len(bugs)
                assert False
            t = []
            r = mojira(project, bugs)
            for bug in r:
                current += 1
                key = bug["key"]
                status = None
                if (
                    bug["fields"]["resolution"]
                    and bug["fields"]["resolution"]["name"] != "Unresolved"
                ):
                    status = (
                        bug["fields"]["resolution"]["name"]
                        .replace("Won&#39;t Fix", "Won't Fix")
                        .replace("Works As Intended", "WAI")
                    )
                t.append((key, status))
                if status is None:
                    logger.info(
                        locale(lang, "log.info", current, total, "{{bug|" + key + "}}")
                    )
                    continue
                logger.info(
                    locale(
                        lang,
                        "log.info",
                        current,
                        total,
                        "{{bug|" + key + "|||" + status + "}}",
                    )
                )
            return t
        except Exception as err:
            if type(err) != AssertionError:
                logger.warning(
                    locale(lang, "log.warning.retry", key, tries + 1, max_tries)
                )
                current = last
            tries += 1
            if tries >= max_tries:
                logger.error(locale(lang, "log.error", current, total, key))
                return []
            return check(project, bugs)

    print()
    if config.sites[lang].read_wiki:
        current = 0
        total = len(pages)
        bugs = []
        for page in pages:
            tries = 0
            current += 1
            bugs.extend(wiki(page))
        bugs_file.write_text("\n".join(sorted(list(set(bugs)))))
    print()
    if config.sites[lang].check_bug:
        current = last = 0
        bugs = ";".join(bugs_file.read_text().strip().split())
        total = len(bugs.split(";"))
        projects = {}
        for bug in bugs.split(";"):
            p = bug.split("-")[0]
            if p not in projects:
                projects[p] = [[]]
            if len(projects[p][-1]) >= config.bugs_size_per_chunk:
                projects[p].append([])
            projects[p][-1].append(bug.strip())
        bug_data = []
        for project, total_bugs in projects.items():
            for bugs in total_bugs:
                tries = 0
                bug_data.extend(check(project, bugs))
                last = current
        data_write = ""
        for bug in bug_data:
            if bug[1] is None:
                data_write += "{{bug|" + bug[0] + "}}\n"
                continue
            data_write += "{{bug|" + bug[0] + "|||" + bug[1] + "}}\n"
        bug_data_file.write_text(data_write.strip())
    print()
    if config.sites[lang].edit_wiki:
        current = 0
        total = len(pages)
        status_pattern = I(
            "(Won'?t Fix|Works As Intended|Fixed|Cannot Reproduce|Awaiting Response|Duplicate|Incomplete|Invalid|Resolved|Unresolved|Won'?t Do|Done|未修复|已修复|不予修复|重复报告|报告不完整|有意为之|无法复现|无效|等待回应|AR|CR|F|INC|INV|U|WAI|WF|D|CNR|WD)"
        )
        pattern = r"\|\|?{s}|res={s}[|}}]".format(s=status_pattern)
        r = bug_data_file.read_text()
        start = 1
        limit = -1
        if limit == -1:
            limit = total
        max_page = start - 1 + limit

        def parse() -> None:
            nonlocal t
            g1 = re.sub(pattern, "", ref[4].strip())
            g1 = "{{bug|" + f"{ref[2].strip()}|{g1 if g1 else ''}"
            g2 = g1
            # BEFORE
            before = re.search(pattern, ref[4].strip())
            if before:
                g1 += before[0].replace("res=", "")
            g1 = g1.strip("|") + "}}"
            # NOW
            now = re.search(r"bug\|" + ref[2].strip() + r"(\|\|\|[^|}]+)?}}", r, re.I)
            if not now:
                t = (
                    t.replace("@@@@@L@@@@@", "{")
                    .replace("@@@@@R@@@@@", "}")
                    .replace("@@@@@S@@@@@", "|")
                    .replace("@@@@@M@@@@@", "<code>")
                    .replace("@@@@@N@@@@@", "</code>")
                )
                return
            if g2.endswith("|"):
                g2 += "|"
            if now and now[1]:
                g2 += "|" + now[1].removeprefix("|||")
            g2 = g2.strip("|") + "}}"
            if g2.count("|") == 3 and g2.find("||") == -1:
                g2 = g2.replace("|", "||").replace(
                    f"|{ref[2].strip()}||", f"{ref[2].strip()}|"
                )
            g2 = g2.replace("|}}", "}}")
            g2 = re.sub(
                f"[Bb]ug\\|{ref[2].strip()}" + r"\|(\|?)([^|]+)\|\|\|",
                f"bug|{ref[2].strip()}" + r"|\1\2|",
                g2,
            )
            if g2.find("Duplicate") != -1:
                dup = mojira(ref[2].split("-")[0], [ref[2].strip()])[0]
                try:
                    dup = dup["fields"]["issuelinks"][0]["outwardIssue"]["key"]
                    dup_res = mojira(dup.split("-")[0], [dup])[0]["fields"][
                        "resolution"
                    ]
                    if dup_res:
                        dup_res = "|" + dup_res["name"]
                    g2 = re.sub(
                        r"\{\{[Bb]ug\|[A-Za-z0-9-]+\|(.*?)\|(.*?)\|.*?}}",
                        "{{" + f"bug|{dup}|\\1|\\2{dup_res or ""}",
                        g2,
                    )
                    g2 = g2.strip("|") + "}}"
                except:
                    logger = get_logger()
                    logger.error(g2)
                    return
            g2 = (
                g2.replace("@@@@@L@@@@@", "{")
                .replace("@@@@@R@@@@@", "}")
                .replace("@@@@@S@@@@@", "|")
                .replace("@@@@@M@@@@@", "<code>")
                .replace("@@@@@N@@@@@", "</code>")
            )
            if ref[0].find("|archive=") == -1:
                t = t.replace(
                    ref[0],
                    f"<ref{' ' + ref[1] if ref[1] else ''}>{g2}</ref>".replace(
                        "  ", " "
                    ),
                )

            logger = get_logger(logging.INFO, False)
            logger.info(f"== {current}/{total} ==")
            if len(g2) > len(g1):
                logger.info(f"{g1} => {g2}")
            elif g2.count("|") == g1.count("|"):
                logger.warning(f"{g1} => {g2}")
            else:
                logger.error(f"{g1} => {g2}")

        for page in pages[start - 1 : max_page]:
            tries = 0
            current += 1
            logger = get_logger()
            _page = site.pages[page]
            t = _page.text()
            t_bak = t
            for ref1 in re.findall(
                r'(<ref( *name=".*?")? *>\{\{bug\|[^}]+?\{(</code>|[^<])+?}}</ref>)',
                t,
                re.I,
            ):
                z = ref1[0]
                ref1 = list(ref1)
                ref1[0] = (
                    ref1[0]
                    .replace("{{bug|", "@@@@@A@@@@@")
                    .replace("{{Bug|", "@@@@@A@@@@@")
                    .replace("}}</ref>", "@@@@@B@@@@@")
                    .replace("{", "@@@@@L@@@@@")
                    .replace("}", "@@@@@R@@@@@")
                    .replace("@@@@@A@@@@@", "{{bug|")
                    .replace("@@@@@B@@@@@", "}}</ref>")
                    .replace("<code>", "@@@@@M@@@@@")
                    .replace("</code>", "@@@@@N@@@@@")
                )
                while len(re.findall(r"(@@@@@L@@@@@.*?)\|(.*?@@@@@R@@@@@)", ref1[0])):
                    ref1[0] = re.sub(
                        r"(@@@@@L@@@@@.*?)\|(.*?@@@@@R@@@@@)",
                        r"\1@@@@@S@@@@@\2",
                        ref1[0],
                    )
                if ref1[0].find("#") == -1 and ref1[0].find("|archive=") == -1:
                    t = t.replace(z, ref1[0])
            for ref1 in re.findall(
                r'(<ref( *name=".*?")? *>\{\{bug\|([A-Za-z0-9-]+)[^{}]*\|(res|text|title|[2-4])=[^}]+?}}</ref>)',
                t,
                re.I,
            ):
                r_res, r_text, r_title = (
                    re.search(r"\|res=([^|}]+)", ref1[0], re.I)
                    or re.search(r"\|4=([^|}]+)", ref1[0], re.I),
                    re.search(r"\|text=([^|}]+)", ref1[0], re.I)
                    or re.search(r"\|2=([^|}]+)", ref1[0], re.I),
                    re.search(r"\|title=([^|}]+)", ref1[0], re.I)
                    or re.search(r"\|3=([^|}]+)", ref1[0], re.I),
                )
                if r_res:
                    r_res = r_res.group(1)
                if r_text:
                    r_text = r_text.group(1)
                if r_title:
                    r_title = r_title.group(1)
                bug_code = (
                    "{{bug|"
                    + f"{ref1[2]}|{r_text if r_text else ''}|{r_title if r_title else ''}|{r_res if r_res else ''}"
                    + "}}"
                )
                if ref1[0].find("#") == -1 and ref1[0].find("|archive=") == -1:
                    t = t.replace(ref1[0], f"<ref{ref1[1]}>{bug_code}</ref>")
            for ref in re.findall(
                r'(<ref( *name=".*?")? *>\{\{bug\|([A-Z0-9-]+?)(\|([^<]+?)}}</ref>|}}</ref>))',
                t,
                re.I,
            ):
                parse()
            if t == t_bak:
                continue
            _page.edit(
                t,
                summary=(
                    locale(lang, "summary.bot", locale(lang, "summary.message.edit"))
                    if isbot
                    else locale(
                        lang, "summary.human", locale(lang, "summary.message.edit")
                    )
                ),
                minor=True,
                bot=True if isbot else False,
                section=None,
            )

    if config.sites[lang].record_bug_hole:
        hole_page = site.pages[config.sites[lang].record_bug_hole]
        hole_bugs = bug_data_file.read_text()
        hole_bugs = (
            hole_bugs.replace("{{bug|", "<option>")
            .replace("{{Bug|", "<option>")
            .replace("|||", "|")
            .replace("}}", "</option>")
        )
        hole_version = f"""
    <choose {{{{#if:{{{{{{uncached|}}}}}}|uncached|}}}}>
    {hole_bugs}
    <choicetemplate>User:TeaSummer/Bug|hole=1</choicetemplate>
    </choose><noinclude>{{{{documentation}}}}
    </noinclude>
        """.strip()
        hole_page.edit(
            hole_version,
            summary=(
                locale(
                    lang,
                    "summary.bot",
                    locale(lang, "summary.message.edit.hole_page")
                    or locale(lang, "summary.message.edit"),
                )
                if isbot
                else locale(
                    lang,
                    "summary.human",
                    locale(lang, "summary.message.edit.hole_page")
                    or locale(lang, "summary.message.edit"),
                )
            ),
            minor=True,
            bot=True if isbot else False,
            section=None,
        )


if __name__ == "__main__":
    config = from_dict(
        data_class=Config,
        data=orjson.loads(base_dir.joinpath("config.json").read_bytes()),
    )
    bugs_file = base_dir / config.save_for.bugs
    bug_data_file = base_dir / config.save_for.bug_data
    max_tries = config.max_tries

    sdjira_email = config.sdjira_account_email or os.getenv("SDJIRA_EMAIL")
    sdjira_password = config.sdjira_account_password or os.getenv("SDJIRA_PASSWORD")
    sdjira_cookies = None

    bugs_file.parent.mkdir(parents=True, exist_ok=True)
    bug_data_file.parent.mkdir(parents=True, exist_ok=True)
    bugs_file.unlink(missing_ok=True)
    bug_data_file.unlink(missing_ok=True)

    for lang in config.sites:
        logger = get_logger()
        logger.info(locale(lang, "log.info.start", lang))
        if not config.sites[lang].enabled:
            logger.warning(locale(lang, "log.warning.skip"))
            continue
        main(lang)
