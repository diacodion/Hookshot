# Forgejo Actions runners

Hookshot's CI runs on Forgejo Actions (`.forgejo/workflows/`), on runners the team registers with git.magicbean.dev. None
are registered yet, and Actions is off on the repositories. These are manual steps for an admin of the instance or of
MagicBeanDev; nothing here needs a game.

## Turn Actions on

Repository *Settings > Units > Actions* (or the API's `has_actions`), on MagicBeanDev/Hookshot and
MagicBeanDev/LongshotLauncher. The secrets and the variable the release workflows read are in [RELEASING.md](../RELEASING.md), *One-time setup*.

## The labels the workflows ask for

| Label | Used by | What it must be |
|---|---|---|
| `ubuntu-latest` | `release.yml`, `ci.yml` (its title check and its Linux checks run on `ubuntu-24.04`), `third-party.yml`, the launcher's `ci.yml` and `release.yml` | Linux, Docker: e.g. `ubuntu-latest:docker://ghcr.io/catthehacker/ubuntu:act-24.04` (Node for the actions, git, Python 3, curl, sudo and apt) |
| `ubuntu-24.04` | `ci.yml`'s Linux parts, `build-linux.yml`, `build-windows.yml`'s Linux jobs (the Vulkan back end with llvm-mingw, dav1d), `vulkan-dlss.yml` | the same image; the AppImage job builds inside Ubuntu 22.04 itself (`scripts/linux/deps.sh` runs in its container) and needs Docker or enough disk (about 30 GB) |
| `windows-2022` | `ci.yml`'s Windows parts, `build-windows.yml`'s zip job (`dotnet publish` of the WPF builder, the setup with llvm-mingw) | a Windows runner in host mode: Git for Windows (its bash), Python 3, the .NET 8 SDK; it may instead be replaced by a Linux job that cross-compiles with llvm-mingw (the setup and the Vulkan back end already do), with `EnableWindowsTargeting` for the WPF publish |

## Register a runner

1. Download [forgejo-runner](https://code.forgejo.org/forgejo/runner/releases) for the machine.
2. In MagicBeanDev's *Settings > Actions > Runners*, *Create new runner*, and copy the token.
3. `forgejo-runner register --instance https://git.magicbean.dev --token <token> --name <name> --labels
   'ubuntu-latest:docker://ghcr.io/catthehacker/ubuntu:act-24.04,ubuntu-24.04:docker://ghcr.io/catthehacker/ubuntu:act-24.04'`
   (a Windows runner: `--labels windows-2022:host`).
4. Run it as a service (`forgejo-runner daemon`). One Linux runner is enough to start; the builder jobs are long (an hour
   and more) and want 4+ cores, 16 GB of memory and about 50 GB of disk.

## Actions from outside

The workflows name actions by full URL: `https://github.com/actions/...` pinned by commit, and
`https://data.forgejo.org/forgejo/upload-artifact@v4` / `download-artifact@v4` (GitHub's v4 and later refuse a server
that is not github.com). The runners must reach github.com and data.forgejo.org, or those actions must be mirrored and
the URLs changed. `actions/cache` needs the runner's cache server (on by default in forgejo-runner).

## Uploads larger than 100 MB

The release workflows upload the builders (hundreds of MB) to the release. git.magicbean.dev is behind Cloudflare, which
cuts a request body at 100 MB, so a runner on the server's own network should upload to the Forgejo it can reach
directly: set the organization variable `RELEASE_UPLOAD_API` to that address's API (e.g. `http://magicbean-git:3000/api/v1`; a variable can't start with `FORGEJO_`).
RELEASING.md, One-time setup, has the rest (the attachment limits in `app.ini`).
