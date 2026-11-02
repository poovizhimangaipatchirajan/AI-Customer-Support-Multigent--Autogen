"""Offline checks for evidence file signature validation."""
from backend.services.ticket_evidence import sniff, MAX_BYTES, ALLOWED
from PIL import Image
import io

def image_bytes(fmt):
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buf, format=fmt)
    return buf.getvalue()

def test_supported_types_and_limits():
    assert MAX_BYTES == 2 * 1024 * 1024
    assert set(ALLOWED) == {'image/jpeg', 'image/png', 'application/pdf'}

def test_reject_mismatched_file_signatures():
    assert not sniff(b'<script>alert(1)</script>', 'image/png')
    assert not sniff(b'%PDF-malformed', 'application/pdf')
    assert not sniff(b'\xff\xd8\xfftruncated', 'image/jpeg')

def test_accept_representative_headers():
    assert sniff(image_bytes('PNG'), 'image/png')
    assert sniff(image_bytes('JPEG'), 'image/jpeg')
    assert sniff(b'%PDF-1.4\n...\n%%EOF\n', 'application/pdf')


def test_reject_incomplete_images_and_mislabeled_formats():
    assert not sniff(b'\x89PNG\r\n\x1a\n' + b'data', 'image/png')
    assert not sniff(b'\xff\xd8\xffdata\xff\xd9', 'image/jpeg')
    assert not sniff(image_bytes('PNG'), 'image/jpeg')
    assert not sniff(image_bytes('JPEG')[:-10], 'image/jpeg')
