# Working on git.magicbean.dev

Hookshot lives on the team's Forgejo, [git.notrexed.dev/Hookshot/Hookshot](https://git.notrexed.dev/Hookshot/Hookshot).
Its `main` takes reviewed pull requests only. You work on branches of your own fork and open pull requests from there,
as on GitHub.

One sign-in covers everything on magicbean.dev: Forgejo signs people in with Discord, and the team chat (Matrix) asks
Forgejo who you are. So you sign in once, with **Sign in with Discord**, and git, the team chat and test requests all
use it. The first time creates your account.

## Set up once

From the repository folder:

```
python -I scripts/forge.py login
```

It prints a link and opens it: click **Sign in with Discord** and approve. Then it:

- adds the remote `forge` (MagicBeanDev/Hookshot: fetch `main` from it, never push to it);
- becomes git's credential helper for git.magicbean.dev only, so `git push`, `git fetch` and `jj git push` sign in by
  themselves. The token renews itself when it runs out.

The sign-in is saved in your config folder (Linux `~/.config/hookshot/`, Windows `%APPDATA%\Hookshot\`), readable by
you only, never in the repository. `python -I scripts/forge.py whoami` shows who is signed in, and `logout` undoes it.

## Set up pushing

Reading, building and testing need only the sign-in. When you have something to send:

```
python -I scripts/forge.py fork
```

It makes your fork, `<you>/Hookshot`, if you have none, and adds the remote `forge-fork` for it: push your branches
there. (`pr create` does this too if it hasn't been done.)

## A change, start to finish

```
jj git fetch --remote forge                                      (or: git fetch forge)
jj new main@forge -m "Fix the walls on Intel Arc"                (or: git switch -c fix-arc-walls forge/main)
... edit, build, test ...
jj bookmark create fix-arc-walls -r @
jj git push --remote forge-fork -b fix-arc-walls                 (or: git push forge-fork fix-arc-walls)
python -I scripts/forge.py pr create fix-arc-walls --title "fix(vk): walls missing on Intel Arc" --body-file pr.md
```

`pr create` prints the pull request's link. Title and description follow
[CONTRIBUTING.md](../CONTRIBUTING.md#commits-and-pull-requests). `--draft` marks it work in progress.

## Other commands

```
python -I scripts/forge.py pr list [--mine] [--state open|closed|all]
python -I scripts/forge.py pr view 12 [--comments]
python -I scripts/forge.py pr comment 12 "Tested on an RX 6700 XT: fixed."
python -I scripts/forge.py pr checkout 12                         its head as the bookmark (jj) or branch (git) pr/12
python -I scripts/forge.py issue list [--label bug]
python -I scripts/forge.py issue view 40 --comments
python -I scripts/forge.py issue create --title "..." --body-file issue.md
python -I scripts/forge.py issue comment 40 "..."
```

## fj, the Forgejo command line

For more than the above (reviews, labels, releases, browsing), install [fj](https://codeberg.org/forgejo-contrib/forgejo-cli/wiki/Installation):
a release binary for Windows or Linux, `cargo install --locked forgejo-cli`, `brew install forgejo-cli`, or your
distribution's `forgejo-cli` package.

fj's browser sign-in works only on servers it knows, and git.magicbean.dev isn't one of them, so give it a token: make one on
[git.magicbean.dev/user/settings/applications](https://git.magicbean.dev/user/settings/applications) (repository and
issue access), then run `fj -H git.magicbean.dev auth add-token` and paste it. Inside the repository, fj finds the host
from the remotes. For example, `fj pr create --base ^ "Title"` opens a pull request from your fork's branch into
Hookshot.

## If something goes wrong

- `not signed in`, or git asks for a password: run `python -I scripts/forge.py login --again`.
- `no branch ... on <you>/Hookshot`: push the branch to `forge-fork` first.
- A push to `forge` is refused: that is Hookshot itself. Push to `forge-fork` and open a pull request.
