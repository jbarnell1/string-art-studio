# Third-party files

Files in this repository that were made by someone else, where they came from, and their licenses.
Python and JavaScript dependencies are not vendored; they are listed in `pyproject.toml` and
`uv.lock` and installed from PyPI.

| What | Where in the repo | Source | License |
|---|---|---|---|
| IBM Plex Sans, Regular (400), Medium (500) and SemiBold (600), Latin-1 subset, WOFF2 | `stringart/web/static/fonts/IBMPlexSans-*-Latin1.woff2` | npm package [`@ibm/plex-sans`](https://www.npmjs.com/package/@ibm/plex-sans) 1.1.0, files `fonts/split/woff2/IBMPlexSans-{Regular,Medium,SemiBold}-Latin1.woff2`, unmodified (upstream repository: [IBM/plex](https://github.com/IBM/plex)) | SIL Open Font License 1.1, text in `stringart/web/static/fonts/OFL.txt`. "Plex" is a Reserved Font Name of IBM Corp. |
| YuNet face detector (`face_detection_yunet_2023mar.onnx`) | `stringart/models/` | [OpenCV model zoo](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet) | MIT, text in `stringart/models/LICENSE-yunet` |

The fonts are served by the app itself (ADR-026); the browser never contacts a font CDN. The
`@font-face` rules in `stringart/web/static/app.css` use the same `unicode-range` as upstream's
Latin-1 file, so characters outside it fall back to the system font.
