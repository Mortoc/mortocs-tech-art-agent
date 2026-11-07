# mortocs-tech-art-agent: notes for agents (and people)

## ⚠ This repository is PUBLIC

Everything committed here, and everything in its history, is readable by the whole internet **forever**. A forced
rewrite does not un-publish it (forks, caches, scrapers). So **before every commit, push, PR, release or doc edit**:

1. **Run the tripwire:** `scripts/check-secrets --staged --strict` (the pre-commit hook does; CI repeats it). Also look
   at what you are about to publish yourself: `git status`, `git diff --cached --stat`, and any *generated* files
   (`docs/blender/`, UI builds, lock files, test output).
2. **No secrets, ever.** API keys, tokens, passwords, private keys, `.env` files, 1Password exports, session
   cookies, and anything pasted from a terminal that might hold one. Secrets live **only** in sops-encrypted
   `secrets/*.env` (recipients in `.sops.yaml`) and in the environment at runtime.
3. **No company data, and no private names: write the alias** (next section). Hostnames, people's names or personal
   emails, vault and item names, 1Password item references, internal URLs and IPs, private repository and project
   names, home-directory paths. The checker catches the *shapes* of these, and the actual names once they are in
   `.renames`; it cannot know a name nobody has told it, so noticing one is your job.
4. **Public keys are fine** (age recipients, SSH `.pub`). **Ciphertext is public forever**: only commit it if every
   recipient key is one you would never lose, and the underlying credential is spend-capped and rotatable.
5. **Never print a secret to see it.** To check what a secrets file holds: `sops decrypt FILE | cut -d= -f1` (names only).
6. **If one gets out:** rotate the credential at the provider *first*, then remove it. Deleting a commit is not a fix.

On Squatch machines the `company-keys` skill carries the full protocol for working with keys: secret-touching commands
(`op`, `gh secret`, anything that decrypts) are **queued in a tmux pane for the human to inspect and run**, never run by
the agent, and the agent never reads a secret value. Follow it.

GitHub secret scanning and push protection are enabled on this repo. The only Actions secret is `SOPS_AGE_KEY`, the
private half of a CI-only age key (a child of the company sops chain); it decrypts `secrets/*.env` and nothing else.
Do not add workflows that expose it to untrusted code: no `pull_request_target`, and keep jobs that run PR code apart
from jobs that use secrets.

## Private names: `.renames` and aliases

`.renames` is a **local, gitignored** table (`private name<TAB>alias`, one per line; the owner copies it between
machines by hand; it is never committed and the checker refuses to let it be). It is the project's memory of *what we
were talking about* with the names removed, so that notes, comments and docs stay readable and consistent.

- **Write the alias, never the name,** in everything that gets published: code comments, docstrings, docs, commit
  messages, PR and issue text, test fixtures, branch names, file names, extension metadata. Use the *same* alias for
  the same thing everywhere; never invent a second one. (In chat with the owner, real names are fine. The rule is
  about what is written into the repo or sent to GitHub.)
- **Translate with the tool, not from memory:** `scripts/rename --lookup TERM` (name or alias), `--list`, and
  `--reveal FILE` (prints the file with aliases turned back into names, to the terminal only, for reading).
- **When you notice a private name that is not in the table** (in a file, a log, a tool result, something the owner
  said that is about to be written down), **add it before you write anything with it:**
  `scripts/rename --add NAME ALIAS`, choosing a plain, unrelated alias (fruit, melon, orchard...) that does not hint at
  the original. Then `scripts/rename` (dry run) and `scripts/rename --write` to sweep the tree.
- **If `.renames` is missing on this machine,** ask the owner to copy it over. Until then write generically ("a dev
  machine", "the company NixOS config") and do not copy proper nouns out of private systems.
- **Enforcement is local:** `.githooks/pre-commit` and `.githooks/commit-msg` run `scripts/check-secrets`, which
  fails on any private name in file contents, file names or the commit message, and prints only the alias. CI has no
  copy of `.renames` (it would list the very names it hides), so CI only sees the generic shapes. The hook is the
  real guard: do not bypass it (`--no-verify`).
- Replacement respects word boundaries and case variants are automatic; for a name embedded in an identifier use
  `scripts/rename --write --loose`. Rebuild `docs/blender/` after renaming anything under `blender/` or `python/`.

## What this is

One agent core (Python: FastAPI + pydantic-ai) that Houdini and Blender (and later a game) connect to. See `README.md`
and `docs/specs/2026-11-03-core-and-clients.md`.

```
core/        the service (tech_art_core), its tests, a demo harness for browser tests
python/      tech_art_agent: the standard-library-only client the apps load (also vendored into the Blender extension)
houdini/     the Houdini package (houdini_pi): runtime, helpers, panel, tests against a fake hou
blender/     the Blender extension source; scripts/build_blender.py builds docs/blender/ (served by GitHub Pages)
protocol/ ui/  the pane (React) and its wire types
nix/ flake.nix  the NixOS module and packages
scripts/     install, build_blender, check-secrets
```

## Checks

```
uv run --project core pytest              # all Python (real Blender / Houdini tests skip when absent)
npm run typecheck && npm test             # pane UI
npm run build && npm run test:e2e         # browser tests against the real core + a fake local model server
scripts/check-secrets --all --strict      # the public-repo tripwire (secrets, company data, private names)
scripts/rename                            # dry run: where does a private name still appear?
```

Rebuild `docs/blender/` (`scripts/build_blender.py`) whenever `blender/` or `python/` changes.
