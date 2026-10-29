# Mega-map taxonomy inputs

Verified October 29, 2026:

- `eBird_taxonomy_v2026.csv` is the official eBird API CSV for taxonomy version
  2026, including `SPECIES_CODE`, `COMMON_NAME`, `SCIENTIFIC_NAME`, and `CATEGORY`.
  It contains 18,631 taxa. Source:
  <https://api.ebird.org/v2/ref/taxonomy/ebird?fmt=csv&version=2026>.
  The official version list is <https://api.ebird.org/v2/ref/taxonomy/versions>.
- `ABA_Checklist.csv` contains the 1,161 species rows from ABA Checklist 8.19
  (January 2026), downloaded through <https://www.aba.org/aba-checklist/> from
  <https://www.aba.org/wp-content/uploads/2026/03/ABA_Checklist-8.19.csv.zip>.
  The download's English name, scientific name, banding code and ABA rarity code
  were retained in the existing four-column format. Blank rows, family headings,
  French names and introductory layout text were excluded. The checklist is
  credited to the American Birding Association; rarity classifications come
  from ABA, not from eBird or inferred taxonomy changes.
- `taxonomy_sources.json` records URLs, versions, counts and SHA-256 hashes.
  `source_csv_sha256` and `source_sha256` identify the original downloads;
  `normalized_sha256` and eBird `sha256` hash UTF-8 text with LF newlines so
  Windows and Linux identify the same content.
- `eBird_taxonomy_v2024.csv` is retained as a historical compatibility fixture.
  The mega workflow uses the 2026 file.

Do not use the Clements-only CSV as a substitute for the eBird CSV. Preserve
`SPECIES_CODE` and all taxonomic categories in the source snapshot. The mega
matcher chooses species-level entries, using common names first and unique
exact scientific names as a fallback. It does not let ISSF, hybrid, or slash
entries replace a species match.

The 275 ABA Code 4/5 entries yield 274 species matches (124 Code 4; 150 Code 5).
`Little White-Tern` now matches `whiter5`. `Wandering Albatross (sp.)` remains
explicitly unresolved: its broad identification must not be treated as the
more specific eBird `Snowy Albatross` (`wanalb1`).

After changing inputs, run the mega regression tests and inspect generated
`species_matches.csv`, `unresolved_names.csv`, `aba4.json`, `aba5.json`, and
`summary.json`. Both diagnostic CSVs are replaced on every build, including
when the unresolved file contains only its header.
