"""실행 결과 자료형."""
from dataclasses import dataclass
from math import ceil
from .passengers import PassengerState

@dataclass(frozen=True)
class Result:
    """simulate()의 결과. 운행안·입력 식별자와 결과별 인원을 함께 둔다.

    served + unserved + censored == total(발생 인원). p90_wait_s는 served 승객 대기의
    nearest-rank P90(0명이면 None). distance_m은 drain_s 안에 복귀까지 끝난 운행편만 합한다.
    """
    plan_id: str
    plan_revision: int
    input_bundle_id: str
    source_checksum: str
    run_checksum: str
    config_hash: str
    passengers: tuple[PassengerState, ...]
    served: int
    unserved: int
    censored: int
    p90_wait_s: int | None
    distance_m: int
    event_count: int
    # P4 카운터. 기본값이 있어 예전 생성 방식도 그대로 동작한다.
    refusal_count: int = 0
    vehicle_stats: tuple['VehicleStats', ...] = ()
    # 결과를 스트리밍했을 때만 값이 있다(이때 passengers는 ()).
    streamed_total: int | None = None
    @property
    def total(self): return len(self.passengers) if self.streamed_total is None else self.streamed_total

@dataclass(frozen=True)
class VehicleStats:
    """차량별 M6 집계(drain_s 이내).

    service_time_s·distance_m: 복귀까지 끝난 운행편만. empty_time_s: 빈 차로 출발해 drain_s
    안에 다음 정류장에 도착한 구간 + 끝난 운행편의 복귀 시간. 빈 차 거리는 Trip에 구간 거리가
    없어 계산하지 않는다(TODO).
    """
    vehicle_id: str
    trips_completed: int
    boarded: int
    served_alighted: int
    service_time_s: int
    distance_m: int
    empty_time_s: int

def p90(values):
    """nearest-rank P90: 정렬 후 ⌈0.9n⌉번째 값. 비어 있으면 None."""
    return sorted(values)[ceil(len(values)*.9)-1] if values else None
