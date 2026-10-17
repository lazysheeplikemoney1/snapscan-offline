"""The scan endpoint must return a reviewable crop before OCR starts."""
import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers import scan


@pytest.fixture
def scanner(monkeypatch, tmp_path):
    monkeypatch.setattr(scan, "UPLOAD_DIR", str(tmp_path))
    return TestClient(app), tmp_path


def photo():
    image = np.full((400, 600, 3), (45, 65, 85), np.uint8)
    cv2.rectangle(image, (170, 40), (430, 360), (225, 235, 245), -1)
    cv2.putText(image, 'RECEIPT 001', (185, 100), cv2.FONT_HERSHEY_SIMPLEX, .65, (30, 30, 30), 1)
    return cv2.imencode('.png', image)[1].tobytes()


def post(client, **data):
    return client.post('/api/scan/correct', files={'file': ('receipt.png', photo(), 'image/png')}, data=data)


def test_manual_crop_removes_background_and_returns_all_review_images(scanner):
    client, directory = scanner
    points = [[170, 40], [430, 40], [430, 360], [170, 360]]
    response = post(client, quad=json.dumps(points), mode='color')
    assert response.status_code == 200
    result = response.json()
    assert result.get('source_size') == [400, 600]
    assert result.get('mode') == 'color'
    assert result.get('detection', {}).get('method') == 'manual'
    assert result['has_quad'] is True
    assert result['quad'] == points
    assert result['size'][0] <= 322 and result['size'][1] <= 262
    for field in ['original_image', 'cropped_image', 'image']:
        assert result[field].startswith('/uploads/')
        image = cv2.imdecode(np.fromfile(directory / Path(result[field]).name, dtype=np.uint8), 1)
        assert image is not None
    output = cv2.imdecode(np.fromfile(directory / Path(result['image']).name, dtype=np.uint8), 1)
    assert output[output.shape[0]//2, 0].mean() > 180


@pytest.mark.parametrize('value', ['invalid', '[]', '[[0,0],[1,1]]', '[[0,0],[600,0],[600,400],[0,400]]',
                                   '[[170,40],[430,360],[430,40],[170,360]]',
                                   '[[0,0],[1,0],[1,1],[0,1]]'])
def test_invalid_manual_points_do_not_fall_back_to_wrong_automatic_crop(scanner, value):
    client, directory = scanner
    response = post(client, quad=value)
    assert response.status_code == 422
    assert not list(directory.iterdir())


def test_unknown_effect_is_rejected(scanner):
    client, directory = scanner
    assert post(client, mode='invented').status_code == 422
    assert not list(directory.iterdir())


def test_no_document_is_explicit_and_keeps_full_image(scanner, monkeypatch):
    client, directory = scanner
    monkeypatch.setattr(scan, 'detect_document', lambda image: {'quad': None, 'confidence': 0., 'method': 'none'}, raising=False)
    result = post(client).json()
    assert result['has_quad'] is False
    assert result['quad'] is None
    assert result['size'] == [400, 600]
    assert any('边界' in text for text in result.get('warnings', []))


def test_clipped_paper_warning_reaches_the_scan_confirmation_ui(scanner):
    client, _ = scanner
    image = Path(__file__).parent / 'fixtures' / 'receipt_clipped_wood.png'
    response = client.post('/api/scan/correct', files={'file': ('receipt.png', image.read_bytes(), 'image/png')})
    result = response.json()
    assert result['has_quad'] is True
    assert result['detection']['document_complete'] is False
    for warning in result['detection']['warnings']:
        assert warning in result['warnings']


def test_second_file_write_failure_removes_only_this_request_files(scanner, monkeypatch):
    client, directory = scanner
    existing = directory/'keep.png'
    existing.write_bytes(b'unchanged')
    original = scan.imwrite_unicode
    calls = []
    def fail_second(path, image, params=None):
        calls.append(path)
        if len(calls) == 2:
            Path(path).write_bytes(b'partial')
            return False
        return original(path, image, params)
    monkeypatch.setattr(scan, 'imwrite_unicode', fail_second)
    response = post(client)
    assert response.status_code == 500
    assert list(directory.iterdir()) == [existing]
    assert existing.read_bytes() == b'unchanged'


def test_scan_preparation_does_not_invoke_ocr_or_add_history(scanner, monkeypatch):
    client, directory = scanner
    from app.services import ocr_engine
    from app.core.db import get_conn
    def forbidden():
        pytest.fail('Preview must not run OCR')
    monkeypatch.setattr(ocr_engine, 'get_engine', forbidden)
    conn=get_conn()
    try:
        before=conn.execute('SELECT COUNT(*) FROM scans').fetchone()[0]
        assert post(client).status_code == 200
        assert conn.execute('SELECT COUNT(*) FROM scans').fetchone()[0] == before
    finally:
        conn.close()
