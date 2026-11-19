"""Explicit, scheduled TTL cleanup for stored uploads and reports."""
import argparse
from pathlib import Path
from backend.service import Service

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--data-dir',type=Path,default=Path('runtime-data'))
    p.add_argument('--age-hours',type=float,default=24)
    a=p.parse_args()
    if a.age_hours<=0:raise SystemExit('age-hours must be positive')
    print('Deleted sessions:',Service(a.data_dir).cleanup(a.age_hours))
