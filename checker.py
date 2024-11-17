import colorlog, logging
import datetime, pytz
import json, re
import os, sys
import requests, urllib.parse
from mwclient import Site

# 配置
lang = "zh"  # lang = "en"
max_retries = 3  # 最大重试次数
level = 0b0111  # 全自动
editextra = 0  # 额外编辑数
nsl = [
    0,
    4,
    9994,
    9996,
    9998,
    10000,
    10002,
    10004,
    10006,
]  # 默认命名空间、Minecraft Wiki命名空间
# 级别：wiki、check、edit
l_wiki = 0b0001  # 获取wiki条目中参考的bug
l_check = 0b0010  # 检查bug模板
l_edit = 0b0100  # 编辑wiki条目


site = Site(f"{lang + '.' if lang != 'en' else ''}minecraft.wiki", path="/")
if level & l_edit:
    password = sys.argv[1]
    # site.login("TeaSummer", password)
    site.login("TeaSummerBot", password)
    editcount1 = len(list(site.usercontributions("TeaSummerBot")))
    isbot = site.username.lower().endswith("bot")
pages = []
for i in range(len(nsl)):
    nsl[i] = str(nsl[i])
for ns in nsl:
    for page in site.search(
        r'insource:/\<ref( *name=".*?")? *>\{\{bug\|([A-Za-z0-9-]+?)(\|[^\<]+?}}\<\/ref>|}}\<\/ref>)|\<ref( *name=".*?")? *>\{\{bug\|[^{}]+\|(title|text|res)=[^}]+?}}\<\/ref>|\<ref( *name=".*?")? *>\{\{bug\|[^}]+?\{(\<\/code>|[^\<])+?}}\<\/ref>/',
        namespace=ns,
    ):
        if ns == 4 and page.get("title").find("/") == -1:
            continue
        pages.append(page.get("title"))
pages = sorted(list(set(pages)))

try:
    os.remove("data.txt")
    os.remove("bugs.txt")
except:
    pass


def get_logger(level=logging.INFO, prefix=True):
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


def wiki(page, isredirect=False):
    def uppercase(match):
        return match.group(0).upper()

    def shorten(p):
        return urllib.parse.unquote(
            re.sub(
                f"^https://({pattern_interwiki})?\\.?" + r"minecraft\.wiki/(w/)?(.)",
                lambda x: (x.group(1) if x.group(1) else "en")
                + f":{x.group(4).upper()}",
                p,
            )
        )

    global retries
    page = page.strip()
    try:
        pattern_interwiki = (
            "(cs|de|el|en|es|fr|hu|id|it|ja|ko|lzh|nl|pl|pt|ru|th|tr|uk|zh)"
        )
        pattern_full = (
            f"^{pattern_interwiki}?\\.?" + r"minecraft\.wiki/(w/)?([:%\.\w\-]+/?)+$"
        )
        page = re.sub(r"^https?://", "", page.replace(" ", "_"))
        custom = re.match(f"^{pattern_interwiki}(?=:)", page)
        l = lang.lower()
        if custom:
            l = custom[0]
        if page == "":
            logger.error(f"({current}/{total}) Invalid wiki page")
            return
        if custom:
            x = re.sub(f"^{l}:", "", page).strip()
            page = f"{l + '.' if l != 'en' else ''}minecraft.wiki/w/" + re.sub(
                "(?<=[:])[a-z]", uppercase, x[0].upper() + x[1:]
            )
        elif not re.match(pattern_full, page):
            page = f"{l + '.' if l != 'en' else ''}minecraft.wiki/w/" + re.sub(
                "(?<=[:])[a-z]", uppercase, page[0].upper() + page[1:]
            )
        page = urllib.parse.quote(urllib.parse.unquote(re.sub(r"[#?].*$", "", page)))
        if re.match(pattern_full, page):
            page = f"https://{page}"
            page_ = re.sub(
                r"minecraft\.wiki/(w/)?(.+)$",
                r"minecraft.wiki/index.php?title=\2&action=raw",
                page,
            )
            o = os.system(f'curl -s "{page_}" > cache.txt')
            with open("cache.txt", encoding="utf-8") as f:
                r = f.read()
            redirect = re.findall(
                r"^#.+?\[\[(.+?)\]\]$", r.strip().split("\n")[0].strip()
            )
            if redirect:
                page_backup = page
                page = redirect[0]
                custom = re.match(f"^{pattern_interwiki}(?=:)", page)
                if custom:
                    l = custom[0]
                page = f"{l + '.' if l != 'en' else ''}minecraft.wiki/w/" + re.sub(
                    f"^{l}:", "", page
                )
                page = f"https://{page}"
                raise NameError
            if r == "":
                raise SyntaxError
            logger.info(f"({current}/{total}) {shorten(page)}")
        else:
            if isredirect:
                pass
            logger.error(f"({current}/{total}) Invalid wiki page")
            return
        bugs = ""
        for ref in re.findall(
            r'(<ref( *name=".*?")? *>\{\{bug\|([A-Za-z0-9-]+)(\|[^<]+?}}</ref>|}}</ref>))',
            r,
            re.I,
        ):
            if ref[0].find("#") == -1:
                bugs += f"{ref[2].strip()}\n"
        # 处理少数情况
        for ref1 in re.findall(
            r'(<ref( *name=".*?")? *>\{\{bug\|[^}]+?\{(</code>|[^<])+?}}</ref>)',
            r,
            re.I,
        ):
            ref1 = list(ref1)
            ref1[0] = (
                ref1[0]
                .replace("{{bug|", "@@@@@A@@@@@")
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
                bugs += f"{bug.strip()}\n"
        for ref1 in re.findall(
            r'(<ref( *name=".*?")? *>\{\{bug\|([A-Za-z0-9-]+)[^{}]*\|(res|text|title)=[^}]+?}}</ref>)',
            r,
            re.I,
        ):
            bug = ref1[2].strip()
            if ref1[0].find("#") == -1:
                bugs += f"{bug.strip()}\n"
        with open("bugs.txt", "a") as f:
            f.write(bugs)
    except Exception as err:
        if type(err) == NameError:
            page = page.replace(" ", "_")
            logger.warning(f"Redirecting {shorten(page_backup)} to {shorten(page)}")
            wiki(page, isredirect=True)
        else:
            retries += 1
            logger.warning(f"Retrying {shorten(page)} - {retries}/{max_retries}")
            if retries >= max_retries:
                logger.error(
                    f"({current}/{total}) Occured when processing {shorten(page)}"
                )
            else:
                wiki(page)


def check(bug):
    global retries
    bug = bug.strip()
    try:
        r = requests.get(
            f"https://bugs.mojang.com/rest/api/2/search?jql=issue%20%3D%20{bug}&fields=resolution"
        ).text.strip()
        r = json.loads(r)
        if r["issues"][0]["fields"]["resolution"]:
            status = "|||" + r["issues"][0]["fields"]["resolution"]["name"]
            status = status.replace("Won&#39;t Fix", "Won't Fix").replace(
                "Works As Intended", "WAI"
            )
            if status == "|||Unresolved":
                status = ""
            with open("data.txt", "a") as f:
                f.write("{{bug|" + bug + status + "}}\n")
            logger.info(f"({current}/{total}) " + "{{bug|" + bug + status + "}}")
        else:
            with open("data.txt", "a") as f:
                f.write("{{bug|" + bug + "}}\n")
            logger.info(f"({current}/{total}) " + "{{bug|" + bug + "}}")
    except Exception as err:
        logger.warning(f"Retrying {bug} - {retries+1}/{max_retries}")
        retries += 1
        if retries >= max_retries:
            logger.error(f"({current}/{total}) Occured when processing {bug}")
        else:
            check(bug)


logger = get_logger()
print()
if level & l_wiki:
    current = 0
    total = len(pages)
    for page in pages:
        retries = 0
        current += 1
        wiki(page)
    with open("bugs.txt") as f:
        r = f.read()
    r = "\n".join(sorted(list(set(r.split("\n")))))
    with open("bugs.txt", "w") as f:
        f.write(r.strip())
    os.remove("cache.txt")
print()
if level & l_check:
    current = 0
    with open("bugs.txt") as f:
        bugs = ";".join(f.read().strip().split())
    total = len(bugs.split(";"))
    for bug in bugs.split(";"):
        retries = 0
        current += 1
        check(bug)
print()
if level & l_edit:
    current = 0
    edittotal = total = len(pages)
    status = "([Ww]on'?t [Ff]ix|WF|WAI|wai|[Ww]orks [Aa]s [Ii]ntended|[Ff]ixed|[Cc]annot [Rr]eproduce|[Aa]waiting [Rr]esponse|[Dd]uplicate|[Ii]ncomplete|[Ii]nvalid|[Rr]esolved|[Uu]nresolved|已修复|不予修复)"
    pattern = r"\|\|?{s}|res={s}".format(s=status)
    with open("data.txt") as f:
        r = f.read()
    start = 1  # 从第N个页面开始编辑
    limit = -1  # 最多编辑N个页面
    if limit == -1:
        limit = total
    maxpage = start - 1 + limit

    def parse():
        global t
        g1 = re.sub(pattern, "", ref[4].strip(), re.I)
        g1 = "{{bug|" + f"{ref[2].strip()}|{g1 if g1 else ''}"
        g2 = g1

        # before
        before = re.search(pattern, ref[4].strip(), re.I)
        if before:
            g1 += before[0].replace("res=", "")
        g1 = g1.removesuffix("|") + "}}"
        # now
        now = re.search(r"bug\|" + ref[2].strip() + r"(\|\|\|[^|}]+)?}}", r, re.I)
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
            f"bug\\|{ref[2].strip()}" + r"\|(\|?)([^|]+)\|\|\|",
            f"bug|{ref[2].strip()}" + r"|\1\2|",
            g2,
        )
        if g2.find("Duplicate") != -1:
            dup = requests.get(
                f"https://bugs.mojang.com/rest/api/2/search?jql=issue%20%3D%20{ref[2].strip()}&fields=issuelinks"
            ).text.strip()
            try:
                dup = str(json.loads(dup)["issues"][0]["fields"]["issuelinks"][0])
                dup = (
                    dup.split("'outwardIssue': {")[1]
                    .split("'self':")[0]
                    .split("'key':")[1]
                    .strip("' ,")
                )
                dup_res = requests.get(
                    f"https://bugs.mojang.com/rest/api/2/search?jql=issue%20%3D%20{dup}&fields=resolution"
                ).text.strip()
                dup_res = json.loads(dup_res)["issues"][0]["fields"]["resolution"][
                    "name"
                ]
                g2 = re.sub(
                    r"\{\{bug\|[A-Za-z0-9-]+\|(.+?)\|(.+?)\|.+?}}",
                    "{{" + f"bug|{dup}|\\1|\\2|{dup_res}" + "}}",
                    g2,
                )
            except:
                logger.error(g2)
                return
        t = t.replace(
            ref[0],
            f"<ref{' ' + ref[1] if ref[1] else ''}>{g2}</ref>".replace("  ", " "),
        )
        g2 = (
            g2.replace("@@@@@L@@@@@", "{")
            .replace("@@@@@R@@@@@", "}")
            .replace("@@@@@S@@@@@", "|")
            .replace("@@@@@M@@@@@", "<code>")
            .replace("@@@@@N@@@@@", "</code>")
        )

        logger = get_logger(logging.INFO, False)
        logger.info(f"== {current}/{total} ==")
        if len(g2) > len(g1):
            logger.info(f"{g1} => {g2}")
        elif g2.count("|") == g1.count("|"):
            logger.warning(f"{g1} => {g2}")
        else:
            logger.error(f"{g1} => {g2}")

    for page in pages[start - 1 : maxpage]:
        retries = 0
        current += 1
        logger = get_logger()
        _page = site.pages[page]
        t = _page.text()
        # 处理少数情况
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
                    r"(@@@@@L@@@@@.*?)\|(.*?@@@@@R@@@@@)", r"\1@@@@@S@@@@@\2", ref1[0]
                )
            if ref1[0].find("#") == -1:
                t = t.replace(z, ref1[0])
        for ref1 in re.findall(
            r'(<ref( *name=".*?")? *>\{\{bug\|([A-Za-z0-9-]+)[^{}]*\|(res|text|title)=[^}]+?}}</ref>)',
            t,
            re.I,
        ):
            r_res, r_text, r_title = (
                re.search(r"\|res=([^|}]+)", ref1[0], re.I),
                re.search(r"\|text=([^|}]+)", ref1[0], re.I),
                re.search(r"\|title=([^|}]+)", ref1[0], re.I),
            )
            if r_res:
                r_res = r_res.group(1)
            if r_text:
                r_text = r_text.group(1)
            if r_title:
                r_title = r_title.group(1)
            bugcode = (
                "{{bug|"
                + f"{ref1[2]}|{r_text if r_text else ''}|{r_title if r_title else ''}|{r_res if r_res else ''}"
                + "}}"
            )
            if ref1[0].find("#") == -1:
                t = t.replace(ref1[0], f"<ref{ref1[1]}>{bugcode}</ref>")
        for ref in re.findall(
            r'(<ref( *name=".*?")? *>\{\{bug\|([A-Z0-9-]+?)(\|([^<]+?)}}</ref>|}}</ref>))',
            t,
            re.I,
        ):
            parse()
        _page.edit(
            t,
            summary=(("机器人：" if isbot else "") + "检查bug模板"),
            minor=True,
            bot=True if isbot else False,
            section=None,
        )

if level & l_edit:
    # 更新日志
    editcount2 = len(list(site.usercontributions("TeaSummerBot")))
    log_page = site.pages["User:TeaSummerBot/Log"]  # 日志页面
    editextra += (log_page.text().count("|-") - 10) * 2  # 需要修改（-N）
    log_time = (
        datetime.datetime.now()
        .replace(tzinfo=pytz.timezone("Asia/Shanghai"))
        .strftime("%Y-%m-%d %H:%M")
    )  # 完成时间（UTC+8）
    log_content = "检查bug模板"  # 内容
    log_scope = f"多个命名空间（{'、'.join(nsl)}）"  # 页面范围
    log_error = 0  # 执行数-意外【预设】
    log_edit = editcount2 - editcount1 - editextra  # 执行数-编辑
    log_total = edittotal  # 执行数-共计
    log_note = "测试性"  # 备注【预设】
    log_about = ""  # 关联话题或请求（如有）【预设】
    if log_note == "" or log_note == "-" or log_note == "—":
        log_note = "&ndash;"
    if log_about == "" or log_about == "-" or log_about == "—":
        log_about = "&ndash;"
    log_addition = f"""
|-
| {log_time}
| {log_content}
| {log_scope}
| {log_error}
| {log_edit}
| {log_total}
| {log_note}
| {log_about}
|}}
    """.strip()
    log_version = log_page.text().replace("|}", log_addition)
    if isbot and log_edit > 0:
        log_page.edit(
            log_version,
            summary="机器人：更新bot日志",
            minor=True,
            bot=True,
            section=None,
        )
    # 更新虫洞
    hole_page = site.pages["User:TeaSummer/Bug_Hole"]  # 虫洞页面
    with open("data.txt") as f:
        hole_bugs = f.read()
    hole_bugs = (
        hole_bugs.replace("{{bug|", "<option>")
        .replace("|||", "|")
        .replace("}}", "</option>")
    )
    hole_version = f"""
<choose uncached>
{hole_bugs}
<choicetemplate>User:TeaSummer/Bug|hole=1</choicetemplate>
</choose><noinclude>{{{{documentation}}}}
</noinclude>
    """.strip()
    if log_edit > 0:
        hole_page.edit(
            hole_version,
            summary=(("机器人：" if isbot else "") + "更新虫洞数据"),
            minor=True,
            bot=True if isbot else False,
            section=None,
        )
