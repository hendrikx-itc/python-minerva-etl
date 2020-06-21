import subprocess
import tempfile
import json


trend_store_json = {
    'data_source': 'test',
    'entity_type': 'Cell',
    'granularity': '1 day',
    'partition_size': '86400s',
    'parts': [
        {
            'name': 'first_part',
            'trends': [
            ]
        },
        {
            'name': 'second_part',
            'trends': [
            ]
        }
    ]
}


def test_create_data_source(start_db_container):
    proc = subprocess.run(['minerva', 'data-source', 'create', 'test'])

    assert proc.returncode == 0

    with tempfile.NamedTemporaryFile('wt') as json_tmp_file:
        json.dump(trend_store_json, json_tmp_file)
        json_tmp_file.flush()

        proc = subprocess.run(['minerva', 'trend-store', 'create', '--format=json', json_tmp_file.name])

    assert proc.returncode == 0

    with tempfile.NamedTemporaryFile() as tmp_file:
        proc = subprocess.run(
            ['minerva', 'load-data', '--data-source', 'test', '--type', 'csv', tmp_file.name]
        )

    assert proc.returncode == 0

    proc = subprocess.run(['minerva', 'data-source', 'delete', 'test'])

    assert proc.returncode == 0