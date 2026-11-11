"""PR #6 시절 측정 스크립트(비교용으로 보존): python -m sim.benchmark --passengers 10000.

차량 1대·정류장 2개 합성 입력이라 실제 처리 성능 근거가 아니다. 새 측정은 sim.harness를 쓴다.
"""
import argparse
from dataclasses import asdict
import json
from time import perf_counter
import tracemalloc
from .models import Config, Trip
from .run_inputs import RunInputs, expand
from .engine import simulate
from .events import JsonlSink, NullSink

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--passengers', type=int, choices=(10000,100000,1000000), default=10000)
    parser.add_argument('--log')
    args = parser.parse_args()
    stream = open(args.log,'w',encoding='utf-8') if args.log else None
    tracemalloc.start()
    started = perf_counter()
    try:
        run = RunInputs('benchmark','synthetic','synthetic','2026-11-08',
            expand([('d','A','B',0,args.passengers)]),(('ab',5),('ba',5)),'fixed')
        trip = Trip('t','v',args.passengers,('A','B'),(0,5),(0,5),10,100)
        result = simulate('benchmark',1,run,(trip,),Config(10,15,20,'fifo','fixed_segments'),
                          JsonlSink(stream) if stream else NullSink())
        print(json.dumps({'passengers':args.passengers,'served':result.served,
            'event_count':result.event_count,'elapsed_s':perf_counter()-started,
            'peak_bytes':tracemalloc.get_traced_memory()[1], 'run_checksum':result.run_checksum}))
    finally:
        if stream: stream.close()
        tracemalloc.stop()

if __name__=='__main__': main()
