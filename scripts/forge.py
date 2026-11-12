"""Work with Hookshot on the team's Forgejo (git.magicbean.dev): sign in, push to your fork, pull requests, issues.
Standard library only (Windows and Linux), so any agent can use it; people may prefer fj, the Forgejo CLI
(docs/forge.md has both).

    python -I scripts/forge.py login [--no-browser] [--again]      sign in, make your fork, set up git
    python -I scripts/forge.py whoami
    python -I scripts/forge.py logout

    python -I scripts/forge.py pr create BRANCH --title T [--body TEXT | --body-file F] [--draft]
    python -I scripts/forge.py pr list [--mine] [--state open|closed|all]
    python -I scripts/forge.py pr view N [--comments]
    python -I scripts/forge.py pr comment N (TEXT | --body-file F)
    python -I scripts/forge.py pr checkout N                       its head as the local branch pr/N

    python -I scripts/forge.py issue list [--state open|closed|all] [--label L]
    python -I scripts/forge.py issue view N [--comments]
    python -I scripts/forge.py issue create --title T [--body TEXT | --body-file F]
    python -I scripts/forge.py issue comment N (TEXT | --body-file F)

login is the team's one sign-in (magicbean_login.py: Sign in with Discord once, for the team chat and git together);
it is skipped when a git sign-in is saved already (--again redoes it). Then it:
  - makes this script git's credential helper for https://git.magicbean.dev (global git config, that host only), so
    git push, git fetch and jj git push sign in by themselves, the token renewed when it runs out;
  - makes your fork, <you>/Hookshot, if you have none;
  - adds two remotes to the repository you run it in, unless one already points there: "forge" for MagicBeanDev/Hookshot
    (fetch from it; its main takes reviewed pull requests only, nobody pushes to it) and "forge-fork" for your fork
    (push your branches there).
pr create opens a pull request from BRANCH on your fork into Hookshot's main (push the branch to your fork first:
jj git push --remote forge-fork -b BRANCH, or git push forge-fork BRANCH). git calls this script as
"forge.py credential get|store|erase".
"""
import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import magicbean_login as auth  # noqa: E402  (same folder; python -I does not add it to the path)

HOST = auth.FORGE_HOST
BASE = auth.FORGE
REPO = "MagicBeanDev/Hookshot"
HELPER_KEY = "credential.%s.helper" % BASE
# A pull request is squash-merged, so its title is the commit on main: a Conventional Commit, as CONTRIBUTING.md and the
# PR title check (.github/workflows/pr-title.yml) want it.
TITLE_TYPES = ("feat", "fix", "perf", "refactor", "docs", "test", "ci", "build", "chore", "revert")
TITLE = re.compile(r"^(%s)(\([\w./-]+\))?!?: \S" % "|".join(TITLE_TYPES))


class ForgeError(Exception):
    pass


def token():
    state = auth.forge_session()
    if not state:
        raise ForgeError("not signed in to %s (run: python -I scripts/forge.py login)" % HOST)
    return state


def api(method, path, body=None):
    try:
        return auth.forge_request(method, BASE + "/api/v1" + path, token=token()["access_token"], body=body)
    except auth.LoginError as e:
        raise ForgeError(str(e))


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
    fork, made = ensure_fork(state["login"])
    print(("Made your fork: %s/%s" if made else "Your fork: %s/%s") % (BASE, fork))
    print(add_remote(args.upstream, REPO) + " (fetch from it; changes reach it through pull requests)")
    print(add_remote(args.fork, fork) + " (push your branches here)")
    if git("config", "user.email", check=False).returncode:
        print("Set your commit name and email too (git config --global user.name / user.email).")


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


def cmd_pr(args):
    if args.action == "create":
        if not TITLE.match(args.title):
            raise ForgeError("the title must be a Conventional Commit, type(scope): description, with the type one of "
                             "%s (CONTRIBUTING.md, Pull request titles), e.g. \"fix(vk): ...\"" % ", ".join(TITLE_TYPES))
        state = token()
        fork, _ = ensure_fork(state["login"])
        try:
            api("GET", "/repos/%s/branches/%s" % (fork, urllib.parse.quote(args.branch, safe="")))
        except ForgeError:
            raise ForgeError("no branch %s on %s: push it there first" % (args.branch, fork))
        pr = api("POST", "/repos/%s/pulls" % REPO, {
            "base": "main", "head": "%s:%s" % (state["login"], args.branch),
            "title": ("WIP: " if args.draft else "") + args.title, "body": text_arg(args)})   # (Forgejo's draft mark)
        print(pr["html_url"])
    elif args.action == "list":
        me = token()["login"] if args.mine else None
        page, shown = 1, 0
        while True:
            prs = api("GET", "/repos/%s/pulls?state=%s&limit=50&page=%d" % (REPO, args.state, page)) or []
            for p in prs:
                if me and p["user"]["login"] != me:
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
        p = api("GET", "/repos/%s/pulls/%d" % (REPO, args.number))
        state = "merged" if p.get("merged") else ("draft" if p.get("draft") else p["state"])
        print("#%d %s\n%s, by %s: %s -> %s, %s\n%s\n\n%s" % (
            p["number"], p["title"], state, p["user"]["login"], p["head"]["label"], p["base"]["ref"],
            "mergeable" if p.get("mergeable") else "not mergeable as it is", p["html_url"], (p.get("body") or "").strip()))
        if args.comments:
            show_comments(args.number)
    elif args.action == "comment":
        print(api("POST", "/repos/%s/issues/%d/comments" % (REPO, args.number), {"body": text_arg(args)})["html_url"])
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
    s = sub.add_parser("login", help="sign in with Discord, make your fork, set up git")
    s.add_argument("--upstream", default="forge", help="remote name for MagicBeanDev/Hookshot (default forge)")
    s.add_argument("--fork", default="forge-fork", help="remote name for your fork (default forge-fork)")
    s.add_argument("--no-browser", action="store_true", help="print the link only")
    s.add_argument("--again", action="store_true", help="sign in anew even when signed in")
    sub.add_parser("whoami")
    sub.add_parser("logout")
    s = sub.add_parser("credential")
    s.add_argument("action", choices=["get", "store", "erase"])

    def body_args(s, text=False):
        if text:
            s.add_argument("text", nargs="?")
        s.add_argument("--body")
        s.add_argument("--body-file")

    pr = sub.add_parser("pr").add_subparsers(dest="action", required=True)
    s = pr.add_parser("create", help="from a branch of your fork into Hookshot's main")
    s.add_argument("branch")
    s.add_argument("--title", required=True)
    s.add_argument("--draft", action="store_true", help="mark it work in progress")
    body_args(s)
    s = pr.add_parser("list")
    s.add_argument("--mine", action="store_true")
    s.add_argument("--state", choices=["open", "closed", "all"], default="open")
    s = pr.add_parser("view")
    s.add_argument("number", type=int)
    s.add_argument("--comments", action="store_true")
    s = pr.add_parser("comment")
    s.add_argument("number", type=int)
    body_args(s, text=True)
    s = pr.add_parser("checkout")
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
    commands = {"login": cmd_login, "whoami": cmd_whoami, "logout": cmd_logout, "credential": cmd_credential,
                "pr": cmd_pr, "issue": cmd_issue}
    try:
        return commands[args.cmd](args) or 0
    except (ForgeError, auth.LoginError) as e:
        print("forge.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
