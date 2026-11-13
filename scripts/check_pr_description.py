"""Check a pull request's description against the template (.forgejo/pull_request_template.md, CONTRIBUTING.md
"Commits and pull requests"):

  - Why, What changed, Flags and settings, Measurements, Not tested and Risk and rollback there, filled in, none of the
    template's <...> stand-ins left;
  - Flags and settings: "None", or a table with a filled-in row per flag; every RX_*, HOS_* or HOOKSHOT_* name the
    diff adds is listed there;
  - Measurements: a table whose columns each name what they measured with its hash, never a bare Before and After:
    one the change ("this PR @ <hash>", or a named build that carries it), one the baseline ("main @ <hash>", or a
    named build). main against this PR needs nothing more; anything else says which column is the baseline in a
    "Baseline: <its column>" line. A main or this PR column comes with its "main: <hash> (<date>)" or "this PR:
    <branch> @ <hash>" line, on the same commit; and a row of values;
  - every box of "For everyone" ticked. The "If applicable" boxes may stay empty.

Warnings, which do not fail it: a diff that changes the menu's code while Flags and settings says None, and a PR
rebased onto a newer main than the one measured.

A pull request still in work in progress (a title starting with WIP: or [WIP], Forgejo's draft mark, or the API's
draft) is not checked: it passes with a notice listing what is left. The check applies once it is marked ready for
review; forge.py pr ready runs the same check before it takes the WIP: off.

    python -I scripts/check_pr_description.py --file body.md [--diff pr.diff] [--wip]
    python -I scripts/check_pr_description.py --pr 12 [--repo MagicBeanDev/Hookshot] [--server https://git.magicbean.dev]

--pr reads the pull request and its diff from the Forgejo API, with the token in FORGEJO_TOKEN or GITHUB_TOKEN when one
is set (a public repository needs none). Exit status 0 when it passes, 1 with the problems listed when it does not.
Standard library only; .forgejo/workflows/pr-description.yml runs it on every pull request.
"""
import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[1] / ".forgejo" / "pull_request_template.md"
# Forgejo's default WORK_IN_PROGRESS_PREFIXES ([repository.pull-request] in app.ini), matched at the start of the title
# whatever the case. The first is the one forge.py adds.
WIP_PREFIXES = ("WIP:", "[WIP]")
NEEDS_MEASUREMENTS = "needs measurements"   # (the label of a PR sent back to WIP for its measurements)
# The sections a description fills in; Screenshots, Reviewer notes and Issues may stay empty.
REQUIRED = ("Why", "What changed", "Flags and settings", "Measurements", "Not tested", "Risk and rollback")
FLAGS = "Flags and settings"
COMMIT = re.compile(r"^[0-9a-f]{7,40}$")
STAND_IN = re.compile(r"<[^<>\n!]+>")   # (the template's <...> stand-ins, read from the template itself)
# A measurement column: "main @ 7afe6ae1", "this PR @ 992a2420, RX_VK_X=1", or a build named by its tag,
# "nightly-123-abcd1234"
AT_COMMIT = re.compile(r"^(?P<name>[^@]+?)\s*@\s*(?P<hash>[^\s,;()]+)")
TAGGED = re.compile(r"^(?P<name>[A-Za-z][\w.]*)-(?:\d+-)?(?P<hash>[0-9a-f]{7,40})\b")
DELTA = re.compile(r"^(Δ|delta|diff|change)\b", re.I)
# Switches the game, the build or a script reads: a quoted name added in the diff (docs/development.md, "Switches for
# testing and debugging"). Menu rows and ini keys are not spotted that surely: a change to the menu's code is a warning.
SWITCH = re.compile(r"[\"'`]((?:RX|HOS|HOOKSHOT)_[A-Z0-9_]+)[\"'`]")
MENU_CODE = ("gpu/vk/vk_menu.cpp",)


def without_comments(text):
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def sections(body, level="##"):
    """{heading: text} for the headings of that level (the text up to the next heading of that level or higher)"""
    out, name = {}, None
    stop = re.compile(r"^#{1,%d} " % len(level))
    for line in body.splitlines():
        m = re.match(r"^%s (.+?)\s*#*\s*$" % level, line)
        if m:
            name = m.group(1).strip()
            out[name] = ""
        elif name is not None and stop.match(line):
            name = None
        elif name is not None:
            out[name] += line + "\n"
    return out


def boxes(text):
    """[(ticked, the item's text)] for the task list items in text"""
    return [(m.group(1).lower() == "x", m.group(2).strip()) for m in re.finditer(r"^\s*[-*] \[([ xX])\] (.+)$", text, re.M)]


def key(item):
    """an item's first words, lower case and without marks: the same item when someone ticks it, reflows it or adds a note"""
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", item.lower()).split()[:4])


def cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def tables(text):
    """[(header cells, [row cells])] for the Markdown tables in text, outside code blocks"""
    text = re.sub(r"^```.*?^```", "", text, flags=re.S | re.M)
    lines = [ln.strip() for ln in text.splitlines()]
    out = []
    for i, ln in enumerate(lines[:-1]):
        if ln.startswith("|") and re.match(r"^\|[\s:|-]+$", lines[i + 1]) and "-" in lines[i + 1]:
            rows = []
            for row in lines[i + 2:]:
                if not row.startswith("|"):
                    break
                rows.append(cells(row))
            out.append((cells(ln), rows))
    return out


def for_everyone(body):
    """the text of ### For everyone under ## Checklist, None when there is none"""
    return sections(sections(body).get("Checklist", ""), "###").get("For everyone")


def column(cell):
    """(name, hash) of a measurement column, or None when it names no commit"""
    m = AT_COMMIT.match(cell) or TAGGED.match(cell)
    return (m.group("name").strip(), m.group("hash")) if m else None


def norm(text):
    return " ".join(text.split()).lower()


def measurement_problems(text):
    """One column is the change (this PR @ <hash>, or a named build that carries it), one a labelled baseline (main @
    <hash>, or a named build with its hash); main against this PR needs nothing more, anything else a Baseline: line"""
    found = tables(text)
    if not found:
        return ["## Measurements has no table: | | main @ <hash> | this PR @ <hash> |"]
    header, rows = found[0]
    values = [i for i, c in enumerate(header) if i and c and not DELTA.match(c)]
    if any(header[i].strip("* ").lower() in ("before", "after") for i in values):
        return ["## Measurements' columns are a bare Before and After: name them 'main @ <hash>' and 'this PR @ <hash>', "
                "or what else was compared, with its hash"]
    out, cols = [], []
    for i in values:
        c = column(header[i])
        if c is None:
            out.append("## Measurements' column '%s' names no commit: 'main @ <hash>', 'this PR @ <hash>', or what "
                       "else was compared, with its hash" % header[i])
        elif not COMMIT.match(c[1]):
            out.append("## Measurements' column '%s': '%s' is not a commit (7 to 40 hex characters)" % (header[i], c[1]))
        elif c[0].lower().startswith("this pr") and c[0].lower() != "this pr":
            out.append("## Measurements' column '%s': this PR's column is 'this PR @ <hash>', with anything more after "
                       "the hash ('this PR @ <hash>, RX_VK_X=1'); another build is named by itself" % header[i])
        else:
            cols.append((c[0].lower(), c[1], norm(header[i])))
    if not out and len(cols) < 2:
        out.append("## Measurements' table has nothing to compare with: add 'main @ <hash>' (or the baseline it was "
                   "compared with, named, with its hash)")
    elif not out:
        said = re.search(r"^\W*baseline:\s*(.+)$", text, re.M | re.I)
        names = [n for n, _, _ in cols]
        if said:
            line = norm(said.group(1))
            hits = [c for c in cols if c[2] in line]
            if not hits:   # (the column by its name and hash only, when that is enough to tell it)
                hits = [c for c in cols if "%s @ %s" % (c[0], c[1]) in line
                        and sum(d[:2] == c[:2] for d in cols) == 1]
            if len(hits) != 1:
                out.append("## Measurements' 'Baseline:' line names no one column of the table: write the column's "
                           "header there")
            elif len(hits) == len(cols):
                out.append("## Measurements' table has only the baseline")
        elif not ("main" in names and "this pr" in names):
            out.append("## Measurements does not say which column is the baseline: add a 'Baseline: <its column>' "
                       "line, with one line on why main against this PR wasn't the comparison")
    for name, pattern, line in (("main", r"^\W*main:\s*([0-9a-f]{7,40})\b", "main: <hash> (<date>)"),
                                ("this pr", r"^\W*this PR:.*?@\s*([0-9a-f]{7,40})\b", "this PR: <branch> @ <hash>")):
        hashes = [h for n, h, _ in cols if n == name]
        if not hashes:
            continue
        m = re.search(pattern, text, re.M | re.I)
        if not m:
            out.append("## Measurements has no '%s' line" % line)
        elif not any(h.startswith(m.group(1)) or m.group(1).startswith(h) for h in hashes):
            out.append("## Measurements' '%s' line names %s, and its column %s" % (line.split(":")[0] + ":",
                                                                                   m.group(1), hashes[0]))
    if not any(r and r[0] and all(i < len(r) and r[i] for i in values) for r in rows):
        out.append("## Measurements' table has no row with a value in each column")
    return out


def flags_problems(text, switches):
    out = []
    plain = text.strip()
    rows = []
    for header, body_rows in tables(plain):
        if header and header[0].lower() == "flag":
            rows += [(header, r) for r in body_rows if r and r[0]]
    if not rows and not re.match(r"^none\b", plain, re.I):
        out.append("## Flags and settings has neither a filled-in row nor 'None'")
    for header, r in rows:
        for i, name in enumerate(header[:-1]):   # (the last column, "Untested, but should help on", is optional)
            if i >= len(r) or not r[i]:
                out.append("## Flags and settings: '%s' has no '%s' (write '-' when there is nothing)" % (r[0], name))
    listed = text if rows else ""
    out += ["the diff adds %s, which ## Flags and settings does not list" % s for s in switches if s not in listed]
    return out


def diff_changes(diff):
    """(the switch names the diff adds, the files it changes) for a unified diff; a name only moved is not added"""
    added, removed, files, path = set(), set(), set(), None
    for line in (diff or "").splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            path = path[2:] if path.startswith("b/") else path
            files.add(path)
        elif line.startswith("--- "):
            continue
        elif path and not path.startswith(("docs/", "tests/")) and not path.endswith(".md"):
            if line.startswith("+"):
                added.update(SWITCH.findall(line))
            elif line.startswith("-"):
                removed.update(SWITCH.findall(line))
    return sorted(added - removed), files


def review(body, diff=None, merge_base=None, template=None):
    """(problems, warnings) for body: it passes when there are no problems. diff (the PR's unified diff) adds the
    Flags checks; merge_base (the commit the PR is now on) the rebase warning."""
    template = template if template is not None else TEMPLATE.read_text(encoding="utf-8")
    stand_ins = set(STAND_IN.findall(without_comments(template)))
    body = without_comments((body or "").replace("\r\n", "\n"))
    top = sections(body)
    out, warn = [], []
    switches, files = diff_changes(diff)
    for name in REQUIRED:
        text = top.get(name)
        if text is None:
            out.append("no ## %s section (.forgejo/pull_request_template.md)" % name)
            continue
        left = sorted(s for s in stand_ins if s in text)
        if left:
            out.append("## %s still holds the template's placeholders: %s%s" % (
                name, ", ".join(left[:3]), " and %d more" % (len(left) - 3) if len(left) > 3 else ""))
        elif not text.strip():
            out.append("## %s is empty" % name)
        elif name == "Measurements":
            out += measurement_problems(text)
        elif name == FLAGS:
            out += flags_problems(text, switches)
    if FLAGS not in top and switches:
        out.append("the diff adds %s, and there is no ## Flags and settings" % ", ".join(switches))
    everyone = for_everyone(body)
    if everyone is None:
        out.append("no ### For everyone checklist under ## Checklist")
    else:
        have = {key(item) for _, item in boxes(everyone)}
        out += ["not ticked: " + item for ticked, item in boxes(everyone) if not ticked]
        out += ["missing from For everyone: " + item for _, item in boxes(for_everyone(without_comments(template)) or "")
                if key(item) not in have]
    flags = top.get(FLAGS, "").strip()
    if files & set(MENU_CODE) and (not flags or re.match(r"^none\b", flags, re.I)):
        warn.append("the diff changes %s and ## Flags and settings says None: list any menu row or ini key it adds "
                    "or changes" % ", ".join(sorted(files & set(MENU_CODE))))
    m = re.search(r"^\W*main:\s*([0-9a-f]{7,40})\b", top.get("Measurements", ""), re.M | re.I)
    if m and merge_base and not merge_base.lower().startswith(m.group(1).lower()):
        warn.append("measured on main %s, but the PR is now on main %s: measure again, or say why the numbers still "
                    "hold" % (m.group(1), merge_base[:8]))
    return out, warn


def problems(body, template=None, diff=None):
    """What keeps body from passing: an empty list when it passes"""
    return review(body, diff=diff, template=template)[0]


def wip_prefix(title):
    """The WIP prefix title starts with, as written there, or None"""
    t = (title or "").lstrip()
    for p in WIP_PREFIXES:
        if t[:len(p)].upper() == p:
            return t[:len(p)]
    return None


def strip_wip(title):
    """title without its WIP prefixes (the prefix, then the spaces after it, as Forgejo takes it off)"""
    title = (title or "").strip()
    while wip_prefix(title):
        title = title[len(wip_prefix(title)):].strip()
    return title


def is_draft(title, api_draft=False):
    """Work in progress: a WIP prefix on the title, or the API says it is a draft"""
    return bool(api_draft) or wip_prefix(title) is not None


def fetch(server, repo, number, suffix=""):
    req = urllib.request.Request("%s/api/v1/repos/%s/pulls/%d%s" % (server.rstrip("/"), repo, number, suffix),
                                 headers={"Accept": "application/json", "User-Agent": "hookshot-pr-check/1"})
    token = os.environ.get("FORGEJO_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", "token " + token)
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
    return data.decode("utf-8", "replace") if suffix else json.loads(data)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", help="the description, from a file")
    src.add_argument("--pr", type=int, help="the pull request's number: its description and diff, from the API")
    p.add_argument("--diff", help="with --file: the PR's unified diff, for the Flags checks")
    p.add_argument("--wip", "--draft", dest="wip", action="store_true",
                   help="with --file: treat it as work in progress (not checked: a notice only)")
    p.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY") or "MagicBeanDev/Hookshot")
    p.add_argument("--server", default=os.environ.get("GITHUB_SERVER_URL") or "https://git.magicbean.dev")
    a = p.parse_args(argv)
    merge_base, diff = None, None
    if a.file:
        body, wip = Path(a.file).read_text(encoding="utf-8"), a.wip
        diff = Path(a.diff).read_text(encoding="utf-8", errors="replace") if a.diff else None
    else:
        pr = fetch(a.server, a.repo, a.pr)
        body, wip, merge_base = pr.get("body") or "", is_draft(pr.get("title"), pr.get("draft")), pr.get("merge_base")
        try:
            diff = fetch(a.server, a.repo, a.pr, ".diff")
        except OSError as e:
            print("::warning::could not read the diff (%s): the Flags table is not checked against it" % e)
    found, warnings = review(body, diff=diff, merge_base=merge_base)
    if wip:
        print("::notice::Work in progress (WIP: in the title): the description is checked once it is marked ready "
              "for review%s" % (" (%d to fix by then)" % len(found) if found else ""))
        for f in found + warnings:
            print("  " + f)
        return 0
    for w in warnings:
        print("::warning::" + w)
    if not found:
        print("The description is complete: every section filled in, the measurements named, every For everyone box "
              "ticked.")
        return 0
    print("The description is not complete (the template is .forgejo/pull_request_template.md; a PR without "
          "measurements goes back to WIP with the label '%s'):" % NEEDS_MEASUREMENTS)
    for f in found:
        print("::error::" + f)
    return 1


if __name__ == "__main__":
    sys.exit(main())
