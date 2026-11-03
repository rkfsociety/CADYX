# Continuous scanning preview

- The live preview has an approximate table-plane fallback and a fixed marker pattern on the table. Live hardware showed about 13 FPS and the object above the table, but exact marker-map correspondence was not confirmed.
- Verify the direction of apparent part rotation against the physical table, establish reliable marker-map registration and table center, and check the saved geometrically stitched cloud's placement. Do not use table markers for geometric stitching or modify saved points for display placement.
- The user's requested push is due after the complete preview/alignment goal is verified. The scanner and table were running for the live test; inspect their current state before controlling them.
