# Continuous scanning preview

- The live preview has an approximate table-plane fallback and a fixed marker pattern on the table. Marker-map search runs in a worker; on the rotating table, 12–15 of 34 markers matched with about 0.2–0.3 median residual and were highlighted red. The scanner reported about 13 FPS.
- Verify the direction of apparent part rotation against the physical table, establish reliable marker-map registration and table center, and check the saved geometrically stitched cloud's placement. Do not use table markers for geometric stitching or modify saved points for display placement.
- The live view most recently showed only mapped markers, not the object cloud; determine why before considering the alignment goal complete. Validate red highlights against physical detections across a full turn, and check rotation direction and part positioning with an actual object cloud.
- The user's requested push is due after the complete preview/alignment goal is verified. The scanner and table were running for the live test; inspect their current state before controlling them.
