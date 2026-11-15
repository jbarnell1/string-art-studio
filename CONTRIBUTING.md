# Contributing

Thanks for looking. This is a hobby project built for one household and shared in case it is
useful to others. Bug reports, photos of finished pieces and small, focused pull requests are all
welcome.

## Before you start

- For anything larger than a fix, open an issue first so we can agree on the shape of it.
- Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for how the parts fit and
  [docs/DECISIONS.md](docs/DECISIONS.md) for why things are the way they are.
  [CLAUDE.md](CLAUDE.md) holds the working rules the project follows; they apply to people too.

## Set up

You need Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/jbarnell1/string-art-studio.git
cd string-art-studio
uv sync --extra dev
uv run string-art-studio serve      # http://127.0.0.1:8050
```

## Checks

CI runs the same three things on every push and pull request:

```bash
uv run ruff check .
uv run pytest -q
docker build -t string-art-studio:dev .
```

The tests need no network and no GPU; the API test runs a real, tiny generation end to end.

## Rules of the road

- **Tests ship with the code.** A fix comes with a test that fails without it.
- **Engine changes come with numbers.** Run `uv run python scripts/bench.py` on
  `samples/portrait-man.jpg` (and with `--palette auto4` on `samples/portrait-color.jpg` for
  colour) before and after, and put both results in the pull request. Compare the lightness error,
  not the line count.
- **One prep pipeline.** The preview and the engines must see the same image
  (`prep.prepare_target`).
- **Settings, not constants.** Anything a builder would want to change is a field on
  `BoardConfig`, `PrepSettings` or `EngineParams` with a default and validation.
- **Relative URLs only** in the client, so the app keeps working under a path prefix.
- **Decisions are written down.** A design choice gets a short entry at the top of
  `docs/DECISIONS.md`.
- **Plain prose.** No em dashes in docs, comments, commits or UI copy.
- **No private data.** Do not add real people's photos, host names, addresses or secrets. The
  sample photos are AI-generated on purpose.

## Pull requests

Keep each pull request to one change, describe how you tested it, and include a screenshot for
anything visible. By contributing you agree that your work is released under the
[MIT licence](LICENSE).
