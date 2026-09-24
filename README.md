# Odia Wikipedia, cleaned for LLM training

Every article of [Odia Wikipedia](https://or.wikipedia.org), from a complete dump, turned into clean
text for training language models. The scripts here do the work one step at a time and write
everything inside this folder; each explains itself at the top and in `--help`.

```bash
uv run prepare.py download   # the newest complete dump, SHA-1 checked, indexed by article
```

Work in progress: `prepare.py build` writes the full README (what the corpus holds, how it was
made, how to use it) together with the corpus.
