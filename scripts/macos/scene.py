#!/usr/bin/env python3
"""(macos) A measured run in a named scene: the dev loop's middle step, on the control bridge and the frame trace.

    python3 -I scripts/macos/scene.py kokiri                      # boot, Continue, settle, measure 60 s, summary
    python3 -I scripts/macos/scene.py market --measure 90 RX_VK_CHUNK=60
    python3 -I scripts/macos/scene.py kokiri --ab RX_VK_CAPTURE_WAIT=0 --repeat 2   # alternating runs, per-arm results
    python3 -I scripts/macos/scene.py --list

What it does, so that nothing of the player's is touched and each run starts the same:
  - the game runs from a dev folder (<work>/dev): clones (APFS copy-on-write, free) of the build's binaries, taken again
    whenever the build changed, and of its program caches the first time. Its own options file
    (scripts/macos/bench_options.ini on the first run: uncapped, the fps counter on) and frame trace live there. Metal's
    shader cache is per user, so it is warm for the dev folder too.
  - the saves are a fresh copy of <work>/dev/clean-user (made once from <work>/sa/user) for every run: a teleport persists
    into saves.
  - the title and the file select are pressed through by state (A until the bridge says play), not by fixed times;
    a teleport (docs/control.md) then goes to the scene.
  - the measured window starts once the scene has settled: no draws left out for pipelines still compiling (the frame
    trace's left_out at its floor) and no waits for pipelines, for --settle seconds in a row.
  - <work>/runs/<name>/: log.txt, frames.csv (RX_VK_FRAME_TRACE), shot.png, summary.json (the build, the times to play
    and to settle, fps and frame-time percentiles of the window, the trace's means).
One game at a time; measuring while a build runs on the same machine skews both.
"""
import argparse, csv, datetime, json, os, shutil, signal, statistics, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from hookshot_ctl import Bridge, Failed   # noqa: E402

WORK = os.path.abspath(os.environ.get("OOT_WORK") or os.path.join(ROOT, "..", "work"))
BUILD, DEV = os.path.join(WORK, "sa", "build"), os.path.join(WORK, "dev")
SCENES = {   # name: the teleport destination's map (None: where the save is, Kokiri Forest for the bench save)
    "kokiri": None,
    "market": "Market_Child",
    "kakariko": "KakarikoVillage_Child",
    "field": "HyruleField_Child",
    "lostwoods": "LostWood_Child",
    "deathmountain": "DeathMountainTrail_Child",
    "zora": "ZoraRiver_Child",
}
BINARIES = ["hookshot", "hookshot_vk.so", "language.txt"]
CACHES = ["hookshot_programs.bin", "hookshot_programs.idx", "hookshot_pipelines.bin", "hookshot_pipeline_states.bin",
          "hookshot_computes.bin"]


def clone(src, dst):
    if os.path.lexists(dst):
        os.remove(dst)
    subprocess.run(["cp", "-c", src, dst], check=True)   # (APFS clone; a plain copy where the volume cannot)


def dev_folder():
    os.makedirs(DEV, exist_ok=True)
    for f in BINARIES:   # (taken again when the build's copy is newer or another size)
        s, d = os.path.join(BUILD, f), os.path.join(DEV, f)
        if os.path.exists(s) and (not os.path.exists(d) or os.path.getmtime(s) > os.path.getmtime(d) or
                                  os.path.getsize(s) != os.path.getsize(d)):
            clone(s, d)
    for f in CACHES:
        s, d = os.path.join(BUILD, f), os.path.join(DEV, f)
        if os.path.exists(s) and not os.path.exists(d):
            clone(s, d)
    ini = os.path.join(DEV, "hookshot_options.ini")
    if not os.path.exists(ini):
        shutil.copyfile(os.path.join(HERE, "bench_options.ini"), ini)
    clean = os.path.join(DEV, "clean-user")
    if not os.path.isdir(clean):
        shutil.copytree(os.path.join(WORK, "sa", "user"), clean, symlinks=False)
    return clean


def rows(path, lo=0, hi=1 << 60):
    try:
        with open(path, newline="") as f:
            return [r for r in csv.DictReader(f) if None not in r.values() and "" not in r.values()
                    and lo <= int(r["frame"]) < hi]
    except FileNotFoundError:
        return []


def stats(rs):
    ms = sorted(float(r["ms"]) for r in rs)
    if not ms:
        return {}
    q = lambda p: round(ms[min(len(ms) - 1, int(p * len(ms)))], 2)
    mean = lambda c: round(statistics.fmean(float(r[c]) for r in rs), 2)
    out = {"frames": len(ms), "seconds": round(sum(ms) / 1000, 1), "fps": round(1000 * len(ms) / sum(ms), 2),
           "ms_p50": q(.5), "ms_p95": q(.95), "ms_p99": q(.99), "ms_max": round(ms[-1], 1),
           "over_33ms_pct": round(100 * sum(m > 33.34 for m in ms) / len(ms), 1), "over_50ms": sum(m > 50 for m in ms)}
    for c in ("left_out", "pipe_waits", "late_wait_ms", "memsync_ms", "draw_ms", "desc_ms", "cmds_ms", "programs_ms",
              "slot_wait_ms", "gpu_behind_ms", "readback_wait_ms"):
        out[c] = mean(c)
    return out


def log_facts(log):
    facts = {}
    try:
        lines = open(log, errors="replace").read().splitlines()
    except FileNotFoundError:
        return facts
    for ln in lines:
        if "vk: driver: " in ln:
            facts["driver"] = ln.split("vk: driver: ", 1)[1].strip()
        elif "vk: device 0: " in ln:
            facts["device"] = ln.split("vk: device 0: ", 1)[1].split(",")[0]
    gpu = [ln for ln in lines if "vk: GPU time " in ln]
    if gpu:
        facts["gpu_time_line_ms"] = float(gpu[-1].split("vk: GPU time ", 1)[1].split()[0])
    pres = [ln for ln in lines if " presents, " in ln and " draws " in ln]
    if pres:
        facts["draws"] = float(pres[-1].split("a frame: ", 1)[1].split()[0])
    return facts


def build_id():
    def git(*a):
        return subprocess.run(["git", "-C", ROOT, *a], capture_output=True, text=True).stdout.strip()
    return git("rev-parse", "--short", "HEAD") + ("-dirty" if git("status", "--porcelain", "--untracked-files=no") else "")


def run(scene, name, measure, settle, settle_max, env_extra, window):
    if subprocess.run(["pgrep", "-x", "hookshot"], capture_output=True).returncode == 0:
        sys.exit("another game is running: one at a time")
    clean = dev_folder()
    out = os.path.join(WORK, "runs", name)
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    os.chmod(out, 0o700)   # (the bridge wants a folder of this user's, writable by no one else)
    user = os.path.join(out, "user")
    shutil.copytree(clean, user)
    sock = os.path.join(out, "c.sock")
    if len(sock) > 100:
        sys.exit("socket path too long for macOS (%d): use a shorter --name or OOT_WORK" % len(sock))
    trace = os.path.join(DEV, "hookshot_frames.csv")
    if os.path.exists(trace):
        os.remove(trace)
    romfs = os.environ.get("OOT_ROMFS") or next(
        l.strip() for l in open(os.path.join(WORK, "dump.txt")) if l.strip() and not l.startswith("#")) + "/romfs"
    env = dict(os.environ, RX_BACKEND="vulkan", HOS_AUDIO="0", HOS_CONTROL=sock, RX_VK_FRAME_TRACE="1",
               RX_VK_SHOTS=os.path.join(out, "shots"), RX_VK_SHOT_EVERY="0")
    env.pop("HOS_INPUT_SCRIPT", None)
    if window:
        env.pop("HOS_HEADLESS", None)
    else:
        env["HOS_HEADLESS"] = "1"
    for kv in env_extra:
        k, v = kv.split("=", 1)
        env[k] = v
    t0 = time.monotonic()
    times = {}
    summary = {"scene": scene, "name": name, "build": build_id(), "env": env_extra, "window": window,
               "when": datetime.datetime.now().isoformat(timespec="seconds")}
    with open(os.path.join(out, "log.txt"), "w") as log, open(os.path.join(out, "stdout.txt"), "w") as so:
        game = subprocess.Popen([os.path.join(DEV, "hookshot"), os.path.join(WORK, "sa", "image", "image.bin"),
                                 "--romfs", romfs, "--user", user], cwd=out, env=env, stdout=so, stderr=log)
    try:
        while not os.path.exists(sock):
            if game.poll() is not None:
                raise Failed("the game exited (%d) before its bridge started: see %s/log.txt" % (game.returncode, out))
            time.sleep(0.2)
        b = Bridge(sock, timeout=30)
        b.request("wait", timeout=900, event="frame", frame=1)
        times["first_frame_s"] = round(time.monotonic() - t0, 1)
        while True:   # (the title and the file select: A until the save is loaded and Link is in play)
            st = b.request("status")
            g = st.get("game") or {}
            if st.get("state") == "play" and st.get("map") and not g.get("loading") and not g.get("event"):
                break
            if time.monotonic() - t0 > 600:
                raise Failed("not in play after 600 s")
            if st.get("state") != "loading":
                b.request("press", buttons="A", frames=6)
            b.request("wait", timeout=10, event="present", frames=20)
        times["in_play_s"] = round(time.monotonic() - t0, 1)
        target = SCENES.get(scene, scene)
        if target and st.get("map") != target:
            for attempt in range(6):   # (refused while an event or a load plays: tried again)
                r = b.request("teleport", to=target)
                if r.get("ok") and r.get("sent"):
                    break
                time.sleep(5)
            else:
                raise Failed("teleport to %s not taken: %s" % (target, r))
            # (the status polled, not a wait for the map event: that event can come before the wait is asked for, and a
            # first visit's load, its pipelines compiling, has taken over 2 minutes)
            arrive_by = time.monotonic() + 600
            while True:
                st = b.request("status")
                g = st.get("game") or {}
                if st.get("map") == r.get("map", target) and st.get("state") == "play" and not g.get("loading"):
                    break
                if time.monotonic() > arrive_by:
                    raise Failed("did not arrive in %s: %s" % (target, st))
                time.sleep(1)
            times["arrived_s"] = round(time.monotonic() - t0, 1)
        arrive_frame = b.request("status")["frame"]
        # settle: the trace's left_out at its floor (a program that never compiles is left out every frame: the floor
        # is the minimum seen) and no pipeline waits, for `settle` seconds of frames in a row
        start = None
        while True:
            time.sleep(2)
            rs = rows(trace, arrive_frame + 60)
            if rs:
                tail_s, i = 0.0, len(rs)
                while i > 0 and tail_s < settle * 1000:
                    i -= 1
                    tail_s += float(rs[i]["ms"])
                tail = rs[i:]
                floor = min(float(r["left_out"]) for r in rs)
                if tail_s >= settle * 1000 and all(float(r["left_out"]) <= floor + 0.5 and float(r["pipe_waits"]) == 0
                                                   for r in tail):
                    start = int(rs[-1]["frame"]) + 1
                    break
            if time.monotonic() - t0 - times.get("arrived_s", times["in_play_s"]) > settle_max:
                start = int(rs[-1]["frame"]) + 1 if rs else arrive_frame
                summary["not_settled"] = True
                break
        times["settled_s"] = round(time.monotonic() - t0, 1)
        time.sleep(measure)
        end = b.request("status")["frame"]
        b.request("shot", timeout=30, path=os.path.join(out, "shot.png"))
        times["total_s"] = round(time.monotonic() - t0, 1)
        try:
            b.request("quit", timeout=10)
        except Failed:
            pass
        game.wait(timeout=30)
    except (Failed, KeyboardInterrupt) as e:
        summary["error"] = str(e)
    finally:
        if game.poll() is None:
            game.send_signal(signal.SIGKILL)
            game.wait()
    if os.path.exists(trace):
        shutil.copyfile(trace, os.path.join(out, "frames.csv"))
    shutil.rmtree(user, ignore_errors=True)
    summary["times"] = times
    if "error" not in summary:
        summary["window"] = [start, end]
        summary.update(stats(rows(os.path.join(out, "frames.csv"), start, end)))
        # (before the window: the start and the trip to the scene. Frames of over 250 ms are stalls, a freeze to the player)
        before = [float(r["ms"]) for r in rows(os.path.join(out, "frames.csv"), 0, start)]
        summary["stall_s_before"] = round(sum(m for m in before if m > 250) / 1000, 1)
        summary["frames_over_1s_before"] = sum(m > 1000 for m in before)
    summary.update(log_facts(os.path.join(out, "log.txt")))
    with open(os.path.join(out, "summary.json"), "w") as f:
        json.dump(summary, f, indent=1)
    return summary


def line(s):
    if "error" in s:
        return "%-14s ERROR %s" % (s["name"], s["error"])
    t = s["times"]
    return ("%-14s %5.1f fps  p50 %5.1f  p95 %5.1f  p99 %5.1f ms  >33ms %4.1f%%  draws %5.0f  left_out %.1f  "
            "[play %ss, settled %ss, stalls %ss before, %s frames]%s" % (
                s["name"], s["fps"], s["ms_p50"], s["ms_p95"], s["ms_p99"], s["over_33ms_pct"], s.get("draws", 0),
                s["left_out"], t.get("in_play_s"), t.get("settled_s"), s.get("stall_s_before"), s["frames"],
                                                      "  NOT SETTLED" if s.get("not_settled") else ""))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("scene", nargs="?", help="a name from --list, or a teleport destination's map")
    p.add_argument("env", nargs="*", help="NAME=VALUE switches for the game")
    p.add_argument("--name", help="the run's folder under <work>/runs (default: the scene)")
    p.add_argument("--measure", type=float, default=60, help="seconds measured once settled (60)")
    p.add_argument("--settle", type=float, default=10, help="seconds in a row with nothing left out to call it settled (10)")
    p.add_argument("--settle-max", type=float, default=600, help="measure anyway after this long (600)")
    p.add_argument("--window", action="store_true", help="a window instead of headless (it opens on the screen)")
    p.add_argument("--ab", metavar="NAME=VALUE", action="append", default=[],
                   help="alternate runs without and with these switches; per-arm results at the end")
    p.add_argument("--repeat", type=int, default=2, help="runs per arm with --ab (2)")
    p.add_argument("--list", action="store_true", help="the scene names")
    a = p.parse_args()
    if a.list or not a.scene:
        for k, v in SCENES.items():
            print("%-14s %s" % (k, v or "(the save's own place)"))
        return
    name = a.name or a.scene
    if not a.ab:
        print(line(run(a.scene, name, a.measure, a.settle, a.settle_max, a.env, a.window)))
        return
    arms = {"base": [], "ab": []}
    for i in range(a.repeat):
        for arm, extra in (("base", []), ("ab", a.ab)):
            s = run(a.scene, "%s_%s%d" % (name, arm, i + 1), a.measure, a.settle, a.settle_max, a.env + extra, a.window)
            print(line(s), flush=True)
            arms[arm].append(s)
    for arm, ss in arms.items():
        ok = [s for s in ss if "fps" in s]
        if ok:
            print("%-5s %s: fps %s (mean %.2f), p95 %s" % (arm, " ".join(a.ab) if arm == "ab" else "(none)",
                                                         " ".join("%.1f" % s["fps"] for s in ok),
                                                         statistics.fmean(s["fps"] for s in ok),
                                                         " ".join("%.1f" % s["ms_p95"] for s in ok)))


if __name__ == "__main__":
    main()
