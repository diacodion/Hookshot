"""The Windows Hookshot Builder's release zip (docs/releases.md), made in CI (.forgejo/workflows/release.yml, ci.yml). No game
data is involved. Python's standard library only.

    python -I scripts/builder_package.py llvm-mingw <folder> [--name]
        tools.lock's llvm-mingw (its SHA-256 checked) unpacked into <folder>; prints its folder. --name: only its folder's name
        (a cache key)
    python -I scripts/builder_package.py package <out folder> --publish <dotnet publish folder> --setup <setup exe>
        the builder's folder, HookshotBuilder\\ in the zip: the published builder, the setup (its Repair runs it), LICENSE,
        the tracked gpu, host, nx2, scripts and third_party under tools\\, and the pipeline state list's sources
        (assets/pipeline_states/, which must be there and read). None of tools.lock's tools: the setup or the launcher adds
        those on the player's PC
    python -I scripts/builder_package.py zip <package folder> <version> --setup <setup exe> --out <zip>
        HookshotBuilder-<version>-windows-x64.zip: HookshotBuilder.exe (the setup), HookshotBuilder\\ (the package),
        tools.lock, VERSION and recomplauncher.json (RecompLauncher reads it at the top). Deterministic: the same files give
        the same bytes
"""
import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def tools_module():
    spec = importlib.util.spec_from_file_location("tools", os.path.join(ROOT, "scripts", "tools.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def llvm_mingw(a):
    tools = tools_module()
    t = tools.named(tools.load(), "llvm-mingw")
    if a.name:
        print(t.prefix)
        return 0
    folder = os.path.join(a.folder, t.prefix)
    if not os.path.isfile(os.path.join(folder, "bin", "clang.exe")):
        with zipfile.ZipFile(tools.fetch(t, os.path.join(a.folder, ".cache"))) as zf:
            zf.extractall(a.folder)
    print(folder)
    return 0


def tracked(*paths):
    r = subprocess.run(["git", "ls-files", "-z", "--"] + list(paths), cwd=ROOT, check=True, stdout=subprocess.PIPE)
    return [f for f in r.stdout.decode("utf-8").split("\0") if f]


def copy(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)


def package(a):
    out = os.path.abspath(a.out)
    if os.path.exists(out):
        shutil.rmtree(out)
    shutil.copytree(a.publish, out)
    copy(a.setup, os.path.join(out, "HookshotBuilder-Setup.exe"))   # (the builder's Repair runs it)
    copy(os.path.join(ROOT, "LICENSE"), os.path.join(out, "LICENSE"))
    tools = os.path.join(out, "tools")
    for f in tracked("gpu", "host", "nx2", "scripts", "third_party"):
        copy(os.path.join(ROOT, f), os.path.join(tools, *f.split("/")))
    if state_list(tools) != 0:
        return 1
    print("package folder: %s" % out)
    return 0


def state_list(tools):
    """The sources of the pipeline state list a release ships (assets/pipeline_states/: the tour's list, the contributed ones and
    their notes), into tools/ at the same path: nx2/make_game.py merges them beside the game. Required: without them a new
    install's first field load is drawn white for seconds while its pipelines compile (v2.0.8 to v2.0.14 shipped none). 1, with
    the reason, when there are none or one does not read (nx2/pipeline_states.py check)"""
    spec = importlib.util.spec_from_file_location("pipeline_states", os.path.join(ROOT, "nx2", "pipeline_states.py"))
    states = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(states)
    src = states.sources_dir(ROOT)
    try:
        recs = states.check_sources(src)
    except SystemExit as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    dst = states.sources_dir(tools)
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)
    print("the pipeline state list: %s" % states.describe(recs))
    return 0


def make_zip(package_dir, version, setup, out):
    entries = {"HookshotBuilder.exe": setup, "tools.lock": os.path.join(ROOT, "tools.lock"),
               "recomplauncher.json": os.path.join(ROOT, "recomplauncher.json"), "VERSION": (version + "\n").encode()}
    for d, dirs, names in os.walk(package_dir):
        dirs.sort()
        for n in names:
            full = os.path.join(d, n)
            entries["HookshotBuilder/" + os.path.relpath(full, package_dir).replace(os.sep, "/")] = full
    with zipfile.ZipFile(out + ".new", "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for name in sorted(entries):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            data = entries[name]
            if not isinstance(data, bytes):
                with open(data, "rb") as f:
                    data = f.read()
            z.writestr(info, data, compresslevel=6)
    os.replace(out + ".new", out)
    print("%s: %d files, %d MB" % (out, len(entries), os.path.getsize(out) >> 20))
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("llvm-mingw")
    s.add_argument("folder", nargs="?", default="")
    s.add_argument("--name", action="store_true")
    s = sub.add_parser("package")
    s.add_argument("out")
    s.add_argument("--publish", required=True)
    s.add_argument("--setup", required=True)
    s = sub.add_parser("zip")
    s.add_argument("package")
    s.add_argument("version")
    s.add_argument("--setup", required=True)
    s.add_argument("--out", required=True)
    a = p.parse_args()
    if a.cmd == "zip":
        return make_zip(a.package, a.version, a.setup, a.out)
    return {"llvm-mingw": llvm_mingw, "package": package}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
