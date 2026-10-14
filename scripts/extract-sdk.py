"""Extract a verified conda SDK using Python 3.15's standard-library Zstandard."""

import sys
import tarfile
import zipfile
from compression import zstd

with zipfile.ZipFile(sys.argv[1]) as package:
    name = next(name for name in package.namelist() if name.startswith("pkg-"))
    with package.open(name) as compressed, zstd.ZstdFile(compressed) as archive:
        with tarfile.open(fileobj=archive, mode="r|") as contents:
            contents.extractall(sys.argv[2], filter="data")
