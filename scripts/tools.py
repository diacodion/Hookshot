"""tools.lock: the build tools the Windows Hookshot Builder needs (docs/releases.md, "tools.lock"; RELEASING.md, "Bump a
tool"). Python's standard library only.

    python -I scripts/tools.py check [--require <name> ...]
        reads tools.lock and refuses a bad line (a name, a SHA-256, a size, a path that leaves the install, a link that is not
        https) or a missing tool. The release workflow requires every tool the builder needs (REQUIRED)
    python -I scripts/tools.py fetch <name> [--cache <folder>]
        the tool's archive, from the first of its URLs that answers, checked by its size and SHA-256; prints its path
    python -I scripts/tools.py install <install folder> [--cache <folder>] [--only <name> ...]
        every tool (or the ones named) unpacked into a builder install, as the setup and the launcher do: <prefix> of the
        archive becomes <dest>
    python -I scripts/tools.py mirror <name>
        puts the tool's archive, fetched from its URLs, in Hookshot's "tools" release as <sha256>-<name>.zip, and makes
        that copy its line's first URL, the others the fallback ($RELEASE_TOKEN: your own token)
    python -I scripts/tools.py add <name> <version> <archive> --dest <folder> [--prefix <folder in the archive>]
        an archive Hookshot made, uploaded to the "tools" release and written into tools.lock in place of the tool's line
    python -I scripts/tools.py assemble python --requirements nx2/prompt_requirements.txt --out <zip>
    python -I scripts/tools.py assemble cpm --add <CPM folder> --out <zip>
    python -I scripts/tools.py assemble third-party --from <archive with tools/third_party> --out <zip>
    python -I scripts/tools.py assemble profile --work <folder> --out <zip>
        a new archive from the tool's current one with what Hookshot adds: the pinned prompt packages (installed by this
        Python's pip for the archive's Python version and win_amd64), packages for the CPM cache (dav1d, scripts/windows/
        dav1d.sh), the prebuilt files of third_party (a64emit.dll, lld-link.exe, SDL2.dll, SDL3.dll, zbic.dll) only, or the
        play profile (work/: hookshot_pieces.prof, hot_funcs.txt, hot_order.txt, hookshot.res; addresses and counts, no game
        data). Then `add` it. Each archive is deterministic: the same files give the same bytes
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCK = os.path.join(ROOT, "tools.lock")
CACHE = os.path.join(ROOT, "build-tools-cache")
REPO = "MagicBeanDev/Hookshot"
SERVER = "https://git.magicbean.dev"
TOOLS_TAG = "tools"
MIRROR = "%s/%s/releases/download/%s/" % (SERVER, REPO, TOOLS_TAG)
# (the tools a Windows builder needs: the release workflow refuses a lock without one of them)
REQUIRED = ("llvm-mingw", "python", "emsdk", "cpm", "wgpu-native", "third-party", "profile")
NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$")
PREBUILT_EXT = (".dll", ".exe", ".lib", ".sha1")


class Tool(object):
    def __init__(self, name, version, system, sha256, size, dest, prefix, urls):
        self.name, self.version, self.system, self.sha256 = name, version, system, sha256
        self.size, self.dest, self.prefix, self.urls = size, dest, prefix, urls

    def line(self):
        return "tool %s %s %s %s %d %s %s %s" % (self.name, self.version, self.system, self.sha256, self.size, self.dest,
                                                self.prefix, " ".join(self.urls))


def fail(message):
    raise SystemExit("tools.py: " + message)


def path_ok(p):
    """a relative path with / that stays inside the folder it is under (as builder/setup/setup.c's list_name_ok)"""
    parts = p.split("/")
    return bool(p) and not p.startswith("/") and "\\" not in p and ":" not in p and all(x not in ("", ".", "..") for x in parts) \
        and all(re.match(r"^[0-9A-Za-z._+-]+$", x) for x in parts)


def parse(text):
    tools = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        w = line.split()
        if w[0] != "tool" or len(w) < 9:
            fail("tools.lock line %d: not 'tool <name> <version> <system> <sha256> <size> <dest> <prefix> <url>...'" % n)
        name, version, system, sha, size, dest, prefix, urls = w[1], w[2], w[3], w[4], w[5], w[6], w[7], w[8:]
        problems = [why for ok, why in (
            (NAME.match(name), "name"), (VERSION.match(version), "version"), (system in ("windows-x64", "linux-x64"), "system"),
            (re.fullmatch(r"[0-9a-f]{64}", sha), "sha256"), (size.isdigit() and 0 < int(size) < (1 << 34), "size"),
            (path_ok(dest), "dest"), (prefix == "-" or path_ok(prefix), "prefix"),
            (all(u.startswith("https://") and " " not in u for u in urls), "url")) if not ok]
        if problems:
            fail("tools.lock line %d (%s): bad %s" % (n, name, ", ".join(problems)))
        if any(t.name == name and t.system == system for t in tools):
            fail("tools.lock line %d: %s twice" % (n, name))
        tools.append(Tool(name, version, system, sha, int(size), dest, prefix, urls))
    return tools


def load(path=LOCK):
    with open(path, encoding="utf-8") as f:
        return parse(f.read())


def named(tools, name):
    for t in tools:
        if t.name == name:
            return t
    fail("no %s in tools.lock" % name)


def write_line(tool, path=LOCK):
    """tool's line in place of the one with its name, or added at the end; the comments stay"""
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    out, done = [], False
    for line in lines:
        w = line.split()
        if len(w) > 3 and w[0] == "tool" and w[1] == tool.name and w[3] == tool.system:
            out.append(tool.line())
            done = True
        else:
            out.append(line)
    if not done:
        out.append(tool.line())
    parse("\n".join(out))
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out) + "\n")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def get(url, timeout=600):
    req = urllib.request.Request(url, headers={"User-Agent": "hookshot-tools"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch(tool, cache=CACHE, getter=get):
    """The tool's archive in cache as <sha256>.zip (the same cache for every version), from its first URL that gives the
    right bytes"""
    os.makedirs(cache, exist_ok=True)
    path = os.path.join(cache, tool.sha256 + ".zip")
    if os.path.isfile(path) and os.path.getsize(path) == tool.size and sha256_file(path) == tool.sha256:
        return path
    last = "no URL"
    for url in tool.urls:
        try:
            data = getter(url)
        except (urllib.error.URLError, OSError) as e:
            last = "%s: %s" % (url, e)
            continue
        if len(data) != tool.size or hashlib.sha256(data).hexdigest() != tool.sha256:
            last = "%s: not the size and SHA-256 tools.lock gives" % url
            continue
        with open(path + ".part", "wb") as f:
            f.write(data)
        os.replace(path + ".part", path)
        return path
    fail("%s: %s" % (tool.name, last))


def unpack(archive, prefix, dest):
    """The archive's members under prefix ("-": all) into dest, which must not exist yet; nothing outside dest is written"""
    if os.path.exists(dest):
        fail("%s is there already" % dest)
    tmp = dest + ".part"
    shutil.rmtree(tmp, ignore_errors=True)
    lead = "" if prefix == "-" else prefix.rstrip("/") + "/"
    root = os.path.realpath(tmp)
    n = 0
    with zipfile.ZipFile(archive) as z:
        for m in z.infolist():
            if m.is_dir() or not m.filename.startswith(lead):
                continue
            rel = m.filename[len(lead):]
            out = os.path.join(tmp, *rel.split("/"))
            if ".." in rel.split("/") or os.path.commonpath([os.path.realpath(out), root]) != root:
                fail("%s: %s leaves the folder" % (archive, m.filename))
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with z.open(m) as src, open(out, "wb") as f:
                shutil.copyfileobj(src, f)
            n += 1
    if not n:
        fail("%s has nothing under %s" % (archive, prefix))
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    os.replace(tmp, dest)
    return n


def install(folder, tools, cache=CACHE, getter=get):
    for t in tools:
        dest = os.path.join(folder, *t.dest.split("/"))
        if os.path.isdir(dest):
            print("%-12s there already" % t.name)
            continue
        n = unpack(fetch(t, cache, getter), t.prefix, dest)
        print("%-12s %s: %d files into %s" % (t.name, t.version, n, t.dest))


def write_zip(path, files, prefix):
    """files {relative path: bytes or a file's path} into a deterministic zip under prefix/ (sorted, fixed dates)"""
    with zipfile.ZipFile(path + ".new", "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for rel in sorted(files):
            info = zipfile.ZipInfo(prefix + "/" + rel, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            data = files[rel]
            if not isinstance(data, bytes):
                with open(data, "rb") as f:
                    data = f.read()
            z.writestr(info, data, compresslevel=6)
    os.replace(path + ".new", path)
    return sha256_file(path), os.path.getsize(path)


def members(archive, prefix):
    lead = "" if prefix == "-" else prefix.rstrip("/") + "/"
    with zipfile.ZipFile(archive) as z:
        return {m.filename[len(lead):]: z.read(m) for m in z.infolist() if not m.is_dir() and m.filename.startswith(lead)}


# ---- assembling Hookshot's own archives ----

def assemble_python(tool, archive, requirements, out, pip=None):
    """The Python archive with requirements' pins (name==version) installed into Lib/site-packages, when it lacks one"""
    with open(requirements, encoding="utf-8") as f:
        pins = [p for p in (l.split("#")[0].strip() for l in f.read().splitlines()) if p]
    files = members(archive, tool.prefix)
    have = {m.split("/")[-2].lower() for m in files if "/" in m and m.split("/")[-2].endswith(".dist-info")}
    missing = [p for p in pins if "%s-%s.dist-info" % tuple(x.strip().lower().replace("-", "_") for x in p.split("==")) not in have]
    if not missing:
        print("the Python archive has %s already" % ", ".join(pins))
        return None
    dll = [m for m in files if "/" not in m and m.lower().startswith("python3") and m[7:-4].isdigit()]
    if len(dll) != 1:
        fail("%s: no pythonXY.dll to give the version" % archive)
    version = "3." + dll[0][7:-4]
    stage = out + ".site"
    shutil.rmtree(stage, ignore_errors=True)
    (pip or subprocess.run)([sys.executable, "-m", "pip", "install", "--no-deps", "--only-binary", ":all:", "--no-compile",
                             "--no-cache-dir", "--disable-pip-version-check", "-q", "--platform", "win_amd64", "--python-version",
                             version, "--implementation", "cp", "--target", stage] + missing, check=True)
    for d, _, names in os.walk(stage):
        for n in names:
            full = os.path.join(d, n)
            files["Lib/site-packages/" + os.path.relpath(full, stage).replace(os.sep, "/")] = full
    result = write_zip(out, files, "python")
    shutil.rmtree(stage, ignore_errors=True)
    print("python %s with %s: %s" % (version, ", ".join(missing), out))
    return result


def assemble_cpm(tool, archive, add, out):
    """The CPM cache archive with the <package>/<version> folders of add (dav1d), when it lacks one"""
    want = sorted((n, v) for n in os.listdir(add) if os.path.isdir(os.path.join(add, n))
                  for v in os.listdir(os.path.join(add, n)) if os.path.isdir(os.path.join(add, n, v)))
    if not want:
        fail("%s: no <package>/<version> folders" % add)
    files = members(archive, tool.prefix)
    have = {tuple(m.split("/")[:2]) for m in files}
    missing = [nv for nv in want if nv not in have]
    if not missing:
        print("the CPM archive has %s already" % ", ".join("/".join(nv) for nv in want))
        return None
    for name, version in missing:
        top = os.path.join(add, name, version)
        for d, _, names in os.walk(top):
            for n in names:
                full = os.path.join(d, n)
                files["%s/%s/%s" % (name, version, os.path.relpath(full, top).replace(os.sep, "/"))] = full
    result = write_zip(out, files, "cpm")
    print("cpm with %s: %s" % (", ".join("/".join(nv) for nv in missing), out))
    return result


def assemble_third_party(source, prefix, out, tracked):
    """The prebuilt files of a tools/third_party (rebuilt on the player's PC when their sources' stamps differ), never a
    source file: third_party/'s sources are the repository's"""
    files = {rel: data for rel, data in members(source, prefix).items()
             if rel.lower().endswith(PREBUILT_EXT) and ("third_party/" + rel) not in tracked}
    if not files:
        fail("%s: no prebuilt files under %s" % (source, prefix))
    result = write_zip(out, files, "third_party")
    print("third-party: %s (%s)" % (out, ", ".join(sorted(files))))
    return result


PROFILE = ("hookshot_pieces.prof", "hot_funcs.txt", "hot_order.txt", "hookshot.res", "hookshot.ico")


def assemble_profile(work, out):
    files = {n: os.path.join(work, n) for n in PROFILE if os.path.isfile(os.path.join(work, n))}
    if "hot_order.txt" not in files:
        fail("%s: no hot_order.txt (make_game.py's profile)" % work)
    result = write_zip(out, files, "work")
    print("profile: %s (%s)" % (out, ", ".join(sorted(files))))
    return result


# ---- the "tools" release ----

def api(method, path, data=None, content_type=None):
    base = os.environ.get("FORGEJO_UPLOAD_API") if data and content_type and content_type.startswith("multipart") else None
    base = base or os.environ.get("FORGEJO_API") or SERVER + "/api/v1"
    token = os.environ.get("RELEASE_TOKEN") or fail("RELEASE_TOKEN is needed (your token, with write access to MagicBeanDev/Hookshot)")
    headers = {"Authorization": "token " + token, "User-Agent": "hookshot-tools", "Accept": "application/json"}
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(base.rstrip("/") + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=1200) as r:
            text = r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404 and method == "GET":
            return None
        fail("%s %s: HTTP %d %s" % (method, path, e.code, e.read().decode("utf-8", "replace")[:300]))
    return json.loads(text) if text else None


def tools_release():
    rel = api("GET", "/repos/%s/releases/tags/%s" % (REPO, TOOLS_TAG))
    if rel:
        return rel
    body = ("The archives of tools.lock that Hookshot keeps a copy of or assembles itself, named <sha256>-<name>.zip. Not a "
            "version of Hookshot: the launcher never offers it, and it is never pruned (docs/releases.md, tools.lock).")
    return api("POST", "/repos/%s/releases" % REPO, json.dumps({
        "tag_name": TOOLS_TAG, "target_commitish": "main", "name": "Build tools", "body": body, "draft": False,
        "prerelease": True}).encode(), "application/json")


def upload(path, name):
    rel = tools_release()
    if any(a["name"] == name for a in rel.get("assets") or []):
        print("%s is in the tools release already" % name)
        return MIRROR + name
    with open(path, "rb") as f:
        data = f.read()
    boundary = "hookshottools" + hashlib.sha256(name.encode()).hexdigest()[:16]
    form = (('--%s\r\nContent-Disposition: form-data; name="attachment"; filename="%s"\r\nContent-Type: application/zip\r\n\r\n'
             % (boundary, name)).encode() + data + ("\r\n--%s--\r\n" % boundary).encode())
    got = api("POST", "/repos/%s/releases/%d/assets?name=%s" % (REPO, rel["id"], urllib.parse.quote(name)), form,
              "multipart/form-data; boundary=" + boundary)
    if got.get("size") != len(data):
        fail("%s: uploaded %s bytes of %d" % (name, got.get("size"), len(data)))
    print("uploaded %s" % name)
    return MIRROR + name


def tracked_files(*paths):
    out = subprocess.run(["git", "ls-files", *paths], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return set(out.split())


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--lock", default=LOCK)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("check")
    s.add_argument("--require", nargs="*")
    s = sub.add_parser("fetch")
    s.add_argument("name")
    s.add_argument("--cache", default=CACHE)
    s = sub.add_parser("install")
    s.add_argument("folder")
    s.add_argument("--cache", default=CACHE)
    s.add_argument("--only", nargs="*")
    s = sub.add_parser("mirror")
    s.add_argument("name")
    s.add_argument("--cache", default=CACHE)
    s = sub.add_parser("add")
    s.add_argument("name")
    s.add_argument("version")
    s.add_argument("archive")
    s.add_argument("--dest", required=True)
    s.add_argument("--prefix", default="-")
    s.add_argument("--system", default="windows-x64")
    s = sub.add_parser("assemble")
    s.add_argument("what", choices=("python", "cpm", "third-party", "profile"))
    s.add_argument("--requirements")
    s.add_argument("--add")
    s.add_argument("--from", dest="source")
    s.add_argument("--from-prefix", default="HookshotBuilder/tools/third_party")
    s.add_argument("--work")
    s.add_argument("--out", required=True)
    s.add_argument("--cache", default=CACHE)
    a = p.parse_args(argv)
    tools = load(a.lock)

    if a.cmd == "check":
        missing = [n for n in (REQUIRED if a.require == [] else a.require or ()) if n not in {t.name for t in tools}]
        for t in tools:
            print("%-12s %-22s %s %6d MB  %s" % (t.name, t.version, t.system, t.size >> 20, t.urls[0]))
        if missing:
            print("error: the tools release is missing: %s (tools.lock has no line for them, and a Windows builder needs "
                  "them); RELEASING.md, One-time setup" % ", ".join(missing), file=sys.stderr)
            return 1
    elif a.cmd == "fetch":
        print(fetch(named(tools, a.name), a.cache))
    elif a.cmd == "install":
        install(a.folder, [t for t in tools if not a.only or t.name in a.only], a.cache)
    elif a.cmd == "mirror":
        t = named(tools, a.name)
        name = "%s-%s.zip" % (t.sha256, t.name)
        url = upload(fetch(t, a.cache), name)
        if t.urls[0] != url:
            t.urls = [url] + [u for u in t.urls if u != url]
            write_line(t, a.lock)
        print("%s: %s" % (t.name, " ".join(t.urls)))
    elif a.cmd == "add":
        sha, size = sha256_file(a.archive), os.path.getsize(a.archive)
        url = upload(a.archive, "%s-%s.zip" % (sha, a.name))
        write_line(Tool(a.name, a.version, a.system, sha, size, a.dest, a.prefix, [url]), a.lock)
        print("tools.lock: %s %s %s" % (a.name, a.version, sha))
    elif a.cmd == "assemble":
        if a.what == "python":
            t = named(tools, "python")
            assemble_python(t, fetch(t, a.cache), a.requirements or os.path.join(ROOT, "nx2", "prompt_requirements.txt"), a.out)
        elif a.what == "cpm":
            t = named(tools, "cpm")
            assemble_cpm(t, fetch(t, a.cache), a.add or fail("--add <CPM folder>"), a.out)
        elif a.what == "third-party":
            assemble_third_party(a.source or fail("--from <archive>"), a.from_prefix, a.out, tracked_files("third_party"))
        else:
            assemble_profile(a.work or fail("--work <folder>"), a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
