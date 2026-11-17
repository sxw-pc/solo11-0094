import json
from pathlib import Path
import pytest
from tzgate.metadata_certificate import load_frame,draw_frame,preview_metadata

ROOT=Path(__file__).resolve().parents[1]
CONTRACT=ROOT/'contracts/metadata_certificate.yaml'
FRAME=ROOT/'fixtures/metadata_cert/frame.json'


def test_verified_document_sample_is_not_population_certificate():
    frame=load_frame(FRAME)
    assert draw_frame(frame)==['b','d','f','e']
    preview=preview_metadata(CONTRACT,ROOT,FRAME,'metadata-run')
    assert preview['frame_size']==6 and preview['sample_size']==4
    assert preview['full_metadata_count']==3
    assert preview['public_index_count']==4
    assert preview['population_certificate'] is None
    assert len(preview['files'])==1 and preview['files'][0]['sha256']


def test_duplicate_document_frame_rejected(tmp_path):
    data=json.loads(FRAME.read_text(encoding='utf-8'))
    data['document_ids'][0]='b'
    path=tmp_path/'bad-frame.json'
    path.write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(ValueError,match='frame IDs invalid'):
        load_frame(path)
