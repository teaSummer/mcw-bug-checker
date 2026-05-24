import copy
import logging
import os
from pathlib import Path
from typing import Any, Literal, Optional, Sequence

import colorlog
from mwclient import Site
from mwclient.page import Page
import mwparserfromhell as mw
from mwparserfromhell.nodes import Tag
import orjson
from pydantic import BaseModel, Field
import requests
from requests.cookies import RequestsCookieJar

type Lang = Literal[
    "de", "en", "es", "fr", "it", "ja", "ko", "lzh", "nl", "pt", "ru", "th", "uk", "zh"
]


class Config(BaseModel):
    class _Site(BaseModel):
        enabled: bool
        namespaces: list[int]
        record_bug_hole: Literal[False] | str
        bug_template: str = "bug"
        read_wiki: bool = True
        check_bug: bool = True
        edit_wiki: bool = True
        search_exclusives: list[str] = Field(default_factory=list)

    class _SaveFor(BaseModel):
        bugs: Path | str = Field(default=Path(__file__).parent / "bugs.txt")
        bug_data: Path | str = Field(default=Path(__file__).parent / "data.json")

    sites: dict[Lang, _Site]
    wiki_bot_username: str
    sdjira_portals: dict[str, int]
    locales: dict[Lang, dict[str, str]]
    as_bot: bool = True
    wiki_useragent: Optional[str] = None
    wiki_bot_password: Optional[str] = None
    sdjira_account_email: Optional[str] = None
    sdjira_account_password: Optional[str] = None
    bugs_size_per_chunk: int = 100
    save_for: _SaveFor = Field(default_factory=_SaveFor)
    max_tries: int = 5


def locale(
    lang: Lang, key: str, *replacements: Any | None, fallback: str | None = None
) -> str:
    value = config.locales.get(lang, {}).get(key)
    if value is None:
        value = config.locales["en"].get(key)
    if value is None:
        if fallback is not None:
            return fallback
        return key
    return value.format(*replacements)


def get_logger(level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger()
    logger.setLevel(level)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    color_formatter = colorlog.ColoredFormatter(
        "%(log_color)s%(levelname)s: %(message)s",
        log_colors={
            "DEBUG": "cyan",
            "INFO": "green",
            "WARNING": "yellow",
            "ERROR": "red",
            "CRITICAL": "bold_red",
        },
    )
    console_handler.setFormatter(color_formatter)
    for handler in logger.handlers:
        logger.removeHandler(handler)
    logger.addHandler(console_handler)
    return logger


def mojira(
    project: str, bugs: Sequence[str], lang: Lang, legacy_mode: bool = False
) -> list[dict]:
    def legacy(bug: str) -> dict:
        response = requests.get(
            f"https://bugs-legacy.mojang.com/rest/api/2/issue/{bug}",
            headers={"Content-Type": "application/json"},
        )
        return response.json()

    def public(bugs: Sequence[str]) -> list[dict]:
        response = requests.post(
            "https://bugs.mojang.com/api/jql-search-post",
            json={
                "advanced": True,
                "project": project,
                "search": f"key in ({','.join(bugs)})",
                "maxResults": config.bugs_size_per_chunk,
            },
            headers={"Content-Type": "application/json"},
        )
        return response.json()["issues"]

    def servicedesk(bug: str) -> dict:
        def authenticate() -> RequestsCookieJar:
            response = requests.post(
                "https://report.bugs.mojang.com/jsd-login/v1/authentication/authenticate",
                json={"email": sdjira_email, "password": sdjira_password},
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
                    "reqDetails": {"key": bug, "portalId": portal},
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

    def run(tries: int = 0) -> list[dict]:
        if legacy_mode:
            return [legacy(bug) for bug in bugs]
        try:
            return public(bugs)
        except Exception:
            if tries >= max_tries:
                logger.error(
                    locale(lang, "log.error.switch_to_single", len(bugs), project)
                )
                return [servicedesk(bug) for bug in bugs]
            logger.warning(
                locale(lang, "log.warning.retry", len(bugs), tries + 1, max_tries)
            )
            return run(tries + 1)

    return run()


def extract_bugs_from_page(wikitext: str, lang: Lang) -> list[str]:
    bugs = []
    code = mw.parse(wikitext)
    for ref in code.filter_tags(matches=lambda tag: tag.tag == "ref"):
        inner_code = copy.deepcopy(ref.contents)
        for template in inner_code.filter_templates():
            if template.name.matches(config.sites[lang].bug_template):
                bug_id = template.get(1).value.strip() if template.has(1) else None
                if bug_id and "#" not in bug_id:
                    bugs.append(bug_id)
    return bugs


def update_ref_bug(
    ref_node: Tag, bug_id: str, status: str | None, lang: Lang, pagename: str = ""
):
    inner_code = copy.deepcopy(ref_node.contents)
    updated = False

    for template in inner_code.filter_templates():
        if (
            template.name.matches(config.sites[lang].bug_template)
            and template.has(1)
            and template.get(1).value.strip() == bug_id
        ):
            if template.has(4):
                template.remove(4)
            if template.has("res"):
                template.remove("res")
            if status == "Duplicate":
                # 处理重复项，替换为目标漏洞
                try:
                    dup_data = mojira(bug_id.split("-")[0], [bug_id], lang)[0]
                    dup_key = dup_data["fields"]["issuelinks"][0]["outwardIssue"]["key"]
                    dup_res_data = mojira(dup_key.split("-")[0], [dup_key], lang)[0][
                        "fields"
                    ]["resolution"]
                    dup_res = dup_res_data["name"] if dup_res_data else None
                    # 修改模板参数
                    template.add(1, dup_key)
                    if dup_res:
                        template.add(4, dup_res)
                    updated = True
                except Exception:
                    logger.error(
                        locale(lang, "log.error.no_duplicates", bug_id, pagename)
                    )
                    return False
            else:
                if status is not None:
                    template.add(4, status)
                updated = True
            break

    if updated:
        new_ref_content = inner_code.strip()
        ref_node.contents = new_ref_content
        return True
    return False


def main(lang: Lang) -> None:
    site = Site(
        f"{lang + '.' if lang != 'en' else ''}minecraft.wiki",
        path="/",
        clients_useragent=config.wiki_useragent,
    )
    logger = get_logger()
    if config.sites[lang].edit_wiki or config.sites[lang].record_bug_hole:
        bot_password = config.wiki_bot_password or os.getenv("WIKI_BOT_PASSWORD")
        if not config.wiki_bot_username or not bot_password:
            raise ValueError(locale(lang, "log.error.bot_config"))
        site.clientlogin(username=config.wiki_bot_username, password=bot_password)
        site.site_init()

    # 搜索
    pages = []
    conditions = [
        r'insource:/\<ref( *name=".*?")? *> *\{\{%(bug)s\|([A-Za-z0-9-]+?)(\|[^\<]+?}} *\<\/ref>|}} *\<\/ref>)|\<ref( *name=".*?")? *> *\{\{%(bug)s\|[^{}]+\|(res|text|title|[2-4])=[^}]+?}} *\<\/ref>|\<ref( *name=".*?")? *> *\{\{%(bug)s\|[^}]+?\{(\<\/code>|[^\<])+?}} *\<\/ref>/'
        % {"bug": config.sites[lang].bug_template}
    ]
    conditions.extend(config.sites[lang].search_exclusives)
    for ns in config.sites[lang].namespaces:
        for page in site.search(" -".join(conditions), namespace=str(ns)):
            pages.append(page.get("title"))
    pages = sorted(set(pages))

    def wiki(pagename: str) -> list[str]:
        nonlocal tries
        pagename = pagename.strip()
        if not pagename:
            logger.error(
                locale(lang, "log.error.no_wiki_page", current, total, pagename)
            )
            return []
        try:
            page = site.pages[pagename]
            if not page.exists:
                logger.error(
                    locale(lang, "log.error.no_wiki_page", current, total, pagename)
                )
                return []
            r = page.resolve_redirect().text()
            logger.info(locale(lang, "log.info", current, total, pagename))
            return extract_bugs_from_page(r, lang)
        except Exception:
            tries += 1
            logger.warning(
                locale(lang, "log.warning.retry", pagename, tries, max_tries)
            )
            if tries >= max_tries:
                logger.error(locale(lang, "log.error", current, total, pagename))
                return []
            return wiki(pagename)

    def check(project: str, bugs: Sequence[str]) -> list[tuple[str, str]]:
        nonlocal current, tries
        key = project
        try:
            if project not in config.sdjira_portals:
                tries = max_tries
                current += len(bugs)
                raise AssertionError
            results = mojira(project, bugs, lang)
            out = []
            for bug in results:
                current += 1
                key = bug["key"]
                resolution = bug["fields"].get("resolution")
                status = None
                if resolution and resolution.get("name") != "Unresolved":
                    status = (
                        resolution["name"]
                        .replace("Won&#39;t Fix", "Won't Fix")
                        .replace("Works As Intended", "WAI")
                    )
                out.append((key, status))
                logger.info(
                    locale(
                        lang,
                        "log.info",
                        current,
                        total,
                        "{{%s|%s}}"
                        % (
                            config.sites[lang].bug_template,
                            f"{key}|||{status}" if status else key,
                        ),
                    )
                )
            return out
        except Exception:
            logger.warning(locale(lang, "log.warning.retry", key, tries + 1, max_tries))
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
        all_bugs = []
        for page in pages:
            tries = 0
            current += 1
            all_bugs.extend(wiki(page))
        bugs_file.write_text("\n".join(sorted(set(all_bugs))))

    print()
    if config.sites[lang].check_bug:
        current = last = 0
        bugs_raw = bugs_file.read_text().strip().split()
        total = len(bugs_raw)
        projects = {}
        for bug in bugs_raw:
            p = bug.split("-")[0]
            projects.setdefault(p, [[]])
            if len(projects[p][-1]) >= config.bugs_size_per_chunk:
                projects[p].append([])
            projects[p][-1].append(bug.strip())

        bug_data_list = []
        for project, chunks in projects.items():
            for chunk in chunks:
                tries = 0
                bug_data_list.extend(check(project, chunk))
                last = current

        bug_data = {}
        for key, status in bug_data_list:
            bug_data[key] = status
        bug_data_file.write_bytes(orjson.dumps(bug_data, option=orjson.OPT_INDENT_2))

    print()
    if config.sites[lang].edit_wiki:
        current = 0
        total = len(pages)
        status_map = orjson.loads(bug_data_file.read_bytes())

        start = 1
        limit = -1
        if limit == -1:
            limit = total
        max_page = start - 1 + limit

        for pagename in pages[start - 1 : max_page]:
            current += 1
            tries = 0
            page = site.pages[pagename]
            original_text = page.text()
            if not original_text:
                continue

            code = mw.parse(original_text)
            modified = False

            if code.filter_comments() or code.filter_templates(
                matches=lambda template: template.name.matches("void")
            ):
                continue

            for ref in code.filter_tags(matches=lambda tag: tag.tag == "ref"):
                # 提取漏洞编号
                inner_code = copy.deepcopy(ref.contents)
                for template in inner_code.filter_templates():
                    if template.name.matches(
                        config.sites[lang].bug_template
                    ) and template.has(1):
                        bug_id = template.get(1).value.strip()
                        if bug_id in status_map:
                            status = status_map[bug_id]
                            if update_ref_bug(ref, bug_id, status, lang, pagename):
                                modified = True

            if modified:
                new_text = str(code)
                page.edit(
                    new_text,
                    summary=locale(
                        lang,
                        "summary.bot" if config.as_bot else "summary.human",
                        locale(lang, "summary.message.edit"),
                    ),
                    minor=False,
                    bot=config.as_bot,
                )
                logger.info(locale(lang, "log.info", current, total, pagename))
                continue
            logger.warning(locale(lang, "log.info", current, total, pagename))

    if config.sites[lang].record_bug_hole:
        hole_page = site.pages[config.sites[lang].record_bug_hole]
        bug_data = orjson.loads(bug_data_file.read_bytes())
        hole_bugs = ""
        for key, value in bug_data.items():
            hole_bugs = hole_bugs + f"<option>{key}|{value}</option>\n"
        hole_version = f"""
<choose {{{{{{#if:{{{{{{uncached|}}}}}}|uncached|}}}}}}>
{hole_bugs.strip()}
<choicetemplate>User:TeaSummer/Bug|hole=1</choicetemplate>
</choose><noinclude>{{{{documentation}}}}
</noinclude>""".strip()
        hole_page.edit(
            hole_version,
            summary=locale(
                lang,
                "summary.bot" if config.as_bot else "summary.human",
                locale(
                    lang,
                    "summary.message.edit.hole_page",
                    fallback=locale(lang, "summary.message.edit"),
                ),
            ),
            minor=False,
            bot=config.as_bot,
        )


if __name__ == "__main__":
    base_dir = Path(__file__).parent
    config = Config.model_validate(
        orjson.loads(base_dir.joinpath("config.json").read_bytes())
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
