# Setting this up on a Mac, from nothing

Written for a machine that has never had this project on it. Every command goes
in **Terminal** (press ⌘-Space, type `Terminal`, press Return). Run them one line
at a time and read what comes back before running the next one.

Roughly 20 minutes, most of it waiting.

---

## 1. Command line tools

```bash
xcode-select --install
```

A dialog appears — click **Install** and wait. If it says the tools are already
installed, good, move on.

## 2. uv (installs and runs Python for you)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then close Terminal, open it again, and check:

```bash
uv --version
```

If that prints a version, uv is working. If it says `command not found`, run
`source $HOME/.local/bin/env` and try again.

## 3. Get the code

The awkward part is proving to GitHub who you are. The GitHub CLI handles it
better than anything else:

```bash
brew install gh
gh auth login
```

If `brew` is not installed, install it first with the command on
[brew.sh](https://brew.sh), then re-run the two lines above.

For `gh auth login`, answer: **GitHub.com** → **HTTPS** → **Yes** (authenticate
Git) → **Login with a web browser**. It shows you a code, opens your browser,
you paste the code and sign in as the account that owns the repository.

Then:

```bash
cd ~
gh repo clone LoganJosephLee/2026HyperscalerSupplyChainMaps
cd 2026HyperscalerSupplyChainMaps
git checkout claude/hyperscaler-supply-chain-graph-8p7qt4
```

<details>
<summary>No Homebrew, or gh will not install</summary>

Clone over HTTPS and paste a Personal Access Token when it asks for a password:

```bash
git clone https://github.com/LoganJosephLee/2026HyperscalerSupplyChainMaps.git
```

Make the token at **github.com → Settings → Developer settings → Personal access
tokens → Tokens (classic) → Generate new token**, tick **repo**, copy it. Paste
it as the *password*; your GitHub username is the username. macOS stores it in
the Keychain, so it only asks once.
</details>

## 4. Install the project

```bash
make setup
uv sync --extra anthropic
```

Check it works — this needs no API key and no network:

```bash
make test
```

Every test should pass. If they do, the code is sound and anything that goes
wrong later is configuration, not the project.

## 5. Environment variables, so they survive a reboot

Two values are needed. **Do not paste your API key into a chat window** — copy it
from [console.anthropic.com](https://console.anthropic.com) straight into this
command.

```bash
echo 'export HSCM_EDGAR_CONTACT="your.email@example.com"' >> ~/.zshrc
echo 'export HSCM_EXTRACTOR=anthropic' >> ~/.zshrc
```

For the key, use this so it is never echoed to the screen or saved in shell
history:

```bash
read -rs KEY && echo "export ANTHROPIC_API_KEY=\"$KEY\"" >> ~/.zshrc && unset KEY
```

The cursor sits there showing nothing — paste the key, press Return. That is
correct behaviour, not a hang.

Then load it:

```bash
source ~/.zshrc
```

Confirm all three are set (this prints the key's first few characters only, so
it is safe to read aloud):

```bash
echo "contact: $HSCM_EDGAR_CONTACT"
echo "extractor: $HSCM_EXTRACTOR"
echo "key: ${ANTHROPIC_API_KEY:0:12}…"
uv run hscm check-api
```

## 6. Get the filings back

Cached filings are not in the repository — they are large and free to re-fetch.
This downloads 43 of them and takes a few minutes:

```bash
uv run hscm fetch
uv run hscm sections
```

`sections` should end with `43 filing(s) inspected` and flag only ASML.

## 7. Carry on

```bash
uv run hscm extract     # resumes if it was interrupted; never pays twice
uv run hscm verify data/extractions.json --out report.json
uv run hscm review edit
uv run hscm build
make serve              # then open http://localhost:8000
```

---

## Before you leave the other machine

Extraction output is tracked now, so it travels — but only if it is pushed:

```bash
git add -A
git commit -m "Extraction run so far"
git push -u origin claude/hyperscaler-supply-chain-graph-8p7qt4
```

Do this even mid-run. `data/extractions.json` is written after every section, so
whatever is committed is real, and the Mac will resume from exactly there rather
than paying for those sections again.

## If something breaks

| What you see | What it means |
|---|---|
| `command not found: uv` | Terminal was not restarted after step 2. `source $HOME/.local/bin/env` |
| `command not found: make` | Step 1 did not finish. Re-run `xcode-select --install` |
| `No company spine at …` | Run `uv run hscm fetch` first |
| `HSCM_EDGAR_CONTACT` complaint | `source ~/.zshrc`, or the line did not get added |
| `conflict markers` in aliases.yaml | `git checkout --theirs -- aliases.yaml` then `git add aliases.yaml` |
| Permission denied on push | Wrong GitHub account cached. `gh auth login` again |
