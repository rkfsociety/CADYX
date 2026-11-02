"""Run geometric registration and separately preserve a cleaned point-cloud candidate.

All outputs are written to a new staging directory; publication is the Core's job.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[2]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

from tools.revopoint.preprocess_scan_frame import verified_preprocessed_frame
from tools.revopoint.register_point_clouds_first_frame import register_sequence, source_paths


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def run_pipeline(series: Path, run_id: str, staging: Path, *, progress=None) -> dict[str, Any]:
    import numpy as np
    import open3d as o3d
    from cadyx.point_cloud import process_points

    series = series.resolve(strict=True)
    paths = source_paths(series, run_id)
    if len(paths) < 2:
        raise ValueError('Для сшивки требуется не менее двух кадров.')
    # Reject mixed, missing or modified cleanup artifacts; keep original data intact.
    for path in paths:
        if path.is_symlink() or verified_preprocessed_frame(path) is None:
            raise ValueError(f'Не подтверждена очистка кадра {path.name}.')
    input_hashes = {path.name: _sha256(path) for path in paths}
    if staging.exists():
        raise FileExistsError('Каталог результатов уже существует.')
    staging.mkdir(parents=True)
    fused = staging / 'fused.ply'
    cleaned = staging / 'cleaned.ply'
    registration = register_sequence(series, run_id, fused, progress=progress)
    if any(path.is_symlink() or _sha256(path) != input_hashes[path.name] for path in paths):
        raise ValueError('Исходные кадры изменились во время сшивки; результат не опубликован.')
    cloud = o3d.io.read_point_cloud(str(fused))
    points = [tuple(map(float, point)) for point in np.asarray(cloud.points)]
    stages = []
    for index, (method, parameters) in enumerate((
            ('isolation_filter', {'rate': 15.0, 'component_factor': 3.0}),
            ('remove_overlaps', {'radius': 0.59}))):
        if progress:
            progress('cleanup', index, 2, fused.name)
        before = len(points)
        diagnostics: dict[str, Any] = {}
        points = process_points(points, method, parameters, diagnostics=diagnostics)
        stages.append({'method': method, 'parameters': parameters, 'input_points': before,
                       'output_points': len(points), 'removed_points': before - len(points),
                       'diagnostics': diagnostics})
    if progress:
        progress('cleanup', 2, 2, None)
    output = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(np.asarray(points)))
    if not o3d.io.write_point_cloud(str(cleaned), output, write_ascii=True, compressed=False):
        raise OSError('Не удалось записать очищенное облако.')
    report = {'format': 'cadyx-scan-pipeline-v1', 'state': 'completed', 'run_id': run_id,
              'quality': 'provisional', 'registration': registration,
              'input_sha256': input_hashes,
              'fused': {'file': fused.name, 'sha256': _sha256(fused)},
              'cleaned': {'file': cleaned.name, 'sha256': _sha256(cleaned)},
              'cleanup': stages,
              'limitations': 'Точность, сохранность тонких деталей и замыкание круга требуют проверки.'}
    (staging / 'pipeline.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--series', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--staging', type=Path, required=True)
    args = parser.parse_args()
    def progress(phase: str, done: int, total: int, filename: str | None) -> None:
        print(json.dumps({'phase': phase, 'done': done, 'total': total, 'file': filename}, ensure_ascii=False), flush=True)
    try:
        report = run_pipeline(args.series, args.run_id, args.staging, progress=progress)
        print(json.dumps({'state': 'completed', 'report': str(args.staging / 'pipeline.json')}, ensure_ascii=False), flush=True)
        return 0
    except Exception as exc:
        print(json.dumps({'state': 'failed', 'error': str(exc)}, ensure_ascii=False), file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
