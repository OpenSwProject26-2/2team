from dataclasses import asdict
from .dispatcher import Dispatcher
from .events import Event, NullSink
from .models import SimulationError, integer
from .passengers import PassengerState
from .vehicles import VehicleState
from .schedule import validate_schedule
from .reporting import Result, p90
from .run_inputs import digest

def simulate(plan_id, revision, inputs, trips, config, sink=None):
    integer(revision, 1)
    if not plan_id: raise SimulationError('missing plan ID')
    validate_schedule(trips)
    if any(p.time_s > config.drain_s for p in inputs.passengers):
        raise SimulationError('drain precedes passenger generation')
    sink = sink if sink is not None else NullSink()
    states = {p.passenger_id: PassengerState(p) for p in inputs.passengers}
    vehicles = {t.vehicle_id: VehicleState(t.capacity) for t in trips}
    dispatcher, count = Dispatcher(), [0]
    def emit(key, kind, pid=None, vid=None):
        sink.emit(Event(*key, kind, pid, vid, len(vehicles[vid].onboard) if vid else None))
        count[0] += 1
    for pid, state in states.items():
        def generate(*key, s=state):
            s.physical = 'waiting'
            emit(key, 'generated', s.passenger.passenger_id)
        def expire(*key, s=state):
            if s.outcome is None and s.physical != 'arrived':
                if key[2].startswith('wait:') and s.physical != 'waiting': return
                s.outcome = 'unserved'
                emit(key, 'expired', s.passenger.passenger_id, s.vehicle_id)
        dispatcher.add(state.passenger.time_s, 1, 'generate:'+pid, generate)
        dispatcher.add(state.passenger.time_s+config.max_wait_s, 4, 'wait:'+pid, expire)
        dispatcher.add(state.passenger.time_s+config.max_journey_s, 4, 'journey:'+pid, expire)
    for trip in trips:
        for i, stop in enumerate(trip.stops):
            sid = trip.trip_id+':'+str(i).zfill(8)
            def arrive(*key, t=trip, stop=stop):
                v = vehicles[t.vehicle_id]
                for pid in sorted(tuple(v.onboard)):
                    s = v.onboard[pid]
                    if s.passenger.destination == stop:
                        del v.onboard[pid]
                        s.physical, s.arrival_s, s.vehicle_id = 'arrived', key[0], None
                        if s.outcome is None: s.outcome = 'served'
                        emit(key, 'alighted', pid, t.vehicle_id)
                v.check()
                emit(key, 'vehicle_arrived', vid=t.vehicle_id)
            def board(*key, t=trip, i=i):
                v = vehicles[t.vehicle_id]
                eligible = sorted((s for s in states.values() if s.physical == 'waiting' and s.outcome is None
                    and s.passenger.origin == t.stops[i] and s.passenger.destination in t.stops[i+1:]),
                    key=lambda s:(s.passenger.time_s, s.passenger.passenger_id))
                for s in eligible[:v.capacity-len(v.onboard)]:
                    if s.vehicle_id is not None: raise RuntimeError('multiple vehicles')
                    s.physical, s.vehicle_id, s.boarding_s = 'onboard', t.vehicle_id, key[0]
                    v.onboard[s.passenger.passenger_id] = s
                    v.check()
                    emit(key, 'boarded', s.passenger.passenger_id, t.vehicle_id)
            dispatcher.add(trip.arrivals[i], 0, sid, arrive)
            dispatcher.add(trip.arrivals[i], 2, sid, board)
            dispatcher.add(trip.departures[i], 3, sid, lambda *key,t=trip: emit(key,'departed',vid=t.vehicle_id))
    dispatcher.run(config.drain_s)
    for s in states.values():
        if s.outcome is None: s.outcome = 'censored'
    ordered = tuple(states[k] for k in sorted(states))
    served = sum(s.outcome == 'served' for s in ordered)
    unserved = sum(s.outcome == 'unserved' for s in ordered)
    censored = sum(s.outcome == 'censored' for s in ordered)
    if served+unserved+censored != len(inputs.passengers): raise RuntimeError('conservation')
    for v in vehicles.values(): v.check()
    return Result(plan_id, revision, inputs.input_bundle_id, inputs.source_checksum, inputs.checksum,
        digest(asdict(config)), ordered, served, unserved, censored,
        p90([s.boarding_s-s.passenger.time_s for s in ordered if s.outcome == 'served']),
        sum(t.distance_m for t in trips if t.return_s <= config.drain_s), count[0])
