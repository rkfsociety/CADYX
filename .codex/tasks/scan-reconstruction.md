# Active scan reconstruction task

- Goal: scan the gypsum figurine in the current placement, preserve the original captures in its existing CADYX project, then register and fuse the views into a volumetric point cloud. Do not create a mesh.
- Target project: `Гипсовая фигурка — 5 ракурсов (2)`; project ID `8d6a8722-01ea-4e09-9c9f-4ce9cad72b39`. Keep the scanner and part fixed. Never tilt the table.
- Existing source set: five views at relative angles 0°, 30°, 60°, 90°, 120° and the provisional fused candidate; files are stored in the project. Earlier geometry alignment was not accepted as final.
- New capture workflow: the project viewport has a user-started 360° button. It captures 12 stationary frames at 30° increments, records encoder poses, verifies level tilt, updates the live viewport with the newest single frame, and returns the table to its starting angle. The capture is not registered/fused while running.
- Only proceed after the user presses the button and the status reports completion. Then inspect the new raw/PLY files and pose manifest, register each view to the first-frame coordinate system using geometric overlap, evaluate overlap and closure, and publish an honest provisional/final point-cloud result to this same project. Preserve all raw inputs. Keep the browser tab open.
- Current state: button/API implementation is in progress; no new physical frames have been captured. Never launch Revo Scan or the scanner workflow on the agent's own initiative.
