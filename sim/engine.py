from collections import deque
from dataclasses import asdict
from functools import partial
import heapq
from .dispatcher import Dispatcher
from .events import Event, NullSink
from .models import SimulationError, integer
from .passengers import PassengerState
from .vehicles import VehicleState
from .schedule import validate_schedule
from .reporting import Result, VehicleStats
from .metrics import WaitStats
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


def simulate(plan_id, revision, inputs, trips, config, sink=None, probe=None, outcome_sink=None):
    """Run one fixed realization.

    probe(stage): optional measurement hook; never changes simulation state.
    outcome_sink(state): optional P3 streaming. Each passenger's final state is passed
    exactly once, when it can no longer change (alighted, or outcome fixed while still
    waiting), then censored ones in passenger_id order at the end. Final states are then
    not retained: Result.passengers is () and Result.total comes from streamed_total.
    """
    integer(revision, 1)
    if not plan_id: raise SimulationError('missing plan ID')
    validate_schedule(trips)
    if any(p.time_s > config.drain_s for p in inputs.passengers):
        raise SimulationError('drain precedes passenger generation')
    sink = sink if sink is not None else NullSink()
    streaming = outcome_sink is not None
    # Generation order (time_s, passenger_id) equals the ('generate:'+pid) key order, and
    # deadline keys (time_s + constant) follow the same order, so all three per-passenger
    # event kinds are sorted streams instead of 3N pre-loaded heap entries.
    order = sorted(inputs.passengers, key=lambda p: (p.time_s, p.passenger_id))
    states = {}
    vehicles = {t.vehicle_id: VehicleState(t.capacity) for t in trips}
    vstats = {vid: {'boarded': 0, 'served_alighted': 0, 'empty_time_s': 0} for vid in vehicles}
    connectivity = _Connectivity(trips, config.drain_s)
    dispatcher, count, refusals = Dispatcher(), [0], [0]
    tally = {'served': 0, 'unserved': 0, 'censored': 0}
    served_waits = WaitStats(0)
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
    def final(s):
        tally[s.outcome] += 1
        if s.outcome == 'served': served_waits.add(s.boarding_s - s.passenger.time_s)
        if streaming:
            del states[s.passenger.passenger_id]
            outcome_sink(s)
    def generate(*key, p):
        s = states[p.passenger_id] = PassengerState(p)
        s.physical = 'waiting'
        waiting.setdefault(p.origin, {}).setdefault(p.destination, deque()).append(s)
        emit(key, 'generated', p.passenger_id)
    def expire(*key, pid):
        s = states.get(pid)
        if s is not None and s.outcome is None and s.physical != 'arrived':
            trigger = 'wait' if key[2].startswith('wait:') else 'journey'
            if trigger == 'wait' and s.physical != 'waiting': return
            s.outcome = 'unserved'
            s.reason = connectivity.reason(s.passenger, trigger)
            emit(key, 'expired', s.passenger.passenger_id, s.vehicle_id)
            if s.physical == 'waiting': final(s)
    dispatcher.add_stream(((p.time_s, 1, 'generate:'+p.passenger_id), partial(generate, p=p)) for p in order)
    dispatcher.add_stream(((p.time_s+config.max_wait_s, 4, 'wait:'+p.passenger_id),
                           partial(expire, pid=p.passenger_id)) for p in order)
    dispatcher.add_stream(((p.time_s+config.max_journey_s, 4, 'journey:'+p.passenger_id),
                           partial(expire, pid=p.passenger_id)) for p in order)
    def arrive(*key, t, i):
        stop, v = t.stops[i], vehicles[t.vehicle_id]
        for pid in sorted(tuple(v.onboard)):
            s = v.onboard[pid]
            if s.passenger.destination == stop:
                del v.onboard[pid]
                s.physical, s.arrival_s, s.vehicle_id = 'arrived', key[0], None
                if s.outcome is None:
                    s.outcome = 'served'
                    vstats[t.vehicle_id]['served_alighted'] += 1
                emit(key, 'alighted', pid, t.vehicle_id)
                final(s)
        v.check()
        emit(key, 'vehicle_arrived', vid=t.vehicle_id)
    def board(*key, t, i):
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
    def depart(*key, t, i):
        if not vehicles[t.vehicle_id].onboard:
            if i < len(t.stops)-1:
                if t.arrivals[i+1] <= config.drain_s:
                    vstats[t.vehicle_id]['empty_time_s'] += t.arrivals[i+1]-t.departures[i]
            elif t.return_s <= config.drain_s:
                vstats[t.vehicle_id]['empty_time_s'] += t.return_s-t.departures[i]
        emit(key, 'departed', vid=t.vehicle_id)
    def trip_events(t):
        # Within one trip keys ascend: a_i <= d_i < a_{i+1}, phases 0 < 2 < 3 at equal times.
        for i in range(len(t.stops)):
            sid = t.trip_id+':'+str(i).zfill(8)
            yield (t.arrivals[i], 0, sid), partial(arrive, t=t, i=i)
            yield (t.arrivals[i], 2, sid), partial(board, t=t, i=i)
            yield (t.departures[i], 3, sid), partial(depart, t=t, i=i)
    for trip in trips: dispatcher.add_stream(trip_events(trip))
    if probe: probe('engine_init')
    dispatcher.run(config.drain_s)
    if probe: probe('engine_run')
    # Remaining non-final states: never-resolved (censored) and journey-expired riders still
    # onboard at drain (unserved). Both are finalized here in passenger_id order.
    for pid in sorted(states):
        s = states[pid]
        if s.outcome is None:
            s.outcome = 'censored'
            final(s)
        elif s.physical == 'onboard':
            final(s)
    served, unserved, censored = tally['served'], tally['unserved'], tally['censored']
    ordered = () if streaming else tuple(states[k] for k in sorted(states))
    if served+unserved+censored != len(inputs.passengers): raise RuntimeError('conservation')
    for v in vehicles.values(): v.check()
    return Result(plan_id, revision, inputs.input_bundle_id, inputs.source_checksum, inputs.checksum,
        digest(asdict(config)), ordered, served, unserved, censored, served_waits.p90(),
        sum(t.distance_m for t in trips if t.return_s <= config.drain_s), count[0], refusals[0],
        vehicle_stats(trips, vstats, config.drain_s), len(inputs.passengers) if streaming else None)


def vehicle_stats(trips, counters, drain_s):
    completed = {vid: [0, 0, 0] for vid in counters}
    for t in trips:
        if t.return_s <= drain_s:
            c = completed[t.vehicle_id]
            c[0] += 1; c[1] += t.return_s-t.arrivals[0]; c[2] += t.distance_m
    return tuple(VehicleStats(vid, completed[vid][0], counters[vid]['boarded'], counters[vid]['served_alighted'],
                              completed[vid][1], completed[vid][2], counters[vid]['empty_time_s'])
                 for vid in sorted(counters))
