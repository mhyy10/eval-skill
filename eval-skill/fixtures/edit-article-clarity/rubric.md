Score the edited article in `output/edited.md` against the original
`input/draft.md` on these axes, 1-5 each:

1. Structure: are ideas ordered so that later sections build on earlier
   ones, with no forward dependencies?
2. Clarity: is each paragraph tight (roughly under 240 characters) and
   free of filler?
3. Fidelity: is every factual claim from the draft preserved, with no
   invented content?

Return JSON only: {"structure": N, "clarity": N, "fidelity": N,
"total": N, "notes": "one paragraph"}.
