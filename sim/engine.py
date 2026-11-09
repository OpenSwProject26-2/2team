from collections import deque
from dataclasses import asdict
import heapq
from .dispatcher import Dispatcher
from .events import Event, NullSink
from .models import SimulationError, integer
from .passengers import PassengerState
from .vehicles import VehicleState
from .schedule import validate_schedule
from .reporting import Result, VehicleStats, p90
from .run_inputs import digest


class _Connectivity:
    """Lazy per-OD facts for unserved-reason classification (sim.metrics.REASON_PRIORITY)."""
    def __init__(self, trips, drain_s):
        self.visits, self.drain_s, self.cache = {}, drain_s, {}
        for t in trips:
            position = {stop: i for i, stop in enumerate(t.stops)}
            for i, stop in enumerate(t.stops):
                self.visits.setdefault(stop, []).append((t.arrivals[i], i, position))

    def reason(self, passenger, trigger):
        od = (passenger.origin, passenger.destination)
        if od not in self.cache:
            connected, last = False, None
            for arrival, i, position in self.visits.get(od[0], ()):
                if position.get(od[1], -1) > i:
                    connected = True
                    if arrival <= self.drain_s and (last is None or arrival > last): last = arrival
            self.cache[od] = (connected, last)
        connected, last = self.cache[od]
        if not connected: return 'no_connection'
        if last is None or passenger.time_s > last: return 'service_end'
        return trigger


def simulate(plan_id, revision, inputs, trips, config, sink=None, probe=None):
    """probe(stage) is an optional measurement hook; it never changes simulation state."""
    integer(revision, 1)
    if not plan_id: raise SimulationError('missing plan ID')
    validate_schedule(trips)
    if any(p.time_s > config.drain_s for p in inputs.passengers):
        raise SimulationError('drain precedes passenger generation')
    sink = sink if sink is not None else NullSink()
    states = {p.passenger_id: PassengerState(p) for p in inputs.passengers}
    vehicles = {t.vehicle_id: VehicleState(t.capacity) for t in trips}
    vstats = {vid: {'boarded': 0, 'served_alighted': 0, 'empty_time_s': 0} for vid in vehicles}
    connectivity = _Connectivity(trips, config.drain_s)
    dispatcher, count, refusals = Dispatcher(), [0], [0]
    # waiting[origin][destination]: FIFO in generation order, i.e. (time_s, passenger_id),
    # because generate events run in (time_s, phase 1, 'generate:'+pid) order. Entries that
    # stopped being eligible (boarded or outcome fixed) are dropped lazily at the head.
    waiting = {}
    def eligible(s): return s.physical == 'waiting' and s.outcome is None
    def head(q):
        while q and not eligible(q[0]): q.popleft()
        return q[0] if q else None
    def emit(key, kind, pid=None, vid=None):
        sink.emit(Event(*key, kind, pid, vid, len(vehicles[vid].onboard) if vid else None))
        count[0] += 1
    for pid, state in states.items():
        def generate(*key, s=state):
            s.physical = 'waiting'
            waiting.setdefault(s.passenger.origin, {}).setdefault(s.passenger.destination, deque()).append(s)
            emit(key, 'generated', s.passenger.passenger_id)
        def expire(*key, s=state):
            if s.outcome is None and s.physical != 'arrived':
                trigger = 'wait' if key[2].startswith('wait:') else 'journey'
                if trigger == 'wait' and s.physical != 'waiting': return
                s.outcome = 'unserved'
                s.reason = connectivity.reason(s.passenger, trigger)
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
                        if s.outcome is None:
                            s.outcome = 'served'
                            vstats[t.vehicle_id]['served_alighted'] += 1
                        emit(key, 'alighted', pid, t.vehicle_id)
                v.check()
                emit(key, 'vehicle_arrived', vid=t.vehicle_id)
            def board(*key, t=trip, i=i):
                v = vehicles[t.vehicle_id]
                free, queues = v.capacity-len(v.onboard), waiting.get(t.stops[i])
                if not queues: return
                # k-way merge of per-destination FIFO heads == old global (time_s, id) sort.
                heads = []
                if free > 0:
                    for dest in t.stops[i+1:]:
                        q = queues.get(dest)
                        s = head(q) if q else None
                        if s is not None: heads.append(((s.passenger.time_s, s.passenger.passenger_id), dest))
                    heapq.heapify(heads)
                while heads and free > 0:
                    _, dest = heapq.heappop(heads)
                    q = queues[dest]
                    s = q.popleft()
                    nxt = head(q)
                    if nxt is not None:
                        heapq.heappush(heads, ((nxt.passenger.time_s, nxt.passenger.passenger_id), dest))
                    free -= 1
                    if s.vehicle_id is not None: raise RuntimeError('multiple vehicles')
                    s.physical, s.vehicle_id, s.boarding_s = 'onboard', t.vehicle_id, key[0]
                    v.onboard[s.passenger.passenger_id] = s
                    v.check()
                    vstats[t.vehicle_id]['boarded'] += 1
                    emit(key, 'boarded', s.passenger.passenger_id, t.vehicle_id)
                if free == 0:
                    # M2 full-vehicle refusals: every still-eligible passenger this visit could
                    # have carried counts once per visit (duplicates across visits allowed).
                    for dest in t.stops[i+1:]:
                        for s in queues.get(dest, ()):
                            if eligible(s):
                                s.refused += 1
                                refusals[0] += 1
            def depart(*key, t=trip, i=i):
                if not vehicles[t.vehicle_id].onboard:
                    if i < len(t.stops)-1:
                        if t.arrivals[i+1] <= config.drain_s:
                            vstats[t.vehicle_id]['empty_time_s'] += t.arrivals[i+1]-t.departures[i]
                    elif t.return_s <= config.drain_s:
                        vstats[t.vehicle_id]['empty_time_s'] += t.return_s-t.departures[i]
                emit(key, 'departed', vid=t.vehicle_id)
            dispatcher.add(trip.arrivals[i], 0, sid, arrive)
            dispatcher.add(trip.arrivals[i], 2, sid, board)
            dispatcher.add(trip.departures[i], 3, sid, depart)
    if probe: probe('engine_init')
    dispatcher.run(config.drain_s)
    if probe: probe('engine_run')
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
        sum(t.distance_m for t in trips if t.return_s <= config.drain_s), count[0], refusals[0],
        vehicle_stats(trips, vstats, config.drain_s))


def vehicle_stats(trips, counters, drain_s):
    completed = {vid: [0, 0, 0] for vid in counters}
    for t in trips:
        if t.return_s <= drain_s:
            c = completed[t.vehicle_id]
            c[0] += 1; c[1] += t.return_s-t.arrivals[0]; c[2] += t.distance_m
    return tuple(VehicleStats(vid, completed[vid][0], counters[vid]['boarded'], counters[vid]['served_alighted'],
                              completed[vid][1], completed[vid][2], counters[vid]['empty_time_s'])
                 for vid in sorted(counters))
