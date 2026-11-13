"""Work with Hookshot on the team's Forgejo (git.magicbean.dev): sign in, push to your fork, pull requests, issues.
Standard library only (Windows and Linux), so any agent can use it; people may prefer fj, the Forgejo CLI
(docs/forge.md has both).

    python -I scripts/forge.py login [--no-browser] [--again]      sign in, set up git
    python -I scripts/forge.py fork                                 set up pushing: your fork and its remote
    python -I scripts/forge.py whoami
    python -I scripts/forge.py logout

    python -I scripts/forge.py pr create BRANCH --title T [--body TEXT | --body-file F] [--ready [--force]]
    python -I scripts/forge.py pr ready N [--force]                 take WIP: off, once the description is complete
    python -I scripts/forge.py pr wip N [--needs-measurements]      put WIP: back: it needs more work
    python -I scripts/forge.py pr edit N [--title T] [--body TEXT | --body-file F]
    python -I scripts/forge.py pr update N [--body TEXT | --body-file F] (--comment TEXT | --comment-file F)
    python -I scripts/forge.py pr comment N (TEXT | --body-file F)
    python -I scripts/forge.py pr status N                          WIP or ready, the checklist, reviews, last comments
    python -I scripts/forge.py pr list [--mine] [--state open|closed|all]
    python -I scripts/forge.py pr view N [--comments]
    python -I scripts/forge.py pr checkout N                       its head as the local branch pr/N

    python -I scripts/forge.py issue list [--state open|closed|all] [--label L]
    python -I scripts/forge.py issue view N [--comments]
    python -I scripts/forge.py issue create --title T [--body TEXT | --body-file F]
    python -I scripts/forge.py issue comment N (TEXT | --body-file F)

login is the team's one sign-in (magicbean_login.py: Sign in with Discord once, for the team chat and git together);
it is skipped when a git sign-in is saved already (--again redoes it). Then it:
  - makes this script git's credential helper for https://git.magicbean.dev (global git config, that host only), so
    git push, git fetch and jj git push sign in by themselves, the token renewed when it runs out;
  - adds the remote "forge" for MagicBeanDev/Hookshot to the repository you run it in, unless one already points there
    (fetch from it; its main takes reviewed pull requests only, nobody pushes to it).
That is all reading, testing and test requests need. To send changes, fork sets up pushing: it makes your fork,
<you>/Hookshot, if you have none, and adds the remote "forge-fork" for it (push your branches there). pr create does
the same when it hasn't been done. pr create opens a pull request from BRANCH on your fork into Hookshot's main (push
the branch to your fork first: jj git push --remote forge-fork -b BRANCH, or git push forge-fork BRANCH). git calls
this script as "forge.py credential get|store|erase".

A pull request is work in progress until it is measured and ready for review (CONTRIBUTING.md, Commits and pull
requests): a title starting with "WIP: ", Forgejo's draft mark. pr create opens it so, with the description started
from .forgejo/pull_request_template.md when no --body or --body-file is given; --ready opens it ready, when the
description is complete already; it fills in the commits the Measurements name (main's: the merge base; this PR's:
the branch's head). pr ready takes WIP: off after the same check as the PR description workflow
(check_pr_description.py: every section filled in, the measurements named by commit, the Flags table, every For
everyone box ticked), and refuses when it fails
(--force goes on with a warning); pr wip puts it back. The description is the PR as it is now, the comments its
history: pr update edits the description and posts a comment saying what changed in one step.
"""
import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import magicbean_login as auth  # noqa: E402  (same folder; python -I does not add it to the path)
import check_pr_description as desc  # noqa: E402

HOST = auth.FORGE_HOST
BASE = auth.FORGE
REPO = "MagicBeanDev/Hookshot"
HELPER_KEY = "credential.%s.helper" % BASE
# A pull request is squash-merged, so its title is the commit on main: a Conventional Commit, as CONTRIBUTING.md and the
# PR title check (.github/workflows/pr-title.yml) want it.
TITLE_TYPES = ("feat", "fix", "perf", "refactor", "docs", "test", "ci", "build", "chore", "revert")
TITLE = re.compile(r"^(%s)(\([\w./-]+\))?!?: \S" % "|".join(TITLE_TYPES))
WIP = desc.WIP_PREFIXES[0] + " "   # (the title prefix Forgejo reads as work in progress: "WIP: ")


class ForgeError(Exception):
    pass


def token():
    state = auth.forge_session()
    if not state:
        raise ForgeError("not signed in to %s (run: python -I scripts/forge.py login)" % HOST)
    return state


def me():
    """Your login as Forgejo knows it now: the one saved at sign-in goes stale when the account is renamed"""
    return api("GET", "/user")["login"]


def api(method, path, body=None):
    try:
        return auth.forge_request(method, BASE + "/api/v1" + path, token=token()["access_token"], body=body)
    except auth.LoginError as e:
        raise ForgeError(str(e))


def api_text(path):
    """A text answer from the API (a pull request's .diff), with the same sign-in as api()"""
    req = urllib.request.Request(BASE + "/api/v1" + path, headers={
        "User-Agent": auth.USER_AGENT, "Authorization": "Bearer " + token()["access_token"]})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as e:
        raise ForgeError("GET %s: %s" % (path, e))


def text_arg(args):
    if getattr(args, "body_file", None):
        with open(args.body_file, encoding="utf-8") as f:
            return f.read()
    return getattr(args, "body", None) or getattr(args, "text", None) or ""


# --- git setup -----------------------------------------------------------------------------------------------------

def git(*args, check=True):
    r = subprocess.run(["git"] + list(args), capture_output=True, text=True)
    if check and r.returncode:
        raise ForgeError("git %s: %s" % (" ".join(args[:3]), (r.stderr or r.stdout).strip()))
    return r


def in_jj():
    return bool(shutil.which("jj")) and subprocess.run(["jj", "root"], capture_output=True).returncode == 0


def helper_command():
    """This script by its full path, run by this Python (forward slashes: git runs helpers with sh, on Windows too)."""
    py = sys.executable.replace("\\", "/")
    script = os.path.abspath(__file__).replace("\\", "/")
    return "!%s -I %s credential" % (shlex.quote(py), shlex.quote(script))


def remotes():
    """{name: url} of the repository here (jj's or git's), or None outside one."""
    if in_jj():
        out = subprocess.run(["jj", "git", "remote", "list"], capture_output=True, text=True).stdout
    elif git("rev-parse", "--git-dir", check=False).returncode == 0:
        out = git("remote", "-v").stdout
    else:
        return None
    return {line.split()[0]: line.split()[1] for line in out.splitlines() if len(line.split()) >= 2}


def add_remote(name, repo):
    """A remote for repo (owner/name) in the repository here, unless one points there already."""
    url = "%s/%s.git" % (BASE, repo)
    have = remotes()
    if have is None:
        return "not in a repository: no remote added (git remote add %s %s)" % (name, url)
    want = (HOST + "/" + repo).lower()
    for n, u in have.items():
        if want in u.lower().replace(".git", ""):
            return "remote %s: %s" % (n, repo)
    cmd = ["jj", "git", "remote", "add", name, url] if in_jj() else ["git", "remote", "add", name, url]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        return "could not add remote %s: %s" % (name, (r.stderr or r.stdout).strip())
    return "added remote %s: %s" % (name, url)


def ensure_fork(login):
    """<login>/Hookshot, made from MagicBeanDev/Hookshot if missing; (its full name, whether it was made now)."""
    name = REPO.split("/")[1]
    try:
        mine = api("GET", "/repos/%s/%s" % (login, name))
    except ForgeError as e:
        if "HTTP 404" not in str(e):
            raise
        return api("POST", "/repos/%s/forks" % REPO, {})["full_name"], True
    if not (mine.get("fork") and (mine.get("parent") or {}).get("full_name", "").lower() == REPO.lower()):
        raise ForgeError("%s/%s exists but is not a fork of %s: rename it, or fork by hand" % (login, name, REPO))
    return mine["full_name"], False


# --- commands ------------------------------------------------------------------------------------------------------

def cmd_login(args):
    state = None if args.again else auth.forge_session()
    if state:
        print("Signed in to %s as %s (--again to sign in anew)." % (HOST, state["login"]))
    else:
        try:
            done = auth.sign_in(no_browser=args.no_browser)
        except auth.LoginError as e:
            raise ForgeError(str(e))
        if "forge" not in done:
            raise ForgeError("not signed in to %s" % HOST)
        print("Signed in as %s (the team chat too: %s)." % (done["forge"], done.get("matrix", "no")))
        state = auth.forge_session()
    git("config", "--global", "--replace-all", HELPER_KEY, "")   # (no other helper for this host)
    git("config", "--global", "--add", HELPER_KEY, helper_command())
    print("git signs in to %s with this script (global git config, this host only)." % HOST)
    print(add_remote(args.upstream, REPO) + " (fetch from it; changes reach it through pull requests)")
    print("To send changes: python -I scripts/forge.py fork (your fork, to push to; pr create does it too).")


def set_up_fork(login, remote="forge-fork"):
    """The fork and its remote, made if missing; prints what it did. Returns the fork's full name."""
    fork, made = ensure_fork(login)
    print(("Made your fork: %s/%s" if made else "Your fork: %s/%s") % (BASE, fork))
    print(add_remote(remote, fork) + " (push your branches here)")
    if git("config", "user.email", check=False).returncode:
        print("Set your commit name and email too (git config --global user.name / user.email).")
    return fork


def cmd_fork(args):
    set_up_fork(me(), args.remote)


def cmd_whoami(args):
    state = auth.forge_session()
    print(state["login"] if state else "not signed in (run: python -I scripts/forge.py login)")
    return 0 if state else 1


def cmd_logout(args):
    try:
        os.remove(auth.forge_path())
    except FileNotFoundError:
        pass
    git("config", "--global", "--unset-all", HELPER_KEY, check=False)
    print("Signed out of %s (the team chat stays signed in: team_chat.py logout)." % HOST)


def cmd_credential(args):
    """git's credential protocol on standard input. get answers for this host only; erase (git found the password
    wrong) makes the next get renew the token; store has nothing to do."""
    fields = dict(line.split("=", 1) for line in sys.stdin.read().splitlines() if "=" in line)
    if fields.get("host") != HOST or fields.get("protocol", "https") != "https":
        return 0
    if args.action == "get":
        state = auth.forge_session()
        if state:
            print("username=%s\npassword=%s" % (state.get("login") or "oauth2", state["access_token"]))
        else:
            print("forge.py: not signed in to %s; run: python -I scripts/forge.py login" % HOST, file=sys.stderr)
    elif args.action == "erase":
        state = auth.forge_load()
        if state and fields.get("password") == state.get("access_token"):
            auth.forge_expire()
    return 0


def show_comments(number):
    for c in api("GET", "/repos/%s/issues/%d/comments" % (REPO, number)) or []:
        print("\n--- %s, %s\n%s" % (c["user"]["login"], c["created_at"][:16].replace("T", " "), c["body"].strip()))


# --- pull requests: work in progress, ready, the description and the comments ------------------------------------

def check_title(title):
    """title without its WIP prefix, when the rest is a Conventional Commit"""
    title = desc.strip_wip(title)
    if not TITLE.match(title):
        raise ForgeError("the title must be a Conventional Commit, type(scope): description, with the type one of "
                         "%s (CONTRIBUTING.md, Pull request titles), e.g. \"fix(vk): ...\"" % ", ".join(TITLE_TYPES))
    return title


def body_given(args):
    return getattr(args, "body", None) is not None or getattr(args, "body_file", None) is not None


def template_body():
    try:
        return desc.TEMPLATE.read_text(encoding="utf-8")
    except OSError:
        return ""


def pr_diff(number):
    """The pull request's diff, for the Flags checks; None (with a note) when it can't be read"""
    try:
        return api_text("/repos/%s/pulls/%d.diff" % (REPO, number))
    except ForgeError as e:
        print("forge.py: could not read #%d's diff (%s): the Flags table is not checked against it" % (number, e),
              file=sys.stderr)
        return None


def complete_or_refuse(body, force, number=None, diff=None, merge_base=None):
    """Refuses (a ForgeError listing what is missing) unless body passes the PR description check; --force warns and
    goes on. The check's warnings are printed either way."""
    found, warnings = desc.review(body, diff=diff, merge_base=merge_base)
    to_open = desc.requests_to_open(body)
    for w in warnings if not to_open else [w for w in warnings if "still to open" not in w]:
        print("forge.py: warning: " + w, file=sys.stderr)
    # (ready only once the test requests Needs other testers names are open: CONTRIBUTING.md, Commits and pull requests)
    found = found + ["## Testing's 'Needs other testers:' still has a request to open: %s (open it: python -I "
                     "scripts/test_requests.py request --pr <n> ..., then put its id there)" % item
                     for item in to_open]
    if not found:
        return
    listed = "\n".join("  - " + f for f in found)
    if force:
        print("forge.py: warning: marked ready with an incomplete description (--force):\n" + listed, file=sys.stderr)
        return
    fix = ("python -I scripts/forge.py pr edit %d --body-file F" % number) if number else "fill in the --body-file"
    raise ForgeError("the description is not ready for review (.forgejo/pull_request_template.md; CONTRIBUTING.md, "
                     "Commits and pull requests):\n%s\nComplete it (%s), then try again; --force goes on anyway."
                     % (listed, fix))


def warn_if_incomplete(pr, body):
    """After a description edit on a PR marked ready: say so when it no longer passes the check"""
    found = desc.problems(body)
    if found and not desc.is_draft(pr.get("title"), pr.get("draft")):
        print("forge.py: warning: #%d is marked ready, but its description no longer passes the check:\n%s\n"
              "(if it needs more work: python -I scripts/forge.py pr wip %d)"
              % (pr["number"], "\n".join("  - " + f for f in found), pr["number"]), file=sys.stderr)


def fill_commits(body, branch, pr):
    """body with the template's stand-ins for the commits filled in: main's is the merge base, this PR's the head"""
    head, base = (pr.get("head") or {}).get("sha"), pr.get("merge_base")
    if not head or not base:
        return body
    date = None
    try:
        date = api("GET", "/repos/%s/git/commits/%s" % (REPO, base))["commit"]["committer"]["date"][:10]
    except (ForgeError, KeyError, TypeError):
        pass
    for old, new in (("main: <commit> (<date>)", "main: %s (%s)" % (base[:8], date) if date else None),
                     ("main: <commit>", "main: %s" % base[:8]),
                     ("this PR: <branch> @ <commit>", "this PR: %s @ %s" % (branch, head[:8])),
                     ("main @ <commit>", "main @ %s" % base[:8]),
                     ("this PR @ <commit>", "this PR @ %s" % head[:8])):
        if new:
            body = body.replace(old, new)
    return body


def get_pr(number):
    return api("GET", "/repos/%s/pulls/%d" % (REPO, number))


def edit_pr(number, **fields):
    return api("PATCH", "/repos/%s/pulls/%d" % (REPO, number), fields)


def post_comment(number, text):
    if not text.strip():
        raise ForgeError("the comment is empty")
    return api("POST", "/repos/%s/issues/%d/comments" % (REPO, number), {"body": text})["html_url"]


def open_or_refuse(pr):
    if pr.get("merged") or pr.get("state") != "open":
        raise ForgeError("#%d is %s" % (pr["number"], "merged" if pr.get("merged") else pr.get("state")))


def label_id(name):
    page = 1
    while True:
        labels = api("GET", "/repos/%s/labels?limit=50&page=%d" % (REPO, page)) or []
        for label in labels:
            if label["name"].lower() == name.lower():
                return label["id"]
        if len(labels) < 50:
            return None
        page += 1


def cmd_pr_ready(args):
    pr = get_pr(args.number)
    open_or_refuse(pr)
    if not desc.is_draft(pr["title"], pr.get("draft")):
        print("#%d is ready for review already: %s" % (pr["number"], pr["html_url"]))
        return
    complete_or_refuse(pr.get("body"), args.force, pr["number"], pr_diff(pr["number"]), pr.get("merge_base"))
    done = edit_pr(pr["number"], title=desc.strip_wip(pr["title"]))
    if done.get("draft"):   # (the server reads another prefix as work in progress too)
        raise ForgeError("Forgejo still takes #%d for work in progress: %r (its WORK_IN_PROGRESS_PREFIXES); take the "
                         "prefix off by hand: pr edit %d --title" % (pr["number"], done["title"], pr["number"]))
    if any(l["name"].lower() == desc.NEEDS_MEASUREMENTS for l in pr.get("labels") or []):
        try:
            api("DELETE", "/repos/%s/issues/%d/labels/%d" % (REPO, pr["number"], label_id(desc.NEEDS_MEASUREMENTS)))
            print("Took off the label '%s'." % desc.NEEDS_MEASUREMENTS)
        except ForgeError as e:
            print("Could not take off the label '%s' (a maintainer will): %s" % (desc.NEEDS_MEASUREMENTS, e))
    print("#%d is ready for review: %s" % (pr["number"], done["html_url"]))


def cmd_pr_wip(args):
    pr = get_pr(args.number)
    open_or_refuse(pr)
    if desc.is_draft(pr["title"], pr.get("draft")):
        print("#%d is work in progress already." % pr["number"])
    else:
        edit_pr(pr["number"], title=WIP + desc.strip_wip(pr["title"]))
        print("#%d is work in progress again: %s" % (pr["number"], pr["html_url"]))
    if args.needs_measurements:
        lid = label_id(desc.NEEDS_MEASUREMENTS)
        if lid is None:
            raise ForgeError("no label '%s' on %s" % (desc.NEEDS_MEASUREMENTS, REPO))
        api("POST", "/repos/%s/issues/%d/labels" % (REPO, pr["number"]), {"labels": [lid]})
        print("Added the label '%s'." % desc.NEEDS_MEASUREMENTS)
    print("Say what it needs in a comment: python -I scripts/forge.py pr comment %d \"...\"" % pr["number"])


def edit_description(args, pr):
    """The title and the description from args, on pr; the title keeps its WIP state (pr ready and pr wip change it)"""
    fields = {}
    if getattr(args, "title", None):
        title = check_title(args.title)
        fields["title"] = (WIP + title) if desc.is_draft(pr["title"], pr.get("draft")) else title
    if body_given(args):
        fields["body"] = text_arg(args)
    if not fields:
        return pr
    done = edit_pr(pr["number"], **fields)
    if "body" in fields:
        warn_if_incomplete(done, fields["body"])
    print("Updated #%d's %s: %s" % (pr["number"], " and ".join(
        {"title": "title", "body": "description"}[k] for k in fields), done["html_url"]))
    return done


def cmd_pr_edit(args):
    if not args.title and not body_given(args):
        raise ForgeError("nothing to change: give --title, --body or --body-file")
    pr = get_pr(args.number)
    open_or_refuse(pr)
    edit_description(args, pr)


def cmd_pr_update(args):
    comment = text_arg(argparse.Namespace(body=args.comment, body_file=args.comment_file))
    if not comment.strip():
        raise ForgeError("say what changed: --comment TEXT or --comment-file F (the comments are the PR's history)")
    pr = get_pr(args.number)
    open_or_refuse(pr)
    edit_description(args, pr)
    print(post_comment(pr["number"], comment))


def cmd_pr_status(args):
    pr = get_pr(args.number)
    body = pr.get("body") or ""
    wip = desc.is_draft(pr["title"], pr.get("draft"))
    state = ("merged" if pr.get("merged") else pr["state"] if pr["state"] != "open"
             else "work in progress" if wip else "ready for review")
    print("#%d %s\n%s, by %s: %s" % (pr["number"], pr["title"], state, pr["user"]["login"], pr["html_url"]))
    found, warnings = desc.review(body, diff=pr_diff(pr["number"]), merge_base=pr.get("merge_base"))
    to_open = len(desc.requests_to_open(body))
    print("Description: %s" % (("complete" if not to_open else "complete but for %d test request%s to open" % (
        to_open, "" if to_open == 1 else "s")) if not found else "%d to fix%s" % (
        len(found), "" if pr["state"] != "open" else " before pr ready" if wip
        else " (it is marked ready: pr wip %d if it needs more work)" % pr["number"])))
    for f in found:
        print("  - " + f)
    for w in warnings:
        print("  warning: " + w)
    parts = desc.testing_parts(desc.sections(desc.without_comments(body)).get("Testing", ""))
    needs = parts.get("Needs other testers", "").strip()
    if not parts:
        print("Testing: no Tested / Not tested / Needs other testers parts")
    else:
        items = [] if re.match(r"^none\b", needs, re.I) else desc.tester_lines(needs)
        print("Testing: %s; needs other testers: %s" % (
            ", ".join("%s %s" % (p, "filled in" if desc.STAND_IN.sub("", parts.get(p, "")).strip() else "missing") for p in desc.TESTING_PARTS[:2]),
            "none" if not items else "%d (%d with a test request, %d to open)" % (
                len(items), sum(bool(desc.TEST_REQUEST.search(i)) for i in items), len(desc.requests_to_open(body)))))
    lists = desc.sections(desc.sections(desc.without_comments(body)).get("Checklist", ""), "###")
    for name in ("For everyone", "If applicable"):
        items = desc.boxes(lists.get(name, ""))
        print("Checklist, %s: %d of %d ticked" % (name, sum(t for t, _ in items), len(items)))
    if pr["state"] == "open":
        print("Mergeable: %s" % ("yes" if pr.get("mergeable") else "no (rebase onto main)"))
    labels = [l["name"] for l in pr.get("labels") or []]
    print("Labels: %s" % (", ".join(labels) or "none"))
    latest = {}
    for r in api("GET", "/repos/%s/pulls/%d/reviews" % (REPO, pr["number"])) or []:
        if r.get("state") != "PENDING" and r.get("user"):
            latest[r["user"]["login"]] = r   # (the last review of each reviewer counts)
    print("Reviews: %s" % (", ".join("%s %s" % (who, r["state"].lower().replace("_", " "))
                                     for who, r in latest.items()) or "none"))
    comments = api("GET", "/repos/%s/issues/%d/comments" % (REPO, pr["number"])) or []
    if comments:
        print("Last comments (%d in all):" % len(comments))
        for c in comments[-args.last:]:
            text = " ".join((c.get("body") or "").split())
            print("  %s, %s: %s" % (c["user"]["login"], c["created_at"][:16].replace("T", " "),
                                    text if len(text) <= 100 else text[:99] + "..."))


def cmd_pr(args):
    commands = {"ready": cmd_pr_ready, "wip": cmd_pr_wip, "edit": cmd_pr_edit, "update": cmd_pr_update,
                "status": cmd_pr_status}
    if args.action in commands:
        return commands[args.action](args)
    if args.action == "create":
        if args.force and not args.ready:
            raise ForgeError("--force goes with --ready")
        title = check_title(args.title)
        body = text_arg(args) if body_given(args) else template_body()   # (nobody starts from nothing)
        if args.ready:
            complete_or_refuse(body, args.force)
        login = me()
        fork, made = ensure_fork(login)
        if made:   # (first pull request: set up pushing, then the branch has to go there)
            set_up_fork(login)
        try:
            api("GET", "/repos/%s/branches/%s" % (fork, urllib.parse.quote(args.branch, safe="")))
        except ForgeError:
            raise ForgeError("no branch %s on %s: push it there first (jj git push --remote forge-fork -b %s, or "
                             "git push forge-fork %s)" % (args.branch, fork, args.branch, args.branch))
        pr = api("POST", "/repos/%s/pulls" % REPO, {
            "base": "main", "head": "%s:%s" % (login, args.branch),
            "title": title if args.ready else WIP + title, "body": body})
        filled = fill_commits(body, args.branch, pr)
        if filled != body:
            edit_pr(pr["number"], body=filled)
        print(pr["html_url"])
        if not args.ready:
            print("Opened as work in progress (%s). Fill in the description (pr edit %d --body-file F); once the "
                  "measurements and the checklist are in: python -I scripts/forge.py pr ready %d"
                  % (WIP.strip(), pr["number"], pr["number"]))
    elif args.action == "list":
        mine = me() if args.mine else None
        page, shown = 1, 0
        while True:
            prs = api("GET", "/repos/%s/pulls?state=%s&limit=50&page=%d" % (REPO, args.state, page)) or []
            for p in prs:
                if mine and p["user"]["login"] != mine:
                    continue
                state = "merged" if p.get("merged") else p["state"]
                print("#%-5d %-7s %-16s %s" % (p["number"], state, p["user"]["login"], p["title"]))
                shown += 1
            if len(prs) < 50:
                break
            page += 1
        if not shown:
            print("no pull requests")
    elif args.action == "view":
        p = get_pr(args.number)
        state = "merged" if p.get("merged") else ("work in progress" if p["state"] == "open" and desc.is_draft(
            p["title"], p.get("draft")) else p["state"])
        print("#%d %s\n%s, by %s: %s -> %s, %s\n%s\n\n%s" % (
            p["number"], p["title"], state, p["user"]["login"], p["head"]["label"], p["base"]["ref"],
            "mergeable" if p.get("mergeable") else "not mergeable as it is", p["html_url"], (p.get("body") or "").strip()))
        if args.comments:
            show_comments(args.number)
    elif args.action == "comment":
        print(post_comment(args.number, text_arg(args)))
    elif args.action == "checkout":
        branch = "pr/%d" % args.number
        url = "%s/%s.git" % (BASE, REPO)
        spec = "+refs/pull/%d/head:refs/heads/%s" % (args.number, branch)
        if in_jj():
            gitdir = subprocess.run(["jj", "git", "root"], capture_output=True, text=True).stdout.strip()
            git("--git-dir", gitdir, "fetch", url, spec)
            subprocess.run(["jj", "git", "import"], capture_output=True)
            print("bookmark %s: jj new %s" % (branch, branch))
        else:
            git("fetch", url, spec)
            print("branch %s: git switch %s" % (branch, branch))


def cmd_issue(args):
    if args.action == "list":
        label = "&labels=" + urllib.parse.quote(args.label) if args.label else ""
        page, shown = 1, 0
        while True:
            items = api("GET", "/repos/%s/issues?type=issues&state=%s&limit=50&page=%d%s" % (
                REPO, args.state, page, label)) or []
            for i in items:
                labels = ",".join(l["name"] for l in i.get("labels") or [])
                print("#%-5d %-6s %s%s" % (i["number"], i["state"], i["title"], "  [%s]" % labels if labels else ""))
                shown += 1
            if len(items) < 50:
                break
            page += 1
        if not shown:
            print("no issues")
    elif args.action == "view":
        i = api("GET", "/repos/%s/issues/%d" % (REPO, args.number))
        print("#%d %s\n%s, by %s\n%s\n\n%s" % (i["number"], i["title"], i["state"], i["user"]["login"], i["html_url"],
                                             (i.get("body") or "").strip()))
        if args.comments:
            show_comments(args.number)
    elif args.action == "create":
        print(api("POST", "/repos/%s/issues" % REPO, {"title": args.title, "body": text_arg(args)})["html_url"])
    elif args.action == "comment":
        print(api("POST", "/repos/%s/issues/%d/comments" % (REPO, args.number), {"body": text_arg(args)})["html_url"])


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("login", help="sign in with Discord, set up git")
    s.add_argument("--upstream", default="forge", help="remote name for MagicBeanDev/Hookshot (default forge)")
    s.add_argument("--no-browser", action="store_true", help="print the link only")
    s.add_argument("--again", action="store_true", help="sign in anew even when signed in")
    s = sub.add_parser("fork", help="set up pushing: your fork of Hookshot and its remote")
    s.add_argument("--remote", default="forge-fork", help="remote name for your fork (default forge-fork)")
    sub.add_parser("whoami")
    sub.add_parser("logout")
    s = sub.add_parser("credential")
    s.add_argument("action", choices=["get", "store", "erase"])

    def body_args(s, text=False, what="the description"):
        if text:
            s.add_argument("text", nargs="?", help=what)
        g = s.add_mutually_exclusive_group()
        g.add_argument("--body", metavar="TEXT", help=what)
        g.add_argument("--body-file", metavar="F", help=what + ", from a file (UTF-8)")

    pr = sub.add_parser("pr", help="pull requests into Hookshot's main").add_subparsers(dest="action", required=True)
    s = pr.add_parser("create", help="open a pull request from a branch of your fork, as work in progress (WIP: title)",
                      description="Opens a pull request from BRANCH on your fork into Hookshot's main. It opens as work "
                      "in progress (a 'WIP: ' title) unless --ready; without --body or --body-file, the description "
                      "starts from .forgejo/pull_request_template.md.")
    s.add_argument("branch")
    s.add_argument("--title", required=True, help="a Conventional Commit, type(scope): description")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--ready", action="store_true",
                   help="open it ready for review: the description must pass the check (as pr ready)")
    g.add_argument("--wip", "--draft", dest="wip", action="store_true",
                   help="open it as work in progress (the default; --draft is the earlier name)")
    s.add_argument("--force", action="store_true", help="with --ready: open it ready even if the check fails")
    body_args(s)
    s = pr.add_parser("ready", help="mark it ready for review: take WIP: off, once the description passes the check",
                      description="Takes the WIP prefix off the title, after the PR description check: Why and "
                      "Measurements filled in, every For everyone box ticked. Refuses when it fails.")
    s.add_argument("number", type=int)
    s.add_argument("--force", action="store_true", help="mark it ready even if the check fails (with a warning)")
    s = pr.add_parser("wip", help="put it back to work in progress (WIP: title): it needs more work")
    s.add_argument("number", type=int)
    s.add_argument("--needs-measurements", action="store_true",
                   help="also add the label 'needs measurements' (maintainers)")
    s = pr.add_parser("edit", help="change the title or the description (it stays WIP or ready as it is)")
    s.add_argument("number", type=int)
    s.add_argument("--title", help="the new title, a Conventional Commit (the WIP prefix is kept as it is)")
    body_args(s, what="the new description")
    s = pr.add_parser("update", help="after a push, a measurement or a test run: the description and a comment in one go",
                      description="Updates the description (when --body or --body-file is given) and posts a comment "
                      "saying what changed: the description is the PR as it is now, the comments its history.")
    s.add_argument("number", type=int)
    body_args(s, what="the new description")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--comment", metavar="TEXT", help="what changed and why")
    g.add_argument("--comment-file", metavar="F", help="what changed and why, from a file (UTF-8)")
    s = pr.add_parser("comment", help="post a comment")
    s.add_argument("number", type=int)
    body_args(s, text=True, what="the comment")
    s = pr.add_parser("status", help="WIP or ready, the description check, the checklist, mergeable, reviews, comments")
    s.add_argument("number", type=int)
    s.add_argument("--last", type=int, default=3, metavar="N", help="the last N comments (default 3)")
    s = pr.add_parser("list", help="list pull requests")
    s.add_argument("--mine", action="store_true", help="yours only")
    s.add_argument("--state", choices=["open", "closed", "all"], default="open")
    s = pr.add_parser("view", help="the title, state and description")
    s.add_argument("number", type=int)
    s.add_argument("--comments", action="store_true", help="the comments too")
    s = pr.add_parser("checkout", help="its head as the local bookmark (jj) or branch (git) pr/N")
    s.add_argument("number", type=int)

    issue = sub.add_parser("issue").add_subparsers(dest="action", required=True)
    s = issue.add_parser("list")
    s.add_argument("--state", choices=["open", "closed", "all"], default="open")
    s.add_argument("--label")
    s = issue.add_parser("view")
    s.add_argument("number", type=int)
    s.add_argument("--comments", action="store_true")
    s = issue.add_parser("create")
    s.add_argument("--title", required=True)
    body_args(s)
    s = issue.add_parser("comment")
    s.add_argument("number", type=int)
    body_args(s, text=True)

    args = p.parse_args(argv)
    commands = {"login": cmd_login, "fork": cmd_fork, "whoami": cmd_whoami, "logout": cmd_logout, "credential": cmd_credential,
                "pr": cmd_pr, "issue": cmd_issue}
    try:
        return commands[args.cmd](args) or 0
    except (ForgeError, auth.LoginError) as e:
        print("forge.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
