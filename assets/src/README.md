Generators for the images in `assets/`. Text is baked to glyph outlines (Inter), so GitHub renders
them the same everywhere. Run from the repo root:

    pip install fonttools uharfbuzz numpy
    for f in banner badges workflow findings throughput roadmap; do python assets/src/$f.py; done

`findings.py` reads `findings/runs/*-exploration.json`; `banner.py` takes an optional seed and land threshold (`3 0.34`).
