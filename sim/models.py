"""M05 내부 기본 자료형. M00 승인 DTO가 아니다.

시각은 service date 자정 기준 정수 초(time_s), 거리는 m, 인원·정원은 정수다.
"""
from dataclasses import dataclass

class SimulationError(ValueError):
    """잘못된 입력·일정·정책. 결과를 만들지 않고 실행을 멈춘다."""
    code = "INPUT_INVALID"

def integer(x, minimum=0):
    """bool이 아닌 정수이며 minimum 이상인지 검사한다."""
    if type(x) is not int or x < minimum:
        raise SimulationError("invalid integer")

@dataclass(frozen=True, slots=True)
class Passenger:
    """승객 1명. time_s는 출발 정류장에 나타나는 발생 시각이다."""
    passenger_id: str
    origin: str
    destination: str
    time_s: int

@dataclass(frozen=True)
class Trip:
    """차량 1대의 운행편 1회.

    stops 순서대로 방문하며 i번째 정류장에 arrivals[i]에 도착해 departures[i]에 떠난다.
    마지막 정류장 뒤에는 빈 차로 출발 거점에 돌아오며 return_s에 복귀가 끝난다.
    distance_m은 복귀까지 포함한 운행 거리다.
    """
    trip_id: str
    vehicle_id: str
    capacity: int
    stops: tuple[str, ...]
    arrivals: tuple[int, ...]
    departures: tuple[int, ...]
    return_s: int
    distance_m: int

@dataclass(frozen=True)
class Config:
    """실행 정책. 숨은 기본값 없이 호출자가 모두 정한다.

    max_wait_s: 발생 후 승차까지 최대 대기. max_journey_s: 발생 후 목적지 도착까지 최대 시간.
    drain_s: 이 시각까지만 사건을 처리하고 미확정 승객은 censored로 둔다(절대 time_s).
    """
    max_wait_s: int
    max_journey_s: int
    drain_s: int
    boarding_policy: str
    travel_policy: str
    def __post_init__(self):
        for x in (self.max_wait_s, self.max_journey_s, self.drain_s): integer(x)
        # 현재 지원하는 정책은 선착순 승차, 고정 구간 주행시간뿐이다.
        if self.boarding_policy != 'fifo' or self.travel_policy != 'fixed_segments':
            raise SimulationError("unsupported explicit policy")
