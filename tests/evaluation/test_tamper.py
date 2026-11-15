"""변조 로그 검출(simulator.md §9.10). 한 군데씩 망가뜨린 실행이 해당 위반으로 실패하는지 본다.

의미 검사를 보려는 변조는 로그를 §6.1 정렬 키로 다시 정렬하고(V01 통과) 출력 해시를 run.json에
다시 적는다(V15 통과). 그래서 실패한 검사는 변조의 의미를 잡은 것이다.

`failed`는 실패해야 하는 검사 전부다(정확히 이 집합). 설계 표(§9.10)의 최소 집합에 더해, 로그를
바꾸면 지표가 로그와 어긋나므로 V13이, 승객 수가 바뀌면 V09의 정차 시간이 함께 실패하는 식의
연쇄를 그대로 고정한다.
"""

import tempfile
import unittest
from pathlib import Path

from busstrike.evaluation.validator import validate_run

from tests.evaluation.runs import RunDir, find, make_run
from tests.sim import cases


# ---------------------------------------------------------------- 변조 함수(이벤트 목록을 제자리에서 고친다)

def remove(**match):
    def fn(es):
        target = find(es, **match)
        return [e for e in es if e is not target]
    return fn


def set_fields(match: dict, **values):
    def fn(es):
        find(es, **match).update(values)
    return fn


def duplicate(**match):
    def fn(es):
        es.append(dict(find(es, **match)))
    return fn


def swap_visits_4_5(es):
    a = es.index(find(es, type="arrive", visit=4))
    b = es.index(find(es, type="arrive", visit=5))
    es[a], es[b] = es[b], es[a]


def denied_q3_boards_instead(es):
    """T2 visit 0: 정원 2인 차에 q3까지 태운 것으로 꾸민다."""
    find(es, type="denied", passenger_id="q3")["type"] = "board"


def alight_p4_before_board(es):
    """T1 p4는 visit 1(B, 134)에서 탄다. 하차를 visit 0(A, 0)으로 옮긴다."""
    find(es, type="alight", passenger_id="p4").update(t=0, visit=0, stop_id="A", stop_seq=0, trip_id="R1-V1-000-0")


def v2_runs_as_v1(es):
    """T3: V2의 정차를 V1의 것으로 꾸민다 → 한 차량이 같은 시각에 R1·R2 trip을 함께 달린다."""
    for e in es:
        if e.get("vehicle_id") == "V2":
            e.update(vehicle_id="V1", visit=e["visit"] + 4)


def ghost_passenger(es):
    es.append({"t": 0, "type": "no_service", "passenger_id": "ghost", "cause": "NO_ROUTE_AT_STOP"})


def events(fn, **kw):
    return lambda rd: rd.edit_events(fn, **kw)


def metrics_satisfied_plus_one(rd: RunDir):
    m = rd.read_json("metrics.json")
    m["passengers"]["satisfied"] += 1
    rd.write_json("metrics.json", m)
    rd.resign()


def timetable_trips_overlap(rd: RunDir):
    """T1 시간표에서 cycle 1 영업 trip(A 680)을 600으로 당겨 앞 공차 trip(…A 680)과 겹치게 한다."""
    t = rd.read_json("timetable.json")
    for trip in t["trips"]:
        if trip["trip_id"] == "R1-V1-001-0":
            for st, s in zip(trip["stop_times"], (600, 730, 920)):
                st["sched_s"] = s
    rd.write_json("timetable.json", t)
    rd.resign()


def wrong_log_hash(rd: RunDir):
    r = rd.read_json("run.json")
    r["event_log_sha256"] = "0" * 64
    rd.write_json("run.json", r)


# (이름, 시나리오, 변조, 실패해야 하는 검사 집합, 반드시 있어야 하는 위반 (check_id, message 일부, passenger_id))
CASES = [
    # ---- 완료 기준: 정원 초과
    ("capacity_exceeded", cases.t2, events(denied_q3_boards_instead),
     {"V02", "V03", "V06", "V07", "V09", "V11", "V13"}, ("V03", "capacity exceeded", None)),
    ("board_duplicated", cases.t1, events(duplicate(type="board", passenger_id="p1")),
     {"V02", "V03", "V06", "V09", "V11", "V13"}, ("V03", "arrive.boarded differs", None)),
    # ---- 완료 기준: 인원 누락
    ("alight_missing", cases.t1, events(remove(type="alight", passenger_id="p5")),
     {"V02", "V03", "V09", "V13"}, ("V02", "no final outcome", "p5")),
    ("abandon_missing", cases.t1, events(remove(type="abandon", passenger_id="p3")),
     {"V02", "V13"}, ("V02", "no final outcome", "p3")),
    ("unknown_passenger", cases.t1, events(ghost_passenger),
     {"V02"}, ("V02", "unknown passenger_id", "ghost")),
    # ---- 완료 기준: 차량 일정 중복
    ("vehicle_double_booked", cases.t3, events(v2_runs_as_v1),
     {"V04", "V05", "V11", "V13"}, ("V04", "trip starts before the previous trip", None)),
    ("timetable_overlap", cases.t1, timetable_trips_overlap,
     {"V13", "V14"}, ("V14", "trips of a vehicle overlap", None)),
    # ---- 완료 기준: 시간 역행
    ("depart_after_next_arrive", cases.t1, events(set_fields({"type": "depart", "visit": 4}, t=700)),
     {"V05", "V08", "V09"}, ("V05", "arrive earlier than the previous visit", None)),
    ("log_order_broken", cases.t1, events(swap_visits_4_5, resort=False),
     {"V01"}, ("V01", "sort key decreases", None)),
    # ---- 완료 기준: 승차 전 하차
    ("alight_before_board_time", cases.t1, events(alight_p4_before_board),
     {"V03", "V05", "V07", "V09", "V13"}, ("V05", "alight earlier than board", "p4")),
    ("alight_stop_seq_before_board", cases.t1, events(set_fields({"type": "alight", "passenger_id": "p2"}, stop_seq=0)),
     {"V07"}, ("V07", "alight stop_seq is not after board stop_seq", "p2")),
    # ---- §9.10 표의 나머지
    ("depart_one_second_early", cases.t1, events(set_fields({"type": "depart", "t": 147}, t=146)),
     {"V08", "V09"}, ("V09", "dwell differs", None)),
    ("board_before_arrival", cases.t1, events(set_fields({"type": "board", "passenger_id": "p4"}, t=90)),
     {"V06", "V13"}, ("V06", "board before the passenger arrived", "p4")),
    ("arrive_after_window", cases.t1, events(set_fields({"type": "arrive", "t": 1372}, t=1501)),
     {"V08", "V10", "V13"}, ("V10", "outside", None)),
    ("abandon_wrong_cause", cases.t1, events(set_fields({"type": "abandon", "passenger_id": "p3"}, cause="FULL")),
     {"V12", "V13"}, ("V12", "abandon cause differs", "p3")),
    ("queue_jump", cases.t2, events(set_fields({"type": "board", "passenger_id": "q2"}, passenger_id="q3")),
     {"V02", "V03", "V06", "V07", "V11", "V13"}, ("V11", "boarders differ from the FIFO reconstruction", None)),
    ("metrics_satisfied_plus_one", cases.t1, metrics_satisfied_plus_one,
     {"V13"}, ("V13", "passengers.satisfied", None)),
    ("event_log_hash_mismatch", cases.t1, wrong_log_hash,
     {"V15"}, ("V15", "run.event_log_sha256", None)),
]


class TamperedRunTests(unittest.TestCase):
    def test_each_tamper_fails_with_its_violation(self):
        for name, factory, tamper, failed, (cid, fragment, pid) in CASES:
            with self.subTest(tamper=name), tempfile.TemporaryDirectory() as d:
                rd = make_run(factory(), Path(d))
                self.assertTrue(validate_run(rd.d)["verified"], "untampered run must verify")
                tamper(rd)
                report = validate_run(rd.d)
                self.assertFalse(report["verified"])
                got = {c["id"] for c in report["checks"] if c["status"] == "fail"}
                self.assertEqual(got, failed)
                hits = [v for v in report["violations"] if v["check_id"] == cid and fragment in v["message"]
                        and (pid is None or v.get("passenger_id") == pid)]
                self.assertTrue(hits, f"no {cid} violation containing {fragment!r}")
                if "V01" in failed:
                    self.assertTrue(all(c["status"] == "skipped" for c in report["checks"][1:]))

    def test_capacity_violation_reports_capacity_and_load(self):
        with tempfile.TemporaryDirectory() as d:
            rd = make_run(cases.t2(), Path(d))
            rd.edit_events(denied_q3_boards_instead)
            report = validate_run(rd.d)
            v = next(x for x in report["violations"] if x["message"] == "capacity exceeded")
            self.assertEqual((v["vehicle_id"], v["t"], v["expected"], v["actual"]), ("V1", 0, 2, 3))
            self.assertEqual(v["stop_id"], "A")

    def test_unresigned_semantic_tamper_also_fails_v15(self):
        with tempfile.TemporaryDirectory() as d:
            rd = make_run(cases.t1(), Path(d))
            rd.edit_events(set_fields({"type": "depart", "t": 147}, t=146), resign=False)
            failed = {c["id"] for c in validate_run(rd.d)["checks"] if c["status"] == "fail"}
            self.assertEqual(failed, {"V08", "V09", "V15"})


if __name__ == "__main__":
    unittest.main()
