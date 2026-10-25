# Offline typefaces

The Archive loads these fonts from its own local static directory. It makes no
requests to Google Fonts or another font CDN.

The five TTF files are unmodified binaries from Google's official
[google/fonts repository](https://github.com/google/fonts), pinned to commit
`e7db6ca603fd4862010383ff17ffd6cc4ffac1c0` and retrieved on 2026-10-25. The variable
files have shorter local filenames; their embedded family names are unchanged.
`manifest.json` records each exact source URL, byte count, and SHA-256 hash.

- **IBM Plex Mono**: regular (400) and medium (500), version 2.3.
  License: `IBMPlexMono-OFL.txt`.
- **Newsreader**: upright variable font, version 1.003, weights 200-800 and
  optical sizes 6-72. License: `Newsreader-OFL.txt`.
- **Source Sans 3**: upright and italic variable fonts, version 3.052, weights
  200-900. License: `SourceSans3-OFL.txt`.

All three families use the SIL Open Font License, version 1.1. Keep the
corresponding license files with redistributed font binaries.

The font table bounds, embedded family/version names, variable axis ranges, and
whole-font SFNT checksums were checked after download. `fonts.css` declares the
families and supported weights used by the interface with `font-display: swap`.
